// terminal-state.js — orderflow terminal: pure in-memory stores + structure builders
// (DESIGN-orderflow-terminal.md §4 + §4b + §4c + §4d + §4e + §4f).
//
// RESEARCH / DESCRIPTIVE ONLY. Everything held here is LIVE-DESCRIPTIVE session
// state (§0.1): it is never merged into a backtested series or the OOS harness,
// and it renders only what actually arrived over the wire this session (§0.7 —
// no fabricated history, no backfill, gaps are left as gaps).
//
// Contract (§4): plain-script IIFE exposing ONE global, `BTCQ_TERMINAL_STATE`,
// loaded after terminal-adapters.js and before terminal-views.js. Inputs are the
// adapters' NORMALIZED events only — the shapes in §4 (`{kind:'trade', ex, ts,
// price, qty, aggressorBuy, id}` etc.), never raw exchange frames. Aggressor
// normalization already happened upstream (§0.6); these stores trust it.
//
// Two rails this file enforces on itself:
//   - ZERO DOM/window access. Every store runs unmodified under Node so the
//     fixture smoke (scripts/check_terminal.cjs) can replay recorded frames
//     through it — same dual-export trick as quant.js.
//   - NO Date.now() anywhere. Every method that needs a clock takes `ts` from
//     the event (or an explicit argument). Reason: replay/testability — a store
//     must produce byte-identical output when fed the same recorded tape twice,
//     and a wall clock would poison that. "Now" is the tape's now.
//
// Numeric hygiene follows quant.js mean(): non-finite inputs are IGNORED, never
// coerced to 0 (a NaN price silently becoming a $0 trade would fabricate data).
'use strict';

(function (global) {
  // ─── Shared helpers ────────────────────────────────────────────────────

  /** Finite-or-default: constructor params get sane fallbacks, never NaN state. */
  function finiteOr(x, dflt) {
    return Number.isFinite(x) ? x : dflt;
  }

  /** Canonicalize a price to 8 decimals so Map keys built from float math
   *  (e.g. 61855.2 - 0.1 = 61855.099999…) collide correctly. 8dp is far below
   *  any BTC venue tick and far above double noise at 1e5-scale prices. */
  function roundPx(x) {
    return Math.round(x * 1e8) / 1e8;
  }

  /** Snap a price onto the tick grid. `up=false` buckets DOWN (bids/footprint/
   *  profile), `up=true` buckets UP (asks). This is the price-IMPROVING-side-
   *  conservative convention: a grouped bid is never shown above where you
   *  could actually sell, a grouped ask never below where you could buy.
   *  The ±1e-9 epsilon keeps exact-multiple prices (61855.20/0.10 =
   *  618551.9999999999 in doubles) in their own bucket instead of the next. */
  function snapTick(price, tick, up) {
    const n = up ? Math.ceil(price / tick - 1e-9) : Math.floor(price / tick + 1e-9);
    return roundPx(n * tick);
  }

  /** Fixed-capacity ring buffer (O(1) push, oldest evicted). toArray() returns
   *  oldest→newest; callers reverse for display. Used by TapeStore/LiqStore so
   *  a day-long session cannot grow memory without bound. */
  function makeRing(max) {
    const cap = Math.max(1, Math.floor(finiteOr(max, 1000)));
    const buf = [];
    let start = 0; // index of the oldest element once the buffer is full
    return {
      push(x) {
        if (buf.length < cap) buf.push(x);
        else { buf[start] = x; start = (start + 1) % cap; }
      },
      toArray() {
        return buf.length < cap ? buf.slice() : buf.slice(start).concat(buf.slice(0, start));
      },
      get length() { return buf.length; },
    };
  }

  /** Is this a well-formed normalized trade event? (§4 shape; hygiene rail.)
   *  qty must be strictly positive — zero-size prints carry no flow information
   *  and would still create phantom price levels in footprint/profile maps. */
  function validTrade(t) {
    return !!t && Number.isFinite(t.ts) && Number.isFinite(t.price)
      && Number.isFinite(t.qty) && t.qty > 0;
  }

  // ─── TapeStore(max) — time & sales ring (§4) ───────────────────────────
  //
  // Holds the last `max` normalized trades from every leg (the tape view mixes
  // exchanges deliberately — each row carries `ex`). Ring, not array: a busy
  // BTC session prints ~10–20 trades/s across three venues and the tape only
  // ever shows the recent past.
  function TapeStore(max) {
    const ring = makeRing(finiteOr(max, 2000));

    /** Ingest one normalized trade event. Non-finite fields → dropped (hygiene). */
    function push(trade) {
      if (!validTrade(trade)) return;
      ring.push(trade);
    }

    /** Trades with notional (price·qty, USD) ≥ minNotional, NEWEST-FIRST —
     *  the order a tape renders in. Non-finite filter → 0 (i.e. everything). */
    function filtered(minNotional) {
      const min = finiteOr(minNotional, 0);
      const out = [];
      const all = ring.toArray();
      for (let i = all.length - 1; i >= 0; i--) {
        const t = all[i];
        if (t.price * t.qty >= min) out.push(t);
      }
      return out;
    }

    return { push, filtered, get length() { return ring.length; } };
  }

  // ─── BookStore() — one exchange's L2 book (§4) ─────────────────────────
  //
  // Bids/asks are Maps price→qty. Two wire realities drive applyDepth (§2,
  // verified in scripts/fixtures_ws.json):
  //   - Bybit orderbook.200 (§4b — upgraded from .50 in O-2; identical
  //     snapshot/delta semantics at any depth) sends a `snapshot` then `delta`
  //     frames where qty "0" DELETES a level (fixtures bybit_orderbook_delta +
  //     bybit_orderbook200_delta); OKX `books` updates use the same tombstone
  //     convention (fixture okx_books_update);
  //   - Binance depth20@100ms frames are each a FULL 20-level snapshot
  //     (adapter marks every frame isSnapshot:true).
  // So: isSnapshot → replace the side maps wholesale; delta → set/delete.
  function BookStore() {
    const bids = new Map(); // price → qty
    const asks = new Map();
    let lastTs = NaN;       // ts of the last applied event — staleness signal
                            // for views/AggBook (event time, never Date.now()).

    /** Apply one side's [[price, qty], …] rows. qty ≤ 0 deletes (Bybit delta
     *  convention). Non-finite price/qty rows are skipped, not zeroed. */
    function applySide(map, rows) {
      if (!Array.isArray(rows)) return;
      for (const r of rows) {
        const p = +r[0], q = +r[1];
        if (!Number.isFinite(p) || !Number.isFinite(q)) continue;
        if (q <= 0) map.delete(p);
        else map.set(p, q);
      }
    }

    /** Ingest a normalized depth event (§4 `{kind:'depth', …}`). */
    function applyDepth(ev) {
      if (!ev) return;
      if (ev.isSnapshot) { bids.clear(); asks.clear(); }
      applySide(bids, ev.bids);
      applySide(asks, ev.asks);
      if (Number.isFinite(ev.ts)) lastTs = ev.ts;
    }

    /** Best bid/ask as {bid:[p,q], ask:[p,q]} (null side when empty). Linear
     *  scan over ≤50 levels per side — cheaper and simpler than keeping the
     *  maps sorted on every delta. */
    function best() {
      let bb = null, ba = null;
      for (const [p, q] of bids) if (!bb || p > bb[0]) bb = [p, q];
      for (const [p, q] of asks) if (!ba || p < ba[0]) ba = [p, q];
      return { bid: bb, ask: ba };
    }

    /** Ladder grouped into tickSize buckets, ≤ nLevels per side, BEST-FIRST
     *  (bids descending, asks ascending). Bids bucket DOWN, asks bucket UP —
     *  see snapTick() for why that is the conservative direction. */
    function grouped(tickSize, nLevels) {
      const tick = finiteOr(tickSize, 1);
      const n = finiteOr(nLevels, Infinity);
      const side = (map, isBid) => {
        const acc = new Map();
        for (const [p, q] of map) {
          const b = snapTick(p, tick, !isBid);
          acc.set(b, (acc.get(b) || 0) + q);
        }
        const rows = [...acc.entries()]
          .sort((a, b2) => (isBid ? b2[0] - a[0] : a[0] - b2[0]))
          .slice(0, n)
          .map(([price, qty]) => ({ price, qty }));
        return rows;
      };
      return { bids: side(bids, true), asks: side(asks, false) };
    }

    // Maps + lastTs exposed read-style so AggBookStore/views/tests can inspect
    // raw levels and staleness without a copy per frame.
    return { applyDepth, best, grouped, bids, asks, get lastTs() { return lastTs; } };
  }

  // ─── AggBookStore(exs) — multi-exchange merged book (§4) ───────────────
  //
  // One BookStore per exchange; grouped() merges the per-venue ladders into
  // {price, total, byEx} rows (CryExc's aggregated orderbook). A stale or
  // never-connected leg simply has empty maps and contributes nothing — the
  // merge stays correct with any subset of legs alive (that is the whole
  // resilience story here: no leg is required, none is interpolated, §0.7).
  function AggBookStore(exs) {
    const books = new Map(); // ex → BookStore
    if (Array.isArray(exs)) for (const ex of exs) books.set(ex, BookStore());

    /** Route a depth event to its exchange's book (lazily admits an exchange
     *  not named at construction rather than dropping real data on the floor). */
    function applyDepth(ev) {
      if (!ev || !ev.ex) return;
      let b = books.get(ev.ex);
      if (!b) { b = BookStore(); books.set(ev.ex, b); }
      b.applyDepth(ev);
    }

    /** Merged grouped ladder, both sides, best-first, ≤ nLevels rows each:
     *  [{price, total, byEx:{ex: qty, …}}, …].
     *
     *  Correctness note on the per-leg cut: taking each book's top-n grouped
     *  levels BEFORE merging is exact, not an approximation — if a merged price
     *  ranks in the global top-n, then in every exchange holding it fewer than
     *  n prices sit above it (the union can only add prices), so it is inside
     *  every contributing leg's top-n and no byEx quantity is lost.
     *
     *  T-2 (§4h): optional `includeExs` (array of ex codes) is a DISPLAY-side
     *  include filter — the agg panel's per-leg checkboxes. Filtering happens
     *  at merge time so an excluded leg contributes nothing, but its book
     *  keeps ingesting untouched (unchecking a leg is a view choice, never a
     *  data drop). Omitted/invalid → all legs, the pre-T-2 behavior. */
    function grouped(tickSize, nLevels, includeExs) {
      const n = finiteOr(nLevels, Infinity);
      const inc = Array.isArray(includeExs) ? new Set(includeExs) : null;
      const merge = (isBid) => {
        const acc = new Map(); // price → {total, byEx}
        for (const [ex, book] of books) {
          if (inc && !inc.has(ex)) continue;
          const rows = book.grouped(tickSize, n)[isBid ? 'bids' : 'asks'];
          for (const { price, qty } of rows) {
            let cell = acc.get(price);
            if (!cell) { cell = { price, total: 0, byEx: {} }; acc.set(price, cell); }
            cell.total += qty;
            cell.byEx[ex] = (cell.byEx[ex] || 0) + qty;
          }
        }
        return [...acc.values()]
          .sort((a, b) => (isBid ? b.price - a.price : a.price - b.price))
          .slice(0, n);
      };
      return { bids: merge(true), asks: merge(false) };
    }

    // Per-exchange books exposed so terminal.js can drive staleness chips off
    // each leg's lastTs (event time — the chip logic supplies its own clock).
    return { applyDepth, grouped, books };
  }

  // ─── FootprintStore({barMs, tickSize, …}) — bid×ask footprint bars (§4) ─
  //
  // Per bar (bar index = floor(ts/barMs)): a Map priceLevel→{buy, sell} of
  // aggressor volume (base-asset units) plus o/h/l/c, delta (buyVol−sellVol)
  // and totalVol. Price levels bucket DOWN onto the tick grid (same floor
  // convention as ProfileStore so the session-VP gutter lines up with cells).
  //
  // Bars close on EVENT TIME only: a bar is finished when the first trade of a
  // later bar arrives. No timer, no wall clock (replay rail). Consequence we
  // accept and state: a bar with zero trades never exists — gaps are left as
  // gaps (§3 resilience rail parity), and the "current" bar stays open across
  // a quiet spell until flow resumes.
  //
  // Diagonal imbalance (§4 verbatim): `buy[i] ≥ k·sell[i+1]`, default k=3,
  // with levels indexed BEST-FIRST / DESCENDING price (ladder order) — so
  // sell[i+1] is the sell volume ONE TICK BELOW buy[i]. That is the classic
  // footprint diagonal: buys print at the ask, contemporaneous sells print at
  // the bid one tick lower, so buy(p) vs sell(p−tick) compares like-for-like.
  // Flags are computed ONLY when a bar finishes — flags on a half-formed bar
  // flicker and invite reading signal into incomplete prints.
  //
  // T-1 delta-pro additions (§4g): the running intra-bar delta PATH (signed
  // qty accumulated trade-by-trade, starting at 0 each bar) is tracked as it
  // forms → per-bar deltaMin/deltaMax (the path's extremes, both anchored at
  // 0 — a bar whose delta only ever rose has deltaMin 0, not the first
  // trade's value). deltaPct = delta/totalVol ∈ [−1, 1] (0 when totalVol is
  // 0 — a ratio over no volume is "no read" and renders neutral). Unfinished-
  // auction flags follow the finished-bar-only discipline above.
  function FootprintStore(opts) {
    const o = opts || {};
    const barMs = finiteOr(o.barMs, 60000);
    const tickSize = finiteOr(o.tickSize, 1);
    const imbK = finiteOr(o.imbalanceK, 3);          // §4 default k=3
    // Min-volume floor (base-asset units, i.e. BTC): without it, 0.001 vs 0
    // "wins" the ratio test trivially — especially against an EMPTY diagonal
    // neighbor, which we count as 0 rather than skipping (a genuine one-sided
    // sweep past a vacant level IS an imbalance; the floor keeps dust out).
    const imbMinVol = finiteOr(o.imbalanceMinVol, 1.0);
    const RING = 120;    // finished-bar ring (§4: last 120 bars)

    const finished = []; // ascending time order
    let cur = null;      // {idx, t, o,h,l,c, buyVol, sellVol, levels:Map}

    function newBar(idx, firstPrice) {
      return {
        idx, t: idx * barMs,
        o: firstPrice, h: firstPrice, l: firstPrice, c: firstPrice,
        buyVol: 0, sellVol: 0,
        runDelta: 0, dMin: 0, dMax: 0, // intra-bar delta path (§4g), 0-anchored
        levels: new Map(), // snapped price → {buy, sell}
      };
    }

    /** Freeze a bar into the render shape: levels as a DESCENDING-price array
     *  of {price, buy, sell, buyImb, sellImb}. `withFlags` only for finished
     *  bars (see header note). Diagonal lookups go through the Map at exactly
     *  price∓tick (roundPx-canonical), NOT the adjacent array slot — the array
     *  can have gaps and a gap is zero opposing volume, not "next level down". */
    function snapshot(bar, isFinished) {
      const prices = [...bar.levels.keys()].sort((a, b) => b - a); // ladder order
      const levels = prices.map((p) => {
        const c = bar.levels.get(p);
        return { price: p, buy: c.buy, sell: c.sell, buyImb: false, sellImb: false };
      });
      if (isFinished) {
        for (const row of levels) {
          const below = bar.levels.get(roundPx(row.price - tickSize));
          const above = bar.levels.get(roundPx(row.price + tickSize));
          row.buyImb = row.buy > 0 && row.buy >= imbMinVol
            && row.buy >= imbK * (below ? below.sell : 0);
          row.sellImb = row.sell > 0 && row.sell >= imbMinVol
            && row.sell >= imbK * (above ? above.buy : 0);
        }
      }
      // Unfinished-auction flags (§4g), finished bars only (same discipline as
      // the imbalance flags above). The classic orderflow marker, Dalton-
      // adjacent (Mind over Markets' "unfinished business" at an extreme,
      // read here from footprint prints instead of TPO singles): a CLEAN
      // extreme prints one side only — at the high the last business is
      // buyers lifting offers, at the low sellers hitting bids. If the
      // extreme level printed BOTH buy AND sell volume, two-sided trade was
      // still being done when price turned — the auction did not finish its
      // business there. Descriptive marker by convention; conventionally such
      // extremes get revisited, but that is a trader's read, not a claim.
      let unfinishedHigh = false, unfinishedLow = false;
      if (isFinished && levels.length) {
        const top = levels[0], bot = levels[levels.length - 1]; // desc order
        unfinishedHigh = top.buy > 0 && top.sell > 0;
        unfinishedLow = bot.buy > 0 && bot.sell > 0;
      }
      const delta = bar.buyVol - bar.sellVol;
      const totalVol = bar.buyVol + bar.sellVol;
      return {
        t: bar.t, o: bar.o, h: bar.h, l: bar.l, c: bar.c,
        buyVol: bar.buyVol, sellVol: bar.sellVol,
        delta, totalVol,
        deltaMin: bar.dMin, deltaMax: bar.dMax,
        deltaPct: totalVol > 0 ? delta / totalVol : 0,
        unfinishedHigh, unfinishedLow,
        levels, finished: isFinished,
      };
    }

    /** Ingest one normalized trade. Late prints (ts before the current bar)
     *  are DROPPED, not folded into the open bar and never written into a
     *  closed one — rewriting finished bars is fabrication (§0.7), and folding
     *  cross-feed clock skew forward would misplace flow in time. */
    function onTrade(t) {
      if (!validTrade(t)) return;
      const idx = Math.floor(t.ts / barMs);
      if (cur && idx > cur.idx) {
        finished.push(snapshot(cur, true));
        if (finished.length > RING) finished.shift();
        cur = null;
      } else if (cur && idx < cur.idx) {
        return; // late/out-of-order print — see note above
      }
      if (!cur) cur = newBar(idx, t.price);
      if (t.price > cur.h) cur.h = t.price;
      if (t.price < cur.l) cur.l = t.price;
      cur.c = t.price;
      const lp = snapTick(t.price, tickSize, false);
      let cell = cur.levels.get(lp);
      if (!cell) { cell = { buy: 0, sell: 0 }; cur.levels.set(lp, cell); }
      if (t.aggressorBuy) { cell.buy += t.qty; cur.buyVol += t.qty; cur.runDelta += t.qty; }
      else { cell.sell += t.qty; cur.sellVol += t.qty; cur.runDelta -= t.qty; }
      // One trade moves the delta path in exactly one direction — else-if is
      // exhaustive here, not an optimization shortcut.
      if (cur.runDelta < cur.dMin) cur.dMin = cur.runDelta;
      else if (cur.runDelta > cur.dMax) cur.dMax = cur.runDelta;
    }

    /** The open bar's snapshot (imbalance flags all false — not final), or null. */
    function current() {
      return cur ? snapshot(cur, false) : null;
    }

    /** Finished bars (ascending, ≤120) plus the open bar appended, per §4. */
    function bars() {
      const out = finished.slice();
      if (cur) out.push(snapshot(cur, false));
      return out;
    }

    return { onTrade, bars, current, barMs, tickSize };
  }

  // ─── CvdStore({bucketsUsd}) — cumulative volume delta by trade size (§4) ─
  //
  // Signed flow per trade: +qty·price if the aggressor bought, −qty·price if
  // they sold — USD NOTIONAL, not contracts, so legs from different venues sum
  // in one unit. On top of the overall CVD, one cumulative series per notional
  // bucket (CryExc's CVD-by-trade-size): a trade lands in the SMALLEST bucket
  // threshold ≥ its notional, or 'whale' above the largest. With the default
  // [1e4, 1e5, 1e6]: ≤$10k retail, ≤$100k mid, ≤$1M large, >$1M whale.
  //
  // SESSION-ANCHORED (§4): CVD has no natural zero — only its slope and
  // divergences mean anything — so the anchor is "since page open / since
  // reset()", stated in the view, never spliced onto history we didn't see.
  function CvdStore(opts) {
    const o = opts || {};
    const src = Array.isArray(o.bucketsUsd) && o.bucketsUsd.length ? o.bucketsUsd : [1e4, 1e5, 1e6];
    const thresholds = src.filter(Number.isFinite).slice().sort((a, b) => a - b);
    // Bucket keys are the numeric thresholds as strings, plus 'whale' — stable
    // machine keys; the view formats them ("≤$10k" etc.).
    const keys = thresholds.map(String).concat(['whale']);

    // Exact running totals (scalars — never decimated, always exact):
    let cumOverall = 0;
    let cumBy = {};
    // Sampled series for plotting:
    let t = [], overall = [], byBucket = {};
    // Sampling is per-trade but CAPPED. Why: BTC perp flow runs ~0.5–1.5M
    // trades/day (§3 sizing note); storing and re-plotting every point would
    // grow without bound and stall the rAF canvas loop within hours. Above
    // MAX_PTS we halve the stored series (keep every 2nd point) and DOUBLE the
    // forward sampling stride — i.e. "keep every Nth" with N growing as the
    // session ages. Only plot resolution coarsens; the cumulative scalars, and
    // therefore every future sample's VALUE, remain exact.
    const MAX_PTS = 20000;
    let stride = 1, sinceSample = 0;

    function zero() {
      cumOverall = 0; cumBy = {};
      t = []; overall = []; byBucket = {};
      for (const k of keys) { cumBy[k] = 0; byBucket[k] = []; }
      stride = 1; sinceSample = 0;
    }
    zero();

    function bucketKey(notional) {
      for (const th of thresholds) if (notional <= th) return String(th);
      return 'whale';
    }

    function decimate() {
      // Keep even indices: with an odd length both the first sample (session
      // anchor) and the newest survive.
      const half = (arr) => { const out = []; for (let i = 0; i < arr.length; i += 2) out.push(arr[i]); return out; };
      t = half(t); overall = half(overall);
      for (const k of keys) byBucket[k] = half(byBucket[k]);
      stride *= 2;
    }

    /** Ingest one normalized trade. */
    function onTrade(tr) {
      if (!validTrade(tr)) return;
      const notional = tr.qty * tr.price;
      const signed = tr.aggressorBuy ? notional : -notional;
      cumOverall += signed;
      cumBy[bucketKey(notional)] += signed;
      sinceSample++;
      if (sinceSample >= stride) {
        sinceSample = 0;
        t.push(tr.ts);
        overall.push(cumOverall);
        for (const k of keys) byBucket[k].push(cumBy[k]);
        if (t.length > MAX_PTS) decimate();
      }
    }

    /** Plot series {t, overall, byBucket}. Returns LIVE array references —
     *  treat as read-only; copying ~20k×(buckets+2) floats per rAF frame is
     *  pure waste. Invariant the fixture smoke asserts: Σ byBucket = overall
     *  at every sample (every signed dollar lands in exactly one bucket). */
    function series() {
      return { t, overall, byBucket };
    }

    /** Re-anchor the session (e.g. user reset). Wipes series AND totals. */
    function reset() { zero(); }

    return { onTrade, series, reset, buckets: keys.slice() };
  }

  // ─── ProfileStore({tickSize}) — session volume-at-price (§4) ────────────
  //
  // Session volume per snapped price level (floor-bucketed — same grid as
  // FootprintStore, so the VP gutter aligns with footprint cells). profile()
  // derives POC / VAH / VAL and HVN/LVN candidates on demand; the store keeps
  // only the raw map (single source of truth, nothing derived is cached stale).
  function ProfileStore(opts) {
    const tickSize = finiteOr(opts && opts.tickSize, 1);
    const vol = new Map(); // snapped price → base-asset volume
    let totalVol = 0;

    function onTrade(t) {
      if (!validTrade(t)) return;
      const lp = snapTick(t.price, tickSize, false);
      vol.set(lp, (vol.get(lp) || 0) + t.qty);
      totalVol += t.qty;
    }

    /** {poc, vah, val, levels:[{price, vol}] ascending, totalVol, hvn, lvn}.
     *
     *  Value area = standard 70% EXPANSION FROM POC: start at the POC row,
     *  then repeatedly compare the single next level ABOVE the accepted range
     *  vs the single next level BELOW and absorb whichever carries more volume
     *  (ties expand upward — arbitrary but deterministic), until ≥70% of total
     *  session volume is inside. VAH/VAL are the extreme accepted prices.
     *  (This is the one-row-at-a-time variant; the TPO two-row variant differs
     *  slightly at the margin — we state which one we use rather than letting
     *  readers assume.)
     *
     *  HVN/LVN are CANDIDATES, deliberately conservative: a level is HVN if it
     *  is a strict local maximum vs both tick-neighbors in the level array AND
     *  above the median level volume; LVN mirrored below the median. Strict
     *  ">" against both neighbors plus the median gate is a minimal prominence
     *  filter — it kills plateau noise but a real prominence threshold (e.g.
     *  x% of POC volume) is left to the view/user; do not read these as
     *  validated structure levels. */
    function profile() {
      if (!vol.size) {
        return { poc: NaN, vah: NaN, val: NaN, levels: [], totalVol: 0, hvn: [], lvn: [] };
      }
      const levels = [...vol.entries()]
        .map(([price, v]) => ({ price, vol: v }))
        .sort((a, b) => a.price - b.price); // ascending — VP renders low→high
      const n = levels.length;

      // POC: max-volume level; ties resolve to the LOWEST price (first strict
      // max in the ascending scan) — deterministic, and ties are vanishingly
      // rare with real flow.
      let pocIdx = 0;
      for (let i = 1; i < n; i++) if (levels[i].vol > levels[pocIdx].vol) pocIdx = i;

      // 70% expansion from POC (see doc above). -1 sentinels: an exhausted
      // side always loses the comparison since every real volume is > 0.
      const target = 0.7 * totalVol;
      let covered = levels[pocIdx].vol;
      let up = pocIdx + 1, dn = pocIdx - 1;
      while (covered < target && (up < n || dn >= 0)) {
        const vu = up < n ? levels[up].vol : -1;
        const vd = dn >= 0 ? levels[dn].vol : -1;
        if (vu >= vd) { covered += vu; up++; }
        else { covered += vd; dn--; }
      }
      const vah = levels[up - 1].price;
      const val = levels[dn + 1].price;

      // Median level volume (gate for HVN/LVN).
      const sorted = levels.map((l) => l.vol).sort((a, b) => a - b);
      const med = n % 2 ? sorted[(n - 1) / 2] : 0.5 * (sorted[n / 2 - 1] + sorted[n / 2]);

      const hvn = [], lvn = [];
      for (let i = 1; i < n - 1; i++) { // edges have one neighbor — excluded
        const v = levels[i].vol, a = levels[i - 1].vol, b = levels[i + 1].vol;
        if (v > a && v > b && v > med) hvn.push(levels[i].price);
        else if (v < a && v < b && v < med) lvn.push(levels[i].price);
      }

      return { poc: levels[pocIdx].price, vah, val, levels, totalVol, hvn, lvn };
    }

    return { onTrade, profile, tickSize };
  }

  // ─── LiqStore(max) — liquidation feed ring + rolling sums (§4) ──────────
  //
  // Ring of normalized liq events (§4 `{kind:'liq', ex, ts, side, price, qty,
  // notionalUsd}`; side = the LIQUIDATED position per §3 — Bybit `Buy` print =
  // a short got force-bought, normalized upstream). Rolling sums are computed
  // over event timestamps against the LAST EVENT's ts by default — no wall
  // clock (replay rail). A live view that wants "the last 60s of wall time"
  // passes its own `nowTs`; the store itself never asks the OS what time it is.
  function LiqStore(max) {
    const ring = makeRing(finiteOr(max, 500));
    let lastTs = NaN;

    function push(liq) {
      if (!liq || !Number.isFinite(liq.ts) || !Number.isFinite(liq.notionalUsd)) return;
      ring.push(liq);
      if (!(lastTs >= liq.ts)) lastTs = liq.ts; // monotone anchor (NaN-safe)
    }

    /** Total liquidated notional (USD) inside (nowTs − ms, nowTs]. Default
     *  anchor = newest event ts (frozen during quiet spells — honest under
     *  replay; pass wall-clock nowTs from the view for live decay). The 1m/5m
     *  header sums are sumWindow(60000) / sumWindow(300000).
     *  Caveat stated, not hidden: the window can only see what the ring still
     *  holds — with a tiny `max` during a cascade, oldest-in-window events may
     *  already be evicted, so size the ring generously (default 500). */
    function sumWindow(ms, nowTs) {
      const now = Number.isFinite(nowTs) ? nowTs : lastTs;
      if (!Number.isFinite(now) || !Number.isFinite(ms)) return 0;
      const cutoff = now - ms;
      let s = 0;
      for (const l of ring.toArray()) {
        if (l.ts > cutoff && l.ts <= now) s += l.notionalUsd;
      }
      return s;
    }

    /** Newest-first slice for the feed view (default: everything held). */
    function recent(n) {
      const all = ring.toArray().reverse();
      return Number.isFinite(n) ? all.slice(0, Math.max(0, n)) : all;
    }

    return { push, sumWindow, recent, get length() { return ring.length; }, get lastTs() { return lastTs; } };
  }

  // ─── DepthHistoryStore({tickSize, maxSamples, nLevels}) — book history ring (§4b) ─
  //
  // Session-local history of the RESTING book: one grouped-ladder snapshot per
  // sample() call, held in a fixed ring so the orderbook heatmap (X = session
  // time, Y = price, alpha ∝ resting qty) can render without unbounded growth.
  // Memory is bounded BY CONSTRUCTION (§4b): maxSamples × 2 sides × nLevels
  // (defaults 3600 × 2×40 ≈ one hour of book at the 1/s cadence).
  //
  // CADENCE IS THE CALLER'S JOB (§4b): terminal.js samples on rAF only when the
  // book's newest EVENT ts advanced ≥ 1 s — so `ts` here is wire time and this
  // store stays Date.now()-free (replay rail: same tape in → same ring out).
  // Sampling faster only shortens the visible window; nothing here breaks.
  //
  // §0.7 rail: the ring holds only ladders that actually stood this session —
  // no interpolation across gaps, no backfill. A reconnect gap is a gap.
  function DepthHistoryStore(opts) {
    const o = opts || {};
    const tickSize = finiteOr(o.tickSize, 1);
    const maxSamples = finiteOr(o.maxSamples, 3600);
    const nLevels = finiteOr(o.nLevels, 40);
    const ring = makeRing(maxSamples);

    /** Snapshot one exchange's BookStore into the ring: {ts, bids:Map bucket→qty,
     *  asks:Map bucket→qty}, top-nLevels per side via the book's own grouped()
     *  (same snap conventions as the DOM ladder — buckets line up across views).
     *
     *  GUARD (§4b): no-op while the book has no levels yet (pre-snapshot /
     *  mid-reconnect). Recording an all-zero column would render as a false
     *  "all liquidity vanished" band in the heatmap — absence of data is not
     *  data (§0.7), so we record nothing instead of recording zeros. */
    function sample(ts, bookStore) {
      if (!Number.isFinite(ts) || !bookStore) return;
      const g = bookStore.grouped(tickSize, nLevels);
      if (!g.bids.length && !g.asks.length) return; // empty book — see guard note
      const toMap = (rows) => {
        const m = new Map();
        for (const r of rows) m.set(r.price, r.qty);
        return m;
      };
      ring.push({ ts, bids: toMap(g.bids), asks: toMap(g.asks) });
    }

    /** Oldest→newest LIVE references — treat as read-only. Copying ~3600×80
     *  map entries per heatmap redraw would be pure waste (CvdStore precedent). */
    function samples() { return ring.toArray(); }

    /** {min, max} price bucket across every held sample, both sides — the
     *  heatmap's Y extent. NaN/NaN when empty (ProfileStore's NaN convention:
     *  "no data" must never look like price 0). Full scan on demand — callers
     *  redraw behind a dirty flag, not per frame, so O(samples×levels) is fine. */
    function priceRange() {
      let min = Infinity, max = -Infinity;
      for (const s of ring.toArray()) {
        for (const m of [s.bids, s.asks]) {
          for (const p of m.keys()) {
            if (p < min) min = p;
            if (p > max) max = p;
          }
        }
      }
      return min <= max ? { min, max } : { min: NaN, max: NaN };
    }

    /** Resting-qty velocity at one price bucket: Δqty/Δseconds between the
     *  oldest and newest samples inside the window ENDING AT THE NEWEST
     *  SAMPLE'S ts (event-time anchor — no wall clock, replay rail). Positive
     *  = liquidity building, negative = draining. Returns 0 when fewer than 2
     *  samples fall in the window (§4b) or Δt ≤ 0 — "unknown" renders as flat,
     *  never as NaN poisoning a canvas gradient. A bucket absent from a sample
     *  counts as qty 0: a deleted level is genuinely gone, that IS the signal.
     *  Bucket is roundPx-canonicalized so caller float math still hits the key. */
    function velocity(bucket, windowMs) {
      if (!Number.isFinite(bucket) || !Number.isFinite(windowMs)) return 0;
      const all = ring.toArray();
      if (all.length < 2) return 0;
      const b = roundPx(bucket);
      const last = all[all.length - 1];
      const cutoff = last.ts - windowMs;
      let first = null;
      for (const s of all) {
        if (s.ts >= cutoff) { first = s; break; }
      }
      if (!first || first === last) return 0; // <2 samples in window
      const dt = (last.ts - first.ts) / 1000;
      if (!(dt > 0)) return 0;
      const qtyAt = (s) => (s.bids.get(b) || 0) + (s.asks.get(b) || 0);
      return (qtyAt(last) - qtyAt(first)) / dt;
    }

    return { sample, samples, priceRange, velocity, tickSize };
  }

  // ─── SpoofIcebergDetector({wallK, wallWindowMs, …}) — HEURISTIC flags (§4b) ─
  //
  // ⚠ HEURISTIC, NOT PROOF (§4b rail, stated on every event AND on the panel):
  // these rules flag order-book patterns *consistent with* spoofing (a large
  // resting order pulled before it trades) and icebergs (hidden size refilling
  // behind a small display). INTENT IS UNOBSERVABLE from public L2 data — a
  // "spoof-pull" can be an honest market maker re-quoting on new information,
  // an "iceberg-refill" can be coincidental independent flow at a busy level.
  // Every emitted event therefore carries label:'heuristic' and the views keep
  // that badge visible. Nothing here ever feeds a signal (§0.1).
  //
  // Inputs (both event-time, no Date.now() — replay rail):
  //   onDepthSample(ts, grouped) — a BookStore.grouped(tick, n) result, same
  //     ≤1/s cadence as DepthHistoryStore (terminal.js feeds both together).
  //   onTrade(t)                 — normalized §4 trade events from the SAME venue.
  // Per-bucket state (§4b): displayed-size history + traded volume, both
  // bounded windows, evicted by EVENT ts as events arrive.
  //
  // `tickSize` (extra opt, default 1) MUST match the tick the caller used for
  // grouped(): trades are snapped onto the same grid so "traded volume at that
  // bucket" compares like-for-like with displayed size at that bucket.
  function SpoofIcebergDetector(opts) {
    const o = opts || {};
    const wallK = finiteOr(o.wallK, 8);                    // wall = ≥ k × median level size
    const wallWindowMs = finiteOr(o.wallWindowMs, 15000);  // pull must happen this fast
    const tradeCoverPct = finiteOr(o.tradeCoverPct, 0.2);  // traded < pct×wall ⇒ not "eaten"
    const icebergM = finiteOr(o.icebergM, 3);              // traded ≥ m × max displayed
    const icebergWindowMs = finiteOr(o.icebergWindowMs, 60000);
    const minQty = finiteOr(o.minQty, 0);                  // dust floor (base units) for both rules
    const tickSize = finiteOr(o.tickSize, 1);              // see header — must match grouped()

    // One retention horizon serves both rules (spoof cover lookups never reach
    // farther back than wallWindowMs ≤ retainMs; iceberg uses icebergWindowMs).
    const retainMs = Math.max(wallWindowMs, icebergWindowMs);
    const ring = makeRing(100); // §4b: events() ring(100)
    // bucket → {disp:[{ts,qty}], traded:[{ts,qty}], lastIcebergTs}. Bounded:
    // entries evicted past retainMs on every touch + a full sweep per depth
    // sample drops buckets whose histories emptied (price drifts, memory doesn't).
    const stats = new Map();
    // bucket → {side:'bid'|'ask', firstTs, maxQty} — walls currently on display.
    const walls = new Map();

    function bucketStats(b) {
      let s = stats.get(b);
      if (!s) { s = { disp: [], traded: [], lastIcebergTs: -Infinity }; stats.set(b, s); }
      return s;
    }

    /** Drop leading entries older than cutoff (arrays are ts-ascending because
     *  events arrive in tape order; a stray out-of-order ts just evicts late). */
    function evictOld(arr, cutoff) {
      let i = 0;
      while (i < arr.length && arr[i].ts < cutoff) i++;
      if (i) arr.splice(0, i);
    }

    /** Ingest one ≤1/s grouped ladder. Detects wall births and wall pulls. */
    function onDepthSample(ts, grouped) {
      if (!Number.isFinite(ts) || !grouped) return;
      const bids = Array.isArray(grouped.bids) ? grouped.bids : [];
      const asks = Array.isArray(grouped.asks) ? grouped.asks : [];
      const cutoff = ts - retainMs;

      // 1. Record displayed sizes + collect this sample's ladder stats.
      const present = new Map(); // bucket → displayed qty this sample
      const sideBy = new Map();  // bucket → 'bid' | 'ask'
      const qtys = [];
      const ingest = (rows, side) => {
        for (const r of rows) {
          if (!r || !Number.isFinite(r.price) || !Number.isFinite(r.qty) || r.qty <= 0) continue;
          present.set(r.price, (present.get(r.price) || 0) + r.qty);
          if (!sideBy.has(r.price)) sideBy.set(r.price, side);
          qtys.push(r.qty);
          const s = bucketStats(r.price);
          s.disp.push({ ts, qty: r.qty });
        }
      };
      ingest(bids, 'bid');
      ingest(asks, 'ask');
      // An EMPTY ladder is a reconnect gap, not a 40-level mass pull — firing
      // on it would fabricate events out of missing data (§0.7). Skip.
      if (!qtys.length) return;

      // 2. Wall registration. "Wall" = a level ≥ wallK × the MEDIAN level size
      //    on this ladder (§4b) — median, not mean, so the wall itself (or one
      //    whale neighbor) cannot drag the baseline up and hide itself. A wall
      //    keeps its ORIGINAL firstTs while it stays displayed: re-stamping on
      //    every sample would let a wall that stood for minutes fire as a
      //    "fast pull" when it finally leaves — lifetime must be honest.
      qtys.sort((a, b) => a - b);
      const n = qtys.length;
      const med = n % 2 ? qtys[(n - 1) / 2] : 0.5 * (qtys[n / 2 - 1] + qtys[n / 2]);
      const wallThresh = Math.max(wallK * med, minQty);
      for (const [bucket, qty] of present) {
        const w = walls.get(bucket);
        if (w) { if (qty > w.maxQty) w.maxQty = qty; } // wall size = max ever displayed
        else if (qty >= wallThresh) {
          walls.set(bucket, { side: sideBy.get(bucket), firstTs: ts, maxQty: qty });
        }
      }

      // 3. Pull detection on tracked walls that VANISHED this sample.
      //    "Vanished" at grouped-bucket resolution means the displayed size
      //    collapsed back to ORDINARY (≤ this ladder's median), not that the
      //    bucket printed exactly zero: after a real pull, OTHER participants'
      //    residual orders keep the bucket alive with normal-looking size —
      //    requiring literal emptiness would blind the detector to nearly
      //    every actual pull. The wall (the anomalous size) is what left.
      //    Partial shrinks that stay above median keep tracking silently.
      //
      //    Visibility guard: grouped() is a top-N window — a wall bucket can
      //    leave the ladder entirely because price drifted and N better levels
      //    now sit in front of it. That is NOT a pull, and from top-N data the
      //    two are indistinguishable, so a wall whose bucket is ABSENT and
      //    outside the still-visible price range of its side is dropped
      //    SILENTLY (say nothing rather than fabricate). A bucket still
      //    displayed, or absent while inside the visible range, is a real read.
      const rangeOf = (rows) => {
        let min = Infinity, max = -Infinity;
        for (const r of rows) {
          if (!r || !Number.isFinite(r.price)) continue;
          if (r.price < min) min = r.price;
          if (r.price > max) max = r.price;
        }
        return min <= max ? { min, max } : null;
      };
      const vis = { bid: rangeOf(bids), ask: rangeOf(asks) };
      for (const [bucket, w] of [...walls]) {
        const dispQty = present.get(bucket) || 0;
        if (dispQty > med) continue; // still wall-ish — see "vanished" note above
        // Collapse guard: "≤ median" alone could also mean the MEDIAN rose
        // (whole ladder thickened around an unchanged order) — nothing was
        // pulled. Require the size itself to have fallen below 1/wallK of the
        // wall's max — un-walled by the same multiplier that made it a wall.
        if (dispQty > w.maxQty / wallK) continue; // not collapsed — keep tracking
        walls.delete(bucket); // resolved below, one way or the other
        if (!present.has(bucket)) {
          const vr = vis[w.side];
          if (!vr || bucket < vr.min || bucket > vr.max) continue; // scrolled out — unknowable
        }
        const lifetimeMs = ts - w.firstTs;
        // Spoof-pull rule (§4b, verbatim): vanished within wallWindowMs AND
        // traded volume at that bucket over the wall's lifetime < tradeCoverPct
        // × wall size (a wall that got EATEN was real liquidity, not a spoof).
        if (!(lifetimeMs >= 0 && lifetimeMs <= wallWindowMs)) continue; // stood too long (or ts skew) — a real wall that left
        const st = stats.get(bucket);
        let covered = 0;
        if (st) for (const tr of st.traded) if (tr.ts >= w.firstTs && tr.ts <= ts) covered += tr.qty;
        if (covered < tradeCoverPct * w.maxQty) {
          ring.push({ kind: 'spoof-pull', ts, price: bucket, size: w.maxQty, lifetimeMs, label: 'heuristic' });
        }
      }

      // 4. Bounded-memory sweep (§4b: evict beyond windows by event ts). Buckets
      //    whose histories emptied AND that hold no live wall are forgotten —
      //    a session that drifts $2k does not accumulate dead per-price state.
      for (const [bucket, s] of stats) {
        evictOld(s.disp, cutoff);
        evictOld(s.traded, cutoff);
        if (!s.disp.length && !s.traded.length && !walls.has(bucket)) stats.delete(bucket);
      }
    }

    /** Ingest one normalized trade from the same venue as the depth samples. */
    function onTrade(t) {
      if (!validTrade(t)) return;
      const cutoff = t.ts - retainMs;
      // Grid attribution: depth buckets snap DOWN on bids and UP on asks
      // (snapTick), so an off-grid print is attributable to either side's
      // bucket. We credit BOTH (they coincide for on-grid prints). Effect,
      // stated not hidden: spoof-pull gets MORE cover volume → harder to fire
      // (conservative — good for a heuristic); iceberg counts a ±1-tick band
      // around the bucket — acceptable at heatmap resolution and labeled.
      const down = snapTick(t.price, tickSize, false);
      const up = snapTick(t.price, tickSize, true);
      const buckets = up === down ? [down] : [down, up];
      for (const b of buckets) {
        const s = bucketStats(b);
        s.traded.push({ ts: t.ts, qty: t.qty });
        evictOld(s.traded, cutoff);
        evictOld(s.disp, cutoff);
        // Iceberg-refill rule (§4b, verbatim): traded volume at the bucket
        // within icebergWindowMs ≥ icebergM × the max DISPLAYED size there.
        // maxDisp must be > 0: a bucket that never displayed anything has no
        // refill to observe — trading through empty space is not an iceberg.
        // Checked on trades only: samples can't newly satisfy it (they only
        // raise maxDisp), and the eviction that lowers maxDisp is re-run here.
        const icut = t.ts - icebergWindowMs;
        let traded = 0;
        for (const tr of s.traded) if (tr.ts >= icut) traded += tr.qty;
        let maxDisp = 0;
        for (const d of s.disp) if (d.ts >= icut && d.qty > maxDisp) maxDisp = d.qty;
        if (maxDisp > 0 && traded >= icebergM * maxDisp && traded >= minQty
            && t.ts - s.lastIcebergTs >= icebergWindowMs) {
          // Re-arm only after a full window — without this, every subsequent
          // print at the level would re-emit the same finding 100× (ring spam).
          s.lastIcebergTs = t.ts;
          ring.push({
            kind: 'iceberg-refill', ts: t.ts, price: b,
            tradedQty: traded, maxDisplayed: maxDisp, label: 'heuristic',
          });
        }
      }
    }

    /** Last ≤100 events, oldest→newest, EVERY one labeled 'heuristic' (§4b —
     *  the label rides the event so no view can drop it by accident). Feed
     *  views reverse for newest-first display, as with LiqStore.recent(). */
    function events() { return ring.toArray(); }

    return { onDepthSample, onTrade, events };
  }

  // ─── LiqHeatmapModel({tiers, mmr, tickSize}) — ESTIMATED liq bands (§4b) ─
  //
  // ⚠ MODEL ESTIMATE, NOT OBSERVED DATA (§0.4 rail — same class as max_pain/
  // unsigned gamma): true liquidation prices require knowing every position's
  // entry, leverage and margin mode, none of which is knowable keyless. This
  // model PROXIES them, and every output carries label:'estimated':
  //   - entry prices  ∝ session volume-at-price (ProfileStore levels): where
  //     volume printed is where positions were opened — a proxy, not a ledger;
  //   - leverage mix  = the given tiers weighted EQUALLY (§4b: stated model
  //     assumption — the real tier mix is unknowable keyless, so we refuse to
  //     invent a distribution and say so instead);
  //   - isolated-margin, linear-contract formula with a flat maintenance
  //     margin rate (real venues tier mmr by position size — ignored, stated):
  //       long-liq  ≈ entry · (1 − 1/L + mmr)   — BELOW entry
  //       short-liq ≈ entry · (1 + 1/L − mmr)   — ABOVE entry
  // Observed liquidation prints pass through UNTOUCHED in `observed` — they
  // are rendered distinctly and NEVER blended into the estimated bands (§4b).
  function LiqHeatmapModel(opts) {
    const o = opts || {};
    const srcTiers = Array.isArray(o.tiers) && o.tiers.length ? o.tiers : [5, 10, 25, 50, 100];
    // L must be > 1: at L ≤ 1 the formula puts "liquidation" at ≈ entry·mmr
    // (no leverage → no forced liquidation level worth drawing). Filtered, not
    // clamped — a nonsense tier is a caller bug we surface by ignoring it.
    const tiers = srcTiers.filter((L) => Number.isFinite(L) && L > 1);
    const mmr = finiteOr(o.mmr, 0.005);
    const tickSize = finiteOr(o.tickSize, 1);

    /** estimate(profileLevels, mark, observedLiqs) → {bands, observed, label}.
     *  - profileLevels: ProfileStore.profile().levels ([{price, vol}]) — the
     *    volume-weighted entry proxy. weight ∝ level volume (§4b).
     *  - mark: current mark price — the liveness filter (below).
     *  - observedLiqs: LiqStore prints, passed through (lightly validated).
     *
     *  Band prices snap onto the tickSize grid AWAY from mark (long bands
     *  floor DOWN, short bands ceil UP) — the same conservative direction as
     *  the book's snapTick: an estimate is never displayed CLOSER to mark
     *  than the math puts it. Same-bucket weights merge by summation (§4b).
     *
     *  LIVENESS FILTER (§4b): only bands on the correct side of mark survive —
     *  long bands strictly BELOW mark, short bands strictly ABOVE. A long
     *  whose liq price sits at/above the current mark has ALREADY been
     *  liquidated (or never existed) — its position is gone, so drawing its
     *  band would show liquidity that cannot fire. Filtered on the BUCKETED
     *  price (what the view actually draws — deterministic for the fixture).
     *
     *  Weights are normalized post-merge/post-filter so max = 1 (proportions
     *  preserved — "∝ volume" holds; views map weight straight to alpha).
     *  No mark / no tiers / no levels → empty bands, never NaN bands. */
    function estimate(profileLevels, mark, observedLiqs) {
      const observed = Array.isArray(observedLiqs)
        ? observedLiqs.filter((l) => l && Number.isFinite(l.ts) && Number.isFinite(l.price))
        : [];
      if (!Number.isFinite(mark) || !tiers.length || !Array.isArray(profileLevels)) {
        return { bands: [], observed, label: 'estimated' };
      }
      const perTier = 1 / tiers.length; // equal tier weights — §4b model assumption
      const longAcc = new Map();  // bucket → weight (merged)
      const shortAcc = new Map();
      for (const lv of profileLevels) {
        if (!lv || !Number.isFinite(lv.price) || !Number.isFinite(lv.vol)) continue;
        if (lv.price <= 0 || lv.vol <= 0) continue; // hygiene: no phantom entries
        const w = lv.vol * perTier;
        for (const L of tiers) {
          const longB = snapTick(lv.price * (1 - 1 / L + mmr), tickSize, false);
          const shortB = snapTick(lv.price * (1 + 1 / L - mmr), tickSize, true);
          if (longB < mark) longAcc.set(longB, (longAcc.get(longB) || 0) + w);
          if (shortB > mark) shortAcc.set(shortB, (shortAcc.get(shortB) || 0) + w);
        }
      }
      const bands = [];
      for (const [price, weight] of longAcc) bands.push({ price, weight, side: 'long' });
      for (const [price, weight] of shortAcc) bands.push({ price, weight, side: 'short' });
      let wmax = 0;
      for (const b of bands) if (b.weight > wmax) wmax = b.weight;
      if (wmax > 0) for (const b of bands) b.weight /= wmax;
      bands.sort((a, b) => a.price - b.price); // ascending — heatmap renders low→high
      return { bands, observed, label: 'estimated' };
    }

    return { estimate, tiers: tiers.slice(), mmr, tickSize };
  }

  // ════════ O-3 (§4c) — structure builders: pure functions over klines ═════
  //
  // Everything below is a PURE FUNCTION (plus one tiny gated store) over
  // chronological kline bars `[{ts,o,h,l,c,v}]` as normalized by
  // terminal-hist.js. Wire reality worth restating here (§4c empirical data
  // map, fixtures `bybit_rest_kline` + `_o3_notes`): Bybit REST klines arrive
  // NEWEST-FIRST as string arrays — the fetcher reverses and Number()s BEFORE
  // these builders ever see them; feeding a raw (un-reversed) list would build
  // mirror-image sessions. Same two rails as the stores above: zero DOM, zero
  // Date.now() — `new Date(ms)` over an INPUT timestamp (UTC date label) is a
  // pure computation, not a wall-clock read.

  /** Positive-finite-or-default: tick/period params must be > 0 — a zero or
   *  negative tick would loop forever (or backwards) enumerating buckets, so
   *  finiteOr() alone is not a sufficient guard here. */
  function posOr(x, dflt) {
    return Number.isFinite(x) && x > 0 ? x : dflt;
  }

  /** Well-formed kline bar for range work: ts + a sane low ≤ high. (v is
   *  checked by buildKlineVp, the only consumer that reads it; o/c are not
   *  consumed by any §4c builder.) Malformed bars are SKIPPED, never zero-
   *  coerced — same hygiene rule as validTrade(). */
  function validBar(b) {
    return !!b && Number.isFinite(b.ts) && Number.isFinite(b.l)
      && Number.isFinite(b.h) && b.h >= b.l;
  }

  // Hygiene cap on tick buckets per bar. A mis-scaled tickSize (say $0.01 on a
  // $60k instrument with $500 bar ranges) or one corrupt h/l pair would try to
  // allocate millions of row objects and stall the tab; a single bar spanning
  // more ticks than this is bad data or a bad parameter, not market structure.
  // Such bars are skipped (stated here, not hidden) rather than guessed at.
  const MAX_BAR_BUCKETS = 20000;

  /** Tick buckets covering [l, h], BOTH ends floor-snapped — the same DOWN-
   *  bucketing grid as FootprintStore/ProfileStore, so TPO rows and kline-VP
   *  levels line up with footprint cells and the session-VP gutter across
   *  views. Returns null (caller skips the bar) on a corrupt/oversized range
   *  — see MAX_BAR_BUCKETS. Prices are roundPx-canonical Map keys. */
  function barBuckets(l, h, tick) {
    const b0 = snapTick(l, tick, false);
    const nUp = Math.round((snapTick(h, tick, false) - b0) / tick);
    if (!(nUp >= 0) || nUp > MAX_BAR_BUCKETS) return null;
    const out = new Array(nUp + 1);
    for (let k = 0; k <= nUp; k++) out[k] = roundPx(b0 + k * tick);
    return out;
  }

  /** 70% value-area expansion over an ascending-price weight array — the SAME
   *  algorithm as ProfileStore.profile() (§4c binding: "value area = same 70%
   *  expansion as ProfileStore"), factored out so buildTpo (weights = TPO
   *  period counts) and buildKlineVp (weights = volumes) share one
   *  implementation instead of drifting: start at the POC row, then repeatedly
   *  absorb whichever SINGLE next row (above the accepted range vs below)
   *  carries more weight — ties expand UPWARD, arbitrary but deterministic —
   *  until ≥ 70% of total weight is covered. -1 sentinels: an exhausted side
   *  always loses the comparison since every real weight is > 0. Returns
   *  {loIdx, hiIdx} index bounds into the weights array. */
  function valueArea70(weights, pocIdx) {
    const n = weights.length;
    let total = 0;
    for (const w of weights) total += w;
    const target = 0.7 * total;
    let covered = weights[pocIdx];
    let up = pocIdx + 1, dn = pocIdx - 1;
    while (covered < target && (up < n || dn >= 0)) {
      const vu = up < n ? weights[up] : -1;
      const vd = dn >= 0 ? weights[dn] : -1;
      if (vu >= vd) { covered += vu; up++; }
      else { covered += vd; dn--; }
    }
    return { loIdx: dn + 1, hiIdx: up - 1 };
  }

  // ─── buildTpo(bars, {tickSize, periodMs}) — Market Profile / TPO (§4c) ───
  //
  // CLASSICAL TPO CONSTRUCTION — this IS the canonical method, not an
  // approximation: Market Profile (Steidlmayer/CBOT) marks, for each 30-minute
  // period, every price the market touched during that period. A 30m OHLC bar
  // records exactly that touch range [l, h], so "one bar = one period letter
  // across its full low..high" reproduces the textbook profile. Unlike
  // buildKlineVp below, nothing intra-bar is being approximated, because TPO
  // never used intra-period distribution in the first place — only touched/
  // not-touched per period.
  //
  // Session = UTC DAY, exchange-agnostic: crypto perps trade 24/7 with no pit
  // open/close to anchor a session, and the UTC day is the convention the
  // venues' own daily klines and our collector timestamps (§3 schema, UTC ms)
  // already use — so the profile a Bybit reader sees matches an OKX reader's.
  //
  // Period index is CLOCK-derived — floor(intra-day offset / periodMs), so
  // 0..47 at the default 30m — NOT sequence-derived: a missing kline leaves a
  // HOLE in the letters instead of silently re-lettering later periods (gaps
  // are gaps, §0.7), and feeding finer bars (e.g. 5m) into 30m periods simply
  // merges six bars into one letter via the per-row period Set.
  //
  // Sessions return NEWEST-FIRST — the TPO view's session selector reads [0]
  // as "today" (same display convention as TapeStore.filtered/LiqStore.recent).
  function buildTpo(bars, opts) {
    const o = opts || {};
    const tick = posOr(o.tickSize, 1);
    const periodMs = posOr(o.periodMs, 1800000); // 30 m — the classical period
    const DAY_MS = 86400000;
    const days = new Map(); // dayIdx → {rows: Map(price → Set(periodIdx)), meta: [{p, l, h}]}
    if (Array.isArray(bars)) {
      for (const b of bars) {
        if (!validBar(b)) continue;
        const buckets = barBuckets(b.l, b.h, tick);
        if (!buckets) continue; // corrupt range / mis-scaled tick — see MAX_BAR_BUCKETS
        const dayIdx = Math.floor(b.ts / DAY_MS);
        const p = Math.floor((b.ts - dayIdx * DAY_MS) / periodMs);
        let d = days.get(dayIdx);
        if (!d) { d = { rows: new Map(), meta: [] }; days.set(dayIdx, d); }
        for (const price of buckets) {
          let set = d.rows.get(price);
          if (!set) { set = new Set(); d.rows.set(price, set); }
          set.add(p);
        }
        d.meta.push({ p, l: b.l, h: b.h }); // raw l/h retained for the IB bracket
      }
    }

    const sessions = [];
    for (const [dayIdx, d] of days) {
      const rows = [...d.rows.entries()]
        .map(([price, set]) => ({ price, periods: [...set].sort((a, b2) => a - b2) }))
        .sort((a, b2) => a.price - b2.price); // ascending — profile renders low→high
      const counts = rows.map((r) => r.periods.length);
      const lo = rows[0].price, hi = rows[rows.length - 1].price;

      // POC = the row holding the MOST periods; tie → closest to the session
      // MID (the classical TPO tiebreak — the profile's center of rotation),
      // with mid computed on the BUCKETED range so the tiebreak lives on the
      // same grid as the rows it ranks; a still-equidistant tie keeps the
      // LOWER price (the ascending scan keeps its first hit — deterministic,
      // same spirit as ProfileStore's lowest-price POC tie rule).
      const mid = (lo + hi) / 2;
      let pocIdx = 0;
      for (let i = 1; i < rows.length; i++) {
        if (counts[i] > counts[pocIdx]
            || (counts[i] === counts[pocIdx]
                && Math.abs(rows[i].price - mid) < Math.abs(rows[pocIdx].price - mid))) {
          pocIdx = i;
        }
      }

      // Value area on TPO COUNTS (periods per row), not volume — §4c: the
      // same 70% expansion as ProfileStore with the weight swapped for the
      // TPO analogue. VAH/VAL are the extreme accepted row prices.
      const va = valueArea70(counts, pocIdx);

      // Singles = rows printed in exactly ONE period AND strictly INSIDE the
      // session's bucket range. Edges are excluded because session extremes
      // are single-printed BY CONSTRUCTION — only the excursion bar touches
      // them; in Market Profile terms those are the tails, a different object.
      // Single-print analysis targets INTERIOR rows left behind by a fast
      // one-directional move — that is the actual structure read.
      const singles = [];
      for (const r of rows) {
        if (r.periods.length === 1 && r.price > lo && r.price < hi) singles.push(r.price);
      }

      // IB = range of the first 2 OBSERVED periods (classical initial balance
      // = the first hour = two 30m letters). "Observed" deliberately: the
      // oldest session of a limit-capped kline fetch starts mid-day, and
      // anchoring to clock periods 0–1 would fabricate an IB from bars we
      // never received (§0.7) — we bracket what actually arrived instead.
      // Raw (un-bucketed) l/h: the bracket is a price range, not a row.
      const ibIdx = new Set([...new Set(d.meta.map((m) => m.p))].sort((a, b2) => a - b2).slice(0, 2));
      let ibHi = -Infinity, ibLo = Infinity;
      for (const m of d.meta) {
        if (!ibIdx.has(m.p)) continue;
        if (m.h > ibHi) ibHi = m.h;
        if (m.l < ibLo) ibLo = m.l;
      }

      sessions.push({
        // Pure function of the bar ts — a date LABEL, not a wall-clock read.
        date: new Date(dayIdx * DAY_MS).toISOString().slice(0, 10),
        rows,
        poc: rows[pocIdx].price,
        vah: rows[va.hiIdx].price,
        val: rows[va.loIdx].price,
        singles,
        ib: { hi: ibHi, lo: ibLo },
      });
    }
    // Newest-first (ISO dates sort lexicographically = chronologically).
    sessions.sort((a, b2) => (a.date < b2.date ? 1 : a.date > b2.date ? -1 : 0));
    return sessions;
  }

  // ─── buildKlineVp(bars, {tickSize}) — composite VP from klines (§4c) ─────
  //
  // ⚠ APPROXIMATION, LABELED (§4c rail): OHLCV bars do not say WHERE inside
  // [l, h] the volume printed, so each bar's v is spread UNIFORMLY across its
  // tick buckets. The return value carries approx:'bar-range' and KlineVpView
  // keeps a permanent badge — tick-accurate volume-at-price is the footprint
  // gutter (live session) or the collector's stored trades (§3), NEVER this.
  function buildKlineVp(bars, opts) {
    const tick = posOr(opts && opts.tickSize, 1);
    const APPROX = 'bar-range'; // §4c label — rides the return value itself
    const vol = new Map(); // bucket → volume
    if (Array.isArray(bars)) {
      for (const b of bars) {
        // v must be a positive finite number: a zero-volume bar adds nothing
        // and its 0-rows would drag the HVN/LVN median gate toward zero.
        if (!validBar(b) || !Number.isFinite(b.v) || b.v <= 0) continue;
        const buckets = barBuckets(b.l, b.h, tick);
        if (!buckets) continue;
        const per = b.v / buckets.length; // the uniform spread — THE approximation
        for (const price of buckets) vol.set(price, (vol.get(price) || 0) + per);
      }
    }
    if (!vol.size) {
      // ProfileStore's NaN convention: "no data" must never look like price 0.
      return { levels: [], poc: NaN, vah: NaN, val: NaN, hvns: [], lvns: [], approx: APPROX };
    }
    const levels = [...vol.entries()]
      .map(([price, v]) => ({ price, vol: v }))
      .sort((a, b2) => a.price - b2.price); // ascending — VP renders low→high
    const vols = levels.map((l) => l.vol);
    const n = levels.length;

    // POC: max-volume level, ties → LOWEST price (ProfileStore convention).
    let pocIdx = 0;
    for (let i = 1; i < n; i++) if (vols[i] > vols[pocIdx]) pocIdx = i;
    const va = valueArea70(vols, pocIdx);

    // HVN/LVN: local extrema vs a ±2-neighbor window, gated on PROMINENCE ≥
    // 25% of the MEDIAN level volume. Why a prominence gate at all, and why
    // 25%-of-median: the uniform spread manufactures plateaus and ±1-bucket
    // ripple wherever overlapping bar ranges shift by a tick, so the strict
    // ">" rule ProfileStore uses on real tick data would flag that
    // discretization noise as structure here. Requiring a node to clear its
    // strongest (weakest) window neighbor by a quarter of a typical level's
    // volume keeps only features larger than the noise floor the bar-range
    // approximation itself introduces. The window is ±2 (not ±1) so a one-
    // bucket blip flanked at distance two by a near-equal cannot pass.
    // Edge rows are excluded: a node needs graded neighbors on BOTH sides.
    const sortedVols = vols.slice().sort((a, b2) => a - b2);
    const med = n % 2 ? sortedVols[(n - 1) / 2] : 0.5 * (sortedVols[n / 2 - 1] + sortedVols[n / 2]);
    const prom = 0.25 * med;
    const hvns = [], lvns = [];
    for (let i = 1; i < n - 1; i++) {
      let maxN = -Infinity, minN = Infinity;
      for (let j = Math.max(0, i - 2); j <= Math.min(n - 1, i + 2); j++) {
        if (j === i) continue;
        if (vols[j] > maxN) maxN = vols[j];
        if (vols[j] < minN) minN = vols[j];
      }
      if (vols[i] - maxN >= prom) hvns.push(levels[i].price);
      else if (minN - vols[i] >= prom) lvns.push(levels[i].price);
    }

    return {
      levels,
      poc: levels[pocIdx].price,
      vah: levels[va.hiIdx].price,
      val: levels[va.loIdx].price,
      hvns, lvns,
      approx: APPROX,
    };
  }

  // ─── rollingCorr(retsA, retsB, window) — rolling Pearson series (§4c) ────

  /** Pearson r over two equal-length CLEANED arrays (callers filter non-
   *  finite pairs first). NaN when n < 2 or either side has zero variance —
   *  the correlation of a constant is undefined, and NaN says "undefined"
   *  where a fabricated 0 would claim "uncorrelated" (quant.js hygiene).
   *  Clamped to [−1, 1]: float rounding can put a perfect fit at 1 + 2e−16
   *  and a view mapping r to a color scale must never see |r| > 1. */
  function pearsonR(xs, ys) {
    const n = xs.length;
    if (n < 2) return NaN;
    let mx = 0, my = 0;
    for (let i = 0; i < n; i++) { mx += xs[i]; my += ys[i]; }
    mx /= n; my /= n;
    let sxx = 0, syy = 0, sxy = 0;
    for (let i = 0; i < n; i++) {
      const dx = xs[i] - mx, dy = ys[i] - my;
      sxx += dx * dx; syy += dy * dy; sxy += dx * dy;
    }
    const den = Math.sqrt(sxx * syy);
    if (!(den > 0)) return NaN;
    return Math.max(-1, Math.min(1, sxy / den));
  }

  /** Rolling Pearson correlation over ALIGNED return arrays (§4c — MacroView's
   *  BTC×ETH×PAXG block; the caller aligns by bar timestamp first): one output
   *  {i, r} per index of the common length, r computed over the trailing
   *  `window` samples ending AT index i. NaN-safety, two layers:
   *    - a pair with either side non-finite is SKIPPED, never coerced to 0 —
   *      a fabricated flat return would drag r toward 0 (fake decorrelation);
   *    - r = NaN while the window holds fewer than window/2 valid pairs —
   *      below half a window the estimate is noise wearing a confident
   *      number, so we return "unknown" instead (small-n honesty, the same
   *      rail as SessionSeriesStore.corr's mandatory n).
   *  O(n·window) full recompute, no incremental sums: at kline scale
   *  (n ≤ ~1000, window ≤ ~168) that is microseconds, and it keeps the
   *  NaN-skipping exact instead of drift-prone. */
  function rollingCorr(retsA, retsB, window) {
    if (!Array.isArray(retsA) || !Array.isArray(retsB)) return [];
    const w = Math.floor(finiteOr(window, 0));
    if (w < 2) return []; // a 1-sample "correlation" is undefined — refuse
    const n = Math.min(retsA.length, retsB.length);
    const out = [];
    const xs = [], ys = [];
    for (let i = 0; i < n; i++) {
      xs.length = 0; ys.length = 0;
      for (let j = Math.max(0, i - w + 1); j <= i; j++) {
        const a = retsA[j], b = retsB[j];
        if (Number.isFinite(a) && Number.isFinite(b)) { xs.push(a); ys.push(b); }
      }
      out.push({ i, r: xs.length < w / 2 ? NaN : pearsonR(xs, ys) });
    }
    return out;
  }

  // ─── SessionSeriesStore({sampleMs}) — polled mids + session corr (§4c) ───
  //
  // For legs with NO history endpoint — HIP-3 dex perps expose live `allMids`
  // only; `candleSnapshot` returns empty/500 keyless (§4c empirical data map,
  // fixtures `_o3_notes`) — the ONLY honest correlation is one built from mids
  // WE sampled this session (§0.7: no fabricated history, no backfill from a
  // source that does not exist). This store is that accumulator: terminal.js
  // pushes every polled mid through onSample and the store keeps at most one
  // sample per key per sampleMs, gated on the EVENT/response ts — no
  // Date.now() (replay rail; the poller's timestamp is the clock).
  //
  // SESSION-ANCHORED + SMALL-N HONESTY (§4c): corr() returns {r, n} and
  // callers MUST display n — MacroView labels cells `session · n=…` and hides
  // them below n = 30. A correlation over twelve minutes of samples is an
  // anecdote; the sample count is part of the result, not droppable metadata.
  function SessionSeriesStore(opts) {
    const sampleMs = posOr(opts && opts.sampleMs, 60000);
    const byKey = new Map(); // key → [{ts, px}] ascending (the gate enforces order)

    /** Record one polled mid. Gated: accepted only when ts is ≥ sampleMs past
     *  the key's last ACCEPTED sample — a 5 s poller and a 60 s poller thus
     *  feed identical series (cadence lives here, not in the caller), and the
     *  same comparison drops out-of-order ts (recorded history is never
     *  rewritten). px must be a positive finite price: returns() takes logs.
     *  Memory: growth is gate-bounded (~1.4k samples/key/day at the default
     *  cadence) and deliberately NOT ring-capped — evicting old samples would
     *  silently turn "session-anchored" into "trailing window" and the
     *  `session · n=…` label would lie. */
    function onSample(ts, key, px) {
      if (!Number.isFinite(ts) || !Number.isFinite(px) || px <= 0) return;
      if (typeof key !== 'string' || !key) return;
      let arr = byKey.get(key);
      if (!arr) { arr = []; byKey.set(key, arr); }
      if (arr.length && ts - arr[arr.length - 1].ts < sampleMs) return;
      arr.push({ ts, px });
    }

    /** Accepted samples for one key, oldest→newest: [{ts, px}]. A copy —
     *  small by construction (see onSample), so safe to hand out, unlike the
     *  live-reference returns of CvdStore/DepthHistoryStore. Unknown key → []. */
    function series(key) {
      const arr = byKey.get(key);
      return arr ? arr.slice() : [];
    }

    /** Log-returns between CONSECUTIVE accepted samples: ln(px_i / px_{i−1}).
     *  Log, not simple (quant.js logReturns convention): symmetric, summable,
     *  and what corr()/rollingCorr consume. Guaranteed finite because
     *  onSample only admits positive finite prices. */
    function returns(key) {
      const arr = byKey.get(key) || [];
      const out = [];
      for (let i = 1; i < arr.length; i++) out.push(Math.log(arr[i].px / arr[i - 1].px));
      return out;
    }

    /** Session correlation {r, n} between two keys' RETURN series, paired by
     *  SAMPLE INDEX (§4c): every key is fed by the same poll loop from page
     *  open, so index i is the same wall-slice on both sides. Caveat stated,
     *  not hidden: a key that JOINED LATE is offset by its missed samples —
     *  that skew is the price of index pairing; it is why the anchor is the
     *  session and why n is mandatory display (a late joiner shows small n).
     *  Returns, not prices: price series are near-integrated, so price-level
     *  correlation reads ≈ 1 for any two drifting assets — meaningless.
     *  r is NaN below 2 valid pairs or at zero variance (pearsonR); n is the
     *  number of pairs actually used, NaN-skipped pairs excluded. */
    function corr(keyA, keyB) {
      const ra = returns(keyA), rb = returns(keyB);
      const m = Math.min(ra.length, rb.length);
      const xs = [], ys = [];
      for (let i = 0; i < m; i++) {
        if (Number.isFinite(ra[i]) && Number.isFinite(rb[i])) { xs.push(ra[i]); ys.push(rb[i]); }
      }
      return { r: pearsonR(xs, ys), n: xs.length };
    }

    return { onSample, series, returns, corr, sampleMs };
  }

  // ════════ O-4 (§4d) — intelligence builders: descriptive reads, NEVER signals ═
  //
  // §4d rail, restated where it bites: screener rows, confluence reads and
  // alert events are DESCRIPTIVE. The IC run-log (RESEARCH-ic-runlog.md)
  // measured ≈0 forward IC for board signals, so nothing below is a score to
  // trade — confluenceReads() carries that sentence on its return value and
  // AlertEngine events are attention triggers, not entries. Same two rails as
  // everything above: zero DOM, zero Date.now() (input ts is the only clock).

  // ─── buildScreener(tickerRows, {topN}) — turnover-ranked screener rows (§4d) ─
  //
  // PURE PASS-THROUGH SHAPING: the VIEW renders (bubbles, colors, hover);
  // this just ranks and slices. Input = normalizeBybitTickers() rows
  // (terminal-hist §4d shape: {sym, last, vwap24h, vwapDevPct, pct24h,
  // turnover24h, fundingRate, fundingIntervalH, annualizedFundingPct, oiUsd,
  // mark, index}) — ONE Bybit call carries all ~720 linear symbols (§4d
  // empirical map), so the slice keeps the bubble canvas readable; it is not
  // a data limit. Sort key = turnover24h (USD): the only ranking comparable
  // ACROSS symbols — volume24h is in base coins (38k BTC vs 34B PEPE says
  // nothing). Rows with non-finite turnover sink to the END, never get
  // dropped: `total` must state the true universe size so the view's
  // "top 40 of 720" header stays honest. topN ≤ 0 → all rows (§4d 'all'
  // passthrough). The input array is not mutated (slice-before-sort).
  function buildScreener(tickerRows, opts) {
    const topN = finiteOr(opts && opts.topN, 40);
    const src = Array.isArray(tickerRows) ? tickerRows.filter((r) => !!r) : [];
    const rows = src.slice().sort((a, b) => {
      const ta = Number.isFinite(a.turnover24h) ? a.turnover24h : -Infinity;
      const tb = Number.isFinite(b.turnover24h) ? b.turnover24h : -Infinity;
      return tb - ta;
    });
    return { rows: topN > 0 ? rows.slice(0, Math.floor(topN)) : rows, total: src.length };
  }

  // ─── confluenceReads(inputs) — the 9-category mechanical board (§4d) ─────
  //
  // EXACTLY the nine §4d categories (footprint Δ-trend, CVD slope, price vs
  // POC/VA, TPO position, funding sign/extreme, OI 1h change, liq-pressure
  // 5m, book top-10 imbalance, price vs SMA50), each read one of
  // 'bullish'|'bearish'|'neutral'|'n/a' plus a human detail string. Inputs
  // are PLAIN VALUES assembled by terminal.js from the live stores — this
  // function never touches a store (pure, replayable):
  //   { fpDeltas:[last N finished-bar deltas], cvdSlope, price, poc, vah,
  //     val, tpoPoc, tpoVah, tpoVal, fundingRate, fundingIntervalH (optional,
  //     default 8 — pass Bybit's response-provided fundingIntervalHour when
  //     known, §4d: not the 8h constant), oiChangePct1h (%/h), liqImb5m
  //     (−1..1, long-liq vs short-liq notional: +1 = all longs liquidated),
  //     bookImb (−1..1, top-10 bid vs ask depth: +1 = all bids), sma50,
  //     lastClose }
  //
  // A missing feed must NEVER default to 'neutral' — absence is not
  // information (§0.7): 'neutral' claims "I looked and it is balanced",
  // which is a fabricated read when nothing arrived. Missing/non-finite
  // input → 'n/a', counted in tally.na, never in the directional tally.
  //
  // The returned label is MANDATORY VIEW TEXT (§4d): it rides the return
  // value itself so no view can render the tally without it.
  function confluenceReads(inputs) {
    const inp = inputs || {};
    const fin = Number.isFinite;
    const reads = [];
    const NA = 'no feed'; // n/a detail — absence stated plainly, never dressed up
    const row = (category, read, detail) => { reads.push({ category, read, detail }); };

    // Shared value-area position rule (categories 3 + 4 — session VP and TPO
    // apply the same acceptance logic to their own levels): trading ABOVE
    // the value area = buyers accepting higher prices (bullish read), BELOW
    // = sellers accepting lower (bearish), INSIDE = balance/rotation —
    // a genuine 'neutral' read of a present feed, unlike 'n/a'.
    const vaRow = (category, price, poc, vah, val) => {
      if (!fin(price) || !fin(poc) || !fin(vah) || !fin(val)) return row(category, 'n/a', NA);
      if (price > vah) return row(category, 'bullish', 'price ' + price + ' > VAH ' + vah + ' (POC ' + poc + ')');
      if (price < val) return row(category, 'bearish', 'price ' + price + ' < VAL ' + val + ' (POC ' + poc + ')');
      return row(category, 'neutral', 'inside value ' + val + '..' + vah + ' (POC ' + poc + ')');
    };

    // 1. Footprint Δ-trend — net signed aggressor flow over the last N
    //    finished bars, scale-free: ΣΔ/Σ|Δ| ∈ [−1, 1]. Threshold 0.2 (WHY:
    //    net flow must be ≥20% of gross before calling a trend — below that,
    //    buyers and sellers traded near-symmetric size and the residual's
    //    sign is chop, not flow). Non-finite entries are skipped (hygiene);
    //    an empty/absent array is a missing feed → n/a, not neutral.
    {
      const deltas = Array.isArray(inp.fpDeltas) ? inp.fpDeltas.filter(fin) : [];
      if (!deltas.length) row('footprint Δ-trend', 'n/a', NA);
      else {
        let net = 0, gross = 0;
        for (const d of deltas) { net += d; gross += Math.abs(d); }
        const r = gross > 0 ? net / gross : 0; // all-zero deltas = genuinely balanced
        row('footprint Δ-trend',
          r > 0.2 ? 'bullish' : r < -0.2 ? 'bearish' : 'neutral',
          'ΣΔ/Σ|Δ| = ' + r.toFixed(2) + ' over ' + deltas.length + ' bars');
      }
    }

    // 2. CVD slope — SIGN only. WHY no magnitude threshold: the slope's unit
    //    depends on the caller's window choice; inventing a "$X/s is steep"
    //    constant here would be a threshold hidden in logic (§4d forbids
    //    exactly that for AlertEngine — same discipline here). Sign is
    //    unit-free and honest; exactly 0 is genuinely flat → neutral.
    {
      const s = inp.cvdSlope;
      if (!fin(s)) row('CVD slope', 'n/a', NA);
      else row('CVD slope', s > 0 ? 'bullish' : s < 0 ? 'bearish' : 'neutral', 'slope ' + s);
    }

    // 3. Price vs session POC/VA (live ProfileStore levels).
    vaRow('price vs POC/VA', inp.price, inp.poc, inp.vah, inp.val);

    // 4. TPO position (buildTpo's newest session levels) — same rule, the
    //    structural (30m-period) counterpart to the live profile above.
    vaRow('TPO position', inp.price, inp.tpoPoc, inp.tpoVah, inp.tpoVal);

    // 5. Funding sign/extreme — CONTRARIAN crowding read on the ANNUALIZED
    //    rate. WHY 30%/yr: neutral BTC perp funding ≈ 0.01%/8h ≈ 11%/yr, so
    //    30%/yr ≈ 3× baseline — one side is paying real money to stay in
    //    (crowded). Crowded longs (positive extreme) → bearish read, crowded
    //    shorts → bullish; anything milder is baseline carry → neutral.
    //    Interval: response-provided fundingIntervalHour when the caller
    //    passes it (§4d — not the 8h constant), 8h fallback otherwise.
    {
      const f = inp.fundingRate;
      if (!fin(f)) row('funding sign/extreme', 'n/a', NA);
      else {
        const intervalH = posOr(inp.fundingIntervalH, 8);
        const annPct = f * (8760 / intervalH) * 100;
        row('funding sign/extreme',
          annPct > 30 ? 'bearish' : annPct < -30 ? 'bullish' : 'neutral',
          annPct.toFixed(1) + '%/yr annualized' + (Math.abs(annPct) > 30 ? ' — crowded' : ''));
      }
    }

    // 6. OI 1h change — the classic board convention: rising OI = new
    //    positioning entering (read bullish inflow), falling = deleveraging
    //    (read bearish). Textbook caveat, stated not hidden: OI classically
    //    CONFIRMS a trend rather than owning a direction — this naive read
    //    is exactly the kind of board signal the IC run-log measured at ≈0
    //    forward IC, hence the mandatory label on the return value.
    //    Threshold ±0.5%/h (WHY: on a multi-billion-USD OI base that is a
    //    deliberate positioning change, not minute-to-minute drift).
    {
      const oi = inp.oiChangePct1h;
      if (!fin(oi)) row('OI 1h change', 'n/a', NA);
      else row('OI 1h change',
        oi > 0.5 ? 'bullish' : oi < -0.5 ? 'bearish' : 'neutral',
        oi.toFixed(2) + '%/h open interest');
    }

    // 7. Liq-pressure 5m — liqImb5m ∈ [−1, 1], +1 = ALL liquidated notional
    //    was longs. Long liqs print as forced SELLS (bearish pressure);
    //    short liqs as forced BUYS (squeeze fuel → bullish). Threshold 0.5
    //    (WHY: x − (1−x) ≥ 0.5 ⇒ one side carries ≥75% of the 5m liq
    //    notional — cascades are bursty, a 60/40 split is ordinary churn).
    {
      const li = inp.liqImb5m;
      if (!fin(li)) row('liq-pressure 5m', 'n/a', NA);
      else row('liq-pressure 5m',
        li > 0.5 ? 'bearish' : li < -0.5 ? 'bullish' : 'neutral',
        'imbalance ' + li.toFixed(2) + ' (long-liq vs short-liq)');
    }

    // 8. Book top-10 imbalance — bookImb ∈ [−1, 1], +1 = all bids. Threshold
    //    0.25 (§4d: bids carrying ≥62.5% of top-10 depth). WHY the wide
    //    dead-band: resting depth is the most spoofable input on this board
    //    (the §4b detector exists precisely because of that), so a modest
    //    skew is noise or bait — only a heavy one gets a read.
    {
      const bi = inp.bookImb;
      if (!fin(bi)) row('book top-10 imbalance', 'n/a', NA);
      else row('book top-10 imbalance',
        bi > 0.25 ? 'bullish' : bi < -0.25 ? 'bearish' : 'neutral',
        'imbalance ' + bi.toFixed(2) + ' (bid vs ask)');
    }

    // 9. Price vs SMA50 — the historical-trend leg: last 1h kline close vs
    //    its SMA50 (both computed by the caller from Bybit klines, quant.js
    //    sma — never reimplemented here). Dead-band ±10bp (WHY: a close
    //    sitting ON the average, within spread-and-float distance, is not a
    //    trend statement in either direction). sma50 must be > 0: it divides.
    {
      const sma = inp.sma50, c = inp.lastClose;
      if (!fin(sma) || !fin(c) || sma <= 0) row('price vs SMA50', 'n/a', NA);
      else {
        const dev = c / sma - 1;
        row('price vs SMA50',
          dev > 0.001 ? 'bullish' : dev < -0.001 ? 'bearish' : 'neutral',
          'close ' + c + ' vs SMA50 ' + sma + ' (' + (dev * 100).toFixed(2) + '%)');
      }
    }

    // Tally counts READS only — n/a rows are counted apart (they are absence,
    // not opinion) so bullish+bearish+neutral+na always sums to 9.
    const tally = { bullish: 0, bearish: 0, neutral: 0, na: 0 };
    for (const r of reads) tally[r.read === 'n/a' ? 'na' : r.read]++;
    return {
      reads,
      tally,
      // §4d mandatory IC-honesty sentence — verbatim from the contract.
      label: 'un-validated descriptive reads — forward IC of board signals ≈ 0 (RESEARCH-ic-runlog); NOT a signal',
    };
  }

  // ─── AlertEngine({rules, cooldownMs}) — descriptive alert triggers (§4d) ─
  //
  // Rules are ATTENTION triggers, never entries (§4d rail — AlertsView's
  // banner reads "descriptive triggers — un-validated"). rules = [{id, kind,
  // threshold?, enabled}] with THRESHOLDS INJECTED by the caller (§4d: no
  // defaults hidden in logic — a kind that needs a threshold and lacks a
  // finite one simply cannot fire; that surfaces the config bug instead of
  // alerting on an invented number).
  //
  // evaluate(snap) is EVENT-TS driven: snap.ts is the only clock — cooldown
  // arithmetic, event timestamps, everything (replay rail: same snaps in →
  // same events out; no Date.now()). Snap fields consumed per kind (each
  // optional — a missing field just means that kind cannot fire this pass):
  //   ts        — required; without an event time nothing can be honestly
  //               timestamped, so evaluate() returns [] rather than guessing
  //   price     — last trade price                          (price-cross)
  //   trades    — normalized §4 trade events NEW since the last evaluate
  //                                                          (whale-print)
  //   liq1mUsd  — LiqStore.sumWindow(60000, snap.ts), caller-computed — the
  //               engine never reaches into stores (pure)    (liq-1m)
  //   fundingRate                                            (funding-flip)
  //   window    — {price:[…], cvd:[…]} aligned recent samples (cvd-divergence)
  //   bookImb   — −1..1 top-10 bid vs ask                    (book-imbalance)
  //   detectorEvents — SpoofIcebergDetector events NEW since last evaluate
  //                                                          (detector-pass)
  //   oiChangePct1h — %/h                                    (oi-jump)
  //   basisBp   — (mark−index)/index in bp                   (basis-bp)
  //
  // Per-rule COOLDOWN (default 60 s): after a rule fires, further fires are
  // suppressed until snap.ts has advanced ≥ cooldownMs. WHY: a threshold
  // that stays breached (funding stays extreme, book stays lopsided) would
  // otherwise re-alert on every evaluate tick — alert fatigue turns alerts
  // into noise. Tracker state (price-cross prev, funding sign) keeps
  // updating THROUGH the cooldown so a rule re-arms against current reality,
  // not against a snapshot frozen at its last fire.
  function AlertEngine(opts) {
    const o = opts || {};
    const cooldownMs = finiteOr(o.cooldownMs, 60000);
    const ring = makeRing(200); // §4d: events() retains the last 200
    const state = new Map();    // rule id → {lastFireTs, prevPrice, prevFundingSign}
    let rules = [];

    /** Replace the rule set (AlertsView edits rules in place). State for ids
     *  that SURVIVE is kept — editing one rule must not re-arm the others'
     *  cooldowns or wipe a price-cross's prev; state for removed ids is
     *  pruned so deleted rules do not leak memory. Malformed rules (no id /
     *  no kind) are dropped here, once, instead of re-checked per evaluate. */
    function setRules(next) {
      rules = Array.isArray(next)
        ? next.filter((r) => r && r.id != null && typeof r.kind === 'string')
        : [];
      const ids = new Set(rules.map((r) => r.id));
      for (const id of [...state.keys()]) if (!ids.has(id)) state.delete(id);
    }
    setRules(o.rules);

    function stateFor(id) {
      let s = state.get(id);
      if (!s) { s = { lastFireTs: NaN, prevPrice: NaN, prevFundingSign: 0 }; state.set(id, s); }
      return s;
    }

    /** Half-window extremum helpers for cvd-divergence. Non-finite entries
     *  are skipped; an all-invalid half returns ±Infinity, which the caller
     *  rejects — a divergence "detected" against missing data would be a
     *  fabricated pattern (§0.7). */
    function hiOf(a, s, e) { let m = -Infinity; for (let i = s; i < e; i++) if (Number.isFinite(a[i]) && a[i] > m) m = a[i]; return m; }
    function loOf(a, s, e) { let m = Infinity; for (let i = s; i < e; i++) if (Number.isFinite(a[i]) && a[i] < m) m = a[i]; return m; }

    /** Evaluate every enabled rule against one snapshot. Returns ONLY the
     *  newly-fired events [{ts, ruleId, kind, msg, label?}]; events() replays
     *  the retained ring. */
    function evaluate(snap) {
      const out = [];
      if (!snap || !Number.isFinite(snap.ts)) return out; // no event time — see header
      const ts = snap.ts;
      for (const rule of rules) {
        // Disabled = invisible: no firing AND no tracking, so re-enabling
        // starts from a fresh seed instead of firing off a stale prev.
        if (rule.enabled === false) continue;
        const st = stateFor(rule.id);
        const th = rule.threshold; // injected or absent — never defaulted here
        const fired = [];          // {msg, label?} candidates from this rule

        switch (rule.kind) {
          case 'price-cross': {
            // Fires on a CROSS in either direction (§4d), not on "is
            // beyond": prev strictly on one side, now at-or-beyond the
            // other (landing exactly ON the level counts as reaching it).
            // The first evaluate only seeds prev — you cannot cross a line
            // you were never seen on one side of.
            const px = snap.price;
            if (Number.isFinite(px) && Number.isFinite(th)) {
              const prev = st.prevPrice;
              if (Number.isFinite(prev)
                  && ((prev < th && px >= th) || (prev > th && px <= th))) {
                fired.push({ msg: 'price crossed ' + th + ' (' + prev + ' → ' + px + ')' });
              }
              st.prevPrice = px; // tracks through cooldown — see header
            }
            break;
          }
          case 'whale-print': {
            // One event per evaluate — the LARGEST qualifying print: with a
            // cooldown in force anyway, per-print spam adds noise, not
            // information. Notional = price·qty (USD, linear contracts).
            if (!Number.isFinite(th)) break; // threshold required (§4d)
            const trades = Array.isArray(snap.trades) ? snap.trades : [];
            let best = null, bestN = -Infinity;
            for (const t of trades) {
              if (!validTrade(t)) continue;
              const n = t.price * t.qty;
              if (n >= th && n > bestN) { bestN = n; best = t; }
            }
            if (best) {
              const side = best.aggressorBuy === true ? ' buy' : best.aggressorBuy === false ? ' sell' : '';
              fired.push({ msg: 'whale print $' + Math.round(bestN) + side + ' @ ' + best.price });
            }
            break;
          }
          case 'liq-1m': {
            // liq1mUsd is LiqStore-style: sumWindow(60000, snap.ts), summed
            // by the caller so this engine stays store-free (pure).
            if (Number.isFinite(th) && Number.isFinite(snap.liq1mUsd) && snap.liq1mUsd >= th) {
              fired.push({ msg: '1m liquidations $' + Math.round(snap.liq1mUsd) + ' ≥ $' + th });
            }
            break;
          }
          case 'funding-flip': {
            // Sign change of the funding rate — the paying side swapped.
            // Tracks the last NONZERO sign so +→0→− still reads as ONE flip
            // (zero is "nobody pays", not a side; no threshold needed).
            const f = snap.fundingRate;
            if (Number.isFinite(f) && f !== 0) {
              const sign = f > 0 ? 1 : -1;
              if (st.prevFundingSign !== 0 && sign !== st.prevFundingSign) {
                fired.push({
                  msg: 'funding flipped ' + (sign > 0 ? 'negative → positive' : 'positive → negative') + ' (now ' + f + ')',
                });
              }
              st.prevFundingSign = sign; // tracks through cooldown
            }
            break;
          }
          case 'cvd-divergence': {
            // ⚠ HEURISTIC (§4d — the label rides the event): a two-half
            // window comparison — price prints a higher high while CVD
            // prints a lower high (bearish read: price rising on fading net
            // flow) and the mirror lower-low / higher-low (bullish read).
            // This is a DESCRIPTIVE PATTERN, not a validated one:
            // divergences resolve both ways, and the IC run-log grants no
            // board signal forward IC. n ≥ 4 (2 samples per half) is the
            // floor for comparing extrema at all.
            const w = snap.window;
            const ps = w && Array.isArray(w.price) ? w.price : [];
            const cs = w && Array.isArray(w.cvd) ? w.cvd : [];
            const n = Math.min(ps.length, cs.length);
            if (n >= 4) {
              const half = n >> 1;
              const p1h = hiOf(ps, 0, half), p2h = hiOf(ps, half, n);
              const c1h = hiOf(cs, 0, half), c2h = hiOf(cs, half, n);
              const p1l = loOf(ps, 0, half), p2l = loOf(ps, half, n);
              const c1l = loOf(cs, 0, half), c2l = loOf(cs, half, n);
              const finAll = (...xs) => xs.every(Number.isFinite);
              if (finAll(p1h, p2h, c1h, c2h) && p2h > p1h && c2h < c1h) {
                fired.push({ msg: 'bearish CVD divergence: price higher-high ' + p2h + ' on a CVD lower-high', label: 'heuristic' });
              } else if (finAll(p1l, p2l, c1l, c2l) && p2l < p1l && c2l > c1l) {
                fired.push({ msg: 'bullish CVD divergence: price lower-low ' + p2l + ' on a CVD higher-low', label: 'heuristic' });
              }
            }
            break;
          }
          case 'book-imbalance': {
            const bi = snap.bookImb;
            if (Number.isFinite(th) && Number.isFinite(bi) && Math.abs(bi) >= th) {
              fired.push({ msg: 'book ' + (bi > 0 ? 'bid' : 'ask') + '-heavy: imbalance ' + bi.toFixed(2) + ' (|x| ≥ ' + th + ')' });
            }
            break;
          }
          case 'detector-pass': {
            // Pass-through of SpoofIcebergDetector events the caller
            // collected since the last evaluate. Their 'heuristic' label is
            // PRESERVED (re-defaulted if a caller stripped it) — §4b: no
            // layer may drop that badge. Each event forwards individually
            // (distinct facts); the cooldown then gates subsequent passes.
            const evs = Array.isArray(snap.detectorEvents) ? snap.detectorEvents : [];
            for (const dev of evs) {
              if (!dev || typeof dev.kind !== 'string') continue;
              fired.push({
                msg: dev.kind + (Number.isFinite(dev.price) ? ' @ ' + dev.price : ''),
                label: typeof dev.label === 'string' && dev.label ? dev.label : 'heuristic',
              });
            }
            break;
          }
          case 'oi-jump': {
            const oi = snap.oiChangePct1h;
            if (Number.isFinite(th) && Number.isFinite(oi) && Math.abs(oi) >= th) {
              fired.push({ msg: 'OI ' + (oi > 0 ? 'jump +' : 'drop ') + oi.toFixed(2) + '%/h (|x| ≥ ' + th + '%/h)' });
            }
            break;
          }
          case 'basis-bp': {
            const b = snap.basisBp;
            if (Number.isFinite(th) && Number.isFinite(b) && Math.abs(b) >= th) {
              fired.push({ msg: 'basis ' + b.toFixed(1) + ' bp (|x| ≥ ' + th + ' bp)' });
            }
            break;
          }
          default: break; // unknown kind (stale saved rules) — ignored, never guessed
        }

        if (fired.length) {
          // Cooldown gate. NaN lastFireTs (never fired) fails the `<` and
          // passes; a ts that regressed below lastFireTs stays suppressed
          // (event-time honesty — we do not fire into the past).
          if (!(ts - st.lastFireTs < cooldownMs)) {
            st.lastFireTs = ts;
            for (const f of fired) {
              const ev = { ts, ruleId: rule.id, kind: rule.kind, msg: f.msg };
              if (f.label) ev.label = f.label; // heuristic badge rides the event
              ring.push(ev);
              out.push(ev);
            }
          }
        }
      }
      return out;
    }

    /** Retained events, oldest→newest, ≤200 (ring). Views reverse for
     *  newest-first display — same convention as LiqStore.recent(). */
    function events() { return ring.toArray(); }

    return { evaluate, events, setRules, cooldownMs };
  }

  // ═══ O-5 (§4e): trade journal — pure stats/calendar/CSV over the USER'S OWN
  // logged trades ═══════════════════════════════════════════════════════════
  //
  // RAIL (§4e): the journal records the user's own MANUAL trade log
  // (localStorage; CSV export/import for portability). Every stat below is a
  // DESCRIPTIVE RECORD of journaled trades — "your logged trades — descriptive
  // record, NOT a backtest" is the mandatory panel label. Nothing here ever
  // feeds the OOS harness (§0.1).
  //
  // Trade shape (§4e): { id, tsOpen, tsClose, side:'long'|'short', entry,
  // exit, size, riskUsd, tag, note, ctx? } — ctx is the auto-captured
  // descriptive context snapshot {mark, fundingRate, oi, cvdSlope,
  // confluenceTally} at log time.
  //
  // R convention (§4e, honest difference stated): R = pnlUsd / riskUsd where
  // riskUsd is the USER-DECLARED 1R for that trade (Tharp's own definition —
  // "how much you decided to lose if wrong"). The repo's backtest ledger
  // (risk.py expectancy_report / quant.js expectancyReport) instead derives R
  // from a VOL-NOTIONAL proxy (k·vol·price) because a backtest has no declared
  // stop. Same R-multiple statistics FAMILY (expectancy/SQN/PF definitions are
  // identical and mirrored from those two files), different 1R denominator —
  // declared risk here, volatility proxy there.

  /** Signed trade P&L in USD from the journal fields. Sign convention: a
   *  short profits when exit < entry. */
  function journalPnlUsd(t) {
    return (t.exit - t.entry) * t.size * (t.side === 'short' ? -1 : 1);
  }

  /** Is this journal trade usable for R statistics? riskUsd must be a
   *  STRICTLY POSITIVE finite number: R = pnl/riskUsd is undefined at 0 and
   *  sign-flipped below it — such rows are EXCLUDED AND COUNTED (§4e), never
   *  silently coerced into the stats. */
  function journalStatable(t) {
    return !!t && (t.side === 'long' || t.side === 'short')
      && Number.isFinite(t.entry) && Number.isFinite(t.exit) && Number.isFinite(t.size)
      && Number.isFinite(t.riskUsd) && t.riskUsd > 0;
  }

  /**
   * Tharp block over journaled trades (§4e): { n, excluded, winRate,
   * expectancyR, sqn, profitFactor, avgWinR, avgLossR, maxDrawR, byTag,
   * label }.
   *
   * Definitions mirror quant.js expectancyReport / risk.py expectancy_report
   * EXACTLY (the house Tharp conventions — never reimplemented differently):
   *   wins = R > 0; winRate = wins/n; expectancyR = mean(R);
   *   avgWinR = mean(wins) (0 when none); avgLossR = mean(losses) (≤ 0);
   *   SQN = mean(R)/std(R, ddof=1)·√n; PF = ΣwinR / |ΣlossR|.
   * maxDrawR = deepest peak-to-trough drawdown of cumulative R in tsClose
   * order (the journal's own equity curve, in R units).
   * byTag: tag → {n, expectancyR} (untagged trades group under 'untagged').
   */
  function journalStats(trades) {
    const label = 'your logged trades — descriptive record, NOT a backtest';
    const list = Array.isArray(trades) ? trades : [];
    const usable = [];
    let excluded = 0;
    for (const t of list) {
      if (journalStatable(t)) usable.push(t);
      else excluded++;                                 // counted, never silently dropped (§4e)
    }
    const out = {
      n: usable.length, excluded, winRate: NaN, expectancyR: NaN, sqn: NaN,
      profitFactor: NaN, avgWinR: NaN, avgLossR: NaN, maxDrawR: NaN, byTag: {}, label,
    };
    if (!usable.length) return out;

    // R multiples in tsClose order (the drawdown walk needs the time order;
    // the moment stats don't care).
    const seq = usable.slice().sort((a, b) => (a.tsClose || 0) - (b.tsClose || 0));
    const rms = seq.map((t) => journalPnlUsd(t) / t.riskUsd);

    const n = rms.length;
    const mean = rms.reduce((a, b) => a + b, 0) / n;
    const wins = rms.filter((r) => r > 0), losses = rms.filter((r) => r < 0);
    out.expectancyR = mean;
    out.winRate = wins.length / n;
    out.avgWinR = wins.length ? wins.reduce((a, b) => a + b, 0) / wins.length : 0;
    out.avgLossR = losses.length ? losses.reduce((a, b) => a + b, 0) / losses.length : 0;
    if (n > 1) {
      // Sample stdev (ddof=1) — the quant.js std() convention SQN mirrors.
      let s = 0;
      for (const r of rms) s += (r - mean) * (r - mean);
      const sd = Math.sqrt(s / (n - 1));
      out.sqn = sd > 0 ? (mean / sd) * Math.sqrt(n) : NaN;
    }
    const grossLoss = -losses.reduce((a, b) => a + b, 0);
    out.profitFactor = grossLoss > 0 ? wins.reduce((a, b) => a + b, 0) / grossLoss : NaN;

    // Equity walk in R units: maxDrawR = max(peak − cum) over the sequence.
    let cum = 0, peak = 0, dd = 0;
    for (const r of rms) {
      cum += r;
      if (cum > peak) peak = cum;
      if (peak - cum > dd) dd = peak - cum;
    }
    out.maxDrawR = dd;

    // Per-tag expectancy (same mean-of-R definition, per bucket).
    const tagged = new Map();
    seq.forEach((t, i) => {
      const tag = typeof t.tag === 'string' && t.tag !== '' ? t.tag : 'untagged';
      if (!tagged.has(tag)) tagged.set(tag, []);
      tagged.get(tag).push(rms[i]);
    });
    for (const [tag, arr] of tagged) {
      out.byTag[tag] = { n: arr.length, expectancyR: arr.reduce((a, b) => a + b, 0) / arr.length };
    }
    return out;
  }

  /** 'YYYY-MM-DD' UTC day key from epoch ms. */
  function utcDayKey(ts) { return new Date(ts).toISOString().slice(0, 10); }

  /** ISO-8601 week key 'YYYY-Www' (UTC). The Thursday trick: a date's ISO
   *  week-year is the calendar year of ITS week's Thursday — that is the
   *  whole edge case (e.g. Mon 2024-12-30 belongs to 2025-W01; Fri
   *  2027-01-01 belongs to 2026-W53). Weekly buckets keyed by the raw
   *  calendar year would split those weeks in two. */
  function isoWeekKey(ts) {
    const d = new Date(ts);
    const day0 = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
    const dow = (new Date(day0).getUTCDay() + 6) % 7;    // Mon=0 … Sun=6
    const thu = day0 + (3 - dow) * 86400000;             // this ISO week's Thursday
    const isoYear = new Date(thu).getUTCFullYear();
    const week = Math.floor((thu - Date.UTC(isoYear, 0, 1)) / 604800000) + 1;
    return isoYear + '-W' + String(week).padStart(2, '0');
  }

  /**
   * Calendar aggregation of journal R (§4e): { daily:{'YYYY-MM-DD'→ΣR},
   * weekly:{'YYYY-Www'→ΣR}, monthly:{'YYYY-MM'→ΣR}, hourly:{0..23→ΣR} } —
   * all bucketed by CLOSE timestamp in UTC (§4e: close-ts bucketing; an
   * exchange-less journal has no session timezone, UTC is the one honest
   * grid). Only statable trades contribute (same riskUsd>0 rule as
   * journalStats — a calendar cell must mean the same R the stats mean).
   * Only touched buckets appear (absent day ≠ 0R day — no fabricated flats).
   */
  function calendarReturns(trades) {
    const out = { daily: {}, weekly: {}, monthly: {}, hourly: {} };
    for (const t of Array.isArray(trades) ? trades : []) {
      if (!journalStatable(t) || !Number.isFinite(t.tsClose)) continue;
      const r = journalPnlUsd(t) / t.riskUsd;
      const dk = utcDayKey(t.tsClose);
      const wk = isoWeekKey(t.tsClose);
      const mk = dk.slice(0, 7);
      const hk = new Date(t.tsClose).getUTCHours();
      out.daily[dk] = (out.daily[dk] || 0) + r;
      out.weekly[wk] = (out.weekly[wk] || 0) + r;
      out.monthly[mk] = (out.monthly[mk] || 0) + r;
      out.hourly[hk] = (out.hourly[hk] || 0) + r;
    }
    return out;
  }

  // ── Journal CSV (§4e data portability): export/import must round-trip ──
  // Column order is the contract; `ctx` is a JSON column (the snapshot object
  // serialized) — JSON commas/quotes are exactly why the writer must quote
  // per RFC 4180 and the reader must be a real state-machine parser, not a
  // split(',').
  const JOURNAL_CSV_COLS = ['id', 'tsOpen', 'tsClose', 'side', 'entry', 'exit',
    'size', 'riskUsd', 'tag', 'note', 'ctx'];

  /** RFC-4180 field quoting: quote when the value carries a comma, a quote,
   *  or a newline; inner quotes double. */
  function csvField(v) {
    const s = String(v == null ? '' : v);
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  /** Journal trades → CSV text (header + one row per trade). Numbers are
   *  serialized with String() — JS shortest-round-trip printing, so
   *  Number(String(x)) === x and the import reproduces every float exactly
   *  (the round-trip identity the fixture smoke pins). */
  function journalToCsv(trades) {
    const lines = [JOURNAL_CSV_COLS.join(',')];
    for (const t of Array.isArray(trades) ? trades : []) {
      if (!t) continue;
      lines.push([
        csvField(t.id), csvField(t.tsOpen), csvField(t.tsClose), csvField(t.side),
        csvField(t.entry), csvField(t.exit), csvField(t.size), csvField(t.riskUsd),
        csvField(t.tag), csvField(t.note),
        csvField(t.ctx === undefined ? '' : JSON.stringify(t.ctx)),
      ].join(','));
    }
    return lines.join('\n') + '\n';
  }

  /** Minimal RFC-4180 reader → array of rows (arrays of string fields).
   *  Handles quoted fields, doubled inner quotes, and quoted newlines (a
   *  journal note may legitimately contain all three). */
  function parseCsv(text) {
    const rows = [];
    let row = [], field = '', inQ = false, i = 0;
    const s = String(text);
    while (i < s.length) {
      const c = s[i];
      if (inQ) {
        if (c === '"') {
          if (s[i + 1] === '"') { field += '"'; i += 2; continue; }   // doubled quote → literal
          inQ = false; i++; continue;
        }
        field += c; i++; continue;
      }
      if (c === '"') { inQ = true; i++; continue; }
      if (c === ',') { row.push(field); field = ''; i++; continue; }
      if (c === '\n' || c === '\r') {
        if (c === '\r' && s[i + 1] === '\n') i++;
        row.push(field); rows.push(row); row = []; field = ''; i++; continue;
      }
      field += c; i++;
    }
    if (field !== '' || row.length) { row.push(field); rows.push(row); }
    return rows;
  }

  /**
   * CSV text → { trades, errors } (§4e). IMPORT NEVER SILENTLY COERCES: a
   * row that fails ANY field check lands in `errors` as {line, reason} and
   * imports nothing — half-guessed trades would poison every stat above.
   * Accepted rows reproduce the §4e trade shape exactly (ctx present only
   * when the column was non-empty valid JSON). riskUsd ≤ 0 rows ARE accepted
   * (they are valid journal records — journalStats excludes and counts them);
   * riskUsd must still be a NUMBER.
   */
  function validateJournalCsv(text) {
    const out = { trades: [], errors: [] };
    const rows = parseCsv(text);
    if (!rows.length) { out.errors.push({ line: 1, reason: 'empty file' }); return out; }
    if (rows[0].join(',') !== JOURNAL_CSV_COLS.join(',')) {
      out.errors.push({ line: 1, reason: 'header mismatch — expected: ' + JOURNAL_CSV_COLS.join(',') });
      return out;
    }
    for (let li = 1; li < rows.length; li++) {
      const r = rows[li];
      const line = li + 1;                             // 1-based, header = line 1
      if (r.length === 1 && r[0] === '') continue;     // trailing blank line
      if (r.length !== JOURNAL_CSV_COLS.length) {
        out.errors.push({ line, reason: 'expected ' + JOURNAL_CSV_COLS.length + ' columns, got ' + r.length });
        continue;
      }
      const [id, tsOpenS, tsCloseS, side, entryS, exitS, sizeS, riskS, tag, note, ctxS] = r;
      const tsOpen = Number(tsOpenS), tsClose = Number(tsCloseS);
      const entry = Number(entryS), exit = Number(exitS), size = Number(sizeS), riskUsd = Number(riskS);
      let reason = null;
      if (id === '') reason = 'empty id';
      else if (tsOpenS === '' || !Number.isFinite(tsOpen)) reason = 'tsOpen not a finite number: ' + JSON.stringify(tsOpenS);
      else if (tsCloseS === '' || !Number.isFinite(tsClose)) reason = 'tsClose not a finite number: ' + JSON.stringify(tsCloseS);
      else if (side !== 'long' && side !== 'short') reason = "side must be 'long'|'short', got " + JSON.stringify(side);
      else if (entryS === '' || !Number.isFinite(entry)) reason = 'entry not a finite number: ' + JSON.stringify(entryS);
      else if (exitS === '' || !Number.isFinite(exit)) reason = 'exit not a finite number: ' + JSON.stringify(exitS);
      else if (sizeS === '' || !Number.isFinite(size)) reason = 'size not a finite number: ' + JSON.stringify(sizeS);
      else if (riskS === '' || !Number.isFinite(riskUsd)) reason = 'riskUsd not a finite number: ' + JSON.stringify(riskS);
      let ctx;
      if (!reason && ctxS !== '') {
        try { ctx = JSON.parse(ctxS); } catch (_) { reason = 'ctx column is not valid JSON'; }
        if (!reason && (ctx === null || typeof ctx !== 'object' || Array.isArray(ctx))) {
          reason = 'ctx must be a JSON object';
        }
      }
      if (reason) { out.errors.push({ line, reason }); continue; }
      const t = { id, tsOpen, tsClose, side, entry, exit, size, riskUsd, tag, note };
      if (ctx !== undefined) t.ctx = ctx;
      out.trades.push(t);
    }
    return out;
  }

  // ════════ I-1 (§4f) — Institutional Auction Suite builders: tick-exact
  // auction analytics, pure over already-normalized inputs ══════════════════
  //
  // §4f rails, restated where they bite: every derived read below is
  // DESCRIPTIVE (§0.1 — nothing feeds a signal or the OOS harness);
  // heuristics carry label:'heuristic' on the event itself; OFI and
  // microprice carry their paper citations (Cont–Kukanov–Stoikov 2014;
  // Stoikov 2018) in the code that implements them. Same two rails as every
  // store above: zero DOM, zero Date.now() — event ts is the only clock.

  // ─── buildDeltaProfile(levels) — per-level delta + render intensity (§4f) ─
  //
  // Input: /v1/profile `levels` rows ({lvl, buy_vol, sell_vol}; the live
  // ProfileStore-adjacent {price, buy, sell} spelling is accepted too so the
  // 'today live' selector can feed footprint-derived levels through the same
  // builder). Output: ascending-lvl [{lvl, delta, intensity}] for the
  // diverging delta render (§4f dataviz rail: two-hue + NEUTRAL midpoint).
  //
  // `delta` is the RAW per-level difference buy−sell, untouched by the
  // normalization — so Σdelta ≡ Σbuy−Σsell exactly (§4f binding invariant):
  // intensity is a SEPARATE display field, never a scaled/clamped delta, and
  // no level is dropped or merged by this builder (malformed rows are skipped
  // per the validTrade hygiene rule — skipping garbage is not clamping data).
  //
  // `intensity` = |delta| / p95(|delta|), clamped to [0, 1]. WHY p95 and not
  // max: one outlier level (a single sweep print) would otherwise compress
  // every other level's hue toward the neutral midpoint and the profile would
  // render as one colored line — normalizing at the 95th percentile lets the
  // bulk of the distribution use the full ramp while the true outliers
  // saturate at 1. Nearest-rank p95 (deterministic, no interpolation).
  function buildDeltaProfile(levels) {
    const out = [];
    for (const r of Array.isArray(levels) ? levels : []) {
      if (!r) continue;
      const lvl = Number.isFinite(r.lvl) ? r.lvl : r.price;
      const buy = Number.isFinite(r.buy_vol) ? r.buy_vol : r.buy;
      const sell = Number.isFinite(r.sell_vol) ? r.sell_vol : r.sell;
      if (!Number.isFinite(lvl) || !Number.isFinite(buy) || !Number.isFinite(sell)) continue;
      out.push({ lvl, delta: buy - sell, intensity: 0 });
    }
    out.sort((a, b) => a.lvl - b.lvl); // ascending — profile renders low→high
    if (!out.length) return out;
    const mags = out.map((r) => Math.abs(r.delta)).sort((a, b) => a - b);
    const p95 = mags[Math.min(mags.length - 1, Math.max(0, Math.ceil(0.95 * mags.length) - 1))];
    if (p95 > 0) {
      for (const r of out) r.intensity = Math.min(1, Math.abs(r.delta) / p95);
    }
    // p95 === 0 (all deltas zero) → every intensity stays 0: a perfectly
    // balanced profile renders neutral, never NaN (0/0) into a color ramp.
    return out;
  }

  // ─── SessionClock() — UTC session tags + boxes (§4f) ─────────────────────
  //
  // Sessions (§4f, UTC): Asia 00–08, London 07–16, NY 12–21. HONESTY NOTE,
  // stated not hidden: these hours are the classic FX-DESK CONVENTION, not an
  // oracle — crypto perps trade 24/7 with no venue open/close, so a "session"
  // here is a human attention window borrowed from FX desks, useful for
  // reading WHERE flow concentrated, never a claim that the market opens or
  // closes. The overlaps are REAL and deliberately labeled as such: London
  // overlaps Asia 07–08 and NY 12–16 (both sessions tag simultaneously), and
  // 21–24 UTC belongs to no session at all — an honest dead zone, not a bug.
  //
  // Boundary semantics: half-open [start, end) — at exactly 07:00 UTC both
  // Asia and London are active; at exactly 08:00 Asia is over. Half-open is
  // the only convention where every instant maps to a deterministic tag set
  // with no double-counted boundary hour.
  function SessionClock() {
    const HOUR = 3600000, DAY = 86400000;
    const SESSIONS = [
      { name: 'Asia', startH: 0, endH: 8 },
      { name: 'London', startH: 7, endH: 16 },
      { name: 'NY', startH: 12, endH: 21 },
    ];

    /** Active session names at epoch-ms `ts` (possibly several — overlaps are
     *  real). Non-finite ts → [] (unknown time is no session, never a guess). */
    function tag(ts) {
      if (!Number.isFinite(ts)) return [];
      const ms = ((ts % DAY) + DAY) % DAY; // UTC intra-day offset (negative-safe)
      const out = [];
      for (const s of SESSIONS) {
        if (ms >= s.startH * HOUR && ms < s.endH * HOUR) out.push(s.name);
      }
      return out;
    }

    /** Session shading boxes for the UTC day starting at `dayStartMs`:
     *  [{name, startMs, endMs}]. Pure arithmetic on the given anchor — the
     *  caller owns "which day"; this never asks the OS what day it is. */
    function boxesFor(dayStartMs) {
      if (!Number.isFinite(dayStartMs)) return [];
      return SESSIONS.map((s) => ({
        name: s.name,
        startMs: dayStartMs + s.startH * HOUR,
        endMs: dayStartMs + s.endH * HOUR,
      }));
    }

    return { tag, boxesFor, sessions: SESSIONS.map((s) => ({ name: s.name, startH: s.startH, endH: s.endH })) };
  }

  // ─── AnchoredVwap() — streaming anchored vwap ± σ bands (§4f) ────────────
  //
  // Volume-weighted mean + variance via the WEIGHTED WELFORD update (Welford
  // 1962; West 1979 weighted-increment form) — one pass, O(1) state, no
  // stored trade list:
  //     W += w;  Δ = x − μ;  μ += (w/W)·Δ;  S += w·Δ·(x − μ)
  // giving μ = Σwx/Σw (the vwap) and σ² = S/W ≡ Σw(x−μ)²/Σw — the volume-
  // weighted POPULATION variance, i.e. byte-for-byte the batch formula the
  // /v1/vwap endpoint computes (§4f: deterministic vs batch to 1e-9; the
  // self-check pins that). WHY Welford and not naive Σwx²−(Σwx)²/W: at 1e5-
  // scale BTC prices the naive form cancels catastrophically (1e10-scale
  // squares differing in the 12th digit); Welford keeps full precision on a
  // stream of millions of trades.
  //
  // bands() → {vwap, s1, s2, n}: s1/s2 are the 1σ and 2σ DISTANCES — views
  // draw vwap±s1 / vwap±s2 (the AMT read, §4f: value ≈ vwap ± 1σ). Empty
  // state → NaN bands (ProfileStore convention: "no data" never looks like
  // price 0). reset(anchorTs) re-anchors: state zeroed, trades with
  // ts < anchorTs ignored — the anchor is an EVENT-time cut, no Date.now().
  function AnchoredVwap() {
    let anchorTs = -Infinity; // default: everything fed is in-anchor
    let W = 0, mean = 0, S = 0, n = 0;

    /** Re-anchor at `ts` (epoch ms). Non-finite → -Infinity (accept all). */
    function reset(ts) {
      anchorTs = Number.isFinite(ts) ? ts : -Infinity;
      W = 0; mean = 0; S = 0; n = 0;
    }

    /** Ingest one normalized trade (§4 shape). Pre-anchor / malformed → dropped. */
    function onTrade(t) {
      if (!validTrade(t)) return;
      if (t.ts < anchorTs) return; // before the anchor — not this vwap's flow
      const w = t.qty, x = t.price;
      W += w;
      const d = x - mean;
      mean += (w / W) * d;
      S += w * d * (x - mean);
      n++;
    }

    /** {vwap, s1, s2, n} — see header. Math.max(0, ·) guards the sqrt against
     *  a −1e-18 float residue on constant-price streams. */
    function bands() {
      if (!(W > 0)) return { vwap: NaN, s1: NaN, s2: NaN, n: 0 };
      const sigma = Math.sqrt(Math.max(0, S / W));
      return { vwap: mean, s1: sigma, s2: 2 * sigma, n };
    }

    return { onTrade, bands, reset };
  }

  // ─── OfiStore({levels=5}) — Cont–Kukanov–Stoikov order-flow imbalance (§4f) ─
  //
  // CITATION + EXACT RULE (§4f mandatory): R. Cont, A. Kukanov & S. Stoikov
  // (2014), "The Price Impact of Order Book Events", J. Financial
  // Econometrics 12(1), 47–88 — OFI between successive book states n−1 → n:
  //     e_n =  1{Pᵇ_n ≥ Pᵇ_{n−1}}·qᵇ_n − 1{Pᵇ_n ≤ Pᵇ_{n−1}}·qᵇ_{n−1}
  //          − 1{Pᵃ_n ≤ Pᵃ_{n−1}}·qᵃ_n + 1{Pᵃ_n ≥ Pᵃ_{n−1}}·qᵃ_{n−1}
  // Spelled out per side (the rule this store implements, bid side): if the
  // bid PRICE ROSE → +qᵇ_n (new demand at a better price); price UNCHANGED →
  // qᵇ_n − qᵇ_{n−1} (net add/cancel at the standing price — both indicators
  // fire); price FELL → −qᵇ_{n−1} (the standing bid was removed/consumed).
  // Asks are the exact mirror with the sign flipped: ask price FELL → −qᵃ_n,
  // unchanged → −(qᵃ_n − qᵃ_{n−1}), ROSE → +qᵃ_{n−1} (standing ask lifted =
  // buying pressure). Positive e = net buy-side pressure.
  //
  // Top-N extension (§4f "formula family"): the same per-level rule applied
  // at each ladder INDEX i = 0..levels−1 and summed — the standard multi-
  // level generalization (index-aligned, as in Xu, Gould et al. 2019,
  // "Multi-level order-flow imbalance in a limit order book"). A level index
  // present on only one side of the comparison is the degenerate case of the
  // same rule: newly-visible bid depth → +qᵇ_n, vanished bid depth → −qᵇ_{n−1},
  // mirrored for asks.
  //
  // STATED APPROXIMATION (§4f): CKS define e_n per individual book EVENT;
  // this store computes it between successive ~1 s GROUPED snapshots (the
  // same cadence terminal.js feeds DepthHistoryStore), so each e_t aggregates
  // every event inside the sample interval. That loses intra-second
  // sequencing, not net flow — and it is what the wire cadence honestly
  // supports. An EMPTY ladder is a reconnect gap, not a mass cancel (§0.7):
  // it is skipped AND clears the prev seed so no e is fabricated across it.
  function OfiStore(opts) {
    const levels = Math.max(1, Math.floor(finiteOr(opts && opts.levels, 5)));
    // ~68 min at the 1/s cadence — bounded, and longer than any zscore/rolling
    // window a view asks for (MicrostructureView's pane shows minutes).
    const ring = makeRing(4096); // {ts, e}
    let prev = null;             // {bids:[{price,qty}], asks:[{price,qty}]} top-N

    /** Validated top-N copy of one grouped side (best-first order preserved). */
    function topN(rows) {
      const out = [];
      if (Array.isArray(rows)) {
        for (const r of rows) {
          if (!r || !Number.isFinite(r.price) || !Number.isFinite(r.qty)) continue;
          out.push({ price: r.price, qty: r.qty });
          if (out.length >= levels) break;
        }
      }
      return out;
    }

    /** One side's summed per-level contribution; `sign` = +1 bids / −1 asks.
     *  For asks the price indicators flip direction with the sign (see the
     *  header formula) — implemented by comparing sign·price so one body
     *  serves both sides without duplicating the rule. */
    function sideContribution(cur, prv, sign) {
      let e = 0;
      const m = Math.max(cur.length, prv.length);
      for (let i = 0; i < m; i++) {
        const c = cur[i], p = prv[i];
        if (c && p) {
          const pc = sign * c.price, pp = sign * p.price;
          if (pc >= pp) e += sign * c.qty;  // price improved-or-held → count new qty
          if (pc <= pp) e -= sign * p.qty;  // price worsened-or-held → remove old qty
        } else if (c) {
          e += sign * c.qty;                // newly visible depth (degenerate rose)
        } else if (p) {
          e -= sign * p.qty;                // vanished depth (degenerate fell)
        }
      }
      return e;
    }

    /** Ingest one grouped ladder (BookStore.grouped() shape). Returns the
     *  computed e_t, or null on seed/skip. */
    function onDepthSample(ts, grouped) {
      if (!Number.isFinite(ts) || !grouped) return null;
      const bids = topN(grouped.bids), asks = topN(grouped.asks);
      if (!bids.length && !asks.length) { prev = null; return null; } // gap — see header
      if (!prev) { prev = { bids, asks }; return null; }              // seed: e needs two states
      const e = sideContribution(bids, prev.bids, 1) + sideContribution(asks, prev.asks, -1);
      prev = { bids, asks };
      ring.push({ ts, e });
      return e;
    }

    /** Rolling-sum series over the retained e_t ring: [{ts, ofi}] where ofi =
     *  Σe over (ts−windowMs, ts]. Two-pointer O(n) over the ring — the pane
     *  redraws this whole series per frame. Default 60 s (the classic OFI
     *  aggregation bucket in CKS's regressions is O(10 s)–O(1 min)). */
    function series(windowMs) {
      const w = posOr(windowMs, 60000);
      const evs = ring.toArray();
      const out = [];
      let lo = 0, sum = 0;
      for (let i = 0; i < evs.length; i++) {
        sum += evs[i].e;
        while (evs[lo].ts <= evs[i].ts - w) { sum -= evs[lo].e; lo++; }
        out.push({ ts: evs[i].ts, ofi: sum });
      }
      return out;
    }

    /** z-score of the LATEST e against the trailing `window` samples
     *  (inclusive; sample stdev ddof=1, the quant.js std convention). NaN
     *  below 2 samples or at zero variance — "unknown" stays NaN, never a
     *  fabricated 0 (which would claim "exactly average"). */
    function zscore(window) {
      const w = Math.floor(finiteOr(window, 300));
      if (w < 2) return NaN;
      const evs = ring.toArray();
      if (evs.length < 2) return NaN;
      const tail = evs.slice(Math.max(0, evs.length - w));
      if (tail.length < 2) return NaN;
      let m = 0;
      for (const ev of tail) m += ev.e;
      m /= tail.length;
      let s = 0;
      for (const ev of tail) s += (ev.e - m) * (ev.e - m);
      const sd = Math.sqrt(s / (tail.length - 1));
      return sd > 0 ? (tail[tail.length - 1].e - m) / sd : NaN;
    }

    return { onDepthSample, series, zscore, levels };
  }

  // ─── microprice(book) — Stoikov imbalance-weighted mid (§4f) ─────────────
  //
  // CITATION (§4f mandatory): S. Stoikov (2018), "The micro-price: a high-
  // frequency estimator of future prices", Quantitative Finance 18(12). The
  // §4f contract uses the paper's first-order object, the imbalance-weighted
  // mid:
  //     microprice = (Pᵇ·Qᵃ + Pᵃ·Qᵇ) / (Qᵃ + Qᵇ)
  // — the mid pulled TOWARD the thin side: when the ask queue is small the
  // next move is likelier up, so the estimator sits above mid. (Stoikov's
  // full micro-price adds a Markov-chain adjustment on the imbalance state;
  // that refinement needs fitted transition estimates and is NOT computed
  // here — this is the closed-form first approximation, stated as such.)
  //
  // Input: a BookStore (its .best() is used) or a plain {bid:[p,q],
  // ask:[p,q]} best-levels object. Returns null on an empty/one-sided book
  // or zero total best-depth — null is "no estimate", never NaN into a chart.
  function microprice(book) {
    if (!book) return null;
    let bb = null, ba = null;
    if (typeof book.best === 'function') {
      const b = book.best();
      bb = b && b.bid; ba = b && b.ask;
    } else {
      bb = book.bid; ba = book.ask;
    }
    if (!bb || !ba) return null;
    const pb = +bb[0], qb = +bb[1], pa = +ba[0], qa = +ba[1];
    if (!Number.isFinite(pb) || !Number.isFinite(qb)
        || !Number.isFinite(pa) || !Number.isFinite(qa)) return null;
    const den = qa + qb;
    if (!(den > 0)) return null;
    return (pb * qa + pa * qb) / den;
  }

  // ─── stackedImbalances(bars, {k, minRun, tickSize, minVol}) — zones (§4f) ─
  //
  // Consecutive same-side DIAGONAL-imbalance runs inside one finished
  // footprint bar → price zones [{top, bottom, side, barIdx, active}].
  // Diagonal rule = FootprintStore's §4 convention exactly (recomputed here
  // with THIS k so the builder is parameterizable independent of the store's
  // imbalanceK): buy-imbalance at p ⇔ buy(p) ≥ k·sell(p−tick) with a vacant
  // neighbor counting as 0 and the same minVol dust floor (default 1.0,
  // mirroring imbalanceMinVol — see FootprintStore for the WHY). `tickSize`
  // MUST match the FootprintStore that built the bars (default 1, same as
  // the store's default): "consecutive" means price-adjacent ON THE TICK
  // GRID — a vacant level breaks the run (no imbalance printed there), it is
  // never bridged.
  //
  // Zones are read as the classic footprint objects: a stacked BUY run =
  // aggressive buying absorbed level after level → demand zone (support);
  // stacked SELL run → supply zone (resistance). INVALIDATION (§4f "zone
  // dies when traded through"): a buy zone dies when any LATER bar's range
  // trades strictly below its bottom (price broke down through the support);
  // a sell zone when a later bar's high exceeds its top. Dead zones stay in
  // the output with active:false — views shade them differently rather than
  // pretending they never printed.
  //
  // SESSION-LOCAL HONESTY (§4f, stated not hidden): zones exist only within
  // the bars array passed — this session's footprint window (≤120 finished
  // bars). There is no cross-session persistence and no claim these are
  // validated structure levels; a stacked imbalance is a descriptive
  // order-flow pattern, and the IC run-log grants board patterns ≈0 forward
  // IC. Zones from the OPEN bar are never created (flags on a half-formed
  // bar flicker — FootprintStore's rule), but the open bar's real range DOES
  // participate in invalidation: its prints already happened.
  function stackedImbalances(bars, opts) {
    const o = opts || {};
    const k = posOr(o.k, 3);
    const minRun = Math.max(1, Math.floor(finiteOr(o.minRun, 3)));
    const tick = posOr(o.tickSize, 1);
    const minVol = finiteOr(o.minVol, 1.0);
    const list = Array.isArray(bars) ? bars : [];
    const zones = [];

    for (let bi = 0; bi < list.length; bi++) {
      const bar = list[bi];
      if (!bar || bar.finished !== true || !Array.isArray(bar.levels)) continue;
      const byPx = new Map(); // roundPx price → {buy, sell}
      for (const lv of bar.levels) {
        if (!lv || !Number.isFinite(lv.price)
            || !Number.isFinite(lv.buy) || !Number.isFinite(lv.sell)) continue;
        byPx.set(roundPx(lv.price), lv);
      }
      const prices = [...byPx.keys()].sort((a, b) => b - a); // ladder order (desc)

      // Diagonal flags at THIS k (see header — same rule as FootprintStore).
      const imb = (p, side) => {
        const cell = byPx.get(p);
        if (!cell) return false;
        if (side === 'buy') {
          const below = byPx.get(roundPx(p - tick));
          return cell.buy > 0 && cell.buy >= minVol && cell.buy >= k * (below ? below.sell : 0);
        }
        const above = byPx.get(roundPx(p + tick));
        return cell.sell > 0 && cell.sell >= minVol && cell.sell >= k * (above ? above.buy : 0);
      };

      for (const side of ['buy', 'sell']) {
        let run = []; // consecutive grid-adjacent flagged prices, descending
        const flush = () => {
          if (run.length >= minRun) {
            zones.push({ top: run[0], bottom: run[run.length - 1], side, barIdx: bi, active: true });
          }
          run = [];
        };
        for (const p of prices) {
          const on = imb(p, side);
          const contiguous = run.length > 0 && roundPx(run[run.length - 1] - tick) === p;
          if (on && (run.length === 0 || contiguous)) run.push(p);
          else { flush(); if (on) run.push(p); }
        }
        flush();
      }
    }

    // Invalidation pass — later bars only (including the open bar; see header).
    for (const z of zones) {
      for (let j = z.barIdx + 1; j < list.length; j++) {
        const b = list[j];
        if (!b || !Number.isFinite(b.l) || !Number.isFinite(b.h)) continue;
        const crossed = z.side === 'buy' ? b.l < z.bottom : b.h > z.top;
        if (crossed) { z.active = false; break; }
      }
    }
    return zones;
  }

  // ─── AbsorptionDetector({volK, progressTicks, tickSize}) — HEURISTIC (§4f) ─
  //
  // ⚠ HEURISTIC, NOT PROOF (§4f rail — label:'heuristic' rides EVERY event,
  // same discipline as SpoofIcebergDetector): "absorption" here means a
  // volume spike at one price level with no follow-through — a pattern
  // CONSISTENT WITH passive size absorbing aggressive flow, but from public
  // prints alone it is indistinguishable from, e.g., two aggressors crossing
  // at a magnet price and interest simply moving on. Pattern-consistent-with,
  // never proof of a resting iceberg or institutional defense.
  //
  // Rule (both legs on FINISHED bars only — half-formed bars flicker):
  //   1. spike: a finished bar has a level whose total volume (buy+sell) ≥
  //      volK × the MEDIAN level volume of that same bar (median, not mean —
  //      the spike itself cannot drag the baseline up and hide itself; the
  //      SpoofIcebergDetector wall rule uses the same argument);
  //   2. no follow-through: the NEXT finished bar fails to progress beyond
  //      progressTicks ticks past the spike level in the AGGRESSOR direction
  //      — aggressor = the level's dominant side (buy ≥ sell → buyers were
  //      hitting into it → progress would be next bar's high clearing
  //      price + progressTicks·tick; mirrored for sells vs the next low).
  // Both legs true → one event {kind:'absorption', ts (spike bar's t), price,
  // side, vol, medianVol, label:'heuristic'}. `tickSize` must match the
  // FootprintStore that built the bars (default 1, same as the store).
  function AbsorptionDetector(opts) {
    const o = opts || {};
    const volK = posOr(o.volK, 3);
    const progressTicks = finiteOr(o.progressTicks, 1);
    const tick = posOr(o.tickSize, 1);
    const ring = makeRing(100); // events, oldest→newest (SpoofIcebergDetector precedent)
    let pending = null;         // spike candidates from the previous finished bar

    /** Feed finished bars IN ORDER (terminal.js feeds each bar once as it
     *  closes; the self-check replays FootprintStore.bars()). Unfinished or
     *  malformed bars are ignored — never resolved against. */
    function onBar(bar) {
      if (!bar || bar.finished !== true || !Array.isArray(bar.levels) || !bar.levels.length) return;
      if (!Number.isFinite(bar.h) || !Number.isFinite(bar.l)) return;

      // 1. Resolve the previous bar's candidates against THIS bar's range.
      //    The 1e-9 epsilon absorbs float residue in price+ticks arithmetic
      //    (snapTick's own epsilon rationale) — never decides a real case.
      if (pending) {
        for (const c of pending.candidates) {
          const progressed = c.side === 'buy'
            ? bar.h - c.price > progressTicks * tick + 1e-9
            : c.price - bar.l > progressTicks * tick + 1e-9;
          if (!progressed) {
            ring.push({
              kind: 'absorption', ts: pending.ts, price: c.price, side: c.side,
              vol: c.vol, medianVol: c.med, label: 'heuristic',
            });
          }
        }
      }

      // 2. Collect this bar's spike candidates (they resolve on the NEXT bar).
      const cells = [];
      for (const lv of bar.levels) {
        if (!lv || !Number.isFinite(lv.price)
            || !Number.isFinite(lv.buy) || !Number.isFinite(lv.sell)) continue;
        cells.push({ price: lv.price, buy: lv.buy, sell: lv.sell, vol: lv.buy + lv.sell });
      }
      const candidates = [];
      if (cells.length) {
        const vols = cells.map((c) => c.vol).sort((a, b) => a - b);
        const m = vols.length;
        const med = m % 2 ? vols[(m - 1) / 2] : 0.5 * (vols[m / 2 - 1] + vols[m / 2]);
        if (med > 0) { // an all-empty bar has no baseline to spike against
          for (const c of cells) {
            if (c.vol >= volK * med) {
              candidates.push({ price: c.price, side: c.buy >= c.sell ? 'buy' : 'sell', vol: c.vol, med });
            }
          }
        }
      }
      pending = { ts: bar.t, candidates };
    }

    /** Retained events, oldest→newest, ≤100, EVERY one labeled 'heuristic'
     *  (the label rides the event so no view can drop it by accident). */
    function events() { return ring.toArray(); }

    return { onBar, events };
  }

  // ─── cumDelta(bars) — footprint cumulative-delta series accessor (§4f) ───
  //
  // Running sum of per-bar delta (buyVol−sellVol, already computed by
  // FootprintStore.snapshot) over the bars IN THE ORDER GIVEN →
  // [{ts, cum}] for the footprint cum-delta mini-pane. SESSION-ANCHORED like
  // CvdStore: cum-delta has no natural zero — only slope/divergence read —
  // so the anchor is the first bar in the window and the view states it.
  // Bars with a non-finite t or delta are SKIPPED, never zero-coerced (a
  // fabricated flat bar would fake "no net flow" — CvdStore hygiene).
  function cumDelta(bars) {
    const out = [];
    let cum = 0;
    for (const b of Array.isArray(bars) ? bars : []) {
      if (!b || !Number.isFinite(b.t) || !Number.isFinite(b.delta)) continue;
      cum += b.delta;
      out.push({ ts: b.t, cum });
    }
    return out;
  }

  // ════════ T-1 (§4g) — Trader's Edge stores: pure, event-time driven ══════
  //
  // §4g rails, restated where they bite: everything below is DESCRIPTIVE
  // (§0.1 — nothing feeds a signal or the OOS harness); every threshold is a
  // labeled CONVENTION, not a validated parameter; citations ride the code
  // that implements the cited statistic. Same two rails as every store above:
  // zero DOM, zero Date.now() — event ts is the only clock.

  // ─── TapeIntensityStore() — tape speed windows + session z (§4g) ─────────
  //
  // push(ts, notional) per trade (ts = event epoch ms; notional = price·qty
  // USD — one unit across venues, CvdStore precedent). Keeps:
  //   - rolling 10 s / 60 s windows → trades/sec + notional/sec, anchored at
  //     the NEWEST push's ts (LiqStore's event-time anchor: frozen during a
  //     quiet spell is honest under replay — a wall clock would poison it);
  //   - a session baseline of COMPLETED per-10s bucket trade counts via the
  //     Welford one-pass update (same recurrence as AnchoredVwap, unweighted)
  //     → z-score of the CURRENT rolling-10s trade count vs that baseline
  //     (identical 10 s spans, so counts compare like-for-like);
  //   - ring(60) of completed per-10s notional samples for the sparkline
  //     (~10 min of tape speed).
  // Buckets close on EVENT TIME only, FootprintStore's rule: a bucket with
  // zero trades never exists — after a quiet gap the next print jumps the
  // bucket index and no zero samples are synthesized (gaps are gaps, §0.7;
  // consequence stated: the baseline is over buckets that actually printed,
  // so it reads "how fast is the tape when it moves", not wall-time average).
  // z stays 0 (not NaN) below 5 baseline samples or at zero variance — a
  // stated convention of THIS implementation (§4g pins no such number; the
  // panel hint states it): 0 renders the gauge calm while the baseline
  // forms — a deliberate, stated exception to the NaN-for-unknown house
  // convention.
  //
  // Window prune is a head-index deque over one shared event array — O(1)
  // amortized per push (each event enters and leaves each window once); the
  // consumed head is compacted away once it grows past a fixed slack. Late
  // prints (ts before the open bucket's start) are DROPPED, FootprintStore's
  // rule; stray in-bucket ts inversions just evict late (detector precedent).
  function TapeIntensityStore() {
    const W10 = 10000, W60 = 60000;   // the §4g windows
    const BUCKET = 10000;             // baseline sample span = the 10 s window
    const MIN_BASELINE = 5;           // stated convention: z = 0 until ≥5 completed samples
    const spark = makeRing(60);       // completed {ts, notional} per-10s samples

    let evs = [];                     // shared deque: {ts, notional}, ts-ascending
    let head10 = 0, head60 = 0;       // window head indexes into evs
    let n10 = 0, n60 = 0;             // running notional sums per window
    let bStart = NaN;                 // open bucket's start ts (NaN = none yet)
    let bCount = 0, bNotional = 0;    // open bucket accumulators
    let wN = 0, wMean = 0, wM2 = 0;   // Welford over completed bucket counts

    function completeBucket() {
      wN++;
      const d = bCount - wMean;
      wMean += d / wN;
      wM2 += d * (bCount - wMean);
      spark.push({ ts: bStart, notional: bNotional });
    }

    /** Ingest one trade. Non-finite / non-positive notional → dropped. */
    function push(ts, notional) {
      if (!Number.isFinite(ts) || !Number.isFinite(notional) || notional <= 0) return;
      if (Number.isFinite(bStart)) {
        if (ts < bStart) return; // late print — see header
        if (ts >= bStart + BUCKET) {
          completeBucket();
          bStart = Math.floor(ts / BUCKET) * BUCKET; // jump — no zero buckets
          bCount = 0; bNotional = 0;
        }
      } else {
        bStart = Math.floor(ts / BUCKET) * BUCKET;
      }
      bCount++; bNotional += notional;

      evs.push({ ts, notional });
      n10 += notional; n60 += notional;
      // Prune both windows to (ts − W, ts] — the LiqStore half-open window.
      while (head60 < evs.length && evs[head60].ts <= ts - W60) { n60 -= evs[head60].notional; head60++; }
      while (head10 < evs.length && evs[head10].ts <= ts - W10) { n10 -= evs[head10].notional; head10++; }
      if (head60 > 2048) { // compact the consumed head — O(1) amortized
        evs = evs.slice(head60);
        head10 -= head60; head60 = 0;
      }
    }

    /** {tradesPerSec10, notionalPerSec10, tradesPerSec60, notionalPerSec60,
     *  z, baselineN} at the newest event's anchor. Empty store → all-zero
     *  (an untouched tape genuinely has rate 0 — not an unknown). */
    function stats() {
      const c10 = evs.length - head10, c60 = evs.length - head60;
      let z = 0;
      if (wN >= MIN_BASELINE) {
        const sd = Math.sqrt(wM2 / (wN - 1)); // sample stdev ddof=1 (quant.js std)
        if (sd > 0) z = (c10 - wMean) / sd;
      }
      return {
        tradesPerSec10: c10 / 10, notionalPerSec10: n10 / 10,
        tradesPerSec60: c60 / 60, notionalPerSec60: n60 / 60,
        z, baselineN: wN,
      };
    }

    /** Completed per-10s samples, oldest→newest, ≤60 — the sparkline feed. */
    function sparkline() { return spark.toArray(); }

    return { push, stats, sparkline };
  }

  // ─── WallsLedger({k, m, max}) — big-level book history ledger (§4g) ──────
  //
  // DESCRIPTIVE BOOK-HISTORY BOOKKEEPING, NOT INTENT (§4g label, kept on the
  // panel): the ledger records what unusually large resting levels DID —
  // entered the book, got pulled, or traded through — and claims nothing
  // about why. It cross-references the SpoofIcebergDetector's rule family
  // (its spoof-pull is the same "big level vanished untraded" observation
  // under extra time/coverage constraints) but is deliberately NOT merged
  // with it: the detector emits pattern-consistent-with-spoofing events, the
  // ledger keeps a neutral history of every wall regardless of lifetime.
  //
  // Caller contract (terminal.js feeds from the same grouped ladder cadence
  // as DepthHistoryStore): one update() per sample for every level worth
  // reporting — including a qty-0 (or shrunk) update for a level previously
  // tracked, which is how disappearance is observed. `p95qty` is the
  // caller's p95 of ladder level sizes (the baseline; p95 not median so one
  // whale neighbor cannot hide a wall — same argument as the detector's
  // median, one notch stricter), `ticksFromMid` the level's distance from
  // mid in ticks. `midPrice` is accepted for signature parity with §4g and
  // is informational only — the pull rule keys off ticksFromMid.
  //
  // Rules (K=4, M=5 — §4g conventions, constructor-overridable):
  //   - ENTER: qty ≥ K·p95 sustained over ≥ M CONSECUTIVE samples → ledger
  //     entry {price, side, firstTs, lastTs, maxQty, status:'standing'}.
  //     A sub-threshold sample resets the streak — M−1 never enters.
  //   - PULLED: a standing wall's level vanishes (update below threshold)
  //     while MORE than 1 tick from mid — it left without price ever getting
  //     there, so it cannot have traded. Book fact, not intent.
  //   - Vanish AT/WITHIN 1 tick of mid is AMBIGUOUS (it may be mid-fill):
  //     status stays 'standing' and only markTrade() may flip it to
  //     'filled' — we refuse to guess between fill and pull (honest degrade).
  //   - FILLED: markTrade(ts, price) crossed the level (price ≤ wall for
  //     bids, ≥ for asks — a print AT the level is the wall trading).
  // Ring 50, list() newest-first (feed-view convention).
  function WallsLedger(opts) {
    const o = opts || {};
    const K = posOr(o.k, 4);
    const M = Math.max(1, Math.floor(finiteOr(o.m, 5)));
    const ring = makeRing(finiteOr(o.max, 50));
    // side@price → {streak, firstTs, maxQty, entry|null} for live candidates.
    // Bounded by the ladder the caller reports (≤ levels per sample) — a
    // broken streak or resolved wall deletes its key immediately.
    const track = new Map();

    const keyOf = (side, price) => side + '@' + roundPx(price);

    /** One level observation from one depth sample — see caller contract. */
    function update(ts, side, price, qty, p95qty, ticksFromMid, midPrice) {
      void midPrice; // informational only — see caller contract above
      if (!Number.isFinite(ts) || !Number.isFinite(price) || !Number.isFinite(qty)) return;
      if (side !== 'bid' && side !== 'ask') return;
      if (!Number.isFinite(p95qty) || p95qty <= 0) return; // no baseline → no wall judgment
      const key = keyOf(side, price);
      const st = track.get(key);

      if (qty > 0 && qty >= K * p95qty) { // wall-sized this sample
        if (!st) {
          track.set(key, { streak: 1, firstTs: ts, maxQty: qty, entry: null });
          if (M <= 1) enter(track.get(key), side, price, ts);
          return;
        }
        st.streak++;
        if (qty > st.maxQty) st.maxQty = qty;
        if (st.entry) { st.entry.lastTs = ts; st.entry.maxQty = st.maxQty; }
        else if (st.streak >= M) enter(st, side, price, ts);
        return;
      }

      // Below threshold: streak broken / wall gone. Resolve then forget.
      if (!st) return;
      if (st.entry && st.entry.status === 'standing'
          && Number.isFinite(ticksFromMid) && ticksFromMid > 1) {
        st.entry.status = 'pulled'; // > 1 tick out — price never got there
        st.entry.lastTs = ts;
      }
      // ≤ 1 tick from mid (or unknown distance): ambiguous — stays
      // 'standing' until a markTrade() cross confirms 'filled' (see header).
      track.delete(key);
    }

    function enter(st, side, price, ts) {
      st.entry = {
        price: roundPx(price), side,
        firstTs: st.firstTs, lastTs: ts, maxQty: st.maxQty, status: 'standing',
      };
      ring.push(st.entry);
    }

    /** A trade print — flips standing walls the price crossed to 'filled'.
     *  Also drops their live tracking: a wall that re-forms at the same
     *  price after being eaten is a NEW wall and re-earns its M samples. */
    function markTrade(ts, price) {
      if (!Number.isFinite(ts) || !Number.isFinite(price)) return;
      for (const e of ring.toArray()) {
        if (e.status !== 'standing') continue;
        const crossed = e.side === 'bid' ? price <= e.price : price >= e.price;
        if (!crossed) continue;
        e.status = 'filled';
        e.lastTs = ts;
        track.delete(keyOf(e.side, e.price));
      }
    }

    /** Ledger entries NEWEST-FIRST, ≤50 (feed-view convention). */
    function list() { return ring.toArray().reverse(); }

    return { update, markTrade, list };
  }

  // ─── VpinStore(bucketVol) — volume-synchronized flow imbalance (§4g) ─────
  //
  // CITATION (§4g mandatory): D. Easley, M. López de Prado & M. O'Hara
  // (2012), "Flow Toxicity and Liquidity in a High-Frequency World", Review
  // of Financial Studies 25(5) — VPIN: trades are grouped into equal-VOLUME
  // buckets (the volume clock), each completed bucket contributes
  // |buyVol − sellVol| / V, and VPIN is the mean over recent buckets.
  //
  // Two departures from the paper, both STATED:
  //   - We classify with the REAL per-trade aggressor flags (§0.6 normalized
  //     taker side) — BETTER-INFORMED than the paper's Bulk Volume
  //     Classification, which infers sides probabilistically from price
  //     changes because their futures feed lacked trade signs. Same
  //     statistic, strictly better input; stated, not oversold.
  //   - The TOXICITY INTERPRETATION IS CONTESTED: Andersen & Bondarenko
  //     (2014, Journal of Financial Markets) show VPIN's predictive content
  //     is largely explained by trading intensity/volatility and dispute the
  //     flash-crash early-warning claim. We show the series; we do not claim
  //     toxicity. Descriptive only (§0.1).
  //
  // push(ts, qty, isBuy) splits a trade that straddles a bucket boundary
  // EXACTLY: the boundary slice completes the bucket, the remainder opens
  // the next (looping — one print can span several buckets). The live
  // caller arms V ≈ session-volume/50 re-estimated hourly (§4g);
  // setBucketVol(v) re-arms FUTURE buckets only — completed buckets keep
  // the V they were measured at (their |Δ|/V is never restated), and a
  // partially-filled bucket completes at its armed V (changing the boundary
  // mid-fill would make the split ill-defined). An EMPTY in-progress bucket
  // re-arms immediately (nothing measured yet — it IS a future bucket).
  function VpinStore(bucketVol) {
    let curV = posOr(bucketVol, 50); // base-asset units; caller arms the real V
    let nextV = curV;
    let buy = 0, sell = 0;           // open bucket accumulators
    const ring = makeRing(50);       // §4g: last 50 completed {ts, imb}

    /** Ingest one trade's (qty, side); ts stamps any bucket it completes. */
    function push(ts, qty, isBuy) {
      if (!Number.isFinite(ts) || !Number.isFinite(qty) || qty <= 0) return;
      let rem = qty;
      while (rem > 0) {
        const space = curV - buy - sell;
        if (rem >= space) { // fills (or exactly closes) the bucket — split here
          if (isBuy) buy += space; else sell += space;
          ring.push({ ts, imb: Math.abs(buy - sell) / curV });
          rem -= space;
          buy = 0; sell = 0; curV = nextV;
        } else {
          if (isBuy) buy += rem; else sell += rem;
          rem = 0;
        }
      }
    }

    /** Mean |Δ|/V over the retained completed buckets; null until the first
     *  bucket completes (no estimate is null, never a fabricated 0). */
    function vpin() {
      const all = ring.toArray();
      if (!all.length) return null;
      let s = 0;
      for (const b of all) s += b.imb;
      return s / all.length;
    }

    /** Completed buckets oldest→newest, ≤50 — the sparkline feed. */
    function buckets() { return ring.toArray(); }

    /** Re-arm FUTURE buckets at v — see header for what "future" means. */
    function setBucketVol(v) {
      if (!Number.isFinite(v) || v <= 0) return;
      nextV = v;
      if (buy + sell === 0) curV = v; // empty open bucket is a future bucket
    }

    return { push, vpin, buckets, setBucketVol, get bucketVol() { return curV; } };
  }

  // ─── OpeningTypeClassifier(openTs) — AMT opening-type read (§4g) ─────────
  //
  // CITATION (§4g mandatory): J. Dalton, Mind over Markets — the four classic
  // opening types (open-drive, open-test-drive, open-rejection-reverse,
  // open-auction). DESCRIPTIVE SESSION READ — NOT A SIGNAL (§0.1; the label
  // rides every classify() result). Dalton's types are qualitative floor
  // reads; turning them into code requires cutoffs, and EVERY cutoff below
  // is a labeled CONVENTION of this implementation, not a validated
  // parameter and not Dalton's numbers:
  //   - WINDOW_MS  60 min — the opening window being classified (§4g).
  //   - PROBE_MS   30 min — a test-drive's probe must complete this fast (§4g).
  //   - RETRACE_FRAC 0.2  — open-drive purity: max retrace against the drive
  //     < 20% of the open range (§4g); doubles as the minimum first-leg size
  //     for rejection-reverse (a leg under 20% of range is noise, not a drive).
  //   - PROBE_FRAC 0.5    — a "probe" is ≤ half the eventual opposite drive;
  //     larger first legs read as a drive that reversed, not a test.
  //
  // Event-time only: feed(ts, price) ignores pre-open prints, uses prints
  // inside [openTs, openTs+60min) for the stats, and later prints only to
  // advance the clock (classify() unlocks on EVENT time — replay rail).
  // State is O(1) scalars (extremes, retraces, open-cross count) — no price
  // array; deterministic: same tape in → same type out.
  //
  // classify() → {type:'pending'} until 60 min elapsed, then {type, evidence,
  // label}. Precedence (first match wins — deterministic):
  //   1. degenerate zero range → open-auction (a flat hour is rotational);
  //   2. open-drive: retrace against the dominant direction < 20% of range;
  //   3. open-test-drive: first extreme is a PROBE (≤30 min, ≤ half the
  //      opposite extension, beyond the open) and the drive went opposite,
  //      with at most one open cross (the probe's return);
  //   4. open-rejection-reverse: a real first leg (≥20% of range), then
  //      EXACTLY one full cross back through the open with no re-cross —
  //      drove, got rejected, reversed and stayed reversed;
  //   5. else open-auction (rotational — repeated open crosses land here).
  function OpeningTypeClassifier(openTs) {
    const WINDOW_MS = 3600000, PROBE_MS = 1800000;    // §4g conventions
    const RETRACE_FRAC = 0.2, PROBE_FRAC = 0.5;       // labeled conventions
    const LABEL = 'descriptive session read — not a signal';
    const t0 = finiteOr(openTs, NaN);

    let open = NaN, lastTs = NaN, n = 0;
    let hi = -Infinity, hiTs = NaN, lo = Infinity, loTs = NaN;
    let retraceUp = 0, retraceDn = 0; // max path move against each direction
    let prevSide = 0, crossCount = 0; // open-cross bookkeeping (at-open holds side)

    /** Ingest one print. Pre-open and malformed prints are dropped; prints
     *  after the window only advance the event clock. */
    function feed(ts, price) {
      if (!Number.isFinite(t0) || !Number.isFinite(ts) || !Number.isFinite(price)) return;
      if (ts < t0) return;                    // pre-open — not the opening auction
      if (!(ts <= lastTs)) lastTs = ts;       // monotone event clock (NaN-safe)
      if (ts >= t0 + WINDOW_MS) return;       // window closed — clock only
      if (!Number.isFinite(open)) open = price;
      n++;
      if (price > hi) { hi = price; hiTs = ts; }
      if (price < lo) { lo = price; loTs = ts; }
      if (hi - price > retraceDn) retraceDn = hi - price;
      if (price - lo > retraceUp) retraceUp = price - lo;
      const s = price > open ? 1 : price < open ? -1 : 0;
      if (s !== 0) {
        if (prevSide !== 0 && s !== prevSide) crossCount++;
        prevSide = s;
      }
    }

    function classify() {
      if (!Number.isFinite(t0) || !Number.isFinite(open)
          || !(lastTs - t0 >= WINDOW_MS)) return { type: 'pending', label: LABEL };
      const range = hi - lo;
      const extUp = hi - open, extDn = open - lo;
      const dir = extUp >= extDn ? 'up' : 'down';        // dominant side (tie → up)
      const firstSide = hiTs <= loTs ? 'up' : 'down';    // earlier extreme (tie → up)
      const firstExt = firstSide === 'up' ? extUp : extDn;
      const secondExt = firstSide === 'up' ? extDn : extUp;
      const firstTsRel = (firstSide === 'up' ? hiTs : loTs) - t0;
      const evidence = {
        open, hi, lo, range, extUp, extDn, retraceUp, retraceDn,
        hiTs, loTs, dir, firstSide, crossCount, n,
      };
      const done = (type) => ({ type, evidence, label: LABEL });

      if (!(range > 0)) return done('open-auction');
      const driveRetrace = dir === 'up' ? retraceDn : retraceUp;
      if (driveRetrace < RETRACE_FRAC * range) return done('open-drive');
      if (firstSide !== dir && firstExt > 0 && firstTsRel <= PROBE_MS
          && firstExt <= PROBE_FRAC * secondExt && crossCount <= 1) {
        return done('open-test-drive');
      }
      if (firstExt >= RETRACE_FRAC * range && crossCount === 1) {
        return done('open-rejection-reverse');
      }
      return done('open-auction');
    }

    return { feed, classify, openTs: t0 };
  }

  // ─── BasisSeries({max}) — perp basis + funding history ring (§4g) ────────
  //
  // Ring (default 3600 ≈ 1 h at the existing 1 s mark cadence — §4g: NO new
  // feeds, this is fed from the mark events the header already consumes) of
  // {ts, basisBp, fundingRate} for the STRUCTURE two-pane mini chart.
  // fundingRate may be absent on a sample (Bybit tickers partial deltas omit
  // unchanged fields) — stored as NaN, never zero-coerced: a fabricated 0
  // would claim "flat funding" where the honest read is "unchanged/unknown";
  // views skip NaN points (ProfileStore's NaN convention).
  function BasisSeries(opts) {
    const ring = makeRing(finiteOr(opts && opts.max, 3600));
    let last = null;

    function push(ts, basisBp, fundingRate) {
      if (!Number.isFinite(ts) || !Number.isFinite(basisBp)) return;
      last = { ts, basisBp, fundingRate: Number.isFinite(fundingRate) ? fundingRate : NaN };
      ring.push(last);
    }

    /** Oldest→newest, ≤max — the chart feed. */
    function list() { return ring.toArray(); }

    /** Newest sample or null (never a fabricated zero row). */
    function latest() { return last; }

    return { push, list, latest, get length() { return ring.length; } };
  }

  // ─── deriveVenueIds(sym) — per-venue symbol mapping (§4g) ────────────────
  //
  // Mirrors the collector's `_symbol_legs` convention (btcquant/collector.py
  // — ONE derivation for record and replay, base = symbol with the trailing
  // USDT stripped) with one stated difference: where the collector keeps a
  // heuristic passthrough for non-USDT symbols (and logs the derived ids so
  // a wrong mapping is visible), the terminal returns NULL for a leg it
  // cannot derive, so the UI degrades honestly ('no <venue> leg for <sym>'
  // chip, §4g) instead of subscribing to a guessed id. A derived id is a
  // NAMING convention, not proof of a listing — startAllLegs (terminal.js)
  // probes derived binancef/coinbase ids against the venue before
  // subscribing (§4g unknown/unreachable rule).
  //   - bybit: the picker universe's own id, as-is — always known.
  //   - binancef: the same USDT-perp id — a convention shared only by USDT
  //     perps, so a non-USDT symbol degrades to null, never a guess.
  //   - okx: <base>-USDT-SWAP; coinbase: <base>-USD (spot).
  //   - Not USDT-quoted → binancef + okx + coinbase null (unknown mapping).
  //   - Base starts with digits (1000PEPE — a multiplied perp contract) →
  //     coinbase null: no such SPOT market exists, the 1000× prefix is a
  //     derivatives listing artifact.
  function deriveVenueIds(sym) {
    const s = typeof sym === 'string' ? sym : '';
    if (!s) return { bybit: null, binancef: null, okx: null, coinbase: null };
    const out = { bybit: s, binancef: null, okx: null, coinbase: null };
    if (!s.endsWith('USDT') || s.length <= 4) return out; // unknown mapping — honest nulls
    const base = s.slice(0, -4);
    out.binancef = s;
    out.okx = base + '-USDT-SWAP';
    if (!/^\d/.test(base)) out.coinbase = base + '-USD';
    return out;
  }

  // ─── deriveLegIds(sym) — 7-leg venue×market matrix mapping (§4h) ─────────
  //
  // T-2 extension of deriveVenueIds to the full matrix. ADDITIVE on purpose:
  // deriveVenueIds keeps its exact T-1 shape for existing consumers
  // (terminal.js startAllLegs, the collector-mirror contract above) and this
  // function owns the seven-leg registry. Same philosophy as its T-1 parent:
  // a derived id is a NAMING convention, not proof of a listing — the per-leg
  // listability probes (§4h reuses the T-1 rail) gate every derived id before
  // subscribing, and a leg we cannot even NAME is null so the UI degrades
  // honestly instead of guessing.
  //   - bybit_linear: the picker universe's own id, as-is — always known.
  //   - Derivatives + USDT-spot ids only exist for USDT-quoted symbols:
  //     non-USDT → every leg but bybit_linear is null (T-1 rule, §4g).
  //   - Spot ids (§4h): bybit_spot/binance_spot = <base>USDT (the perp id —
  //     same string, different venue endpoint), okx_spot = <base>-USDT,
  //     coinbase = <base>-USD.
  //   - Digit-prefixed base (1000PEPE — a multiplied perp contract) →
  //     coinbase null, the T-1 hard rule carried over: no USD spot market can
  //     exist for a derivatives multiplier artifact. The USDT-spot ids stay
  //     DERIVED for such bases — whether a venue lists a 1000×-spot pair is a
  //     listing question the probe answers, not a naming impossibility.
  function deriveLegIds(sym) {
    const s = typeof sym === 'string' ? sym : '';
    const out = {
      bybit_linear: s || null, bybit_spot: null, binancef: null,
      binance_spot: null, okx_swap: null, okx_spot: null, coinbase: null,
    };
    if (!s.endsWith('USDT') || s.length <= 4) return out; // unknown mapping — honest nulls
    const base = s.slice(0, -4);
    out.bybit_spot = s;
    out.binancef = s;
    out.binance_spot = s;
    out.okx_swap = base + '-USDT-SWAP';
    out.okx_spot = base + '-USDT';
    if (!/^\d/.test(base)) out.coinbase = base + '-USD';
    return out;
  }

  // ─── LegRegistry({enabled}) — the 7-leg venue×market matrix store (§4h) ──
  //
  // Pure session store behind the T-2 leg lifecycle: one row per matrix leg
  // (keys ALIGN with deriveLegIds — the registry names WHICH legs exist, the
  // mapping names their venue ids). `enabled` is the persisted user choice
  // (seeded by the caller from settings; default ALL enabled per §4h) — the
  // caller consults it BEFORE opening a socket, so a disabled leg never
  // subscribes and its chip states the reason ('disabled (settings)') instead
  // of pretending to connect. `status` is caller-owned bookkeeping (the leg's
  // last chip state) carried for the UI snapshot — this store never invents
  // one. No clocks, no DOM: same rails as every store above.
  function LegRegistry(opts) {
    // The seven §4h legs, fixed order (matrix display order). `venue` is the
    // exchange family, `market` the leg's product class — 'perp'|'spot' is
    // what SpotPerpCvdStore's isPerp split keys on, so it lives HERE (one
    // source of truth) rather than being re-derived per call site.
    const DEFS = [
      { key: 'bybit_linear', venue: 'bybit', market: 'perp' },
      { key: 'bybit_spot', venue: 'bybit', market: 'spot' },
      { key: 'binancef', venue: 'binance', market: 'perp' },
      { key: 'binance_spot', venue: 'binance', market: 'spot' },
      { key: 'okx_swap', venue: 'okx', market: 'perp' },
      { key: 'okx_spot', venue: 'okx', market: 'spot' },
      { key: 'coinbase', venue: 'coinbase', market: 'spot' },
    ];
    const legs = new Map(); // key → {key, venue, market, enabled, status}
    const seed = (opts && opts.enabled) || {};
    for (const d of DEFS) {
      // Only a STRICT stored boolean overrides the all-enabled default —
      // corrupt/stale storage must not silently kill a leg (settings rail).
      const en = typeof seed[d.key] === 'boolean' ? seed[d.key] : true;
      legs.set(d.key, { key: d.key, venue: d.venue, market: d.market, enabled: en, status: null });
    }

    /** The 7 leg keys in matrix order. */
    function keys() { return DEFS.map((d) => d.key); }

    /** One leg's row as a COPY (callers must not reach into the store), or
     *  null for an unknown key — never a guessed row. */
    function get(key) {
      const l = legs.get(key);
      return l ? { key: l.key, venue: l.venue, market: l.market, enabled: l.enabled, status: l.status } : null;
    }

    /** Unknown keys read as DISABLED: a leg the registry cannot name must
     *  never be started (deriveLegIds' honest-null philosophy). */
    function isEnabled(key) {
      const l = legs.get(key);
      return !!l && l.enabled;
    }

    /** market 'perp' → true — the SpotPerpCvdStore split (§4h). */
    function isPerp(key) {
      const l = legs.get(key);
      return !!l && l.market === 'perp';
    }

    /** Flip a leg. Returns true iff the stored value CHANGED (the caller
     *  restarts sockets / persists only on a real transition). Non-boolean
     *  or unknown-key calls are dropped, never coerced. */
    function setEnabled(key, on) {
      const l = legs.get(key);
      if (!l || typeof on !== 'boolean' || l.enabled === on) return false;
      l.enabled = on;
      return true;
    }

    /** Caller-owned status bookkeeping (chip state); unknown keys dropped. */
    function setStatus(key, status) {
      const l = legs.get(key);
      if (l) l.status = status;
    }

    /** {key: enabled} of all 7 legs — the persistence payload shape. */
    function enabledMap() {
      const out = {};
      for (const d of DEFS) out[d.key] = legs.get(d.key).enabled;
      return out;
    }

    /** All 7 rows as copies, matrix order — the UI's one read. */
    function snapshot() { return DEFS.map((d) => get(d.key)); }

    return { keys, get, isEnabled, isPerp, setEnabled, setStatus, enabledMap, snapshot };
  }

  // ─── TapeAggregator({aggWindowMs, size}) — merged-tape print aggregation (§4i)
  //
  // Ports aggr's aggregated TAPE model (§4i): consecutive prints that are the
  // SAME venue, SAME aggressor side, at the SAME price and inside a short time
  // window collapse into ONE row shown `×count` — the "read the tape" surface
  // where a swept level reads as a single block instead of a blur of dust.
  // Mirrors aggr's `aggregationLength`. The one rule this store exists to keep:
  // a run NEVER merges across venues — two exchanges printing the same price in
  // the same millisecond are two prints, and fusing them would FAKE a single
  // print that never happened on any one book (§0.7 fabrication — forbidden).
  //
  // Boundaries that FLUSH the open row and start a fresh one: a price change, a
  // side flip, a venue (ex) change, or WINDOW EXPIRY — a same-ex/side/price
  // print whose ts is more than aggWindowMs past the run's FIRST print (the
  // window is anchored on the run start, so a stuck price cannot grow one
  // unbounded row; it splits into ≤ aggWindowMs buckets, aggr's fixed bucket).
  // The window edge is INCLUSIVE (≤ aggWindowMs merges) — stated convention.
  //
  // No clocks, no DOM (file rails): the window is measured on the EVENT ts, and
  // the open (in-progress) row shows in list() as the newest entry the way
  // FootprintStore.bars() appends its open bar — the tape must show the block
  // that is forming, not only completed ones. row.ts is the run's MOST RECENT
  // print (age reads off the latest activity); the internal startTs anchors the
  // window. VWAP = Σ(price·qty)/Σqty (equals the shared price on a same-price
  // run — computed generally, correct either way).
  function TapeAggregator(opts) {
    const o = opts || {};
    const aggWindowMs = finiteOr(o.aggWindowMs, 100);
    const ring = makeRing(finiteOr(o.size, 200));
    let open = null; // {startTs, ts, ex, isBuy, priceKey, sumQty, sumPxQty, sumNotional, count}

    /** Freeze the open accumulator into a render row (VWAP price + count). */
    function materialize(a) {
      return {
        ts: a.ts, ex: a.ex, isBuy: a.isBuy,
        price: a.sumPxQty / a.sumQty,
        qty: a.sumQty, notional: a.sumNotional, count: a.count,
      };
    }

    /** Ingest one normalized trade {ts, ex, isBuy, price, qty, notional}.
     *  Malformed prints (non-finite ts/price/qty, non-positive qty, or no
     *  venue label) are DROPPED — an unlabelled or zero-size row cannot be a
     *  merge-keyed tape print (validTrade / SpotPerpCvdStore hygiene). */
    function push(trade) {
      if (!trade) return;
      const ts = trade.ts, price = trade.price, qty = trade.qty;
      if (!Number.isFinite(ts) || !Number.isFinite(price) || !Number.isFinite(qty) || qty <= 0) return;
      if (typeof trade.ex !== 'string' || !trade.ex) return;
      const ex = trade.ex, isBuy = !!trade.isBuy, pk = roundPx(price);
      const notl = Number.isFinite(trade.notional) ? trade.notional : price * qty;

      // Mergeable iff same venue + side + price AND still inside the window
      // anchored on the run's first print. Anything else closes the run.
      const mergeable = open && open.ex === ex && open.isBuy === isBuy
        && open.priceKey === pk && (ts - open.startTs) <= aggWindowMs;
      if (mergeable) {
        open.ts = ts;
        open.sumQty += qty;
        open.sumPxQty += price * qty;
        open.sumNotional += notl;
        open.count++;
        return;
      }
      if (open) ring.push(materialize(open)); // flush the closed run
      open = {
        startTs: ts, ts, ex, isBuy, priceKey: pk,
        sumQty: qty, sumPxQty: price * qty, sumNotional: notl, count: 1,
      };
    }

    /** Aggregated rows NEWEST-FIRST: the forming open row first (it is the
     *  most recent activity), then flushed rows newest→oldest. */
    function list() {
      const out = ring.toArray();
      out.reverse();
      if (open) out.unshift(materialize(open));
      return out;
    }

    return { push, list, aggWindowMs, get length() { return ring.length + (open ? 1 : 0); } };
  }

  // ─── sizeTier(notional, thresholds) — USD-notional print classifier (§4i) ──
  //
  // Pure size-tier read for tape emphasis (§4i "read the tape" core): a print's
  // USD notional → 'baseline' | 'sig' | 'large' | 'huge' | 'whale'. Each cut is
  // a LABELED DISPLAY CONVENTION, not a signal (§0.1) — BTC-scaled defaults the
  // caller may override. Boundaries are INCLUSIVE LOWER (≥): a print exactly at
  // a threshold takes the HIGHER tier (the same ≥ convention as the tiers'
  // aggr origin). A partial override merges over the defaults so a caller can
  // move one cut without redeclaring the rest; a non-finite notional is
  // 'baseline' (a NaN size is not a whale — hygiene, never coerced upward).
  const SIZE_TIER_DEFAULTS = { sig: 1e5, large: 2.5e5, huge: 1e6, whale: 5e6 };

  function sizeTier(notional, thresholds) {
    if (!Number.isFinite(notional)) return 'baseline';
    const t = thresholds ? { ...SIZE_TIER_DEFAULTS, ...thresholds } : SIZE_TIER_DEFAULTS;
    // Strict top-down cascade: the override cuts MUST stay ascending
    // (sig < large < huge < whale). A non-monotone override (e.g. whale below
    // huge) lets the higher cut fire first and SHADOWS the lower tier — it goes
    // unreachable, silently. Deliberately NOT re-guarded here (a hot per-print
    // path): the coherence seam is upstream — BOTH the settings load and the
    // tier-input handler adopt only a strictly-increasing positive set, so
    // settings.tapeTiers is always monotone; only a direct caller hand-passing
    // a mis-ordered override (the check group's boundary probe) hits the
    // shadowing. Documented so an override author knows the ordering is
    // load-bearing, not incidental.
    if (notional >= t.whale) return 'whale';
    if (notional >= t.huge) return 'huge';
    if (notional >= t.large) return 'large';
    if (notional >= t.sig) return 'sig';
    return 'baseline';
  }

  // ─── liqTier(notional, thresholds) — liquidation notional classifier (§4i) ─
  //
  // Liquidations get their OWN notional tiers (§4i), separate from the tape's:
  // a forced-order's USD notional → 'baseline' | 'big' | 'huge'. Same INCLUSIVE
  // LOWER (≥) convention and non-finite→baseline hygiene as sizeTier; the cuts
  // are the same labeled DISPLAY conventions (not signals, §0.1). Pure so the
  // liq feed's ◆/◇ emphasis AND the audio-ping trigger read ONE classifier
  // (the ping fires on 'huge'), never a re-derived inline threshold.
  const LIQ_TIER_DEFAULTS = { big: 2.5e5, huge: 1e6 };

  function liqTier(notional, thresholds) {
    if (!Number.isFinite(notional)) return 'baseline';
    const t = thresholds ? { ...LIQ_TIER_DEFAULTS, ...thresholds } : LIQ_TIER_DEFAULTS;
    if (notional >= t.huge) return 'huge';
    if (notional >= t.big) return 'big';
    return 'baseline';
  }

  // ─── filterTapeRows(rows, opts) — merged-tape row filter + tag (§4i) ───────
  //
  // The merged multi-venue tape's row projection (§4i): the ONE aggregator's
  // rows (each already a single-venue block) filtered by min-notional / single-
  // venue / market, then tagged with the size tier and a spot|perp MARKET
  // label. Pure so the market filter (both/spot/perp drops the right side) and
  // the per-row spot/perp tag have an L0 witness — the enabled-leg half of the
  // §4i item is enforced UPSTREAM (a disabled leg's socket never opens, so no
  // print reaches the aggregator; the spot-vs-perp CVD store shares that rule),
  // not here. `marketOf(ex) → 'perp'|'spot'` is injected (the caller resolves
  // ex→leg→isPerp against the registry) so this stays registry-free and pure.
  function filterTapeRows(rows, opts) {
    const o = opts || {};
    const market = o.market || 'both';
    const venue = o.venue || 'all';
    const minN = o.minN;
    const tiers = o.tiers;
    const marketOf = typeof o.marketOf === 'function' ? o.marketOf : () => 'perp';
    const out = [];
    for (const r of (Array.isArray(rows) ? rows : [])) {
      if (!r) continue;
      if (Number.isFinite(minN) && minN > 0 && r.notional < minN) continue;
      if (venue !== 'all' && r.ex !== venue) continue;
      const mkt = marketOf(r.ex) === 'spot' ? 'spot' : 'perp';
      if (market === 'spot' && mkt !== 'spot') continue;
      if (market === 'perp' && mkt !== 'perp') continue;
      out.push({
        ts: r.ts, ex: r.ex, isBuy: r.isBuy, price: r.price, qty: r.qty,
        notional: r.notional, count: r.count,
        tier: sizeTier(r.notional, tiers), market: mkt,
      });
    }
    return out;
  }

  // ─── BigPrintRail({max, thresholds}) — pinned huge/whale strip (§4i) ───────
  //
  // The "don't-miss-the-block" surface (§4i): the last N aggregated tape rows
  // whose tier is 'huge' or 'whale', newest-first, ring-bounded. Fed the
  // TapeAggregator's rows; classifies each by sizeTier (thresholds shared with
  // the tape so the rail and the tape agree on what a block IS) and keeps only
  // the two top tiers. Descriptive emphasis, not a signal (§0.1).
  function BigPrintRail(opts) {
    const o = opts || {};
    const ring = makeRing(finiteOr(o.max, 12));
    const thresholds = o.thresholds; // undefined → sizeTier's defaults

    /** Feed one aggregated row; kept iff huge/whale. The stored row is a COPY
     *  tagged with its tier (callers must not reach into the source row). */
    function push(row) {
      if (!row || !Number.isFinite(row.notional)) return;
      const tier = sizeTier(row.notional, thresholds);
      if (tier !== 'huge' && tier !== 'whale') return;
      ring.push({ ...row, tier });
    }

    /** The kept blocks NEWEST-FIRST, ≤ max (feed-view convention). */
    function list() { return ring.toArray().reverse(); }

    return { push, list, get length() { return ring.length; } };
  }

  // ─── TradeImprint({windowMs}) — rolling executed volume-at-price (§4i) ─────
  //
  // The tape-meets-DOM surface (§4i): rolling-window executed buy vs sell
  // volume bucketed by price level, painted on the ladder as a mini
  // volume-at-price. push(ts, price, qty, isBuy, tickSize) folds one print into
  // its tick level; levelAt(price)/map() read the current window.
  //
  // Rounding convention: a print snaps to the NEAREST tick line
  // (Math.round(price/tick)) — the ladder row it visually sits on. The Map is
  // keyed by that INTEGER tick INDEX, never a float price: a key built from
  // float arithmetic drifts (roundPx header) and would split one level in two;
  // an integer index cannot. map() re-expands indices to grid prices for the
  // caller using the last-seen tick (upstream rebuilds the store on a
  // tick-size change — the honest-restart rule — so the grid is stable within
  // one store's life). Window is the half-open (ts−windowMs, ts] on EVENT ts
  // (TapeIntensityStore rule); pruning subtracts each aged print from its
  // level and DELETES a level once its last print ages out — that clears any
  // float residue so an empty price never lingers as a phantom bucket.
  function TradeImprint(opts) {
    const o = opts || {};
    const windowMs = posOr(o.windowMs, 60000);
    const levels = new Map(); // int tick index → {buyQty, sellQty, n}
    let evs = [];             // {ts, idx, qty, isBuy} ascending — the prune deque
    let head = 0;
    let curTick = NaN;        // last push's tick — the grid levelAt()/map() report on

    const tickIdx = (price, tick) => Math.round(price / tick);

    /** Fold one executed print into its tick level, then prune the window. */
    function push(ts, price, qty, isBuy, tickSize) {
      if (!Number.isFinite(ts) || !Number.isFinite(price) || !Number.isFinite(qty) || qty <= 0) return;
      const tick = posOr(tickSize, NaN);
      if (!Number.isFinite(tick)) return; // no grid → cannot bucket (never a zeroed level)
      curTick = tick;
      const idx = tickIdx(price, tick);
      let cell = levels.get(idx);
      if (!cell) { cell = { buyQty: 0, sellQty: 0, n: 0 }; levels.set(idx, cell); }
      if (isBuy) cell.buyQty += qty; else cell.sellQty += qty;
      cell.n++;
      evs.push({ ts, idx, qty, isBuy });
      // Prune (ts−windowMs, ts]; a level whose last print ages out is deleted
      // (n→0) rather than left at a float-residue ~0 — see header.
      while (head < evs.length && evs[head].ts <= ts - windowMs) {
        const e = evs[head++];
        const c = levels.get(e.idx);
        if (c) {
          if (e.isBuy) c.buyQty -= e.qty; else c.sellQty -= e.qty;
          if (--c.n <= 0) levels.delete(e.idx);
        }
      }
      if (head > 2048) { evs = evs.slice(head); head = 0; } // compact consumed head — O(1) amortized
    }

    /** {buyQty, sellQty} at price's tick level (a COPY), or zeros if untouched
     *  / before any push (a level with no prints genuinely has 0 volume). */
    function levelAt(price) {
      if (!Number.isFinite(price) || !Number.isFinite(curTick)) return { buyQty: 0, sellQty: 0 };
      const c = levels.get(tickIdx(price, curTick));
      return c ? { buyQty: c.buyQty, sellQty: c.sellQty } : { buyQty: 0, sellQty: 0 };
    }

    /** Snapshot Map<gridPrice, {buyQty, sellQty}> — indices re-expanded to
     *  prices on the current tick grid; fresh objects (no internal handles). */
    function map() {
      const out = new Map();
      if (!Number.isFinite(curTick)) return out;
      for (const [idx, c] of levels) out.set(roundPx(idx * curTick), { buyQty: c.buyQty, sellQty: c.sellQty });
      return out;
    }

    return { push, levelAt, map, windowMs, get size() { return levels.size; } };
  }

  // ─── DepthLadder helpers — pure reads over a best-first book snapshot (§4i)
  //
  // All three take a book snapshot {bids:[[px,qty]…] DESC, asks:[[px,qty]…]
  // ASC} — exactly the shape terminal-books.js topN()/BookStore.grouped()
  // materialize at paint cadence — and compute pure ladder reads. No clocks,
  // no venue logic here: the cross-venue aggregated ladder is mergeSameQuoteBooks
  // below (and is loudly caveated there); these operate on ONE book.

  /** ladderRows(book, mid, tickSize, nRows) → {bids:[…], asks:[…]} where each
   *  side holds ≤ nRows rows {side, price, qty, cum, ticks} from mid OUTWARD.
   *  Venue levels are bucketed onto the absolute tick grid (snapped toward the
   *  price-improving side — asks up, bids down, the file's snapTick convention
   *  — so a coarse tick-group selector merges sub-tick levels into one row);
   *  the book arrives best-first so insertion order IS mid-outward, and `cum`
   *  is the running qty from mid out to and including each row. `ticks` is the
   *  row's tick distance from mid (what makes mid load-bearing here). */
  function ladderRows(book, mid, tickSize, nRows) {
    const out = { bids: [], asks: [] };
    const tick = posOr(tickSize, NaN);
    if (!book || !Number.isFinite(mid) || !Number.isFinite(tick)) return out;
    const n = Math.max(0, Math.floor(finiteOr(nRows, 0)));

    const build = (levels, isBid) => {
      const grid = new Map(); // gridPrice → summed qty
      const seq = [];         // grid prices in first-seen (mid-outward) order
      for (const lvl of (Array.isArray(levels) ? levels : [])) {
        if (!lvl) continue;
        const p = Number(lvl[0]), q = Number(lvl[1]);
        if (!Number.isFinite(p) || !Number.isFinite(q) || q <= 0) continue;
        const gp = snapTick(p, tick, !isBid); // asks up, bids down
        if (!grid.has(gp)) { grid.set(gp, 0); seq.push(gp); }
        grid.set(gp, grid.get(gp) + q);
      }
      const rows = [];
      let cum = 0;
      for (const gp of seq) {
        if (rows.length >= n) break;
        const qty = grid.get(gp);
        cum += qty;
        rows.push({
          side: isBid ? 'bid' : 'ask',
          price: gp, qty, cum,
          ticks: Math.round(Math.abs(gp - mid) / tick),
        });
      }
      return rows;
    };
    out.bids = build(book.bids, true);
    out.asks = build(book.asks, false);
    return out;
  }

  /** depthImbalance(book, mid, nTicks, tickSize) → {bidSum, askSum, pct} over
   *  the levels within nTicks of mid. `pct` is the SIGNED (bidSum−askSum)/
   *  (bidSum+askSum) in −1..1 — the house convention (terminal.js bookImb10;
   *  +1 = all bids), NaN when the band is EMPTY (absence, not balance — the
   *  view scales ×100 for its % label). The band edge is INCLUSIVE: a level
   *  EXACTLY nTicks out counts (same ≥/≤ boundary convention as sizeTier).
   *  (Deviation from §4i's bare `within N ticks` sketch: tickSize is a real
   *  argument — a tick band is undefined without the tick, and reinterpreting
   *  nTicks as a raw price width would silently mis-scale it.) */
  function depthImbalance(book, mid, nTicks, tickSize) {
    const out = { bidSum: 0, askSum: 0, pct: NaN };
    const tick = posOr(tickSize, NaN);
    if (!book || !Number.isFinite(mid) || !Number.isFinite(tick)) return out;
    const n = Math.max(0, finiteOr(nTicks, 0));
    const band = n * tick;
    const eps = tick * 1e-9; // float slack so an exact-tick edge lands inside
    for (const lvl of (Array.isArray(book.bids) ? book.bids : [])) {
      if (!lvl) continue;
      const p = Number(lvl[0]), q = Number(lvl[1]);
      if (!Number.isFinite(p) || !Number.isFinite(q) || q <= 0) continue;
      if (p <= mid + eps && p >= mid - band - eps) out.bidSum += q; // within band, at/below mid
    }
    for (const lvl of (Array.isArray(book.asks) ? book.asks : [])) {
      if (!lvl) continue;
      const p = Number(lvl[0]), q = Number(lvl[1]);
      if (!Number.isFinite(p) || !Number.isFinite(q) || q <= 0) continue;
      if (p >= mid - eps && p <= mid + band + eps) out.askSum += q; // within band, at/above mid
    }
    const tot = out.bidSum + out.askSum;
    out.pct = tot > 0 ? (out.bidSum - out.askSum) / tot : NaN;
    return out;
  }

  /** logBarWidth(qty, maxQty) → depth-bar fraction in 0..1, LOG-scaled:
   *  log1p(qty)/log1p(maxQty). Log so one whale wall does not flatten every
   *  other level to an invisible sliver (§4i). Monotone in qty; clamped to
   *  [0,1] (qty > maxQty → 1); 0 for empty/non-positive/non-finite inputs
   *  (a bar with no size, never NaN width). */
  function logBarWidth(qty, maxQty) {
    if (!Number.isFinite(qty) || !Number.isFinite(maxQty) || maxQty <= 0 || qty <= 0) return 0;
    const w = Math.log1p(qty) / Math.log1p(maxQty);
    return w < 0 ? 0 : w > 1 ? 1 : w;
  }

  /** mergeSameQuoteBooks(booksByLeg, legMeta) → {book, includedLegs,
   *  excludedLegs} — the aggregated-ladder builder (§4i source-select).
   *
   *  LOUD CAVEAT (§4i, kept on the panel): cross-venue depth is a DISPLAY
   *  APPROXIMATION, NEVER a merged truth. Books from different matching engines
   *  do not share a queue; summing them onto one grid is a reading aid, not a
   *  real order book. The one hard rule that keeps it honest: ONLY same-quote
   *  (USDT) legs are summed — a coin/USD leg (coinbase BTC-USD) is EXCLUDED,
   *  not silently rescaled by a spot FX rate we do not have (§0.7; that would
   *  fabricate depth at fictitious prices).
   *
   *  booksByLeg: {legKey: {bids, asks}}. legMeta: {legKey: {quote, tickSize,
   *  primary?}}. The common grid is the leg flagged `primary` (its tickSize),
   *  falling back to the first included USDT leg's tick — stated, because a
   *  wrong grid would misalign every summed level. A leg with unknown/non-USDT
   *  quote is reported in excludedLegs and contributes nothing to the sum. */
  function mergeSameQuoteBooks(booksByLeg, legMeta) {
    const books = booksByLeg || {};
    const meta = legMeta || {};
    const includedLegs = [], excludedLegs = [], usdt = [];
    for (const k of Object.keys(books)) {
      const m = meta[k];
      if (m && m.quote === 'USDT') { includedLegs.push(k); usdt.push(k); }
      else excludedLegs.push(k); // non-USDT / unknown-quote — never rescaled onto the sum
    }
    // Grid owner: the flagged primary's tick, else the first USDT leg's tick.
    let gridTick = NaN;
    for (const k of Object.keys(meta)) {
      const m = meta[k];
      if (m && m.primary) { gridTick = posOr(m.tickSize, NaN); break; }
    }
    if (!Number.isFinite(gridTick)) {
      for (const k of usdt) { const t = meta[k] && posOr(meta[k].tickSize, NaN); if (Number.isFinite(t)) { gridTick = t; break; } }
    }

    const bidGrid = new Map(), askGrid = new Map();
    const accum = (grid, levels, up) => {
      for (const lvl of (Array.isArray(levels) ? levels : [])) {
        if (!lvl) continue;
        const p = Number(lvl[0]), q = Number(lvl[1]);
        if (!Number.isFinite(p) || !Number.isFinite(q) || q <= 0) continue;
        const gp = snapTick(p, gridTick, up);
        grid.set(gp, (grid.get(gp) || 0) + q);
      }
    };
    if (Number.isFinite(gridTick)) {
      for (const k of usdt) {
        const bk = books[k];
        if (!bk) continue;
        accum(bidGrid, bk.bids, false); // bids snap down
        accum(askGrid, bk.asks, true);  // asks snap up
      }
    }
    const bids = [...bidGrid.entries()].map(([p, q]) => [p, q]).sort((a, b) => b[0] - a[0]);
    const asks = [...askGrid.entries()].map(([p, q]) => [p, q]).sort((a, b) => a[0] - b[0]);
    return { book: { bids, asks }, includedLegs, excludedLegs, gridTick };
  }

  // ─── makePanelGuard({threshold}) — per-panel render circuit breaker (N1) ──
  //
  // Pure, DOM-free, clock-free companion to the paint loop's per-panel error
  // boundary (terminal.js safePanel). A closed→open circuit breaker (Nygard,
  // Release It!): N CONSECUTIVE render throws latch it OPEN ('dead'); one clean
  // run resets the consecutive count. Once open it STAYS open — a persistent
  // render fault is a real bug to SURFACE (the dead-panel chip), never a
  // catch-and-retry that spin-loops and hides the breakage (§N1). We omit
  // half-open/auto-retry ON PURPOSE: retry against a deterministic render fault
  // is the exact spin-loop the roadmap item warns against. Revives ONLY on
  // reset() (symbol-switch re-init rebuilds every store, so the fault's inputs
  // are gone — a reconnect or un-pause does NOT clear a code fault, so neither
  // does this). Counts events, never time, so it needs no clock and stays a
  // pure Node-testable unit.
  function makePanelGuard(opts) {
    const o = opts || {};
    // Clamp ≥ 1: a 0/NaN threshold would be a breaker that never opens — a
    // silent no-op guard, the opposite of the honesty rail. Default 3: one
    // transient NaN for a single frame should not quarantine a panel; three
    // consecutive throws is a reproducible fault, not a one-off.
    const N = Math.max(1, Math.floor(finiteOr(o.threshold, 3)));
    let consecutive = 0;   // consecutive throws since the last ok()/reset()
    let dead = false;      // latched open — surfaced, never auto-retried
    let failures = 0;      // TOTAL throws ever (diagnostics)
    let lastError = null;  // message of the most recent throw (chip tooltip)

    return {
      /** Clean run: the consecutive streak resets. Never revives a dead guard
       *  (a quarantined panel is not called, so ok() is not reached for it). */
      ok() { if (!dead) consecutive = 0; },

      /** Record a throw. Returns true ONLY on the false→true transition into
       *  dead — so the caller logs + flags EXACTLY once; false below threshold
       *  and false when already dead (rate-limit rail: no per-frame spam). */
      fail(err) {
        failures++;
        lastError = err && err.message ? String(err.message) : String(err);
        if (dead) return false;
        consecutive++;
        if (consecutive >= N) { dead = true; return true; }
        return false;
      },

      /** Latched open? The caller skips the whole panel block while true. */
      isDead() { return dead; },

      /** Explicit revival — symbol-switch re-init only (see header). */
      reset() { consecutive = 0; dead = false; lastError = null; },

      /** Read-only snapshot for the UI dead-chip + the harness. */
      stats() { return { dead, consecutive, failures, lastError, threshold: N }; },
    };
  }

  // ─── Export — ONE global + Node (quant.js dual-export pattern) ──────────

  const TerminalState = {
    TapeStore, BookStore, AggBookStore, FootprintStore, CvdStore, ProfileStore, LiqStore,
    // O-2 (§4b): heatmap history + labeled heuristic/model layers.
    DepthHistoryStore, SpoofIcebergDetector, LiqHeatmapModel,
    // O-3 (§4c): structure builders (pure functions over klines) + the
    // session-correlation store for history-less HIP-3 legs.
    buildTpo, buildKlineVp, rollingCorr, SessionSeriesStore,
    // O-4 (§4d): intelligence builders — descriptive reads/triggers, never
    // signals (IC-honesty label mandatory on the confluence output).
    buildScreener, confluenceReads, AlertEngine,
    // O-5 (§4e): journal stats/calendar/CSV over the user's OWN logged trades
    // — descriptive record, NOT a backtest (mandatory label rides the stats).
    journalStats, calendarReturns, journalToCsv, validateJournalCsv,
    // I-1 (§4f): Institutional Auction Suite builders — tick-exact auction
    // analytics; OFI/microprice carry their paper citations at the
    // implementation, heuristics carry label:'heuristic' on every event.
    buildDeltaProfile, SessionClock, AnchoredVwap, OfiStore, microprice,
    stackedImbalances, AbsorptionDetector, cumDelta,
    // T-1 (§4g): Trader's Edge stores — descriptive, convention-labeled;
    // VPIN carries its citations (and the contested-interpretation note) at
    // the implementation, the opening classifier its Dalton attribution.
    TapeIntensityStore, WallsLedger, VpinStore, OpeningTypeClassifier,
    BasisSeries, deriveVenueIds,
    // T-2 (§4h): the 7-leg matrix mapping — additive next to deriveVenueIds,
    // whose T-1 shape stays frozen for existing consumers — plus the leg
    // registry the terminal's matrix lifecycle consults before any socket.
    deriveLegIds, LegRegistry,
    // T-3 (§4i): Tape & Ladder Pro pure stores — aggregated-tape merge +
    // size-tier emphasis (conventions, not signals) + rolling volume-at-price,
    // and the pure DOM-ladder reads over a best-first book snapshot. The
    // aggregated-ladder builder carries the cross-venue display-approximation
    // caveat at its implementation (never a merged truth).
    TapeAggregator, sizeTier, SIZE_TIER_DEFAULTS, liqTier, LIQ_TIER_DEFAULTS,
    filterTapeRows, BigPrintRail, TradeImprint,
    ladderRows, depthImbalance, logBarWidth, mergeSameQuoteBooks,
    // N1: per-panel render circuit breaker (paint-loop error boundary) — pure,
    // DOM-free, clock-free; wired into terminal.js frame() via safePanel.
    makePanelGuard,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = TerminalState;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_STATE = TerminalState;
})(typeof globalThis !== 'undefined' ? globalThis : this);
