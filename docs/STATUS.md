# STATUS — the single current-state index

**Maintained, not archival: this file says where everything stands NOW and where its full
record lives.** Superseded facts get struck through with a pointer, never silently removed.
Last update: **2026-08-06, post-buildout + anti-rot gate** (19-agent adversarial audit: 12 findings confirmed, all fixed; machine rebooted 05:23 local — collector self-recovered via launchd).

---

## 1. Running systems

| system | state | check with | record |
|---|---|---|---|
| collector (16 legs, launchd `com.btcquant.collector`) | **healthy**; `_stamped` + aggTrades cursor fix active. **Host rebooted 2026-08-06 05:23 local; launchd restarted the collector cleanly.** RESIDUAL CLOSED: the ~10 min reboot window's aggTrades backlog was lost and the overlap gate has now measured it exactly — 1,744 prints, matching the pre-emptive count to the print. Cross-process seeding via the cursor sidecar is live (`425877d`) | `curl 127.0.0.1:8788/health` | `dc9857b` |
| tick lifecycle (HF sync, 07:20 WIB) | bounded 2-day local store, ~490 MB steady state | `ls data/ticks/` | `scripts/upload_hf.py` |
| LockBox integrity | **PASS** — 1 recorded defect (depth row, ENOSPC). The reboot wiped `/tmp` (the stamped log), so the gate now distinguishes entry-predates-log (manifest = surviving record) from phantom. **`/tmp` log location is a standing risk — relocation recommended** | `make lockbox-integrity` | `reports/lockbox-manifest.json` |
| churn vs §14b pre-registration | **clock reset** — churn returned `2026-08-05 18:21:40Z`; ambiguous band | `make churn-threshold` | EDA §14b |
| cursor-fix production evidence (b-ii/b-iii) | **0 events** — needs a natural `binancef-aggTrades` leg restart | `grep -E "resuming at id\|exceeds the\|id GAP" ~/Library/Logs/btcquant-collector.log` | EDA §10 |

## 2. Data

| store | state |
|---|---|
| `data/ticks/` | today + yesterday only, by design; older days on HF `azulcoder/btc-quant-ticks/data/date=*` |
| recorded damage | **3 entries**: `2026-08-03` 28,428 (4 blocks) · `2026-08-04` 21,706 (2 blocks, pre-fix) · `2026-08-05` **1,744 (reboot window 22:23–22:29Z) — VERIFIED 2026-08-06T08:30Z against the venue archive: `archive \\ recorded = 1,744`, `recorded \\ archive = 0`, exact. `[DIUKUR]`, entry carries `verified: true`**. PENDING: `2026-08-06` will carry a ~4 min bootout hole (~02:52–02:56Z, log-relocation restart) — measure and record once the day closes. Outages <~90 s lose nothing (seed poll covers the last 1,000 ids — measured) |
| `data/vision/` (archive tape) | **remote-first as of 2026-08-06**: 0 partitions hold a local `trades.parquet`; every one lives on HF under the `vision/` prefix, with **2,085 local manifests retained** as the local proof of content. Span `2019-12-31..2026-08-01` with a **296-day hole [count corrected by audit 2026-08-06]** `2025-10-08..2026-07-30` (ENOSPC casualty) — the hole is unchanged by the migration and is still open |
| Vision→HF migration | **COMPLETE 2026-08-06T06:57Z**. Final batch 713/713, 0 throttle events, exit 0, lock released cleanly. Totals: **2,084 deletes, every one carrying `transport_ok` + `content_ok` + `manifest_ok`**; 0 of 2,239 ledger lines unparseable despite a two-writer window. Terminal states read `deleted 2,079 / upload_failed 5` — those 5 are the mislabelled race casualties (§5c B), not failures; their `deleted` records are intact and verified. Checkpoint `reports/vision-migration.jsonl` |
| **LockBox** | **`2026-08-05 01:00Z` onward — NOT read, NOT queried, ever.** Boundary moved once (00:00→01:00) for a documented defect, before any byte was read. Quarantines: `2026-08-04`, `2026-08-05 00:00–01:00` |
| exploration slice | **FROZEN**: `2026-07-05..2026-08-03`. New collection goes to the LockBox, so every 30-day table (funding/OI/options/crowding/dvol) is stuck at N=30 for exploration |

## 3. Research verdicts (full records in the named docs)

| item | verdict | where |
|---|---|---|
| swing board (14 candidates) | nothing clears DSR 0.95; `tsmom` 0.93 **NOT CLEARED** (N_eff estimators straddle the bar → LockBox queue L7) | EDA §4bis-B, §7; STRATEGY L7 |
| PBO clause of the promotion bar | **RESOLVED 2026-08-06**: replaced by the calibrated-null test, run as declared (3 controls passed, incl. power control PC2). Verdict **ABSTAINS, unanimous 4 arms** — `pbo: INDETERMINATE` on registry entries; P95≈0.91 upper-tail alarm armed | `PREREG-pbo-null-001.md` RESULT; `reports/pbo-null-result.json` |
| maker viability (standalone MM) | **NO, arithmetically** (−3.98 bps/RT at VIP 0; queue ~$445k on a 1-tick book) | `EDA-execution-001.md` §E0 |
| execution overlay | alive — economics = 6 bps/RT fee differential, not spread capture; §E1–E4 awaiting approval | same doc |
| C1 daily CVD | **AMBIGUOUS** (8.66× binary anchor after correction) — not proposed | `PRECHECK-cvd-turnover.md` + PLAN |
| C2 basis / C3 funding | **C2 feasibility map DONE**: structural point (z=2.0/0.5, W=60) **passes both gates** (breadth 18.6 %, drag 0.61× anchor) inside a contiguous window — graduates to a pre-registration draft (no returns scored). C3 conditioning-only | `PLAN-derivative-001.md` C2 RESULT |
| C4 OI quadrants | **FROZEN PERMANENTLY** (endpoint serves 30 rolling days, probed) | same |
| options orthogonality | **CANNOT DECIDE** (N=30 frozen, CI ±0.36) — DEFERRED, no date; route = check for a keyless historical chain | EDA §19 |
| microstructure PREREG-001 (trades-only spread vs book) | **CLOSED 2026-08-07 — INDETERMINATE, and the MA(1) premise is REFUTED by the data.** Pooled over 23 dates / 13.4 M lag-1 pairs: `rho_1 = -0.7127`, outside the `[-0.5, +0.5]` any MA(1) can produce, and `sigma2_w` came out NEGATIVE. Per-day `rho_2` is +0.31 to +0.52 where MA(1) needs 0; the order gate rejects every day tested. Kill criterion §7.2 is met: no cost model may be built from the Roll family on this instrument until the cause is known | `docs/PREREG-microstructure-001.md` RESULT; `reports/prereg-microstructure-001-result.json` |
| scalping PREREG (1–30 s, 1:1) | **WRITTEN** (`docs/PREREG-scalp-001.md`, 315 lines): premise fails arithmetically in all three execution models (p* 118–222 % at 30 s); rejection registered as the deliverable; one reformulation declared-not-activated (5-min bars, R=3, p* 51.1 %, N_trials cap 3) | `docs/PREREG-scalp-001.md` |

**Look counter — the value lives in `docs/EDA-microstructure-001.md` and nowhere else** (a second
copy went stale here once; `scripts/doc_freshness.py` A2 now enforces single ownership). That
table also carries the audit's reconciliation row for the pre-squash offset.

## 4. Standing rails added this session (all in `STRATEGY.md` §6 + ledger)

tie-break (verdict flipping on a free methodological choice = NOT CLEARED) · new instrument's
first number is a CONTROL · conclusions print BESIDE their numbers · empirical prose cites its
query or carries [UNVERIFIED] · sparse streams need a liveness witness · verifiers are tested on
known-PASS cases too (class I) · cost-drag gate before any candidate design (anchor 188 bps/yr,
thresholds declared arbitrary). **Instrument-blindness taxonomy: 9 classes, 14 instances** —
classes in `CLAUDE.md` (15 lines), ledger in `STRATEGY.md`.

## 5. Open decisions (azul's, in rough priority)

> **Housekeeping note, 2026-08-06.** Six of the nine items previously listed here were already
> done and were still being reported as open — and `make handoff` copies this list verbatim into
> the brief a web session reads, so the staleness propagated off-machine. `doc_freshness.py`
> cannot see this class: a finished item listed as open is not a wrong *number*, so no assert
> fires. Each closure below was re-verified against the artifact, not against memory.

1. **`pytest -q` hides skips in the gate and in CI** — add `-rs` (and consider failing on an
   unexpected skip count). §5c C measured six silent skips covering the whole venue
   fidelity + completeness gate. One flag; highest value per character in the repo.
2. **979 duplicate rows in `trades`, found 2026-08-06 (§5c-bis).** No unique constraint on
   `(exchange, symbol, trade_id)` and the aggTrades dedup guard is in-memory only, so a reconnect
   that replays ids writes them twice. Per-trade statistics over an affected day are inflated
   until deduped. Decide: unique index, or a dedup pass at read time, or both.
2b. **One or more prints whose qty differs from the venue by ~0.01**, price/timestamp/side exact.
   Chasing it means a second, undeclared read of the LockBox slice — **your call, not mine.**
3. **Five ledger entries carry a wrong terminal state** (`upload_failed` on partitions that
   migrated successfully — §5c B). The ledger is append-only, so the correction is an appended
   corrective record, never a rewrite. Shape of that record is azul's call.
4. **296-day backfill** via the `--date` path — **now unblocked** (the local migration is done).
   `2025-10-08` still holds a `trades.parquet.bad` partial artifact that must be cleared as part
   of that day's re-ingest. Do it chunked, not as a naive `--date` loop.
5. **`2026-08-06` bootout damage entry**: a ~4 min hole (~02:52–02:56Z, log-relocation restart)
   is expected; measure and record it once the day closes.
6. **disablesleep experiment**: locked behind the 36 h churn threshold; needs a fresh baseline.
7. **`make check-vision` is locally infeasible at full scale** (audit-measured: dedup over 2.83 B
   rows needs ~160 GB of aggregate state — needs a per-month window flag and an explicit
   `temp_directory`). Now more pressing, since the local copies it used to read are gone.
8. **Three MA(1)-dependent estimators do not call their own order gate** — `roll`,
   `pricing_error_lower_bound` and `identified_interval_c`. Only `sigma2_w_ma1` and
   `sigma2_w_wold` gate. The adversarial review found one instance and it was fixed; the
   pattern turned out to be three, and PREREG-001 is what surfaced it. That run survived only
   because its discriminant happened to go negative. Adding the gate makes those functions
   stricter, so it changes what the instrument reports — **your call.**
9. **Hasbrouck estimators — the plan is now written**: `docs/PLAN-microstructure-001.md`.
   Headline finding, measured: the Roll family needs **~147 days** of aggTrades before
   `sd(gamma_1)` falls to 20 % of the signal; at half a day the noise is 3.6x the signal.
   The archive has 16x that, so it is feasible pooled — and daily subsampling, which the
   extraction docs recommend, is the WRONG unit here. Next step is a PREREG, then a
   positive control against the book-measured 0.0156 bps. **Not on the terminal** — the
   terminal already has the book, and at intraday n the estimator would cry wolf.

**Closed since this list was last edited** (each re-verified 2026-08-06, not assumed):
Vision migration real run — complete, 2,084 verified deletes ·
collector log out of `/tmp` — `StandardOutPath` now `~/Library/Logs/btcquant-collector.log` ·
cross-process aggTrades cursor — sidecar live in `collector.py` (`425877d`) ·
PBO bar — calibrated-null test run as declared, **ABSTAINS** unanimous across 4 arms ·
C2 feasibility map — done, structural point passes both gates ·
infra batch — `make gate`, rail-review agent, `.claude/settings.json`, a real `CLAUDE.md`
(no longer a stub), and `PREREG-scalp-001.md` all exist.

## 5b. Structural anti-rot (added 2026-08-06, after the audit found rot 12 ways)

Rot here was never a vigilance failure, so the mitigation is not vigilance:

| gate | asserts | negative controls |
|---|---|---|
| `make doc-freshness` | **A1** no `file:line` pointers in living docs · **A2** the look counter's value lives in exactly one file · **A3** fast-moving facts live only in this file | 3 violating fixtures + 2 must-PASS fixtures (`tests/test_doc_freshness.py`) |
| `make handoff` | regenerates `docs/HANDOFF.md` from sources; an unreadable source says `UNKNOWN`, never a stale value | runs as the last step of `make gate`, so a green gate implies a fresh handoff |

**First run measured** [2026-08-06]: 69 `file:line` pointers in living docs, of which at least
4 already pointed at blank lines and 5 more had drifted onto braces or comment markers; 2 stale
look-counter copies (this file said 535 while the owner said 557); 10 fast-moving facts outside
this file. All fixed; 59 pointers were converted automatically to symbol anchors verified unique
in their target file, the rest marked `[UNVERIFIED]` rather than given an invented anchor.

**Escapes are explicit and printed on every run** (`[HISTORICAL]`, strikethrough, a same-line
date stamp for A3, archival files, generated artifacts) — an exemption that stops being true
shows up as a number that stops matching, not as silence.

## 5c. Migration forensics A/B/C — READ-ONLY mapping, no code changed (2026-08-06)

Three questions, mapped and not fixed; every repair below is azul's call. Measured
`2026-08-06T05:56Z–06:21Z` **while the migration was running**, so counts drift by construction
and each one names its instant. Full record: `AUDIT_LOG.md` 2026-08-06 entry and
`DESIGN-vision-remote-first.md` §25.

**A — why `upload_failed`: two unrelated populations, and merging them was the reading error.**
Four records (2026-08-05 19:20–19:34Z) are genuine network timeouts (`[Errno 60]` /
`The read operation timed out`); all four were retried and reached `deleted`. Five records
(04:50:57Z) are `[Errno 2] No such file or directory` — the local parquet was deleted by the
concurrent run before this one reached it, so the exception fired **before any network call**,
which is why the log shows `up 0.0s rb 0.0s` and `throttle events: 0`. It is not a commit
conflict, not a rate limit, not auth, not a retry bug. **It cannot recur on a single-writer
relaunch**: the PID lock refuses a second writer, and `FileNotFoundError` is now a distinct
non-fatal state. The error strings live in the ledger's `err` key — reading `error` instead
returns `None` for all nine and nearly produced a false "the quote has no source" finding.

**B — the checkpoint survived two writers; the design, not luck.** At `06:21:34Z`: 1,679 raw
`deleted`, 128 `readback_ok`, 9 `upload_failed`; **zero unparseable lines** (a single-line
`O_APPEND` write is atomic), zero torn records, and **every one of the deletes carries all three
verifications**. 133 dates hold more than one record in exactly three patterns: 124
`readback_ok → deleted` (the two-phase flow), 4 `upload_failed → readback_ok → deleted` (timeouts
that retried), and 5 `deleted → upload_failed` — causally ordered, not corruption. Terminal state
is nevertheless **wrong for those five**: `load_states` is last-line-wins, so five fully-verified
migrations read as failures. Verified independently against the hub: all five present as parquet
and manifest, content matching the local manifest exactly, absent locally. Fix = an appended
corrective record (append-only ledger, never a rewrite) — **not done, azul's call**.

**C — the gate was green because its load-bearing tests SKIPPED, not because they passed.**
`pytest -q -rs` at `05:56Z`: **367 passed, 6 skipped**, and all six are `test_vision_overlap.py` —
the entire fidelity + completeness gate against the venue (never-invents-a-print, every-field-
matches-the-venue, completeness-is-zero-or-exactly-the-documented-damage, seam, L3 QA, archive-side
soundness). `make gate` and CI both run `pytest -q` **without `-rs`**, so the run prints
`......ssss.ss` and a bare count: no test name, no reason, no baseline for how many skips are
normal. The answer to "(a) or (b)" is **(b)**. Separately, the skip message is one string covering
three different worlds (archive not published yet · local day file gone · network dropped
mid-loop) — class B, absence is ambiguous. Measured live: `_pick_day()` returns `''` because the
archive still answers 404 for `2026-08-05` (`2026-08-04` answers 200), i.e. the benign cause —
but the message cannot say so. Note the gate is **not** blind to `data/vision`: `make handoff`,
its last step, reads the partition count into `docs/HANDOFF.md` (measured 439 → 427 in 92 s as
deletes ran). It *reports* that number; it never *gates* on it.

**The extreme case then ran itself, unplanned.** The migration finished at `06:57Z` leaving
**zero** local partitions holding a `trades.parquet` — down from ~2,084. The suite was re-run
immediately after: **367 passed, 6 skipped**, the same six tests with the same reason, byte-identical
to the run taken while ~975 partitions were still local. Losing the entire local archive changed
nothing the gate reports. That converts C from "these tests can skip" into a measured fact:
**a total loss of the local archive is invisible to `make gate`.**

**The pre-emptive `2026-08-05` damage entry rides on this.** Its verification is exactly the six
tests that skip, and it can only run once the archive publishes that day. Nothing schedules it.

### 5c-bis. That window opened, and the gate fired — 2026-08-06T08:30Z [DIUKUR]

The archive published `2026-08-05` and the six tests stopped skipping. Nobody scheduled it; the
suite happened to be run for another reason. Three results, in order of what they cost:

**1. The pre-emptive count is confirmed EXACTLY.** `archive \ recorded = 1,744`,
`recorded \ archive = 0` — the number written before the archive existed, derived from in-day
DISTINCT-id holes, matches the venue's own set difference to the print. `missing_rows` in
`reports/recorded-damage.json` is now **`[DIUKUR]`**, not `[DISIMPULKAN]`, and the entry carries
`verified: true`. On the joined rows: `ts_mismatch 0`, `max|Δprice| 0.0`, `side_mismatch 0`.

**2. NEW — 979 duplicate rows on the recorded side.** `887,614` recorded rows but only `886,635`
distinct `(exchange, symbol, trade_id)`. The `trades` table has **no unique constraint** and the
aggTrades dedup guard is in-memory only, so a reconnect that replays ids writes them again. This
is a RECORDED defect, distinct from tape loss: nothing is missing, something is counted twice.
Any per-trade statistic over this day is inflated by 979 prints until it is deduped.

**3. NEW — at least one print's qty disagrees with the venue by ~0.01.** `max|Δqty| = 0.0099999…`
while price, timestamp and side all match exactly. Float noise would be ~1e-15, so this is a real
quantity difference, not representation. **Not investigated further** — see the boundary note.

**A boundary question that is azul's, not mine.** `2026-08-05` is inside the LockBox
(`01:00Z` onward). The declared gate reading it is one thing — it is a fidelity check against the
venue, it reports row counts and field mismatches, and it extracts nothing about returns. Writing
a fresh ad-hoc query to chase the Δqty would be a second, undeclared read of that slice, so it
has not been done. The `Δqty` cause is therefore **UNKNOWN and stays UNKNOWN** until that call
is made.

## 6. Doc map (what lives where)

| doc | holds |
|---|---|
| `docs/EDA-microstructure-001.md` | the measurement record: §0–§19 + look counter — missingness, cost, EV gate, re-scores, PBO, clustering, tape loss vs venue archive, inventories, liveness, taxonomy instances |
| `docs/EDA-execution-001.md` | maker viability gate §E0 (execution overlay research) |
| `docs/PLAN-derivative-001.md` | derivative candidates + the cost-drag gate procedure |
| `docs/PRECHECK-cvd-turnover.md` | C1 precheck + anchor correction |
| `docs/PREREG-pbo-null-001.md` | declared-not-run PBO replacement |
| `docs/EXTRACT-hasbrouck-001.md` | microstructure estimators E1–E7 extracted from a copyrighted source (equations restated, section-number citations, PDF never committed) |
| `docs/EXTRACT-hasbrouck-s9-s12.md` | inference (§9) + the VAR/IRF/Cholesky machinery (§12), and its own amendments to the above |
| `docs/PLAN-microstructure-001.md` | what the estimators are FOR, the sample size they need, why they stay off the terminal, and the order to build in |
| `docs/VERIFY-hasbrouck-extraction.md` | independent replication of both — what held, what was corrected, and what has no checker on this machine |
| `docs/DESIGN-vision-remote-first.md` | migration design §17–§25: nondeterminism claims table, checkpoint/caffeinate rules, batch results, the concurrency race and the lock that closes it |
| `docs/STATUS.md` | this file |
| `docs/HANDOFF.md` | GENERATED — the self-contained brief for a session without this machine |
| `reports/recorded-damage.json` / `lockbox-manifest.json` / `vision-migration.jsonl` | machine-checked damage, LockBox defects, migration checkpoint |
| `CLAUDE.md` (repo root, STUB) | 9 blindness classes, class-H trap checklist, labels, LockBox pointer |
| `STRATEGY.md` §6 | refusals + ledger + taxonomy; the promotion bar (grep `` `DSR>0.95` net-of-cost AND `PBO<threshold` ``) |
| `reports/incident-2026-08-04-sleep/` | prediction.md still binding; baseline EXPIRED |

## 7. Known technical traps (details in `CLAUDE.md` class-H checklist)

DuckDB `/` is float division · `CAST AS BIGINT` rounds · `strftime` renders in session TZ ·
`get_ohlcv` defaults to 300 bars · parquet normalisation is NONDETERMINISTIC (sha256 = transport
+ bit-rot proof only; content proof = rows/id stats) · HF commit budget ~125–130/short window ·
`hf://` ZSTD read failures are transient (retry, don't re-upload).
