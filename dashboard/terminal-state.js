// terminal-state.js — orderflow terminal: pure in-memory stores (DESIGN-orderflow-terminal.md §4).
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
  //   - Bybit orderbook.50 sends a `snapshot` then `delta` frames where
  //     qty "0" DELETES a level (fixture bybit_orderbook_delta);
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

  // ─── Export — ONE global + Node (quant.js dual-export pattern) ──────────

  const TerminalState = {
    TapeStore, BookStore, AggBookStore, FootprintStore, CvdStore, ProfileStore, LiqStore,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = TerminalState;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_STATE = TerminalState;
})(typeof globalThis !== 'undefined' ? globalThis : this);
