# STATUS — the single current-state index

**Maintained, not archival: this file says where everything stands NOW and where its full
record lives.** Superseded facts get struck through with a pointer, never silently removed.
Last update: **2026-08-06, post-buildout + anti-rot gate** (19-agent adversarial audit: 12 findings confirmed, all fixed; machine rebooted 05:23 local — collector self-recovered via launchd).

---

## 1. Running systems

| system | state | check with | record |
|---|---|---|---|
| collector (16 legs, launchd `com.btcquant.collector`) | **healthy**; `_stamped` + aggTrades cursor fix active. **Host rebooted 2026-08-06 05:23 local; launchd restarted the collector cleanly.** KNOWN RESIDUAL: the cursor dict is per-process, so the ~10 min reboot window's aggTrades backlog is lost (will surface in tomorrow's overlap gate on `2026-08-05`) — cross-process seeding from the day file's max id is the open fix | `curl 127.0.0.1:8788/health` | `dc9857b` |
| tick lifecycle (HF sync, 07:20 WIB) | bounded 2-day local store, ~490 MB steady state | `ls data/ticks/` | `scripts/upload_hf.py` |
| LockBox integrity | **PASS** — 1 recorded defect (depth row, ENOSPC). The reboot wiped `/tmp` (the stamped log), so the gate now distinguishes entry-predates-log (manifest = surviving record) from phantom. **`/tmp` log location is a standing risk — relocation recommended** | `make lockbox-integrity` | `reports/lockbox-manifest.json` |
| churn vs §14b pre-registration | **clock reset** — churn returned `2026-08-05 18:21:40Z`; ambiguous band | `make churn-threshold` | EDA §14b |
| cursor-fix production evidence (b-ii/b-iii) | **0 events** — needs a natural `binancef-aggTrades` leg restart | `grep -E "resuming at id\|exceeds the\|id GAP" ~/Library/Logs/btcquant-collector.log` | EDA §10 |

## 2. Data

| store | state |
|---|---|
| `data/ticks/` | today + yesterday only, by design; older days on HF `azulcoder/btc-quant-ticks/data/date=*` |
| recorded damage | **3 entries**: `2026-08-03` 28,428 (4 blocks) · `2026-08-04` 21,706 (2 blocks, pre-fix) · `2026-08-05` **1,744 (reboot window 22:23–22:29Z, PRE-EMPTIVE — archive publishes ~07:00Z and the gate will verify the exact set)**. PENDING: `2026-08-06` will carry a ~4 min bootout hole (~02:52–02:56Z, log-relocation restart) — measure and record once the day closes. Outages <~90 s lose nothing (seed poll covers the last 1,000 ids — measured) |
| `data/vision/` (archive tape) | **2,084 local partitions** `2019-12-31..2026-08-01`, **296-day hole [count corrected by audit 2026-08-06]** `2025-10-08..2026-07-30` (ENOSPC casualty), `2026-07-30` migrated & deleted locally |
| Vision→HF migration | **REAL RUN IN FLIGHT**, single-run PID-locked after a measured concurrency race (`DESIGN-vision-remote-first.md` §25 — no data lost; every delete carries all three verifications). Live counts: run `make handoff`. Checkpoint `reports/vision-migration.jsonl` |
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

1. **Vision migration real run**: approve ~25 partitions/commit (HF throttles at ~125–130
   commits/window, measured) + network-timeout retry; then delete-only over the verified 128
   as the delete-path proof. Design: `DESIGN-vision-remote-first.md` §24.
2. **Collector log out of `/tmp`** (wiped on every reboot — it just happened): change
   `StandardOutPath`/`StandardErrorPath` in `com.btcquant.collector.plist` to a repo path and
   kickstart once. Cheap; protects the forensic record the LockBox gate depends on.
3. **Cross-process aggTrades cursor**: seed from `max(trade_id)` in the day file at startup, so
   process restarts (reboots) stop losing the down-window backlog. Same class as `dc9857b`.
4. **PBO bar**: (c) calibrated-null as declared vs (a) drop the clause; ABSTAIN semantics.
5. **296-day backfill** via `--date` path (after local migration; `2025-10-08` needs its
   `trades.parquet.bad` partial artifact cleared as part of that day's re-ingest).
6. **C2 feasibility map** (breadth × drag, criteria declared before looking).
7. **disablesleep experiment**: locked behind the 36 h churn threshold; needs a fresh baseline.
8. **`make check-vision` is locally infeasible at full scale** (audit-measured: dedup over 2.83 B
   rows needs ~160 GB of aggregate state vs 14 GB free — needs a per-month window flag and an
   explicit `temp_directory`).
9. Older infra items: combined `make gate`, rail-review agent, `.claude/settings.json`, full
   CLAUDE.md (current one is a stub), `PREREG-scalp-001.md`.

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

## 6. Doc map (what lives where)

| doc | holds |
|---|---|
| `docs/EDA-microstructure-001.md` | the measurement record: §0–§19 + look counter — missingness, cost, EV gate, re-scores, PBO, clustering, tape loss vs venue archive, inventories, liveness, taxonomy instances |
| `docs/EDA-execution-001.md` | maker viability gate §E0 (execution overlay research) |
| `docs/PLAN-derivative-001.md` | derivative candidates + the cost-drag gate procedure |
| `docs/PRECHECK-cvd-turnover.md` | C1 precheck + anchor correction |
| `docs/PREREG-pbo-null-001.md` | declared-not-run PBO replacement |
| `docs/DESIGN-vision-remote-first.md` | migration design §17–§24: nondeterminism claims table, checkpoint/caffeinate rules, batch results |
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
