// terminal-hist.js — pure REST fetchers + normalizers for the STRUCTURE views (O-3).
//
// DESIGN-orderflow-terminal.md §4c contract: every fetcher is a THIN wrapper
// (fetch + AbortController 10s + silent-null on failure) around a PURE
// normalize*(parsedJson) function — the normalizers are what the fixture smoke
// replays (scripts/fixtures_ws.json keys bybit_rest_kline / okx_rest_funding /
// okx_rest_oi, captured 2026-07-04), so they are coded against REAL responses,
// not remembered API docs.
//
// Empirical data map encoded here (DESIGN §4c, probed 2026-07-04):
//   - Bybit REST klines (linear BTCUSDT/ETHUSDT/PAXGUSDT) return the list
//     NEWEST-FIRST as [startMs,o,h,l,c,vol,turnover] STRING tuples — the gotcha:
//     reverse for chronological order before anything downstream sees the bars.
//   - OKX REST funding-rate / open-interest work keyless; funding interval is 8h
//     (derived from the response, never hardcoded blindly — see normalizeOkxFunding).
//   - Hyperliquid HIP-3 dexs (km:US500, km:GOLD, …) expose LIVE allMids ONLY —
//     candleSnapshot for HIP-3 returns empty/500 keyless → NO history. Macro
//     history legs therefore use PAXG (tokenized gold, Bybit klines) + ETH; HIP-3
//     legs get session-correlation from polled mids, labeled `session · n=…`.
//   - Main-universe HL "SPX" is the SPX6900 MEMECOIN (~$0.37), NOT the index —
//     normalizeHlMids filters to dex-prefixed keys BY CONSTRUCTION so it can
//     never leak into a macro panel.
//   - No CME feeds; stooq is NOT keyless-scriptable (JS challenge) — dropped,
//     stated. We render what public REST actually serves, nothing more (§0.2).
//
// Honesty rails (DESIGN §0): everything fetched here is LIVE-DESCRIPTIVE (§0.1)
// — klines feed charts/TPO/VP panels and NEVER a backtest or the OOS harness.
// No fabricated history (§0.7): a failed fetch returns null and the caller
// renders '—'; we never substitute, interpolate, or mix sources into one series
// without a per-source label (every §4c view carries its source label).
//
// No DOM access, no Date.now() (fetch timeouts use setTimeout, matching the
// makeBinanceRestPoller idiom in terminal-adapters.js), no globals beyond the
// ONE export — normalizers unit-testable in Node via the quant.js dual-export
// pattern (consumed by scripts/check_terminal.cjs).
'use strict';

(function (global) {
  // ─── Pure normalizers (fixture-replayed — the testable core) ────────────────

  /**
   * Bybit v5 REST kline → chronological [{ts,o,h,l,c,v}].
   *
   * Wire reality (fixture bybit_rest_kline): result.list is NEWEST-FIRST — each
   * row [startMs, open, high, low, close, volume, turnover] as STRINGS. We
   * reverse into chronological order (oldest → newest) because every consumer
   * (candlestick chart, SMA/Heikin-Ashi from quant.js, buildTpo/buildKlineVp,
   * bar replay) assumes time-ascending bars; feeding newest-first bars would
   * silently mirror every indicator. Number() everything — strings on the wire.
   * `turnover` (row[6]) is dropped: no §4c view consumes it.
   *
   * Tolerates a Bybit-side error (retCode !== 0) or malformed payload → null,
   * so the fetch wrapper's silent-null contract holds end-to-end.
   */
  function normalizeBybitKlines(json) {
    if (!json || json.retCode !== 0) return null;      // Bybit errors keep HTTP 200 — retCode is the real status
    const list = json.result && json.result.list;
    if (!Array.isArray(list)) return null;
    const out = [];
    // Iterate BACKWARDS instead of list.slice().reverse(): same chronological
    // result, and the fixture object stays unmutated (replays reuse it).
    for (let i = list.length - 1; i >= 0; i--) {
      const r = list[i];
      const ts = Number(r[0]), o = Number(r[1]), h = Number(r[2]),
            l = Number(r[3]), c = Number(r[4]), v = Number(r[5]);
      // Never emit a NaN bar (adapter rail): one malformed row is dropped, the
      // rest of the history survives.
      if (!Number.isFinite(ts) || !Number.isFinite(o) || !Number.isFinite(h) ||
          !Number.isFinite(l) || !Number.isFinite(c) || !Number.isFinite(v)) continue;
      out.push({ ts, o, h, l, c, v });
    }
    return out;
  }

  /**
   * OKX /public/funding-rate → { fundingRate, nextFundingTs, intervalH }.
   *
   * Naming gotcha (fixture okx_rest_funding): OKX `fundingTime` is the UPCOMING
   * settlement of the CURRENT rate — i.e. what a countdown targets — while
   * `nextFundingTime` is the settlement AFTER that. So nextFundingTs (the §4
   * mark-event vocabulary: "when does the displayed rate settle") maps to
   * `fundingTime`, and the interval is the fundingTime → nextFundingTime
   * spacing (fixture: 1783152000000 − 1783123200000 = 28800000 ms = 8 h).
   * Deriving intervalH from the response (fallback 8 when the spacing is
   * absent/degenerate) keeps the FundingArbView's annualization
   * (rate × 8760/intervalH, §4c) honest if OKX ever changes the interval —
   * hardcoding 8 would silently mis-annualize.
   */
  function normalizeOkxFunding(json) {
    if (!json || json.code !== '0') return null;       // OKX status code is a STRING '0'
    const row = Array.isArray(json.data) ? json.data[0] : null;
    if (!row) return null;
    const fundingRate = Number(row.fundingRate);
    const nextFundingTs = Number(row.fundingTime);     // upcoming settlement (see gotcha above)
    if (!Number.isFinite(fundingRate) || !Number.isFinite(nextFundingTs)) return null;
    const spacingMs = Number(row.nextFundingTime) - nextFundingTs;
    // Fallback 8 (§4c): the OKX BTC perp interval, used only when the response
    // doesn't carry a sane spacing (e.g. empty nextFundingTime near settlement).
    const intervalH = Number.isFinite(spacingMs) && spacingMs > 0 ? spacingMs / 3600000 : 8;
    return { fundingRate, nextFundingTs, intervalH };
  }

  /**
   * OKX /public/open-interest → { oi, oiUsd, ts }.
   *
   * UNIT RAIL (same §4b gotcha as the OKX WS leg): the response's raw `oi`
   * field is in CONTRACTS (BTC-USDT-SWAP ctVal = 0.01 BTC — fixtures
   * `_okx_ctval_note`); `oiCcy` is the COIN amount. We return oiCcy as `oi`
   * because the FundingArbView column is "OI coin+USD" (§4c) and the other
   * venues' OI events are coin-denominated — returning contracts would
   * overstate OKX OI 100× in the venue table.
   */
  function normalizeOkxOi(json) {
    if (!json || json.code !== '0') return null;       // OKX status code is a STRING '0'
    const row = Array.isArray(json.data) ? json.data[0] : null;
    if (!row) return null;
    const oi = Number(row.oiCcy);                      // COIN, not the contracts field (rail above)
    const oiUsd = Number(row.oiUsd);
    const ts = Number(row.ts);                         // wire ts is a numeric-string ms epoch
    if (!Number.isFinite(oi) || !Number.isFinite(ts)) return null;
    return { oi, oiUsd, ts };
  }

  /**
   * Hyperliquid allMids response → plain { name: Number(mid) }, FILTERED to
   * keys carrying the requested dex prefix (e.g. 'km' → only 'km:US500',
   * 'km:GOLD', …).
   *
   * SPX-MEMECOIN GUARD (DESIGN §4c, honesty-critical): the HL MAIN-universe
   * symbol "SPX" is the SPX6900 memecoin (~$0.37), NOT the S&P 500 index —
   * labeling it macro would put a memecoin in the macro strip. Real
   * index/commodity perps live on HIP-3 dexs under dex-prefixed names
   * ('km:US500', 'km:USTECH', 'km:GOLD', 'km:USOIL', 'xyz:XYZ100'). Filtering
   * to `dex + ':'`-prefixed keys BY CONSTRUCTION means un-prefixed
   * main-universe names like "SPX" can never pass this normalizer, whatever
   * the server happens to include in the response. Keys keep their full
   * prefixed name — the prefix IS the provenance label (§0.7).
   */
  function normalizeHlMids(json, dex) {
    if (!json || typeof json !== 'object' || Array.isArray(json)) return null;
    if (typeof dex !== 'string' || dex.length === 0) return null;   // no dex → no prefix → guard can't hold
    const prefix = dex + ':';
    const out = {};
    for (const name of Object.keys(json)) {
      if (name.indexOf(prefix) !== 0) continue;        // the guard: dex-prefixed keys ONLY
      const mid = Number(json[name]);                  // mids arrive as numeric strings
      if (!Number.isFinite(mid)) continue;             // never emit a NaN mid
      out[name] = mid;
    }
    return out;
  }

  // ─── Thin fetch wrappers (AbortController 10s, silent-null) ─────────────────

  /**
   * Shared GET/POST-JSON with a 10s abort (§4c). Throws on any failure — the
   * per-endpoint wrappers below catch and return null: a transient REST
   * failure is NOT a terminal state; the caller renders '—' and the next
   * poll/refresh retries (same rule as makeBinanceRestPoller). No fabricated
   * values, no retry storms.
   */
  async function getJSON(url, init) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 10000);
    try {
      const opts = Object.assign({ headers: { Accept: 'application/json' } }, init || {});
      opts.signal = ctrl.signal;
      const res = await fetch(url, opts);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } finally {
      clearTimeout(t);
    }
  }

  /** Bybit v5 klines (linear). interval per Bybit docs: '5','30','60','240','D' etc. */
  async function fetchBybitKlines(sym, interval, limit) {
    try {
      const j = await getJSON(
        'https://api.bybit.com/v5/market/kline?category=linear' +
        '&symbol=' + encodeURIComponent(sym) +
        '&interval=' + encodeURIComponent(interval) +
        '&limit=' + encodeURIComponent(limit)
      );
      return normalizeBybitKlines(j);   // retCode errors also fall through to null
    } catch (_) { return null; }        // transient REST failure ≠ terminal state — caller renders '—'
  }

  /** OKX current funding rate for one instId (e.g. 'BTC-USDT-SWAP'). */
  async function fetchOkxFunding(instId) {
    try {
      const j = await getJSON(
        'https://www.okx.com/api/v5/public/funding-rate?instId=' + encodeURIComponent(instId)
      );
      return normalizeOkxFunding(j);
    } catch (_) { return null; }        // transient REST failure ≠ terminal state — caller renders '—'
  }

  /** OKX open interest for one SWAP instId. */
  async function fetchOkxOi(instId) {
    try {
      const j = await getJSON(
        'https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=' + encodeURIComponent(instId)
      );
      return normalizeOkxOi(j);
    } catch (_) { return null; }        // transient REST failure ≠ terminal state — caller renders '—'
  }

  /**
   * Hyperliquid live mids for one HIP-3 dex (POST info {type:'allMids', dex}).
   * Live mids are ALL a HIP-3 dex offers keyless (§4c: candleSnapshot returns
   * empty/500 → no history) — callers build session-correlation from polls,
   * labeled `session · n=…`, never pretend these are historical series.
   */
  async function fetchHlMids(dex) {
    try {
      const j = await getJSON('https://api.hyperliquid.xyz/info', {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'allMids', dex: dex }),
      });
      return normalizeHlMids(j, dex);
    } catch (_) { return null; }        // transient REST failure ≠ terminal state — caller renders '—'
  }

  // ─── Export (ONE global + Node dual-export, quant.js pattern) ───────────────
  const HIST = {
    normalizeBybitKlines, normalizeOkxFunding, normalizeOkxOi, normalizeHlMids,
    fetchBybitKlines, fetchOkxFunding, fetchOkxOi, fetchHlMids,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = HIST;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_HIST = HIST;
})(typeof globalThis !== 'undefined' ? globalThis : this);
