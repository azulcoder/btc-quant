// terminal-hist.js — pure REST fetchers + normalizers for the STRUCTURE views
// (O-3, §4c) and the INTELLIGENCE views (O-4, §4d).
//
// DESIGN-orderflow-terminal.md §4c/§4d contract: every fetcher is a THIN
// wrapper (fetch + AbortController 10s — 30s for the one 33 MB endpoint — +
// silent-null on failure) around a PURE normalize*(parsedJson) function — the
// normalizers are what the fixture smoke replays (scripts/fixtures_ws.json
// keys bybit_rest_kline / okx_rest_funding / okx_rest_oi captured 2026-07-04;
// bybit_rest_tickers / deribit_rest_book_summary / deribit_rest_dvol /
// hl_leaderboard_sample / hl_clearinghouse_state captured 2026-07-05), so they
// are coded against REAL responses, not remembered API docs.
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
// O-4 empirical data map (DESIGN §4d, probed 2026-07-05, fixtures _o4_notes):
//   - Deribit REST is CORS-OPEN to browser origins (verified: the
//     access-control-allow-origin header echoes) — the chain/DVOL fetchers run
//     straight from the page, no proxy. DVOL comes from
//     get_index_price?index_name=btcdvol_usdc (the 'BTC-DVOL' ticker
//     instrument is INVALID — probed, not assumed).
//   - Deribit `mark_iv` is in PERCENT → /100 before any vol formula
//     (DEVELOPMENT.md §5 — "forgetting this is a silent 100× bug").
//   - Bybit `tickers?category=linear` returns ~720 symbols in ONE call, with
//     `fundingIntervalHour` RESPONSE-PROVIDED per symbol and a 24h-VWAP proxy
//     = turnover24h/volume24h (label '24h VWAP' — it is a proxy, not a
//     tick-accurate VWAP).
//   - HL leaderboard = 33 MB / ~40k rows — CALLERS gate the fetch behind an
//     explicit user click (WhaleView's "discover top traders" button states
//     the size); after the one-shot load, only light per-address
//     clearinghouseState polls remain.
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

  // ─── O-4 pure normalizers (§4d — fixture-replayed like the O-3 set) ─────────

  /**
   * Number() with the empty-string trap closed: Number('') and Number(null)
   * are 0, NOT NaN — so a pre-listing Bybit ticker row shipping lastPrice ''
   * (the fixture's basisRate/preOpenPrice fields show '' is how Bybit spells
   * "absent") would survive the NaN-row drop as a fake price-0 symbol, and a
   * blank HL entryPx would render as entry $0. Blank on the wire means
   * ABSENT, and absent must never become a plottable 0 (§0.7).
   */
  function num(v) {
    return v === '' || v === null || v === undefined ? NaN : Number(v);
  }

  /**
   * Bybit v5 REST tickers (category=linear, ~720 symbols in ONE call) →
   * screener rows.
   *
   * Wire reality (fixture bybit_rest_tickers): every numeric field is a
   * STRING → Number() everything; a row with a non-finite core is DROPPED
   * (a screener bubble with NaN axes renders at the origin and lies).
   *
   * - vwap24h = turnover24h / volume24h. This is a PROXY (label '24h VWAP'
   *   in ScreenerView): the true VWAP needs per-trade data; the 24h turnover ÷
   *   24h volume ratio is the volume-weighted mean price of the window, which
   *   is exactly the VWAP definition at bar granularity zero — but it is
   *   response-derived, not tick-accumulated, so it stays labeled. null when
   *   volume24h is 0 (new/dead listing) — never a fabricated 0/0.
   * - fundingIntervalH = Number(fundingIntervalHour): RESPONSE-PROVIDED per
   *   symbol (§4d — this beats the O-3 fallback-8 constant used when a venue
   *   response carries no interval; some Bybit alts fund every 4h or 1h and a
   *   blanket 8 would mis-annualize them 2–8×). Fallback 8 only when the
   *   field is absent/degenerate.
   * - annualizedFundingPct = fundingRate × (8760 / intervalH) × 100 — same
   *   annualization as FundingArbView (§4c).
   */
  function normalizeBybitTickers(json) {
    if (!json || json.retCode !== 0) return null;      // Bybit errors keep HTTP 200 — retCode is the real status
    const list = json.result && json.result.list;
    if (!Array.isArray(list)) return null;
    const out = [];
    for (const r of list) {
      if (!r || typeof r.symbol !== 'string' || r.symbol === '') continue;
      const last = num(r.lastPrice);
      const turnover24h = num(r.turnover24h);
      const volume24h = num(r.volume24h);
      const pct24h = num(r.price24hPcnt) * 100;        // wire is a fraction (0.01536 = +1.536%)
      const fundingRate = num(r.fundingRate);
      const oiUsd = num(r.openInterestValue);
      const mark = num(r.markPrice);
      const index = num(r.indexPrice);
      // NaN rows dropped (pre-listing symbols ship empty strings): every
      // screener axis/encoding — x=pct24h, y=vwapDev, r=turnover, color=
      // funding — must be plottable, and mark/index/oiUsd feed the hover
      // readout. One bad symbol dies; the other ~719 survive.
      if (!Number.isFinite(last) || !Number.isFinite(turnover24h) ||
          !Number.isFinite(volume24h) || !Number.isFinite(pct24h) ||
          !Number.isFinite(fundingRate) || !Number.isFinite(oiUsd) ||
          !Number.isFinite(mark) || !Number.isFinite(index)) continue;
      const fihRaw = num(r.fundingIntervalHour);
      const fundingIntervalH = Number.isFinite(fihRaw) && fihRaw > 0 ? fihRaw : 8;
      const vwap24h = volume24h > 0 ? turnover24h / volume24h : null;   // no volume → no VWAP, never 0/0
      const vwapDevPct = vwap24h !== null && vwap24h > 0
        ? ((last - vwap24h) / vwap24h) * 100
        : null;                                        // dev is undefined without a VWAP — null, not 0 (flat lies)
      const annualizedFundingPct = fundingRate * (8760 / fundingIntervalH) * 100;
      out.push({
        sym: r.symbol, last, vwap24h, vwapDevPct, pct24h, turnover24h,
        fundingRate, fundingIntervalH, annualizedFundingPct, oiUsd, mark, index,
      });
    }
    return out;
  }

  // Deribit option-name month tokens (mirrors app.js parseInstrument — that
  // parser lives on the analytics page's script and terminal.html never loads
  // app.js, so the terminal carries its own copy of the 12-entry table).
  const DERIBIT_MONTHS = {
    JAN: 1, FEB: 2, MAR: 3, APR: 4, MAY: 5, JUN: 6,
    JUL: 7, AUG: 8, SEP: 9, OCT: 10, NOV: 11, DEC: 12,
  };

  /**
   * Parse a Deribit option instrument name `CCY-DDMMMYY-STRIKE-C|P` (e.g.
   * 'BTC-28AUG26-105000-C', and single-digit days like 'BTC-6JUL26-54000-P')
   * → { expiryTs, strike, cp } | null.
   *
   * expiryTs = 08:00 UTC on the contract date — the Deribit convention
   * (European cash-settled options expire 08:00 UTC; same rule app.js uses).
   * Pure calendar math via Date.UTC on PARSED fields — no Date.now(), so the
   * normalizer stays replay-deterministic.
   */
  function parseDeribitOptionName(name) {
    const parts = String(name).split('-');
    if (parts.length !== 4) return null;               // futures ('BTC-25SEP26') and spot pairs fall out here
    const dateTok = parts[1];
    const cp = parts[3].toUpperCase();
    if (cp !== 'C' && cp !== 'P') return null;
    if (dateTok.length < 6) return null;               // D MMM YY needs ≥6 chars
    const month = DERIBIT_MONTHS[dateTok.slice(-5, -2).toUpperCase()];
    if (!month) return null;
    const day = parseInt(dateTok.slice(0, dateTok.length - 5), 10);
    const year = 2000 + parseInt(dateTok.slice(-2), 10);
    const strike = parseFloat(parts[2]);
    if (!Number.isFinite(day) || !Number.isFinite(year) || !Number.isFinite(strike)) return null;
    const expiryTs = Date.UTC(year, month - 1, day, 8, 0, 0);   // 08:00 UTC expiry (Deribit convention)
    if (!Number.isFinite(expiryTs)) return null;
    return { expiryTs, strike, cp };
  }

  /**
   * Deribit get_book_summary_by_currency (kind=option) → { rows, skipped }.
   *
   * IV PERCENT TRAP (DEVELOPMENT.md §5): `mark_iv` arrives in PERCENT
   * (fixture: 48.58 for BTC-28AUG26-105000-C) — divide by 100 to the decimal
   * every vol formula (quant.js black76Greeks) expects. Forgetting this is a
   * silent 100× bug; the fixture smoke pins iv === mark_iv/100 exactly.
   * A non-finite mark_iv becomes iv NaN but the row is KEPT: PCR and max pain
   * consume oi/volume and need no iv, so dropping the row would silently bias
   * those; the GEX/smile consumers filter non-finite iv themselves (same
   * choice app.js made).
   *
   * Rows with UNPARSEABLE names are skipped and COUNTED (`skipped`) — the
   * OptionsView surfaces the count instead of silently shrinking the chain
   * (§0: state what the wire delivered, including what we could not read).
   * This endpoint has mark_iv only, no greeks (DEVELOPMENT §5) — greeks are
   * client-side Black-76; the panel stays 'mark-only chain' + unsigned GEX
   * (§0.5: dealer sign unknowable keyless).
   */
  function normalizeDeribitChain(json) {
    const list = json && json.result;                  // JSON-RPC errors carry `error` and no result array
    if (!Array.isArray(list)) return null;
    const rows = [];
    let skipped = 0;
    for (const r of list) {
      const p = r ? parseDeribitOptionName(r.instrument_name) : null;
      if (!p) { skipped++; continue; }                 // counted, not silently dropped (see docstring)
      const markIv = num(r.mark_iv);
      rows.push({
        name: r.instrument_name,
        expiryTs: p.expiryTs,
        strike: p.strike,
        cp: p.cp,                                      // 'C' | 'P'
        iv: Number.isFinite(markIv) ? markIv / 100 : NaN,   // PERCENT → decimal (the /100!)
        oi: num(r.open_interest),                      // contracts (BTC) — PCR-by-OI + GEX weight
        volume: num(r.volume),                         // 24h contracts — PCR-by-volume
        markPrice: num(r.mark_price),                  // in BTC (Deribit quotes options in coin)
        underlying: num(r.underlying_price),           // per-expiry synthetic future = Black-76 F
      });
    }
    return { rows, skipped };
  }

  /**
   * Deribit get_index_price (btcdvol_usdc) → Number | null. DVOL is Deribit's
   * 30-day BTC implied-vol index in VOL POINTS (fixture: 38.68 ≈ 38.68% ann.)
   * — displayed as-is in the OptionsView stat, never fed to a vol formula
   * (that is what per-strike iv is for).
   */
  function normalizeDeribitDvol(json) {
    const v = json && json.result ? num(json.result.index_price) : NaN;
    return Number.isFinite(v) ? v : null;
  }

  /**
   * HL leaderboard (33 MB — see fetchHlLeaderboard) → seed lists for the
   * whale watchlist: { topByValue: [{addr, acctVal}], topByRoi30d: [{addr,
   * roi, pnl}] }, each capped at n (default 10, §4d "seeds top-10 + top-10").
   *
   * Wire shape (fixture hl_leaderboard_sample): rows carry `ethAddress`,
   * `accountValue` (string USD) and `windowPerformances` as an ARRAY OF PAIRS
   * [[window, {pnl, roi, vlm}], …] — NOT an object. Inspected fixture window
   * keys: 'day' | 'week' | 'month' | 'allTime'; 'month' is the 30d window the
   * §4d ROI ranking wants (there is no literal '30d' key).
   *
   * ROI ranking excludes accounts with acctVal < $10k: dust accounts distort
   * ROI (a $50 account that lucked into 40× outranks every real book while
   * being unfollowable); the VALUE ranking keeps everyone — size is size.
   */
  function normalizeHlLeaderboard(json, n) {
    const topN = Number.isFinite(n) && n > 0 ? n : 10;
    const list = json && json.leaderboardRows;
    if (!Array.isArray(list)) return null;
    const rows = [];
    for (const r of list) {
      if (!r || typeof r.ethAddress !== 'string' || r.ethAddress === '') continue;
      const acctVal = num(r.accountValue);
      if (!Number.isFinite(acctVal)) continue;         // never rank a NaN book
      let month = null;                                // the 30d window ({pnl, roi} strings)
      if (Array.isArray(r.windowPerformances)) {
        for (const pair of r.windowPerformances) {
          if (Array.isArray(pair) && pair[0] === 'month' && pair[1]) { month = pair[1]; break; }
        }
      }
      rows.push({
        addr: r.ethAddress,
        acctVal,
        roi: month ? num(month.roi) : NaN,
        pnl: month ? num(month.pnl) : NaN,
      });
    }
    const topByValue = rows.slice()                    // slice(): the two sorts must not fight over one array
      .sort((a, b) => b.acctVal - a.acctVal)
      .slice(0, topN)
      .map((r) => ({ addr: r.addr, acctVal: r.acctVal }));
    const topByRoi30d = rows
      .filter((r) => r.acctVal >= 10000 && Number.isFinite(r.roi))   // dust filter (see docstring) + no NaN ranks
      .sort((a, b) => b.roi - a.roi)
      .slice(0, topN)
      .map((r) => ({ addr: r.addr, roi: r.roi, pnl: r.pnl }));
    return { topByValue, topByRoi30d };
  }

  /**
   * HL clearinghouseState → [{coin, szi, side, entryPx, posValue, uPnl,
   * leverage}] from assetPositions[].position — the WhaleView row shape.
   * These are PUBLIC on-chain facts (§4d rail: facts, not signals).
   *
   * Wire shapes (fixture hl_clearinghouse_state):
   * - `szi` is a SIGNED string coin size — sign IS the direction (fixture
   *   holds longs only; a short is a negative szi). side = szi > 0 ? long
   *   : short; zero-size rows are dropped (no position, no direction).
   * - `leverage` is an OBJECT { type: 'cross'|'isolated', value: 17 } — take
   *   .value (inspected fixture); tolerate a bare number in case the isolated
   *   shape differs; null when absent (render '—', never a fake 1×).
   */
  function normalizeHlPositions(json) {
    const list = json && json.assetPositions;
    if (!Array.isArray(list)) return null;
    const out = [];
    for (const ap of list) {
      const p = ap && ap.position;
      if (!p || typeof p.coin !== 'string' || p.coin === '') continue;
      const szi = num(p.szi);
      const entryPx = num(p.entryPx);
      // szi/entryPx are the row's identity — non-finite (or flat-zero szi)
      // rows are dropped; posValue/uPnl pass through num()ed and the view
      // renders '—' on a NaN rather than hiding a real position.
      if (!Number.isFinite(szi) || szi === 0 || !Number.isFinite(entryPx)) continue;
      const levRaw = p.leverage && typeof p.leverage === 'object' ? p.leverage.value : p.leverage;
      const leverage = Number.isFinite(num(levRaw)) ? num(levRaw) : null;
      out.push({
        coin: p.coin,
        szi,
        side: szi > 0 ? 'long' : 'short',              // the szi sign IS the direction
        entryPx,
        posValue: num(p.positionValue),
        uPnl: num(p.unrealizedPnl),
        leverage,
      });
    }
    return out;
  }

  // ─── O-5 pure normalizers (§4e — fixture-replayed like the O-3/O-4 sets) ────
  //
  // O-5 empirical data map (DESIGN §4e, probed 2026-07-05, fixtures _o5_notes):
  //   - Polymarket gamma REST is CORS `*`. The WORKING route is
  //     /events?tag_slug=bitcoin (events carry nested markets[]) —
  //     `markets?search=` and `markets?tag_slug=` both IGNORE their filters
  //     (verified: they return FIFA/Rihanna markets), so the markets routes
  //     are unusable for a BTC panel. `outcomePrices` arrives as a STRING
  //     containing a JSON-encoded array of STRINGS ("[\"0.9995\", \"0.0005\"]")
  //     — decode twice or render garbage.
  //   - Tree of Alpha REST is CORS `*` (/api/news?limit=N); rows carry
  //     _id/title/source/time/symbols/url.
  //   - faireconomy econ JSON has NO CORS header → the browser CANNOT fetch it
  //     directly. Design: scripts/fetch_econ.py (stdlib) writes
  //     dashboard/econ_calendar.json (gitignored, same-origin) via `make econ`;
  //     fetchEconLocal() reads THAT local file and the panel shows a fetch-age
  //     stamp + a "run `make econ`" note when it is absent/stale.

  /**
   * Polymarket /events?tag_slug=bitcoin → [{title, endTs, vol24h,
   * markets:[{question, yesPct, vol24h}]}].
   *
   * THE §4e STRING TRAP: market.outcomePrices is a STRING holding a
   * JSON-encoded array of STRINGS — yesPct = Number(JSON.parse(str)[0]) × 100,
   * a plain 0–100 Number the view can bar-render. A market whose prices
   * don't decode is SKIPPED (never a guessed 50%); closed markets are skipped
   * (their "price" is a settlement artifact, not a live crowd read).
   *
   * RAIL (§4e): these are CROWD-IMPLIED PROBABILITIES — the PolymarketView
   * labels them so; nothing here is a forecast endorsement or a signal.
   */
  function normalizePolymarketEvents(json) {
    if (!Array.isArray(json)) return null;             // the /events route returns a bare array
    const out = [];
    for (const ev of json) {
      if (!ev || typeof ev.title !== 'string' || ev.title === '') continue;
      const markets = [];
      for (const m of Array.isArray(ev.markets) ? ev.markets : []) {
        if (!m || typeof m.question !== 'string' || m.question === '') continue;
        if (m.closed === true) continue;               // settled — not a live crowd read
        let prices = null;
        try { prices = JSON.parse(m.outcomePrices); } catch (_) { prices = null; }
        if (!Array.isArray(prices) || !prices.length) continue;   // undecodable → skipped, never guessed
        const yes = Number(prices[0]);                 // outcome[0] is 'Yes' on these binary markets
        if (!Number.isFinite(yes)) continue;
        markets.push({ question: m.question, yesPct: yes * 100, vol24h: num(m.volume24hr) });
      }
      if (!markets.length) continue;                   // an event with no readable market renders nothing
      out.push({
        title: ev.title,
        endTs: Date.parse(ev.endDate),                 // NaN when absent — the countdown renders '—'
        vol24h: num(ev.volume24hr),
        markets,
      });
    }
    return out;
  }

  /**
   * Tree of Alpha /api/news → [{ts, title, source, url, symbols}], newest
   * first. `source` is the transport/category ('Twitter', 'Blogs', …); rows
   * without a finite ts or a title are dropped (an undatable headline can't
   * be ordered honestly). RAIL (§4e): a CONTEXT FEED — the NewsView labels
   * it; headlines are descriptive context, never tradeable information.
   */
  function normalizeToaNews(json) {
    if (!Array.isArray(json)) return null;
    const out = [];
    for (const r of json) {
      if (!r || typeof r.title !== 'string' || r.title === '') continue;
      const ts = Number(r.time);
      if (!Number.isFinite(ts)) continue;
      out.push({
        ts,
        title: r.title,
        source: typeof r.source === 'string' ? r.source : '',
        url: typeof r.url === 'string' ? r.url : '',
        symbols: Array.isArray(r.symbols) ? r.symbols.filter((s) => typeof s === 'string') : [],
      });
    }
    out.sort((a, b) => b.ts - a.ts);                   // newest first — deterministic whatever the wire order
    return out;
  }

  /**
   * Local dashboard/econ_calendar.json (written by scripts/fetch_econ.py —
   * §4e: faireconomy has NO CORS, so the browser reads this same-origin
   * mirror instead) → { fetchedTs, events:[{ts, title, country, impact,
   * forecast, previous}] }, events sorted ASCENDING by ts (the panel is an
   * upcoming-events list).
   *
   * fetchedTs passes through UNCHANGED — it is the fetch-age stamp the
   * EconView must show (a calendar of unknown age is a stale-data trap).
   * Rows whose `date` doesn't parse are dropped (an undatable event can't be
   * counted down to); forecast/previous stay strings ('' = the source had
   * none — speeches carry no forecast; '' renders '—', never a fake 0).
   */
  function normalizeEconLocal(json) {
    if (!json || typeof json !== 'object' || !Array.isArray(json.events)) return null;
    const events = [];
    for (const r of json.events) {
      if (!r || typeof r.title !== 'string' || r.title === '') continue;
      const ts = Date.parse(r.date);                   // ff dates carry their own offset ('2026-06-28T08:15:00-04:00')
      if (!Number.isFinite(ts)) continue;
      events.push({
        ts,
        title: r.title,
        country: typeof r.country === 'string' ? r.country : '',
        impact: typeof r.impact === 'string' ? r.impact : '',
        forecast: typeof r.forecast === 'string' ? r.forecast : '',
        previous: typeof r.previous === 'string' ? r.previous : '',
      });
    }
    events.sort((a, b) => a.ts - b.ts);                // ascending — the panel reads top-down toward the future
    return { fetchedTs: json.fetchedTs, events };
  }

  // ─── Thin fetch wrappers (AbortController 10s, silent-null) ─────────────────

  /**
   * Shared GET/POST-JSON with a 10s abort (§4c). Throws on any failure — the
   * per-endpoint wrappers below catch and return null: a transient REST
   * failure is NOT a terminal state; the caller renders '—' and the next
   * poll/refresh retries (same rule as makeBinanceRestPoller). No fabricated
   * values, no retry storms. `timeoutMs` overrides the 10s default for the
   * ONE endpoint that genuinely needs longer (the 33 MB HL leaderboard, §4d).
   */
  async function getJSON(url, init, timeoutMs) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs === undefined ? 10000 : timeoutMs);
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

  /** OKX SWAP contract multiplier (ctVal, base-asset units per contract) for
   *  one instId — /api/v5/public/instruments. T-1 (§4g): the okx adapter
   *  REQUIRES the real multiplier (§4b unit rail — a guessed ctVal would
   *  mis-scale every okx size against the base-denominated legs), so on a
   *  null return the caller SKIPS the leg with an honest degrade chip
   *  instead of subscribing with a wrong number. */
  async function fetchOkxCtVal(instId) {
    try {
      const j = await getJSON(
        'https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId=' + encodeURIComponent(instId)
      );
      const row = j && Array.isArray(j.data) ? j.data[0] : null;
      const v = row ? Number(row.ctVal) : NaN;
      return Number.isFinite(v) && v > 0 ? v : null;
    } catch (_) { return null; }        // unreachable/unknown → caller degrades the leg honestly
  }

  /** T-1 (§4g) listing probes — a derived binancef/coinbase id is a NAMING
   *  convention, not proof of a listing (deriveVenueIds, terminal-state.js),
   *  so startAllLegs asks the venue before subscribing any non-pinned id.
   *  Three-state: true = listed; false = the venue answered "no such
   *  market"; null = probe unreachable. The caller degrades false/null to
   *  the honest 'no leg' chip (§4g unknown/unreachable rule) instead of
   *  opening a socket that never delivers — the watchdog would loop
   *  'stalled — reconnecting' forever over a feed that never existed.
   *  Raw fetch, not getJSON: the not-listed answer ARRIVES as an HTTP error
   *  status, which getJSON collapses into the same throw as an outage. */
  async function probeListing(url, notFoundStatus) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 10000);
    try {
      const res = await fetch(url, { headers: { Accept: 'application/json' }, signal: ctrl.signal });
      if (res.ok) return true;
      return res.status === notFoundStatus ? false : null;   // other statuses = venue trouble, not an answer
    } catch (_) {
      return null;
    } finally {
      clearTimeout(t);
    }
  }

  /** Binance Futures listing probe: premiumIndex on the SAME fapi host the
   *  REST poller consumes — 200 iff the perp exists, 400 (code -1121
   *  "Invalid symbol") iff not (CORS-open, verified 2026-07-23). */
  function probeBinanceFutSymbol(sym) {
    return probeListing(
      'https://fapi.binance.com/fapi/v1/premiumIndex?symbol=' + encodeURIComponent(sym), 400);
  }

  /** Coinbase product probe: Exchange REST /products/<id> — keyless,
   *  CORS-open (verified 2026-07-23), same product ids as the advanced-trade
   *  WS feed; 404 iff no such spot market. */
  function probeCoinbaseProduct(productId) {
    return probeListing(
      'https://api.exchange.coinbase.com/products/' + encodeURIComponent(productId), 404);
  }

  /** T-2 (§4h) — Binance SPOT listing probe, the T-1 probeListing rail on the
   *  spot host: ticker/price answers 200 iff the pair exists, 400 (code
   *  -1121 "Invalid symbol") iff not (verified 2026-07-23). Gates the
   *  binance_spot leg's derived id before any subscribe. */
  function probeBinanceSpotSymbol(sym) {
    return probeListing(
      'https://api.binance.com/api/v3/ticker/price?symbol=' + encodeURIComponent(sym), 400);
  }

  /** T-2 (§4h) — Bybit SPOT listing probe. Bybit answers HTTP 200 for BOTH
   *  outcomes (the not-listed answer is retCode 0 + an EMPTY result.list —
   *  verified 2026-07-23), so the HTTP-status probeListing rail cannot read
   *  it; presence-of-data is the answer, the fetchOkxCtVal pattern. */
  async function probeBybitSpotSymbol(sym) {
    try {
      const j = await getJSON(
        'https://api.bybit.com/v5/market/instruments-info?category=spot&symbol=' + encodeURIComponent(sym));
      if (!j || Number(j.retCode) !== 0 || !j.result || !Array.isArray(j.result.list)) return null;
      return j.result.list.length > 0;
    } catch (_) { return null; }        // unreachable → caller degrades the leg honestly
  }

  /** T-2 (§4h) — OKX SPOT instrument probe: instruments?instType=SPOT answers
   *  code "0" + a data row iff the instId exists, code 51001 + empty data iff
   *  not (verified 2026-07-23) — presence-of-data again, HTTP 200 either way.
   *  No ctVal here on purpose: SPOT sz is already coin units (§4b's contracts
   *  rail is derivatives-only). */
  async function probeOkxSpotInst(instId) {
    try {
      const j = await getJSON(
        'https://www.okx.com/api/v5/public/instruments?instType=SPOT&instId=' + encodeURIComponent(instId));
      if (!j || !Array.isArray(j.data)) return null;
      if (String(j.code) === '0') return j.data.length > 0;
      return String(j.code) === '51001' ? false : null;   // other codes = venue trouble, not an answer
    } catch (_) { return null; }        // unreachable → caller degrades the leg honestly
  }

  /** T-2 (§4h) — Binance REST depth snapshot for the BinanceBookSync engines
   *  (`market` 'spot' | 'futures', depth?limit=1000 per the official local-
   *  book algo). Level rows return VERBATIM (wire string tuples): the engines
   *  key their books by the venue's OWN price strings (terminal-books.js
   *  primitives note) — a Number round-trip here would fork "65016.73000000"
   *  from the diff stream's key of the same level. Silent-null on failure:
   *  the caller's flush tick simply retries (poller idiom above). */
  async function fetchBinanceDepthSnapshot(market, sym) {
    const url = (market === 'futures'
      ? 'https://fapi.binance.com/fapi/v1/depth?symbol='
      : 'https://api.binance.com/api/v3/depth?symbol=')
      + encodeURIComponent(sym) + '&limit=1000';
    try {
      const j = await getJSON(url);
      const id = j ? Number(j.lastUpdateId) : NaN;
      if (!Number.isFinite(id) || !Array.isArray(j.bids) || !Array.isArray(j.asks)) return null;
      return { lastUpdateId: id, bids: j.bids, asks: j.asks };
    } catch (_) { return null; }        // transient REST failure ≠ terminal state — next tick retries
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

  // ─── O-4 fetch wrappers (§4d — same AbortController/silent-null pattern) ────

  /**
   * Bybit v5 tickers, category=linear: the WHOLE perp universe (~720 symbols)
   * in ONE call (§4d empirical) — the screener/RSI-heatmap symbol source.
   * One 30s poll of this single endpoint replaces 720 per-symbol calls.
   */
  async function fetchBybitAllTickers() {
    try {
      const j = await getJSON('https://api.bybit.com/v5/market/tickers?category=linear');
      return normalizeBybitTickers(j);
    } catch (_) { return null; }        // transient REST failure ≠ terminal state — caller renders '—'
  }

  /**
   * Deribit option chain: get_book_summary_by_currency, kind=option — the
   * whole currency's chain in one call (no per-instrument ticker fan-out;
   * that is rate-limit hostile, DEVELOPMENT §5). Deribit REST is CORS-OPEN
   * to browser origins (§4d, verified 2026-07-05) — fetched straight from
   * the page, no proxy.
   */
  async function fetchDeribitChain(currency) {
    try {
      const j = await getJSON(
        'https://www.deribit.com/api/v2/public/get_book_summary_by_currency' +
        '?currency=' + encodeURIComponent(currency || 'BTC') + '&kind=option'
      );
      return normalizeDeribitChain(j);
    } catch (_) { return null; }        // transient REST failure ≠ terminal state — caller renders '—'
  }

  /** Deribit DVOL (30d BTC IV index) via get_index_price?index_name=
   *  btcdvol_usdc — the 'BTC-DVOL' ticker instrument is INVALID (probed,
   *  fixtures _o4_notes), so the index endpoint is the one keyless source. */
  async function fetchDeribitDvol() {
    try {
      const j = await getJSON(
        'https://www.deribit.com/api/v2/public/get_index_price?index_name=btcdvol_usdc'
      );
      return normalizeDeribitDvol(j);
    } catch (_) { return null; }        // transient REST failure ≠ terminal state — caller renders '—'
  }

  /**
   * HL leaderboard — a 33 MB / ~40k-row payload (§4d empirical). CALLERS gate
   * this behind an EXPLICIT user click (WhaleView's "discover top traders
   * (~33 MB, one-shot)" button — the size is stated on the button); it is
   * never polled and never auto-fired on page load. 30s abort instead of the
   * usual 10s: 33 MB legitimately takes >10s on slower links, and aborting a
   * download the user explicitly asked for would just waste the transfer.
   * Returns the SMALL normalized seed lists, not the 33 MB parse.
   */
  async function fetchHlLeaderboard(n) {
    try {
      const j = await getJSON('https://stats-data.hyperliquid.xyz/Mainnet/leaderboard', undefined, 30000);
      return normalizeHlLeaderboard(j, n);
    } catch (_) { return null; }        // transient REST failure ≠ terminal state — caller renders '—'
  }

  /** HL per-address positions (POST info {type:'clearinghouseState', user})
   *  — the LIGHT (~KB) per-address poll that follows the one-shot leaderboard
   *  load. Public on-chain state: facts, not signals (§4d rail). */
  async function fetchHlClearinghouse(addr) {
    try {
      const j = await getJSON('https://api.hyperliquid.xyz/info', {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'clearinghouseState', user: addr }),
      });
      return normalizeHlPositions(j);
    } catch (_) { return null; }        // transient REST failure ≠ terminal state — caller renders '—'
  }

  // ─── O-5 fetch wrappers (§4e — same AbortController/silent-null pattern) ────

  /** Polymarket BTC events — the ONE route whose filter actually works (§4e
   *  empirical: /events?tag_slug=bitcoin; the markets?search / markets?
   *  tag_slug filters are IGNORED server-side and return unrelated markets).
   *  CORS `*` — fetched straight from the page. 60s poll. */
  async function fetchPolymarketBtc() {
    try {
      const j = await getJSON(
        'https://gamma-api.polymarket.com/events?tag_slug=bitcoin&closed=false&limit=12'
      );
      return normalizePolymarketEvents(j);
    } catch (_) { return null; }        // transient REST failure ≠ terminal state — caller renders '—'
  }

  /** Tree of Alpha news (CORS `*`, §4e) — 30s poll, limit capped sane. */
  async function fetchToaNews(limit) {
    const n = Number.isFinite(limit) && limit > 0 ? Math.min(Math.floor(limit), 100) : 30;
    try {
      const j = await getJSON('https://news.treeofalpha.com/api/news?limit=' + n);
      return normalizeToaNews(j);
    } catch (_) { return null; }        // transient REST failure ≠ terminal state — caller renders '—'
  }

  /** Local econ mirror (§4e: faireconomy has NO CORS — the browser cannot
   *  fetch it; scripts/fetch_econ.py writes this same-origin file via
   *  `make econ`). null = file absent/unreadable → the EconView renders the
   *  honest "run `make econ`" note instead of an empty-looking calendar. */
  async function fetchEconLocal() {
    try {
      const j = await getJSON('./econ_calendar.json', { cache: 'no-store' });
      return normalizeEconLocal(j);
    } catch (_) { return null; }        // absent local file ≠ error state — caller renders the make-econ hint
  }

  // ─── Export (ONE global + Node dual-export, quant.js pattern) ───────────────
  const HIST = {
    // O-3 (§4c)
    normalizeBybitKlines, normalizeOkxFunding, normalizeOkxOi, normalizeHlMids,
    fetchBybitKlines, fetchOkxFunding, fetchOkxOi, fetchOkxCtVal, fetchHlMids,
    // O-4 (§4d)
    normalizeBybitTickers, normalizeDeribitChain, normalizeDeribitDvol,
    normalizeHlLeaderboard, normalizeHlPositions,
    fetchBybitAllTickers, fetchDeribitChain, fetchDeribitDvol,
    fetchHlLeaderboard, fetchHlClearinghouse,
    // O-5 (§4e)
    normalizePolymarketEvents, normalizeToaNews, normalizeEconLocal,
    fetchPolymarketBtc, fetchToaNews, fetchEconLocal,
    // T-1 (§4g)
    probeBinanceFutSymbol, probeCoinbaseProduct,
    // T-2 (§4h): matrix-leg listability probes + the book-engine snapshots.
    probeBinanceSpotSymbol, probeBybitSpotSymbol, probeOkxSpotInst,
    fetchBinanceDepthSnapshot,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = HIST;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_HIST = HIST;
})(typeof globalThis !== 'undefined' ? globalThis : this);
