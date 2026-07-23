// terminal-books.js — orderflow terminal: full-book sync engines + spot/perp CVD
// (DESIGN-orderflow-terminal.md §4h, T-2 Venue Matrix).
//
// RESEARCH / DESCRIPTIVE ONLY. Same rails as terminal-state.js (§0.1/§0.7):
// live-descriptive session state, never a backtest input, renders only what
// arrived over the wire. The single honesty rule this file exists to enforce:
// a local book whose continuity can no longer be PROVEN (sequence gap, broken
// pu chain, checksum mismatch) is CLEARED and counted as a resync — it is
// never silently patched, because a book with a known hole is fabricated data
// if it stays on screen (§4h "counted honest resync, never silent patching").
// (The checksum-mismatch mechanism is OkxBookSync's, DORMANT on the keyless
// wire — OKX sends checksum:0 there, measured §4h — so the LIVE okx leg proves
// continuity via the seqId/prevSeqId chain in the adapter, not here; see the
// OkxBookSync header. The live-wired engines in THIS file are Binance's two
// diff rails and Coinbase's staleness gate.)
//
// Contract (§4h): plain-script IIFE exposing ONE global, `BTCQ_TERMINAL_BOOKS`,
// standalone next to terminal-state.js (no dependency either way — the few
// tiny helpers shared in spirit are mirrored locally so both files stay plain
// scripts loadable in any order, and runnable under Node for the fixture
// smoke — quant.js dual-export trick).
//
// Perf contract (§4h, stated): every wire event costs O(1) Map set/delete per
// touched level. Sorted materialization (best()/topN()/checksum ranking) runs
// only when ASKED — i.e. at paint cadence or on a checksum-carrying frame —
// never per buffered message.
//
// Two rails inherited verbatim from terminal-state.js:
//   - ZERO DOM/window access; NO Date.now(). "Now" is the tape's now — every
//     method that needs a clock takes ts from the event (replay rail).
//   - Non-finite inputs are IGNORED, never coerced to 0 (a NaN qty silently
//     becoming an empty level would fabricate book state).
'use strict';

(function (global) {
  // ─── Shared helpers (mirrored from terminal-state.js — see header) ──────

  /** Finite-or-default: constructor params get sane fallbacks, never NaN state. */
  function finiteOr(x, dflt) {
    return Number.isFinite(x) ? x : dflt;
  }

  /** Positive-finite-or-default (ring capacities, sample spans). */
  function posOr(x, dflt) {
    return Number.isFinite(x) && x > 0 ? x : dflt;
  }

  /** Fixed-capacity ring buffer (O(1) push, oldest evicted), toArray()
   *  oldest→newest — terminal-state.js makeRing, mirrored. */
  function makeRing(max) {
    const cap = Math.max(1, Math.floor(finiteOr(max, 1000)));
    const buf = [];
    let start = 0;
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

  // ─── Book-side primitives ────────────────────────────────────────────────
  //
  // All three engines key their side Maps by the venue's own PRICE STRING,
  // not by Number(price). Float keys are a real bug class: a key derived from
  // float arithmetic drifts (61855.2 − 0.1 = 61855.099999…) and a "same"
  // level then exists twice, one of them undeletable by the venue's later
  // tombstone. Within one venue+market the snapshot and the diff stream come
  // from the SAME formatter (Binance fixes decimals per symbol filter, OKX
  // and Coinbase send canonical decimal strings), so string keys collide
  // exactly by construction — no arithmetic ever touches a key. For OKX the
  // string is additionally load-bearing: the venue checksum is defined over
  // its own decimal strings, and a Number round-trip would turn "8477.0"
  // into "8477" and break the CRC on a byte level.

  /** Apply [[price, qty], …] rows onto a string-keyed side map storing
   *  NUMERIC qty (Binance/Coinbase). Absolute replace; qty 0 deletes (the
   *  tombstone convention all three venues share); a NEGATIVE qty can only
   *  be corruption and deletes too — the conservative read (BookStore's
   *  q ≤ 0 rule). Non-finite price/qty rows are skipped, never zeroed. */
  function applyRowsNum(map, rows) {
    if (!Array.isArray(rows)) return;
    for (const r of rows) {
      if (!r) continue;
      const k = String(r[0]), q = Number(r[1]);
      if (!Number.isFinite(Number(k)) || !Number.isFinite(q)) continue;
      if (q <= 0) map.delete(k);
      else map.set(k, q);
    }
  }

  /** Same, but storing the WIRE qty string (OKX — the checksum needs the
   *  venue's own bytes, see the primitives note above). */
  function applyRowsStr(map, rows) {
    if (!Array.isArray(rows)) return;
    for (const r of rows) {
      if (!r) continue;
      const k = String(r[0]), qs = String(r[1]), q = Number(qs);
      if (!Number.isFinite(Number(k)) || !Number.isFinite(q)) continue;
      if (q <= 0) map.delete(k);
      else map.set(k, qs);
    }
  }

  /** One side's entries sorted best-first (bids: price DESC, asks ASC) —
   *  [priceStr, storedQty] pairs. O(L log L), paint-cadence only (§4h perf
   *  contract): per-event work never sorts. */
  function sortedSide(map, isBid) {
    const rows = [...map.entries()];
    rows.sort((a, b) => (isBid ? Number(b[0]) - Number(a[0]) : Number(a[0]) - Number(b[0])));
    return rows;
  }

  /** Best bid/ask as {bid:[px,qty]|null, ask:[px,qty]|null} — linear scan,
   *  no sort (BookStore.best precedent). */
  function bestOf(bids, asks) {
    let bb = null, ba = null;
    for (const [k, v] of bids) { const p = Number(k); if (!bb || p > bb[0]) bb = [p, Number(v)]; }
    for (const [k, v] of asks) { const p = Number(k); if (!ba || p < ba[0]) ba = [p, Number(v)]; }
    return { bid: bb, ask: ba };
  }

  /** Top-n numeric ladder {bids:[[px,qty],…] desc, asks:[[px,qty],…] asc} —
   *  the render-window materialization the views call at paint cadence. */
  function topNOf(bids, asks, n) {
    const lim = posOr(n, Infinity);
    const cut = (m, isBid) => sortedSide(m, isBid).slice(0, lim).map(([k, v]) => [Number(k), Number(v)]);
    return { bids: cut(bids, true), asks: cut(asks, false) };
  }

  // ─── CRC32 (dependency-free) — the OKX books checksum primitive (§4h) ────
  //
  // Standard reflected CRC-32 (polynomial 0xEDB88320, init 0xFFFFFFFF, final
  // xor 0xFFFFFFFF) — bit-identical to zlib.crc32. OKX compares the value as
  // a SIGNED int32, hence the `| 0` on return. charCodeAt is byte-exact here
  // because checksum strings are pure ASCII by construction (decimal digits,
  // '.', ':'); a general UTF-8 encoder is deliberately NOT carried.
  //
  // Pinned vectors, independently computed (do not trust our own table):
  //   python3 -c "import zlib; print(zlib.crc32(b'8476.98:415:8477:7:8475.55:100:8477.34:85'))"
  //     → 3025791351 unsigned ≡ -1269175945 signed int32
  //   python3 -c "import zlib; print(zlib.crc32(b'3366.1:7:3366.8:9:3368:8:3372:8'))"
  //     → 831078360 unsigned ≡ 831078360 signed int32
  // (check_terminal group "OKX checksum" asserts both.)
  const CRC_TABLE = (() => {
    const t = new Int32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c;
    }
    return t;
  })();

  function crc32(str) {
    let c = 0xFFFFFFFF;
    for (let i = 0; i < str.length; i++) {
      c = CRC_TABLE[(c ^ str.charCodeAt(i)) & 0xFF] ^ (c >>> 8);
    }
    return (c ^ 0xFFFFFFFF) | 0; // signed int32 — OKX's comparison domain
  }

  // ─── BinanceBookSync({mode}) — diff-depth local book, official algo (§4h) ─
  //
  // Binance's documented local-orderbook procedure: buffer diff events, fetch
  // a REST snapshot (depth?limit=1000 → lastUpdateId), drop buffered events
  // entirely covered by it, then apply the rest under the continuity rule.
  // The two markets verify continuity DIFFERENTLY — that is the whole reason
  // `mode` exists and the check group proves the rules are not interchangeable:
  //   - spot:    every applied event must satisfy U ≤ lastId+1 ≤ u
  //              (events may overlap what we hold; a gap is U > lastId+1);
  //   - futures: event.pu must equal the previously applied event's u exactly
  //              (pu chaining — the venue hands us the predecessor's id).
  // The FIRST event applied after any snapshot uses the bracketing rule in
  // both modes: a futures snapshot's lastUpdateId is not an event u, so no pu
  // can be expected to equal it — the official futures first-event rule
  // (U ≤ lastUpdateId ≤ u) is subsumed by the bracket, which additionally
  // admits the exactly-contiguous U = lastId+1 event (no data missing there).
  //
  // Any violation → state 'desync': the book is CLEARED and the resync
  // COUNTED (chip renders 'resync ×N'), never silently patched — see the file
  // header for why a known-gap book is fabricated data. Diffs arriving while
  // desynced re-buffer, so the caller's fresh snapshot resumes seamlessly.
  function BinanceBookSync(opts) {
    // Default 'spot' — stated, because the two continuity rules differ and a
    // silent wrong default would "work" until the first real gap.
    const mode = (opts && opts.mode) === 'futures' ? 'futures' : 'spot';
    const bids = new Map(), asks = new Map(); // priceStr → qty (Number)
    const BUF_MAX = 8192; // ~14 min of @100ms diffs — snapshot fetch is seconds
    let buffer = [];
    let state = 'buffering'; // 'buffering' | 'synced' | 'desync'
    let lastUpdateId = NaN;  // snapshot lastUpdateId, then every applied u
    let firstPending = false; // next applied event is the first after a snapshot
    let resyncCount = 0;

    /** Buffer a pre-snapshot / post-desync diff. Bounded: past BUF_MAX the
     *  oldest goes — if that lost event turns out to be needed to bracket the
     *  snapshot, the drain fails the continuity rule and counts an honest
     *  resync (never a silent hole). */
    function pushBuffer(ev) {
      buffer.push(ev);
      if (buffer.length > BUF_MAX) buffer.shift();
    }

    /** Apply one diff against the current lastUpdateId.
     *  Returns 'applied' | 'stale' (entirely covered by the snapshot — dropped
     *  per the official algo) | 'gap' (continuity violated → caller desyncs).
     *  Corrupt U/u are 'gap': continuity that cannot be VERIFIED is treated
     *  as broken, not assumed fine. */
    function applyDiff(ev) {
      const U = Number(ev.U), u = Number(ev.u);
      if (!Number.isFinite(U) || !Number.isFinite(u)) return 'gap';
      if (u <= lastUpdateId) return 'stale';
      if (firstPending || mode === 'spot') {
        // Bracket rule U ≤ lastId+1 ≤ u; the right half already holds (u > lastId).
        if (!(U <= lastUpdateId + 1)) return 'gap';
      } else if (Number(ev.pu) !== lastUpdateId) {
        return 'gap'; // futures: the chain must be exact, overlap is not enough
      }
      applyRowsNum(bids, ev.bids);
      applyRowsNum(asks, ev.asks);
      lastUpdateId = u;
      firstPending = false;
      return 'applied';
    }

    /** Honest resync entry: count, CLEAR (never patch), and keep `keep` —
     *  the violating event and anything after it may legitimately bracket the
     *  NEXT snapshot, so they seed the new buffer. */
    function enterDesync(keep) {
      state = 'desync';
      resyncCount++;
      bids.clear(); asks.clear();
      lastUpdateId = NaN;
      firstPending = false;
      buffer = keep.length > BUF_MAX ? keep.slice(keep.length - BUF_MAX) : keep;
    }

    /** Seed from a REST snapshot, then drain the buffer under the continuity
     *  rule. Buffered events with u ≤ lastUpdateId are dropped (covered);
     *  the first survivor must bracket the snapshot. A corrupt id changes
     *  nothing — the caller's fetch loop simply tries again. */
    function onSnapshot(snapLastUpdateId, sBids, sAsks) {
      const id = Number(snapLastUpdateId);
      if (!Number.isFinite(id)) return;
      bids.clear(); asks.clear();
      applyRowsNum(bids, sBids);
      applyRowsNum(asks, sAsks);
      lastUpdateId = id;
      firstPending = true;
      state = 'synced';
      const pend = buffer;
      buffer = [];
      for (let i = 0; i < pend.length; i++) {
        if (applyDiff(pend[i]) === 'gap') {
          enterDesync(pend.slice(i));
          return;
        }
      }
    }

    /** Ingest one diff event {U, u, pu?, bids, asks}. Before a snapshot (or
     *  after a desync) it buffers; when synced it applies or desyncs. */
    function onDiff(ev) {
      if (!ev) return;
      if (state !== 'synced') { pushBuffer(ev); return; }
      if (applyDiff(ev) === 'gap') enterDesync([ev]);
    }

    /** True whenever the caller must (re)fetch the REST snapshot. */
    function needsSnapshot() { return state !== 'synced'; }

    function best() { return bestOf(bids, asks); }
    function topN(n) { return topNOf(bids, asks, n); }

    return {
      onSnapshot, onDiff, needsSnapshot, best, topN,
      get state() { return state; },
      get resyncCount() { return resyncCount; },
      get lastUpdateId() { return lastUpdateId; },
      get bufferedCount() { return buffer.length; },
      get mode() { return mode; },
    };
  }

  // ─── OkxBookSync() — books-channel CRC32 verifier (DORMANT keyless, §4h) ──
  //
  // [SUPERSEDED 2026-07-24] WHY this engine is NOT the live OKX rail: it proves
  // continuity by the OKX CRC32 checksum, but on the KEYLESS public `books`
  // channel that checksum is degenerate. MEASURED on the real wire (179/179
  // BTC-USDT-SWAP `books` frames 2026-07-24, 300+ across SWAP/spot/ETH the day
  // before) EVERY frame carries `checksum: 0` — the venue only populates the
  // CRC on the login/VIP-gated `books-l2-tbt` tick-by-tick channel. So keyless
  // this engine has no input to verify against, and the live okx legs do NOT
  // use it: their continuity runs on the seqId/prevSeqId chain in
  // makeOkxBooksAdapter (terminal-adapters.js, §4h) — that chain, not the CRC,
  // IS the venue's ordering guarantee keyless. Retained DORMANT (dependency-
  // free crc32 + pinned zlib vectors) for the tbt channel should we ever
  // authenticate. Do NOT wire it to the keyless leg: fed a real `books` frame
  // its verify() fails on the checksum:0 and desyncs the book every frame.
  //
  // The CRC rail itself, for the authenticated tbt channel it is built for —
  // OKX `books` (400 levels): one snapshot, then absolute-qty updates (qty
  // "0" deletes), and — the reason this engine is simpler than Binance's —
  // EVERY frame carries a venue-computed checksum of the resulting top of
  // book, so continuity is verified cryptographically instead of by sequence
  // arithmetic. Verification per the OKX spec:
  //
  //   take the top-25 bids and top-25 asks of YOUR maintained book (fewer
  //   when fewer exist), interleave rank by rank as
  //   bid1px:bid1qty:ask1px:ask1qty:bid2px:bid2qty:…, ':'-joined, and CRC32
  //   the string; the result compared as a SIGNED int32.
  //
  // Worked example (both pinned in the crc32 header + check group):
  //   bids [["8476.98","415"],["8475.55","100"]], asks [["8477","7"],["8477.34","85"]]
  //     → "8476.98:415:8477:7:8475.55:100:8477.34:85" → crc32 −1269175945
  //   one bid vs three asks (a short side contributes what it has):
  //   bids [["3366.1","7"]], asks [["3366.8","9"],["3368","8"],["3372","8"]]
  //     → "3366.1:7:3366.8:9:3368:8:3372:8" → crc32 831078360
  //
  // The strings are the VENUE'S wire strings — stored verbatim as map values,
  // never reformatted (see the primitives note: Number round-trips break the
  // CRC bytes). Mismatch → counted resync + cleared book, the file-header
  // honesty rule; updates arriving while desynced are ignored (applying
  // deltas to a cleared book would fabricate levels) until a fresh snapshot.
  function OkxBookSync() {
    const bids = new Map(), asks = new Map(); // priceStr → qtyStr (wire strings)
    let state = 'awaiting'; // 'awaiting' | 'synced' | 'desync'
    let resyncCount = 0;

    /** The exact OKX interleave string over the CURRENT book (top-25 per
     *  side). Exposed for the check group's hand-computed comparison. */
    function checksumString() {
      const bs = sortedSide(bids, true);
      const as = sortedSide(asks, false);
      const parts = [];
      const depth = Math.min(25, Math.max(bs.length, as.length));
      for (let i = 0; i < depth; i++) {
        if (i < bs.length) parts.push(bs[i][0], bs[i][1]);
        if (i < as.length) parts.push(as[i][0], as[i][1]);
      }
      return parts.join(':');
    }

    /** Our signed-int32 CRC of the current book. */
    function checksum() { return crc32(checksumString()); }

    /** Does the venue's checksum match our book? A missing/non-numeric
     *  checksum FAILS: an unverifiable book is an unverified book — the §4h
     *  rail is that every update proves itself. */
    function verify(want) {
      const w = Number(want);
      if (!Number.isFinite(w)) return false;
      return checksum() === (w | 0);
    }

    function enterDesync() {
      state = 'desync';
      resyncCount++;
      bids.clear(); asks.clear(); // cleared, never silently patched (header rule)
    }

    /** Seed/replace from a `books` snapshot. OKX sends a checksum on the
     *  snapshot too — when present it must verify (a snapshot failing its own
     *  checksum is corrupt, same rail as an update); absent → trusted as the
     *  fresh baseline, per spec. Rows may carry the venue's extra columns
     *  ([px, qty, liqOrders, numOrders]) — only [0]/[1] are book state. */
    function onSnapshot(sBids, sAsks, cs) {
      bids.clear(); asks.clear();
      applyRowsStr(bids, sBids);
      applyRowsStr(asks, sAsks);
      if (cs !== undefined && cs !== null && !verify(cs)) { enterDesync(); return; }
      state = 'synced';
    }

    /** Apply one update (absolute qty, "0" deletes), then verify the venue
     *  checksum against the RESULT — every update, no exceptions (§4h). */
    function onUpdate(uBids, uAsks, cs) {
      if (state !== 'synced') return; // see header: no deltas onto a cleared book
      applyRowsStr(bids, uBids);
      applyRowsStr(asks, uAsks);
      if (!verify(cs)) enterDesync();
    }

    function needsSnapshot() { return state !== 'synced'; }
    function best() { return bestOf(bids, asks); }
    function topN(n) { return topNOf(bids, asks, n); }

    return {
      onSnapshot, onUpdate, needsSnapshot, best, topN, checksumString, checksum,
      get state() { return state; },
      get resyncCount() { return resyncCount; },
    };
  }

  // ─── CoinbaseBookSync() — level2_batch full snapshot + l2update (§4h) ────
  //
  // Coinbase level2_batch (keyless): a FULL snapshot (~44k levels measured on
  // this network, §4h table) then batched l2update changes [side, price, qty]
  // with ABSOLUTE qty (qty "0" removes the level).
  //
  // Stated limitation: the channel carries NO sequence number — there is no
  // U/u arithmetic and no checksum to verify continuity against, so a dropped
  // frame is UNDETECTABLE from the stream itself. The only rail is that a
  // reconnect always delivers a fresh full snapshot; lastUpdateTs (event/
  // arrival ts, supplied by the caller — no Date.now() here) is tracked so
  // the caller can staleness-gate and force that reconnect. This engine
  // therefore has no desync state to count: honesty lives in the gate.
  function CoinbaseBookSync() {
    const bids = new Map(), asks = new Map(); // priceStr → qty (Number)
    let lastUpdateTs = NaN;

    /** Replace the book wholesale from a snapshot's bids/asks arrays. */
    function onSnapshot(sBids, sAsks, ts) {
      bids.clear(); asks.clear();
      applyRowsNum(bids, sBids);
      applyRowsNum(asks, sAsks);
      if (Number.isFinite(ts)) lastUpdateTs = ts;
    }

    /** Apply one l2update's changes [[side, price, qty], …]. A corrupt side
     *  tag skips the row (never guessed onto a side). ts advances on any
     *  frame with a finite ts even if every row was corrupt — the staleness
     *  gate measures channel liveness, not row quality. */
    function onL2Update(changes, ts) {
      if (Array.isArray(changes)) {
        for (const ch of changes) {
          if (!ch) continue;
          const side = ch[0] === 'buy' ? bids : ch[0] === 'sell' ? asks : null;
          if (!side) continue;
          const k = String(ch[1]), q = Number(ch[2]);
          if (!Number.isFinite(Number(k)) || !Number.isFinite(q)) continue;
          if (q <= 0) side.delete(k);
          else side.set(k, q);
        }
      }
      if (Number.isFinite(ts)) lastUpdateTs = ts;
    }

    function best() { return bestOf(bids, asks); }
    function topN(n) { return topNOf(bids, asks, n); }

    return {
      onSnapshot, onL2Update, best, topN,
      get lastUpdateTs() { return lastUpdateTs; },
    };
  }

  // ─── SpotPerpCvdStore({sampleMs, max}) — spot vs perp CVD strip (§4h) ────
  //
  // Two session-anchored cumulative series in USD notional: Σ signed flow of
  // the enabled PERP legs vs Σ of the enabled SPOT legs (the caller pushes
  // only enabled legs — a disabled leg's past contribution stays in the sums,
  // honestly: the series is "since page open", CvdStore's no-natural-zero
  // rule). PURE ACCUMULATION, NO SMOOTHING: this is a descriptive lead/lag
  // read — "which side of the market moved first this session" — and is
  // labeled so on the panel; it is NOT a signal and nothing here scores it.
  //
  // Ring of completed per-10s samples (~3600 ≈ 10 h) keyed by bucket-start
  // ts; buckets close on EVENT TIME only and a bucket with zero pushes never
  // exists (TapeIntensityStore's rule — gaps are gaps, §0.7). Cross-venue ts
  // interleave is routine, so a push OLDER than the open bucket still
  // ACCUMULATES (dropping real flow would bias the perp/spot comparison) —
  // only the sampling clock is monotone.
  function SpotPerpCvdStore(opts) {
    const o = opts || {};
    const sampleMs = posOr(o.sampleMs, 10000);
    const ring = makeRing(posOr(o.max, 3600));
    let cvdPerp = 0, cvdSpot = 0;
    const byLeg = {}; // cumulative signed notional per leg key — panel legend
    let bStart = NaN, lastTs = NaN;

    /** Ingest one signed notional delta (qty·price, + for aggressive buy).
     *  Zero deltas carry no flow information and are dropped (validTrade
     *  precedent); a push without a leg identity is malformed and dropped. */
    function push(ts, legKey, isPerp, notional) {
      if (!Number.isFinite(ts) || !Number.isFinite(notional) || notional === 0) return;
      if (typeof legKey !== 'string' || !legKey) return;
      if (!Number.isFinite(bStart)) {
        bStart = Math.floor(ts / sampleMs) * sampleMs;
      } else if (ts >= bStart + sampleMs) {
        // Close the open bucket at its pre-push cums, then jump — no zero
        // samples are synthesized for empty buckets in between.
        ring.push({ ts: bStart, cvdPerp, cvdSpot });
        bStart = Math.floor(ts / sampleMs) * sampleMs;
      }
      if (isPerp) cvdPerp += notional;
      else cvdSpot += notional;
      byLeg[legKey] = (byLeg[legKey] || 0) + notional;
      if (!(lastTs >= ts)) lastTs = ts; // NaN-safe max — interleave keeps it monotone
    }

    /** Live cumulative read {ts, cvdPerp, cvdSpot}, or null before any push
     *  (never a fabricated zero row — BasisSeries convention). */
    function latest() {
      return Number.isFinite(lastTs) ? { ts: lastTs, cvdPerp, cvdSpot } : null;
    }

    /** Completed per-bucket samples, oldest→newest, ≤max — the strip feed. */
    function list() { return ring.toArray(); }

    return { push, latest, list, byLeg, get length() { return ring.length; } };
  }

  // ─── Export — ONE global + Node (quant.js dual-export pattern) ──────────

  const TerminalBooks = {
    // T-2 (§4h): full-book sync engines — each proves its own continuity
    // (sequence bracket / pu chain / venue CRC32 — the last DORMANT keyless,
    // OkxBookSync header) and counts honest resyncs; crc32 exported so the
    // check can pin it against zlib independently.
    BinanceBookSync, OkxBookSync, CoinbaseBookSync, SpotPerpCvdStore, crc32,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = TerminalBooks;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_BOOKS = TerminalBooks;
})(typeof globalThis !== 'undefined' ? globalThis : this);
