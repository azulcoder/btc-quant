# HANDOFF — generated, do not hand-edit

**Generated 2026-08-06T12:40:31Z by `make handoff`.** Every field below is read from a source; an
unreadable source says `UNKNOWN` rather than carrying a stale value forward. Hand-editing
this file defeats its purpose — regenerate it instead (`make gate` does so automatically).

## Commit

- `e432b14` — Implement the verified Hasbrouck estimators; verify the 2026-08-05 damage entry
- authored 2026-08-06T15:32:45+07:00 · working tree **dirty** · unpushed commits: 4
- public repo: <https://github.com/azulcoder/btc-quant> — a web session can fetch it directly

## State [all values generated this run]

| field | value |
|---|---|
| collector `/health` | legs 16/16 · writer running · rows_dropped_error 0 · uptime 9.7 h |
| last GREEN gate | GREEN at 2026-08-06T04:26:13Z on cf77e9e (an EARLIER commit) · UNKNOWN |
| look counter (owner: `docs/EDA-microstructure-001.md`) | 557 diagnostic / 81 predictive |
| vision partitions still local | 0 |
| migration states | {'readback_ok': 128, 'upload_failed': 9, 'deleted': 2084} |
| migration last record | 2019-12-31 -> deleted @ 2026-08-06T06:57:33.556Z |
| disk free | 23.5 GB |
| recorded damage (date, prints) | 2026-08-03 28,428 · 2026-08-04 21,706 · 2026-08-05 1,744 |

**Who may raise the look counter:** only a session that actually ran the look, in the same
commit that records it. A variant proposed in a web session and later scored here is a look
that must be counted — if it reaches the repo without a counter increase, the counter is
wrong and every DSR deflated against it inherits the error.

## Open decisions (generated from `docs/STATUS.md`)

1. **`pytest -q` hides skips in the gate and in CI** — add `-rs` (and consider failing on an unexpected skip count). §5c C measured six silent skips covering the whole venue fidelity + completeness gate. One flag; highest value per character in the repo.
2. **979 duplicate rows in `trades`, found 2026-08-06 (§5c-bis).** No unique constraint on `(exchange, symbol, trade_id)` and the aggTrades dedup guard is in-memory only, so a reconnect that replays ids writes them twice. Per-trade statistics over an affected day
3. **Five ledger entries carry a wrong terminal state** (`upload_failed` on partitions that migrated successfully — §5c B). The ledger is append-only, so the correction is an appended corrective record, never a rewrite. Shape of that record is azul's call.
4. **296-day backfill** via the `--date` path — **now unblocked** (the local migration is done). `2025-10-08` still holds a `trades.parquet.bad` partial artifact that must be cleared as part of that day's re-ingest. Do it chunked, not as a naive `--date` loop.
5. **`2026-08-06` bootout damage entry**: a ~4 min hole (~02:52–02:56Z, log-relocation restart) is expected; measure and record it once the day closes.
6. **disablesleep experiment**: locked behind the 36 h churn threshold; needs a fresh baseline.
7. **`make check-vision` is locally infeasible at full scale** (audit-measured: dedup over 2.83 B rows needs ~160 GB of aggregate state — needs a per-month window flag and an explicit `temp_directory`). Now more pressing, since the local copies it used to read ar
8. **Hasbrouck estimators — the plan is now written**: `docs/PLAN-microstructure-001.md`. Headline finding, measured: the Roll family needs **~147 days** of aggTrades before `sd(gamma_1)` falls to 20 % of the signal; at half a day the noise is 3.6x the signal. Th

## Binding rules for ANY agent, in any surface

- **LockBox `2026-08-05 01:00 UTC` onward is never read, queried, or peeked.** It is an
  evaluate-once slice; a single look destroys its only property. The boundary was moved
  forward once (`00:00 → 01:00`) for a documented data defect, before any byte was read.
- **Exploration slice is FROZEN** at `2026-07-05 … 2026-08-03`. Newly collected data goes
  to the LockBox, so no 30-day table can grow — `N` will not increase by waiting.
- **Declare before running.** Thresholds, criteria and the interpretation of every outcome
  are written down before the number exists, in the doc that will hold the result.
- **A new instrument's first number is a CONTROL**, not a result — it must reproduce a known
  value by an independent route. Anchors pin to CLOSED data (a live partial bar broke one).
- **A verifier is tested on cases known to PASS**, not only on known failures.
- **Conclusions print BESIDE the numbers that produced them.**
- **Cite grep-able strings, never `file:line`** — enforced by `scripts/doc_freshness.py`.
- **Label every number**: `[DIUKUR]` measured · `[DISIMPULKAN]` inferred · `[DIASUMSIKAN]`
  assumed · `[UNVERIFIED]` claimed but unchecked.
- **No AI attribution** in commits, PRs, code comments, or prose.
- **Gaps stay gaps** — no backfill, smoothing, or interpolation into a recorded series.

## What a session WITHOUT this machine must not assume

These exist only on the collector host and cannot be inferred from the repo:

- the live collector and its `/health` (leg states, dropped-row counters, uptime)
- the two local day files (today + yesterday); everything older lives on Hugging Face
- the stamped collector log (`~/Library/Logs/`), which is the forensic record
- free disk, and anything about the migration's live progress
- whether a background job is still running

If a decision depends on any of those, it cannot be settled off-machine — ask for a
measurement, do not estimate one. **Anything decided elsewhere must return as a commit,
or it is not durable.**

## Where the records live (no line numbers by design)

| document | holds |
|---|---|
| `docs/STATUS.md` | the current-state index — start here |
| `docs/EDA-microstructure-001.md` | the measurement record and the look counter (its owner) |
| `docs/EDA-execution-001.md` | maker viability gate for the execution-overlay track |
| `docs/PLAN-derivative-001.md` | derivative candidates + the cost-drag gate procedure |
| `docs/PRECHECK-cvd-turnover.md` | the CVD turnover precheck and its anchor correction |
| `docs/PREREG-pbo-null-001.md` | the PBO replacement: declared, run, verdict inside |
| `docs/PREREG-scalp-001.md` | scalping pre-registration; the premise is rejected arithmetically |
| `docs/DESIGN-vision-remote-first.md` | the archive migration design and its measurements |
| `STRATEGY.md` | the refusals, the blindness ledger, and the taxonomy |
| `CLAUDE.md` | working rules loaded every session |
| `reports/*.json[l]` | machine-checked damage, LockBox defects, migration checkpoint |

