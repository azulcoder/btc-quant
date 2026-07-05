// terminal-state.js — orderflow terminal: pure in-memory stores + structure builders
// (DESIGN-orderflow-terminal.md §4 + §4b + §4c + §4d).
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
     *  every contributing leg's top-n and no byEx quantity is lost. */
    function grouped(tickSize, nLevels) {
      const n = finiteOr(nLevels, Infinity);
      const merge = (isBid) => {
        const acc = new Map(); // price → {total, byEx}
        for (const [ex, book] of books) {
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
      return {
        t: bar.t, o: bar.o, h: bar.h, l: bar.l, c: bar.c,
        buyVol: bar.buyVol, sellVol: bar.sellVol,
        delta: bar.buyVol - bar.sellVol,
        totalVol: bar.buyVol + bar.sellVol,
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
      if (t.aggressorBuy) { cell.buy += t.qty; cur.buyVol += t.qty; }
      else { cell.sell += t.qty; cur.sellVol += t.qty; }
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
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = TerminalState;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_STATE = TerminalState;
})(typeof globalThis !== 'undefined' ? globalThis : this);
