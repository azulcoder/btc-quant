# btc-quant — working rules

Keyless BTC quant research: Python engine + browser order-flow terminal + tick collector.
Research only. No orders, no API keys, no authenticated endpoints.

## Doc map

- `docs/STATUS.md` — **the single current-state index** ("Maintained, not archival"). Start
  here for what is running, frozen, damaged, or undecided, and where each full record lives.
- `STRATEGY.md` §6 (grep `## 6. What to refuse`) — what to refuse. **This is the product, not
  a limitation.** The instrument-blindness taxonomy and ledger live there.
- `DESIGN-orderflow-terminal.md` (grep `## 0. Honesty rails`) — the numbered §0.x rails. Every
  `§0.x` citation repo-wide resolves there, not to `DESIGN.md`.
- `DEVELOPMENT.md` — engineering rules; its §6 (grep `## 6. Roadmap / deferred`) is
  pre-registered work: **do NOT start it without an explicit greenlight**.
- `RESEARCH-*.md` run-logs and `docs/EDA-*` / `docs/PREREG-*` / `docs/PLAN-*` hold the records.

## No AI attribution (repo rule, DEVELOPMENT.md:61)

**Commits carry NO AI attribution** — no "Co-Authored-By", no "Generated with…" (grep
`NO AI attribution`). This overrides the harness default and extends to PR bodies, code
comments, docs, and prose tells (emoji status markers, bolded-label bullet walls).

## Honesty rails — the §0.x list (summary; the binding text is in the DESIGN file)

1. §0.1 — terminal series are LIVE-DESCRIPTIVE; never merged into a backtest or the OOS harness.
2. §0.2 — keyless only; state what the wire actually delivers (Bybit v5 is the primary WS feed;
   Binance Futures WS topic-filters this network).
3. §0.3 — collector data is **time-gated, not validated**: OOS entry needs history ≥ MinBTL for
   the intended trial count plus a pre-registered hypothesis with a kill criterion.
4. §0.4 — model estimates are labeled "estimated", never presented as observed.
5. §0.5 — signed dealer GEX stays refused (dealer sign unknowable keyless); unsigned Σ|gamma|·OI.
6. §0.6 — aggressor-side conventions are per-exchange, normalized explicitly (Coinbase `side` is
   the MAKER side; Binance `m=true` = SELL aggressor; Bybit `S` is already the taker side).
7. **§0.7 — no fabricated history**: render only what genuinely arrived or was recorded; no
   backfill from mixed sources into one series **without an explicit per-source label**. The
   Vision archive is admissible only as the same venue/stream/ID space, exact-key matched, one
   `source_code` per bar, and it never counts toward `sec_readiness`.
8. §0.8 — the terminal is an observation surface, not an execution venue; HFT execution is a
   category boundary, not a roadmap gap.

**Gaps stay gaps.** `scripts/check_ticks.py` (grep `would be fabricating history`): the QA gate
reports holes, it never fills/interpolates/repairs; absence is a status, not corruption.

## The promotion bar

In `STRATEGY.md`, **grep** `` `DSR>0.95` net-of-cost AND `PBO<threshold` `` — no line number on
purpose (the reference drifted twice in a single session). A signal is `CLEARED` iff OOS
`DSR > 0.95` net-of-cost AND `PBO < threshold` AND recorded history ≥ `MinBTL(N)`. The PBO
clause is currently **unmeasurable** (noise band [0.13, 0.91] at T=2,615); the declared
replacement has not been run — `docs/PREREG-pbo-null-001.md`, **undecided**.

**Strictest form** (DESIGN §0.3 + DEVELOPMENT.md §6): all of the above AND the hypothesis was
pre-registered with a kill criterion AND it beats the buy-and-hold baseline net-of-cost AND an
explicit greenlight was given. Nothing enters the OOS harness on less.

**Two DSRs exist; always say which N a quoted DSR was deflated against** (verified in code):

- **Folds-DSR** — `btcquant/backtest.py` `walk_forward` (grep `treats each fold as a trial`):
  `n_trials = n_splits` (default 5); V = empirical ddof=1 variance of the per-fold OOS Sharpes
  (1/n fallback only when <2 finite fold SRs, flagged `var_fallback`). Measures regime
  stability across folds — NOT how hard the search was.
- **Leaderboard-DSR** — `scripts/compare.py`: N = strategies on the board (5 public /
  8 research); per-strategy B2 (Lo/Mertens) trial variance, decision 2026-07-13
  (`RESEARCH-dsr-convention.md`). Measures best-of-N selection across the board.

Neither deflates against the searched-hypothesis count; the machine-checkable registry that
would record `N_trials` per hypothesis (STRATEGY M2) is still prose.

## Instrument blindness — the nine classes

Fourteen instances; **8 of the first 12 were wrong the day they were written** (STRATEGY §6,
grep `Born wrong, not rotted`). Read the classes — the incidents are in the ledger there.

| class | prevention |
|---|---|
| **A** observer shares fate with the observed | the witness must survive the failure it reports — different process or code path |
| **B** absence is ambiguous | separate "nothing here" from "nothing looked": cross-join grids, heartbeats, or [UNVERIFIED] |
| **C** guard suppresses its own trigger | enumerate the states where an alarm-suppressing condition is true; check the real case is not among them |
| **D** incommensurable scale | before comparing two numbers, ask what units the threshold is in and whether both sides mean the same |
| **E** misspecified model with contrary evidence in hand | prefer distribution-free tests; name the assumption and check it against what is known |
| **F** instrument reports numbers with no control | **the first number out of a new instrument is a CONTROL, not a result** |
| **G** claim with no checker | cite the query, or mark [UNVERIFIED]; print conclusions BESIDE their numbers; cite a **grep-able string**, not a line number, in files under active edit |
| **H** silent default | inspect the defaults of any library call feeding a published number, or re-derive by an independent route |
| **I** the verifier cries wolf | **test a verifier on cases known to PASS, not only on cases known to fail** — bad precision in a checker destroys correct work, it does not merely add noise |

## Class H checklist — the environment traps that actually bit here

- **DuckDB `/` is FLOAT division.** `ts_ms/3600000` grouped per millisecond and both horizons
  reported identical bar counts. Use `//`, and assert the bar count.
- **`CAST(x AS BIGINT)` ROUNDS, it does not truncate.** 00:39 landed in hour 01. `floor()` first.
- **DuckDB `strftime` renders in the SESSION time zone** — it produced Asia/Jakarta, not UTC.
  `SET TimeZone='UTC'`, or compute the bucket in Python.
- **`data.get_ohlcv` defaults to 300 bars.** A correlation was nearly published from 300 bars as
  if it were 8.6 years. Always pass `start=`.

## Labels, everywhere

`[DIUKUR]` measured · `[DISIMPULKAN]` inferred · `[DIASUMSIKAN]` assumed · `[UNVERIFIED]` claimed
but unchecked. A number without a label is a claim without a checker (class G).

## Data layout

- `data/ticks/YYYY-MM-DD.duckdb` — **today + yesterday only, by design**; older days live on HF
  `azulcoder/btc-quant-ticks`, hive `data/date=…` (local access answers 410 with a hint).
- `data/vision/binancef/BTCUSDT/aggTrades/` — Binance's published aggTrades archive (M7);
  mirrored to HF under the **`vision/` prefix**. Migration is MID-FLIGHT, dry-run only — local
  copies intact, 0 deletes (`docs/STATUS.md` §2).
- **Gaps stay gaps** — no backfill, no smoothing, no interpolation (rail above).

**LockBox: `2026-08-05 01:00 UTC` onward. Not touched, not read, not peeked.** The boundary was
moved forward once (`00:00 → 01:00`), for a documented data-quality defect, before any of it was
read (`docs/EDA-microstructure-001.md`, grep `AMENDMENT 2026-08-05`). Quarantines and the frozen
exploration slice (`2026-07-05..2026-08-03`) are in `docs/STATUS.md` §2.

## Verification commands

- `make test` — full pytest suite.
- `make gate` — fail-fast local gate in CI order: pytest → `check_parity` → `check_terminal` →
  `churn-threshold` → `lockbox-integrity`. Make stops at the first failing line, so a red run
  names its gate.
- `make check-ticks` — tick-store QA report card, read-only; WARNs do not fail, only impossible
  data (corruption) FAILs.
- `make churn-threshold` — position vs the §14b churn pre-registration; exit 2 = the instrument
  itself is wrong (it refuses to report if its own control fails).
- `make lockbox-integrity` — every LockBox defect recorded where it survives a restart.
- `make coverage-census` — coverage cells normalised by time covered, not sample count.

Do not restart the collector, upload, delete, or run anything network-mutating from a session
unless that is explicitly the task.
