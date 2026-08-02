'use strict';
// check_terminal.cjs — fixture smoke for the orderflow terminal (DESIGN-orderflow-terminal.md §4).
//
// Follows the scripts/_parity_eval.cjs pattern: plain node, zero deps. Both
// terminal-adapters.js and terminal-state.js carry the quant.js dual-export
// (`module.exports` alongside the ONE window global), so a plain require() is
// enough — no vm sandbox / window stub needed. This file has NO normalization
// logic of its own: every assertion drives the REAL adapter/store code against
// the REAL captured frames in scripts/fixtures_ws.json (DESIGN §2 — actual
// wire shapes, not remembered docs), so any failure here is a true contract
// break between what the exchanges send and what the stores consume.
//
// What this smoke pins (DESIGN §4 "Fixture smoke" + §0.6 aggressor rails):
//   1. Bybit publicTrade  → aggressorBuy === (S === 'Buy')  (taker side, as-is)
//   2. Coinbase trades    → aggressorBuy INVERTED from `side` (maker-side rule,
//                           DEVELOPMENT.md §5 gotcha) + reconnect snapshot NOT re-seeded
//   3. Bybit orderbook (.50-captured; the adapter routes any depth) → snapshot+
//                           delta through BookStore: sane best(), and a delta
//                           qty-"0" level actually DELETES (tombstone rail)
//   4. Binance depth20    → combined {stream,data} unwrap, full-snapshot semantics
//   5. Bybit tickers      → partial-delta merge: mark/funding persist across a
//                           delta that omits them (the fixture's deltas really do)
//   6. Bybit allLiquidation → printed side inverted to the LIQUIDATED position (§3)
//   7. FootprintStore     → bar delta == Σ signed qty; diagonal imbalance fires on
//                           a constructed 3:1 (and only on finished bars)
//   8. CvdStore           → Σ per-bucket series == overall series at EVERY sample
//   9. ProfileStore       → POC = max-volume level; VAH ≥ POC ≥ VAL; value-area
//                           volume ≈ 70% (±5pp)
//  10. AggBookStore       → two-exchange merge: row.total == Σ row.byEx exactly
//
// O-2 additions (DESIGN §4b "check_terminal.cjs additions", binding list):
//  11. OKX adapter        → descriptor contract (plain-text 'ping', books+trades
//                           subscribe) + trade ctVal math: sz "200" → 2.00 BTC
//                           EXACTLY; taker side as-is (§0.6 Bybit family);
//                           ctVal override opt; markAlive per data frame
//  12. OKX books          → snapshot+update through BookStore, ctVal-scaled
//                           levels, incl. a REAL sz-"0" delete from the captured
//                           update frames (store-side tombstone rail)
//  13. Bybit orderbook.200 → subscribe arg upgraded (.200, §4b), snapshot sane at
//                           200 levels/side through BookStore, delta tombstones delete
//  14. DepthHistoryStore  → empty-book guard, ring bound by construction,
//                           velocity sign (+ on a constructed fill, − on a pull)
//  15. SpoofIcebergDetector → fires on a constructed wall-pull AND iceberg-refill,
//                           stays QUIET on a benign book; every event label:'heuristic'
//  16. LiqHeatmapModel    → exact band math (entry 100, L 10, mmr 0.005 →
//                           long 90.5 / short 109.5), sides correct vs mark,
//                           observed prints passed through UNblended, label:'estimated'
//
// O-3 additions (DESIGN §4c "check_terminal.cjs additions", binding list):
//  17. Bybit REST klines  → normalizer REVERSES the NEWEST-FIRST list to
//                           chronological, exact fixture numbers, input
//                           unmutated, retCode error → null, NaN row dropped
//  18. buildTpo           → constructed 30m bars: rows/periods, POC (count tie
//                           broken toward session mid), VAH/VAL (70% expansion),
//                           interior-only singles, IB = first 2 OBSERVED
//                           periods — all EXACT; sessions per UTC day, newest-first
//  19. buildKlineVp       → Σ levels.vol ≡ Σ bars.v (constructed exact + real
//                           fixture bars ≤1e-9), POC tie → lowest, HVN
//                           prominence gate, approx:'bar-range' label ALWAYS on
//  20. rollingCorr        → identical series = +1 / inverted = −1 on every full
//                           window, NaN below window/2 valid pairs, NaN pairs
//                           SKIPPED (never zero-coerced), window < 2 refused
//  21. OKX REST funding/OI → fundingRate exact; nextFundingTs = `fundingTime`
//                           (the UPCOMING settlement — naming gotcha); intervalH
//                           derived = 8 (+ fallback-8 path); OI = oiCcy COIN,
//                           NOT the contracts `oi` field (§4b ctVal unit rail)
//  22. HL mids normalizer → SPX-MEMECOIN GUARD: main-universe 'SPX' (SPX6900,
//                           ~$0.37, NOT the index) can never surface for a
//                           dex-filtered query — dex-prefixed keys only
//  23. BYOD row→event     → exact field rename for ALL 5 collector tables
//                           (trades/depth/liqs/funding/oi), §0.6 values pass
//                           through UNCHANGED (no re-inversion), corrupt rows → null
//
// O-4 additions (DESIGN §4d "check_terminal.cjs additions", binding list):
//  24. Bybit REST tickers → 24h-VWAP proxy === turnover24h/volume24h (≤1e-9,
//                           null on zero volume — never a fabricated 0/0),
//                           fundingIntervalH RESPONSE-provided (fallback 8
//                           only when absent), annualized = rate×(8760/H)×100,
//                           pct24h ×100, the Number('')===0 trap (blank wire
//                           field → row dropped, never a plottable 0)
//  25. Deribit chain + DVOL → name parse ('BTC-28AUG26-105000-C' + the
//                           single-digit-day edge) → strike/cp/expiryTs
//                           verified against hand-computed Date.UTC at the
//                           08:00 UTC convention; iv === mark_iv/100 (the
//                           PERCENT trap, DEVELOPMENT §5); unparseable names
//                           SKIPPED AND COUNTED; DVOL = 38.68 (the PINNED
//                           payload — §0: real capture wins)
//  26. HL leaderboard/positions → topByValue addr + acctVal exact,
//                           `windowPerformances` PAIR-ARRAY parse ('month' =
//                           the 30d window), dust (<$10k) excluded from the
//                           ROI ranking ONLY; positions szi/side/entry/
//                           leverage.value vs fixture, szi<0 → short
//  27. buildScreener       → turnover-USD ranking on the REAL fixture rows,
//                           topN slice + 'all' passthrough, total = universe
//                           size, non-finite turnover sinks (never dropped),
//                           input unmutated
//  28. confluenceReads     → EXACTLY the 9 §4d categories in order, each
//                           driven bullish AND bearish, n/a on missing feeds
//                           (never neutral), response-provided funding
//                           interval honored, tally sums to 9, the mandatory
//                           IC-honesty label VERBATIM
//  29. AlertEngine         → per-kind fire + cooldown for all 9 rule kinds,
//                           cvd-divergence carries label:'heuristic' both
//                           directions, thresholds injected (no threshold →
//                           cannot fire), event-ts driven (NaN ts → [])
//  30. unsigned GEX + PCR  → Black-76 Γ > 0 on a pinned real chain row
//                           (T from the fixture's own creation_timestamp),
//                           call Γ ≡ put Γ, Σ|Γ|·OI over two constructed
//                           rows === the hand sum (quant.js — the view's
//                           math source), PCR-by-OI exact on constructed
//                           rows AND vs hand-summed raw fixture OI
//
// O-5 additions (DESIGN §4e "check_terminal.cjs additions", binding list):
//  31. Polymarket normalizer → the §4e STRING trap: `outcomePrices` is a
//                           STRING holding a JSON array of STRINGS —
//                           yesPct = Number(JSON.parse(s)[0])·100, a plain
//                           0–100 NUMBER; event titles/volumes EXACT vs the
//                           /events?tag_slug=bitcoin fixture; closed and
//                           undecodable markets SKIPPED (never a guessed
//                           50%); non-array → null
//  32. ToA news normalizer → ts/title/source/url exact vs fixture, missing
//                           `symbols` → [] (two real fixture rows lack it),
//                           newest-first ordering imposed (input order must
//                           not matter), undatable/untitled rows dropped
//  33. econ local-file normalizer → SYNTHETIC file object (fetchedTs +
//                           fixture events — the §4e no-CORS design means
//                           the input is scripts/fetch_econ.py's output):
//                           fetchedTs passthrough UNCHANGED, ts =
//                           Date.parse of the offset-carrying date
//                           (08:15-04:00 ≡ 12:15Z, hand-checked), ascending
//                           ts sort imposed, forecast/previous stay strings
//                           ('' stays '', never a fake 0), undatable dropped
//  34. journalStats        → 3-trade HAND-COMPUTED exactness (the full hand
//                           math is in the group's comments): R = [2, −0.5,
//                           +0.5] → winRate 2/3, expectancy 2/3, avgWin
//                           1.25, avgLoss −0.5, PF 5, SQN 4/√19 (ddof=1),
//                           maxDrawR 0.5, byTag split — Tharp definitions
//                           mirrored from quant.js/risk.py, R = pnl/declared
//                           riskUsd (§4e)
//  35. riskUsd ≤ 0 exclusion → rows with riskUsd 0 / negative / NaN are
//                           EXCLUDED AND COUNTED (`excluded`), the R stats
//                           are bit-identical to group 34's (bad rows can't
//                           tilt them), and the mandatory §4e label rides
//                           the output VERBATIM
//  36. calendarReturns     → UTC close-ts bucketing (day key + hour key from
//                           the SAME trade, hand-checked), same-day trades
//                           SUM, and the ISO-week edges: Mon 2024-12-30 →
//                           2025-W01, Fri 2027-01-01 → 2026-W53 (the
//                           Thursday rule — raw-year keys would split both
//                           weeks); unstatable rows never touch a bucket
//  37. journal CSV round-trip → export→import IDENTITY (deepStrictEqual)
//                           through a note carrying comma + quotes + a
//                           NEWLINE and a ctx JSON column (RFC-4180 quoting
//                           is the whole point); numbers reproduce exactly
//                           (String↔Number shortest-round-trip); bad rows
//                           (side, non-numeric riskUsd, column count, ctx
//                           non-JSON) land in `errors` with 1-based line
//                           numbers while good rows still import — §4e:
//                           import NEVER silently coerces
//
// I-1 additions (DESIGN §4f "check_terminal groups mandatory", binding list):
//  38. buildDeltaProfile   → Σdelta ≡ Σbuy−Σsell EXACTLY (the §4f binding
//                           invariant), nearest-rank-p95 intensity hand-
//                           checked (0, 3/8, 1), ascending-lvl order, both
//                           input spellings; adversarial: an ALL-BUY day
//                           stays bounded in [0,1] with the outlier clamped
//                           to exactly 1, an all-zero-delta profile renders
//                           neutral (never NaN), garbage rows skipped
//  39. AnchoredVwap        → streaming Welford ≡ two-pass batch Σq(p−μ)²/Σq
//                           to 1e-9 over 500 deterministic LCG trades at
//                           BTC price scale; reset(anchor) drops pre-anchor
//                           prints (hand case σ=√0.75); adversarial: a
//                           SINGLE trade gives σ=0 (not NaN), empty state
//                           gives NaN bands + n:0, garbage trades dropped
//  40. OfiStore            → Cont–Kukanov–Stoikov e_t HAND-COMPUTED on a
//                           3-snapshot sequence (6 then 20), rolling-sum
//                           series both windows, zscore(2) = 1/√2 exact;
//                           adversarial: EMPTY ladder = reconnect gap
//                           (clears the seed, fabricates no e), CROSSED
//                           book stays finite (+5 hand value), unequal
//                           ladder depth = the degenerate add/remove case
//  41. microprice          → Stoikov hand case 403/4 = 100.75 (pulled
//                           TOWARD the thin ask), identical through a REAL
//                           BookStore; equal depth ≡ mid; null on empty/
//                           one-sided/zero-depth books (never NaN)
//  42. stackedImbalances   → constructed 3-run buy zone {104..102} fires
//                           exact; open-bar range INVALIDATES it (l below
//                           bottom → active:false) while flag-worthy open-
//                           bar levels create NO zone; sell mirror + the
//                           strict-inequality boundary (h == top holds);
//                           adversarial: a grid GAP breaks the run,
//                           alternating buy/sell rows never stack
//  43. AbsorptionDetector  → spike (35 vs median 4) + no-follow-through
//                           fires ONE event with label:'heuristic' riding
//                           it; progress kills it; no-spike bar is quiet;
//                           adversarial: the session's FIRST bar alone can
//                           never fire (no next bar to resolve against),
//                           unfinished bars are ignored entirely; sell-side
//                           mirror resolves against the next bar's LOW
//  44. SessionClock        → half-open [start,end) boundaries EXACT:
//                           07:00:00.000 UTC = Asia+London, 08:00 leaves
//                           Asia, 21:00 + 23:59:59.999 = the honest dead
//                           zone, 00:00 = Asia; negative/NaN ts → [];
//                           boxesFor pure arithmetic on the given anchor
//  45. cumDelta            → hand accumulation with malformed bars SKIPPED
//                           (never zero-coerced) + integration over a REAL
//                           FootprintStore tape (finished + open bars)
//  46. terminal-hfdata     → normalizeArchivedRow: parquet BigInt → Number
//                           (the ONLY decode/BYOD mismatch, §4f probe),
//                           everything else untouched (depth JSON strings
//                           stay strings), normalized rows feed the REAL
//                           byodRowToEvent unchanged; archivedParquetUrl
//                           %3D hive encoding + allowlist THROWS on bad
//                           repo/date/table segments — all network-free
//
// T-1 additions (DESIGN §4g "check_terminal groups (mandatory adds)"):
//  47. footprint delta path → deltaMin/deltaMax = extremes of the HAND-
//                           WRITTEN running-delta path (0-anchored) on a
//                           constructed trade sequence; deltaPct =
//                           delta/totalVol exact (buy-only bar → 1)
//  48. unfinished auction  → both-sided extreme flags TRUE, one-sided
//                           extreme flags FALSE, and flags stay false on
//                           the OPEN bar (finished-bar-only discipline)
//  49. TapeIntensityStore  → window math at fixed ts steps (hand-computed
//                           rates), z stays 0 until 5 COMPLETED baseline
//                           samples then z = −1/√5 exact, far-ahead ts
//                           jump prunes both windows down to the lone print
//  50. WallsLedger         → enters after EXACTLY M sustained samples (M−1
//                           + a break must NOT enter), pulled on a >1-tick
//                           vanish, filled via markTrade cross, near-mid
//                           vanish stays standing until the cross
//  51. VpinStore           → hand-computed buckets incl. a print that
//                           STRADDLES the boundary (split exact) and one
//                           spanning multiple buckets; vpin = hand mean;
//                           null before the first complete; setBucketVol
//                           re-arms FUTURE buckets only
//  52. OpeningTypeClassifier → four constructed price paths, one per Dalton
//                           class, plus pending before 60 min; the
//                           descriptive-read label rides every result
//  53. deriveVenueIds      → BTCUSDT/ETHUSDT full maps, 1000PEPEUSDT has
//                           NO coinbase spot (null), non-USDT symbol
//                           degrades binancef+okx+coinbase to null (bybit
//                           keeps the id — it IS the picker's universe)
//  54. BasisSeries         → ring wrap at max (oldest evicted, order kept),
//                           NaN funding stored NOT zero-coerced, latest()
//
// T-2 additions (DESIGN §4h "check_terminal groups (mandatory adds)"):
//  55. Binance spot continuity → buffered-before-snapshot drop (u ≤ lastId),
//                           event STRADDLING the snapshot boundary applies,
//                           contiguous chain applies, qty-0 delete + absolute
//                           replace, stale live diff ignored, gap → desync +
//                           resyncCount + CLEARED book, and the violating
//                           event re-buffers so a fresh snapshot resumes
//  56. Binance futures pu chain → valid pu chain applies; broken pu → desync
//                           even though U is CONTIGUOUS — and the SAME event
//                           sequence stays synced under the spot rule, proving
//                           the two continuity rules genuinely differ
//  57. OKX CRC32 checksum  → dependency-free crc32 vs TWO pinned zlib.crc32
//                           vectors (commands + values in the group), the
//                           interleave string hand-written and asserted
//                           byte-exact, valid snapshot+update verify, a
//                           deliberate qty tweak → mismatch → desync +
//                           cleared, post-desync updates ignored, fresh
//                           snapshot re-arms
//  58. Coinbase l2 book    → snapshot + apply/remove-zero/absolute-replace,
//                           corrupt side tag skipped, lastUpdateTs advances
//                           per frame (the ONLY rail — no sequence number,
//                           stated), new snapshot replaces wholesale
//  59. SpotPerpCvdStore    → hand-computed perp/spot accumulation, per-10s
//                           samples close on event time (no zero-gap
//                           synthesis), out-of-order push still accumulates,
//                           ring wrap, invalid pushes dropped, latest() null
//                           before any push
//  60. deriveLegIds        → BTCUSDT full 7-leg matrix, 1000PEPEUSDT coinbase
//                           null (T-1 hard rule carried over), non-USDT →
//                           only bybit_linear survives; deriveVenueIds keeps
//                           its frozen 4-key T-1 shape (additivity)
//
// T-2 wave-2 additions (§4h adapters + leg registry; fixtures `_t2_notes`,
// live captures 2026-07-23):
//  61. LegRegistry         → 7 fixed defs aligned with deriveLegIds keys,
//                           default all-enabled, strict-boolean seed, flip
//                           reports change-once, snapshot rows are COPIES,
//                           unknown keys read disabled / write dropped
//  62. bybit spot adapter  → spot endpoint + both topics + op-ping; real
//                           publicTrade frames (taker side AS-IS, §0.6),
//                           orderbook.200 snapshot+delta through BookStore
//                           incl. a REAL qty-"0" tombstone; book frames mark
//                           alive (no tickers channel), trades never
//  63. binance spot adapter → aggTrade `m` INVERSION on real frames (§0.6:
//                           isBuyerMaker true = SELL aggressor), diffs feed
//                           BinanceBookSync (never the sink) and the REAL
//                           same-moment REST snapshot drains them to synced —
//                           the fixture pins u==lastUpdateId (covered drop)
//                           and U==lastUpdateId+1 (contiguous) AS CAPTURED
//  64. binance fut diff    → real @depth@100ms frames pu-chain exactly
//                           (pinned per-frame) and the REAL fapi snapshot
//                           lands INSIDE the bracket → futures engine syncs,
//                           zero resyncs; adapter emits nothing to any sink
//                           (§0.2 — fut trades live in the collector's REST
//                           aggTrades poller, never duplicated here)
//  65. okx books adapter   → MEASURED §4h deviation pinned as a fixture
//                           precondition: every real books frame (swap+spot)
//                           carries checksum:0 — the venue no longer
//                           populates the CRC32 on the keyless channel, so
//                           OkxBookSync's verify (group 57) has no wire
//                           input; the adapter enforces the INTACT seqId/
//                           prevSeqId chain instead: real frames apply
//                           (ctVal ×0.01 exact on a pinned swap row; spot
//                           UNSCALED — already coin units), a TAMPERED
//                           prevSeqId halts emission + flags bookGapped()
//                           until a fresh snapshot re-arms; trades taker-
//                           side as-is; one socket, plain-text ping
//  66. coinbase l2 adapter → exchange-feed snapshot (>1MB on the wire,
//                           trimmed fixture) + l2updates through
//                           CoinbaseBookSync (absolute qty, ts rail), match
//                           maker-side INVERSION (lowercase `side`),
//                           last_match seeds ONCE, monotonic trade_id dedupe
//                           swallows re-delivery, l2/heartbeat mark alive,
//                           matches never; NO ping (a re-subscribe nudge
//                           would re-send the whole snapshot)
//
// T-3 additions (DESIGN §4i "check_terminal groups", binding list):
//  67. TapeAggregator      → a same-ex/side/price run merges (hand VWAP +
//                           count), each of price change / side flip / ex
//                           change / window expiry FLUSHES a fresh row, the
//                           forming row shows in list() newest-first, and a
//                           cross-ex same-instant print NEVER merges (§0.7)
//  68. sizeTier            → classification EXACTLY at each boundary notional
//                           (1e5/2.5e5/1e6/5e6 → the higher tier, inclusive ≥)
//                           and just below (the lower), partial-override merge,
//                           non-finite → baseline; defaults object exported
//  69. BigPrintRail        → only huge/whale kept (large/sig/baseline dropped),
//                           newest-first, ring-bound at N (oldest evicted),
//                           each kept row tagged with its tier
//  70. TradeImprint        → buy/sell split at a tick level, NEAREST-tick
//                           rounding lands two prices on the right levels, and
//                           a far-ts push PRUNES the aged window (levels drop
//                           to the lone survivor); map() re-expands grid prices
//  71. DepthLadder         → ladderRows cumulative-from-mid sums + ticks by
//                           hand (nRows-capped); depthImbalance within N ticks
//                           with the level EXACTLY N ticks out INCLUDED and
//                           N+1 excluded; logBarWidth monotone + bounded [0,1]
//                           (value pinned via the ln(1+x) identity, not a
//                           log1p mirror); mergeSameQuoteBooks sums two USDT
//                           legs and EXCLUDES a coin/USD leg (never rescaled)
//  72. liqTier             → liquidation notional tiers (own big/huge cuts,
//                           separate from the tape): EXACTLY at a cut → higher
//                           tier (inclusive ≥), just below → lower, partial
//                           override merge, non-finite → baseline (the audio
//                           ping's 'huge' gate never fires on NaN/Infinity)
//  73. filterTapeRows      → merged-tape row projection: both/spot/perp filter
//                           drops the right market, per-row spot/perp tag +
//                           sizeTier tag, exact single-venue + min-notional
//                           gates, unknown ex / no resolver → perp default
//                           (never a silent spot mislabel)
//  74. isControlFrame      → T-4 R2: the OPTIONAL control-frame predicate and
//                           livewire's branch for it — exact 'pong' equality,
//                           only OKX declares it (measured), a control frame is
//                           NOT a dropped frame and NOT a message, a genuinely
//                           malformed frame still counts, an absent predicate
//                           is byte-identical to today (app.js unaffected);
//                           plus the LIVENESS half driven through the real
//                           watchdog on a stubbed clock: a keepalive stamps the
//                           answering clock, never retracts amber, and never
//                           saves a pong-only socket from the DEAD_MS force-
//                           reconnect — and Bybit's JSON pong (real captured
//                           frames) takes the same route via markControlAlive
//  75. tapeFloorSummary    → T-4: the sub-floor residue the min-notional floor
//                           takes out of view — blocks vs prints, buy/sell
//                           split, share, filterTapeRows' verbatim `<` boundary,
//                           null when the floor is off or nothing fell below,
//                           and the kept+hidden === passed invariant that proves
//                           nothing vanishes silently
//  76. news relevance      → T-4: newsRelevance evidence ladder over REAL
//                           captured rows (t4_toa_news) — account-mapped
//                           suggestions are NOT content evidence, transport
//                           'source' is not evidence, venue notices pass,
//                           BTC rides coins not the 0/200 symbols array; and
//                           filterNewsRows' visible counts (kept+filtered
//                           === total always, 'all' gives everything back)
//  77. layout invariants   → T-4: the ONE structural fact the local-only strip
//                           depends on and nothing else could witness (it is
//                           `if (REPLAY) return`, so the ?replay=1 browser
//                           harness cannot reach it) — no two elements share a
//                           grid area, the strips claim none at all, every
//                           area-* class has a grid-area rule, and no panel the
//                           strip folds carries a section-nav anchor id//  78. PANEL_DEFS         → M3: the ONE panel registry. Keys unique, minMs
//      registry             positive, sections real; 'header' AND 'local' are the
//                           only gate-exempt descriptors and each is pinned WITH
//                           its reason (masking feed health / a display:none node
//                           an IntersectionObserver would latch off forever);
//                           every render unit exists as an id in terminal.html AND
//                           every DOM id="view-*" is claimed by a descriptor;
//                           fp and local each own TWO units; derived tables OMIT
//                           nulls, return fresh objects, and reproduce the
//                           pre-M3 literals against GOLDEN pins (data-level
//                           equivalence — the L1 harness is not pixel-deterministic,
//                           ~15% run-to-run on the live-clock panels)
//
// Exit: 0 with one PASS line per group; non-zero with a clear FAIL message
// (plus stack) if any group breaks. Run: node scripts/check_terminal.cjs

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const A = require(path.join(__dirname, '..', 'dashboard', 'terminal-adapters.js'));
const S = require(path.join(__dirname, '..', 'dashboard', 'terminal-state.js'));
// N5: the shared reconnect/backoff/watchdog socket — dual-exported (module.exports
// alongside the window global), so the livewire onDropped group can drive its two
// onmessage catches directly with a WebSocket stub (no bundler, no network).
const LW = require(path.join(__dirname, '..', 'dashboard', 'livewire.js'));
const H = require(path.join(__dirname, '..', 'dashboard', 'terminal-hist.js'));
const R = require(path.join(__dirname, '..', 'dashboard', 'terminal-replay.js'));
// I-1 (§4f Wave 2): hyparquet is resolved LAZILY inside terminal-hfdata.js, so
// requiring it here never touches the vendor bundle — group 46 only drives the
// pure normalizer + URL allowlist (network-free by construction).
const HF = require(path.join(__dirname, '..', 'dashboard', 'terminal-hfdata.js'));
// T-2 (§4h): full-book sync engines — pure, constructed-sequence groups only
// (no fixtures needed: continuity/checksum rules are exact arithmetic).
const B = require(path.join(__dirname, '..', 'dashboard', 'terminal-books.js'));
// Views are DOM-driven, but their pure formatters are not — and hmsMs makes an
// honesty CLAIM (event time, ms resolution), so it gets pinned like any rail.
const V = require(path.join(__dirname, '..', 'dashboard', 'terminal-views.js'));
// quant.js is the O-4 views' options-math source (§4d: Γ via black76Greeks,
// max pain via maxPain — the house rule forbids reimplementing either), so
// group 30 drives it directly with normalized chain rows.
const Q = require(path.join(__dirname, '..', 'dashboard', 'quant.js'));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures_ws.json'), 'utf8'));

// makeSocket's liveApi surface, minus the socket: adapters only ever touch
// markAlive/onStatus here. Frames are fed straight into adapter.onMessage —
// exactly what livewire.js does after JSON.parse.
const nullApi = { markAlive() {}, onStatus() {} };

/** Like nullApi but counts markAlive() calls — the O-2 OKX groups pin the
 *  "every books/trades data frame marks alive" contract (§4b), which is that
 *  adapter's ONLY liveness source (its 'pong' is plain text and never survives
 *  makeSocket's JSON.parse). */
function countingApi() {
  const api = { alive: 0, markAlive() { api.alive++; }, onStatus() {} };
  return api;
}

/** Capture everything an adapter's subscribe()/ping() writes to the socket —
 *  frames are JSON.parse'd when possible, kept as raw strings otherwise (the
 *  OKX keepalive is deliberately a NON-JSON plain-text 'ping', §4b). */
function captureWs() {
  const sent = [];
  return { sent, ws: { send(s) { try { sent.push(JSON.parse(s)); } catch (_) { sent.push(s); } } } };
}

/** Drive every frame of a fixture array through an adapter, exactly as
 *  livewire.js would post-JSON.parse. Events land in the collecting sink the
 *  adapter was constructed with — see collectSink(). */
function replay(adapter, frames) {
  for (const f of frames) adapter.onMessage(f, nullApi);
}

function collectSink() {
  const evts = [];
  return { evts, sink: (e) => evts.push(e) };
}

let failures = 0;
function group(name, fn) {
  try {
    fn();
    console.log('PASS ' + name);
  } catch (e) {
    failures++;
    console.error('FAIL ' + name + ' — ' + (e && e.message ? e.message : e));
    if (e && e.stack) console.error(String(e.stack).split('\n').slice(1, 4).join('\n'));
  }
}

const approx = (a, b, eps) => Math.abs(a - b) <= (eps === undefined ? 1e-9 : eps);

// ─── 1. Bybit publicTrade → normalized trade (§0.6: taker side AS-IS) ────────
group('bybit publicTrade normalization', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBybitAdapter('BTCUSDT', sink);
  replay(ad, FX.bybit_publicTrade);
  assert.ok(evts.length === 3, 'expected 3 trades from 3 fixture frames, got ' + evts.length);
  // Pair each emitted event with its raw wire item (frames carry 1 trade each).
  FX.bybit_publicTrade.forEach((frame, i) => {
    const raw = frame.data[0], ev = evts[i];
    assert.strictEqual(ev.kind, 'trade');
    assert.strictEqual(ev.ex, 'bybit');
    // The §0.6 rail this whole group exists for: S is ALREADY the aggressor.
    assert.strictEqual(ev.aggressorBuy, raw.S === 'Buy', 'aggressorBuy must equal (S===Buy), no inversion');
    assert.ok(Number.isInteger(ev.ts) && ev.ts === raw.T, 'ts must be the wire T as an int (epoch ms)');
    assert.ok(typeof ev.price === 'number' && Number.isFinite(ev.price) && ev.price === Number(raw.p), 'price must be Number(p)');
    assert.ok(typeof ev.qty === 'number' && Number.isFinite(ev.qty) && ev.qty === Number(raw.v), 'qty must be Number(v)');
    assert.strictEqual(ev.id, raw.i, 'id must be the UUID string verbatim');
  });
});

// ─── 2. Coinbase market_trades → maker-side INVERSION + no snapshot re-seed ──
group('coinbase maker-side inversion + reconnect snapshot skip', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeCoinbaseAdapter('BTC-USD', sink);

  // First snapshot seeds the tape.
  replay(ad, FX.coinbase_market_trades_snapshot);
  const nSeed = evts.length;
  const rawSnapTrades = FX.coinbase_market_trades_snapshot[0].events[0].trades;
  assert.ok(nSeed === rawSnapTrades.length, 'seed snapshot should emit every trade once (' + rawSnapTrades.length + '), got ' + nSeed);

  // §0.6 / DEVELOPMENT.md §5: `side` is the MAKER — aggressor is the INVERSE.
  // Cross-check every emitted event against its raw wire trade by id.
  const rawById = new Map(rawSnapTrades.map((t) => [String(t.trade_id), t]));
  for (const ev of evts) {
    const raw = rawById.get(ev.id);
    assert.ok(raw, 'emitted trade id ' + ev.id + ' not in the fixture');
    assert.strictEqual(ev.aggressorBuy, raw.side === 'SELL',
      'maker side=' + raw.side + ' must invert to aggressorBuy=' + (raw.side === 'SELL'));
    assert.ok(Number.isInteger(ev.ts) && ev.ts === Date.parse(raw.time), 'ts must be int epoch ms of the wire time');
    assert.ok(Number.isFinite(ev.price) && Number.isFinite(ev.qty), 'price/qty must be finite Numbers');
  }
  // Batches must come out oldest→newest (trade_id ascending) so CVD/footprint
  // accumulate in time order — the wire delivers them NEWEST-first.
  for (let i = 1; i < evts.length; i++) {
    assert.ok(Number(evts[i].id) > Number(evts[i - 1].id), 'seed batch must be re-sorted oldest→newest');
  }

  // A SECOND snapshot (Coinbase re-fires the full snapshot on every reconnect)
  // must be skipped entirely — re-seeding would double-count the whole batch
  // into CVD/footprint (fabricated flow, §0.7).
  replay(ad, FX.coinbase_market_trades_snapshot);
  assert.strictEqual(evts.length, nSeed, 'reconnect snapshot must emit ZERO new trades');

  // Updates still flow after the skipped snapshot, inverted + deduped.
  replay(ad, FX.coinbase_market_trades_update);
  const updates = evts.slice(nSeed);
  const nRawUpd = FX.coinbase_market_trades_update
    .reduce((n, f) => n + f.events[0].trades.length, 0);
  assert.strictEqual(updates.length, nRawUpd, 'every update trade emits exactly once');
  for (const ev of updates) {
    assert.ok(Number(ev.id) > 1049465696, 'update ids must be newer than the snapshot max');
  }
  // Spot-check one known update against the rail: trade 1049465700 side=SELL → aggressorBuy true.
  const u700 = updates.find((e) => e.id === '1049465700');
  assert.ok(u700 && u700.aggressorBuy === true, 'SELL maker print must normalize to an aggressive BUY');
});

// ─── 3. Bybit orderbook snapshot+delta → BookStore (qty-0 tombstone rail) ────
// (Frames were captured from orderbook.50 in O-1; the adapter now subscribes
// .200 with a depth-AGNOSTIC 'orderbook.' route — §4b — so these .50 frames
// still exercise the exact same code path. Group 13 covers the .200 frames.)
group('bybit book snapshot+delta through BookStore', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBybitAdapter('BTCUSDT', sink);
  const book = S.BookStore();

  // Snapshot first.
  replay(ad, FX.bybit_orderbook_snapshot);
  assert.strictEqual(evts.length, 1);
  assert.strictEqual(evts[0].kind, 'depth');
  assert.strictEqual(evts[0].isSnapshot, true, 'type:snapshot must map to isSnapshot:true');
  book.applyDepth(evts[0]);
  let b = book.best();
  assert.ok(b.bid && b.ask && Number.isFinite(b.bid[0]) && Number.isFinite(b.ask[0]), 'best() finite after snapshot');
  assert.ok(b.bid[0] < b.ask[0], 'bid < ask after snapshot');
  assert.strictEqual(b.bid[0], 61855.0, 'fixture best bid');
  assert.strictEqual(b.ask[0], 61855.1, 'fixture best ask');
  assert.ok(book.bids.has(61844.8), 'level 61844.80 present pre-delta');

  // Delta 1 carries ["61844.80","0"] — the qty-0 tombstone MUST survive the
  // adapter (kept, not filtered) and the store must DELETE the level.
  const d1 = FX.bybit_orderbook_delta[0];
  evts.length = 0;
  replay(ad, [d1]);
  assert.strictEqual(evts[0].isSnapshot, false, 'type:delta must map to isSnapshot:false');
  assert.ok(evts[0].bids.some((l) => l[0] === 61844.8 && l[1] === 0), 'adapter must keep the qty-0 tombstone for the store');
  book.applyDepth(evts[0]);
  assert.ok(!book.bids.has(61844.8), 'delta qty "0" must DELETE the level from the book');
  assert.strictEqual(book.bids.get(61854.4), 0.002, 'delta must upsert the new bid level');

  // Remaining deltas keep the book sane (61863.60 deleted then re-added at 1.407).
  evts.length = 0;
  replay(ad, FX.bybit_orderbook_delta.slice(1));
  for (const ev of evts) book.applyDepth(ev);
  assert.ok(!book.asks.has(61857.1), 'third delta deletes ask 61857.10');
  assert.ok(!book.asks.has(61863.5), 'ask 61863.50 added then deleted across deltas');
  assert.strictEqual(book.asks.get(61863.6), 1.407, 'ask 61863.60 deleted then re-added at 1.407');
  b = book.best();
  assert.ok(Number.isFinite(b.bid[0]) && Number.isFinite(b.ask[0]) && b.bid[0] < b.ask[0], 'best() sane after all deltas');
});

// ─── 4. Binance depth20 (combined {stream,data} wrap) → depth applied ────────
group('binance depth20 combined-stream unwrap', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBinanceDepthAdapter('BTCUSDT', sink);
  const book = S.BookStore();
  replay(ad, FX.binancef_depth20);
  assert.strictEqual(evts.length, FX.binancef_depth20.length, 'one depth event per wrapped frame');
  for (const ev of evts) {
    assert.strictEqual(ev.kind, 'depth');
    assert.strictEqual(ev.ex, 'binancef');
    assert.strictEqual(ev.isSnapshot, true, 'every depth20 frame is a FULL snapshot (wire reality)');
    assert.strictEqual(ev.bids.length, 20);
    assert.strictEqual(ev.asks.length, 20);
    // Sorted best-first per the §4 contract.
    for (let i = 1; i < 20; i++) {
      assert.ok(ev.bids[i][0] < ev.bids[i - 1][0], 'bids descending (best first)');
      assert.ok(ev.asks[i][0] > ev.asks[i - 1][0], 'asks ascending (best first)');
    }
    book.applyDepth(ev);
  }
  const b = book.best();
  assert.ok(b.bid && b.ask && Number.isFinite(b.bid[0]) && Number.isFinite(b.ask[0]), 'best() finite');
  assert.ok(b.bid[0] < b.ask[0], 'bid < ask');
  assert.strictEqual(b.bid[0], 61883.6, 'fixture best bid (second snapshot replaced the first)');
  assert.strictEqual(b.ask[0], 61883.7, 'fixture best ask');
  // Full-snapshot semantics: level 61887.50 exists only in frame 1 — frame 2
  // must have wiped it (a merge instead of a replace would leak stale levels).
  assert.ok(!book.asks.has(61887.5), 'snapshot replace must drop levels absent from the newest frame');
});

// ─── 5. Bybit tickers partial-delta merge → mark/funding persist ─────────────
group('bybit tickers snapshot+delta merge', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBybitAdapter('BTCUSDT', sink);
  const snap = FX.bybit_tickers_snapshot[0].data;

  replay(ad, FX.bybit_tickers_snapshot);
  const marks0 = evts.filter((e) => e.kind === 'mark');
  const ois0 = evts.filter((e) => e.kind === 'oi');
  assert.strictEqual(marks0.length, 1, 'snapshot emits one mark');
  assert.strictEqual(ois0.length, 1, 'snapshot emits one oi');
  assert.strictEqual(marks0[0].mark, Number(snap.markPrice));
  assert.strictEqual(ois0[0].oi, Number(snap.openInterest));

  // The fixture deltas genuinely OMIT markPrice/fundingRate/openInterest
  // (delta 1 is bid/ask-only; delta 2 moves indexPrice; delta 3 bid-only) —
  // exactly the wire reality the merge exists for. Assert the precondition so
  // this test can never silently pass against a re-captured fixture that
  // stopped exercising the merge.
  for (const f of FX.bybit_tickers_delta) {
    assert.ok(!('markPrice' in f.data) && !('fundingRate' in f.data) && !('openInterest' in f.data),
      'fixture precondition: deltas must omit mark/funding/OI to exercise the merge');
  }

  evts.length = 0;
  replay(ad, FX.bybit_tickers_delta);
  const marks = evts.filter((e) => e.kind === 'mark');
  const ois = evts.filter((e) => e.kind === 'oi');
  assert.strictEqual(marks.length, 3, 'one merged mark per delta');
  assert.strictEqual(ois.length, 3, 'one merged oi per delta');
  for (const m of marks) {
    assert.strictEqual(m.mark, Number(snap.markPrice), 'mark must PERSIST from the snapshot across omitting deltas');
    assert.strictEqual(m.fundingRate, Number(snap.fundingRate), 'fundingRate must persist');
    assert.strictEqual(m.nextFundingTs, Number(snap.nextFundingTime), 'nextFundingTs must persist');
  }
  for (const o of ois) assert.strictEqual(o.oi, Number(snap.openInterest), 'oi must persist');
  // Delta 2 DOES update indexPrice — the merge must take the new value…
  assert.strictEqual(marks[0].index, Number(snap.indexPrice), 'delta 1 (no index change) keeps the snapshot index');
  assert.strictEqual(marks[1].index, 61876.89, 'delta 2 updates the merged index');
  // …and delta 3 (bid-only) must keep delta 2's index, not regress to the snapshot.
  assert.strictEqual(marks[2].index, 61876.89, 'delta 3 keeps the last merged index');
});

// ─── 6. Bybit allLiquidation → side = the LIQUIDATED position (§3) ──────────
group('bybit liquidation side inversion', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBybitAdapter('BTCUSDT', sink);
  // Real captured frames (JUP/BEAT/1000PEPE — no BTC liq fired in the capture
  // window; the convention is symbol-independent). Retarget topics so the
  // adapter's own topic routing is exercised too.
  const frames = FX.bybit_allLiquidation.map((f) => Object.assign({}, f, { topic: 'allLiquidation.BTCUSDT' }));
  replay(ad, frames);
  assert.strictEqual(evts.length, frames.length, 'one liq per fixture frame');
  frames.forEach((f, i) => {
    const raw = f.data[0], ev = evts[i];
    assert.strictEqual(ev.kind, 'liq');
    // Printed 'Buy' is the forced BUY-BACK of a liquidated SHORT (and vice
    // versa) — reading the print as the position would flip every label.
    assert.strictEqual(ev.side, raw.S === 'Buy' ? 'short' : 'long', 'printed side must invert to the liquidated position');
    assert.ok(approx(ev.notionalUsd, Number(raw.p) * Number(raw.v)), 'notional = price × qty');
    assert.ok(Number.isInteger(ev.ts) && ev.ts === raw.T, 'ts = wire T');
  });
  // The fixture contains both prints, so both output sides are exercised.
  assert.ok(evts.some((e) => e.side === 'short') && evts.some((e) => e.side === 'long'),
    'fixture precondition: both Buy and Sell prints present');
});

// ─── 7. FootprintStore: delta = Σ signed qty; 3:1 diagonal imbalance ─────────
group('footprint bar delta + diagonal imbalance', () => {
  const fp = S.FootprintStore({ barMs: 60000, tickSize: 1 }); // imbalanceK=3, minVol=1.0 defaults
  const T0 = 1783076400000; // bar-aligned epoch ms
  const mk = (ts, price, qty, buy) => ({ kind: 'trade', ex: 'bybit', ts, price, qty, aggressorBuy: buy, id: String(ts) });

  // Bar 1 — constructed EXACT 3:1 diagonal: 3.0 BTC bought at 101 vs 1.0 BTC
  // sold one tick below at 100 → buyImb at 101 (3.0 ≥ 3×1.0 AND ≥ minVol 1.0;
  // the rule is `buy[p] ≥ k·sell[p−tick]`, §4). The stray 0.5 sell goes to 99
  // — deliberately BELOW minVol so it must not flag anything itself, and one
  // extra tick down so it cannot pollute the 101↔100 diagonal under test.
  fp.onTrade(mk(T0 + 1000, 100, 1.0, false));   // sell 1.0 @ 100
  fp.onTrade(mk(T0 + 2000, 101, 2.0, true));    // buy  2.0 @ 101
  fp.onTrade(mk(T0 + 3000, 101, 1.0, true));    // buy  1.0 @ 101 (same level accumulates)
  fp.onTrade(mk(T0 + 4000, 99.4, 0.5, false));  // sell 0.5 @ 99.4 → snaps DOWN to level 99

  // While the bar is OPEN: delta already sums, but imbalance flags must be all
  // false (flags are finished-bar-only — half-formed flags flicker).
  const open = fp.current();
  assert.ok(open && !open.finished, 'current() is the open bar');
  assert.ok(open.levels.every((l) => !l.buyImb && !l.sellImb), 'no imbalance flags on the open bar');

  // Signed sum: +2 +1 (buys) −1 −0.5 (sells) = 1.5; total 4.5.
  fp.onTrade(mk(T0 + 61000, 100.5, 0.25, true)); // first trade of the NEXT bar closes bar 1
  const bars = fp.bars();
  assert.strictEqual(bars.length, 2, 'one finished + one open bar');
  const bar1 = bars[0];
  assert.ok(bar1.finished, 'bar 1 finished on event time');
  assert.ok(approx(bar1.delta, 1.5), 'delta must equal Σ signed qty (got ' + bar1.delta + ')');
  assert.ok(approx(bar1.totalVol, 4.5), 'totalVol = buy + sell volume');
  assert.ok(approx(bar1.buyVol, 3.0) && approx(bar1.sellVol, 1.5), 'per-side volumes');
  assert.strictEqual(bar1.o, 100, 'OHLC: open = first print');
  assert.strictEqual(bar1.h, 101);
  assert.strictEqual(bar1.l, 99.4);
  assert.strictEqual(bar1.c, 99.4, 'close = last print');

  // Levels come out DESCENDING price (ladder order): [101, 100, 99].
  assert.deepStrictEqual(bar1.levels.map((l) => l.price), [101, 100, 99], 'levels descending');
  const l101 = bar1.levels[0], l100 = bar1.levels[1], l99 = bar1.levels[2];
  assert.ok(approx(l101.buy, 3.0) && approx(l101.sell, 0), 'level 101 volumes');
  assert.ok(approx(l100.buy, 0) && approx(l100.sell, 1.0), 'level 100 volumes');
  assert.ok(approx(l99.sell, 0.5), 'level 99 volumes (99.4 snapped down)');
  assert.strictEqual(l101.buyImb, true, '3:1 diagonal must flag buyImb at 101 (buy 3.0 ≥ 3×sell(100)=3.0)');
  assert.strictEqual(l100.sellImb, false, 'sell 100 must NOT flag (1.0 < 3×buy(101)=9)');
  assert.strictEqual(l100.buyImb, false, 'zero buy volume can never flag buyImb');
  assert.strictEqual(l99.sellImb, false, '0.5 BTC is under the 1.0 minVol floor — dust must not flag');
});

// ─── 8. CvdStore: Σ per-bucket == overall at every sample ────────────────────
group('cvd bucket sums equal overall', () => {
  const cvd = S.CvdStore({ bucketsUsd: [1e4, 1e5, 1e6] });
  const T0 = 1783076400000;
  // Mixed notionals hitting every bucket incl. whale, both signs. price=50k →
  // qty 0.1 = $5k (≤10k), qty 1 = $50k (≤100k), qty 10 = $500k (≤1M), qty 30 = $1.5M (whale).
  const flows = [
    [0.1, true], [1, false], [10, true], [30, false],
    [0.1, false], [1, true], [10, false], [30, true],
    [0.2, true], [2, true],
  ];
  flows.forEach(([qty, buy], i) => cvd.onTrade({ ts: T0 + i * 100, price: 50000, qty, aggressorBuy: buy }));
  const s = cvd.series();
  assert.strictEqual(s.t.length, flows.length, 'stride-1 sampling: one sample per trade');
  assert.ok(cvd.buckets.includes('whale'), "bucket keys include 'whale'");
  for (let i = 0; i < s.t.length; i++) {
    let sum = 0;
    for (const k of cvd.buckets) sum += s.byBucket[k][i];
    // Every signed dollar lands in exactly ONE bucket — Σ buckets must equal
    // overall at every sample, not just the last (float-assoc tolerance only).
    assert.ok(approx(sum, s.overall[i], 1e-6 * Math.max(1, Math.abs(s.overall[i]))),
      'sample ' + i + ': Σ buckets ' + sum + ' != overall ' + s.overall[i]);
  }
  // Sanity on the final value: hand-summed signed notional of the flows above.
  const expected = flows.reduce((a, [q, b]) => a + (b ? 1 : -1) * q * 50000, 0);
  assert.ok(approx(s.overall[s.overall.length - 1], expected, 1e-6), 'final overall = hand-computed signed notional');
});

// ─── 9. ProfileStore: POC / VAH ≥ POC ≥ VAL / value area ≈ 70% ───────────────
group('profile POC + 70% value area', () => {
  const prof = S.ProfileStore({ tickSize: 1 });
  const T0 = 1783076400000;
  // 101 one-unit levels at prices 100..200 plus 1 extra unit at 150 → POC=150
  // (vol 2), total 102. Expansion absorbs one 1-unit level per step, so the
  // covered volume overshoots the 70% target by < one level (~1%) — well
  // inside the ±5pp assertion band.
  for (let p = 100; p <= 200; p++) prof.onTrade({ ts: T0 + p, price: p, qty: 1, aggressorBuy: true });
  prof.onTrade({ ts: T0 + 999, price: 150, qty: 1, aggressorBuy: false });
  const pr = prof.profile();

  assert.strictEqual(pr.poc, 150, 'POC must be the max-volume level');
  assert.ok(Number.isFinite(pr.vah) && Number.isFinite(pr.val), 'VAH/VAL finite');
  assert.ok(pr.vah >= pr.poc && pr.poc >= pr.val, 'VAH ≥ POC ≥ VAL');
  assert.strictEqual(pr.totalVol, 102, 'total session volume');
  assert.strictEqual(pr.levels.length, 101, 'one level per integer price');
  for (let i = 1; i < pr.levels.length; i++) assert.ok(pr.levels[i].price > pr.levels[i - 1].price, 'levels ascending');

  // Value-area volume: sum of levels inside [VAL, VAH] vs total → ≈70% ±5pp.
  const vaVol = pr.levels.reduce((a, l) => a + (l.price >= pr.val && l.price <= pr.vah ? l.vol : 0), 0);
  const frac = vaVol / pr.totalVol;
  assert.ok(frac >= 0.65 && frac <= 0.75, 'value-area volume ' + (100 * frac).toFixed(1) + '% outside 70%±5pp');
  // POC is a strict local max above the median → must appear as an HVN candidate.
  assert.ok(pr.hvn.includes(150), 'POC level qualifies as an HVN candidate');
});

// ─── 10. AggBookStore: two-exchange merge — total == Σ byEx ──────────────────
group('agg book two-exchange merge math', () => {
  const agg = S.AggBookStore(['bybit', 'binancef']);
  // Feed REAL normalized depth from both venues through their adapters.
  {
    const { evts, sink } = collectSink();
    const ad = A.makeBybitAdapter('BTCUSDT', sink);
    replay(ad, FX.bybit_orderbook_snapshot);
    replay(ad, FX.bybit_orderbook_delta);
    for (const ev of evts) if (ev.kind === 'depth') agg.applyDepth(ev);
  }
  {
    const { evts, sink } = collectSink();
    const ad = A.makeBinanceDepthAdapter('BTCUSDT', sink);
    replay(ad, FX.binancef_depth20);
    for (const ev of evts) agg.applyDepth(ev);
  }

  // Tick 50 makes the venues' books share $-buckets (bybit ~61855, binance
  // ~61883 both land in the 61850 bid bucket) so byEx really has 2 keys.
  const g = agg.grouped(50, 10);
  assert.ok(g.bids.length > 0 && g.asks.length > 0, 'merged ladder non-empty');
  let sawMultiEx = false;
  for (const rows of [g.bids, g.asks]) {
    for (const row of rows) {
      const sum = Object.values(row.byEx).reduce((a, q) => a + q, 0);
      assert.ok(approx(row.total, sum, 1e-9), 'row @' + row.price + ': total ' + row.total + ' != Σ byEx ' + sum);
      if (Object.keys(row.byEx).length >= 2) sawMultiEx = true;
    }
  }
  assert.ok(sawMultiEx, 'at least one merged bucket must carry BOTH exchanges (else the merge was never exercised)');
  for (let i = 1; i < g.bids.length; i++) assert.ok(g.bids[i].price < g.bids[i - 1].price, 'merged bids best-first');
  for (let i = 1; i < g.asks.length; i++) assert.ok(g.asks[i].price > g.asks[i - 1].price, 'merged asks best-first');

  // Per-venue totals must be conserved through the merge (nothing invented,
  // nothing dropped): Σ byEx.bybit over all bid rows == bybit book's own
  // grouped bid total at the same tick/level count.
  const bybitOwn = agg.books.get('bybit').grouped(50, 10).bids.reduce((a, r) => a + r.qty, 0);
  const bybitMerged = g.bids.reduce((a, r) => a + (r.byEx.bybit || 0), 0);
  assert.ok(approx(bybitOwn, bybitMerged, 1e-9), 'bybit bid quantity conserved through the merge');
});

// ─── 11. OKX adapter: descriptor contract + trade ctVal math (§4b) ──────────
group('okx descriptor + trade ctVal math (CONTRACTS → BTC)', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeOkxAdapter('BTC-USDT-SWAP', sink);
  const api = countingApi();

  // Descriptor contract (§4b): OKX prescribes a PLAIN TEXT 'ping' ≲30s — a
  // JSON op frame (Bybit-style) would be ignored server-side and the socket
  // would die at the idle timeout.
  assert.strictEqual(ad.url, 'wss://ws.okx.com:8443/ws/v5/public');
  assert.strictEqual(ad.pingMs, 25000, 'keepalive must beat the ~30s idle drop');
  {
    const { sent, ws } = captureWs();
    ad.ping(ws);
    assert.strictEqual(sent[0], 'ping', "keepalive must be the literal text 'ping', NOT JSON");
    ad.subscribe(ws);
    const sub = sent[1];
    assert.strictEqual(sub.op, 'subscribe');
    const chans = sub.args.map((a) => a.channel).sort();
    assert.deepStrictEqual(chans, ['books', 'trades'], 'subscribe books + trades (§4b)');
    for (const a of sub.args) assert.strictEqual(a.instId, 'BTC-USDT-SWAP');
  }

  // Sub-ack (fixture okx_sub_ack: {event:'subscribe',…}) carries no data →
  // swallowed, and it must NOT count as liveness (an ack is not a data frame).
  for (const f of FX.okx_sub_ack) ad.onMessage(f, api);
  assert.strictEqual(evts.length, 0, 'sub ack must emit nothing');
  assert.strictEqual(api.alive, 0, 'sub ack must not markAlive');

  // Fixture precondition for the §4b headline assertion: the capture really
  // does carry a sz "200" print (re-captured fixtures must keep exercising it).
  assert.strictEqual(FX.okx_trades[0].data[0].sz, '200', 'fixture precondition: first trade sz must be "200"');

  for (const f of FX.okx_trades) ad.onMessage(f, api);
  assert.strictEqual(evts.length, 3, 'one trade per fixture frame');
  // §4b UNIT RAIL: sizes are CONTRACTS; qty = sz × ctVal (0.01 BTC pinned in
  // fixtures _okx_ctval_note). sz "200" → 2 BTC EXACTLY (200 × 0.01 === 2 in
  // doubles); skipping the multiply would overstate OKX flow 100×.
  assert.strictEqual(evts[0].qty, 2, 'sz "200" × ctVal 0.01 must be EXACTLY 2.00 BTC, got ' + evts[0].qty);
  FX.okx_trades.forEach((f, i) => {
    const raw = f.data[0], ev = evts[i];
    assert.strictEqual(ev.kind, 'trade');
    assert.strictEqual(ev.ex, 'okx');
    // §0.6: OKX `side` is the TAKER (aggressor) — as-is, Bybit family, NO
    // Coinbase-style inversion.
    assert.strictEqual(ev.aggressorBuy, raw.side === 'buy', 'aggressorBuy must equal (side===buy), no inversion');
    assert.ok(Number.isInteger(ev.ts) && ev.ts === Number(raw.ts), 'ts must be the numeric-string wire ts as int ms');
    assert.strictEqual(ev.price, Number(raw.px), 'price must be Number(px)');
    assert.ok(approx(ev.qty, Number(raw.sz) * 0.01, 1e-12), 'qty must be Number(sz) × 0.01');
    assert.strictEqual(ev.id, String(raw.tradeId), 'id must be the tradeId as a string');
  });
  // §4b liveness: EVERY books/trades data frame marks alive — data frames are
  // this socket's only liveness source (the text 'pong' dies in JSON.parse).
  assert.strictEqual(api.alive, 3, 'each trade data frame must markAlive');

  // ctVal is an OPT (another instId = another multiplier): override must win.
  const { evts: e2, sink: s2 } = collectSink();
  const ad2 = A.makeOkxAdapter('BTC-USDT-SWAP', s2, { ctVal: 1 });
  ad2.onMessage(FX.okx_trades[0], nullApi);
  assert.strictEqual(e2[0].qty, 200, 'ctVal override 1 must yield raw contract count');
});

// ─── 12. OKX books snapshot+update → BookStore (ctVal levels + real delete) ──
group('okx books snapshot+update through BookStore (ctVal + tombstone delete)', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeOkxAdapter('BTC-USDT-SWAP', sink);
  const api = countingApi();
  const book = S.BookStore();
  const CT = 0.01;   // pinned BTC-USDT-SWAP ctVal (fixtures _okx_ctval_note)

  // Fixture preconditions: the snapshot carries checksum/seqId (the adapter
  // IGNORES them by design — re-snapshot-on-reconnect instead, §4b — so the
  // fields must exist for that to be a decision rather than a vacuous no-op),
  // and update[1] carries a REAL sz-"0" tombstone for a level the snapshot
  // holds (bid 62009.2) — no synthesized delete needed, the wire provided one.
  const rawSnap = FX.okx_books_snapshot[0].data[0];
  assert.ok('checksum' in rawSnap && 'seqId' in rawSnap, 'fixture precondition: snapshot carries checksum/seqId');
  assert.ok(rawSnap.bids.some((l) => l[0] === '62009.2'),
    'fixture precondition: snapshot holds bid 62009.2');
  assert.ok(FX.okx_books_update[1].data[0].bids.some((l) => l[0] === '62009.2' && l[1] === '0'),
    'fixture precondition: update[1] deletes bid 62009.2 with sz "0"');

  // Snapshot: action:'snapshot' → isSnapshot:true; 25 levels/side, sorted
  // best-first, EVERY level qty ctVal-scaled (books sz is CONTRACTS too, §4b).
  for (const f of FX.okx_books_snapshot) ad.onMessage(f, api);
  assert.strictEqual(evts.length, 1);
  const snap = evts[0];
  assert.strictEqual(snap.kind, 'depth');
  assert.strictEqual(snap.ex, 'okx');
  assert.strictEqual(snap.isSnapshot, true, "action:'snapshot' must map to isSnapshot:true");
  assert.ok(Number.isInteger(snap.ts) && snap.ts === Number(rawSnap.ts), 'ts from the books row');
  assert.strictEqual(snap.bids.length, 25);
  assert.strictEqual(snap.asks.length, 25);
  for (let i = 1; i < 25; i++) {
    assert.ok(snap.bids[i][0] < snap.bids[i - 1][0], 'bids descending (best first)');
    assert.ok(snap.asks[i][0] > snap.asks[i - 1][0], 'asks ascending (best first)');
  }
  // Cross-check EVERY emitted level against its raw 4-tuple: [px, sz, deprecated,
  // nOrders] → [Number(px), Number(sz)×ctVal] (tuple tail ignored).
  const rawBidBySz = new Map(rawSnap.bids.map((l) => [Number(l[0]), Number(l[1])]));
  for (const [p, q] of snap.bids) {
    assert.ok(rawBidBySz.has(p), 'emitted bid ' + p + ' not in the raw snapshot');
    assert.ok(approx(q, rawBidBySz.get(p) * CT, 1e-12), 'bid @' + p + ' qty must be sz × ctVal');
  }

  book.applyDepth(snap);
  let b = book.best();
  assert.strictEqual(b.bid[0], 62009.9, 'fixture best bid');
  assert.strictEqual(b.ask[0], 62010, 'fixture best ask');
  assert.ok(approx(b.bid[1], 883.58 * CT, 1e-12), 'best-bid qty ctVal-scaled (8.8358 BTC, not 883.58)');
  assert.ok(book.bids.has(62009.2), 'level 62009.2 present pre-update');

  // Update 0: action:'update' → isSnapshot:false (no clear); upserts merge.
  evts.length = 0;
  ad.onMessage(FX.okx_books_update[0], api);
  assert.strictEqual(evts[0].isSnapshot, false, "action:'update' must map to isSnapshot:false");
  book.applyDepth(evts[0]);
  assert.ok(approx(book.asks.get(62010), 250.44 * CT, 1e-12), 'update must upsert ask 62010 (ctVal-scaled)');
  assert.ok(book.bids.has(62008.7), 'update 0 adds bid 62008.7');

  // Update 1 carries the REAL ["62009.2","0",…] tombstone — the adapter must
  // KEEP it (qty 0, 0×ctVal is still 0) and the store must DELETE the level.
  evts.length = 0;
  ad.onMessage(FX.okx_books_update[1], api);
  assert.ok(evts[0].bids.some((l) => l[0] === 62009.2 && l[1] === 0),
    'adapter must keep the sz-"0" tombstone (qty 0) for the store');
  book.applyDepth(evts[0]);
  assert.ok(!book.bids.has(62009.2), 'sz "0" must DELETE bid 62009.2 store-side');
  assert.ok(!book.bids.has(62008.7), 'bid 62008.7 added by update 0 then deleted by update 1');

  // Update 2 keeps the book sane; ask 62011 (in the snapshot) is deleted here.
  evts.length = 0;
  ad.onMessage(FX.okx_books_update[2], api);
  book.applyDepth(evts[0]);
  assert.ok(!book.asks.has(62011), 'update 2 deletes ask 62011');
  b = book.best();
  assert.ok(Number.isFinite(b.bid[0]) && Number.isFinite(b.ask[0]) && b.bid[0] < b.ask[0], 'best() sane after all updates');
  assert.ok(approx(book.bids.get(62009.9), 614.58 * CT, 1e-12), 'best bid qty tracks the last update');

  // §4b liveness: every books data frame (snapshot + 3 updates) marks alive.
  assert.strictEqual(api.alive, 4, 'each books data frame must markAlive');
});

// ─── 13. Bybit orderbook.200 (§4b upgrade): subscribe arg + 200-level sanity ─
group('bybit orderbook.200 subscribe + snapshot/delta through BookStore', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBybitAdapter('BTCUSDT', sink);

  // The §4b upgrade is in the SUBSCRIBE (deeper heatmap range); snapshot/delta
  // semantics are identical at any depth and share one 'orderbook.' route.
  {
    const { sent, ws } = captureWs();
    ad.subscribe(ws);
    const args = sent[0].args;
    assert.ok(args.indexOf('orderbook.200.BTCUSDT') >= 0, 'must subscribe orderbook.200 (§4b)');
    assert.ok(!args.some((a) => a.indexOf('orderbook.50.') === 0), 'the .50 subscription must be GONE');
  }

  // Real captured .200 frames: full 200 levels/side, then sparse deltas.
  const book = S.BookStore();
  replay(ad, FX.bybit_orderbook200_snapshot);
  assert.strictEqual(evts.length, 1, 'one depth event from the snapshot');
  const snap = evts[0];
  assert.strictEqual(snap.isSnapshot, true);
  assert.strictEqual(snap.bids.length, 200, '200 bid levels');
  assert.strictEqual(snap.asks.length, 200, '200 ask levels');
  for (let i = 1; i < 200; i++) {
    assert.ok(snap.bids[i][0] < snap.bids[i - 1][0], 'bids descending (best first)');
    assert.ok(snap.asks[i][0] > snap.asks[i - 1][0], 'asks ascending (best first)');
  }
  book.applyDepth(snap);
  assert.strictEqual(book.bids.size, 200, 'store holds all 200 bid levels');
  assert.strictEqual(book.asks.size, 200, 'store holds all 200 ask levels');
  let b = book.best();
  assert.strictEqual(b.bid[0], 62011.8, 'fixture best bid');
  assert.strictEqual(b.ask[0], 62011.9, 'fixture best ask');
  assert.ok(book.bids.has(62011.3) && book.asks.has(62037.5), 'delta-targeted levels present pre-delta');

  // Deltas: tombstones deep in a 200-level book must still delete store-side.
  evts.length = 0;
  replay(ad, FX.bybit_orderbook200_delta);
  for (const ev of evts) {
    assert.strictEqual(ev.isSnapshot, false, 'type:delta must map to isSnapshot:false');
    book.applyDepth(ev);
  }
  assert.ok(!book.bids.has(62011.3), 'delta ["62011.30","0"] must delete the bid');
  assert.ok(!book.asks.has(62037.5), 'delta must delete ask 62037.50 (the former 200th level)');
  b = book.best();
  assert.ok(Number.isFinite(b.bid[0]) && Number.isFinite(b.ask[0]) && b.bid[0] < b.ask[0], 'best() sane after deltas');
});

// ─── 14. DepthHistoryStore: guard, ring bound, velocity sign (§4b) ──────────
group('depth history ring bound + velocity sign', () => {
  const T0 = 1783076400000;
  const book = S.BookStore();
  const hist = S.DepthHistoryStore({ tickSize: 1, maxSamples: 5, nLevels: 40 });

  // Empty-book guard (§4b): sampling before any snapshot records NOTHING —
  // an all-zero column would render as a fake "liquidity vanished" band.
  hist.sample(T0, book);
  assert.strictEqual(hist.samples().length, 0, 'empty book must not produce a sample');
  assert.ok(Number.isNaN(hist.priceRange().min) && Number.isNaN(hist.priceRange().max), 'empty range is NaN/NaN, never 0');

  // Constructed FILL at bid 100: qty ramps 1→8 over 8 one-second samples.
  // maxSamples=5 → the ring must hold ONLY the newest 5 (bound by construction).
  for (let i = 0; i < 8; i++) {
    book.applyDepth({
      kind: 'depth', ex: 'bybit', ts: T0 + i * 1000, isSnapshot: true,
      bids: [[100, 1 + i]], asks: [[101, 2]],
    });
    hist.sample(T0 + i * 1000, book);
  }
  const smp = hist.samples();
  assert.strictEqual(smp.length, 5, 'ring bound: 8 samples in, 5 held');
  assert.strictEqual(smp[0].ts, T0 + 3000, 'oldest 3 evicted (oldest→newest order)');
  assert.strictEqual(smp[4].ts, T0 + 7000, 'newest sample kept');
  assert.strictEqual(smp[4].bids.get(100), 8, 'grouped bid qty recorded');
  const r = hist.priceRange();
  assert.strictEqual(r.min, 100); assert.strictEqual(r.max, 101);

  // Velocity over the full held window: (8 − 4) qty / 4 s = +1/s — POSITIVE
  // on a fill (liquidity building).
  assert.ok(approx(hist.velocity(100, 10000), 1), 'fill must read as positive velocity, got ' + hist.velocity(100, 10000));
  // <2 samples in a tiny window → 0 ("unknown renders flat, never NaN").
  assert.strictEqual(hist.velocity(100, 1), 0, 'sub-sample window must return 0');

  // Constructed PULL: qty collapses 8 → 0.5 → NEGATIVE velocity. Also checks
  // the absent-bucket-is-zero rule via ask 101 disappearing entirely.
  book.applyDepth({
    kind: 'depth', ex: 'bybit', ts: T0 + 8000, isSnapshot: true,
    bids: [[100, 0.5]], asks: [[102, 1]],
  });
  hist.sample(T0 + 8000, book);
  assert.ok(hist.velocity(100, 4000) < 0, 'pull must read as negative velocity');
  assert.ok(hist.velocity(101, 4000) < 0, 'a bucket that vanished counts as qty 0 (its deletion IS the signal)');
});

// ─── 15. SpoofIcebergDetector: fires on pull + refill, quiet on benign (§4b) ─
group('spoof/iceberg detector fires on constructed pull+refill, quiet on benign', () => {
  const T0 = 1783076400000;
  // Grouped-ladder builders (BookStore.grouped() shape: [{price, qty}]).
  const rows = (pairs) => pairs.map(([price, qty]) => ({ price, qty }));
  const baseBids = () => rows([[100, 1], [99, 1.1], [98, 0.9], [97, 1], [96, 1.05],
    [94, 1], [93, 0.95], [92, 1], [91, 1.1], [90, 1]]);
  const baseAsks = () => rows([[101, 1], [102, 1.2], [103, 0.9], [104, 1]]);

  // (a) SPOOF-PULL: a 20 BTC wall at bid 95 (≥ wallK=8 × median≈1) appears at
  // T0, then VANISHES 5s later with ZERO traded volume there — §4b verbatim:
  // pulled within wallWindowMs (15s) and traded < tradeCoverPct (0.2) × wall.
  // 95 stays inside the still-visible bid range (90..100), so the top-N
  // visibility guard cannot mistake the pull for a scroll-out.
  const det = S.SpoofIcebergDetector({ tickSize: 1 });
  det.onDepthSample(T0, { bids: baseBids().concat(rows([[95, 20]])), asks: baseAsks() });
  det.onDepthSample(T0 + 5000, { bids: baseBids(), asks: baseAsks() });
  const spoofs = det.events().filter((e) => e.kind === 'spoof-pull');
  assert.strictEqual(spoofs.length, 1, 'exactly one spoof-pull, got ' + spoofs.length);
  assert.strictEqual(spoofs[0].price, 95, 'pull at the wall bucket');
  assert.strictEqual(spoofs[0].size, 20, 'size = max displayed wall size');
  assert.strictEqual(spoofs[0].lifetimeMs, 5000, 'lifetime = event-ts span on display');

  // (b) NOT a spoof when the wall was EATEN: same wall, but 5 BTC (≥ 0.2×20)
  // trades at the bucket before it vanishes — real liquidity got filled.
  const det2 = S.SpoofIcebergDetector({ tickSize: 1 });
  det2.onDepthSample(T0, { bids: baseBids().concat(rows([[95, 20]])), asks: baseAsks() });
  det2.onTrade({ kind: 'trade', ex: 'bybit', ts: T0 + 2000, price: 95, qty: 5, aggressorBuy: false, id: 'x1' });
  det2.onDepthSample(T0 + 5000, { bids: baseBids(), asks: baseAsks() });
  assert.strictEqual(det2.events().filter((e) => e.kind === 'spoof-pull').length, 0,
    'a wall consumed by real trades must NOT flag as a spoof');

  // (c) ICEBERG-REFILL: bucket 100 displays max 2 BTC but 7.5 BTC trades there
  // inside icebergWindowMs — traded ≥ icebergM (3) × maxDisplayed (2). The
  // event fires on the crossing trade and re-arms only after a full window
  // (a 4th trade must NOT re-emit).
  const det3 = S.SpoofIcebergDetector({ tickSize: 1 });
  det3.onDepthSample(T0, { bids: rows([[100, 2], [99, 1], [98, 1]]), asks: rows([[101, 1], [102, 1]]) });
  const mkT = (ts, qty) => ({ kind: 'trade', ex: 'bybit', ts, price: 100, qty, aggressorBuy: false, id: String(ts) });
  det3.onTrade(mkT(T0 + 1000, 2.5));   // 2.5 < 6 — quiet
  det3.onTrade(mkT(T0 + 2000, 2.5));   // 5.0 < 6 — quiet
  assert.strictEqual(det3.events().length, 0, 'no iceberg before the 3× threshold');
  det3.onTrade(mkT(T0 + 3000, 2.5));   // 7.5 ≥ 3×2 — fires
  det3.onTrade(mkT(T0 + 4000, 3));     // within the window — must NOT re-fire
  const bergs = det3.events().filter((e) => e.kind === 'iceberg-refill');
  assert.strictEqual(bergs.length, 1, 'exactly one iceberg-refill per bucket per window, got ' + bergs.length);
  assert.strictEqual(bergs[0].price, 100);
  assert.ok(approx(bergs[0].tradedQty, 7.5), 'tradedQty = window sum at the crossing trade');
  assert.strictEqual(bergs[0].maxDisplayed, 2, 'maxDisplayed = max shown size in window');

  // (d) BENIGN book: ordinary jittering ladder + dust trades → ZERO events.
  const det4 = S.SpoofIcebergDetector({ tickSize: 1 });
  for (let i = 0; i < 10; i++) {
    const jit = 0.05 * (i % 3);
    det4.onDepthSample(T0 + i * 1000, {
      bids: baseBids().map((r2) => ({ price: r2.price, qty: r2.qty + jit })),
      asks: baseAsks(),
    });
    det4.onTrade({ kind: 'trade', ex: 'bybit', ts: T0 + i * 1000 + 500, price: 100, qty: 0.2, aggressorBuy: true, id: 'b' + i });
  }
  assert.strictEqual(det4.events().length, 0, 'benign book must stay silent, got ' + det4.events().length + ' event(s)');

  // §4b label rail: EVERY emitted event carries label:'heuristic' — the label
  // rides the event itself so no view can drop it by accident.
  for (const d of [det, det3]) {
    for (const e of d.events()) assert.strictEqual(e.label, 'heuristic', "every detector event must carry label:'heuristic'");
  }
});

// ─── 16. LiqHeatmapModel: exact band math + sides + 'estimated' label (§4b) ──
group('liq heatmap model exact band math + estimated label', () => {
  // §4b known-value case: entry 100, L 10, mmr 0.005 →
  //   long-liq  = 100·(1 − 1/10 + 0.005) =  90.5  (BELOW entry/mark)
  //   short-liq = 100·(1 + 1/10 − 0.005) = 109.5  (ABOVE entry/mark)
  // tick 0.5 keeps both on-grid so the snap must not move them.
  const model = S.LiqHeatmapModel({ tiers: [10], mmr: 0.005, tickSize: 0.5 });
  const obs = [{ ts: 1783076400000, price: 99, side: 'long', qty: 1, notionalUsd: 99 }];
  const est = model.estimate([{ price: 100, vol: 5 }], 100, obs);

  assert.strictEqual(est.label, 'estimated', "model output must carry label:'estimated' (§0.4)");
  assert.strictEqual(est.bands.length, 2, 'one long + one short band');
  const [lo, hi] = est.bands;   // ascending by price
  assert.strictEqual(lo.price, 90.5, 'long-liq band EXACTLY at 90.5, got ' + lo.price);
  assert.strictEqual(lo.side, 'long');
  assert.strictEqual(hi.price, 109.5, 'short-liq band EXACTLY at 109.5, got ' + hi.price);
  assert.strictEqual(hi.side, 'short');
  assert.strictEqual(lo.weight, 1, 'single entry level → both bands at max weight 1');
  assert.strictEqual(hi.weight, 1);

  // Observed prints pass through BY REFERENCE — never blended into bands, and
  // never mutated (§4b: estimates and observations must not be confusable).
  assert.strictEqual(est.observed.length, 1);
  assert.strictEqual(est.observed[0], obs[0], 'observed prints must be the SAME objects, unblended');

  // Liveness filter across the full default tier set + several entry levels:
  // EVERY surviving long band sits strictly BELOW mark, every short strictly
  // ABOVE (a long whose liq price is at/above mark has already fired — §4b).
  const model2 = S.LiqHeatmapModel({ tickSize: 1 });   // tiers [5,10,25,50,100], mmr 0.005
  const levels = [{ price: 61000, vol: 3 }, { price: 62000, vol: 5 }, { price: 63000, vol: 2 }];
  const mark = 62000;
  const est2 = model2.estimate(levels, mark, []);
  assert.ok(est2.bands.length > 0, 'real profile must produce bands');
  let wmax = 0;
  for (const bd of est2.bands) {
    if (bd.side === 'long') assert.ok(bd.price < mark, 'long band ' + bd.price + ' must be strictly below mark');
    else assert.ok(bd.price > mark, 'short band ' + bd.price + ' must be strictly above mark');
    assert.ok(bd.weight > 0 && bd.weight <= 1, 'weights normalized to (0, 1]');
    if (bd.weight > wmax) wmax = bd.weight;
  }
  assert.strictEqual(wmax, 1, 'max band weight must normalize to exactly 1');
  for (let i = 1; i < est2.bands.length; i++) {
    assert.ok(est2.bands[i].price > est2.bands[i - 1].price, 'bands ascending by price');
  }
  assert.strictEqual(est2.label, 'estimated');

  // No mark → NO bands (never NaN bands), observed still passes through.
  const estNaN = model2.estimate(levels, NaN, obs);
  assert.strictEqual(estNaN.bands.length, 0, 'non-finite mark must yield empty bands');
  assert.strictEqual(estNaN.observed.length, 1);
  assert.strictEqual(estNaN.label, 'estimated');
});

// ─── 17. Bybit REST klines: NEWEST-FIRST reversal + exact fixture numbers ────
group('bybit REST kline normalizer reversal (exact fixture numbers)', () => {
  const rawList = FX.bybit_rest_kline.result.list;
  // Fixture precondition (§4c gotcha this whole group pins): the capture
  // really is NEWEST-FIRST — a re-captured fixture that arrived chronological
  // would let a reversal-dropping regression pass silently.
  assert.ok(Number(rawList[0][0]) > Number(rawList[rawList.length - 1][0]),
    'fixture precondition: bybit kline list must be NEWEST-FIRST');

  const before = JSON.stringify(FX.bybit_rest_kline);
  const bars = H.normalizeBybitKlines(FX.bybit_rest_kline);
  // The normalizer iterates backwards instead of slice().reverse() so replays
  // can reuse the cached fixture — pin that the input really is untouched.
  assert.strictEqual(JSON.stringify(FX.bybit_rest_kline), before, 'normalizer must NOT mutate its input');

  assert.strictEqual(bars.length, 5, 'one bar per fixture row');
  for (let i = 1; i < bars.length; i++) {
    assert.ok(bars[i].ts > bars[i - 1].ts, 'bars must come out CHRONOLOGICAL (oldest → newest)');
  }
  // Exact values, both ends: the fixture's LAST row (oldest) must land FIRST…
  assert.strictEqual(bars[0].ts, 1783112400000, 'oldest wire row must land at index 0');
  assert.strictEqual(bars[0].o, 62748);
  assert.strictEqual(bars[0].h, 62946.1);
  assert.strictEqual(bars[0].v, 1783.151);
  // …and the fixture's FIRST row (newest) must land LAST, fully Number()ed.
  const newest = bars[4];
  assert.strictEqual(newest.ts, 1783119600000, 'newest wire row must land at the end');
  assert.strictEqual(newest.o, 62542.2);
  assert.strictEqual(newest.h, 62578.2);
  assert.strictEqual(newest.l, 62516.5);
  assert.strictEqual(newest.c, 62537.1);
  assert.strictEqual(newest.v, 59.008);
  for (const b of bars) {
    for (const k of ['ts', 'o', 'h', 'l', 'c', 'v']) {
      assert.ok(typeof b[k] === 'number' && Number.isFinite(b[k]), k + ' must be a finite Number (wire sends strings)');
    }
  }

  // Bybit errors keep HTTP 200 — retCode is the real status → null (so the
  // fetch wrapper's silent-null contract holds end-to-end).
  assert.strictEqual(H.normalizeBybitKlines(Object.assign({}, FX.bybit_rest_kline, { retCode: 10001 })), null,
    'retCode !== 0 must yield null');
  assert.strictEqual(H.normalizeBybitKlines(null), null);
  assert.strictEqual(H.normalizeBybitKlines({ retCode: 0, result: {} }), null, 'missing list must yield null');

  // One malformed row is DROPPED (never a NaN bar), the rest of the history survives.
  const mangled = JSON.parse(before);
  mangled.result.list[2][3] = 'not-a-number';
  const survived = H.normalizeBybitKlines(mangled);
  assert.strictEqual(survived.length, 4, 'exactly the NaN row dropped, 4 bars kept');
  assert.ok(!survived.some((b) => b.ts === 1783116000000), 'the mangled row is the one missing');
});

// ─── 18. buildTpo: constructed 30m bars — POC/VA/singles/IB exact (§4c) ──────
group('buildTpo constructed bars: POC/VA/singles/IB exact + UTC sessions newest-first', () => {
  const P = 1800000;   // 30 m — the classical TPO period
  // Hand-derivable session on UTC day 0 (tick 1), plus one bar on day 1 so
  // the per-UTC-day split and newest-first session order are both exercised.
  //   period 0: 100..104  → rows 100-104 get letter 0
  //   period 1: 102..106  → rows 102-106 get letter 1
  //   period 2: 103..110  → rows 103-110 get letter 2
  // Row counts (ascending 100..110): [1,1,2,3,3,2,2,1,1,1,1], total 18.
  const bars = [
    { ts: 0,        o: 100, h: 104, l: 100, c: 104, v: 10 },
    { ts: P,        o: 104, h: 106, l: 102, c: 106, v: 5 },
    { ts: 2 * P,    o: 106, h: 110, l: 103, c: 110, v: 2 },
    { ts: 86400000, o: 200, h: 201, l: 200, c: 201, v: 1 },   // next UTC day
  ];
  const sessions = S.buildTpo(bars, { tickSize: 1, periodMs: P });

  assert.strictEqual(sessions.length, 2, 'one session per UTC day');
  assert.strictEqual(sessions[0].date, '1970-01-02', 'sessions newest-first ([0] = latest day)');
  assert.strictEqual(sessions[1].date, '1970-01-01');

  const s0 = sessions[1];   // the constructed 3-period day
  assert.deepStrictEqual(s0.rows.map((r) => r.price),
    [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110], 'rows ascending, one per tick touched');
  assert.deepStrictEqual(s0.rows.map((r) => r.periods.length),
    [1, 1, 2, 3, 3, 2, 2, 1, 1, 1, 1], 'per-row TPO counts (each bar letters its FULL l..h range)');
  assert.deepStrictEqual(s0.rows[3].periods, [0, 1, 2], 'period indices ascending within a row');

  // POC: rows 103 and 104 tie at 3 letters — the classical tiebreak goes to
  // the row closest to the session mid (105), so 104 must win, EXACTLY.
  assert.strictEqual(s0.poc, 104, 'count tie must break toward the session mid');
  // 70% expansion on counts (target 12.6 of 18), hand-traced: absorb 103(3),
  // 105(2), 106(2), 102(2) → covered 12 < 12.6, then the 107-vs-101 tie (1 vs
  // 1) expands UPWARD → 107, covered 13 → VA rows = 102..107. Same algorithm
  // as ProfileStore (shared valueArea70 helper).
  assert.strictEqual(s0.vah, 107, 'VAH exact');
  assert.strictEqual(s0.val, 102, 'VAL exact');

  // Singles: 1-letter rows STRICTLY INSIDE the session range — 101 and
  // 107..109 qualify; the extremes 100/110 are tails BY CONSTRUCTION and must
  // NOT be flagged (§4c interior-only rule).
  assert.deepStrictEqual(s0.singles, [101, 107, 108, 109], 'interior single prints exact (edges excluded)');

  // IB = range of the first 2 OBSERVED periods (0 and 1): hi 106, lo 100 —
  // raw bar extremes, not bucketed rows.
  assert.deepStrictEqual(s0.ib, { hi: 106, lo: 100 }, 'initial balance exact');

  // Hygiene: malformed bars are SKIPPED, never zero-coerced.
  const dirty = S.buildTpo([bars[0], { ts: NaN, l: 1, h: 2 }, { ts: 0, l: 105, h: 104 }], { tickSize: 1, periodMs: P });
  assert.strictEqual(dirty.length, 1, 'NaN-ts and h<l bars dropped');
  assert.strictEqual(dirty[0].rows.length, 5, 'only the valid bar contributed rows');
});

// ─── 19. buildKlineVp: volume conservation + POC tie + 'bar-range' label ─────
group('buildKlineVp volume conservation + bar-range approximation label', () => {
  // Constructed exact case (tick 1): bar 1 spreads v=10 over 5 buckets
  // (2 each), bar 2 puts v=3 entirely on 102 → levels [2,2,5,2,2].
  const bars = [
    { ts: 0,     o: 100, h: 104, l: 100, c: 104, v: 10 },
    { ts: 60000, o: 102, h: 102, l: 102, c: 102, v: 3 },
  ];
  const vp = S.buildKlineVp(bars, { tickSize: 1 });
  // §4c LABEL RAIL: the approximation label rides the RETURN VALUE itself so
  // no view can drop it by accident — and it must be the exact token the
  // KlineVpView badge renders.
  assert.strictEqual(vp.approx, 'bar-range', "return value must carry approx:'bar-range'");
  assert.deepStrictEqual(vp.levels.map((l) => l.price), [100, 101, 102, 103, 104], 'levels ascending');
  assert.ok(approx(vp.levels[2].vol, 5), 'level 102 = 2 (spread) + 3 (point bar)');
  assert.ok(approx(vp.levels.reduce((a, l) => a + l.vol, 0), 13), 'Σ levels.vol == Σ bars.v (13) on the constructed case');
  assert.strictEqual(vp.poc, 102);
  assert.strictEqual(vp.vah, 104); assert.strictEqual(vp.val, 101, '70% expansion exact');
  assert.deepStrictEqual(vp.hvns, [102], 'the 5-vs-2 spike clears the 25%-of-median prominence gate');
  assert.deepStrictEqual(vp.lvns, [], 'flat shoulders must NOT flag as LVNs');

  // CONSERVATION on the REAL fixture bars through the REAL normalizer (messy
  // floats: v like 1783.151 split over multi-bucket ranges): the uniform
  // spread must neither invent nor lose volume — Σ levels ≡ Σ bars ≤ 1e-9.
  const fbars = H.normalizeBybitKlines(FX.bybit_rest_kline);
  const fvp = S.buildKlineVp(fbars, { tickSize: 10 });
  const fsum = fvp.levels.reduce((a, l) => a + l.vol, 0);
  const vsum = fbars.reduce((a, b) => a + b.v, 0);
  assert.ok(Math.abs(fsum - vsum) <= 1e-9,
    'fixture-bar conservation: Σ levels.vol ' + fsum + ' != Σ bars.v ' + vsum);
  assert.strictEqual(fvp.approx, 'bar-range', 'label present on real data too');

  // POC tie → LOWEST price (ProfileStore convention, deterministic).
  const tie = S.buildKlineVp([
    { ts: 0, o: 105, h: 105, l: 105, c: 105, v: 4 },
    { ts: 1, o: 101, h: 101, l: 101, c: 101, v: 4 },
  ], { tickSize: 1 });
  assert.strictEqual(tie.poc, 101, 'POC tie must resolve to the LOWEST price');

  // Empty input: NaN sentinels (no data must never look like price 0) and the
  // label STILL present — a view rendering the empty state keeps its badge.
  const empty = S.buildKlineVp([], { tickSize: 1 });
  assert.ok(empty.levels.length === 0 && Number.isNaN(empty.poc) && Number.isNaN(empty.vah) && Number.isNaN(empty.val));
  assert.strictEqual(empty.approx, 'bar-range');
});

// ─── 20. rollingCorr: ±identical = ±1, small-n NaN, NaN pairs skipped ────────
group('rollingCorr identical=+1 / inverted=−1 / short-window NaN', () => {
  const x = [0.01, -0.02, 0.015, 0.005, -0.01, 0.02, -0.005, 0.012];
  const w = 4;

  const same = S.rollingCorr(x, x, w);
  assert.strictEqual(same.length, x.length, 'one output per aligned index');
  assert.strictEqual(same[0].i, 0, 'outputs carry their index');
  // Small-n honesty: index 0 holds ONE valid pair < w/2 → NaN, never a
  // confident-looking number.
  assert.ok(Number.isNaN(same[0].r), 'below window/2 valid pairs r must be NaN');
  for (let i = w - 1; i < x.length; i++) {
    assert.ok(approx(same[i].r, 1, 1e-12), 'identical series must read +1 on every full window (i=' + i + ')');
  }

  const inv = S.rollingCorr(x, x.map((v) => -v), w);
  for (let i = w - 1; i < x.length; i++) {
    assert.ok(approx(inv[i].r, -1, 1e-12), 'inverted series must read −1 on every full window (i=' + i + ')');
  }

  // A NaN pair is SKIPPED — the window correlates the remaining pairs instead
  // of zero-coercing (a fabricated flat return would fake decorrelation).
  const y = x.slice(); y[5] = NaN;
  const sk = S.rollingCorr(x, y, w);
  assert.ok(approx(sk[6].r, 1, 1e-12), 'window spanning the NaN pair must skip it and still read +1');
  assert.ok(approx(sk[7].r, 1, 1e-12));

  // Refusals: a 1-sample "correlation" is undefined; non-arrays have no rows.
  assert.deepStrictEqual(S.rollingCorr(x, x, 1), [], 'window < 2 must be refused');
  assert.deepStrictEqual(S.rollingCorr(null, x, w), [], 'non-array input must yield []');
});

// ─── 21. OKX REST funding + OI normalizers vs fixtures (§4c) ─────────────────
group('okx REST funding (intervalH derivation) + OI (oiCcy unit rail) normalizers', () => {
  // Funding: nextFundingTs must be OKX `fundingTime` — the UPCOMING settlement
  // (naming gotcha: OKX `nextFundingTime` is the one AFTER that) — and
  // intervalH is DERIVED from the fundingTime→nextFundingTime spacing.
  const before = JSON.stringify(FX.okx_rest_funding);
  const f = H.normalizeOkxFunding(FX.okx_rest_funding);
  assert.strictEqual(JSON.stringify(FX.okx_rest_funding), before, 'normalizer must NOT mutate its input');
  assert.strictEqual(f.fundingRate, 0.0000387369202921, 'fundingRate exact (Number of the wire string)');
  assert.strictEqual(f.nextFundingTs, 1783123200000, 'nextFundingTs = fundingTime (upcoming settlement), NOT nextFundingTime');
  assert.strictEqual(f.intervalH, 8, 'intervalH derived from the response spacing = 8h exactly');

  // Fallback path (§4c): degenerate/absent spacing → the stated 8h fallback
  // (never NaN — the annualization column divides by intervalH).
  const clone = JSON.parse(before);
  clone.data[0].nextFundingTime = '';
  assert.strictEqual(H.normalizeOkxFunding(clone).intervalH, 8, 'missing nextFundingTime must fall back to 8');
  assert.strictEqual(H.normalizeOkxFunding({ code: '51000', msg: 'err', data: [] }), null, "code !== '0' must yield null");
  assert.strictEqual(H.normalizeOkxFunding({ code: '0', data: [] }), null, 'missing row must yield null');

  // OI unit rail (§4b ctVal gotcha, REST edition): the raw `oi` field is
  // CONTRACTS; `oiCcy` is COIN. Fixture precondition first, so a re-capture
  // where the two fields agree can never vacuously pass the mixup check.
  const raw = FX.okx_rest_oi.data[0];
  assert.ok(approx(Number(raw.oi) * 0.01, Number(raw.oiCcy), 1e-6),
    'fixture precondition: oi(contracts) × ctVal 0.01 == oiCcy(coin)');
  const o = H.normalizeOkxOi(FX.okx_rest_oi);
  assert.strictEqual(o.oi, 31337.2794000001118, 'oi must be oiCcy (COIN) — exact fixture number');
  assert.notStrictEqual(o.oi, Number(raw.oi), 'returning the CONTRACTS field would overstate OKX OI 100×');
  assert.strictEqual(o.oiUsd, Number(raw.oiUsd), 'oiUsd passed through Number()ed');
  assert.ok(approx(o.oiUsd, 1959817785.0363069919161, 1e-3), 'oiUsd ≈ the captured $1.96B');
  assert.strictEqual(o.ts, 1783120004270, 'ts = wire numeric-string ms as int');
  assert.strictEqual(H.normalizeOkxOi({ code: '1', data: [] }), null, "code !== '0' must yield null");
});

// ─── 22. HL mids normalizer: the SPX-memecoin guard (§4c, honesty-critical) ──
group('hl mids normalizer memecoin guard (main-universe SPX must never surface)', () => {
  // Synthetic allMids-shaped payload — HIP-3 dexs expose LIVE mids only, so no
  // captured fixture exists (fixtures _o3_notes); the shape is the documented
  // flat {name: "mid-string"} object. It deliberately contains the
  // honesty-critical trap: main-universe 'SPX' is the SPX6900 MEMECOIN
  // (~$0.37), NOT the S&P 500 — filtered out BY CONSTRUCTION (only
  // dex-prefixed keys pass), whatever the server includes.
  const wire = {
    SPX: '0.3712',            // the memecoin trap — MUST NOT surface
    BTC: '62000.5',           // main-universe majors don't belong in a dex query either
    'km:US500': '6234.5',
    'km:GOLD': '3412.8',
    'km:USOIL': 'not-a-number',   // NaN mid — dropped, never emitted
    'xyz:XYZ100': '2201.1',
  };
  const km = H.normalizeHlMids(wire, 'km');
  // deepStrictEqual pins the WHOLE object: nothing extra can hide in it.
  assert.deepStrictEqual(km, { 'km:US500': 6234.5, 'km:GOLD': 3412.8 },
    'km query must yield EXACTLY the finite km:-prefixed mids — SPX guarded out, NaN dropped');
  assert.ok(!('SPX' in km), 'the SPX6900 memecoin must NEVER pass a dex-filtered query');

  const xyz = H.normalizeHlMids(wire, 'xyz');
  assert.deepStrictEqual(xyz, { 'xyz:XYZ100': 2201.1 }, 'prefix filter is per-dex');

  // Guard preconditions: no dex → no prefix → the guard cannot hold → null
  // (a permissive fallback would let 'SPX' through the moment dex is '').
  assert.strictEqual(H.normalizeHlMids(wire, ''), null, 'empty dex must yield null');
  assert.strictEqual(H.normalizeHlMids(wire, undefined), null, 'missing dex must yield null');
  assert.strictEqual(H.normalizeHlMids(null, 'km'), null, 'non-object payload must yield null');
  assert.strictEqual(H.normalizeHlMids(['km:US500'], 'km'), null, 'array payload is not an allMids object');
});

// ─── 23. BYOD row→event mapping exactness — all 5 collector tables (§4c) ─────
group('byod row→event mapping exactness (all 5 tables)', () => {
  const T0 = 1783076400123;

  // trades: collector snake_case → §4 camelCase, values UNCHANGED — the §0.6
  // aggressor conventions were applied at RECORD time (normalize_bybit_trade
  // etc.), so re-deriving anything here would double-apply them.
  assert.deepStrictEqual(
    R.byodRowToEvent('trades', {
      exchange: 'bybit', symbol: 'BTCUSDT', trade_id: '6c84…-uuid',
      ts_ms: T0, price: 62000.5, qty: 0.25, aggressor_buy: true,
    }),
    { kind: 'trade', ex: 'bybit', ts: T0, price: 62000.5, qty: 0.25, aggressorBuy: true, id: '6c84…-uuid' },
    'trades row must rename EXACTLY onto the §4 trade event');
  assert.strictEqual(
    R.byodRowToEvent('trades', { exchange: 'coinbase', trade_id: '1', ts_ms: T0, price: 1, qty: 1, aggressor_buy: false }).aggressorBuy,
    false, 'stored false must stay false — NO re-inversion of already-normalized rows');

  // depth_snapshots: JSON-string sides parsed, isSnapshot ALWAYS true (every
  // stored row is a full merged top-20 book — BookStore must replace, not merge).
  assert.deepStrictEqual(
    R.byodRowToEvent('depth_snapshots', {
      exchange: 'binancef', symbol: 'BTCUSDT', ts_ms: T0 + 1,
      bids: '[[62000.1,1.5],[62000,2.25]]', asks: '[[62000.2,0.75]]',
    }),
    { kind: 'depth', ex: 'binancef', ts: T0 + 1, bids: [[62000.1, 1.5], [62000, 2.25]], asks: [[62000.2, 0.75]], isSnapshot: true },
    'depth row must parse both sides and always set isSnapshot:true');
  assert.strictEqual(
    R.byodRowToEvent('depth_snapshots', { exchange: 'binancef', ts_ms: T0, bids: 'not json', asks: '[]' }),
    null, 'corrupt depth JSON must be dropped, never guessed');

  // liquidations: side is ALREADY the liquidated position ('long'|'short') —
  // the §0.6 print-side inversion happened in normalize_bybit_liq; pass through.
  assert.deepStrictEqual(
    R.byodRowToEvent('liquidations', {
      exchange: 'bybit', symbol: 'BTCUSDT', ts_ms: T0 + 2,
      side: 'short', price: 61500, qty: 0.4, notional_usd: 24600,
    }),
    { kind: 'liq', ex: 'bybit', ts: T0 + 2, side: 'short', price: 61500, qty: 0.4, notionalUsd: 24600 },
    'liq row must pass the stored (already-inverted) side through unchanged');

  // funding_mark → §4 'mark' event vocabulary.
  assert.deepStrictEqual(
    R.byodRowToEvent('funding_mark', {
      exchange: 'bybit', symbol: 'BTCUSDT', ts_ms: T0 + 3,
      mark: 62001.1, index: 61998.7, funding_rate: 0.0001, next_funding_ts: 1783094400000,
    }),
    { kind: 'mark', ex: 'bybit', ts: T0 + 3, mark: 62001.1, index: 61998.7, fundingRate: 0.0001, nextFundingTs: 1783094400000 },
    'funding_mark row must rename EXACTLY onto the §4 mark event');

  // open_interest → §4 'oi' event.
  assert.deepStrictEqual(
    R.byodRowToEvent('open_interest', { exchange: 'binancef', symbol: 'BTCUSDT', ts_ms: T0 + 4, oi: 83456.123 }),
    { kind: 'oi', ex: 'binancef', ts: T0 + 4, oi: 83456.123 },
    'open_interest row must rename EXACTLY onto the §4 oi event');

  // Hygiene: a row with no time has no home in any store; unknown tables are
  // seam drift with BYOD_ENDPOINTS — both dropped, never guessed.
  assert.strictEqual(R.byodRowToEvent('trades', { exchange: 'bybit', price: 1, qty: 1 }), null, 'missing ts_ms → null');
  assert.strictEqual(R.byodRowToEvent('not_a_table', { ts_ms: T0 }), null, 'unknown table → null');
  assert.strictEqual(R.byodRowToEvent('trades', null), null, 'null row → null');

  // End-to-end sanity: the mapped events ARE what the real stores consume —
  // run one mapped depth row through BookStore (replace semantics hold).
  const book = S.BookStore();
  book.applyDepth(R.byodRowToEvent('depth_snapshots', {
    exchange: 'binancef', ts_ms: T0, bids: '[[62000.1,1.5]]', asks: '[[62000.2,0.75]]',
  }));
  const b = book.best();
  assert.strictEqual(b.bid[0], 62000.1);
  assert.strictEqual(b.ask[0], 62000.2);
});

// ─── 24. Bybit REST tickers: VWAP proxy + response fundingIntervalHour (§4d) ─
group('bybit REST tickers normalizer (24h-VWAP proxy + response funding interval)', () => {
  const before = JSON.stringify(FX.bybit_rest_tickers);
  const rows = H.normalizeBybitTickers(FX.bybit_rest_tickers);
  assert.strictEqual(JSON.stringify(FX.bybit_rest_tickers), before, 'normalizer must NOT mutate its input');
  assert.strictEqual(rows.length, 6, 'all 6 fixture symbols survive');

  // §4d headline: vwap24h = turnover24h / volume24h to 1e-9, EXACT fixture
  // numbers (the '24h VWAP' proxy the ScreenerView labels).
  const btc = rows.find((r) => r.sym === 'BTCUSDT');
  assert.ok(btc, 'BTCUSDT present');
  const expVwap = 2384597440.4426 / 38072.45;
  assert.ok(approx(btc.vwap24h, expVwap), 'vwap24h must equal turnover24h/volume24h (Δ ≤ 1e-9), got ' + btc.vwap24h);
  assert.strictEqual(btc.last, 63100.30, 'last = Number(lastPrice)');
  assert.ok(approx(btc.vwapDevPct, ((63100.30 - expVwap) / expVwap) * 100, 1e-12), 'vwapDevPct = (last−vwap)/vwap ×100');
  // Wire price24hPcnt is a FRACTION (0.01536) → ×100 to the % the scatter plots.
  assert.ok(approx(btc.pct24h, 1.536, 1e-12), 'pct24h = price24hPcnt × 100');
  // §4d: fundingIntervalHour is RESPONSE-PROVIDED — use it, not the 8h constant.
  assert.strictEqual(btc.fundingIntervalH, 8, 'fundingIntervalH = Number(fundingIntervalHour) from the response');
  assert.strictEqual(btc.fundingRate, 0.00003549, 'fundingRate exact');
  assert.ok(approx(btc.annualizedFundingPct, 0.00003549 * (8760 / 8) * 100, 1e-12),
    'annualized = rate × (8760/intervalH) × 100');
  assert.strictEqual(btc.oiUsd, 3637380173.00, 'oiUsd = openInterestValue');
  assert.strictEqual(btc.mark, 63100.20);
  assert.strictEqual(btc.index, 63118.24);
  // Sign preserved through annualization (XRPUSDT funds negative in the capture).
  const xrp = rows.find((r) => r.sym === 'XRPUSDT');
  assert.strictEqual(xrp.fundingRate, -0.00000458, 'fixture precondition: a negative-funding symbol exists');
  assert.ok(xrp.annualizedFundingPct < 0, 'negative funding must stay negative annualized');

  // Response interval ≠ 8 must be USED (some alts fund 4h/1h — a blanket 8
  // would mis-annualize 2–8×); absent/degenerate interval falls back to 8.
  const alt = JSON.parse(before);
  alt.result.list[1].fundingIntervalHour = '4';
  const btc4 = H.normalizeBybitTickers(alt).find((r) => r.sym === 'BTCUSDT');
  assert.strictEqual(btc4.fundingIntervalH, 4, 'response interval 4 must win over the 8h constant');
  assert.ok(approx(btc4.annualizedFundingPct, 0.00003549 * (8760 / 4) * 100, 1e-12), 'annualization uses the response interval');
  const noFih = JSON.parse(before);
  noFih.result.list[1].fundingIntervalHour = '';
  assert.strictEqual(H.normalizeBybitTickers(noFih).find((r) => r.sym === 'BTCUSDT').fundingIntervalH, 8,
    'absent interval falls back to 8 (stated fallback, not a hidden constant)');

  // Zero volume → vwap/dev NULL (a new/dead listing has no VWAP), row kept.
  const zv = JSON.parse(before);
  zv.result.list[1].volume24h = '0.0000';
  const rz = H.normalizeBybitTickers(zv).find((r) => r.sym === 'BTCUSDT');
  assert.strictEqual(rz.vwap24h, null, 'volume 0 → vwap24h null, never a fabricated 0/0');
  assert.strictEqual(rz.vwapDevPct, null, 'no VWAP → no deviation (null, not 0 — flat lies)');

  // The Number('')===0 trap: Bybit spells "absent" as '' (the fixture's own
  // basisRate/preOpenPrice fields show it) — a blank lastPrice must DROP the
  // row, not plot a price-0 symbol at the origin.
  const blank = JSON.parse(before);
  blank.result.list[0].lastPrice = '';
  const survived = H.normalizeBybitTickers(blank);
  assert.strictEqual(survived.length, 5, 'blank-field row dropped, the other 5 survive');
  assert.ok(!survived.some((r) => r.sym === '1000PEPEUSDT'), 'the blanked symbol is the one missing');
  assert.ok(!survived.some((r) => r.last === 0), 'no row may surface a fake price 0');

  assert.strictEqual(H.normalizeBybitTickers(Object.assign({}, FX.bybit_rest_tickers, { retCode: 10001 })), null,
    'retCode !== 0 must yield null');
  assert.strictEqual(H.normalizeBybitTickers(null), null);
});

// ─── 25. Deribit chain name-parse + iv PERCENT/100 + DVOL (§4d) ──────────────
group('deribit chain normalizer (name parse + iv/100) + dvol exact', () => {
  const before = JSON.stringify(FX.deribit_rest_book_summary);
  const ch = H.normalizeDeribitChain(FX.deribit_rest_book_summary);
  assert.strictEqual(JSON.stringify(FX.deribit_rest_book_summary), before, 'normalizer must NOT mutate its input');
  // {rows, skipped} shape: skipped is COUNTED, never silently hidden (§0).
  assert.strictEqual(ch.rows.length, 10, 'all 10 captured instruments parse');
  assert.strictEqual(ch.skipped, 0, 'nothing skipped on the real capture');

  // Headline row, every field vs the wire; expiry against a HAND-COMPUTED
  // Date.UTC at the Deribit 08:00 UTC convention, plus the literal epoch
  // (python-datetime cross-checked) so a Date.UTC misuse cannot self-confirm.
  const r0 = ch.rows.find((r) => r.name === 'BTC-28AUG26-105000-C');
  assert.ok(r0, 'BTC-28AUG26-105000-C present');
  assert.strictEqual(r0.strike, 105000, 'strike from the name');
  assert.strictEqual(r0.cp, 'C', 'call/put flag from the name');
  assert.strictEqual(r0.expiryTs, Date.UTC(2026, 7, 28, 8, 0, 0), 'expiry = 08:00 UTC on the contract date');
  assert.strictEqual(r0.expiryTs, 1787904000000, 'expiry epoch ms exact (2026-08-28T08:00Z)');
  // THE §4d TRAP: mark_iv arrives in PERCENT (48.58) — /100 or every vol
  // formula downstream is silently 100× off (DEVELOPMENT §5).
  assert.strictEqual(r0.iv, 48.58 / 100, 'iv === mark_iv/100 exactly');
  assert.strictEqual(r0.iv, 0.4858, 'iv decimal exact');
  assert.strictEqual(r0.oi, 161.3, 'oi = open_interest (BTC contracts)');
  assert.strictEqual(r0.volume, 0, 'volume passthrough');
  assert.strictEqual(r0.markPrice, 0.00026511, 'markPrice (coin-quoted)');
  assert.strictEqual(r0.underlying, 63358.41, 'underlying = per-expiry synthetic future (Black-76 F)');

  // Single-digit day edge, straight from the real capture ('BTC-6JUL26-…').
  const r1 = ch.rows.find((r) => r.name === 'BTC-6JUL26-54000-P');
  assert.ok(r1, 'single-digit-day instrument present in the capture');
  assert.strictEqual(r1.strike, 54000);
  assert.strictEqual(r1.cp, 'P');
  assert.strictEqual(r1.expiryTs, Date.UTC(2026, 6, 6, 8, 0, 0), 'D MMM YY (no leading zero) must parse');
  assert.strictEqual(r1.expiryTs, 1783324800000, 'expiry epoch ms exact (2026-07-06T08:00Z)');
  assert.strictEqual(r1.iv, 57.71 / 100);

  // Unparseable names (futures, perps) are skipped AND counted — the
  // OptionsView surfaces the count instead of silently shrinking the chain.
  const dirty = JSON.parse(before);
  dirty.result.push({ instrument_name: 'BTC-25SEP26', mark_iv: 50 });          // a future — 3 tokens
  dirty.result.push({ instrument_name: 'BTC_USDC-PERPETUAL', mark_iv: 50 });   // a perp — no date/strike/cp
  const ch2 = H.normalizeDeribitChain(dirty);
  assert.strictEqual(ch2.rows.length, 10, 'options still parse alongside the junk');
  assert.strictEqual(ch2.skipped, 2, 'both non-option names counted, not hidden');
  assert.strictEqual(H.normalizeDeribitChain({ error: { code: 1 } }), null, 'JSON-RPC error payload → null');
  assert.strictEqual(H.normalizeDeribitChain(null), null);
  // Non-finite mark_iv → iv NaN but the row is KEPT: PCR/max-pain need oi,
  // not iv — dropping the row would silently bias both.
  const noIv = JSON.parse(before);
  noIv.result[0].mark_iv = null;
  const chN = H.normalizeDeribitChain(noIv);
  assert.strictEqual(chN.rows.length, 10, 'iv-less row kept for the OI consumers');
  assert.ok(Number.isNaN(chN.rows.find((r) => r.name === 'BTC-28AUG26-105000-C').iv), 'its iv reads NaN, never 0');

  // DVOL: the PINNED capture says 38.68 (§0 — the real payload wins; the §4d
  // list's "38.67" was a transcription slip, corrected in DESIGN 2026-07-05).
  assert.strictEqual(H.normalizeDeribitDvol(FX.deribit_rest_dvol), 38.68, 'DVOL = result.index_price exact');
  assert.strictEqual(H.normalizeDeribitDvol({ result: {} }), null, 'missing index_price → null');
  assert.strictEqual(H.normalizeDeribitDvol(null), null);
});

// ─── 26. HL leaderboard (pair-array parse + dust rule) + whale positions ─────
group('hl leaderboard windowPerformances parse + dust exclusion + positions', () => {
  const before = JSON.stringify(FX.hl_leaderboard_sample);
  const lb = H.normalizeHlLeaderboard(FX.hl_leaderboard_sample);
  assert.strictEqual(JSON.stringify(FX.hl_leaderboard_sample), before, 'normalizer must NOT mutate its input');

  // Top by account value: addr + Number()ed acctVal exact.
  assert.strictEqual(lb.topByValue.length, 3, 'all 3 fixture rows rank (n=10 default caps)');
  assert.strictEqual(lb.topByValue[0].addr, '0xa822a9ceb6d6cb5b565bd10098abcfa9cf18d748', 'largest book first');
  assert.strictEqual(lb.topByValue[0].acctVal, Number('13295008398.5851707458'), 'acctVal Number()ed exact');
  assert.strictEqual(lb.topByValue[1].addr, '0x1c498a93b145e7a73d69691e9023f6f308e1cc3f');
  assert.strictEqual(lb.topByValue[2].addr, '0x24de6b77e8bc31c40aa452926daa6bbab7a71b0f');

  // ROI window: windowPerformances is an ARRAY OF PAIRS [[window, {…}], …]
  // and 'month' is the 30d window (there is no literal '30d' key — wire
  // reality). Only one fixture row has a nonzero month ROI — it must rank #1.
  assert.strictEqual(lb.topByRoi30d[0].addr, '0x24de6b77e8bc31c40aa452926daa6bbab7a71b0f', 'pair-array month window drives the ROI rank');
  assert.strictEqual(lb.topByRoi30d[0].roi, 0.0070708003, 'month roi exact');
  assert.strictEqual(lb.topByRoi30d[0].pnl, Number('14464050.7791530006'), 'month pnl exact');

  // Dust rule (§4d): acctVal < $10k is excluded from the ROI ranking ONLY —
  // a $52 account that lucked into 40× must not outrank every real book —
  // while the VALUE ranking keeps everyone (size is size).
  const dusty = JSON.parse(before);
  dusty.leaderboardRows.push({
    ethAddress: '0xdust', accountValue: '52.10',
    windowPerformances: [['month', { pnl: '2000.0', roi: '40.0', vlm: '0' }]],
  });
  const lb2 = H.normalizeHlLeaderboard(dusty);
  assert.ok(!lb2.topByRoi30d.some((r) => r.addr === '0xdust'), 'dust excluded from the ROI ranking');
  assert.strictEqual(lb2.topByValue.length, 4, 'dust still counts in the VALUE ranking');
  const lbN = H.normalizeHlLeaderboard(FX.hl_leaderboard_sample, 2);
  assert.strictEqual(lbN.topByValue.length, 2, 'n caps the value list');
  assert.strictEqual(lbN.topByRoi30d.length, 2, 'n caps the ROI list');
  assert.strictEqual(H.normalizeHlLeaderboard({}), null, 'missing leaderboardRows → null');

  // Whale positions: assetPositions[].position → the WhaleView row shape.
  const pBefore = JSON.stringify(FX.hl_clearinghouse_state);
  const pos = H.normalizeHlPositions(FX.hl_clearinghouse_state);
  assert.strictEqual(JSON.stringify(FX.hl_clearinghouse_state), pBefore, 'normalizer must NOT mutate its input');
  assert.strictEqual(pos.length, 5, 'all 5 fixture positions survive');
  const sol = pos.find((p) => p.coin === 'SOL');
  assert.strictEqual(sol.szi, 169806.92, 'szi Number()ed, sign intact');
  assert.strictEqual(sol.side, 'long', 'szi > 0 → long (the sign IS the direction)');
  assert.strictEqual(sol.entryPx, 74.6913, 'entryPx exact');
  assert.strictEqual(sol.posValue, Number('14000920.1678400002'), 'posValue = positionValue');
  assert.strictEqual(sol.uPnl, Number('1317808.6891600001'), 'uPnl = unrealizedPnl');
  assert.strictEqual(sol.leverage, 17, 'leverage = leverage.value (the OBJECT wire shape)');
  assert.strictEqual(pos.find((p) => p.coin === 'HYPE').leverage, 2, 'per-position leverage varies');

  // The fixture holds longs only — construct the short so BOTH sides are
  // exercised (same why as the liq-side precondition in group 6).
  assert.ok(pos.every((p) => p.side === 'long'), 'fixture precondition: capture is all-long');
  const shorted = JSON.parse(pBefore);
  shorted.assetPositions[0].position.szi = '-100.5';
  const ps = H.normalizeHlPositions(shorted)[0];
  assert.strictEqual(ps.side, 'short', 'negative szi → short');
  assert.strictEqual(ps.szi, -100.5, 'sign preserved on the row');
  // Zero size = no position = no direction → dropped; bare-number leverage
  // tolerated; missing payload → null.
  const edge = JSON.parse(pBefore);
  edge.assetPositions[1].position.leverage = 5;
  edge.assetPositions[2].position.szi = '0';
  const pe = H.normalizeHlPositions(edge);
  assert.strictEqual(pe.find((p) => p.coin === 'AAVE').leverage, 5, 'bare-number leverage shape tolerated');
  assert.ok(!pe.some((p) => p.coin === 'WLD'), 'zero-szi row dropped');
  assert.strictEqual(H.normalizeHlPositions({}), null, 'missing assetPositions → null');
});

// ─── 27. buildScreener: turnover-USD ranking + slice honesty (§4d) ───────────
group('buildScreener turnover ranking + topN slice + honest total', () => {
  // Rank the REAL normalized fixture rows: turnover24h (USD — the only
  // cross-symbol-comparable key; volume24h is base-coin apples vs oranges).
  const rows = H.normalizeBybitTickers(FX.bybit_rest_tickers);
  const symsBefore = rows.map((r) => r.sym).join(',');
  const s3 = S.buildScreener(rows, { topN: 3 });
  assert.strictEqual(rows.map((r) => r.sym).join(','), symsBefore, 'input array must NOT be mutated (slice-before-sort)');
  assert.deepStrictEqual(s3.rows.map((r) => r.sym), ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'],
    'fixture turnover order: BTC $2.38B > ETH $1.32B > SOL $546M');
  assert.strictEqual(s3.total, 6, "total states the TRUE universe size (the 'top 3 of 6' header)");
  // Default topN = 40 > universe → everything; topN ≤ 0 = the 'all' passthrough.
  assert.strictEqual(S.buildScreener(rows).rows.length, 6, 'default top-40 on 6 rows keeps all 6');
  const all = S.buildScreener(rows, { topN: 0 });
  assert.strictEqual(all.rows.length, 6, "topN 0 = 'all' passthrough");
  for (let i = 1; i < all.rows.length; i++) {
    assert.ok(all.rows[i].turnover24h <= all.rows[i - 1].turnover24h, 'rows descending by turnover');
  }
  // Non-finite turnover SINKS to the end but is never dropped — total must
  // keep stating the real universe size.
  const mixed = S.buildScreener([{ sym: 'NANO', turnover24h: NaN }, { sym: 'REAL', turnover24h: 5 }], { topN: 0 });
  assert.deepStrictEqual(mixed.rows.map((r) => r.sym), ['REAL', 'NANO'], 'NaN turnover sinks, never dropped');
  assert.strictEqual(mixed.total, 2);
  assert.deepStrictEqual(S.buildScreener(null), { rows: [], total: 0 }, 'no tickers → empty, total 0');
});

// ─── 28. confluenceReads: 9 categories, both directions, n/a rail (§4d) ──────
group('confluenceReads all-9 both directions + n/a-on-missing + tally + IC label', () => {
  const CATS = ['footprint Δ-trend', 'CVD slope', 'price vs POC/VA', 'TPO position',
    'funding sign/extreme', 'OI 1h change', 'liq-pressure 5m', 'book top-10 imbalance', 'price vs SMA50'];
  const LABEL = 'un-validated descriptive reads — forward IC of board signals ≈ 0 (RESEARCH-ic-runlog); NOT a signal';
  const readOf = (out, cat) => out.reads.find((r) => r.category === cat).read;

  // All-bullish drive: price above both value areas, net buy flow, positive
  // CVD slope, CROWDED SHORTS (negative funding extreme → contrarian
  // bullish), OI building, short-side liq pressure, bid-heavy book, close
  // above SMA50.
  const bull = S.confluenceReads({
    fpDeltas: [2, 3, 1], cvdSlope: 5,
    price: 110, poc: 100, vah: 105, val: 95,
    tpoPoc: 100, tpoVah: 105, tpoVal: 95,
    fundingRate: -0.0005,               // ×(8760/8)×100 = −54.8%/yr — crowded shorts
    oiChangePct1h: 1.0, liqImb5m: -0.8, bookImb: 0.5,
    sma50: 100, lastClose: 101,
  });
  assert.deepStrictEqual(bull.reads.map((r) => r.category), CATS, 'EXACTLY the 9 §4d categories, in order');
  for (const c of CATS) assert.strictEqual(readOf(bull, c), 'bullish', c + ' must read bullish on the bullish drive');
  assert.deepStrictEqual(bull.tally, { bullish: 9, bearish: 0, neutral: 0, na: 0 }, 'tally counts every read');
  assert.strictEqual(bull.label, LABEL, 'the mandatory IC-honesty sentence, VERBATIM');

  // All-bearish mirror (crowded LONGS: positive funding extreme → bearish).
  const bear = S.confluenceReads({
    fpDeltas: [-2, -3, -1], cvdSlope: -5,
    price: 90, poc: 100, vah: 105, val: 95,
    tpoPoc: 100, tpoVah: 105, tpoVal: 95,
    fundingRate: 0.0005,
    oiChangePct1h: -1.0, liqImb5m: 0.8, bookImb: -0.5,
    sma50: 100, lastClose: 99,
  });
  for (const c of CATS) assert.strictEqual(readOf(bear, c), 'bearish', c + ' must read bearish on the bearish drive');
  assert.deepStrictEqual(bear.tally, { bullish: 0, bearish: 9, neutral: 0, na: 0 });
  assert.strictEqual(bear.label, LABEL);

  // §4d: the RESPONSE-PROVIDED funding interval must drive the annualization
  // — the same rate reads neutral at 8h (5.5%/yr) but crowded at 1h (43.8%/yr).
  const in8 = { fundingRate: 0.00005 };
  assert.strictEqual(readOf(S.confluenceReads(in8), 'funding sign/extreme'), 'neutral', '0.005%/8h ≈ 5.5%/yr — baseline carry');
  assert.strictEqual(readOf(S.confluenceReads(Object.assign({ fundingIntervalH: 1 }, in8)), 'funding sign/extreme'),
    'bearish', 'same rate every 1h ≈ 43.8%/yr — crowded longs');

  // n/a rail (§0.7): a missing feed must NEVER default to neutral — 'neutral'
  // claims "I looked and it is balanced", a fabricated read when nothing
  // arrived. Empty inputs → all 9 n/a; a single present feed leaves 8 n/a.
  const na = S.confluenceReads({});
  assert.deepStrictEqual(na.tally, { bullish: 0, bearish: 0, neutral: 0, na: 9 }, 'no feeds → 9 × n/a, ZERO neutral');
  assert.ok(na.reads.every((r) => r.read === 'n/a'), "every read is 'n/a', none invented");
  assert.strictEqual(na.label, LABEL, 'label present even on an all-n/a board');
  const one = S.confluenceReads({ cvdSlope: 0 });
  assert.strictEqual(readOf(one, 'CVD slope'), 'neutral', 'a PRESENT flat feed is a genuine neutral');
  assert.deepStrictEqual(one.tally, { bullish: 0, bearish: 0, neutral: 1, na: 8 }, 'tally always sums to 9');

  // Dead-bands read neutral, not directional: balanced flow, inside-value
  // price, mild OI/liq/book/SMA moves.
  const mid = S.confluenceReads({
    fpDeltas: [1, -1], cvdSlope: 0,
    price: 100, poc: 100, vah: 105, val: 95,
    tpoPoc: 100, tpoVah: 105, tpoVal: 95,
    fundingRate: 0.00001, oiChangePct1h: 0.2, liqImb5m: 0.1, bookImb: 0.1,
    sma50: 100, lastClose: 100.05,
  });
  assert.deepStrictEqual(mid.tally, { bullish: 0, bearish: 0, neutral: 9, na: 0 }, 'dead-band drive → 9 × neutral');
});

// ─── 29. AlertEngine: per-kind fire + cooldown + heuristic label (§4d) ───────
group('alert engine per-kind fire/cooldown + cvd-divergence heuristic label', () => {
  const T0 = 1783186000000;
  const CD = 60000;   // default cooldownMs
  const eng = (rule) => S.AlertEngine({ rules: [rule] });

  // price-cross: fires on a CROSS in either direction; the first evaluate
  // only seeds prev; prev keeps tracking THROUGH the cooldown so the rule
  // re-arms against reality, not a frozen snapshot.
  {
    const e = eng({ id: 'pc', kind: 'price-cross', threshold: 100 });
    assert.strictEqual(e.evaluate({ ts: T0, price: 99 }).length, 0, 'first sight only seeds prev');
    const up = e.evaluate({ ts: T0 + 1000, price: 100.5 });
    assert.strictEqual(up.length, 1, 'upward cross fires');
    assert.strictEqual(up[0].kind, 'price-cross');
    assert.strictEqual(up[0].ts, T0 + 1000, 'event ts = snap ts (event-time driven)');
    assert.strictEqual(e.evaluate({ ts: T0 + 2000, price: 99 }).length, 0, 'cross inside cooldown suppressed');
    // prev tracked through the cooldown: 99 is on record, so the next
    // post-cooldown tick at 101 is a genuine re-cross and must fire…
    assert.strictEqual(e.evaluate({ ts: T0 + 1000 + CD, price: 101 }).length, 1, 'post-cooldown re-cross fires (prev tracked)');
    // …and the DOWNWARD direction fires symmetrically.
    assert.strictEqual(e.evaluate({ ts: T0 + 1000 + 2 * CD, price: 98 }).length, 1, 'downward cross fires');
    assert.strictEqual(e.evaluate({ ts: T0 + 1000 + 3 * CD, price: 98.5 }).length, 0, 'no cross (same side) → quiet');
  }

  // whale-print: ONE event per evaluate — the largest qualifying notional.
  {
    const e = eng({ id: 'wp', kind: 'whale-print', threshold: 1e6 });
    const mk = (qty, buy) => ({ ts: T0, price: 60000, qty, aggressorBuy: buy, id: String(qty), kind: 'trade', ex: 'bybit' });
    const ev = e.evaluate({ ts: T0, trades: [mk(20, true), mk(50, false), mk(0.1, true)] });
    assert.strictEqual(ev.length, 1, 'one event per evaluate — the largest print, not per-print spam');
    assert.ok(ev[0].msg.indexOf('3000000') >= 0 && ev[0].msg.indexOf('sell') >= 0, 'largest = $3M sell, got: ' + ev[0].msg);
    assert.strictEqual(e.evaluate({ ts: T0 + 1000, trades: [mk(50, false)] }).length, 0, 'cooldown suppresses');
    assert.strictEqual(e.evaluate({ ts: T0 + CD, trades: [mk(50, false)] }).length, 1, 'post-cooldown fires again');
    // Thresholds are INJECTED (§4d): a threshold-less rule cannot fire.
    const bare = eng({ id: 'wp2', kind: 'whale-print' });
    assert.strictEqual(bare.evaluate({ ts: T0, trades: [mk(1000, true)] }).length, 0, 'no threshold → cannot fire, ever');
  }

  // liq-1m: caller-summed LiqStore-style notional ≥ threshold.
  {
    const e = eng({ id: 'lq', kind: 'liq-1m', threshold: 5e6 });
    assert.strictEqual(e.evaluate({ ts: T0, liq1mUsd: 6e6 }).length, 1, 'fires at $6M ≥ $5M');
    assert.strictEqual(e.evaluate({ ts: T0 + 1000, liq1mUsd: 9e6 }).length, 0, 'still-breached inside cooldown stays quiet');
    assert.strictEqual(e.evaluate({ ts: T0 + CD, liq1mUsd: 6e6 }).length, 1, 're-fires after cooldown');
  }

  // funding-flip: last-NONZERO-sign tracking — + → 0 → − is ONE flip (zero is
  // "nobody pays", not a side).
  {
    const e = eng({ id: 'ff', kind: 'funding-flip' });
    assert.strictEqual(e.evaluate({ ts: T0, fundingRate: 0.0001 }).length, 0, 'first sign only seeds');
    assert.strictEqual(e.evaluate({ ts: T0 + 1000, fundingRate: 0 }).length, 0, 'zero is not a flip');
    const flip = e.evaluate({ ts: T0 + 2000, fundingRate: -0.0001 });
    assert.strictEqual(flip.length, 1, '+ → 0 → − reads as exactly ONE flip');
    assert.ok(flip[0].msg.indexOf('positive → negative') >= 0, 'direction stated: ' + flip[0].msg);
    const back = e.evaluate({ ts: T0 + 2000 + CD, fundingRate: 0.0001 });
    assert.strictEqual(back.length, 1, 'flip back fires after cooldown');
    assert.ok(back[0].msg.indexOf('negative → positive') >= 0);
  }

  // cvd-divergence: §4d headline — price HH on a CVD lower-high (bearish) and
  // the LL/HL mirror (bullish); EVERY event carries label:'heuristic' (a
  // descriptive pattern, not a validated one). n ≥ 4 floor.
  {
    const e = eng({ id: 'dv', kind: 'cvd-divergence' });
    const bearish = e.evaluate({ ts: T0, window: { price: [100, 101, 102, 103], cvd: [50, 60, 55, 58] } });
    assert.strictEqual(bearish.length, 1, 'HH price + LH CVD fires');
    assert.strictEqual(bearish[0].label, 'heuristic', "divergence event MUST carry label:'heuristic'");
    assert.ok(bearish[0].msg.indexOf('bearish') >= 0, 'direction in the message');
    const bullish = e.evaluate({ ts: T0 + CD, window: { price: [103, 102, 101, 100], cvd: [55, 50, 58, 60] } });
    assert.strictEqual(bullish.length, 1, 'LL price + HL CVD fires (mirror)');
    assert.strictEqual(bullish[0].label, 'heuristic');
    assert.ok(bullish[0].msg.indexOf('bullish') >= 0);
    const tiny = eng({ id: 'dv2', kind: 'cvd-divergence' });
    assert.strictEqual(tiny.evaluate({ ts: T0, window: { price: [100, 101, 103], cvd: [60, 55, 50] } }).length, 0,
      'n < 4 cannot compare extrema — quiet');
  }

  // book-imbalance: |x| ≥ threshold, either sign.
  {
    const e = eng({ id: 'bi', kind: 'book-imbalance', threshold: 0.4 });
    assert.strictEqual(e.evaluate({ ts: T0, bookImb: 0.3 }).length, 0, 'below threshold quiet');
    const bid = e.evaluate({ ts: T0 + 1000, bookImb: 0.5 });
    assert.strictEqual(bid.length, 1, 'bid-heavy fires');
    assert.ok(bid[0].msg.indexOf('bid-heavy') >= 0);
    const ask = e.evaluate({ ts: T0 + 1000 + CD, bookImb: -0.5 });
    assert.strictEqual(ask.length, 1, 'ask-heavy fires (|x|)');
    assert.ok(ask[0].msg.indexOf('ask-heavy') >= 0);
  }

  // detector-pass: forwards each §4b detector event, PRESERVING the
  // 'heuristic' badge (re-defaulted if a caller stripped it — no layer may
  // drop it); the cooldown then gates subsequent passes.
  {
    const e = eng({ id: 'dp', kind: 'detector-pass' });
    const evs = e.evaluate({
      ts: T0,
      detectorEvents: [
        { kind: 'spoof-pull', price: 95, label: 'heuristic' },
        { kind: 'iceberg-refill', price: 100 },   // stripped label — must be re-defaulted
      ],
    });
    assert.strictEqual(evs.length, 2, 'each detector event forwards individually');
    assert.ok(evs.every((x) => x.label === 'heuristic'), 'heuristic badge preserved AND re-defaulted');
    assert.ok(evs[0].msg.indexOf('spoof-pull') >= 0 && evs[1].msg.indexOf('iceberg-refill') >= 0);
    assert.strictEqual(e.evaluate({ ts: T0 + 1000, detectorEvents: [{ kind: 'spoof-pull', price: 95 }] }).length, 0,
      'cooldown gates subsequent passes');
  }

  // oi-jump + basis-bp: |x| ≥ injected threshold.
  {
    const e = eng({ id: 'oj', kind: 'oi-jump', threshold: 2 });
    assert.strictEqual(e.evaluate({ ts: T0, oiChangePct1h: 1 }).length, 0);
    assert.strictEqual(e.evaluate({ ts: T0 + 1000, oiChangePct1h: 2.5 }).length, 1, 'OI jump fires');
    assert.strictEqual(e.evaluate({ ts: T0 + 1000 + CD, oiChangePct1h: -2.5 }).length, 1, 'OI drop fires (|x|)');
    const b = eng({ id: 'bb', kind: 'basis-bp', threshold: 20 });
    assert.strictEqual(b.evaluate({ ts: T0, basisBp: 10 }).length, 0);
    assert.strictEqual(b.evaluate({ ts: T0 + 1000, basisBp: 25 }).length, 1, 'rich basis fires');
    assert.strictEqual(b.evaluate({ ts: T0 + 1000 + CD, basisBp: -25 }).length, 1, 'discount basis fires (|x|)');
  }

  // Hygiene: event-ts is the ONLY clock — no ts, no events (nothing may be
  // timestamped by guessing); disabled rules are invisible; the ring retains.
  {
    const e = eng({ id: 'lq', kind: 'liq-1m', threshold: 1 });
    assert.strictEqual(e.evaluate(null).length, 0, 'null snap → []');
    assert.strictEqual(e.evaluate({ liq1mUsd: 9e9 }).length, 0, 'missing ts → [] (event-time honesty)');
    assert.strictEqual(e.evaluate({ ts: NaN, liq1mUsd: 9e9 }).length, 0, 'NaN ts → []');
    const off = eng({ id: 'x', kind: 'liq-1m', threshold: 1, enabled: false });
    assert.strictEqual(off.evaluate({ ts: T0, liq1mUsd: 9e9 }).length, 0, 'disabled rule never fires');
    e.evaluate({ ts: T0, liq1mUsd: 5 });
    assert.strictEqual(e.events().length, 1, 'events() replays the retained ring');
    assert.strictEqual(e.events()[0].ruleId, 'lq');
  }
});

// ─── 30. Unsigned GEX sanity + PCR-by-OI math (§4d / §0.5) ───────────────────
group('unsigned GEX (black76 Γ>0, Σ|Γ|·OI hand sum) + PCR by OI', () => {
  const YEAR_MS = 31536000000;   // 365d — the OptionsView constant (quant.js periodsPerYear=365 convention)

  // A PINNED real chain row through the REAL normalizer, T from the
  // fixture's own creation_timestamp (the view's nowTs = slice ts rule —
  // never Date.now(), so this stays deterministic forever).
  const ch = H.normalizeDeribitChain(FX.deribit_rest_book_summary);
  const nowTs = FX.deribit_rest_book_summary.result[0].creation_timestamp;
  assert.ok(Number.isFinite(nowTs), 'fixture precondition: capture carries creation_timestamp');
  const r = ch.rows.find((x) => x.name === 'BTC-25DEC26-80000-C');
  assert.ok(r, 'pinned row BTC-25DEC26-80000-C present');
  const T = (r.expiryTs - nowTs) / YEAR_MS;
  assert.ok(T > 0, 'pinned expiry is live relative to its own capture ts');
  // iv is the normalizer's /100 decimal — feeding black76Greeks the raw
  // PERCENT value is exactly the silent 100× bug this chain of asserts pins.
  const g = Q.black76Greeks(r.underlying, r.strike, r.iv, T, r.cp).gamma;
  assert.ok(Number.isFinite(g) && g > 0, 'Black-76 Γ must be finite and > 0 on a live near-money row, got ' + g);
  // Γ is call/put-identical in Black-76 — the unsigned Σ|Γ|·OI therefore
  // cannot depend on the cp mix at a strike (structural sanity).
  assert.strictEqual(Q.black76Greeks(r.underlying, r.strike, r.iv, T, 'P').gamma, g, 'call Γ ≡ put Γ');
  // And the PERCENT-trap tripwire: raw mark_iv (48-ish "decimal" = 4858% vol)
  // would produce a wildly smaller gamma — assert the decimal iv differs.
  const gWrong = Q.black76Greeks(r.underlying, r.strike, r.iv * 100, T, r.cp).gamma;
  assert.ok(!(Math.abs(gWrong - g) <= 1e-12), 'iv fed as PERCENT must NOT reproduce the decimal-iv gamma');

  // Σ|Γ|·OI over two constructed rows === the hand-computed sum, through
  // quant.js gammaConcentration (the same |Γ|·OI-by-strike accumulation the
  // GEX profile draws — §4d: unsigned Σ|gamma|·OI convention, §0.5).
  const fwd = 63000, t = 0.25;
  const rows2 = [
    { strike: 60000, type: 'C', oi: 100, iv: 0.5 },
    { strike: 60000, type: 'P', oi: 50, iv: 0.45 },
  ];
  const g1 = Q.black76Greeks(fwd, 60000, 0.5, t, 'C').gamma;
  const g2 = Q.black76Greeks(fwd, 60000, 0.45, t, 'P').gamma;
  const gc = Q.gammaConcentration(rows2, fwd, t);
  assert.deepStrictEqual(gc.strikes, [60000], 'both rows accumulate onto the one strike');
  assert.strictEqual(gc.gammaOi[0], Math.abs(g1) * 100 + Math.abs(g2) * 50,
    'Σ|Γ|·OI must equal the hand sum EXACTLY (same op order)');
  assert.ok(gc.gammaOi[0] > 0, 'gamma mass positive');

  // PCR by OI, constructed: puts 15 / calls 30 = 0.5 exactly (quant.js
  // maxPain.pcRatio — the same put/call-OI ratio arithmetic as the view tile).
  const mp = Q.maxPain([
    { strike: 100, type: 'C', oi: 30, underlying: 100 },
    { strike: 100, type: 'P', oi: 15, underlying: 100 },
  ]);
  assert.strictEqual(mp.pcRatio, 0.5, 'PCR by OI = ΣputOI/ΣcallOI exact');
  assert.strictEqual(mp.maxPain, 100, 'single-strike slice pins max pain there');

  // PCR on the REAL chain vs a hand sum straight off the RAW wire fields —
  // pins the whole path (name-parse cp + oi passthrough + ratio) at once.
  let cOi = 0, pOi = 0;
  for (const raw of FX.deribit_rest_book_summary.result) {
    const cp = raw.instrument_name.slice(-1);
    if (cp === 'C') cOi += raw.open_interest; else pOi += raw.open_interest;
  }
  assert.ok(cOi > 0 && pOi > 0, 'fixture precondition: both calls and puts carry OI');
  const mpAll = Q.maxPain(ch.rows.map((x) => ({ strike: x.strike, type: x.cp, oi: x.oi, underlying: x.underlying })));
  assert.ok(approx(mpAll.pcRatio, pOi / cOi, 1e-12), 'chain PCR by OI ' + mpAll.pcRatio + ' != raw-wire hand sum ' + (pOi / cOi));
});

// ─── 31. Polymarket /events normalizer — the §4e STRING-outcomePrices trap ───
group('polymarket events normalizer (STRING outcomePrices → yesPct number)', () => {
  const raw = FX.polymarket_events;
  const out = H.normalizePolymarketEvents(raw);
  assert.ok(Array.isArray(out), 'normalizer must return an array for the fixture');
  assert.strictEqual(out.length, 3, 'all 3 fixture events survive (every one has readable open markets)');

  // Event titles EXACT vs the captured /events?tag_slug=bitcoin payload.
  assert.strictEqual(out[0].title, 'Bitcoin above ___ on July 5?');
  assert.strictEqual(out[1].title, 'What price will Bitcoin hit in July?');
  assert.strictEqual(out[2].title, 'What price will Bitcoin hit June 29-July 5?');

  // THE §4e TRAP: outcomePrices arrives as a STRING containing a JSON array
  // of STRINGS ("[\"0.9995\", \"0.0005\"]"). Assert the fixture really is
  // that shape (the trap stays pinned), then that the normalizer decoded it
  // into a PLAIN 0–100 NUMBER: yesPct = Number(JSON.parse(s)[0]) × 100.
  assert.strictEqual(typeof raw[0].markets[0].outcomePrices, 'string',
    'fixture precondition: outcomePrices is a STRING (the wire shape)');
  const m0 = out[0].markets[0];
  assert.strictEqual(typeof m0.yesPct, 'number', 'yesPct must be a Number, not a string');
  assert.ok(approx(m0.yesPct, 99.95, 1e-9), '"0.9995" → 99.95, got ' + m0.yesPct);
  assert.ok(approx(out[1].markets[0].yesPct, 0.15, 1e-9), '"0.0015" → 0.15, got ' + out[1].markets[0].yesPct);
  for (const ev of out) {
    for (const m of ev.markets) {
      assert.ok(Number.isFinite(m.yesPct) && m.yesPct >= 0 && m.yesPct <= 100,
        'yesPct in [0,100]: ' + m.yesPct);
    }
  }

  // Field passthrough exact: event vol24h / market vol24h / question / endTs.
  assert.strictEqual(out[0].vol24h, 864130.3836839998);
  assert.strictEqual(m0.vol24h, 68312.595);
  assert.strictEqual(m0.question, 'Will the price of Bitcoin be above $50,000 on July 5?');
  assert.strictEqual(out[0].endTs, Date.parse('2026-07-05T16:00:00Z'));
  assert.strictEqual(out[0].markets.length, 4, 'all 4 open markets kept');

  // Skip rails (§4e: a market that can't be read is SKIPPED, never guessed):
  // closed market, non-JSON prices, non-numeric price — only the good one
  // survives; an event with NO readable market renders nothing at all.
  const synth = [{
    title: 'synthetic', endDate: '2026-08-01T00:00:00Z', volume24hr: 1,
    markets: [
      { question: 'closed', outcomePrices: '["0.5", "0.5"]', closed: true, volume24hr: 1 },
      { question: 'not json', outcomePrices: 'oops', closed: false, volume24hr: 1 },
      { question: 'not a number', outcomePrices: '["x", "y"]', closed: false, volume24hr: 1 },
      { question: 'good', outcomePrices: '["0.25", "0.75"]', closed: false, volume24hr: 2 },
    ],
  }, {
    title: 'all unreadable', endDate: '2026-08-01T00:00:00Z', volume24hr: 1,
    markets: [{ question: 'bad', outcomePrices: 'nope', closed: false }],
  }];
  const sOut = H.normalizePolymarketEvents(synth);
  assert.strictEqual(sOut.length, 1, 'the no-readable-market event is dropped entirely');
  assert.strictEqual(sOut[0].markets.length, 1, 'closed + undecodable markets skipped, never guessed');
  assert.strictEqual(sOut[0].markets[0].question, 'good');
  assert.ok(approx(sOut[0].markets[0].yesPct, 25, 1e-9));

  // Non-array (error body / null) → null, the caller's 'awaiting' state.
  assert.strictEqual(H.normalizePolymarketEvents(null), null);
  assert.strictEqual(H.normalizePolymarketEvents({ error: 'x' }), null);
});

// ─── 32. Tree of Alpha news normalizer (ts/title/source + ordering) ─────────
group('toa news normalizer (ts/title/source exact, newest-first, symbols default)', () => {
  const raw = FX.toa_news;
  const out = H.normalizeToaNews(raw);
  assert.strictEqual(out.length, 3, 'all 3 fixture rows have a title and a finite time');

  // Exact fields vs the captured /api/news rows; fixture is newest-first
  // already, so out[0] is the newest row.
  assert.strictEqual(out[0].ts, 1783206550597);
  assert.strictEqual(out[0].title, 'WHITEHOUSE: Saving Americas Story');
  assert.strictEqual(out[0].source, 'Blogs');
  assert.strictEqual(out[0].url, 'https://www.whitehouse.gov/releases/2026/07/saving-americas-story');
  assert.deepStrictEqual(out[0].symbols, [], 'explicit empty symbols passes through');
  // Two REAL fixture rows carry NO `symbols` key at all → [] (never undefined
  // — the view maps over it unconditionally).
  assert.ok(!('symbols' in raw[1]), 'fixture precondition: row 1 has no symbols key');
  assert.deepStrictEqual(out[1].symbols, []);
  assert.deepStrictEqual(out[2].symbols, []);

  // Ordering is IMPOSED, not inherited: reversed input → identical output.
  const rev = H.normalizeToaNews(raw.slice().reverse());
  assert.deepStrictEqual(rev, out, 'newest-first must not depend on wire order');
  for (let i = 1; i < out.length; i++) assert.ok(out[i - 1].ts >= out[i].ts, 'descending ts');

  // Drop rails: undatable / untitled rows vanish; non-string symbols filtered.
  const synth = H.normalizeToaNews([
    { title: 'ok', time: 5, source: 'Twitter', url: '', symbols: ['BTCUSDT', 7, null, 'ETH'] },
    { title: 'no time', source: 'Twitter' },
    { time: 6, source: 'Twitter' },                       // no title
    { title: '', time: 7 },                                // empty title
  ]);
  assert.strictEqual(synth.length, 1);
  assert.deepStrictEqual(synth[0].symbols, ['BTCUSDT', 'ETH'], 'non-string symbol entries filtered');
  assert.strictEqual(H.normalizeToaNews(null), null);
  assert.strictEqual(H.normalizeToaNews({}), null);
});

// ─── 33. econ local-file normalizer (fetchedTs passthrough + ts sort) ────────
group('econ local-file normalizer (synthetic file object: fetchedTs passthrough, ts sort)', () => {
  // §4e design: faireconomy has NO CORS → the browser reads the LOCAL file
  // scripts/fetch_econ.py writes ({fetchedTs, events}). The normalizer's
  // input is therefore a SYNTHETIC file object built from the pinned week
  // rows — exactly what fetchEconLocal() would hand it.
  const FETCHED = 1751791234567;
  const file = { fetchedTs: FETCHED, events: FX.ff_econ_sample };
  const out = H.normalizeEconLocal(file);
  assert.ok(out, 'valid file object must normalize');

  // fetchedTs passes through UNCHANGED — it is the §4e fetch-age stamp the
  // EconView must display; touching it would forge the mirror's age.
  assert.strictEqual(out.fetchedTs, FETCHED);

  assert.strictEqual(out.events.length, 5, 'all 5 fixture rows carry a parseable date');
  // Hand-checked offset math: '2026-06-28T08:15:00-04:00' ≡ 12:15 UTC.
  assert.strictEqual(out.events[0].ts, Date.UTC(2026, 5, 28, 12, 15, 0));
  assert.strictEqual(out.events[0].title, 'RBA Gov Bullock Speaks');
  assert.strictEqual(out.events[0].country, 'AUD');
  assert.strictEqual(out.events[0].impact, 'Medium');
  // forecast/previous STAY STRINGS: '' when the source had none (speeches) —
  // rendered '—', never a fake 0; real values pass through verbatim.
  assert.strictEqual(out.events[0].forecast, '');
  const retail = out.events.find((e) => e.title === 'Retail Sales y/y');
  assert.strictEqual(retail.forecast, '3.1%');
  assert.strictEqual(retail.previous, '2.1%');
  for (let i = 1; i < out.events.length; i++) {
    assert.ok(out.events[i - 1].ts <= out.events[i].ts, 'ascending ts (upcoming-events order)');
  }

  // Sort is IMPOSED: reversed input rows → identical ascending output.
  const rev = H.normalizeEconLocal({ fetchedTs: FETCHED, events: FX.ff_econ_sample.slice().reverse() });
  assert.deepStrictEqual(rev, out, 'ascending sort must not depend on file order');

  // Drop rails: undatable / untitled rows vanish; garbage input → null.
  const synth = H.normalizeEconLocal({
    fetchedTs: 1,
    events: [
      { title: 'ok', date: '2026-07-06T08:00:00-04:00', country: 'USD', impact: 'High', forecast: '', previous: '' },
      { title: 'undatable', date: 'not-a-date' },
      { date: '2026-07-06T09:00:00-04:00' },              // no title
    ],
  });
  assert.strictEqual(synth.events.length, 1);
  assert.strictEqual(synth.events[0].impact, 'High');
  assert.strictEqual(H.normalizeEconLocal(null), null);
  assert.strictEqual(H.normalizeEconLocal({ events: 'nope' }), null);
});

// ─── 34. journalStats — 3-trade hand-computed exactness (§4e Tharp block) ────
//
// The 3 trades and the FULL hand math (R = pnl / user-declared riskUsd, §4e):
//   t1 long  100 → 110, size 1, risk  $5 → pnl = (110−100)·1      = +$10 → R = +2.0
//   t2 short 100 → 105, size 2, risk $20 → pnl = (105−100)·2·(−1) = −$10 → R = −0.5
//   t3 long   50 →  53, size 5, risk $30 → pnl = (53−50)·5        = +$15 → R = +0.5
// R sequence (tsClose order) = [+2, −0.5, +0.5]:
//   n = 3;  wins {2, 0.5} → winRate = 2/3
//   expectancyR = (2 − 0.5 + 0.5)/3 = 2/3
//   avgWinR = (2 + 0.5)/2 = 1.25;  avgLossR = −0.5
//   profitFactor = ΣwinR/|ΣlossR| = 2.5/0.5 = 5
//   sample stdev (ddof=1): deviations {4/3, −7/6, −1/6} → Σsq = 16/9 + 49/36
//     + 1/36 = 114/36 = 19/6 → var = (19/6)/2 = 19/12 → sd = √(19/12)
//   SQN = mean/sd·√n = (2/3)/√(19/12)·√3 = (2/3)·√(36/19) = 4/√19 ≈ 0.917663
//   equity walk 2 → 1.5 → 2.0: peak 2, trough 1.5 → maxDrawR = 0.5
//   byTag: 'break' = {n:2, exp (2−0.5)/2 = 0.75}; untagged = {n:1, exp 0.5}
group('journalStats 3-trade hand-computed exactness (Tharp block, §4e)', () => {
  const trades = [
    { id: 't1', tsOpen: 900, tsClose: 1000, side: 'long', entry: 100, exit: 110, size: 1, riskUsd: 5, tag: 'break', note: '' },
    { id: 't2', tsOpen: 1900, tsClose: 2000, side: 'short', entry: 100, exit: 105, size: 2, riskUsd: 20, tag: 'break', note: '' },
    { id: 't3', tsOpen: 2900, tsClose: 3000, side: 'long', entry: 50, exit: 53, size: 5, riskUsd: 30, tag: '', note: '' },
  ];
  // Feed them OUT of close order — the stats must sort by tsClose themselves
  // (the drawdown walk depends on it).
  const st = S.journalStats([trades[2], trades[0], trades[1]]);
  assert.strictEqual(st.n, 3);
  assert.strictEqual(st.excluded, 0);
  assert.ok(approx(st.winRate, 2 / 3), 'winRate 2/3, got ' + st.winRate);
  assert.ok(approx(st.expectancyR, 2 / 3), 'expectancyR 2/3, got ' + st.expectancyR);
  assert.ok(approx(st.avgWinR, 1.25), 'avgWinR 1.25, got ' + st.avgWinR);
  assert.ok(approx(st.avgLossR, -0.5), 'avgLossR −0.5, got ' + st.avgLossR);
  assert.ok(approx(st.profitFactor, 5), 'PF 5, got ' + st.profitFactor);
  assert.ok(approx(st.sqn, 4 / Math.sqrt(19)), 'SQN 4/√19 ≈ 0.917663, got ' + st.sqn);
  assert.ok(approx(st.maxDrawR, 0.5), 'maxDrawR 0.5 (2 → 1.5 walk), got ' + st.maxDrawR);
  assert.strictEqual(st.byTag.break.n, 2);
  assert.ok(approx(st.byTag.break.expectancyR, 0.75), 'break-tag expectancy 0.75');
  assert.strictEqual(st.byTag.untagged.n, 1);
  assert.ok(approx(st.byTag.untagged.expectancyR, 0.5), 'untagged expectancy 0.5');
});

// ─── 35. riskUsd ≤ 0 exclusion — counted, and the stats stay untilted ────────
group('journalStats riskUsd<=0 exclusion counted + mandatory §4e label verbatim', () => {
  const good = [
    { id: 't1', tsOpen: 900, tsClose: 1000, side: 'long', entry: 100, exit: 110, size: 1, riskUsd: 5, tag: 'break', note: '' },
    { id: 't2', tsOpen: 1900, tsClose: 2000, side: 'short', entry: 100, exit: 105, size: 2, riskUsd: 20, tag: 'break', note: '' },
    { id: 't3', tsOpen: 2900, tsClose: 3000, side: 'long', entry: 50, exit: 53, size: 5, riskUsd: 30, tag: '', note: '' },
  ];
  // Three unstatable rows: R = pnl/riskUsd is undefined at 0, sign-flipped
  // below it, and meaningless at NaN — §4e says EXCLUDE AND COUNT, never
  // silently coerce (a riskUsd-0 row folded in as R=∞ or R=0 would both lie).
  const bad = [
    { id: 'x1', tsOpen: 3900, tsClose: 4000, side: 'long', entry: 100, exit: 200, size: 1, riskUsd: 0, tag: '', note: '' },
    { id: 'x2', tsOpen: 4900, tsClose: 5000, side: 'long', entry: 100, exit: 200, size: 1, riskUsd: -5, tag: '', note: '' },
    { id: 'x3', tsOpen: 5900, tsClose: 6000, side: 'long', entry: 100, exit: 200, size: 1, riskUsd: NaN, tag: '', note: '' },
  ];
  const st = S.journalStats(good.concat(bad));
  assert.strictEqual(st.excluded, 3, 'all three unstatable rows counted');
  assert.strictEqual(st.n, 3, 'n counts statable rows only');
  // Bit-identical to group 34's hand numbers — the excluded rows (each a
  // huge fake "win") must not tilt a single stat.
  assert.ok(approx(st.expectancyR, 2 / 3) && approx(st.winRate, 2 / 3)
    && approx(st.profitFactor, 5) && approx(st.sqn, 4 / Math.sqrt(19))
    && approx(st.maxDrawR, 0.5), 'stats must equal the 3-trade hand math exactly');
  // Mandatory §4e rail label rides the output VERBATIM (the view renders it;
  // pinning it here keeps the wording from drifting).
  assert.strictEqual(st.label, 'your logged trades — descriptive record, NOT a backtest');
  // Empty-journal shape: NaN stats (never fake zeros), label still present.
  const empty = S.journalStats([]);
  assert.strictEqual(empty.n, 0);
  assert.ok(Number.isNaN(empty.expectancyR) && Number.isNaN(empty.sqn), 'no trades → NaN stats, not 0s');
  assert.strictEqual(empty.label, st.label);
});

// ─── 36. calendarReturns — UTC day/hour bucketing + the ISO-week edge ────────
group('calendarReturns UTC day/hour bucketing + ISO-week edge (Thursday rule)', () => {
  // All keys derive from tsClose in UTC (§4e). Hand-picked closes:
  //   a: Mon 2024-12-30 23:30 UTC — LATE-YEAR ISO EDGE: the week's Thursday
  //      is 2025-01-02, so the ISO week is 2025-W01 even though the DAY key
  //      says 2024. R = +2  (long 100→110, size 1, risk 5)
  //   b: Mon 2024-12-30 07:05 UTC — same UTC day, different hour.
  //      R = −0.5 (short 100→105, size 2, risk 20)
  //   c: Fri 2027-01-01 12:00 UTC — EARLY-YEAR ISO EDGE mirrored: the week's
  //      Thursday is 2026-12-31, so the ISO week is 2026-W53 (2026 starts on
  //      a Thursday → a 53-week ISO year) even though the DAY key says 2027.
  //      R = +0.5 (long 50→53, size 5, risk 30)
  const A = Date.UTC(2024, 11, 30, 23, 30);
  const B = Date.UTC(2024, 11, 30, 7, 5);
  const C = Date.UTC(2027, 0, 1, 12, 0);
  const trades = [
    { id: 'a', tsOpen: A - 1, tsClose: A, side: 'long', entry: 100, exit: 110, size: 1, riskUsd: 5, tag: '', note: '' },
    { id: 'b', tsOpen: B - 1, tsClose: B, side: 'short', entry: 100, exit: 105, size: 2, riskUsd: 20, tag: '', note: '' },
    { id: 'c', tsOpen: C - 1, tsClose: C, side: 'long', entry: 50, exit: 53, size: 5, riskUsd: 30, tag: '', note: '' },
    // Unstatable (riskUsd 0) and undatable (NaN tsClose) rows must never
    // touch a bucket — a calendar cell must mean the same R the stats mean.
    { id: 'x', tsOpen: 1, tsClose: A, side: 'long', entry: 1, exit: 2, size: 1, riskUsd: 0, tag: '', note: '' },
    { id: 'y', tsOpen: 1, tsClose: NaN, side: 'long', entry: 1, exit: 2, size: 1, riskUsd: 5, tag: '', note: '' },
  ];
  const cal = S.calendarReturns(trades);

  // Daily: same-UTC-day trades SUM (+2 − 0.5 = +1.5); day keys are UTC dates.
  assert.deepStrictEqual(Object.keys(cal.daily).sort(), ['2024-12-30', '2027-01-01']);
  assert.ok(approx(cal.daily['2024-12-30'], 1.5), 'same-day sum 1.5, got ' + cal.daily['2024-12-30']);
  assert.ok(approx(cal.daily['2027-01-01'], 0.5));

  // Hourly: the SAME two trades split into their UTC close hours.
  assert.ok(approx(cal.hourly[23], 2), 'hour 23 ← trade a');
  assert.ok(approx(cal.hourly[7], -0.5), 'hour 7 ← trade b');
  assert.ok(approx(cal.hourly[12], 0.5), 'hour 12 ← trade c');
  assert.strictEqual(Object.keys(cal.hourly).length, 3, 'untouched hours ABSENT, not 0R (no fabricated flats)');

  // Monthly: plain calendar months of the close date.
  assert.ok(approx(cal.monthly['2024-12'], 1.5));
  assert.ok(approx(cal.monthly['2027-01'], 0.5));

  // THE ISO-WEEK EDGES (the whole reason weekly keys aren't 'YYYY-Www' off
  // the raw calendar year): Mon 2024-12-30 belongs to 2025-W01 and Fri
  // 2027-01-01 belongs to 2026-W53 — keying by each date's own year would
  // split both weeks in two.
  assert.deepStrictEqual(Object.keys(cal.weekly).sort(), ['2025-W01', '2026-W53']);
  assert.ok(approx(cal.weekly['2025-W01'], 1.5), '2024-12-30 lands in 2025-W01 (Thursday rule)');
  assert.ok(approx(cal.weekly['2026-W53'], 0.5), '2027-01-01 lands in 2026-W53 (Thursday rule)');
});

// ─── 37. journal CSV round-trip identity + bad rows land in errors ───────────
group('journal CSV round-trip identity (comma+quote+newline note, ctx JSON) + bad rows → errors', () => {
  // t1's note carries a comma, doubled-quote bait AND a raw newline; its ctx
  // is the §4e auto-snapshot object serialized into ONE CSV column — commas
  // and quotes inside JSON are exactly why the writer must RFC-4180-quote
  // and the reader must be a real parser, not a split(',').
  const t1 = {
    id: 'a1', tsOpen: 1751700000000, tsClose: 1751703600000, side: 'long',
    entry: 108250.5, exit: 109001.25, size: 0.042, riskUsd: 150,
    tag: 'breakout', note: 'note with, comma and "quotes" and\na newline',
    ctx: { mark: 108251.5, fundingRate: 0.0001, oi: 52341.25, cvdSlope: -1234.5,
           confluenceTally: { bullish: 3, bearish: 2, neutral: 3, na: 1 } },
  };
  const t2 = { id: 'a2', tsOpen: 1, tsClose: 2, side: 'short', entry: 100, exit: 99.5, size: 1, riskUsd: 10, tag: '', note: '' };   // no ctx — column stays ''

  const csv = S.journalToCsv([t1, t2]);
  const back = S.validateJournalCsv(csv);
  assert.strictEqual(back.errors.length, 0, 'clean export re-imports with zero errors: ' + JSON.stringify(back.errors));
  // IDENTITY: every field — floats via String()↔Number() shortest-round-trip,
  // the newline note byte-for-byte, ctx deep-equal, and NO ctx key on t2.
  assert.deepStrictEqual(back.trades, [t1, t2]);
  assert.ok(!('ctx' in back.trades[1]), 'empty ctx column → no ctx key (not ctx:undefined)');

  // Bad rows: each lands in errors with its 1-BASED line number (header =
  // line 1) and imports NOTHING, while the good rows around them still
  // import — §4e: import never silently coerces.
  const lines = csv.split('\n');
  const header = lines[0];
  const goodRow = lines.slice(1).find((l) => l.startsWith('a2,'));
  const badCsv = [
    header,
    goodRow,                                                    // line 2 — good
    'b1,1,2,LONG,100,101,1,10,,,',                              // line 3 — side must be lowercase long|short
    'b2,1,2,long,100,101,1,abc,,,',                             // line 4 — riskUsd not a number
    'b3,1,2,long,100,101,1,10',                                 // line 5 — wrong column count
    'b4,1,2,long,100,101,1,10,,,not-json',                      // line 6 — ctx column not valid JSON
    'b5,1,2,long,100,101,1,-4,,,',                              // line 7 — riskUsd NEGATIVE: a VALID row (stats exclude it)
  ].join('\n') + '\n';
  const res = S.validateJournalCsv(badCsv);
  assert.strictEqual(res.trades.length, 2, 'good row + the riskUsd<0 row import (stats-layer exclusion, not import rejection)');
  assert.strictEqual(res.trades[0].id, 'a2');
  assert.strictEqual(res.trades[1].id, 'b5');
  assert.strictEqual(res.trades[1].riskUsd, -4);
  assert.deepStrictEqual(res.errors.map((e) => e.line), [3, 4, 5, 6], '1-based line numbers, header = 1');
  assert.ok(/side/.test(res.errors[0].reason), 'reason names the field: ' + res.errors[0].reason);
  assert.ok(/riskUsd/.test(res.errors[1].reason));
  assert.ok(/columns/.test(res.errors[2].reason));
  assert.ok(/ctx/.test(res.errors[3].reason));

  // Header mismatch / empty file: refused up front, nothing guessed.
  assert.strictEqual(S.validateJournalCsv('id,side\n1,long\n').trades.length, 0);
  assert.ok(/header/.test(S.validateJournalCsv('id,side\n').errors[0].reason));
  assert.ok(/empty/.test(S.validateJournalCsv('').errors[0].reason));
});

// ─── 38. buildDeltaProfile: Σdelta identity + p95 intensity (§4f) ────────────
group('buildDeltaProfile Σdelta≡Σbuy−Σsell + p95 intensity + all-buy/all-zero bounds', () => {
  // Hand case (integers so every equality below is EXACT, no float slack):
  // deltas 90:0, 100:+3, 110:−8 → mags sorted [0,3,8], nearest-rank p95 of
  // n=3 is index ceil(0.95·3)−1 = 2 → p95 = 8 → intensities 0, 3/8, 1.
  const dp = S.buildDeltaProfile([
    { lvl: 100, buy_vol: 5, sell_vol: 2 },
    { lvl: 110, buy_vol: 1, sell_vol: 9 },
    { lvl: 90, buy_vol: 3, sell_vol: 3 },
  ]);
  assert.deepStrictEqual(dp, [
    { lvl: 90, delta: 0, intensity: 0 },
    { lvl: 100, delta: 3, intensity: 0.375 },
    { lvl: 110, delta: -8, intensity: 1 },
  ], 'ascending lvl, raw deltas, nearest-rank-p95 intensities — all exact');
  // The §4f binding invariant, checked as an identity not an approximation.
  assert.strictEqual(dp.reduce((a, r) => a + r.delta, 0), (5 + 1 + 3) - (2 + 9 + 3),
    'Σdelta must equal Σbuy − Σsell EXACTLY (intensity is display-only, delta untouched)');

  // The live 'today' selector feeds ProfileStore-adjacent {price, buy, sell}
  // rows through the same builder — both spellings, one output shape.
  assert.deepStrictEqual(S.buildDeltaProfile([{ price: 100, buy: 5, sell: 2 }]),
    [{ lvl: 100, delta: 3, intensity: 1 }], 'alt {price,buy,sell} spelling accepted');

  // ADVERSARIAL — all-buy day: every delta positive and one outlier level.
  // p95 must keep every intensity in [0,1] with the outlier CLAMPED to 1
  // (max-normalizing would compress the bulk toward the neutral midpoint).
  const allBuy = [];
  for (let i = 0; i < 19; i++) allBuy.push({ lvl: 100 + i, buy_vol: 2 + i, sell_vol: 0 });
  allBuy.push({ lvl: 200, buy_vol: 1000, sell_vol: 0 });
  const ab = S.buildDeltaProfile(allBuy);
  assert.strictEqual(ab.length, 20);
  for (const r of ab) assert.ok(r.intensity >= 0 && r.intensity <= 1, 'intensity bounded: ' + r.intensity);
  assert.strictEqual(ab[ab.length - 1].intensity, 1, 'outlier saturates at exactly 1');
  assert.strictEqual(ab.reduce((a, r) => a + r.delta, 0), 1000 + allBuy.slice(0, 19).reduce((a, r) => a + r.buy_vol, 0),
    'identity holds on the all-buy day too');

  // ADVERSARIAL — perfectly balanced profile: p95 = 0 must render NEUTRAL
  // (intensity 0 everywhere), never 0/0 = NaN into a color ramp.
  assert.deepStrictEqual(S.buildDeltaProfile([
    { lvl: 1, buy_vol: 2, sell_vol: 2 }, { lvl: 2, buy_vol: 0, sell_vol: 0 },
  ]), [{ lvl: 1, delta: 0, intensity: 0 }, { lvl: 2, delta: 0, intensity: 0 }]);

  // Hygiene: malformed rows are SKIPPED (validTrade discipline), non-array → [].
  assert.deepStrictEqual(S.buildDeltaProfile([null, { lvl: NaN, buy_vol: 1, sell_vol: 1 }, { lvl: 5 }, 'x']), []);
  assert.deepStrictEqual(S.buildDeltaProfile('nope'), []);
});

// ─── 39. AnchoredVwap: streaming ≡ batch to 1e-9 + reset + single-trade σ (§4f) ─
group('AnchoredVwap streaming≡batch 1e-9 + reset anchor cut + single-trade σ=0', () => {
  // 500 deterministic LCG trades at BTC price scale (~60000 ± 100) — the
  // regime where the naive Σqp²−(Σqp)²/W form loses ~8 digits; the Welford
  // stream must still match the well-conditioned TWO-PASS batch to 1e-9.
  let x = 42 >>> 0;
  const lcg = () => { x = (Math.imul(x, 1664525) + 1013904223) >>> 0; return x / 4294967296; };
  const trades = [];
  for (let i = 0; i < 500; i++) trades.push({ ts: i, price: 60000 + lcg() * 200 - 100, qty: 0.001 + lcg() * 2, aggressorBuy: true });
  const av = S.AnchoredVwap();
  for (const t of trades) av.onTrade(t);
  let sq = 0, spq = 0;
  for (const t of trades) { sq += t.qty; spq += t.price * t.qty; }
  const vb = spq / sq;                       // batch vwap = Σpq/Σq
  let ss = 0;
  for (const t of trades) ss += t.qty * (t.price - vb) * (t.price - vb);
  const sigB = Math.sqrt(ss / sq);           // batch σ = √(Σq(p−vwap)²/Σq) — /v1/vwap's formula
  const b = av.bands();
  assert.ok(approx(b.vwap, vb, 1e-9), 'vwap vs batch: |Δ| = ' + Math.abs(b.vwap - vb));
  assert.ok(approx(b.s1, sigB, 1e-9), 'σ vs batch: |Δ| = ' + Math.abs(b.s1 - sigB));
  assert.ok(approx(b.s2, 2 * sigB, 1e-9), 's2 must be exactly the 2σ distance');
  assert.strictEqual(b.n, 500);

  // reset(anchor) is an EVENT-time cut: the pre-reset trade and the ts<anchor
  // trade are both gone. Hand case: vwap (1·100+3·102)/4 = 101.5,
  // σ² = (1·1.5² + 3·0.5²)/4 = 0.75.
  av.reset(5000);
  av.onTrade({ ts: 4000, price: 999, qty: 5, aggressorBuy: true });  // pre-anchor — dropped
  av.onTrade({ ts: 6000, price: 100, qty: 1, aggressorBuy: true });
  av.onTrade({ ts: 7000, price: 102, qty: 3, aggressorBuy: false });
  const rb = av.bands();
  assert.strictEqual(rb.vwap, 101.5);
  assert.ok(approx(rb.s1, Math.sqrt(0.75)), 'σ after reset = √0.75');
  assert.strictEqual(rb.n, 2, 'the 999 pre-anchor print must not count');

  // ADVERSARIAL — single trade: population σ is exactly 0, never NaN (a NaN
  // here would blank the band series on the first post-anchor print).
  const one = S.AnchoredVwap();
  one.onTrade({ ts: 1, price: 100.5, qty: 2, aggressorBuy: true });
  assert.deepStrictEqual(one.bands(), { vwap: 100.5, s1: 0, s2: 0, n: 1 });

  // Empty state → NaN bands ("no data" never looks like price 0) + n:0;
  // malformed trades never enter the state.
  const empty = S.AnchoredVwap().bands();
  assert.ok(Number.isNaN(empty.vwap) && Number.isNaN(empty.s1) && Number.isNaN(empty.s2) && empty.n === 0);
  const dirty = S.AnchoredVwap();
  dirty.onTrade(null); dirty.onTrade({ ts: 1, price: NaN, qty: 1 }); dirty.onTrade({ ts: 1, price: 100, qty: 0 });
  assert.strictEqual(dirty.bands().n, 0, 'garbage/zero-qty trades dropped (validTrade rail)');
});

// ─── 40. OfiStore: hand-computed CKS e_t + zscore + gap/crossed hygiene (§4f) ─
group('OfiStore hand-computed 3-snapshot e_t (6, 20) + zscore 1/√2 + gap/crossed/unequal', () => {
  // Hand computation against the Cont–Kukanov–Stoikov per-side rule the
  // builder header states (levels=2, index-aligned):
  //   A(seed): bids [100@5, 99@4]   asks [101@6, 102@7]
  //   B:       bids [100@8, 99@4]   asks [101@3, 102@7]
  //     bid0 price held  → +（8−5) = +3;  bid1 held → 0
  //     ask0 price held  → −(3−6) = +3;  ask1 held → 0        e_B = 6
  //   C:       bids [101@2, 100@8]  asks [102@7, 103@1]
  //     bid0 ROSE → +2; bid1 ROSE → +8   (new demand at better prices)
  //     ask0 ROSE → +3; ask1 ROSE → +7   (standing asks lifted = buying)
  //                                                            e_C = 20
  const ofi = S.OfiStore({ levels: 2 });
  assert.strictEqual(ofi.onDepthSample(1000, {
    bids: [{ price: 100, qty: 5 }, { price: 99, qty: 4 }],
    asks: [{ price: 101, qty: 6 }, { price: 102, qty: 7 }],
  }), null, 'first sample seeds — e needs two book states');
  assert.strictEqual(ofi.onDepthSample(2000, {
    bids: [{ price: 100, qty: 8 }, { price: 99, qty: 4 }],
    asks: [{ price: 101, qty: 3 }, { price: 102, qty: 7 }],
  }), 6, 'e_B: bid add +3, ask cancel +3');
  assert.strictEqual(ofi.onDepthSample(3000, {
    bids: [{ price: 101, qty: 2 }, { price: 100, qty: 8 }],
    asks: [{ price: 102, qty: 7 }, { price: 103, qty: 1 }],
  }), 20, 'e_C: both bid levels rose (+10), both asks lifted (+10)');
  // Rolling sum: window spanning both samples vs a 1 s window that evicts e_B.
  assert.deepStrictEqual(ofi.series(60000), [{ ts: 2000, ofi: 6 }, { ts: 3000, ofi: 26 }]);
  assert.deepStrictEqual(ofi.series(1000), [{ ts: 2000, ofi: 6 }, { ts: 3000, ofi: 20 }]);
  // zscore over the 2-sample tail: mean 13, sample sd (ddof=1) √98 = 7√2 →
  // z = 7/(7√2) = 1/√2. Machine-exactness of the closed form pinned to 1e-12.
  assert.ok(approx(ofi.zscore(2), 1 / Math.SQRT2, 1e-12), 'zscore(2) = 1/√2, got ' + ofi.zscore(2));

  // ADVERSARIAL — empty ladder is a RECONNECT GAP (§0.7): it must clear the
  // seed so no e is fabricated across it, and the next real ladder re-seeds.
  const g = S.OfiStore({ levels: 5 });
  g.onDepthSample(1, { bids: [{ price: 100, qty: 5 }, { price: 99, qty: 4 }], asks: [{ price: 101, qty: 6 }] });
  assert.strictEqual(g.onDepthSample(2, { bids: [{ price: 100, qty: 5 }], asks: [{ price: 101, qty: 6 }] }), -4,
    'unequal ladder depth = degenerate remove: vanished bid level → −q_prev');
  assert.strictEqual(g.onDepthSample(3, { bids: [], asks: [] }), null, 'empty ladder skipped');
  assert.strictEqual(g.onDepthSample(4, { bids: [{ price: 100, qty: 5 }], asks: [{ price: 101, qty: 6 }] }), null,
    'post-gap sample must RE-SEED, never diff across the gap');
  assert.strictEqual(g.series(60000).length, 1, 'only the real e (−4) is retained across the gap');
  // Crossed book (bid ≥ ask happens transiently on fast feeds): the per-side
  // rule never divides or compares across sides — result stays finite. Hand:
  // bid rose 100→102 → +5; ask held → 0.
  const ce = g.onDepthSample(5, { bids: [{ price: 102, qty: 5 }], asks: [{ price: 101, qty: 6 }] });
  assert.strictEqual(ce, 5, 'crossed snapshot: finite hand value, no special-casing');
  // Hygiene: non-finite ts / null grouped are refused; zscore honest-NaNs.
  assert.strictEqual(g.onDepthSample(NaN, { bids: [{ price: 1, qty: 1 }], asks: [] }), null);
  assert.strictEqual(g.onDepthSample(6, null), null);
  assert.ok(Number.isNaN(S.OfiStore({}).zscore(300)), 'no samples → NaN (unknown, never a fabricated 0)');
  assert.ok(Number.isNaN(S.OfiStore({}).zscore(1)), 'window < 2 → NaN');
});

// ─── 41. microprice: Stoikov hand case + BookStore + null guards (§4f) ───────
group('microprice hand case 100.75 (thin-ask pull) + real BookStore + empty→null', () => {
  // Hand: Pᵇ=100 Qᵇ=3, Pᵃ=101 Qᵃ=1 → (100·1 + 101·3)/4 = 403/4 = 100.75 —
  // ABOVE the 100.5 mid because the ask queue is the thin side (the next
  // move is likelier up; Stoikov's imbalance-weighted mid).
  assert.strictEqual(S.microprice({ bid: [100, 3], ask: [101, 1] }), 100.75);
  // Same numbers through a REAL BookStore (the shape MicrostructureView feeds).
  const book = S.BookStore();
  book.applyDepth({ kind: 'depth', ex: 'binancef', ts: 1, bids: [[100, 3]], asks: [[101, 1]], isSnapshot: true });
  assert.strictEqual(S.microprice(book), 100.75, 'BookStore.best() path must agree with the plain-object path');
  // Balanced book: microprice degenerates to the mid exactly.
  assert.strictEqual(S.microprice({ bid: [100, 2], ask: [101, 2] }), 100.5);
  // Empty/one-sided/zero-depth books → null ("no estimate", never NaN into a
  // chart series) — the empty-BookStore path is the reconnect reality.
  assert.strictEqual(S.microprice(S.BookStore()), null);
  assert.strictEqual(S.microprice({ bid: [100, 1] }), null, 'one-sided book → null');
  assert.strictEqual(S.microprice({ bid: [100, 0], ask: [101, 0] }), null, 'zero best-depth → null (0/0 guard)');
  assert.strictEqual(S.microprice(null), null);
  assert.strictEqual(S.microprice({ bid: [NaN, 1], ask: [101, 1] }), null, 'non-finite level → null');
});

// ─── 42. stackedImbalances: zone fire + die-on-cross + gap/alternation (§4f) ─
group('stackedImbalances zone fire/die-on-cross + gap breaks run + alternating sides never stack', () => {
  // Constructed finished bar, tick=1, k=3, minVol=1 (defaults): buy flags at
  // 104 (9≥3·2), 103 (9≥3·3), 102 (12≥3·4) — a 3-run; 101 fails (2<3·5);
  // 105 is dust (0.5<minVol); 100 flags alone via the vacant-below rule but a
  // 1-run never zones. Expected: exactly {top:104, bottom:102, side:'buy'}.
  const bar0 = {
    t: 0, finished: true, l: 100, h: 105, levels: [
      { price: 105, buy: 0.5, sell: 0 }, { price: 104, buy: 9, sell: 0 },
      { price: 103, buy: 9, sell: 2 }, { price: 102, buy: 12, sell: 3 },
      { price: 101, buy: 2, sell: 4 }, { price: 100, buy: 1, sell: 5 }],
  };
  const bar1 = { t: 60000, finished: true, l: 103, h: 106, levels: [{ price: 103, buy: 0.5, sell: 0.5 }] };
  assert.deepStrictEqual(S.stackedImbalances([bar0, bar1], { tickSize: 1 }),
    [{ top: 104, bottom: 102, side: 'buy', barIdx: 0, active: true }],
    'one buy zone, still active — bar1 (l=103) never traded below 102');

  // Die-on-cross via the OPEN bar: its prints already happened, so its range
  // participates in invalidation — but a half-formed bar creates NO new zone
  // even with a flag-worthy 3-run (flags on open bars flicker).
  const open = {
    t: 120000, finished: false, l: 101.5, h: 103, levels: [
      { price: 104, buy: 9, sell: 0 }, { price: 103, buy: 9, sell: 0 }, { price: 102, buy: 9, sell: 0 }],
  };
  assert.deepStrictEqual(S.stackedImbalances([bar0, bar1, open], { tickSize: 1 }),
    [{ top: 104, bottom: 102, side: 'buy', barIdx: 0, active: false }],
    'open bar l=101.5 < bottom 102 kills the zone; its own run creates nothing');

  // Sell mirror + the STRICT inequality boundary: a later high EQUAL to the
  // zone top does not kill it (§4f "traded through" = strictly beyond).
  const sbar = {
    t: 0, finished: true, l: 96, h: 100, levels: [
      { price: 100, buy: 0, sell: 0.5 }, { price: 99, buy: 0, sell: 9 },
      { price: 98, buy: 0, sell: 9 }, { price: 97, buy: 0, sell: 9 },
      { price: 96, buy: 5, sell: 0.5 }],
  };
  assert.deepStrictEqual(S.stackedImbalances([sbar, { t: 1, finished: true, l: 95, h: 99, levels: [] }], { tickSize: 1 }),
    [{ top: 99, bottom: 97, side: 'sell', barIdx: 0, active: true }], 'h == top holds the zone');
  assert.deepStrictEqual(S.stackedImbalances([sbar, { t: 1, finished: true, l: 95, h: 99.5, levels: [] }], { tickSize: 1 }),
    [{ top: 99, bottom: 97, side: 'sell', barIdx: 0, active: false }], 'h > top kills it');

  // ADVERSARIAL — a vacant grid level BREAKS the run (102 missing → runs of
  // 2 and 1, no zone): "consecutive" means price-adjacent on the tick grid.
  assert.deepStrictEqual(S.stackedImbalances([{
    t: 0, finished: true, l: 101, h: 104, levels: [
      { price: 104, buy: 9, sell: 0 }, { price: 103, buy: 9, sell: 0 }, { price: 101, buy: 9, sell: 0 }],
  }], { tickSize: 1 }), [], 'gap in the ladder is never bridged');

  // ADVERSARIAL — aggression alternating sides level-by-level: the diagonal
  // test fails everywhere (each 9 faces 3·9=27) so NOTHING stacks — flipping
  // flow is the opposite of the pattern this builder names.
  assert.deepStrictEqual(S.stackedImbalances([{
    t: 0, finished: true, l: 101, h: 104, levels: [
      { price: 104, buy: 9, sell: 0 }, { price: 103, buy: 0, sell: 9 },
      { price: 102, buy: 9, sell: 0 }, { price: 101, buy: 0, sell: 9 }],
  }], { tickSize: 1 }), [], 'alternating buy/sell rows never form a zone');
  // Hygiene: non-array/empty input → no zones, no throw.
  assert.deepStrictEqual(S.stackedImbalances(null, {}), []);
});

// ─── 43. AbsorptionDetector: fire + quiet + label + first-bar guard (§4f) ────
group('AbsorptionDetector spike+no-progress fires labeled, progress/no-spike quiet, first bar never fires', () => {
  // Spike bar: level 100 prints 35 (buy 30 / sell 5) against a median level
  // volume of 4 → 35 ≥ 3·4 candidate, side 'buy' (buyers were hitting into
  // it). Next bar h=101: progress = 101−100 = 1 which is NOT > 1·tick → no
  // follow-through → exactly one event, ts = the SPIKE bar's t.
  const spikeLevels = [
    { price: 104, buy: 2, sell: 2 }, { price: 103, buy: 2, sell: 2 },
    { price: 102, buy: 2, sell: 2 }, { price: 101, buy: 2, sell: 2 },
    { price: 100, buy: 30, sell: 5 }];
  let d = S.AbsorptionDetector({ tickSize: 1 });
  d.onBar({ t: 60000, finished: true, h: 104, l: 100, levels: spikeLevels });
  // ADVERSARIAL — the first bar alone can NEVER fire: absorption is defined
  // against the NEXT bar and there is none yet.
  assert.strictEqual(d.events().length, 0, 'session first bar: candidates pend, nothing fires');
  // An unfinished bar between spike and resolution is ignored ENTIRELY
  // (§4 rail: half-formed bars flicker — they neither resolve nor pend).
  d.onBar({ t: 90000, finished: false, h: 200, l: 0, levels: [{ price: 100, buy: 1, sell: 1 }] });
  d.onBar({ t: 120000, finished: true, h: 101, l: 99, levels: [{ price: 100, buy: 1, sell: 1 }] });
  assert.deepStrictEqual(d.events(),
    [{ kind: 'absorption', ts: 60000, price: 100, side: 'buy', vol: 35, medianVol: 4, label: 'heuristic' }],
    'one labeled event — the label rides the EVENT (§4f heuristic rail)');

  // Progress kills it: next bar clears price+1·tick (102.5 − 100 > 1).
  d = S.AbsorptionDetector({ tickSize: 1 });
  d.onBar({ t: 60000, finished: true, h: 104, l: 100, levels: spikeLevels });
  d.onBar({ t: 120000, finished: true, h: 102.5, l: 99, levels: [{ price: 100, buy: 1, sell: 1 }] });
  assert.strictEqual(d.events().length, 0, 'follow-through = no absorption');

  // No spike, no candidates: all levels at the median (4 < 3·4).
  d = S.AbsorptionDetector({ tickSize: 1 });
  d.onBar({ t: 60000, finished: true, h: 104, l: 100, levels: spikeLevels.map((c) => ({ price: c.price, buy: 2, sell: 2 })) });
  d.onBar({ t: 120000, finished: true, h: 101, l: 99, levels: [{ price: 100, buy: 1, sell: 1 }] });
  assert.strictEqual(d.events().length, 0, 'flat bar never spikes (median baseline)');

  // Sell-side mirror: dominant sell side resolves against the next bar's LOW
  // (100 − 99.5 = 0.5 ≤ 1·tick → no downward follow-through → fires 'sell').
  d = S.AbsorptionDetector({ tickSize: 1 });
  d.onBar({
    t: 60000, finished: true, h: 104, l: 100,
    levels: spikeLevels.map((c) => (c.price === 100 ? { price: 100, buy: 5, sell: 30 } : c)),
  });
  d.onBar({ t: 120000, finished: true, h: 101, l: 99.5, levels: [{ price: 100, buy: 1, sell: 1 }] });
  const sev = d.events();
  assert.strictEqual(sev.length, 1);
  assert.strictEqual(sev[0].side, 'sell');
  assert.strictEqual(sev[0].label, 'heuristic', 'sell mirror carries the label too');
});

// ─── 44. SessionClock: exact half-open boundaries + boxes (§4f) ──────────────
group('SessionClock exact boundaries (07:00 both, 08:00 leaves Asia, 21–24 dead zone) + boxesFor', () => {
  const sc = S.SessionClock();
  const D0 = Date.UTC(2026, 6, 6); // any UTC midnight — the clock is pure modulo arithmetic
  const H = 3600000;
  // Half-open [start,end): the §4f FX-desk-convention hours, boundary-exact.
  assert.deepStrictEqual(sc.tag(D0), ['Asia'], '00:00:00.000 — Asia opens');
  assert.deepStrictEqual(sc.tag(D0 + 7 * H - 1), ['Asia'], '06:59:59.999 — London not yet');
  assert.deepStrictEqual(sc.tag(D0 + 7 * H), ['Asia', 'London'], '07:00:00.000 EXACTLY — the real overlap');
  assert.deepStrictEqual(sc.tag(D0 + 8 * H), ['London'], '08:00:00.000 — Asia is OVER (half-open end)');
  assert.deepStrictEqual(sc.tag(D0 + 12 * H), ['London', 'NY'], '12:00 — London/NY overlap');
  assert.deepStrictEqual(sc.tag(D0 + 16 * H), ['NY'], '16:00 — London closes');
  assert.deepStrictEqual(sc.tag(D0 + 21 * H), [], '21:00 — NY closes; the honest dead zone begins');
  assert.deepStrictEqual(sc.tag(D0 + 24 * H - 1), [], '23:59:59.999 — still no session (never wraps into Asia early)');
  assert.deepStrictEqual(sc.tag(D0 + 24 * H), ['Asia'], 'next midnight — Asia again');
  // ADVERSARIAL — pre-1970 ts must not break the modulo (negative-safe), and
  // unknown time is NO session, never a guess.
  assert.deepStrictEqual(sc.tag(-1), [], 'ts −1 ≡ 23:59:59.999 UTC 1969-12-31 — dead zone, not a crash');
  assert.deepStrictEqual(sc.tag(NaN), []);
  // boxesFor: pure arithmetic on the CALLER's anchor (never asks the OS).
  assert.deepStrictEqual(sc.boxesFor(D0), [
    { name: 'Asia', startMs: D0, endMs: D0 + 8 * H },
    { name: 'London', startMs: D0 + 7 * H, endMs: D0 + 16 * H },
    { name: 'NY', startMs: D0 + 12 * H, endMs: D0 + 21 * H },
  ]);
  assert.deepStrictEqual(sc.boxesFor(NaN), []);
});

// ─── 45. cumDelta: hand accumulation + FootprintStore integration (§4f) ──────
group('cumDelta hand accumulation (skips never zero-coerced) + real FootprintStore tape', () => {
  // Hand case with malformed bars interleaved: null / string t / NaN delta
  // are SKIPPED — a fabricated flat bar would fake "no net flow".
  assert.deepStrictEqual(
    S.cumDelta([{ t: 1, delta: 5 }, { t: 2, delta: -2 }, null, { t: 'x', delta: 1 }, { t: 3, delta: NaN }, { t: 4, delta: 4 }]),
    [{ ts: 1, cum: 5 }, { ts: 2, cum: 3 }, { ts: 4, cum: 7 }]);
  assert.deepStrictEqual(S.cumDelta(undefined), []);

  // Integration: a real FootprintStore tape (2 finished + 1 open bar).
  // Hand deltas: bar0 +2−0.5 = +1.5, bar1 −3, bar2(open) +1 → cum 1.5, −1.5, −0.5.
  const fp = S.FootprintStore({ barMs: 60000, tickSize: 1 });
  fp.onTrade({ ts: 0, price: 100, qty: 2, aggressorBuy: true });
  fp.onTrade({ ts: 1000, price: 100, qty: 0.5, aggressorBuy: false });
  fp.onTrade({ ts: 60000, price: 101, qty: 3, aggressorBuy: false });
  fp.onTrade({ ts: 120000, price: 102, qty: 1, aggressorBuy: true });
  const cd = S.cumDelta(fp.bars());
  assert.deepStrictEqual(cd, [{ ts: 0, cum: 1.5 }, { ts: 60000, cum: -1.5 }, { ts: 120000, cum: -0.5 }]);
  // Session-anchored invariant: the endpoint equals Σ per-bar delta exactly.
  assert.strictEqual(cd[cd.length - 1].cum, fp.bars().reduce((a, b) => a + b.delta, 0));
});

// ─── 46. terminal-hfdata: archived-row normalization + URL allowlist (§4f W2) ─
group('hfdata normalizeArchivedRow BigInt→Number + byodRowToEvent round-trip + URL allowlist throws', () => {
  // The ONE decode/BYOD type mismatch (§4f probe): parquet BIGINT columns
  // arrive as JS BigInt — coerced key-agnostically; everything else passes
  // through UNTOUCHED (depth sides stay the collector's JSON strings —
  // byodRowToEvent owns that parse, one vocabulary one owner).
  const row = HF.normalizeArchivedRow({
    exchange: 'bybit', symbol: 'BTCUSDT', trade_id: 't1',
    ts_ms: 1751673600123n, price: 61850.5, qty: 0.001, aggressor_buy: true,
  });
  assert.deepStrictEqual(row, {
    exchange: 'bybit', symbol: 'BTCUSDT', trade_id: 't1',
    ts_ms: 1751673600123, price: 61850.5, qty: 0.001, aggressor_buy: true,
  });
  assert.strictEqual(typeof row.ts_ms, 'number', 'BigInt ts_ms → Number (exact below 2^53)');
  // Normalized rows feed the REAL replay mapper UNCHANGED — the §4f promise
  // that archived parquet and live BYOD speak one row vocabulary.
  assert.deepStrictEqual(R.byodRowToEvent('trades', row),
    { kind: 'trade', ex: 'bybit', ts: 1751673600123, price: 61850.5, qty: 0.001, aggressorBuy: true, id: 't1' });
  // Key-agnostic coercion (schema-drift-safe) + string passthrough.
  const dr = HF.normalizeArchivedRow({ expiry_ts: 1787904000000n, bids: '[[62000.1,1.5]]', name: 'BTC-28AUG26-105000-C' });
  assert.strictEqual(dr.expiry_ts, 1787904000000);
  assert.strictEqual(dr.bids, '[[62000.1,1.5]]', 'depth JSON strings stay strings — byodRowToEvent parses them');
  // Non-objects are refused, never guessed.
  assert.strictEqual(HF.normalizeArchivedRow(null), null);
  assert.strictEqual(HF.normalizeArchivedRow(42), null);

  // Resolve-URL contract: hive `=` sent as %3D (the probed router requirement).
  assert.strictEqual(HF.archivedParquetUrl('azulcoder/btc-quant-ticks', '2026-07-05', 'trades'),
    'https://huggingface.co/datasets/azulcoder/btc-quant-ticks/resolve/main/data/date%3D2026-07-05/trades.parquet');
  // ADVERSARIAL — the allowlist THROWS on anything that could smuggle path
  // segments into the URL (caller bug, refused loudly): bad date shapes,
  // uppercase/injection table names, slash-less repos.
  for (const args of [
    ['azulcoder/btc-quant-ticks', '2026/07/05', 'trades'],
    ['azulcoder/btc-quant-ticks', '2026-7-5', 'trades'],
    ['azulcoder/btc-quant-ticks', '2026-07-05', 'Trades'],
    ['azulcoder/btc-quant-ticks', '2026-07-05', 'a;b'],
    ['azulcoder/btc-quant-ticks', '2026-07-05', 'a/../b'],
    ['no-slash', '2026-07-05', 'trades'],
  ]) {
    assert.throws(() => HF.archivedParquetUrl(args[0], args[1], args[2]), /bad (repo|date|table)/,
      'must throw for ' + JSON.stringify(args));
  }
});

// ─── 47. FootprintStore delta path: deltaMin/deltaMax/deltaPct (§4g) ─────────
group('footprint deltaMin/deltaMax hand-written running path + deltaPct exact', () => {
  const fp = S.FootprintStore({ barMs: 60000, tickSize: 1 });
  const T0 = 1783076400000; // bar-aligned epoch ms (group 7's anchor)
  const mk = (ts, price, qty, buy) => ({ kind: 'trade', ex: 'bybit', ts, price, qty, aggressorBuy: buy, id: String(ts) });

  // Bar 1 — running-delta path BY HAND (0-anchored, one step per trade):
  //   start 0 → sell 2.0 → −2.0   (path min)
  //            → buy 1.0 → −1.0
  //            → buy 4.0 → +3.0   (path max)
  //            → sell 0.5 → +2.5  (final delta)
  // So deltaMin = −2, deltaMax = 3 — NEITHER equals the final delta 2.5,
  // which is exactly what the min/max fields add over plain delta.
  fp.onTrade(mk(T0 + 1000, 100, 2.0, false));
  fp.onTrade(mk(T0 + 2000, 101, 1.0, true));
  fp.onTrade(mk(T0 + 3000, 101, 4.0, true));
  fp.onTrade(mk(T0 + 4000, 100, 0.5, false));

  // The path is tracked AS TRADES ARRIVE — the open bar already reports it.
  const open = fp.current();
  assert.strictEqual(open.deltaMin, -2, 'open bar deltaMin');
  assert.strictEqual(open.deltaMax, 3, 'open bar deltaMax');

  // All inputs are binary-exact doubles → strict equality, no approx slack.
  fp.onTrade(mk(T0 + 61000, 100, 1.0, true)); // next bar closes bar 1
  const bar1 = fp.bars()[0];
  assert.ok(bar1.finished, 'bar 1 finished');
  assert.strictEqual(bar1.delta, 2.5, 'delta = +1 +4 −2 −0.5');
  assert.strictEqual(bar1.totalVol, 7.5);
  assert.strictEqual(bar1.deltaMin, -2, 'deltaMin = path minimum, not first/last value');
  assert.strictEqual(bar1.deltaMax, 3, 'deltaMax = path maximum');
  assert.strictEqual(bar1.deltaPct, 2.5 / 7.5, 'deltaPct = delta/totalVol exact');

  // Bar 2 — one-directional path: 0 → +1 → +3. The path never goes negative,
  // so deltaMin stays at the 0 ANCHOR (a min over {0, path}, not over trades).
  fp.onTrade(mk(T0 + 62000, 100, 2.0, true));
  fp.onTrade(mk(T0 + 121000, 100, 1.0, false)); // third bar closes bar 2
  const bar2 = fp.bars()[1];
  assert.strictEqual(bar2.deltaMin, 0, 'buy-only bar: deltaMin anchored at 0');
  assert.strictEqual(bar2.deltaMax, 3);
  assert.strictEqual(bar2.deltaPct, 1, 'all-buy bar: deltaPct = 1');
});

// ─── 48. Unfinished-auction flags: both-sided vs one-sided extremes (§4g) ────
group('unfinished-auction flags: both-sided extreme true, one-sided false, open bar never', () => {
  const fp = S.FootprintStore({ barMs: 60000, tickSize: 1 });
  const T0 = 1783076400000;
  const mk = (ts, price, qty, buy) => ({ kind: 'trade', ex: 'bybit', ts, price, qty, aggressorBuy: buy, id: String(ts) });

  // Bar 1 — BOTH extreme levels print both sides: two-sided business was
  // still being done at the extremes → both flags must come up true.
  fp.onTrade(mk(T0 + 1000, 105, 2.0, true));   // high level 105: buy…
  fp.onTrade(mk(T0 + 2000, 105, 1.0, false));  // …AND sell
  fp.onTrade(mk(T0 + 3000, 102, 1.0, true));   // interior (must not matter)
  fp.onTrade(mk(T0 + 4000, 100, 0.5, true));   // low level 100: buy…
  fp.onTrade(mk(T0 + 5000, 100, 1.5, false));  // …AND sell

  // Bar 2 — CLEAN extremes: high printed only buys (buyers lifting offers),
  // low only sells. The auction finished its business → both flags false.
  fp.onTrade(mk(T0 + 61000, 105, 2.0, true));  // closes bar 1
  fp.onTrade(mk(T0 + 62000, 100, 1.0, false));
  fp.onTrade(mk(T0 + 63000, 103, 1.0, false)); // interior both-sided is fine
  fp.onTrade(mk(T0 + 64000, 103, 1.0, true));

  // Bar 3 (open) — both-sided at its extreme, but flags are FINISHED-BAR-ONLY
  // (same discipline as the diagonal imbalance flags: half-formed flickers).
  fp.onTrade(mk(T0 + 121000, 104, 1.0, true)); // closes bar 2
  fp.onTrade(mk(T0 + 122000, 104, 1.0, false));

  const bars = fp.bars();
  assert.strictEqual(bars.length, 3);
  assert.strictEqual(bars[0].unfinishedHigh, true, 'both-sided high → unfinishedHigh');
  assert.strictEqual(bars[0].unfinishedLow, true, 'both-sided low → unfinishedLow');
  assert.strictEqual(bars[1].unfinishedHigh, false, 'buy-only high finished its auction');
  assert.strictEqual(bars[1].unfinishedLow, false, 'sell-only low finished its auction');
  assert.strictEqual(bars[2].finished, false);
  assert.strictEqual(bars[2].unfinishedHigh, false, 'open bar never flags');
  assert.strictEqual(bars[2].unfinishedLow, false, 'open bar never flags');
});

// ─── 49. TapeIntensityStore: window math, z gate, far-jump prune (§4g) ───────
group('TapeIntensity window rates + z gated to 5 baseline samples + far-jump prune', () => {
  const ti = S.TapeIntensityStore();
  const T0 = 1783080000000; // 10s-bucket-aligned (T0/10000 is an integer)

  // Five prints of $100 inside bucket 0 → rolling 10 s holds all five.
  for (let i = 1; i <= 5; i++) ti.push(T0 + i * 1000, 100);
  let st = ti.stats();
  assert.strictEqual(st.tradesPerSec10, 0.5, '5 trades / 10 s');
  assert.strictEqual(st.notionalPerSec10, 50, '$500 / 10 s');
  assert.strictEqual(st.tradesPerSec60, 5 / 60);
  assert.strictEqual(st.z, 0, 'no completed bucket yet → z pinned to 0');
  assert.strictEqual(st.baselineN, 0);

  // First print of bucket 1 completes bucket 0 (count 5, $500) AND prunes the
  // 10 s window to (T0+2000, T0+12000]: the T0+1000/T0+2000 prints leave.
  ti.push(T0 + 12000, 200);
  st = ti.stats();
  assert.strictEqual(st.baselineN, 1);
  assert.strictEqual(st.tradesPerSec10, 0.4, '4 prints left in the 10 s window');
  assert.strictEqual(st.notionalPerSec10, 50, '3×$100 + $200 = $500 / 10 s');
  assert.strictEqual(st.tradesPerSec60, 6 / 60, '60 s window still holds all 6');
  assert.strictEqual(st.z, 0, '1 baseline sample < 5 → z still 0');

  // One print per bucket 2..5. Bucket counts completed so far after the
  // T0+51000 push: [5, 1, 1, 1, 1] → n=5, mean 1.8, sample var (ddof=1)
  // = (3.2² + 4·0.8²)/4 = 12.8/4 = 3.2. Current rolling-10s count = 1 (the
  // T0+41000 print sits exactly on the cutoff and is evicted — half-open
  // window (now−10s, now]). z = (1 − 1.8)/√3.2 = −0.8/(0.8√5) = −1/√5.
  ti.push(T0 + 21000, 100);
  ti.push(T0 + 31000, 100);
  ti.push(T0 + 41000, 100);
  st = ti.stats();
  assert.strictEqual(st.baselineN, 4);
  assert.strictEqual(st.z, 0, '4 baseline samples — still below the gate');
  ti.push(T0 + 51000, 100);
  st = ti.stats();
  assert.strictEqual(st.baselineN, 5);
  assert.strictEqual(st.tradesPerSec10, 0.1, 'only the newest print inside 10 s');
  assert.ok(approx(st.z, -1 / Math.sqrt(5)), 'z = −1/√5 hand-computed, got ' + st.z);

  // Far-ahead jump: both windows must prune down to the lone new print, and
  // NO zero-count buckets are synthesized for the gap (gaps are gaps, §0.7)
  // — exactly one more baseline sample (the completed bucket 5).
  ti.push(T0 + 1000000, 50);
  st = ti.stats();
  assert.strictEqual(st.tradesPerSec10, 0.1);
  assert.strictEqual(st.notionalPerSec10, 5, 'only $50 inside 10 s');
  assert.strictEqual(st.tradesPerSec60, 1 / 60);
  assert.strictEqual(st.notionalPerSec60, 50 / 60);
  assert.strictEqual(st.baselineN, 6, 'gap synthesized no zero samples');

  // Sparkline = completed buckets only, oldest→newest, hand notionals.
  assert.deepStrictEqual(ti.sparkline().map((s) => s.notional), [500, 200, 100, 100, 100, 100]);
  assert.deepStrictEqual(ti.sparkline().map((s) => s.ts), [0, 1, 2, 3, 4, 5].map((i) => T0 + i * 10000));

  // Hygiene: non-finite / non-positive notional never lands.
  ti.push(T0 + 1001000, NaN);
  ti.push(T0 + 1001000, 0);
  assert.strictEqual(ti.stats().tradesPerSec10, 0.1, 'garbage prints dropped');
});

// ─── 50. WallsLedger: enter at exactly M, pulled, filled, near-mid hold (§4g) ─
group('WallsLedger enters at EXACTLY M (M−1 + break never), pulled >1 tick out, filled on cross', () => {
  const wl = S.WallsLedger(); // K=4, M=5 defaults; p95 fed as 1 → wall ⇔ qty ≥ 4
  const T1 = 1783080000000;

  // M−1 sustained samples then a sub-threshold break: must NOT enter, and the
  // break must RESET the streak (not just pause it).
  for (let i = 0; i < 4; i++) wl.update(T1 + i * 1000, 'bid', 99, 5, 1, 8);
  assert.strictEqual(wl.list().length, 0, 'M−1 samples never enter');
  wl.update(T1 + 4000, 'bid', 99, 0.5, 1, 8); // shrank to ordinary — streak broken
  for (let i = 5; i < 9; i++) wl.update(T1 + i * 1000, 'bid', 99, i === 6 ? 6 : 5, 1, 8);
  assert.strictEqual(wl.list().length, 0, '4 post-break samples still not enough');
  wl.update(T1 + 9000, 'bid', 99, 5, 1, 8); // 5th consecutive — enters NOW
  let e = wl.list()[0];
  assert.strictEqual(wl.list().length, 1, 'enters on exactly the Mth sustained sample');
  assert.strictEqual(e.status, 'standing');
  assert.strictEqual(e.firstTs, T1 + 5000, 'firstTs = start of the unbroken streak, not the pre-break one');
  assert.strictEqual(e.lastTs, T1 + 9000);
  assert.strictEqual(e.maxQty, 6, 'maxQty = largest size ever displayed');
  assert.strictEqual(e.side, 'bid');
  assert.strictEqual(e.price, 99);

  // PULLED: vanishes >1 tick from mid — price never got there, cannot have
  // traded; the ledger records the book fact, no intent claim.
  wl.update(T1 + 10000, 'bid', 99, 0, 1, 8);
  assert.strictEqual(wl.list()[0].status, 'pulled');
  assert.strictEqual(wl.list()[0].lastTs, T1 + 10000);

  // FILLED: an ask wall the tape then crosses (print AT the level = it traded).
  for (let i = 0; i < 5; i++) wl.update(T1 + i * 1000, 'ask', 110, 8, 1, 12);
  assert.strictEqual(wl.list()[0].price, 110, 'list is newest-first');
  wl.markTrade(T1 + 5000, 110);
  assert.strictEqual(wl.list()[0].status, 'filled');
  assert.strictEqual(wl.list()[0].lastTs, T1 + 5000);
  assert.strictEqual(wl.list()[1].status, 'pulled', 'a 110 print does not touch the bid-99 entry');

  // NEAR-MID vanish (≤1 tick) is ambiguous — stays standing (we refuse to
  // guess pull vs fill) until a real cross confirms 'filled'.
  for (let i = 0; i < 5; i++) wl.update(T1 + 20000 + i * 1000, 'bid', 105, 9, 1, 3);
  wl.update(T1 + 25000, 'bid', 105, 0, 1, 1); // vanished AT the touch
  assert.strictEqual(wl.list()[0].status, 'standing', 'near-mid vanish never guesses');
  wl.markTrade(T1 + 26000, 104.5);
  assert.strictEqual(wl.list()[0].status, 'filled', 'the cross confirms the fill');

  // Constructor overrides: K=2 with M=1 enters on the first qualifying sample.
  const wl2 = S.WallsLedger({ k: 2, m: 1 });
  wl2.update(T1, 'ask', 200, 2, 1, 5);
  assert.strictEqual(wl2.list().length, 1, 'k/m constructor-overridable');
});

// ─── 51. VpinStore: boundary-straddle split + hand-computed vpin (§4g) ───────
group('VPIN straddling-trade exact split + hand-computed mean + future-only re-arm', () => {
  const vp = S.VpinStore(10); // V = 10 base units
  const T2 = 1783080000000;
  assert.strictEqual(vp.vpin(), null, 'null until the first bucket completes');

  // Hand-computed bucket fills (V=10):
  //   buy 4, sell 4, then buy 5 STRADDLES the boundary: 2 close the bucket
  //   (buy 6 / sell 4 → |Δ|/V = 0.2), 3 carry into the next.
  vp.push(T2 + 1000, 4, true);
  vp.push(T2 + 2000, 4, false);
  assert.strictEqual(vp.vpin(), null, 'bucket still open at 8/10');
  vp.push(T2 + 3000, 5, true);
  assert.ok(approx(vp.vpin(), 0.2), 'bucket 1: |6−4|/10 — straddle split exact');
  //   sell 7 closes bucket 2 exactly (buy 3 / sell 7 → 0.4).
  vp.push(T2 + 4000, 7, false);
  assert.ok(approx(vp.vpin(), (0.2 + 0.4) / 2), 'vpin = mean over completed buckets');
  //   buy 25 spans MULTIPLE buckets: 10 (all-buy → 1.0), 10 (→ 1.0), 5 carries.
  vp.push(T2 + 5000, 25, true);
  assert.ok(approx(vp.vpin(), (0.2 + 0.4 + 1 + 1) / 4), 'multi-bucket print splits across all of them');
  assert.deepStrictEqual(vp.buckets().map((b) => b.imb), [0.2, 0.4, 1, 1].map((v) => v), 'per-bucket series');

  // Re-arm is FUTURE-only: the partially-filled bucket (5 in) completes at
  // its armed V=10; the NEW V applies from the next bucket on. Completed
  // buckets keep the V they were measured at — nothing is restated.
  vp.setBucketVol(4);
  assert.strictEqual(vp.bucketVol, 10, 'partial bucket keeps its armed V');
  vp.push(T2 + 6000, 5, false); // closes it: buy 5 / sell 5 → 0.0
  assert.strictEqual(vp.bucketVol, 4, 'next bucket armed at the new V');
  assert.ok(approx(vp.vpin(), (0.2 + 0.4 + 1 + 1 + 0) / 5), 'earlier buckets unchanged by the re-arm');

  // An EMPTY open bucket is a future bucket — re-arms immediately.
  vp.setBucketVol(8);
  assert.strictEqual(vp.bucketVol, 8);

  // Hygiene: malformed pushes never land.
  vp.push(NaN, 1, true);
  vp.push(T2 + 7000, 0, true);
  vp.push(T2 + 7000, -2, false);
  assert.ok(approx(vp.vpin(), 0.52), 'garbage dropped, state untouched');
});

// ─── 52. OpeningTypeClassifier: four constructed opens + pending (§4g) ───────
group('OpeningType all four Dalton classes on constructed paths + pending before 60 min', () => {
  const T3 = 1783080000000;
  const mm = (m) => T3 + m * 60000;
  const LABEL = 'descriptive session read — not a signal';
  const run = (path) => {
    const oc = S.OpeningTypeClassifier(T3);
    for (const [m, p] of path) oc.feed(mm(m), p);
    return oc.classify();
  };

  // PENDING: nothing fed, and again with <60 min of prints.
  assert.strictEqual(S.OpeningTypeClassifier(T3).classify().type, 'pending');
  const early = run([[0, 100], [30, 105]]);
  assert.strictEqual(early.type, 'pending', 'still inside the opening window');
  assert.strictEqual(early.label, LABEL, 'the descriptive-read label rides pending too');

  // OPEN-DRIVE: one-directional up; worst pullback 0.5 on a 10 range (5%
  // < the 20% convention). The 61-min print only unlocks the event clock.
  const d = run([[0, 100], [5, 102], [15, 104], [20, 103.5], [40, 108], [59, 110], [61, 110]]);
  assert.strictEqual(d.type, 'open-drive');
  assert.strictEqual(d.evidence.dir, 'up');
  assert.strictEqual(d.evidence.retraceDn, 0.5, 'hand path: 104 → 103.5 is the worst retrace');
  assert.strictEqual(d.label, LABEL);

  // OPEN-TEST-DRIVE: probe up 3 by 10 min (≤30 min, ≤ half the 10-point
  // opposite drive), then drive down through the open and stay (1 cross).
  const td = run([[0, 100], [5, 101.5], [10, 103], [20, 99], [35, 95], [55, 90], [61, 90]]);
  assert.strictEqual(td.type, 'open-test-drive');
  assert.strictEqual(td.evidence.firstSide, 'up', 'probed up first…');
  assert.strictEqual(td.evidence.dir, 'down', '…then drove down');
  assert.strictEqual(td.evidence.crossCount, 1);

  // OPEN-REJECTION-REVERSE: drove up 10 of a 14 range, then ONE full
  // reverse through the open with no re-cross. The at-open print (40 min,
  // price 100) is side-neutral — it must not count as a crossing.
  const rr = run([[0, 100], [10, 105], [20, 110], [30, 104], [40, 100], [45, 97], [55, 96], [61, 96]]);
  assert.strictEqual(rr.type, 'open-rejection-reverse');
  assert.strictEqual(rr.evidence.firstSide, 'up');
  assert.strictEqual(rr.evidence.crossCount, 1, 'exactly one cross; the at-open print holds side');

  // OPEN-AUCTION: rotation around the open — five crossings, no dominant leg.
  const a = run([[0, 100], [5, 103], [15, 98], [25, 102], [35, 97], [45, 101], [55, 99], [61, 99]]);
  assert.strictEqual(a.type, 'open-auction');
  assert.strictEqual(a.evidence.crossCount, 5, 'rotation = repeated open crosses');

  // Degenerate flat hour reads rotational, never NaN math.
  assert.strictEqual(run([[0, 100], [30, 100], [61, 100]]).type, 'open-auction');
});

// ─── 53. deriveVenueIds: mapping + honest null degrades (§4g) ────────────────
group('deriveVenueIds BTCUSDT/ETHUSDT full, 1000PEPE no coinbase, non-USDT degrades', () => {
  assert.deepStrictEqual(S.deriveVenueIds('BTCUSDT'),
    { bybit: 'BTCUSDT', binancef: 'BTCUSDT', okx: 'BTC-USDT-SWAP', coinbase: 'BTC-USD' });
  assert.deepStrictEqual(S.deriveVenueIds('ETHUSDT'),
    { bybit: 'ETHUSDT', binancef: 'ETHUSDT', okx: 'ETH-USDT-SWAP', coinbase: 'ETH-USD' });
  // 1000-multiplied perp: the contract exists on the derivatives venues, but
  // there is NO '1000PEPE' spot market — coinbase must degrade to null, not
  // to a guessed id the leg would then 4xx/hang on.
  assert.deepStrictEqual(S.deriveVenueIds('1000PEPEUSDT'),
    { bybit: '1000PEPEUSDT', binancef: '1000PEPEUSDT', okx: '1000PEPE-USDT-SWAP', coinbase: null });
  // Non-USDT symbol: the collector-mirrored derivation only covers USDT
  // perps — binancef/okx/coinbase are UNKNOWN, stated as null (honest
  // degrade, §4g); only bybit keeps the id, because the symbol IS a bybit
  // universe row. A guessed binancef passthrough would subscribe a stream
  // that never delivers and read 'stalled — reconnecting' forever.
  assert.deepStrictEqual(S.deriveVenueIds('BTCUSD'),
    { bybit: 'BTCUSD', binancef: null, okx: null, coinbase: null });
  // Degenerate inputs never fabricate ids.
  assert.deepStrictEqual(S.deriveVenueIds('USDT'),
    { bybit: 'USDT', binancef: null, okx: null, coinbase: null });
  assert.deepStrictEqual(S.deriveVenueIds(''),
    { bybit: null, binancef: null, okx: null, coinbase: null });
});

// ─── 54. BasisSeries: ring wrap + NaN funding never zero-coerced (§4g) ───────
group('BasisSeries ring wrap keeps newest in order + NaN funding + latest()', () => {
  const bs = S.BasisSeries({ max: 5 });
  const T4 = 1783080000000;
  assert.strictEqual(bs.latest(), null, 'empty series has no latest — never a fake row');
  for (let i = 0; i < 7; i++) bs.push(T4 + i * 1000, i, 0.0001 * i);
  const l = bs.list();
  assert.strictEqual(l.length, 5, 'ring capped at max');
  assert.deepStrictEqual(l.map((r) => r.basisBp), [2, 3, 4, 5, 6], 'oldest two evicted, order kept');
  assert.strictEqual(l[0].ts, T4 + 2000);
  assert.strictEqual(bs.latest().basisBp, 6);
  // Funding absent on a sample (Bybit partial deltas) → stored NaN, NEVER a
  // fabricated 0 ("flat funding" is a claim; "unknown" is not).
  bs.push(T4 + 7000, 7);
  assert.ok(Number.isNaN(bs.latest().fundingRate), 'missing funding stays NaN');
  assert.strictEqual(bs.latest().basisBp, 7);
  // Hygiene: a non-finite basis point is dropped whole, not half-recorded.
  bs.push(T4 + 8000, NaN, 0.0001);
  assert.strictEqual(bs.latest().ts, T4 + 7000);
  assert.strictEqual(bs.list().length, 5);
});

// ─── 55. BinanceBookSync spot: buffer/straddle/gap→resync/tombstone (§4h) ────
group('binance spot continuity: buffer drop, straddle applies, gap → counted resync + cleared', () => {
  const b = B.BinanceBookSync({ mode: 'spot' });
  assert.strictEqual(b.mode, 'spot');
  assert.strictEqual(b.state, 'buffering');
  assert.ok(b.needsSnapshot(), 'no snapshot yet → caller must fetch');

  // Diffs BEFORE the snapshot buffer; the book stays honestly empty.
  b.onDiff({ U: 1, u: 3, bids: [['100.0', '1']], asks: [] });                       // u=3 ≤ 5 → dropped at drain
  b.onDiff({ U: 4, u: 6, bids: [['99.5', '2']], asks: [['101.5', '3']] });          // STRADDLES: U=4 ≤ 5+1 ≤ u=6
  b.onDiff({ U: 7, u: 8, bids: [['100.0', '0']], asks: [['101.0', '4']] });         // contiguous after the straddler
  assert.strictEqual(b.bufferedCount, 3);
  assert.deepStrictEqual(b.best(), { bid: null, ask: null }, 'pre-snapshot book is empty, never partial');

  // Snapshot lastUpdateId=5: drain applies events 2+3 under U ≤ lastId+1 ≤ u.
  b.onSnapshot(5, [['100.0', '10'], ['99.0', '5']], [['101.0', '7']]);
  assert.strictEqual(b.state, 'synced');
  assert.ok(!b.needsSnapshot());
  assert.strictEqual(b.resyncCount, 0);
  assert.strictEqual(b.lastUpdateId, 8, 'lastUpdateId = last applied u');
  // Event 3's qty-0 deleted the snapshot's 100.0 level; 101.0 absolutely replaced 7→4.
  assert.deepStrictEqual(b.best(), { bid: [99.5, 2], ask: [101.0, 4] });
  assert.deepStrictEqual(b.topN(2), {
    bids: [[99.5, 2], [99.0, 5]],
    asks: [[101.0, 4], [101.5, 3]],
  });

  // Absolute replace on a live diff: qty 7 REPLACES 2 (never accumulates).
  b.onDiff({ U: 9, u: 10, bids: [['99.5', '7']], asks: [] });
  assert.deepStrictEqual(b.best().bid, [99.5, 7]);
  // A stale live diff (u ≤ lastId — the venue re-delivering) is ignored, not a gap.
  b.onDiff({ U: 2, u: 3, bids: [['1.0', '9']], asks: [] });
  assert.strictEqual(b.state, 'synced');
  // Bid side holds 99.5 + 99.0 (100.0 was tombstoned by the drained diff).
  assert.strictEqual(b.topN(Infinity).bids.length, 2, 'stale diff must not touch the book');

  // GAP: lastId=10, next U=12 > 11 → desync, counted, book CLEARED (§4h:
  // never silently patched — a known-gap book on screen is fabricated data).
  b.onDiff({ U: 12, u: 13, bids: [['99.5', '1']], asks: [] });
  assert.strictEqual(b.state, 'desync');
  assert.strictEqual(b.resyncCount, 1);
  assert.ok(b.needsSnapshot());
  assert.deepStrictEqual(b.best(), { bid: null, ask: null }, 'desync must CLEAR, not keep a holed book');
  assert.strictEqual(b.bufferedCount, 1, 'the violating event re-buffers for the next snapshot');

  // Fresh snapshot resumes: the re-buffered {U:12,u:13} brackets id=11 (12 ≤ 12).
  b.onSnapshot(11, [['98.0', '1']], [['102.0', '1']]);
  assert.strictEqual(b.state, 'synced');
  assert.strictEqual(b.resyncCount, 1, 'resync counter is cumulative, not reset by recovery');
  assert.deepStrictEqual(b.best().bid, [99.5, 1], 'buffered-during-desync diff applied after resync');
});

// ─── 56. BinanceBookSync futures: pu chain ≠ spot rule, provably (§4h) ───────
group('binance futures pu-chaining: broken pu → desync while the SAME sequence passes spot rule', () => {
  // One event sequence, two continuity verdicts — the whole point of `mode`.
  // f3 is crafted so U chains contiguously (111 = 110+1, spot-legal) while
  // pu points at 109 ≠ 110 (futures-illegal): only pu chaining catches it.
  const f1 = { U: 98, u: 102, pu: 97, bids: [['100.0', '6']], asks: [] };  // first after snapshot: bracket 98 ≤ 101 ≤ 102
  const f2 = { U: 103, u: 110, pu: 102, bids: [['99.0', '2']], asks: [] }; // pu === lastAppliedU
  const f3 = { U: 111, u: 120, pu: 109, bids: [['98.0', '9']], asks: [] }; // broken chain, contiguous U

  const f = B.BinanceBookSync({ mode: 'futures' });
  f.onSnapshot(100, [['100.0', '5']], [['101.0', '5']]);
  f.onDiff(f1);
  assert.strictEqual(f.state, 'synced');
  assert.deepStrictEqual(f.best().bid, [100.0, 6], 'first-after-snapshot bracket applied');
  f.onDiff(f2);
  assert.strictEqual(f.state, 'synced');
  assert.strictEqual(f.lastUpdateId, 110);
  f.onDiff(f3);
  assert.strictEqual(f.state, 'desync', 'futures: pu 109 ≠ 110 is a hole even with contiguous U');
  assert.strictEqual(f.resyncCount, 1);
  assert.deepStrictEqual(f.best(), { bid: null, ask: null }, 'cleared, never patched');

  const s = B.BinanceBookSync({ mode: 'spot' });
  s.onSnapshot(100, [['100.0', '5']], [['101.0', '5']]);
  s.onDiff(f1); s.onDiff(f2); s.onDiff(f3);
  assert.strictEqual(s.state, 'synced', 'spot rule U ≤ lastId+1 ≤ u accepts the whole sequence');
  assert.strictEqual(s.resyncCount, 0);
  assert.deepStrictEqual(s.best().bid, [100.0, 6]);
  assert.strictEqual(s.topN(Infinity).bids.length, 3, 'f3 applied under spot — the rules provably differ');
});

// ─── 57. OkxBookSync: CRC32 pinned vectors + mismatch → resync (§4h) ─────────
group('OKX checksum: crc32 vs pinned zlib values, hand interleave, tweak → desync', () => {
  // Pinned INDEPENDENTLY of our table (§4h "pinned test vector"). Commands run
  // 2026-07-23:
  //   python3 -c "import zlib; print(zlib.crc32(b'8476.98:415:8477:7:8475.55:100:8477.34:85'))"
  //     → 3025791351 (unsigned) ≡ -1269175945 as signed int32
  //   python3 -c "import zlib; print(zlib.crc32(b'3366.1:7:3366.8:9:3368:8:3372:8'))"
  //     → 831078360 (unsigned) ≡ 831078360 as signed int32
  assert.strictEqual(B.crc32('8476.98:415:8477:7:8475.55:100:8477.34:85'), -1269175945);
  assert.strictEqual(B.crc32('3366.1:7:3366.8:9:3368:8:3372:8'), 831078360);

  // Constructed book. Interleave BY HAND (bid1:qty:ask1:qty:bid2:qty:ask2:qty):
  //   bids best-first: 100.5×10, 100.0×5; asks best-first: 101.0×3, 101.5×8
  //   → "100.5:10:101.0:3:100.0:5:101.5:8"
  //   python3 -c "import zlib; print(zlib.crc32(b'100.5:10:101.0:3:100.0:5:101.5:8'))" → 490508691
  const ok = B.OkxBookSync();
  assert.ok(ok.needsSnapshot());
  // 4-column wire rows ([px, qty, liqOrders, numOrders]) — extra columns ignored.
  ok.onSnapshot([['100.5', '10', '0', '2'], ['100.0', '5', '0', '1']],
    [['101.0', '3', '0', '1'], ['101.5', '8', '0', '3']], 490508691);
  assert.strictEqual(ok.state, 'synced');
  assert.strictEqual(ok.checksumString(), '100.5:10:101.0:3:100.0:5:101.5:8');
  assert.strictEqual(ok.checksum(), 490508691);

  // Update: replace bid 100.5→12, tombstone ask 101.0 (qty "0"), add ask 102.0×4.
  // Resulting hand interleave: "100.5:12:101.5:8:100.0:5:102.0:4"
  //   python3 -c "import zlib; print(zlib.crc32(b'100.5:12:101.5:8:100.0:5:102.0:4'))" → 1317526142
  ok.onUpdate([['100.5', '12']], [['101.0', '0'], ['102.0', '4']], 1317526142);
  assert.strictEqual(ok.state, 'synced');
  assert.strictEqual(ok.resyncCount, 0);
  assert.deepStrictEqual(ok.best(), { bid: [100.5, 12], ask: [101.5, 8] });
  assert.deepStrictEqual(ok.topN(1), { bids: [[100.5, 12]], asks: [[101.5, 8]] });

  // DELIBERATE MISMATCH: the venue claims the CRC of a book where bid 100.5
  // holds qty 13 (string "100.5:13:101.5:8:100.0:5:102.0:4" → unsigned
  // 3943451248 ≡ signed -351516048, same python command) — ours holds 12.
  ok.onUpdate([], [], -351516048);
  assert.strictEqual(ok.state, 'desync');
  assert.strictEqual(ok.resyncCount, 1);
  assert.ok(ok.needsSnapshot());
  assert.deepStrictEqual(ok.best(), { bid: null, ask: null }, 'mismatch clears — never silently patched');
  // Post-desync updates are ignored: deltas onto a cleared book would fabricate.
  ok.onUpdate([['1.0', '1']], [], 0);
  assert.strictEqual(ok.topN(Infinity).bids.length, 0);
  // Fresh snapshot re-arms; counter stays cumulative.
  ok.onSnapshot([['100.5', '10'], ['100.0', '5']], [['101.0', '3'], ['101.5', '8']], 490508691);
  assert.strictEqual(ok.state, 'synced');
  assert.strictEqual(ok.resyncCount, 1);

  // Asymmetric sides (< 25 levels: use what exists — per spec): 1 bid, 3 asks
  // interleave to the SECOND pinned vector's exact string.
  const ok2 = B.OkxBookSync();
  ok2.onSnapshot([['3366.1', '7']], [['3366.8', '9'], ['3368', '8'], ['3372', '8']], 831078360);
  assert.strictEqual(ok2.checksumString(), '3366.1:7:3366.8:9:3368:8:3372:8');
  assert.strictEqual(ok2.state, 'synced');
  // A snapshot failing its OWN checksum is corrupt → counted desync.
  const ok3 = B.OkxBookSync();
  ok3.onSnapshot([['3366.1', '7']], [['3366.8', '9']], 12345);
  assert.strictEqual(ok3.state, 'desync');
  assert.strictEqual(ok3.resyncCount, 1);
});

// ─── 58. CoinbaseBookSync: apply/remove-zero/replace + ts rail (§4h) ─────────
group('coinbase l2: snapshot+apply+remove-zero+replace, lastUpdateTs advances', () => {
  const T5 = 1783100000000;
  const cb = B.CoinbaseBookSync();
  // No sequence number exists on level2_batch — lastUpdateTs is the ONLY rail
  // (staleness-gate → reconnect → fresh snapshot), so it must track exactly.
  assert.ok(Number.isNaN(cb.lastUpdateTs), 'untouched book has no ts — never a fake 0');
  cb.onSnapshot([['100.0', '2'], ['99.5', '1']], [['100.5', '3']], T5);
  assert.strictEqual(cb.lastUpdateTs, T5);
  assert.deepStrictEqual(cb.best(), { bid: [100.0, 2], ask: [100.5, 3] });

  // One batch: remove (qty "0"), absolute replace (3→5, never 3+5), add.
  cb.onL2Update([['buy', '100.0', '0'], ['sell', '100.5', '5'], ['buy', '99.0', '4']], T5 + 1000);
  assert.strictEqual(cb.lastUpdateTs, T5 + 1000, 'ts advances per frame');
  assert.deepStrictEqual(cb.best(), { bid: [99.5, 1], ask: [100.5, 5] });
  assert.deepStrictEqual(cb.topN(2), { bids: [[99.5, 1], [99.0, 4]], asks: [[100.5, 5]] });

  // Corrupt side tag: row skipped (never guessed onto a side), ts still
  // advances — the gate measures channel liveness, not row quality.
  cb.onL2Update([['hold', '98.0', '1']], T5 + 2000);
  assert.strictEqual(cb.lastUpdateTs, T5 + 2000);
  assert.strictEqual(cb.topN(Infinity).bids.length, 2);

  // Reconnect rail: a new snapshot replaces the book WHOLESALE.
  cb.onSnapshot([['50.0', '1']], [['51.0', '1']], T5 + 3000);
  assert.deepStrictEqual(cb.best(), { bid: [50.0, 1], ask: [51.0, 1] });
  assert.strictEqual(cb.topN(Infinity).bids.length, 1, 'old levels gone with the old book');
});

// ─── 59. SpotPerpCvdStore: hand-computed accumulation + sampling (§4h) ───────
group('SpotPerpCvd accumulation: hand math, event-time buckets, out-of-order, ring', () => {
  const T6 = 1783000000000; // multiple of 10000 — bucket edges land exactly
  const st = B.SpotPerpCvdStore({});
  assert.strictEqual(st.latest(), null, 'no pushes → null, never a fabricated zero row');

  // Hand path: perp +100 −30 = 70; spot +40 within the first 10 s bucket.
  st.push(T6, 'bybit_linear', true, 100);
  st.push(T6 + 1000, 'binance_spot', false, 40);
  st.push(T6 + 2000, 'okx_swap', true, -30);
  assert.deepStrictEqual(st.latest(), { ts: T6 + 2000, cvdPerp: 70, cvdSpot: 40 });
  assert.deepStrictEqual(st.list(), [], 'bucket still open — no sample yet');

  // Crossing the bucket edge closes it at its PRE-push cums.
  st.push(T6 + 10000, 'coinbase', false, -15);
  assert.deepStrictEqual(st.list(), [{ ts: T6, cvdPerp: 70, cvdSpot: 40 }]);
  // Out-of-order push (cross-venue interleave) still ACCUMULATES — dropping
  // real flow would bias the perp/spot comparison — but moves no sample.
  st.push(T6 + 9000, 'bybit_spot', false, 5);
  assert.strictEqual(st.list().length, 1);
  assert.deepStrictEqual(st.latest(), { ts: T6 + 10000, cvdPerp: 70, cvdSpot: 30 }, 'latest ts stays the max seen');

  // Jump over an empty bucket: exactly ONE sample closes (gaps are gaps —
  // no zero-sample synthesis), keyed by ITS bucket start.
  st.push(T6 + 25000, 'bybit_linear', true, 1);
  assert.deepStrictEqual(st.list(), [
    { ts: T6, cvdPerp: 70, cvdSpot: 40 },
    { ts: T6 + 10000, cvdPerp: 70, cvdSpot: 30 },
  ]);
  assert.deepStrictEqual(st.latest(), { ts: T6 + 25000, cvdPerp: 71, cvdSpot: 30 });
  // Per-leg cumulative ledger (panel legend): bybit_linear 100+1, the rest as pushed.
  assert.deepStrictEqual(st.byLeg, {
    bybit_linear: 101, binance_spot: 40, okx_swap: -30, coinbase: -15, bybit_spot: 5,
  });

  // Hygiene: NaN ts / NaN or zero notional / missing legKey never touch state.
  st.push(NaN, 'bybit_linear', true, 5);
  st.push(T6 + 26000, 'bybit_linear', true, NaN);
  st.push(T6 + 26000, 'bybit_linear', true, 0);
  st.push(T6 + 26000, '', true, 5);
  assert.deepStrictEqual(st.latest(), { ts: T6 + 25000, cvdPerp: 71, cvdSpot: 30 });

  // Ring wrap: max 2 keeps only the newest two completed samples, in order.
  const st2 = B.SpotPerpCvdStore({ max: 2 });
  for (let i = 0; i < 4; i++) st2.push(T6 + i * 10000, 'okx_spot', false, 1);
  assert.deepStrictEqual(st2.list().map((r) => r.ts), [T6 + 10000, T6 + 20000]);
  assert.deepStrictEqual(st2.list().map((r) => r.cvdSpot), [2, 3]);
});

// ─── 60. deriveLegIds: the 7-leg matrix + honest degrades (§4h) ──────────────
group('deriveLegIds BTCUSDT full matrix, 1000PEPE coinbase null, non-USDT bybit-only', () => {
  assert.deepStrictEqual(S.deriveLegIds('BTCUSDT'), {
    bybit_linear: 'BTCUSDT', bybit_spot: 'BTCUSDT',
    binancef: 'BTCUSDT', binance_spot: 'BTCUSDT',
    okx_swap: 'BTC-USDT-SWAP', okx_spot: 'BTC-USDT', coinbase: 'BTC-USD',
  });
  // Multiplied perp: coinbase is a NAMING impossibility (T-1 hard rule — no
  // USD spot for a derivatives multiplier artifact); the USDT-spot ids stay
  // derived, and the per-leg listability probe decides whether they exist.
  assert.deepStrictEqual(S.deriveLegIds('1000PEPEUSDT'), {
    bybit_linear: '1000PEPEUSDT', bybit_spot: '1000PEPEUSDT',
    binancef: '1000PEPEUSDT', binance_spot: '1000PEPEUSDT',
    okx_swap: '1000PEPE-USDT-SWAP', okx_spot: '1000PEPE-USDT', coinbase: null,
  });
  // Non-USDT: only the picker's own id survives (§4g rule extended to every
  // derived leg — a guessed id would subscribe a stream that never delivers).
  assert.deepStrictEqual(S.deriveLegIds('BTCUSD'), {
    bybit_linear: 'BTCUSD', bybit_spot: null, binancef: null,
    binance_spot: null, okx_swap: null, okx_spot: null, coinbase: null,
  });
  assert.deepStrictEqual(S.deriveLegIds(''), {
    bybit_linear: null, bybit_spot: null, binancef: null,
    binance_spot: null, okx_swap: null, okx_spot: null, coinbase: null,
  });
  // Additivity rail: deriveVenueIds keeps its frozen 4-key T-1 shape — T-2
  // must not leak matrix keys into the T-1 consumers' contract.
  assert.deepStrictEqual(Object.keys(S.deriveVenueIds('BTCUSDT')).sort(),
    ['binancef', 'bybit', 'coinbase', 'okx']);
});

// ─── 61. LegRegistry: 7-leg matrix store (§4h) ───────────────────────────────
group('LegRegistry: 7 defs, enable/disable change-once, copy snapshot, strict seed', () => {
  const reg = S.LegRegistry();
  assert.deepStrictEqual(reg.keys(),
    ['bybit_linear', 'bybit_spot', 'binancef', 'binance_spot', 'okx_swap', 'okx_spot', 'coinbase']);
  // One naming universe: registry keys ≡ deriveLegIds keys (§4h).
  assert.deepStrictEqual(reg.keys().slice().sort(), Object.keys(S.deriveLegIds('')).sort());
  assert.deepStrictEqual(reg.get('okx_swap'),
    { key: 'okx_swap', venue: 'okx', market: 'perp', enabled: true, status: null });
  assert.strictEqual(reg.isPerp('bybit_linear'), true);
  assert.strictEqual(reg.isPerp('binance_spot'), false);
  assert.ok(reg.snapshot().every((l) => l.enabled === true), 'default: ALL enabled (§4h)');

  // Flip reports a change exactly once; reads and the persistence map agree.
  assert.strictEqual(reg.setEnabled('coinbase', false), true);
  assert.strictEqual(reg.setEnabled('coinbase', false), false, 'no-op flip reports no change');
  assert.strictEqual(reg.isEnabled('coinbase'), false);
  assert.strictEqual(reg.enabledMap().coinbase, false);
  assert.strictEqual(reg.snapshot().find((l) => l.key === 'coinbase').enabled, false);

  // Snapshot rows are COPIES — mutating the UI's read must not corrupt state.
  const snap = reg.snapshot();
  snap[0].enabled = false; snap[0].market = 'corrupted';
  assert.strictEqual(reg.isEnabled('bybit_linear'), true);
  assert.strictEqual(reg.get('bybit_linear').market, 'perp');

  // Unknown keys: disabled reads (a leg the registry cannot name must never
  // start), dropped writes, null get. Non-boolean flips dropped, not coerced.
  assert.strictEqual(reg.isEnabled('nope'), false);
  assert.strictEqual(reg.setEnabled('nope', true), false);
  assert.strictEqual(reg.get('nope'), null);
  assert.strictEqual(reg.setEnabled('okx_spot', 1), false);
  assert.strictEqual(reg.isEnabled('okx_spot'), true);

  // Persisted seed: STRICT booleans on KNOWN keys only (settings rail).
  const reg2 = S.LegRegistry({ enabled: { binance_spot: false, okx_spot: 'no', bogus: false } });
  assert.strictEqual(reg2.isEnabled('binance_spot'), false);
  assert.strictEqual(reg2.isEnabled('okx_spot'), true, 'non-boolean seed ignored');
  assert.strictEqual(reg2.isEnabled('bybit_linear'), true);

  // Status is caller-owned bookkeeping, surfaced by the snapshot.
  reg2.setStatus('binancef', { kind: 'stale', msg: 'disabled (settings)' });
  assert.deepStrictEqual(reg2.get('binancef').status, { kind: 'stale', msg: 'disabled (settings)' });
});

// ─── 62. Bybit SPOT adapter: real publicTrade + orderbook.200 (§4h) ──────────
group('bybit spot adapter: descriptor, taker-side trades, book tombstones, liveness split', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBybitSpotAdapter('BTCUSDT', sink);
  assert.strictEqual(ad.url, 'wss://stream.bybit.com/v5/public/spot');
  const { sent, ws } = captureWs();
  ad.subscribe(ws); ad.ping(ws);
  assert.deepStrictEqual(sent[0], { op: 'subscribe', args: ['publicTrade.BTCUSDT', 'orderbook.200.BTCUSDT'] });
  assert.deepStrictEqual(sent[1], { op: 'ping' });

  // Trades: §0.6 taker side AS-IS, ex 'bybit_spot', wire values exact —
  // and trades NEVER markAlive (quiet spot tape = quiet market).
  const api = countingApi();
  for (const f of FX.t2_bybitspot_publicTrade) ad.onMessage(f, api);
  const nTrades = FX.t2_bybitspot_publicTrade.reduce((n, f) => n + f.data.length, 0);
  assert.strictEqual(evts.length, nTrades);
  assert.strictEqual(api.alive, 0, 'trades must never markAlive');
  let i = 0;
  for (const f of FX.t2_bybitspot_publicTrade) {
    for (const raw of f.data) {
      const ev = evts[i++];
      assert.strictEqual(ev.kind, 'trade');
      assert.strictEqual(ev.ex, 'bybit_spot');
      assert.strictEqual(ev.aggressorBuy, raw.S === 'Buy', 'S is already the taker side — no inversion');
      assert.strictEqual(ev.ts, raw.T);
      assert.strictEqual(ev.price, Number(raw.p));
      assert.strictEqual(ev.qty, Number(raw.v));
      assert.strictEqual(ev.id, String(raw.i));
    }
  }

  // Book: snapshot + deltas through a real BookStore. Every book frame marks
  // alive — this socket has no tickers channel, the book IS its liveness.
  evts.length = 0;
  ad.onMessage(FX.t2_bybitspot_orderbook200_snapshot[0], api);
  for (const f of FX.t2_bybitspot_orderbook200_delta) ad.onMessage(f, api);
  assert.strictEqual(api.alive, 1 + FX.t2_bybitspot_orderbook200_delta.length);
  assert.strictEqual(evts[0].isSnapshot, true);
  assert.strictEqual(evts[0].ex, 'bybit_spot');
  assert.strictEqual(evts[0].bids[0][0], 65072.3, 'fixture best bid');
  assert.strictEqual(evts[0].asks[0][0], 65072.4, 'fixture best ask');
  // A REAL qty-"0" tombstone (delta 1 carries ["64962.8","0"]) survives the
  // adapter for the store to delete — the linear leg's rail, same code path.
  assert.ok(evts[1].bids.some((l) => l[0] === 64962.8 && l[1] === 0), 'tombstone kept for the store');
  const book = S.BookStore();
  for (const ev of evts) book.applyDepth(ev);
  const b = book.best();
  assert.ok(b.bid && b.ask && b.bid[0] < b.ask[0], 'book sane after real deltas');
});

// ─── 63. Binance SPOT adapter: aggTrade inversion + engine sync (§4h) ────────
group('binance spot adapter: m-inversion, diffs feed the engine, real REST snapshot syncs', () => {
  const { evts, sink } = collectSink();
  const eng = B.BinanceBookSync({ mode: 'spot' });
  const ad = A.makeBinanceSpotAdapter('BTCUSDT', sink, { book: eng });
  assert.strictEqual(ad.url, 'wss://stream.binance.com:9443/stream?streams=btcusdt@aggTrade/btcusdt@depth@100ms');

  // §0.6: `m` (isBuyerMaker) true → SELL aggressor — aggressorBuy inverts m.
  const api = countingApi();
  for (const f of FX.t2_binancespot_aggtrade) ad.onMessage(f, api);
  assert.strictEqual(api.alive, 0, 'trades never markAlive');
  assert.strictEqual(evts.length, FX.t2_binancespot_aggtrade.length);
  FX.t2_binancespot_aggtrade.forEach((f, j) => {
    const raw = f.data, ev = evts[j];
    assert.strictEqual(ev.kind, 'trade');
    assert.strictEqual(ev.ex, 'binance_spot');
    assert.strictEqual(ev.aggressorBuy, raw.m === false, 'aggressorBuy must INVERT isBuyerMaker');
    assert.strictEqual(ev.ts, raw.T, 'ts = trade time T, not gateway E');
    assert.strictEqual(ev.id, String(raw.a));
    assert.strictEqual(ev.price, Number(raw.p));
    assert.strictEqual(ev.qty, Number(raw.q));
  });
  // Fixture precondition: the captured prints are all m:true, so a copied
  // (non-inverted) flag would fail every row above as `true`.
  assert.ok(FX.t2_binancespot_aggtrade.every((f) => f.data.m === true));

  // Diffs go to the ENGINE, never the sink; every diff frame marks alive.
  for (const f of FX.t2_binancespot_depthdiff) ad.onMessage(f, api);
  assert.strictEqual(evts.length, FX.t2_binancespot_aggtrade.length, 'diffs must not reach the sink');
  assert.strictEqual(api.alive, FX.t2_binancespot_depthdiff.length);
  assert.strictEqual(eng.state, 'buffering');
  assert.strictEqual(eng.bufferedCount, 4);

  // The REAL same-moment REST snapshot drains them under the spot rule. The
  // capture pinned the interesting boundary: diff 1's u == lastUpdateId
  // EXACTLY (dropped as covered) and diff 2's U == lastUpdateId+1
  // (contiguous) — assert the precondition so a re-capture that stops
  // exercising the boundary cannot silently pass.
  const snap = FX.t2_binancespot_rest_depth;
  assert.strictEqual(Number(FX.t2_binancespot_depthdiff[0].data.u), snap.lastUpdateId);
  assert.strictEqual(Number(FX.t2_binancespot_depthdiff[1].data.U), snap.lastUpdateId + 1);
  eng.onSnapshot(snap.lastUpdateId, snap.bids, snap.asks);
  assert.strictEqual(eng.state, 'synced', 'real diffs + real snapshot must sync');
  assert.strictEqual(eng.resyncCount, 0);
  assert.strictEqual(eng.lastUpdateId, Number(FX.t2_binancespot_depthdiff[3].data.u));
  const b = eng.best();
  assert.ok(b.bid && b.ask && Number.isFinite(b.bid[0]) && b.bid[0] < b.ask[0], 'synced book sane');
});

// ─── 64. Binance FUT diff adapter: real pu chain + fapi snapshot (§4h) ───────
group('binance fut diff adapter: real pu chain syncs the futures engine, sink-free', () => {
  const eng = B.BinanceBookSync({ mode: 'futures' });
  const ad = A.makeBinanceFutDepthDiff('BTCUSDT', { book: eng });
  assert.strictEqual(ad.url, 'wss://fstream.binance.com/stream?streams=btcusdt@depth@100ms');
  const api = countingApi();
  for (const f of FX.t2_binancef_depthdiff) ad.onMessage(f, api);
  assert.strictEqual(api.alive, FX.t2_binancef_depthdiff.length, 'every diff frame marks alive');
  assert.strictEqual(eng.bufferedCount, 4);

  // Fixture preconditions that make this a REAL continuity proof: captured
  // consecutive diffs chain pu === previous u exactly, and the same-moment
  // fapi snapshot id lands INSIDE diff 2's [U, u] (the bracket the engine's
  // first-after-snapshot rule admits).
  const d = FX.t2_binancef_depthdiff.map((f) => f.data);
  for (let j = 1; j < d.length; j++) {
    assert.strictEqual(Number(d[j].pu), Number(d[j - 1].u), 'wire pu chain intact as captured');
  }
  const snap = FX.t2_binancef_rest_depth;
  assert.ok(Number(d[0].u) <= snap.lastUpdateId, 'diff 1 entirely covered by the snapshot');
  assert.ok(Number(d[1].U) <= snap.lastUpdateId + 1 && snap.lastUpdateId <= Number(d[1].u),
    'diff 2 brackets the snapshot id');
  eng.onSnapshot(snap.lastUpdateId, snap.bids, snap.asks);
  assert.strictEqual(eng.state, 'synced', 'real fut diffs + real snapshot must sync via pu chaining');
  assert.strictEqual(eng.resyncCount, 0);
  assert.strictEqual(eng.lastUpdateId, Number(d[3].u));
  const b = eng.best();
  assert.ok(b.bid && b.ask && b.bid[0] < b.ask[0]);
});

// ─── 65. OKX books adapter: measured checksum reality + seq chain (§4h) ──────
group('okx books adapter: real frames carry checksum:0 (measured) — seq chain rail, ctVal, gap halts', () => {
  // THE §4h DEVIATION, pinned as a fixture precondition: every captured
  // books frame (swap AND spot, 2026-07-23, 300+ frames probed across three
  // instruments) carries checksum:0 — the venue no longer populates the
  // CRC32 on the keyless books channel, so OkxBookSync's verify (group 57
  // pins the convention against zlib) has NO wire input to check. The
  // identifiable continuity rail on the real wire is seqId/prevSeqId
  // chaining, which this adapter enforces below ON those same real frames.
  const allBooks = [FX.t2_okxswap_books_snapshot[0]].concat(FX.t2_okxswap_books_update,
    [FX.t2_okxspot_books_snapshot[0]], FX.t2_okxspot_books_update);
  for (const f of allBooks) {
    assert.strictEqual(f.data[0].checksum, 0, 'fixture precondition: checksum is the 0 placeholder');
  }

  // SWAP leg: ctVal 0.01 CONTRACTS→BTC on every level (§4b unit rail).
  const { evts, sink } = collectSink();
  const api = countingApi();
  const ad = A.makeOkxBooksAdapter('BTC-USDT-SWAP', sink, { ex: 'okx', ctVal: 0.01 });
  assert.strictEqual(ad.bookGapped(), false);
  ad.onMessage(FX.t2_okxswap_books_snapshot[0], api);
  for (const f of FX.t2_okxswap_books_update) ad.onMessage(f, api);
  assert.strictEqual(api.alive, 4, 'every books data frame marks alive (§4b contract)');
  assert.strictEqual(evts.length, 4, 'intact chain: snapshot + all three updates emit');
  assert.strictEqual(evts[0].isSnapshot, true);
  assert.ok(evts.slice(1).every((e) => e.isSnapshot === false));
  assert.strictEqual(evts[0].bids[0][0], 65029.9);
  assert.ok(approx(evts[0].bids[0][1], 3.1836, 1e-12), 'sz 318.36 contracts → 3.1836 BTC exactly');
  // A REAL sz-"0" tombstone survives scaling (0 × ctVal = 0) for the store.
  assert.ok(evts[1].bids.some((l) => l[0] === 65020.1 && l[1] === 0), 'real tombstone kept');
  const book = S.BookStore();
  for (const ev of evts) book.applyDepth(ev);
  const b = book.best();
  assert.ok(b.bid && b.ask && b.bid[0] < b.ask[0], 'book sane after the chained updates');
  assert.strictEqual(ad.bookResyncs, 0);

  // TAMPERED prevSeqId → gap: the frame is DROPPED (§0.7 — no deltas past a
  // known hole), bookGapped() flags the caller's resubscribe poll, later
  // GOOD frames stay dropped (the chain is broken, not resumed), and only a
  // fresh snapshot re-arms.
  const tampered = JSON.parse(JSON.stringify(FX.t2_okxswap_books_update[0]));
  tampered.data[0].prevSeqId = Number(tampered.data[0].prevSeqId) + 1;
  const statuses = [];
  const api2 = { markAlive() {}, onStatus(k, m) { statuses.push([k, m]); } };
  const { evts: ev2, sink: sink2 } = collectSink();
  const ad2 = A.makeOkxBooksAdapter('BTC-USDT-SWAP', sink2, { ex: 'okx', ctVal: 0.01 });
  ad2.onMessage(FX.t2_okxswap_books_snapshot[0], api2);
  ad2.onMessage(tampered, api2);
  assert.strictEqual(ad2.bookGapped(), true);
  assert.strictEqual(ad2.bookResyncs, 1, 'counted honest resync');
  assert.strictEqual(ev2.length, 1, 'the gapped update must NOT emit');
  ad2.onMessage(FX.t2_okxswap_books_update[1], api2);   // chains off the update we dropped
  assert.strictEqual(ev2.length, 1, 'post-gap updates stay dropped until a snapshot');
  assert.ok(statuses.some(([k, m]) => k === 'stale' && /seq gap/.test(m)), 'gap chips its reason');
  ad2.onMessage(FX.t2_okxswap_books_snapshot[0], api2);
  assert.strictEqual(ad2.bookGapped(), false, 'fresh snapshot re-arms the chain');
  assert.strictEqual(ev2.length, 2);

  // SPOT leg: sz ALREADY coin units → UNSCALED (ctVal 1), ex tag, and the
  // trades channel taker-side rail on real spot prints.
  const { evts: ev3, sink: sink3 } = collectSink();
  const ad3 = A.makeOkxBooksAdapter('BTC-USDT', sink3, { ex: 'okx_spot', ctVal: 1 });
  ad3.onMessage(FX.t2_okxspot_books_snapshot[0], nullApi);
  for (const f of FX.t2_okxspot_books_update) ad3.onMessage(f, nullApi);
  for (const f of FX.t2_okxspot_trades) ad3.onMessage(f, nullApi);
  const depths = ev3.filter((e) => e.kind === 'depth');
  const trades = ev3.filter((e) => e.kind === 'trade');
  assert.strictEqual(depths.length, 4);
  assert.ok(depths.every((e) => e.ex === 'okx_spot'));
  assert.strictEqual(depths[0].bids[0][0], 65052.3);
  assert.strictEqual(depths[0].bids[0][1], 0.56597003, 'spot qty UNSCALED — already coin units');
  assert.strictEqual(trades.length, FX.t2_okxspot_trades.length);
  trades.forEach((ev, j) => {
    const raw = FX.t2_okxspot_trades[j].data[0];
    assert.strictEqual(ev.ex, 'okx_spot');
    assert.strictEqual(ev.aggressorBuy, raw.side === 'buy', 'taker side as-is (§0.6 Bybit family)');
    assert.strictEqual(ev.qty, Number(raw.sz));
    assert.strictEqual(ev.ts, Number(raw.ts));
    assert.strictEqual(ev.id, String(raw.tradeId));
  });

  // Descriptor: ONE socket per leg (books + trades), plain-text keepalive.
  const { sent, ws } = captureWs();
  ad3.subscribe(ws); ad3.ping(ws);
  assert.deepStrictEqual(sent[0], {
    op: 'subscribe',
    args: [{ channel: 'books', instId: 'BTC-USDT' }, { channel: 'trades', instId: 'BTC-USDT' }],
  });
  assert.strictEqual(sent[1], 'ping');
});

// ─── 66. Coinbase L2 adapter: exchange feed through CoinbaseBookSync (§4h) ───
group('coinbase l2 adapter: real snapshot+l2update via engine, maker inversion, dedupe', () => {
  const { evts, sink } = collectSink();
  const eng = B.CoinbaseBookSync();
  const ad = A.makeCoinbaseL2Adapter('BTC-USD', sink, { book: eng });
  assert.strictEqual(ad.url, 'wss://ws-feed.exchange.coinbase.com');
  const { sent, ws } = captureWs();
  ad.subscribe(ws);
  assert.deepStrictEqual(sent[0], {
    type: 'subscribe', product_ids: ['BTC-USD'],
    channels: ['level2_batch', 'matches', 'heartbeat'],
  });
  // NO ping on purpose: a periodic re-subscribe "nudge" (the advanced-trade
  // idiom) makes this venue re-send the whole >1MB snapshot.
  assert.strictEqual(ad.ping, undefined, 'no keepalive nudge — heartbeat channel is the keepalive');

  const api = countingApi();
  const snap = FX.t2_coinbase_l2_snapshot[0];
  ad.onMessage(snap, api);
  assert.strictEqual(eng.lastUpdateTs, Date.parse(snap.time), 'snapshot time seeds the staleness rail');
  let b = eng.best();
  assert.deepStrictEqual([b.bid[0], b.ask[0]], [65009.22, 65009.23], 'fixture best after snapshot');
  for (const u of FX.t2_coinbase_l2_update) ad.onMessage(u, api);
  assert.strictEqual(eng.lastUpdateTs, Date.parse(FX.t2_coinbase_l2_update[2].time), 'ts advances per l2 frame');
  // REAL change rows, both rails: 65010.85 lands at its ABSOLUTE qty (last
  // write wins, never accumulated), and 64915.46 — set by update 1, zeroed
  // by a later update — is GONE (the qty-0 removal on real frames).
  const bidLadder = eng.topN(Infinity).bids;
  assert.ok(bidLadder.some((l) => l[0] === 65010.85 && l[1] === 0.74329118),
    'l2update change applied absolutely');
  assert.ok(!bidLadder.some((l) => l[0] === 64915.46), 'qty-0 change removed the level');
  b = eng.best();
  assert.ok(b.bid && b.ask && b.bid[0] < b.ask[0], 'book sane after real updates');
  assert.strictEqual(api.alive, FX.t2_coinbase_l2_update.length, 'l2 batches mark alive; the one-shot snapshot does not');
  ad.onMessage(FX.t2_coinbase_heartbeat[0], api);
  assert.strictEqual(api.alive, FX.t2_coinbase_l2_update.length + 1, 'heartbeat marks alive');

  // Matches: §0.6 gotcha — `side` is the MAKER (lowercase on this feed),
  // aggressor is the INVERSE. last_match (the subscribe-time seed print)
  // emits ONCE; monotonic trade_id dedupe swallows re-delivery.
  for (const m of FX.t2_coinbase_matches) ad.onMessage(m, api);
  assert.strictEqual(api.alive, FX.t2_coinbase_l2_update.length + 1, 'matches never markAlive');
  assert.strictEqual(evts.length, FX.t2_coinbase_matches.length);
  FX.t2_coinbase_matches.forEach((raw, j) => {
    const ev = evts[j];
    assert.strictEqual(ev.kind, 'trade');
    assert.strictEqual(ev.ex, 'coinbase');
    assert.strictEqual(ev.aggressorBuy, raw.side === 'sell', 'maker side must invert to the aggressor');
    assert.strictEqual(ev.ts, Date.parse(raw.time));
    assert.strictEqual(ev.id, String(raw.trade_id));
    assert.strictEqual(ev.price, Number(raw.price));
    assert.strictEqual(ev.qty, Number(raw.size));
  });
  assert.strictEqual(FX.t2_coinbase_matches[0].type, 'last_match', 'fixture precondition: the seed print is present');
  for (const m of FX.t2_coinbase_matches) ad.onMessage(m, api);
  assert.strictEqual(evts.length, FX.t2_coinbase_matches.length, 'reconnect re-delivery emits ZERO new trades');
});

// ─── 67. TapeAggregator: merge run + four flush boundaries + no cross-ex (§4i) ─
group('TapeAggregator: same-ex/side/price run merges (VWAP+count), 4 flushes, no cross-ex', () => {
  const T = 1783080000000;

  // A same-ex/side/price run collapses to ONE row; window is anchored on the
  // FIRST print, edge INCLUSIVE — the +100 print at the exact 100 ms edge still
  // merges. VWAP = Σpx·qty/Σqty = 100 exactly on a same-price run; count = 3.
  const ta = S.TapeAggregator({ aggWindowMs: 100, size: 8 });
  ta.push({ ts: T, ex: 'bybit', isBuy: true, price: 100, qty: 1, notional: 100 });
  ta.push({ ts: T + 50, ex: 'bybit', isBuy: true, price: 100, qty: 3, notional: 300 });
  ta.push({ ts: T + 100, ex: 'bybit', isBuy: true, price: 100, qty: 2, notional: 200 });
  let l = ta.list();
  assert.strictEqual(l.length, 1, 'the run is one forming row, not three');
  assert.strictEqual(l[0].count, 3, 'count = prints merged');
  assert.ok(approx(l[0].qty, 6) && approx(l[0].notional, 600), 'qty/notional summed');
  assert.ok(approx(l[0].price, 100), 'VWAP of a same-price run is the price');
  assert.strictEqual(l[0].ts, T + 100, 'row ts = most recent print in the run');

  // WINDOW EXPIRY: a same-ex/side/price print 101 ms past the run start is past
  // the anchored window → flushes the run and opens a fresh row (count 1). The
  // forming row is newest-first; the flushed run sits behind it.
  ta.push({ ts: T + 201, ex: 'bybit', isBuy: true, price: 100, qty: 5, notional: 500 });
  l = ta.list();
  assert.strictEqual(l.length, 2, 'window expiry flushed the run');
  assert.strictEqual(l[0].count, 1, 'new forming row after expiry');
  assert.strictEqual(l[1].count, 3, 'the flushed run kept its merged count');

  // PRICE CHANGE flushes.
  const pc = S.TapeAggregator();
  pc.push({ ts: T, ex: 'okx', isBuy: false, price: 100, qty: 1, notional: 100 });
  pc.push({ ts: T + 1, ex: 'okx', isBuy: false, price: 101, qty: 1, notional: 101 });
  assert.strictEqual(pc.list().length, 2, 'a price change never merges');

  // SIDE FLIP flushes.
  const sf = S.TapeAggregator();
  sf.push({ ts: T, ex: 'okx', isBuy: false, price: 100, qty: 1, notional: 100 });
  sf.push({ ts: T + 1, ex: 'okx', isBuy: true, price: 100, qty: 1, notional: 100 });
  assert.strictEqual(sf.list().length, 2, 'a side flip never merges');

  // EX CHANGE flushes — and the §4i hard rule: two venues printing the SAME
  // price the SAME instant are TWO prints. Merging them would fake one block.
  const xc = S.TapeAggregator();
  xc.push({ ts: T, ex: 'bybit', isBuy: true, price: 100, qty: 1, notional: 100 });
  xc.push({ ts: T, ex: 'binance', isBuy: true, price: 100, qty: 1, notional: 100 });
  const xl = xc.list();
  assert.strictEqual(xl.length, 2, 'cross-ex same-instant same-price NEVER merges');
  assert.deepStrictEqual(xl.map((r) => r.ex), ['binance', 'bybit'], 'newest-first, each venue its own row');

  // Ring bound: flushed rows past `size` evict oldest (open row is extra).
  const rb = S.TapeAggregator({ aggWindowMs: 0, size: 2 });
  for (let i = 0; i < 5; i++) rb.push({ ts: T + i * 10, ex: 'x', isBuy: true, price: 100 + i, qty: 1, notional: 100 });
  assert.ok(rb.list().length <= 3, 'ring bounds flushed rows (+1 forming)');

  // Hygiene: unlabelled / zero-size / non-finite prints are dropped.
  const hg = S.TapeAggregator();
  hg.push({ ts: T, ex: '', isBuy: true, price: 100, qty: 1, notional: 100 });
  hg.push({ ts: T, ex: 'x', isBuy: true, price: 100, qty: 0, notional: 0 });
  hg.push({ ts: NaN, ex: 'x', isBuy: true, price: 100, qty: 1, notional: 100 });
  assert.strictEqual(hg.list().length, 0, 'malformed prints never land');
});

// ─── 68. sizeTier: boundary classification + defaults export (§4i) ───────────
group('sizeTier: exact-boundary → higher tier, just-below → lower, override merge', () => {
  const st = S.sizeTier;
  // Boundaries are INCLUSIVE LOWER (≥): exactly at a cut takes the HIGHER tier.
  assert.strictEqual(st(1e5), 'sig');
  assert.strictEqual(st(2.5e5), 'large');
  assert.strictEqual(st(1e6), 'huge');
  assert.strictEqual(st(5e6), 'whale');
  // Just below each cut → the lower tier.
  assert.strictEqual(st(1e5 - 0.01), 'baseline');
  assert.strictEqual(st(2.5e5 - 0.01), 'sig');
  assert.strictEqual(st(1e6 - 0.01), 'large');
  assert.strictEqual(st(5e6 - 0.01), 'huge');
  assert.strictEqual(st(1e7), 'whale', 'well above the top cut stays whale');

  // The exported defaults object holds the §4i BTC-scaled conventions.
  assert.deepStrictEqual(S.SIZE_TIER_DEFAULTS, { sig: 1e5, large: 2.5e5, huge: 1e6, whale: 5e6 });

  // A PARTIAL override merges over the defaults (move one cut, keep the rest).
  assert.strictEqual(st(3e5, { whale: 2e5 }), 'whale', 'lowered whale cut fires; other cuts still default');
  assert.strictEqual(st(1.5e5, { whale: 2e5 }), 'sig', 'unchanged cuts keep their default');

  // Non-finite notional → baseline (a NaN/Infinity size is not a whale — the
  // finiteness rail runs BEFORE the cuts, never coerced upward).
  assert.strictEqual(st(NaN), 'baseline');
  assert.strictEqual(st(Infinity), 'baseline', 'non-finite guard precedes the ≥ cuts');
});

// ─── 69. BigPrintRail: huge/whale only, newest-first, ring-bound (§4i) ───────
group('BigPrintRail: keeps only huge/whale, newest-first, ring bound at N', () => {
  const T = 1783080000000;
  const rail = S.BigPrintRail({ max: 2 });
  rail.push({ ts: T, ex: 'a', notional: 5e5 });     // large → dropped
  rail.push({ ts: T + 1, ex: 'b', notional: 2.4e5 }); // sig → dropped
  rail.push({ ts: T + 2, ex: 'c', notional: 9e4 });   // baseline → dropped
  assert.strictEqual(rail.list().length, 0, 'sub-huge prints never enter the rail');

  rail.push({ ts: T + 3, ex: 'd', notional: 1e6 });   // huge
  rail.push({ ts: T + 4, ex: 'e', notional: 6e6 });   // whale
  rail.push({ ts: T + 5, ex: 'f', notional: 2e6 });   // huge → evicts the oldest (the 1e6)
  const l = rail.list();
  assert.strictEqual(l.length, 2, 'ring bound at max=2');
  assert.deepStrictEqual(l.map((r) => r.notional), [2e6, 6e6], 'newest-first, oldest huge evicted');
  assert.deepStrictEqual(l.map((r) => r.tier), ['huge', 'whale'], 'each kept row tagged with its tier');
  assert.strictEqual(l[0].ex, 'f', 'row fields carried through');

  // Non-finite notional never lands.
  rail.push({ ts: T + 6, ex: 'g', notional: NaN });
  assert.strictEqual(rail.list()[0].ex, 'f', 'a NaN-notional row is ignored');
});

// ─── 70. TradeImprint: buy/sell split, tick rounding, window prune (§4i) ─────
group('TradeImprint: level buy/sell split, nearest-tick rounding, window prune', () => {
  const T = 1783080000000;
  const ti = S.TradeImprint({ windowMs: 1000 });
  // Nearest-tick rounding (tick 1): 100.4 → 100, 99.9 → 100, 100.6 → 101.
  ti.push(T, 100.4, 2, true, 1);
  ti.push(T + 10, 100.6, 3, false, 1);
  ti.push(T + 20, 99.9, 1, true, 1);
  assert.deepStrictEqual(ti.levelAt(100), { buyQty: 3, sellQty: 0 }, 'two buys round to the 100 level');
  assert.deepStrictEqual(ti.levelAt(101), { buyQty: 0, sellQty: 3 }, 'the sell rounds to 101');
  // map() re-expands integer indices to grid prices.
  const m = ti.map();
  assert.deepStrictEqual([...m.keys()].sort((a, b) => a - b), [100, 101], 'grid prices, not indices');
  assert.deepStrictEqual(m.get(100), { buyQty: 3, sellQty: 0 });

  // WINDOW PRUNE (half-open (ts−windowMs, ts]): a print 2000 ms on ages out the
  // three earlier prints — the 100 level drops to just the new print.
  ti.push(T + 2000, 100, 5, true, 1);
  assert.deepStrictEqual(ti.levelAt(100), { buyQty: 5, sellQty: 0 }, 'aged buys pruned, only the survivor remains');
  assert.strictEqual(ti.size, 1, 'the emptied 101 level is deleted, not left at a phantom 0');
  assert.deepStrictEqual([...ti.map().keys()], [100], 'snapshot holds only the live level');

  // Hygiene: zero/negative qty, non-finite ts/price, or no tick grid → dropped.
  const ti2 = S.TradeImprint();
  ti2.push(T, 100, 0, true, 1);
  ti2.push(T, 100, 1, true, NaN);
  ti2.push(NaN, 100, 1, true, 1);
  assert.strictEqual(ti2.size, 0, 'malformed prints never create a level');
  assert.deepStrictEqual(ti2.levelAt(100), { buyQty: 0, sellQty: 0 }, 'untouched level reads zeros');
});

// ─── 71. DepthLadder: ladderRows / depthImbalance / logBarWidth / merge (§4i) ─
group('DepthLadder: cumulative ladder, N-tick imbalance boundary, log bars, USDT-only merge', () => {
  // Book best-first: bids DESC, asks ASC. mid 100, tick 1.
  const book = { bids: [[99, 1], [98, 2], [97, 4]], asks: [[101, 1], [102, 3], [103, 5]] };

  // ladderRows: cumulative qty from mid OUTWARD, nRows-capped, ticks-from-mid.
  const lad = S.ladderRows(book, 100, 1, 2);
  assert.deepStrictEqual(lad.asks, [
    { side: 'ask', price: 101, qty: 1, cum: 1, ticks: 1 },
    { side: 'ask', price: 102, qty: 3, cum: 4, ticks: 2 },
  ], 'ask cum 1 then 1+3=4, 103 beyond nRows dropped');
  assert.deepStrictEqual(lad.bids, [
    { side: 'bid', price: 99, qty: 1, cum: 1, ticks: 1 },
    { side: 'bid', price: 98, qty: 2, cum: 3, ticks: 2 },
  ], 'bid cum 1 then 1+2=3, 97 beyond nRows dropped');

  // Coarse tick-group: tick 2 buckets sub-tick levels onto one grid row (asks
  // snap UP, bids DOWN — the price-improving convention). 101+102 → grid 102.
  const grouped = S.ladderRows(book, 100, 2, 3);
  assert.ok(grouped.asks[0].price === 102 && approx(grouped.asks[0].qty, 4), 'tick-2 groups 101+102 into the 102 row');

  // depthImbalance within 2 ticks of mid 100: bids 99,98 (97 is 3 ticks — OUT),
  // asks 101,102 (103 is 3 ticks — OUT). The level EXACTLY 2 ticks out (98/102)
  // is INCLUDED (inclusive band); N+1 is excluded.
  const di = S.depthImbalance(book, 100, 2, 1);
  assert.ok(approx(di.bidSum, 3), 'bidSum 1+2, the 3-tick 97 excluded');
  assert.ok(approx(di.askSum, 4), 'askSum 1+3, the 3-tick 103 excluded');
  assert.ok(approx(di.pct, (3 - 4) / 7), 'pct = signed (bid−ask)/(bid+ask), house convention');
  // A tighter band (1 tick) drops the 2-tick levels entirely.
  const di1 = S.depthImbalance(book, 100, 1, 1);
  assert.ok(approx(di1.bidSum, 1) && approx(di1.askSum, 1), 'N=1 keeps only the 1-tick levels');
  // Empty band → NaN pct (absence, not balance).
  assert.ok(Number.isNaN(S.depthImbalance({ bids: [], asks: [] }, 100, 5, 1).pct), 'empty band → NaN, never a fake 0');

  // logBarWidth: 0 at qty 0, 1 at qty=maxQty, clamps above, monotone, bounded.
  assert.strictEqual(S.logBarWidth(0, 10), 0);
  assert.strictEqual(S.logBarWidth(10, 10), 1);
  assert.strictEqual(S.logBarWidth(20, 10), 1, 'qty > maxQty clamps to 1');
  assert.strictEqual(S.logBarWidth(-5, 10), 0);
  assert.strictEqual(S.logBarWidth(5, 0), 0, 'no scale (maxQty ≤ 0) → 0');
  assert.ok(S.logBarWidth(1, 100) < S.logBarWidth(2, 100), 'monotone in qty');
  assert.ok(S.logBarWidth(2, 100) < S.logBarWidth(50, 100), 'monotone across the range');
  const w = S.logBarWidth(5, 10);
  // Pin the VALUE independently of the impl's log1p call, via the identity
  // log1p(x) ≡ ln(1+x): ln(6)/ln(11). A plain log1p mirror would only re-derive
  // the formula (tautology) — this catches a wrong base/offset the bounds and
  // monotonicity asserts above would miss.
  assert.ok(w > 0 && w < 1 && approx(w, Math.log(6) / Math.log(11)), 'ln(6)/ln(11) — independent of the log1p call');

  // mergeSameQuoteBooks: two USDT legs SUM onto the primary's grid; a coin/USD
  // leg is EXCLUDED (not rescaled) — cross-venue depth is a display approx.
  const booksByLeg = {
    bybit_linear: { bids: [[100, 1]], asks: [[101, 2]] },
    okx_swap: { bids: [[100, 3]], asks: [[101, 1]] },
    coinbase: { bids: [[100, 99]], asks: [[101, 99]] },
  };
  const meta = {
    bybit_linear: { quote: 'USDT', tickSize: 1, primary: true },
    okx_swap: { quote: 'USDT', tickSize: 1 },
    coinbase: { quote: 'USD', tickSize: 0.01 },
  };
  const merged = S.mergeSameQuoteBooks(booksByLeg, meta);
  assert.deepStrictEqual(merged.includedLegs, ['bybit_linear', 'okx_swap'], 'only the USDT legs included');
  assert.deepStrictEqual(merged.excludedLegs, ['coinbase'], 'the coin/USD leg excluded');
  assert.deepStrictEqual(merged.book.bids, [[100, 4]], 'USDT bids summed (1+3), USD 99 NOT added');
  assert.deepStrictEqual(merged.book.asks, [[101, 3]], 'USDT asks summed (2+1), USD 99 NOT added');
  assert.strictEqual(merged.gridTick, 1, 'the flagged primary owns the grid');
});

// ─── 72. liqTier: liquidation notional boundary classification (§4i) ─────────
group('liqTier: exact-boundary → higher tier, just-below → lower, non-finite → baseline', () => {
  const lt = S.liqTier;
  // Own tiers, SEPARATE from the tape's; same INCLUSIVE LOWER (≥) convention:
  // exactly at a cut takes the HIGHER tier.
  assert.strictEqual(lt(2.5e5), 'big');
  assert.strictEqual(lt(1e6), 'huge');
  // Just below each cut → the lower tier.
  assert.strictEqual(lt(2.5e5 - 0.01), 'baseline');
  assert.strictEqual(lt(1e6 - 0.01), 'big');
  assert.strictEqual(lt(9e5), 'big', 'between the cuts');
  assert.strictEqual(lt(4e6), 'huge', 'well above the top cut stays huge');

  // The exported defaults hold the §4i liq conventions (distinct from sizeTier).
  assert.deepStrictEqual(S.LIQ_TIER_DEFAULTS, { big: 2.5e5, huge: 1e6 });

  // A partial override merges over the defaults.
  assert.strictEqual(lt(1.2e6, { huge: 2e6 }), 'big', 'raised huge cut: 1.2M no longer huge');
  assert.strictEqual(lt(2e6, { huge: 2e6 }), 'huge', 'exactly at the raised cut fires');

  // Non-finite → baseline: the audio ping (liqTier === 'huge') must NEVER fire
  // on a NaN/Infinity notional (the finiteness rail runs BEFORE the ≥ cuts).
  assert.strictEqual(lt(NaN), 'baseline');
  assert.strictEqual(lt(Infinity), 'baseline', 'non-finite guard precedes the ≥ cuts');
});

// ─── 73. filterTapeRows: market filter + spot/perp tag + tier tag (§4i) ──────
group('filterTapeRows: both/spot/perp filter, spot/perp market tag, venue+minN gates', () => {
  // A fake ex→market resolver (the controller injects the registry-backed one):
  // coinbase/binance_spot are spot legs, bybit is a perp leg.
  const marketOf = (ex) => (ex === 'coinbase' || ex === 'binance_spot') ? 'spot' : 'perp';
  const rows = [
    { ts: 1, ex: 'bybit',        isBuy: true,  price: 100, qty: 3, notional: 3e5,   count: 1 },
    { ts: 2, ex: 'coinbase',     isBuy: false, price: 100, qty: 1, notional: 1.2e6, count: 2 },
    { ts: 3, ex: 'binance_spot', isBuy: true,  price: 100, qty: 1, notional: 5e4,   count: 1 },
  ];

  // market 'both' keeps all; each row tagged with its resolved market AND its
  // sizeTier (3e5 → large, 1.2e6 → huge, 5e4 → baseline; hand-classified).
  const both = S.filterTapeRows(rows, { market: 'both', venue: 'all', minN: 0, marketOf });
  assert.strictEqual(both.length, 3);
  assert.deepStrictEqual(both.map((r) => r.market), ['perp', 'spot', 'spot'], 'spot/perp tag per row');
  assert.deepStrictEqual(both.map((r) => r.tier), ['large', 'huge', 'baseline'], 'sizeTier tag rides along');

  // market 'spot' drops the perp row; 'perp' drops the spot rows.
  assert.deepStrictEqual(S.filterTapeRows(rows, { market: 'spot', marketOf }).map((r) => r.ex),
    ['coinbase', 'binance_spot'], 'spot filter keeps only spot legs');
  assert.deepStrictEqual(S.filterTapeRows(rows, { market: 'perp', marketOf }).map((r) => r.ex),
    ['bybit'], 'perp filter keeps only perp legs');

  // Single-venue filter is exact (each row is one venue's block).
  assert.deepStrictEqual(S.filterTapeRows(rows, { venue: 'coinbase', marketOf }).map((r) => r.ex),
    ['coinbase'], 'venue filter keeps the one venue');

  // min-notional gate drops sub-threshold rows (the 5e4 binance_spot print).
  assert.deepStrictEqual(S.filterTapeRows(rows, { minN: 2e5, marketOf }).map((r) => r.notional),
    [3e5, 1.2e6], 'minN drops the sub-threshold print');

  // An ex the resolver does not know defaults PERP (never masquerades as spot),
  // and no resolver at all → everything perp: a wiring bug fails toward perp,
  // never a silent spot mislabel.
  const dflt = S.filterTapeRows([{ ts: 1, ex: 'mystery', notional: 3e5, count: 1 }], { market: 'both' });
  assert.strictEqual(dflt[0].market, 'perp', 'unknown ex / no resolver → perp default');
});

// ─── N1: makePanelGuard circuit breaker (paint-loop error boundary) ──────────
// The pure primitive behind terminal.js safePanel: closed→open per-panel render
// breaker. Pure/DOM-free/clock-free → directly require-able. Pins the reset
// policy (N consecutive → open; ok() resets the streak; open latched until
// reset()) and the fail()-returns-true-once-on-death contract the log rate-limit
// depends on.
group('makePanelGuard circuit breaker (N1)', () => {
  const E = () => new Error('boom');

  // below-threshold throws stay CLOSED (N-1 of N=3), fail() returns false
  const g1 = S.makePanelGuard({ threshold: 3 });
  assert.strictEqual(g1.fail(E()), false); assert.strictEqual(g1.isDead(), false);
  assert.strictEqual(g1.fail(E()), false); assert.strictEqual(g1.isDead(), false);

  // the Nth consecutive throw OPENS it and returns the death transition ONCE
  assert.strictEqual(g1.fail(E()), true, 'Nth consecutive throw opens the breaker');
  assert.strictEqual(g1.isDead(), true);
  assert.strictEqual(g1.fail(E()), false, 'already-dead fail() returns false (log once)');

  // a clean run RESETS the consecutive streak — 2 + ok() + 2 never reaches 3
  const g2 = S.makePanelGuard({ threshold: 3 });
  g2.fail(E()); g2.fail(E()); g2.ok();
  g2.fail(E()); g2.fail(E());
  assert.strictEqual(g2.isDead(), false, 'ok() cleared the streak — 2+2 never reaches 3');
  assert.strictEqual(g2.fail(E()), true, 'a fresh run of 3 opens it');

  // open STAYS open — ok() never revives; only reset() does (symbol-switch)
  g2.ok(); assert.strictEqual(g2.isDead(), true, 'ok() never revives a dead guard');
  g2.reset(); assert.strictEqual(g2.isDead(), false, 'reset() revives (symbol-switch re-init)');
  assert.strictEqual(g2.stats().consecutive, 0, 'reset() clears the consecutive count');

  // stats() exposes the UI-chip shape; lastError carries the message
  const g3 = S.makePanelGuard({ threshold: 1 });
  assert.strictEqual(g3.fail(new Error('NaN into canvas path')), true, 'threshold 1 opens on the first throw');
  const s = g3.stats();
  assert.strictEqual(s.dead, true);
  assert.strictEqual(s.threshold, 1);
  assert.strictEqual(s.lastError, 'NaN into canvas path');
  assert.ok(s.failures >= 1 && typeof s.consecutive === 'number', 'stats carries failures + consecutive');

  // a non-Error throw still yields a string lastError (never crashes the chip)
  const g4 = S.makePanelGuard({ threshold: 1 });
  g4.fail('bare string');
  assert.strictEqual(g4.stats().lastError, 'bare string', 'non-Error throw stringified');

  // default threshold = 3; non-finite/0 clamps to ≥1 (never a 0-threshold no-op)
  assert.strictEqual(S.makePanelGuard().stats().threshold, 3, 'default threshold 3');
  assert.strictEqual(S.makePanelGuard({ threshold: 0 }).stats().threshold, 1, '0 clamps to 1');
  assert.strictEqual(S.makePanelGuard({ threshold: NaN }).stats().threshold, 3, 'NaN → default 3');
});

group('makeHealthCounter accumulator (N5)', () => {
  // bump accrues by kind + sub-reason; count/snapshot read the running totals.
  const h = S.makeHealthCounter();
  assert.strictEqual(h.count('droppedFrame'), 0, 'unbumped kind reads 0');
  h.bump('droppedFrame', 'parse');
  h.bump('droppedFrame', 'parse');
  h.bump('droppedFrame', 'handler');
  assert.strictEqual(h.count('droppedFrame'), 3, 'three drops → count 3');
  assert.strictEqual(h.snapshot().kinds.droppedFrame, 3, 'snapshot kinds total matches count');
  assert.deepStrictEqual(h.snapshot().detail.droppedFrame, { parse: 2, handler: 1 },
    'per-reason detail splits 2 parse / 1 handler (the header chip tooltip)');

  // falsy kind is a no-op (a guard against a bad call site adding a phantom count).
  const before = JSON.stringify(h.snapshot().kinds);
  h.bump('');
  h.bump(null);
  h.bump(undefined);
  assert.strictEqual(JSON.stringify(h.snapshot().kinds), before, 'falsy kind never mutates the tally');

  // snapshot is a COPY — mutating it can never corrupt the live counter (pure).
  const snap = h.snapshot();
  snap.kinds.droppedFrame = 999;
  snap.detail.droppedFrame.parse = 999;
  assert.strictEqual(h.count('droppedFrame'), 3, 'mutating a snapshot leaves the counter untouched');
  assert.deepStrictEqual(h.snapshot().detail.droppedFrame, { parse: 2, handler: 1 }, 'detail is deep-copied');

  // generic BY KIND: normSkip (the deferred adapter per-level skip) lands under
  // its own kind without disturbing droppedFrame — the one-line future add works.
  h.bump('normSkip', 'bybit');
  assert.strictEqual(h.count('normSkip'), 1, 'a new kind counts independently');
  assert.strictEqual(h.count('droppedFrame'), 3, 'the new kind does not touch droppedFrame');

  // T-4 R1: socket closes ride the SAME generic-by-kind counter — no second
  // telemetry path in the store. subKey is '<leg>/<code>' so the per-leg, per-
  // code breakdown the chip tooltip needs falls straight out of detail{}.
  h.bump('socketClose', 'bybit/1006');
  h.bump('socketClose', 'bybit/1006');
  h.bump('socketClose', 'bybit_spot/1000');
  assert.strictEqual(h.count('socketClose'), 3, 'closes accumulate under their own kind');
  assert.strictEqual(h.count('droppedFrame'), 3, 'a close is NOT a dropped frame — the counts stay separate');
  assert.deepStrictEqual(h.snapshot().detail.socketClose, { 'bybit/1006': 2, 'bybit_spot/1000': 1 },
    'per-leg/per-code detail is what makes the ping-margin theory testable');

  // reset() zeroes everything (symbol-switch re-init).
  h.reset();
  assert.strictEqual(h.count('droppedFrame'), 0, 'reset zeroes droppedFrame');
  assert.strictEqual(h.count('normSkip'), 0, 'reset zeroes every kind');
  assert.deepStrictEqual(h.snapshot().kinds, {}, 'reset empties the kinds map');
  assert.deepStrictEqual(h.snapshot().detail, {}, 'reset empties the detail map');
});

group('livewire onDropped silent-catch hook (N5)', () => {
  // Minimal WebSocket stub: captures the instance makeSocket constructs so we can
  // fire ws.onmessage(...) by hand — the exact frame livewire hands the socket.
  // No auto-open (we never call onopen → no heartbeat interval); the watchdog
  // interval each makeSocket starts is cleared by handle.close() at the end (or
  // the process would hang on the pending timer).
  let captured = null;
  function StubWS(url) { this.url = url; this.readyState = 0; captured = this; }
  StubWS.OPEN = 1;
  StubWS.prototype.close = function () { this.readyState = 3; };
  const prevWS = global.WebSocket;
  global.WebSocket = StubWS;
  try {
    // (a) parse-fail → onDropped('parse'), fired once, no throw escapes onmessage.
    let reasons = [];
    const h1 = LW.makeSocket({ url: 'ws://x', subscribe() {}, onMessage() {} },
      { onStatus() {}, onDropped(r) { reasons.push(r); } });
    assert.doesNotThrow(() => captured.onmessage({ data: 'not json{' }), 'bad JSON never throws out of onmessage');
    assert.deepStrictEqual(reasons, ['parse'], 'JSON.parse failure → onDropped("parse") once');
    h1.close();

    // (b) handler throw → onDropped('handler'), fired once, no throw escapes.
    reasons = [];
    const h2 = LW.makeSocket({ url: 'ws://x', subscribe() {}, onMessage() { throw new Error('bad frame'); } },
      { onStatus() {}, onDropped(r) { reasons.push(r); } });
    assert.doesNotThrow(() => captured.onmessage({ data: '{"ok":1}' }), 'a throwing handler never kills the socket');
    assert.deepStrictEqual(reasons, ['handler'], 'adapter.onMessage throw → onDropped("handler") once');
    h2.close();

    // (c) healthy frame → onDropped NEVER called (silent when healthy).
    reasons = [];
    const h3 = LW.makeSocket({ url: 'ws://x', subscribe() {}, onMessage() {} },
      { onStatus() {}, onDropped(r) { reasons.push(r); } });
    captured.onmessage({ data: '{"ok":1}' });
    assert.deepStrictEqual(reasons, [], 'a good frame through a healthy handler drops nothing');
    h3.close();

    // (d) BACKWARD-COMPAT (proves app.js is unaffected): api WITHOUT onDropped,
    // throwing handler — both the parse-fail and handler-throw catches swallow
    // silently, nothing throws, the socket survives every frame.
    const h4 = LW.makeSocket({ url: 'ws://x', subscribe() {}, onMessage(m) { if (m.boom) throw new Error('boom'); } },
      { onStatus() {} });   // no onDropped — exactly like app.js
    assert.doesNotThrow(() => {
      captured.onmessage({ data: 'not json{' });   // parse-fail path, onDropped absent
      captured.onmessage({ data: '{"boom":1}' });  // handler-throw path, onDropped absent
      captured.onmessage({ data: '{"ok":1}' });    // healthy
    }, 'no onDropped (app.js path) → both catches swallow silently, socket never dies');
    h4.close();

    // (e) a THROWING onDropped can never kill the socket — telemetry is never fatal.
    const h5 = LW.makeSocket({ url: 'ws://x', subscribe() {}, onMessage() {} },
      { onStatus() {}, onDropped() { throw new Error('telemetry blew up'); } });
    assert.doesNotThrow(() => captured.onmessage({ data: 'not json{' }), 'a throwing onDropped is caught — socket survives');
    h5.close();
  } finally {
    global.WebSocket = prevWS;
  }
});

group('type scale enforced: no raw font-size px literals in terminal.css (T-4)', () => {
  // The scale used to bottom out at --fs-xs (11px) while terminal.css carried 39
  // raw literals below it — 24x 10px, 14x 9px, 1x 8px — so the dense chrome was
  // sized by accident, panel by panel, with no shared step. The two steps the
  // design actually needs are NAMED now (--fs-2xs tabular chrome, --fs-3xs
  // micro-annotation), which makes this rule checkable instead of aspirational.
  const css = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'terminal.css'), 'utf8');
  const raw = [];
  const re = /font-size:\s*([0-9.]+)px/g;
  let m;
  while ((m = re.exec(css)) !== null) {
    const line = css.slice(0, m.index).split('\n').length;
    raw.push('terminal.css:' + line + ' → ' + m[0]);
  }
  assert.deepStrictEqual(raw, [],
    'every font-size in terminal.css must use a scale token (--fs-3xs…--fs-2xl), not a px literal');

  // The tokens the sheet now depends on must EXIST in styles.css, or every one of
  // those converted sites silently falls back to the inherited size.
  const tokens = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'styles.css'), 'utf8');
  for (const t of ['--fs-3xs', '--fs-2xs', '--fs-xs', '--fs-sm', '--fs-base', '--fs-lg', '--fs-xl']) {
    assert.ok(new RegExp('\\' + t + ':\\s*[0-9]').test(tokens), t + ' is defined in styles.css');
  }
  // Every --fs-* token terminal.css REFERENCES must be one of those defined — a
  // typo'd var() is invisible in a browser (it just inherits).
  const referenced = new Set((css.match(/var\((--fs-[a-z0-9-]+)/g) || []).map((v) => v.slice(4)));
  for (const t of referenced) {
    assert.ok(new RegExp('\\' + t + ':\\s*[0-9]').test(tokens),
      'terminal.css references ' + t + ' — it must be defined in styles.css');
  }
});

group('PANEL_DEFS registry <-> DOM consistency + derived-table shape (M3)', () => {
  const DEFS = S.PANEL_DEFS;
  assert.ok(Array.isArray(DEFS) && DEFS.length >= 30, 'PANEL_DEFS is the registry array');

  // Identity: keys unique. A duplicate would silently make one descriptor win in
  // every derived table, which is precisely the class of bug the registry exists
  // to kill — so it must fail loudly here.
  const keys = DEFS.map((d) => d.key);
  assert.strictEqual(new Set(keys).size, keys.length, 'panel keys are unique');

  // Budgets: paint budgets must be real positive numbers. A NaN/0 would make
  // due() either never fire or fire every frame.
  for (const d of DEFS) {
    assert.ok(Number.isFinite(d.minMs) && d.minMs > 0, d.key + ': minMs is a positive number');
  }

  // Sections must be one of the five collapse sections the HTML declares via
  // data-sec — a typo'd section name would leave a panel permanently ungated.
  const SECTIONS = ['orderflow', 'structure', 'auction', 'intelligence', 'portfolio'];
  for (const d of DEFS) {
    if (d.section !== null) {
      assert.ok(SECTIONS.indexOf(d.section) >= 0, d.key + ": section '" + d.section + "' is a real section");
    }
  }

  // The header exemption is LOAD-BEARING, not an omission: the stats strip
  // carries the connection chips, so gating it could mask a dead feed. Pin it so
  // nobody "completes the table" by giving header a section/anchor.
  // TWO descriptors are gate-exempt, for two DIFFERENT documented reasons. Both
  // are pinned by name and by reason, because "completing the table" is the
  // tempting wrong move in each case and neither would fail loudly.
  const exempt = DEFS.filter((d) => d.section === null || d.anchor === null);
  assert.deepStrictEqual(exempt.map((d) => d.key).sort(), ['header', 'local'],
    'exactly two gate-exempt descriptors: header (gating it could mask feed health) '
    + 'and local (spans two sections; its node is display:none so an IO would latch it off)');
  const hdr = DEFS.find((d) => d.key === 'header');
  assert.strictEqual(hdr.section, null, 'header has NO section');
  assert.strictEqual(hdr.anchor, null, 'header has NO anchor');
  const loc = DEFS.find((d) => d.key === 'local');
  assert.strictEqual(loc.section, null, 'local has NO section — it spans auction AND portfolio');
  assert.strictEqual(loc.anchor, null, 'local has NO anchor — an IO on a display:none node never intersects');
  // ...but it DOES own render units. anchor and units are independent, which the
  // old parallel tables could only express by special-casing the key twice.
  assert.deepStrictEqual(S.panelUnits('local'), ['local-only-api', 'local-only-econ'],
    'local owns both fold containers despite having no anchor');

  // REGISTRY <-> DOM: every anchor must be an id that actually exists in
  // terminal.html. A stale anchor silently disables that panel's offscreen
  // gating — no error, just a panel that repaints while invisible.
  const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'terminal.html'), 'utf8');
  for (const d of DEFS) {
    for (const id of S.panelUnits(d.key)) {
      assert.ok(html.indexOf('id="' + id + '"') >= 0,
        d.key + ": render unit '" + id + "' exists in terminal.html");
    }
  }
  // fp is the one panel owning two render units — pinned, because the N1
  // quarantine's shared-panel logic depends on that set being complete.
  assert.deepStrictEqual(S.panelUnits('fp'), ['view-footprint', 'view-cvd'],
    'fp owns BOTH the footprint canvas and the CVD strip');
  assert.deepStrictEqual(S.panelUnits('header'), [],
    'header owns no gated render unit (terminal.js falls back to the header panel)');
  // ...and the reverse direction: every id="view-*" in the HTML must be claimed by
  // a descriptor, so a panel added to the DOM cannot be left out of the loop.
  //
  // ONE documented exception: 'view-header' is the stats strip. terminal.html says
  // it carries no data-sec because connection health must stay visible, and
  // terminal.js falls back to 'view-header' for any key with no units. It is
  // therefore expected to be unclaimed — listed here by name so the reverse check
  // stays strict for every OTHER panel instead of being weakened to allow it.
  // ('view-cvd' is NOT here: it is claimed, as fp's `extra` render unit.)
  const UNGATED_BY_DESIGN = ['view-header'];
  const domViews = (html.match(/id="(view-[a-z0-9-]+)"/g) || []).map((m) => m.slice(4, -1));
  const claimed = new Set(S.panelUnitIds());
  for (const v of domViews) {
    if (UNGATED_BY_DESIGN.indexOf(v) >= 0) continue;
    assert.ok(claimed.has(v), 'DOM anchor ' + v + ' is claimed by a PANEL_DEFS descriptor');
  }
  // Pin that the exception really is unclaimed — if someone gives header an
  // anchor, this list is stale and the reader should be told, not silently obeyed.
  for (const v of UNGATED_BY_DESIGN) {
    assert.ok(!claimed.has(v), v + ' is ungated BY DESIGN — remove it from UNGATED_BY_DESIGN if that changed');
  }

  // Derived tables: shapes the render loop depends on.
  const t = S.panelTables();
  assert.deepStrictEqual(Object.keys(t.dirty).sort(), keys.slice().sort(), 'dirty covers every panel');
  assert.ok(Object.values(t.dirty).every((v) => v === true), 'dirty starts all-true (first paint)');
  assert.ok(Object.values(t.lastAt).every((v) => v === 0), 'lastAt starts all-zero');
  assert.deepStrictEqual(Object.keys(t.minMs).sort(), keys.slice().sort(), 'minMs covers every panel');
  // The null-OMISSION contract: consumers test key PRESENCE (`for (const k in
  // VIEW_ANCHOR)`), so a stored null would silently enrol header in the gate.
  for (const k of ['header', 'local']) {
    assert.ok(!(k in t.secOf), 'secOf OMITS ' + k + ' (not a null entry)');
    assert.ok(!(k in t.anchors), 'anchors OMITS ' + k + ' (not a null entry)');
  }
  assert.strictEqual(Object.keys(t.secOf).length, keys.length - exempt.length, 'secOf covers every gated panel');
  assert.strictEqual(Object.keys(t.anchors).length, keys.length - exempt.length, 'anchors covers every gated panel');

  // Fresh objects per call — these are MUTABLE render state (dirty flips every
  // frame); a shared object would alias across callers.
  const t2 = S.panelTables();
  t2.dirty.fp = false;
  assert.strictEqual(t.dirty.fp, true, 'panelTables returns fresh objects, never shared state');

  // ── GOLDEN: the registry must derive EXACTLY the hand-written literals it
  // replaced ────────────────────────────────────────────────────────────────
  // M3 is a pure refactor, so the bar is "byte-identical behaviour", and the
  // honest proof is at the DATA level, not the pixel level: the L1 browser
  // harness is NOT pixel-deterministic (the live-clock panels differ up to ~15%
  // between two runs of the SAME code — measured), so a screenshot diff cannot
  // establish equivalence here. These are the pre-M3 values copied verbatim from
  // terminal.js's five literals; if the registry ever stops reproducing them, the
  // refactor has changed behaviour and this fails.
  const GOLDEN_MIN_MS = {
    fp: 250, dom: 120, tape: 180, agg: 220, header: 400, liq: 300, heat: 500, liqmap: 600, det: 250,
    hist: 500, tpo: 800, vp: 800, farb: 500, macro: 800,
    auct: 800, lvls: 1000, micro: 500,
    scr: 800, rsi: 500, opts: 1000, whale: 600, alerts: 300, conf: 800,
    jour: 400, cal: 600, poly: 1000, news: 800, econ: 1000, local: 1000,
    tapeint: 500, walls: 1000, vpin: 800, klev: 1000, basis: 600,
    spcvd: 600,
  };
  const GOLDEN_SEC_OF = {
    fp: 'orderflow', dom: 'orderflow', tape: 'orderflow', agg: 'orderflow',
    liq: 'orderflow', heat: 'orderflow', liqmap: 'orderflow', det: 'orderflow',
    hist: 'structure', tpo: 'structure', vp: 'structure', farb: 'structure', macro: 'structure',
    auct: 'auction', lvls: 'auction', micro: 'auction',
    scr: 'intelligence', rsi: 'intelligence', opts: 'intelligence',
    whale: 'intelligence', alerts: 'intelligence', conf: 'intelligence',
    jour: 'portfolio', cal: 'portfolio', poly: 'portfolio', news: 'portfolio', econ: 'portfolio',
    tapeint: 'orderflow', walls: 'orderflow', basis: 'structure', klev: 'auction', vpin: 'auction',
    spcvd: 'orderflow',
  };
  const GOLDEN_VIEW_ANCHOR = {
    fp: 'view-footprint', dom: 'view-dom', tape: 'view-tape', agg: 'view-aggbook',
    liq: 'view-liq', heat: 'view-bookheat', liqmap: 'view-liqheat', det: 'view-detect',
    hist: 'view-hist', tpo: 'view-tpo', vp: 'view-klinevp', farb: 'view-farb', macro: 'view-macro',
    auct: 'view-auction', lvls: 'view-levels', micro: 'view-micro',
    scr: 'view-screener', rsi: 'view-rsi', opts: 'view-options',
    whale: 'view-whale', alerts: 'view-alerts', conf: 'view-conf',
    jour: 'view-journal', cal: 'view-calendar', poly: 'view-polymarket', news: 'view-news', econ: 'view-econ',
    tapeint: 'view-tapeint', walls: 'view-walls', basis: 'view-basis', klev: 'view-keylevels', vpin: 'view-vpin',
    spcvd: 'view-spotperp',
  };
  assert.deepStrictEqual(t.minMs, GOLDEN_MIN_MS, 'MIN_MS derives byte-identically to the pre-M3 literal');
  assert.deepStrictEqual(t.secOf, GOLDEN_SEC_OF, 'SEC_OF derives byte-identically to the pre-M3 literal');
  assert.deepStrictEqual(t.anchors, GOLDEN_VIEW_ANCHOR, 'VIEW_ANCHOR derives byte-identically to the pre-M3 literal');
  assert.deepStrictEqual(Object.keys(t.dirty).sort(), Object.keys(GOLDEN_MIN_MS).sort(),
    'dirty covers exactly the upstream key set (35 panels incl. header and local)');
});
group('startLeg drop-wiring composition: makeHealthCounter ← onDropped ← livewire (N5)', () => {
  // The live glue in terminal.js startLeg —
  //   if (!api.onDropped) api.onDropped = (reason) => { health.bump('droppedFrame', reason); dirty.header = true; };
  // — installs the counter onto the leg's api, which livewire.drop() then feeds.
  // startLeg itself lives inside terminal.js's browser-only IIFE (not loadable in
  // Node), so this composes the REAL pieces it wires — a genuine makeHealthCounter,
  // the exact install idiom, and the REAL livewire onmessage catches — and asserts
  // the whole chain end-to-end. It locks the contract that IF startLeg installs
  // this callback the swallowed frame lands as droppedFrame/<reason>; the literal
  // startLeg line stays covered by inspection only (the documented Gap-8 e2e seam).
  let captured = null;
  function StubWS(url) { this.url = url; this.readyState = 0; captured = this; }
  StubWS.OPEN = 1;
  StubWS.prototype.close = function () { this.readyState = 3; };
  const prevWS = global.WebSocket;
  global.WebSocket = StubWS;
  try {
    // (a) INSTALL-IF-ABSENT + reason passthrough → the real counter increments.
    const health = S.makeHealthCounter();
    const api = { onStatus() {} };                                   // a leg api WITHOUT onDropped (the common case)
    if (!api.onDropped) api.onDropped = (reason) => { health.bump('droppedFrame', reason); };   // the startLeg idiom
    const h1 = LW.makeSocket({ url: 'ws://x', subscribe() {}, onMessage() { throw new Error('bad'); } }, api);
    captured.onmessage({ data: 'not json{' });   // parse-fail  → drop('parse')
    captured.onmessage({ data: '{"x":1}' });      // handler-throw → drop('handler')
    assert.strictEqual(health.count('droppedFrame'), 2, 'two swallowed frames reach the real counter through the real socket');
    assert.deepStrictEqual(health.snapshot().detail.droppedFrame, { parse: 1, handler: 1 },
      'the drop reason is passed through to the counter verbatim (parse / handler)');
    h1.close();

    // (b) IDEMPOTENT: a caller that already set onDropped keeps THEIRS (app.js
    //     never reaches startLeg, but a future caller pre-wiring must not be
    //     clobbered) — the `if (!api.onDropped)` guard is the whole point.
    let mine = 0;
    const api2 = { onStatus() {}, onDropped() { mine++; } };
    const health2 = S.makeHealthCounter();
    if (!api2.onDropped) api2.onDropped = (reason) => { health2.bump('droppedFrame', reason); };   // guard MUST skip
    const h2 = LW.makeSocket({ url: 'ws://x', subscribe() {}, onMessage() {} }, api2);
    captured.onmessage({ data: 'not json{' });
    assert.strictEqual(mine, 1, "a pre-set onDropped is preserved — the install guard did not overwrite it");
    assert.strictEqual(health2.count('droppedFrame'), 0, 'the health counter was NOT wired in when the caller already had one');
    h2.close();

    // (c) T-4 R1 close-wiring: the same install idiom for api.onClosed, with
    //     the `by !== 'venue'` filter that keeps OUR OWN closes out of the
    //     venue tally. This is the half that decides whether the telemetry
    //     tells the truth: handle.close() (a symbol switch) and the watchdog's
    //     forced reconnect both travel through the very same ws.onclose, so
    //     without the filter every symbol switch would read as a venue drop.
    const health3 = S.makeHealthCounter();
    const seen = [];
    const api3 = { onStatus() {} };
    if (!api3.onClosed) {
      api3.onClosed = (info) => {                                   // the startLeg idiom
        if (!info || info.by !== 'venue') return;
        health3.bump('socketClose', 'okx/' + (info.code == null ? 'unknown' : info.code));
        seen.push(info.by + ':' + info.code);
      };
    }
    const h3 = LW.makeSocket({ url: 'ws://x', subscribe() {}, onMessage() {} }, api3);
    captured.onclose({ code: 1006, reason: '', wasClean: false });   // the venue/transport dropped us
    assert.strictEqual(health3.count('socketClose'), 1, 'a venue close reaches the counter through the real socket');
    assert.deepStrictEqual(health3.snapshot().detail.socketClose, { 'okx/1006': 1 }, 'subKey is <leg>/<code>');
    assert.deepStrictEqual(seen, ['venue:1006'], 'by:"venue" — nobody on our side closed it');
    // OUR OWN close: handle.close() sets closedByUs, so the SAME onclose path
    // must report by:'us' and the leg filter must swallow it.
    h3.close();
    captured.onclose({ code: 1000, reason: 'client', wasClean: true });
    assert.strictEqual(health3.count('socketClose'), 1, 'our own close is NOT counted as a venue drop');

    // (d) an ABSENT onClosed is the app.js path: nothing runs, nothing throws.
    const h4b = LW.makeSocket({ url: 'ws://x', subscribe() {}, onMessage() {} }, { onStatus() {} });
    assert.doesNotThrow(() => captured.onclose({ code: 1006, reason: '', wasClean: false }),
      'no onClosed (app.js path) → the close telemetry is inert');
    h4b.close();

    // (e) a THROWING onClosed can never kill the socket (telemetry rule).
    const h5b = LW.makeSocket({ url: 'ws://x', subscribe() {}, onMessage() {} },
      { onStatus() {}, onClosed() { throw new Error('telemetry blew up'); } });
    assert.doesNotThrow(() => captured.onclose({ code: 1006, reason: '', wasClean: false }),
      'a throwing onClosed is caught — the reconnect path survives');
    h5b.close();
  } finally {
    global.WebSocket = prevWS;
  }
});

// ─── 74. isControlFrame contract + livewire's control-frame branch (T-4 R2) ──
//
// WHY THIS GROUP EXISTS, and why CI missed the regression it locks down: the
// browser harness's clean N5 pass runs under ?replay=1, where livewire is not
// in the transport at all — so OKX's plain-text 'pong' can never appear there,
// and the permanent amber "degraded" chip on the LIVE page was invisible to the
// whole gate. This group is the Node-side witness that closes that blind spot.
group('isControlFrame: exact pong, OKX-only, control frame is neither dropped nor delivered (T-4 R2)', () => {
  // ── the predicate itself ──
  const okx = A.makeOkxAdapter('BTC-USDT-SWAP', () => {});
  const okxBooks = A.makeOkxBooksAdapter('BTC-USDT-SWAP', () => {}, { ctVal: 0.01 });
  for (const [name, ad] of [['makeOkxAdapter', okx], ['makeOkxBooksAdapter', okxBooks]]) {
    assert.strictEqual(typeof ad.isControlFrame, 'function', name + ' declares isControlFrame');
    assert.strictEqual(ad.isControlFrame('pong'), true, name + ": the measured wire is byte 'pong'");
    // EXACT equality is the contract. A loose match (trim/includes/case-fold)
    // would swallow a genuinely corrupt frame that merely contains 'pong' —
    // destroying the very signal the drop counter exists to raise.
    for (const bad of ['pong ', ' pong', 'PONG', 'Pong', 'pongs', '{"op":"pong"}', 'not json{', '', null, undefined, 0]) {
      assert.strictEqual(ad.isControlFrame(bad), false,
        name + ': ' + JSON.stringify(bad) + ' is NOT a control frame (exact equality, never fuzzy)');
    }
  }

  // ── who may declare it: MEASURED, not guessed ──
  // 2026-07-26, 180s live probe: OKX sent 7 non-JSON frames per leg; bybit,
  // bybit-spot, binance-fut, binance-spot and coinbase sent ZERO out of 11,648
  // (Bybit's pong is a JSON frame, {success, ret_msg:'pong'}, already handled in
  // onMessage). Pinning the ABSENCE keeps a future "let's add it everywhere"
  // from creating adapter paths that no wire ever exercises.
  const noCtrl = [
    ['makeBybitAdapter', A.makeBybitAdapter('BTCUSDT', () => {})],
    ['makeBybitSpotAdapter', A.makeBybitSpotAdapter('BTCUSDT', () => {})],
    ['makeBinanceDepthAdapter', A.makeBinanceDepthAdapter('BTCUSDT', () => {})],
    ['makeBinanceSpotAdapter', A.makeBinanceSpotAdapter('BTCUSDT', () => {})],
    ['makeBinanceFutDepthDiff', A.makeBinanceFutDepthDiff('BTCUSDT', () => {})],
    ['makeCoinbaseAdapter', A.makeCoinbaseAdapter('BTC-USD', () => {})],
    ['makeCoinbaseL2Adapter', A.makeCoinbaseL2Adapter('BTC-USD', () => {})],
  ];
  for (const [name, ad] of noCtrl) {
    assert.strictEqual(ad.isControlFrame, undefined,
      name + ' must NOT declare isControlFrame — 0 non-JSON frames measured on its wire');
  }

  // ── T-4 R1: the Bybit v5 legs carry the jitter margin, not the deadline ──
  assert.strictEqual(A.makeBybitAdapter('BTCUSDT', () => {}).pingMs, 15000, 'bybit linear ping = 15s (≲20s vendor rule, 5s margin)');
  assert.strictEqual(A.makeBybitSpotAdapter('BTCUSDT', () => {}).pingMs, 15000, 'bybit spot ping = 15s (same v5 gateway)');
  assert.strictEqual(okx.pingMs, 25000, 'OKX keepalive cadence unchanged (~30s idle drop)');

  // ── livewire's branch: same stub idiom as the N5 group ──
  let captured = null;
  function StubWS(url) { this.url = url; this.readyState = 0; captured = this; }
  StubWS.OPEN = 1;
  StubWS.prototype.close = function () { this.readyState = 3; };
  const prevWS = global.WebSocket;
  global.WebSocket = StubWS;
  try {
    // (a) a control frame is NEITHER a dropped frame NOR a message: nothing is
    //     counted and the adapter's onMessage is never reached (a 'pong' is not
    //     data and must not be normalized).
    const drops = [];
    let delivered = 0;
    const h1 = LW.makeSocket(
      { url: 'ws://x', subscribe() {}, onMessage() { delivered++; }, isControlFrame: (t) => t === 'pong' },
      { onStatus() {}, onDropped(r) { drops.push(r); } });
    captured.onmessage({ data: 'pong' });
    assert.deepStrictEqual(drops, [], 'a keepalive reply is NOT a dropped frame (the whole R2 regression)');
    assert.strictEqual(delivered, 0, 'a control frame never reaches adapter.onMessage');
    // and a genuinely malformed frame on the SAME socket still counts.
    captured.onmessage({ data: 'not json{' });
    assert.deepStrictEqual(drops, ['parse'], 'a truly malformed frame still increments the counter');
    h1.close();

    // (b) an adapter WITHOUT the predicate behaves exactly as before — this is
    //     the app.js byte-unaffected proof (app.js adapters declare none).
    const drops2 = [];
    const h2 = LW.makeSocket({ url: 'ws://x', subscribe() {}, onMessage() {} },
      { onStatus() {}, onDropped(r) { drops2.push(r); } });
    captured.onmessage({ data: 'pong' });
    assert.deepStrictEqual(drops2, ['parse'], 'no predicate → "pong" is an ordinary parse failure, exactly as today');
    h2.close();

    // (c) a THROWING predicate falls through to drop('parse'): we could not
    //     classify the frame, so we say so rather than assume it was benign.
    const drops3 = [];
    const h3 = LW.makeSocket(
      { url: 'ws://x', subscribe() {}, onMessage() {}, isControlFrame() { throw new Error('bad predicate'); } },
      { onStatus() {}, onDropped(r) { drops3.push(r); } });
    assert.doesNotThrow(() => captured.onmessage({ data: 'pong' }), 'a throwing predicate never kills the socket');
    assert.deepStrictEqual(drops3, ['parse'], 'unclassifiable → counted, never silently forgiven');
    h3.close();

    // (d) THE LIVENESS HALF OF THE CONTRACT, driven through the REAL watchdog.
    //     An earlier version of this sub-test described "reach the stale state
    //     the way the watchdog does, then check that a control frame clears it"
    //     and then asserted only that a pong on a HEALTHY socket is silent — so
    //     deleting the control-frame liveness stamp entirely still passed, and
    //     the one clause DESIGN §4j marks binding had zero CI witness. It also
    //     described the WRONG behavior: a control frame must NOT clear 'stale',
    //     because "live feed recovered" is a claim about DATA that a keepalive
    //     cannot support.
    //     No 40s test and no timer games: the watchdog's own callback is
    //     captured off setInterval (WATCHDOG_MS) and Date.now is stubbed, so
    //     the REAL 12s/40s thresholds and the REAL message text are exercised
    //     on a virtual clock.
    const prevSI = global.setInterval, prevCI = global.clearInterval, prevNow = Date.now;
    let wd = null, t = 1700000000000;
    global.setInterval = (fn, ms) => { if (ms === 2000) wd = fn; return { fake: true }; };
    global.clearInterval = () => {};
    Date.now = () => t;
    try {
      const statuses = [];
      const ctrlAdapter = {
        url: 'ws://x', subscribe() {}, onMessage(m, api) { api.markAlive(); },
        isControlFrame: (x) => x === 'pong',
      };
      const h4 = LW.makeSocket(ctrlAdapter, { onStatus(kind, msg) { statuses.push(kind + ':' + msg); } });
      captured.readyState = StubWS.OPEN;
      captured.onopen();
      assert.ok(typeof wd === 'function', 'the watchdog interval callback was captured');
      const T0 = t;
      captured.onmessage({ data: '{"topic":"books"}' });   // a DATA frame: both clocks fresh
      assert.deepStrictEqual(statuses, ['open:live feed connected'], 'a healthy open says exactly one thing');

      // 13s with NO data → amber, and the message names the DATA gap.
      t = T0 + 13000; wd();
      assert.deepStrictEqual(statuses.slice(1), ['stale:stale — no data for 13s'],
        'the watchdog goes amber on the DATA clock and states the data gap');

      // A control frame lands. It must stamp liveness and say NOTHING: before
      // this fix it emitted ('open', 'live feed recovered') and the chip went
      // GREEN over a feed that had delivered nothing for 13s.
      const beforePong = statuses.length;
      captured.onmessage({ data: 'pong' });
      assert.strictEqual(statuses.length, beforePong,
        'a keepalive reply NEVER emits a recovery status — a pong is not evidence about the feed');

      // …and the stamp IS real: 2s later the amber line reports the socket is
      // still answering, which is only derivable from the control clock. That
      // suffix is the positive witness that markControlAlive() ran.
      t = T0 + 15000; wd();
      assert.strictEqual(statuses[statuses.length - 1], 'stale:stale — no data for 15s (socket still answering)',
        'the control stamp shows up as DIAGNOSTIC context, never as a retraction');

      // Pong all the way to DEAD_MS: the force-reconnect must still fire. This
      // is the whole reason the clocks are split — a pongging socket with a
      // dead subscription may not live forever.
      t = T0 + 39000; captured.onmessage({ data: 'pong' }); wd();
      assert.ok(statuses[statuses.length - 1].indexOf('stale:') === 0,
        'still only amber one second before the dead-man threshold');
      t = T0 + 40000; captured.onmessage({ data: 'pong' }); wd();
      assert.strictEqual(statuses[statuses.length - 1], 'reconnecting:live feed stalled — reconnecting',
        'a pong-only socket is force-reconnected on schedule (DEAD_MS reads the DATA clock)');
      h4.close();

      // Recovery is a DATA event, and it is a single clean transition.
      const st2 = [];
      const h5 = LW.makeSocket(ctrlAdapter, { onStatus(kind, msg) { st2.push(kind + ':' + msg); } });
      captured.readyState = StubWS.OPEN;
      captured.onopen();
      const T1 = t;
      captured.onmessage({ data: '{"topic":"books"}' });
      t = T1 + 13000; wd();
      assert.strictEqual(st2[st2.length - 1], 'stale:stale — no data for 13s', 'amber again');
      captured.onmessage({ data: 'pong' });
      assert.strictEqual(st2.length, 2, 'a pong still does not retract amber');
      captured.onmessage({ data: '{"topic":"books"}' });
      assert.strictEqual(st2[st2.length - 1], 'open:live feed recovered',
        'ONLY a data frame retracts amber — it is the only evidence about the subscription');
      t = T1 + 14000; wd();
      assert.strictEqual(st2.length, 3, 'and recovery is a single transition, not a per-tick repeat');
      h5.close();
    } finally {
      global.setInterval = prevSI; global.clearInterval = prevCI; Date.now = prevNow;
    }

    // (e) a venue whose keepalive reply IS valid JSON takes the SAME rule
    //     through onMessage. Bybit v5 answers our {op:'ping'} with
    //     {success, ret_msg:'pong', op:'ping'} — REAL captured frames, one per
    //     leg (fixture t4_bybit_pong; note `op` echoes 'ping', so ret_msg is
    //     what identifies it). It must stamp CONTROL liveness only: routing it
    //     to markAlive() stamped the data clock and made the DEAD_MS force-
    //     reconnect unreachable on the PRIMARY venue (§2) — the split proven in
    //     (d) would have protected the OKX legs and nobody else.
    const bybitPongs = FX.t4_bybit_pong;
    assert.strictEqual(bybitPongs.length, 2, 'fixture precondition: one captured pong per Bybit leg');
    for (const [name, ad] of [['makeBybitAdapter', A.makeBybitAdapter('BTCUSDT', () => {})],
      ['makeBybitSpotAdapter', A.makeBybitSpotAdapter('BTCUSDT', () => {})]]) {
      const seen = [];
      const api = { markAlive() { seen.push('data'); }, markControlAlive() { seen.push('ctrl'); }, onStatus() {} };
      for (const f of bybitPongs) ad.onMessage(f, api);
      ad.onMessage({ op: 'pong' }, api);   // the documented other-gateway shape
      assert.deepStrictEqual(seen, ['ctrl', 'ctrl', 'ctrl'],
        name + ': a pong stamps CONTROL liveness and must NEVER touch the data clock');
      for (const f of FX.bybit_sub_ack) ad.onMessage(f, api);
      assert.deepStrictEqual(seen, ['ctrl', 'ctrl', 'ctrl'], name + ': a subscribe ack stamps nothing at all');
    }
    // The positive control: a real DATA frame on the same adapter still stamps
    // the DATA clock, so the change narrowed nothing but the pong.
    {
      const seen = [];
      const api = { markAlive() { seen.push('data'); }, markControlAlive() { seen.push('ctrl'); }, onStatus() {} };
      const ad = A.makeBybitAdapter('BTCUSDT', () => {});
      for (const f of FX.bybit_tickers_snapshot) ad.onMessage(f, api);
      assert.deepStrictEqual(seen, ['data'], 'a tickers frame is DATA — it still stamps the dead-man clock');
    }
  } finally {
    global.WebSocket = prevWS;
  }
});

// ─── 75. tapeFloorSummary: the floor filters, it never discards (T-4) ────────
group('tapeFloorSummary: sub-floor residue is summarised, never dropped', () => {
  const marketOf = (ex) => (ex === 'coinbase' || ex === 'binance_spot' ? 'spot' : 'perp');
  // Six hand-built rows: three below a $10k floor, one EXACTLY at it, two above.
  const rows = [
    { ts: 1, ex: 'bybit', isBuy: true, price: 100, qty: 1, notional: 1000, count: 3 },
    { ts: 2, ex: 'bybit', isBuy: false, price: 100, qty: 1, notional: 2500, count: 1 },
    { ts: 3, ex: 'coinbase', isBuy: true, price: 100, qty: 1, notional: 500, count: 2 },
    { ts: 4, ex: 'bybit', isBuy: true, price: 100, qty: 1, notional: 10000, count: 1 },   // EXACTLY at the floor
    { ts: 5, ex: 'okx', isBuy: false, price: 100, qty: 1, notional: 50000, count: 4 },
    { ts: 6, ex: 'coinbase', isBuy: true, price: 100, qty: 1, notional: 36000, count: 1 },
  ];
  const opts = { market: 'both', venue: 'all', minN: 10000, marketOf };
  const s = S.tapeFloorSummary(rows, opts);
  assert.strictEqual(s.blocks, 3, 'three aggregated rows fell below the floor');
  assert.strictEqual(s.prints, 6, 'prints = Σ count (3+1+2) — a different, equally honest number');
  assert.strictEqual(s.notional, 4000, 'sub-floor notional summed exactly');
  assert.strictEqual(s.buyNotional, 1500, 'buy side split (1000 + 500)');
  assert.strictEqual(s.sellNotional, 2500, 'sell side split');
  // share denominator = every row passing market/venue, INCLUDING the kept ones.
  assert.ok(Math.abs(s.share - 4000 / 100000) < 1e-12, 'share = sub-floor $ / total $ of the same projection');

  // Boundary is filterTapeRows' VERBATIM: `< minN` is below, so the row exactly
  // AT the floor is SHOWN and must not also appear in the residue.
  const kept = S.filterTapeRows(rows, opts);
  assert.deepStrictEqual(kept.map((r) => r.notional), [10000, 50000, 36000],
    'a block exactly AT the floor is shown (inclusive), matching filterTapeRows');
  // The invariant that proves nothing vanishes silently.
  assert.strictEqual(kept.length + s.blocks, rows.length,
    'kept + hidden === every row passing market/venue — no row is unaccounted for');

  // The SAME projection applies: a venue/market filter narrows both halves.
  const spot = { market: 'spot', venue: 'all', minN: 10000, marketOf };
  const sSpot = S.tapeFloorSummary(rows, spot);
  assert.strictEqual(sSpot.blocks, 1, 'market filter applies to the residue too (only the coinbase $500)');
  assert.strictEqual(sSpot.prints, 2);
  assert.ok(Math.abs(sSpot.share - 500 / 36500) < 1e-12, 'share denominator is the FILTERED total, not the raw tape');
  assert.strictEqual(S.filterTapeRows(rows, spot).length + sSpot.blocks, 2, 'kept + hidden invariant holds per projection');

  // null, never a "0 hidden" object: a floor that is off, or one nothing fell
  // below, has nothing to state — a zero would imply a filter is doing work.
  assert.strictEqual(S.tapeFloorSummary(rows, { minN: 0, marketOf }), null, 'floor off (0) → null');
  assert.strictEqual(S.tapeFloorSummary(rows, { minN: -5, marketOf }), null, 'negative floor → null');
  assert.strictEqual(S.tapeFloorSummary(rows, { minN: NaN, marketOf }), null, 'non-finite floor → null');
  assert.strictEqual(S.tapeFloorSummary(rows, {}), null, 'absent floor → null');
  assert.strictEqual(S.tapeFloorSummary(rows, { minN: 400, marketOf }), null, 'nothing below the floor → null, not a zero object');
  assert.strictEqual(S.tapeFloorSummary(null, { minN: 10000 }), null, 'no rows → null');

  // Hygiene: a row with no `count` counts as one print; a NaN notional is NOT
  // silently binned below the floor (`< minN` is false for NaN — deliberate).
  const odd = S.tapeFloorSummary(
    [{ ts: 1, ex: 'bybit', isBuy: true, notional: 5, count: undefined },
      { ts: 2, ex: 'bybit', isBuy: true, notional: NaN, count: 9 }],
    { minN: 100, marketOf });
  assert.strictEqual(odd.blocks, 1, 'the NaN-notional row is not counted as sub-floor');
  assert.strictEqual(odd.prints, 1, 'a missing count is one print');

  // Purity: the input rows are never mutated.
  const snapshot = JSON.stringify(rows);
  S.tapeFloorSummary(rows, opts);
  assert.strictEqual(JSON.stringify(rows), snapshot, 'input rows untouched (pure)');
});

// ─── 76. news relevance: evidence ladder + visible counts (T-4 §4j) ──────────
group('newsRelevance / filterNewsRows: evidence order over REAL rows, counts always stated', () => {
  // REAL captured rows (fixtures_ws.json t4_toa_news, 2026-07-26) — the repo's
  // fixture discipline: measured wire, never synthesized shapes.
  const raw = FX.t4_toa_news;
  assert.strictEqual(raw.length, 8, 'fixture precondition: 8 captured rows');
  const rows = H.normalizeToaNews(raw);
  assert.strictEqual(rows.length, 8, 'every captured row has a title and a finite time');
  const byTitle = (frag) => {
    const hit = rows.filter((r) => r.title.indexOf(frag) >= 0);
    assert.strictEqual(hit.length, 1, 'fixture precondition: exactly one row matching ' + JSON.stringify(frag));
    return hit[0];
  };

  // ── normalizer: the evidence is carried through, split by provenance ──
  const elon = byTitle('Starship launching');
  assert.deepStrictEqual(elon.coins, [], 'an ACCOUNT-mapped suggestion is not content evidence');
  assert.deepStrictEqual(elon.accountCoins, ['DOGE'], "…but it is kept — @elonmusk is tagged DOGE by WHO he is");
  const btcRow = byTitle('Michael Saylor');
  assert.deepStrictEqual(btcRow.coins, ['BTC'], 'a content-derived suggestion lands in coins[]');
  assert.deepStrictEqual(btcRow.accountCoins, []);

  // ── the ladder, one fixture row per rung ──
  const rel = (r) => S.newsRelevance(r);
  assert.deepStrictEqual(rel(btcRow), { crypto: true, btc: true, why: 'coins' },
    'coins[] is the strongest evidence (79% coverage on the live wire)');
  assert.strictEqual(rel(byTitle('EU adds HTX')).why, 'symbols',
    'symbols[] is consulted next — even though this title also carries a crypto-press prefix');
  assert.strictEqual(rel(byTitle('CLARITY Act')).why, 'press',
    'a crypto-press publisher stamped INTO the title is content, and rescues a real market headline');
  assert.strictEqual(rel(byTitle('Does crypto make your portfolio')).why, 'keyword',
    'the stated lexicon catches a crypto headline from a general-press publisher');
  const upbit = rows.find((r) => r.source === 'Upbit');
  assert.strictEqual(rel(upbit).why, 'venue',
    'an exchange-notice source passes on the venue rung — its Korean title no English lexicon can reach');

  // ── what must NOT pass ──
  assert.strictEqual(rel(elon).crypto, false,
    'the account-mapped Starship tweet is filtered — honoring isAccountMapped would let the loudest noise source straight through');
  for (const frag of ['Smithsonian', 'Cyclospora']) {
    assert.strictEqual(rel(byTitle(frag)).crypto, false,
      'the transport source is NOT evidence: "Blogs" carries WHITEHOUSE and CDC alongside COINDESK (' + frag + ')');
  }

  // BTC emphasis rides coins, NOT the symbols array — measured 0/200 BTC-
  // prefixed symbols on the live wire, i.e. the old view test was dead code.
  assert.strictEqual(rows.filter((r) => rel(r).btc).length, 1, 'exactly one BTC row in the fixture');
  assert.strictEqual(rows.filter((r) => r.symbols.some((s) => s.indexOf('BTC') === 0)).length, 0,
    'and ZERO of them would have been found by the old symbols-prefixed test');

  // ── the visible projection ──
  const crypto = S.filterNewsRows(rows, { mode: 'crypto' });
  assert.strictEqual(crypto.total, 8);
  assert.strictEqual(crypto.kept, 5, '5 of the 8 captured rows carry crypto/market evidence');
  assert.strictEqual(crypto.filtered, 3, 'the Elon tweet + the two non-crypto releases');
  assert.strictEqual(crypto.kept + crypto.filtered, crypto.total, 'kept + filtered === total — nothing goes unaccounted');
  assert.strictEqual(crypto.btcCount, 1);
  assert.strictEqual(crypto.mode, 'crypto');
  assert.ok(crypto.rows.every((r) => r.rel && r.rel.crypto), 'every kept row carries its relevance read');

  const all = S.filterNewsRows(rows, { mode: 'all' });
  assert.strictEqual(all.kept, all.total, "mode 'all' gives every row back — the toggle can undo itself");
  assert.strictEqual(all.filtered, 0, "…and states no phantom 'filtered' count");
  assert.strictEqual(S.filterNewsRows(rows, {}).mode, 'crypto', 'default mode is crypto');
  assert.strictEqual(S.filterNewsRows(rows, { mode: 'nonsense' }).mode, 'crypto', 'an unknown mode falls back, never filters by accident');
  assert.deepStrictEqual(S.filterNewsRows(null, { mode: 'all' }), { rows: [], total: 0, kept: 0, filtered: 0, btcCount: 0, mode: 'all' },
    'no items → empty projection, not a throw');

  // Purity: shallow-copied rows, input untouched (the filterTapeRows idiom).
  const snapshot = JSON.stringify(rows);
  crypto.rows[0].title = 'MUTATED';
  assert.strictEqual(JSON.stringify(rows), snapshot, 'the returned rows are copies — the store is never mutated');

  // The normalizer STILL filters nothing but its own drop rails (a relevance
  // filter at ingest could never be switched off — that is the hidden filter
  // the honesty rail forbids). Synthetic rows here on purpose: an empty title
  // and a non-finite time are shapes the live wire does not hand out.
  const drops = H.normalizeToaNews([
    { title: 'Elon says hi', time: 5, source: 'Twitter', suggestions: [{ coin: 'DOGE', isAccountMapped: true }] },
    { title: '', time: 6, source: 'Blogs' },
    { title: 'no time', source: 'Blogs' },
  ]);
  assert.strictEqual(drops.length, 1, 'only the untitled/undatable rails drop rows — relevance never does');
  assert.strictEqual(S.newsRelevance(drops[0]).crypto, false, 'and the surviving noise row is filtered at RENDER time, where it can be undone');
});

// ─── 77. Layout invariants the local-only strip rests on (T-4 §4j) ──────────
//
// WHY THIS GROUP EXISTS: `renderLocalOnly()` and `localFoldState()` both open
// with `if (REPLAY) return`, and the browser harness only ever runs ?replay=1 —
// so the entire strip has NO runtime coverage anywhere in the gate. That is the
// same blind-spot shape that let the OKX-pong regression ship (group 74's
// header), repeated in a new feature. What CAN be pinned cheaply and exactly is
// the STRUCTURE the feature rests on, read straight out of the shipped markup:
// the first version hard-coded `area-klev` on the strip while the real
// key-levels panel already owned that cell, so whenever the strip stood in for
// auct/lvls but NOT klev the two were superimposed and the strip — earlier in
// DOM order — painted behind. The offline explanation became invisible, which
// is the precise inversion of what it exists to do.
group('layout: one element per grid area, strips place themselves, anchors survive folding', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'terminal.html'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'terminal.css'), 'utf8');

  // ── one element per named grid cell ──
  // Two items in one `grid-area` do not lay out side by side — they STACK, and
  // the later one in DOM order wins. Read from class attributes only, so prose
  // in a comment or a title can never trip this.
  const areaUse = new Map();
  for (const m of html.matchAll(/class="([^"]*)"/g)) {
    for (const cls of m[1].split(/\s+/)) {
      if (/^area-[a-z0-9]+$/.test(cls)) areaUse.set(cls, (areaUse.get(cls) || 0) + 1);
    }
  }
  assert.ok(areaUse.size >= 30, 'sanity: the page really does place its panels by area-* class');
  const shared = [...areaUse.entries()].filter(([, n]) => n > 1).map(([c, n]) => c + '×' + n);
  assert.deepStrictEqual(shared, [],
    'no two elements may carry the same area-* class — same cell means superimposed, not adjacent');

  // Every area-* class in the markup resolves to a real grid-area rule (a typo
  // would silently auto-place the panel at the end of the grid instead).
  for (const cls of areaUse.keys()) {
    assert.ok(new RegExp('\\.' + cls + '\\s*\\{[^}]*grid-area:').test(css),
      cls + ' has a matching grid-area rule in terminal.css');
  }

  // ── the strips claim no cell of their own ──
  // renderLocalOnly writes style.gridArea with the cell of a panel it ACTUALLY
  // folded (that cell is free by construction). With no class the CSS fallback
  // is grid auto-placement, which by definition cannot overlap.
  for (const id of ['local-only-api', 'local-only-econ']) {
    const tag = html.match(new RegExp('<section[^>]*id="' + id + '"[^>]*>'));
    assert.ok(tag, id + ' exists in the markup');
    assert.ok(/class="[^"]*\blocal-only\b/.test(tag[0]), id + ' carries .local-only (hidden until .local-on)');
    assert.ok(!/class="[^"]*\barea-/.test(tag[0]),
      id + ' must claim NO grid area — its placement is decided at paint time');
  }
  assert.ok(!/\.local-only\s*\{[^}]*grid-area:/.test(css),
    'and no grid-area sneaks back in through the .local-only rule');

  // ── folding must never take a navigation anchor off the page ──
  // .folded-local is display:none, and a hidden element has no layout box, so
  // an `href="#sec-…"` link and the ⌘K jump into it land nowhere. The panels
  // renderLocalOnly folds are resolved exactly as it resolves them: the .panel
  // that CONTAINS each view element.
  const sections = html.split(/<section\b/).slice(1).map((p) => {
    const gt = p.indexOf('>');
    return { attrs: p.slice(0, gt), body: p.slice(gt + 1) };
  });
  for (const view of ['view-auction', 'view-levels', 'view-keylevels', 'view-econ']) {
    const sec = sections.find((s) => s.body.indexOf('id="' + view + '"') >= 0);
    assert.ok(sec, view + ' lives inside a <section class="panel">');
    const anchor = /id="(sec-[a-z]+)"/.exec(sec.attrs);
    assert.strictEqual(anchor, null,
      view + "'s panel is foldable, so it must not carry a section-nav anchor id"
      + (anchor ? ' (found ' + anchor[1] + ')' : ''));
  }
  // …and every anchor the section nav links to still exists somewhere.
  const navTargets = [...html.matchAll(/href="#(sec-[a-z]+)"/g)].map((m) => m[1]);
  assert.ok(navTargets.length >= 5, 'sanity: the section nav really does link by hash');
  for (const t of navTargets) {
    assert.ok(new RegExp('id="' + t + '"').test(html), t + ' is a live anchor, not a dead link');
  }
});


group('hmsMs: millisecond event time, and the second-cache cannot skew it', () => {
  // Fixed instant, chosen so every field is unambiguous.
  const t = Date.UTC(2026, 7, 2, 15, 59, 50) + 358;
  assert.strictEqual(V.hmsMs(t), '15:59:50.358');
  assert.strictEqual(V.hms(t), '15:59:50', 'the second-resolution formatter is unchanged');

  // Zero-padding: 7 ms must not render as ".7", which would sort and read wrong.
  assert.strictEqual(V.hmsMs(Date.UTC(2026, 7, 2, 0, 0, 0) + 7), '00:00:00.007');
  assert.strictEqual(V.hmsMs(Date.UTC(2026, 7, 2, 0, 0, 0) + 70), '00:00:00.070');
  assert.strictEqual(V.hmsMs(Date.UTC(2026, 7, 2, 0, 0, 0) + 700), '00:00:00.700');

  // The memoised second prefix is the only piece of state in the formatter.
  // Walk time BACKWARDS and across a second boundary: a stale prefix would
  // stamp a print with the wrong second, i.e. lie about event time.
  const a = Date.UTC(2026, 7, 2, 12, 0, 1) + 5;
  const b = Date.UTC(2026, 7, 2, 12, 0, 0) + 999;
  assert.strictEqual(V.hmsMs(a), '12:00:01.005');
  assert.strictEqual(V.hmsMs(b), '12:00:00.999', 'cache must not carry the previous second');
  assert.strictEqual(V.hmsMs(a), '12:00:01.005', 'and must recover going forward again');

  // Non-finite input stays the em-dash, never "NaN" and never a guessed now().
  assert.strictEqual(V.hmsMs(NaN), '—');
  assert.strictEqual(V.hmsMs(undefined), '—');
});

group('tradeCircles: only real prints, inside the drawn window and ladder', () => {
  const t0 = 1000, tN = 9000, lo = 100, hi = 200;
  const mk = (o) => Object.assign({ ts: 5000, price: 150, qty: 1, notional: 1000, isBuy: true }, o);
  const rows = [
    mk({}),                                   // in
    mk({ ts: 999 }),                          // before the window
    mk({ ts: 9001 }),                         // after the window
    mk({ price: 99 }),                        // below the drawn ladder
    mk({ price: 201 }),                       // above the drawn ladder
    mk({ ts: NaN }), mk({ price: NaN }),      // unusable
    mk({ notional: 0 }),                      // zero size is not a print
    null,
  ];
  const r = S.tradeCircles({ trades: rows, t0, tN, minPx: lo, maxPx: hi });
  assert.strictEqual(r.circles.length, 1, 'exactly the one print that is really on screen');
  assert.strictEqual(r.circles[0].price, 150);
  // Boundaries are INCLUSIVE on all four edges — a print exactly at the edge of
  // the drawn ladder really is on the drawn ladder.
  const edge = S.tradeCircles({
    trades: [mk({ ts: t0, price: lo }), mk({ ts: tN, price: hi })], t0, tN, minPx: lo, maxPx: hi });
  assert.strictEqual(edge.circles.length, 2, 'window and range edges are inclusive');

  // oldestTs reports over ALL supplied rows, so the coverage caption can say how
  // far back the tape reaches even when nothing is currently visible.
  const none = S.tradeCircles({
    trades: [mk({ ts: 10, price: 1e6 })], t0, tN, minPx: lo, maxPx: hi });
  assert.strictEqual(none.circles.length, 0);
  assert.strictEqual(none.oldestTs, 10, 'coverage is reported even with zero visible circles');
  assert.deepStrictEqual(S.tradeCircles({ trades: [], t0, tN, minPx: lo, maxPx: hi }).circles, []);
});

group('tradeCircles: AREA carries notional, and one whale cannot flatten the rest', () => {
  const t0 = 0, tN = 1e6, lo = 1, hi = 1e9;
  const mk = (n, i) => ({ ts: 100 + i, price: 100, qty: 1, notional: n, isBuy: true });
  // 20 prints at 1x and one at 100x. Under p95 normalisation the ordinary
  // prints keep a readable magnitude; under MAX normalisation (the encoding we
  // did not copy) they would collapse to sqrt(1/100) = 0.1 of full radius.
  const rows = [];
  for (let i = 0; i < 20; i++) rows.push(mk(1000, i));
  rows.push(mk(100000, 99));
  const { circles, ref } = S.tradeCircles({ trades: rows, t0, tN, minPx: lo, maxPx: hi });
  assert.strictEqual(circles.length, 21);
  assert.strictEqual(ref, 1000, 'p95 of this set is the ordinary print, not the whale');
  const ordinary = circles.find((c) => c.n === 1000);
  const whale = circles.find((c) => c.n === 100000);
  assert.ok(approx(ordinary.mag, 1, 1e-12), 'an at-p95 print draws at full magnitude');
  assert.ok(approx(whale.mag, 1, 1e-12), 'above p95 CLAMPS — it never grows without bound');
  assert.ok(approx(Math.sqrt(0.1), 0.31622776601, 1e-9), 'sanity for the check below');

  // Area, not radius: a print of 1/4 the notional must draw at 1/2 the radius,
  // so that the AREA ratio is the notional ratio. A radius-linear encoding
  // would give 0.25 here and overstate large prints by the square.
  // Both compared prints sit BELOW p95 — above it, magnitude clamps at 1 by
  // design and no ratio is observable (a 2-element set puts p95 at the smaller
  // of the pair, which is what makes the naive version of this test wrong).
  const many = [mk(400, 0), mk(100, 1)];
  for (let i = 0; i < 100; i++) many.push(mk(10000, 10 + i));
  const two = S.tradeCircles({ trades: many, t0, tN, minPx: lo, maxPx: hi });
  assert.strictEqual(two.ref, 10000, 'p95 sits above both compared prints');
  const big = two.circles.find((c) => c.n === 400);
  const small = two.circles.find((c) => c.n === 100);
  assert.ok(approx(big.mag, 0.2, 1e-12) && approx(small.mag, 0.1, 1e-12), 'sqrt of the ratio');
  assert.ok(approx(small.mag / big.mag, 0.5, 1e-12),
    'quarter the notional is HALF the radius (area ∝ notional)');
  // The encoding we did NOT choose, stated as a live contrast: radius-linear
  // would put this ratio at 0.25 and make the bigger print look 4x, not 2x.
  assert.ok(Math.abs(small.mag / big.mag - 0.25) > 0.2, 'this is not a radius-linear map');

  // Smallest first, so a whale is never painted behind dust.
  for (let i = 1; i < circles.length; i++) {
    assert.ok(circles[i].n >= circles[i - 1].n, 'circles are ordered small → large');
  }
});

group('same-ms sweep marker requires same venue AND side, not just the stamp', () => {
  // The tape is multi-venue. Two prints on DIFFERENT venues inside one
  // millisecond are a coincidence, not one order — marking them as a sweep
  // would be a fabricated causal claim (§0.7 per-source rail). Caught on a
  // fixture screenshot where okx and bybit-spot shared a synthetic-clock ms.
  // This pins the predicate the TapeView row loop applies.
  const sameRun = (a, b) => V.hmsMs(a.ts) === V.hmsMs(b.ts) && a.ex === b.ex && !!a.isBuy === !!b.isBuy;
  const t = 1785687590358;
  const bybitBuyA = { ts: t, ex: 'bybit', isBuy: true, price: 63080 };
  const bybitBuyB = { ts: t, ex: 'bybit', isBuy: true, price: 63081 };
  const okxBuy    = { ts: t, ex: 'okx',   isBuy: true, price: 63080 };
  const bybitSell = { ts: t, ex: 'bybit', isBuy: false, price: 63080 };
  const bybitLate = { ts: t + 1, ex: 'bybit', isBuy: true, price: 63082 };

  assert.ok(sameRun(bybitBuyB, bybitBuyA), 'same venue+side+ms IS one order taking two levels');
  assert.ok(!sameRun(okxBuy, bybitBuyA), 'a different VENUE in the same ms is coincidence, not a sweep');
  assert.ok(!sameRun(bybitSell, bybitBuyA), 'the opposite SIDE cannot be the same aggressor order');
  assert.ok(!sameRun(bybitLate, bybitBuyA), 'one millisecond later is a different event');
});

group('TapeAggregator preserves a sweep: one row per LEVEL taken', () => {
  // The claim the ms timestamp rests on. An aggressor lifting four ask levels
  // inside one 100 ms window must stay four rows sharing one stamp — if the
  // aggregator merged across price, ms resolution would show a single print and
  // the sweep would be invisible in a different way.
  const agg = S.TapeAggregator({ aggWindowMs: 100, size: 100 });
  const t = 1785687590358;
  const px = [63080.0, 63080.1, 63080.2, 63081.5];
  for (const p of px) agg.push({ ts: t, ex: 'bybit', isBuy: true, price: p, qty: 0.5, notional: p * 0.5 });
  const rows = agg.list();
  assert.strictEqual(rows.length, px.length, 'a price change must FLUSH the run, not extend it');
  for (const r of rows) {
    assert.strictEqual(r.ts, t, 'every level of one sweep carries the same event ms');
    assert.strictEqual(r.count, 1, 'no level was folded into another');
  }
  const seen = new Set(rows.map((r) => r.price));
  assert.strictEqual(seen.size, px.length, 'every swept level survives as its own row');

  // And the merge that SHOULD happen still does: same venue+side+price inside
  // the window is one row with count 4, so a 200-print iceberg refill is still
  // one line rather than 200.
  const agg2 = S.TapeAggregator({ aggWindowMs: 100, size: 100 });
  for (let i = 0; i < 4; i++) {
    agg2.push({ ts: t + i * 10, ex: 'bybit', isBuy: true, price: 63080, qty: 0.25, notional: 63080 * 0.25 });
  }
  const r2 = agg2.list();
  assert.strictEqual(r2.length, 1);
  assert.strictEqual(r2[0].count, 4);
  assert.ok(approx(r2[0].qty, 1.0, 1e-9), 'the merged row carries the summed size');
});

// ─── Verdict ─────────────────────────────────────────────────────────────────
if (failures) {
  console.error('\ncheck_terminal: ' + failures + ' group(s) FAILED');
  process.exit(1);
}
console.log('\ncheck_terminal: all groups passed');
