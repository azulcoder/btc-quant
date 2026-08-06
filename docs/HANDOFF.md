# HANDOFF — generated, do not hand-edit

**Generated 2026-08-06T06:23:31Z by `make handoff`.** Every field below is read from a source; an
unreadable source says `UNKNOWN` rather than carrying a stale value forward. Hand-editing
this file defeats its purpose — regenerate it instead (`make gate` does so automatically).

## Commit

- `d114fe0` — Single-run lock for the migration, after a measured concurrency race the design survived
- authored 2026-08-06T12:45:22+07:00 · working tree **dirty** · unpushed commits: 0
- public repo: <https://github.com/azulcoder/btc-quant> — a web session can fetch it directly

## State [all values generated this run]

| field | value |
|---|---|
| collector `/health` | legs 16/16 · writer running · rows_dropped_error 0 · uptime 3.4 h |
| last GREEN gate | GREEN at 2026-08-06T04:26:13Z on cf77e9e (an EARLIER commit) · UNKNOWN |
| look counter (owner: `docs/EDA-microstructure-001.md`) | 557 diagnostic / 81 predictive |
| vision partitions still local | 390 |
| migration states | {'readback_ok': 128, 'upload_failed': 9, 'deleted': 1694} |
| migration last record | 2021-02-08 -> deleted @ 2026-08-06T06:23:25.915Z |
| disk free | 24.0 GB |
| recorded damage (date, prints) | 2026-08-03 28,428 · 2026-08-04 21,706 · 2026-08-05 1,744 |

**Who may raise the look counter:** only a session that actually ran the look, in the same
commit that records it. A variant proposed in a web session and later scored here is a look
that must be counted — if it reaches the repo without a counter increase, the counter is
wrong and every DSR deflated against it inherits the error.

## Open decisions (generated from `docs/STATUS.md`)

1. **Vision migration real run**: approve ~25 partitions/commit (HF throttles at ~125–130 commits/window, measured) + network-timeout retry; then delete-only over the verified 128 as the delete-path proof. Design: `DESIGN-vision-remote-first.md` §24.
2. **Collector log out of `/tmp`** (wiped on every reboot — it just happened): change `StandardOutPath`/`StandardErrorPath` in `com.btcquant.collector.plist` to a repo path and kickstart once. Cheap; protects the forensic record the LockBox gate depends on.
3. **Cross-process aggTrades cursor**: seed from `max(trade_id)` in the day file at startup, so process restarts (reboots) stop losing the down-window backlog. Same class as `dc9857b`.
4. **PBO bar**: (c) calibrated-null as declared vs (a) drop the clause; ABSTAIN semantics.
5. **296-day backfill** via `--date` path (after local migration; `2025-10-08` needs its `trades.parquet.bad` partial artifact cleared as part of that day's re-ingest).
6. **C2 feasibility map** (breadth × drag, criteria declared before looking).
7. **disablesleep experiment**: locked behind the 36 h churn threshold; needs a fresh baseline.
8. **`make check-vision` is locally infeasible at full scale** (audit-measured: dedup over 2.83 B rows needs ~160 GB of aggregate state vs 14 GB free — needs a per-month window flag and an explicit `temp_directory`).
9. Older infra items: combined `make gate`, rail-review agent, `.claude/settings.json`, full CLAUDE.md (current one is a stub), `PREREG-scalp-001.md`.

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

