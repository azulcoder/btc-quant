# btc-quant — working rules

Keyless BTC quant research: Python engine + browser order-flow terminal + tick collector.
Research only. No orders, no API keys, no authenticated endpoints.

> **This file is a STUB.** The full version planned earlier — every rail distilled with a
> `file:line` citation, ≤150 lines — has not been written. What is here is the one thing that
> most needs to load in every session regardless of launch directory. Do not read the absence of
> a rail here as the absence of the rail.

## Read these first

- `STRATEGY.md` §6 — what to refuse. **This is the product, not a limitation.**
- `DESIGN-orderflow-terminal.md:27-121` — the numbered §0.x rails. Every `§0.x` citation
  repo-wide resolves there, not to `DESIGN.md`.
- `DEVELOPMENT.md:61` — **commits carry NO AI attribution.** This overrides the harness default.
- The promotion bar — in `STRATEGY.md`, **grep** `` `DSR>0.95` net-of-cost AND `PBO<threshold` ``.
  **No line number is given on purpose:** that reference drifted twice in a single session while
  this file was being written (§17). A signal is `CLEARED` iff OOS `DSR > 0.95` net-of-cost AND
  `PBO < threshold` AND history ≥ `MinBTL(N)`. The PBO clause is currently **unmeasurable** —
  `docs/PREREG-pbo-null-001.md`, undecided.

## Instrument blindness — the nine classes

Fourteen instances, **8 of the first 13 wrong the day they were written**. Read the classes — the incidents are in `STRATEGY.md`.

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

- **DuckDB `/` is FLOAT division.** `ts_ms/3600000` grouped per millisecond and both horizons reported identical bar counts. Use `//`, and assert the bar count.
- **`CAST(x AS BIGINT)` ROUNDS, it does not truncate.** 00:39 landed in hour 01. `floor()` first.
- **DuckDB `strftime` renders in the SESSION time zone** — it produced Asia/Jakarta, not UTC. `SET TimeZone='UTC'`, or compute the bucket in Python.
- **`data.get_ohlcv` defaults to 300 bars.** A correlation was nearly published from 300 bars as if it were 8.6 years. Always pass `start=`.

## Labels, everywhere

`[DIUKUR]` measured · `[DISIMPULKAN]` inferred · `[DIASUMSIKAN]` assumed · `[UNVERIFIED]` claimed
but unchecked. A number without a label is a claim without a checker (class G).

## Data

Local `data/ticks/YYYY-MM-DD.duckdb` (today + yesterday; older → HF, 410 with a hint).
Mirror `azulcoder/btc-quant-ticks`, hive `data/date=…`. Archive
`data/vision/binancef/BTCUSDT/aggTrades/`. **Gaps stay gaps** — no backfill, no smoothing, no
interpolation (`scripts/check_ticks.py:12-16`).

**LockBox: `2026-08-05 01:00 UTC` onward. Not touched, not read, not peeked.** The boundary was
moved forward once, for a documented data-quality defect, before any of it was read
(`docs/EDA-microstructure-001.md`, LockBox amendment).
