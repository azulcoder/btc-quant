// terminal-state.js — orderflow terminal: pure in-memory stores (DESIGN-orderflow-terminal.md §4 + §4b).
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

  // ─── Export — ONE global + Node (quant.js dual-export pattern) ──────────

  const TerminalState = {
    TapeStore, BookStore, AggBookStore, FootprintStore, CvdStore, ProfileStore, LiqStore,
    // O-2 (§4b): heatmap history + labeled heuristic/model layers.
    DepthHistoryStore, SpoofIcebergDetector, LiqHeatmapModel,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = TerminalState;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_STATE = TerminalState;
})(typeof globalThis !== 'undefined' ? globalThis : this);
