# STATUS — the single current-state index

**Maintained, not archival: this file says where everything stands NOW and where its full
record lives.** Superseded facts get struck through with a pointer, never silently removed.
Last update: **2026-08-08, tape backup live (append-only GCS, write-only identity) + venue data-ceiling census + PREREG-markout-001 closed GAGAL** (previous stamp: 2026-08-06 post-buildout audit).

---

## 1. Running systems

| system | state | check with | record |
|---|---|---|---|
| collector (16 legs, launchd `com.btcquant.collector`) | **healthy**; `_stamped` + aggTrades cursor fix active. **Host rebooted 2026-08-06 05:23 local; launchd restarted the collector cleanly.** RESIDUAL CLOSED: the ~10 min reboot window's aggTrades backlog was lost and the overlap gate has now measured it exactly — 1,744 prints, matching the pre-emptive count to the print. Cross-process seeding via the cursor sidecar is live (`425877d`) | `curl 127.0.0.1:8788/health` | `dc9857b` |
| tick lifecycle (HF sync, 07:20 WIB) | bounded 2-day local store, ~490 MB steady state | `ls data/ticks/` | `scripts/upload_hf.py` |
| LockBox integrity | **PASS** — 1 recorded defect (depth row, ENOSPC). The reboot wiped `/tmp` (the stamped log), so the gate now distinguishes entry-predates-log (manifest = surviving record) from phantom. **`/tmp` log location is a standing risk — relocation recommended** | `make lockbox-integrity` | `reports/lockbox-manifest.json` |
| churn vs §14b pre-registration | **clock reset** — churn returned `2026-08-05 18:21:40Z`; ambiguous band | `make churn-threshold` | EDA §14b |
| cursor-fix production evidence | **LEG-LEVEL: CONFIRMED 2026-08-06T01:21:29Z** — `binancef-aggTrades: resuming at id 3403202817 (5,031 id(s) of backlog to catch up)`. That is 5x beyond the 1,000-id seed-poll window, and the day it happened lost **zero** prints against the venue archive. **CROSS-PROCESS SIDECAR: still 0 events** — the file exists and is written, but there has been exactly one collector start since it was added, so it has never been exercised ACROSS a restart | `grep -E "resuming at id\|exceeds the\|id GAP" ~/Library/Logs/btcquant-collector.log` | EDA §10 |

## 2. Data

| store | state |
|---|---|
| `data/ticks/` | today + yesterday only, by design; older days on HF `azulcoder/btc-quant-ticks/data/date=*` |
| recorded damage | **3 entries**: `2026-08-03` 28,428 (4 blocks) · `2026-08-04` 21,706 (2 blocks, pre-fix) · `2026-08-05` **1,744 (reboot window 22:23–22:29Z) — VERIFIED 2026-08-06T08:30Z against the venue archive: `archive \\ recorded = 1,744`, `recorded \\ archive = 0`, exact. `[DIUKUR]`, entry carries `verified: true`**. **`2026-08-06`: NO ENTRY — the predicted hole did not happen.** A ~4 min bootout hole was expected; measured against the venue archive on 2026-08-07 the day lost **zero** trade prints (`archive \\ recorded = 0`, `recorded \\ archive = 0`, `ts_mismatch 0`, `max|Δprice| 0.0`, `max|Δqty| 0.0`, `side_mismatch 0`). The prediction was wrong and is recorded as wrong. **Not a clean bill for the day**: the same window cost `binancef` **928 s of `depth_snapshots`** and comparable spans on bybit/okx/coinbase — real loss on legs the trade-overlap gate does not cover, and there is no archive to measure those against. Outages <~90 s lose nothing (seed poll covers the last 1,000 ids — measured) |
| `data/vision/` (archive tape) | **remote-first as of 2026-08-06**: 0 partitions hold a local `trades.parquet`; every one lives on HF under the `vision/` prefix. Census 2026-08-08: **2,111 days** on HF, span `2019-12-31..2026-08-01`. The ENOSPC hole is now **295 days** `2025-10-08..2026-07-29` (`2026-07-30` was ingested as the provenance identity-control day) — still open, still a gap. Separately, the markout item-1 census found **26 scattered published-but-never-ingested days** (2020/2022/2023 — a silent skip of the original migration, recorded nowhere until a cross-join made absence enumerable); **all 26 backfilled 2026-08-08** via `scripts/backfill26_vision_days.sh` (26 ok / 0 fail, 28,683,093 rows, each re-verified on HF by parquet metadata row count) |
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
| microstructure PREREG-001 (trades-only spread vs book) | **CLOSED 2026-08-07 — INDETERMINATE, and the MA(1) premise is REFUTED by the data.** **ANOTASI 2026-08-07:** the venue-contamination hypothesis is **RETRACTED** (434/434 top pairs are binancef on both rows; the unfiltered counterfactual raises on bybit's UUID trade_ids; and the ALL-MIXED control gives `rho_2` of −0.042/−0.030/−0.012, i.e. mixing DESTROYS the signature rather than creating it). The verdict is **NARROWED to `binancef`/BTCUSDT**: `bybit` shows `rho_1 ~ −0.47` with `rho_2 ~ −0.003`, which is MA(1) as Roll assumes, and was never tested. Every claim resting on the `0.0078 bps` book anchor is **`[UNVERIFIED]`** until BOOK-001 lands — the query behind it exists nowhere in the repo. Detail: `docs/DIAG-venue-filter-audit.md`. Pooled over 23 dates / 13.4 M lag-1 pairs: `rho_1 = -0.7127`, outside the `[-0.5, +0.5]` any MA(1) can produce, and `sigma2_w` came out NEGATIVE. Per-day `rho_2` is +0.31 to +0.52 where MA(1) needs 0; the order gate rejects every day tested. Kill criterion §7.2 is met: no cost model may be built from the Roll family on this instrument until the cause is known | `docs/PREREG-microstructure-001.md` RESULT; `reports/prereg-microstructure-001-result.json` |
| scalping PREREG (1–30 s, 1:1) | **WRITTEN** (`docs/PREREG-scalp-001.md`, 315 lines): premise fails arithmetically in all three execution models (p* 118–222 % at 30 s); rejection registered as the deliverable; one reformulation declared-not-activated (5-min bars, R=3, p* 51.1 %, N_trials cap 3) | `docs/PREREG-scalp-001.md` |
| markout PREREG-001 (aggressor side → next move, binancef, no conditioning) | **CLOSED 2026-08-08 — GAGAL, decisively**: all 32 declared cells (8 horizons 1 s–4 h × 4 latency offsets 0–200 ms, 277 archive days, ~3.7 M trades/cell) have their entire 95 % CI below the 4 bps maker hurdle; max cell `+0.157` bps [`+0.138, +0.180`] at 5 s / 0 ms. The sign is real (CIs exclude zero at h ≤ 900 s, L ≤ 10 ms) but ~25× under maker and ~64× under taker economics, decays to ~0 by L=200 ms, flips negative at long horizons. First predictive look since PREREG-microstructure-001; declaration committed before the runner ran, 3 amendments (all my own control-design errors, all caught before real signed data) carry the audit trail | `docs/PREREG-markout-001.md` HASIL; `reports/prereg-markout-001-result.json` |

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

1. ~~`pytest -q` hides skips in the gate and in CI.~~ **CLOSED 2026-08-07** — `-rs` added to
   both Makefile invocations and to CI. Failing on an unexpected skip count was deliberately
   NOT added; that changes the standard and is a separate decision.
2. ~~Duplicate rows in `trades`.~~ **FIXED 2026-08-08, NOT YET IN EFFECT** —
   `UNIQUE (exchange, symbol, trade_id)` on the `trades` DDL, with a conflict-skipping insert
   because a bare constraint loses the WHOLE BATCH on one duplicate (measured: 3 offered, 1
   stored) and `INSERT OR IGNORE` raises on a table without one. Would reject **11,746 of
   13,887,539** rows (0.0846 %) across six audited days. **Activation needs a collector restart**
   — the process has been up since before the commit, so no live day file carries the constraint
   yet (§5a-bis). Original scope, kept for the record: Not one day: **10,248 duplicate
   `(exchange, symbol, trade_id)` rows across 27 partitions = 0.041 %**, with a peak of
   **1.0016 % on `2026-08-02`** whose duplicates sit in five hours ALL INSIDE the UTC 12–23
   window a PREREG would use (`2026-08-01`'s 971 sit in hour 05 and fall outside it by luck).
   **New and load-bearing:** every duplicate pair measured is **byte-identical** across
   `ts_ms`, `price`, `qty` and `aggressor_buy` — 0 disagreeing. So which copy survives cannot
   matter, and a read-time `DISTINCT` is sufficient; a unique index is a schema change and a
   heavier answer to the same problem. Still yours to choose (`DIAG-provenance-001`).
2b. **The ~0.01 qty disagreement — DID NOT RECUR on the second day.** `2026-08-05` aged out,
   `_pick_day()` moved to `2026-08-06`, and the same gate measured **`max|Δqty| 0.0`** there.
   Two observations now: one day with a ~0.01 disagreement, one day exact. So it is
   **day-specific, not systemic** — which lowers its priority but does not close it, because a
   single unexplained fidelity break is still a fidelity break. Chasing the cause still means
   an ad-hoc read of a LockBox day, and that is still **your call, not mine.**
   **The duplicate defect, by contrast, DID recur**: 758 of 688,524 rows on `2026-08-06`
   (0.11 %). That one is systemic and is item 2.
3. **Five ledger entries carry a wrong terminal state** (`upload_failed` on partitions that
   migrated successfully — §5c B). The ledger is append-only, so the correction is an appended
   corrective record, never a rewrite. Shape of that record is azul's call.
4. **295-day backfill** via the `--date` path — **now unblocked** (the local migration is done),
   and the `--date` path is production-proven as of 2026-08-08: the 26 scattered days ran 26/26
   clean through it (`scripts/backfill26_vision_days.sh`). `2025-10-08` still holds a
   `trades.parquet.bad` partial artifact that must be cleared as part of that day's re-ingest.
   Do it chunked, not as a naive `--date` loop.
5. **`2026-08-06` bootout damage entry**: a ~4 min hole (~02:52–02:56Z, log-relocation restart)
   is expected; measure and record it once the day closes.
6. **disablesleep experiment**: locked behind the 36 h churn threshold; needs a fresh baseline.
7. **`make check-vision` is locally infeasible at full scale** (audit-measured: dedup over 2.83 B
   rows needs ~160 GB of aggregate state — needs a per-month window flag and an explicit
   `temp_directory`). Now more pressing, since the local copies it used to read are gone.
7b. **Two microstructure findings CLOSED as not-pursued, 2026-08-08.** The 2.5x-9.5x gap is
   RECORDED, NOT PURSUED — it lives inside a term worth 0.16 % of the taker round trip and its
   own denominator moves 2.3x. Block B is UNANSWERED, NOT PURSUED — its loader put `ORDER BY`
   inside the dedup subquery, so it tested one ordering three times. Neither is "resolved".
8. ~~Three MA(1)-dependent estimators do not call their own order gate.~~ **CLOSED
   2026-08-07** — all five now gate, and `tests/test_hasbrouck_gates.py` makes a sixth
   estimator impossible to add ungated: every name in `__all__` must be classified as
   MA(1)-dependent or exempt, with a reason, or the suite fails. **This changes what the
   instrument reports**: on a non-MA(1) series `roll`, `pricing_error_lower_bound` and
   `identified_interval_c` now ABSTAIN where they used to answer. Before the fix, `roll`
   returned verdict `OK` on a simulated MA(3) while reporting a NEGATIVE `sigma2_u` in the
   same dict.
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

## 5a-bis. Ratifications and corrections (dated; approval order stated explicitly)

**2026-08-08 — `insert_sql_for()` RATIFIED, RETROACTIVELY.** The `trades` UNIQUE constraint was
approved in advance; the write-path change that shipped with it was **not**. It was committed
first (`ff3514e`) and approved afterwards, and that order is recorded rather than smoothed over.

The reason it was approved is the measurement that forced it, not the intent behind it:
a plain `executemany` INSERT on a constrained table **loses the whole batch** on one duplicate —
3 rows offered, 1 stored — and the collector's flush path then does
`rows_dropped_error += len(buf)` and evicts the connection. A bare constraint would have turned a
byte-identical duplicate into real data loss. And `INSERT OR IGNORE` raises a Binder Error on a
table without a constraint, while the store keeps **today and yesterday**, so both schemas are
live at once. Hence detection per connection rather than a remembered rule.

**Not yet in effect.** The collector process started `2026-08-06 09:56:38` and the commit landed
`2026-08-08 04:18:28`; a Python process does not reload modules, so every day file this process
opens still lacks the constraint. Confirmed on `2026-08-06.duckdb`: constraint **NO**, 758
duplicates. Activation requires a restart, which costs a real gap and is not taken here.

## 5a-ter. External report ingested; the un-backfillable gap is now recording (2026-08-08)

- **External feasibility report** audited claim-by-claim in `docs/EXTERNAL-scalping-feasibility-001.md`:
  every back-quote of this repo's numbers is accurate; two framings corrected (its MinBTL alarm is
  backwards for the daily board and understated for the 30-day slice, which supports exactly N=1
  trial; its PBO<0.2 recipe is behind the calibrated-null replacement). Its core negative thesis
  converges with three verdicts this repo reached independently.
- **Raw L2 depth-diff recorder is LIVE** on a GCP e2-small in Tokyo (`btcq-depth-rec-1`,
  asia-northeast1-b): keyless, **no service account, no scopes, no secrets on the VM**, SSH via
  IAP only, shielded boot. Verified `active (running)` via serial console 2026-08-08T00:55:49Z.
  ~164 MB/day compressed measured in smoke; 50 GB disk ≈ ten months of headroom. ~~The VM runs
  commit `c344d3f`~~ **updated 2026-08-08**: serial console shows `Updating c344d3f..7b24df5,
  Fast-forward` + service restart 01:21:04Z, so the three post-refutation recorder fixes are
  live on the VM. The boot chain is now HOME-independent and cache-free (metadata startup →
  local-checkout pull first, curl only for an empty disk) after a five-boot silent-failure
  chain whose root cause was my own `2>/dev/null` swallowing a `git config` error — sequence
  recorded honestly in `deploy/gcp/README.md`, wrong CDN diagnosis marked wrong, not erased.
- **Movement census** (`DIAG-movement-census-001`): near-pure diffusion (RMS/√τ 0.53–0.60);
  median |move| crosses the taker RT between 15 m and 1 h — converging with the existing
  30-minute crossing measurement from a different route.
- **Named, deliberately not written**: ~~`PREREG-markout-001`~~ (**written, run, and CLOSED
  GAGAL 2026-08-08** — see §3), `PREREG-obi-predictive-001`, `PREREG-funding-carry-001` — the
  remaining two are each a predictive look needing declaration + N_trials cap + approval.

## 5a-quater. Tape backup + venue data-ceiling census (2026-08-08)

**The tape is no longer single-copy.** `gs://btcq-depth-tape-1` (asia-northeast1, same region
as the VM so egress is zero; uniform bucket-level access; versioning on; public access
prevention enforced; lifecycle Nearline at 30 d, Coldline at 90 d). The VM holds **one** new
power — `roles/storage.objectCreator` on that bucket alone, doubled at the OAuth layer by a
`devstorage.write_only` instance scope. It cannot read back, list, overwrite, or delete;
every sync run probes its own upload expecting HTTP 403 and the heartbeat publishes
`readback_denied`, so posture drift would appear in the data rather than in an audit nobody
re-runs. Verified end-to-end 2026-08-08: the whole day's tape was downloaded from the bucket
to a DIFFERENT machine, reassembled from its 5 objects, and re-counted — **131,569 frames,
2 holes, 70,627 hole bytes, matching the VM's heartbeat number-for-number**. Local copies are
deleted only 3 days after their day-manifest verifies. Full runbook incl. the honest posture
change: `deploy/gcp/README.md` (grep `Phase 2c`).

**Recorder is now visible with no SSH** — a 5-minute heartbeat object plus a per-boot QC
census, both in the bucket. Before this, current disk usage was **not** readable without SSH
(only the kernel's boot-time disk SIZE appeared on the serial console); that gap is closed
from two directions now. Console:
`https://console.cloud.google.com/storage/browser/<YOUR_TAPE_BUCKET>/heartbeat?project=<YOUR_PROJECT_ID>`

**Measured that day** (details + what they do not prove: `docs/DIAG-recorder-quality-001.md`):
178.3 MB/day compressed over a 3.83 h window [DIUKUR 2026-08-08] — the earlier ~164 MB/day
came from a 45 s smoke; 9.55 fps average; **0 sequence gaps, 0 resyncs**; latency proxy
p50 2 ms / p99 11 ms; 97.40 % recording uptime, with all 6.0 minutes of downtime caused by
this session's own deploys. Disk runway at that rate: ~269 days on the free 47.9 GB.

**Venue data-ceiling census**: `docs/DIAG-data-ceiling-001.md` — eight parallel read-only
probes, nothing ingested. Headlines: Binance Vision publishes **nine** daily USDS-M families
(not aggTrades only — dated correction recorded, old sentence kept), but **no depth diffs
anywhere**, so the recorder's rationale stands; `bookTicker` history is a frozen
2023-05-16..2024-03-30 window and BBO is forward-only since 2024-05; `metrics` gives 5-minute
OI and long/short ratios back to 2020-09-01; **OKX publishes keyless daily L2 400-level
snapshot+update archives back to 2023-12-04**; Coinbase's L3 `full` channel now **requires
auth** (repo's earlier note superseded); Bybit stays trades-only; perp-DEX splits between
open (dYdX, Drift) and gated (Hyperliquid requester-pays, Lighter auth).

**Two contradictions surfaced and deliberately NOT fixed this turn** (fixing them needs
evidence, not a guess): current Bybit docs say a `Buy` liquidation update means a LONG was
liquidated, which is the opposite of the collector's stored mapping; and the collector's
"only depth20@100ms flows on this network" comment is too narrow — `bookTicker` flows too,
measured twice with same-run zero controls on aggTrade/markPrice.

## 5a-quinquies. Billing deadline defused; recorder at full cadence (2026-08-08)

**Third link of the chain is live: VM --write-only--> GCS --pull--> Mac --token--> HF.** The
VM's posture is UNCHANGED — it never sees a Hugging Face token and never talks to HF; the
Mac does that hop with the token it already had. `scripts/tape_gcs_to_hf.py` verifies twice
per object: the downloaded bytes against the md5 GCS recorded at creation, and a read-back
from HF over a plain-HTTPS path that shares no library with the uploader. `data/archive/`
(the last single-copy store on this machine) went up in the same run, 6 files, all
round-trip verified. Ledger: `reports/tape-hf-ledger.jsonl`.

**Per-object verification PASSED for all of it** (22 objects: 16 tape + 6 archive, each
byte-exact on read-back against the md5 GCS recorded at creation). The **full
reassemble-from-HF control has NOT passed yet** — `scripts/verify_tape_hf.py` exists and is
runnable, but it did not complete while the OKX acquisition was saturating the same link and
HF's CDN truncated the 26 MB object repeatedly under that contention. Recorded as NOT YET
PASSED rather than waved through; the GCS-side equivalent of the same control did pass
(131,569 frames, matching the heartbeat).

**Mac uptime requirement, measured**: none, week to week. VM->GCS is a systemd timer every
15 minutes and nothing in GCS ever deletes (`objectCreator` cannot, and lifecycle only
changes storage class). A Mac down for a week loses **nothing** — the mirror is incremental
by ledger and catches up. What a Mac outage costs is that the un-mirrored days sit in the
billing-risk zone: at the measured rate that is ~2 GB per week of tape with two copies
(VM-local for 3 days, GCS) instead of three.

**Recorder now runs `depth@0ms`.** Measured over five heartbeat windows after the switch
[DIUKUR 2026-08-08]: **36.7-37.3 fps** against 9.55 before = 3.85x the frames, but only
**285-296 MB/day** against 178.3 = **1.62x the bytes** — @0ms frames each carry fewer changed
levels, so conflation was costing information far more than it was saving space. Sequence
gaps and resyncs stayed at **0** at the higher rate. GCS cost at the new volume is ~$0.96/mo
by month 12 (published rates).

> **[CORRECTION 2026-08-08]** The earlier "@0ms means 673 MB/day and a 71-day disk runway"
> projection was **wrong twice** and is kept for the trail: it scaled bytes linearly with
> frames (measured 1.62x, not 3.78x), and it assumed the tape lives on the VM forever, which
> stopped being true when local days began expiring 3 days after their manifest verifies.

**OKX L2 sample**: rule declared and committed before any download
(`docs/SAMPLE-okx-l2-001.md`, 62 days = the 7th and 21st of each month 2024-01..2026-07,
two named tranches). Result doc `docs/SAMPLE-okx-l2-001-RESULT.md`: schema measured (400
levels/side, `[price, size, order_count]`, 0.1 tick, 10 ms median cadence, 60 s snapshots,
**no seqId and no checksum** so lost updates leave no trace), and the declared reconstruction
control **FAILED at 52.2 %** on top-20 exact. Best bid/ask **prices** match exactly in 90/90
snapshot pairs; **sizes** diverge, worsening with depth (80 % exact at top-1, 16.7 % at
top-100). **Nothing may be built on OKX book SIZES until that is explained**; touch prices
are exact. Acquisition continues (raw archive is raw archive); research on it does not.
- **Cloud safety notes** (not in any committed file by design): the deploy docs use placeholders
  only; no real project id or account identity enters this public repo.

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
