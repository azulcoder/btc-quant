# DESIGN — Vision ingest, remote-first

**Design only. Nothing has been ingested. Peak disk is measured below and is the number to
approve or reject before anything runs.**

## Why this is not a disk workaround

Four reasons, and only the last is about space:

1. **Almost every analysis in this repo already reads from HF** via `hf://` + `hive_partitioning`
   — the 2,160-cell coverage scan, the 859,264-snapshot spread aggregate, the Q0 verification.
   The pattern is proven at scale; local Vision is the exception, not the norm.
2. **HF partitions carry provenance a local copy does not.** Each manifest records a `sha256`
   verified against the venue's own zip. That is exactly what the CVD precheck used as its
   positive control — 120 partitions, 0 mismatches.
3. **`hf://` queries return the same number on any machine.** `data/vision/` queries only work on
   this laptop.
4. **The ENOSPC that erased 295 days was a LOCAL DISK failure.** Moving Vision to HF removes that
   failure class rather than mitigating it.

## The measurement that decides the design [DIUKUR]

2,085 manifests, split by the granularity the ingest actually fetched:

| source granularity | n | zip (median) | extracted CSV (median) | parquet (median) | **peak disk** |
|---|---:|---:|---:|---:|---:|
| **monthly** | 2,081 | 502.1 MB | 2,585.4 MB | 5.2 MB | **~3.1 GB per month** |
| **daily** | 4 | 7.7 MB | 41.0 MB | 2.4 MB | **~51 MB per day** |

**The original ingest fetched MONTHLY archives.** That is why it died: a monthly object needs
~3.1 GB of transient disk, and it hit ENOSPC.

**Conservative daily estimate for the missing window**, since only 4 daily manifests exist and
they are recent low-volume days — derived from the monthly figures divided by ~30 days:
zip ≈ 16 MB + CSV ≈ 86 MB + parquet ≈ 5 MB = **~107 MB peak per day**.

| | |
|---|---:|
| **peak disk, daily granularity, one partition at a time** | **~51 MB measured / ~107 MB conservative** |
| free disk now | 2.3 GB |
| headroom | **21×–45×** |
| peak if monthly granularity were used | ~3.1 GB — **does not fit** |

**So the design decision is forced by measurement: fetch DAILY, never monthly.** It costs 295
requests instead of 10, and it bounds peak disk at ~107 MB regardless of how much history is
ingested. Total parquet produced (~1.5 GB for 295 days) goes to HF and never accumulates locally.

## Local disk state, and why this is urgent [DIUKUR]

At the time of writing the volume was **100 % full, 593 MiB free**, and `/health` reported
**`rows_dropped_error: 1`** — the collector had already lost a row to ENOSPC, on the LockBox
slice. 2.1 GB of my own session scratchpad was cleared, taking free space to 2.3 GB, which is
breathing room and not a fix.

**`data/vision` was 12 GB and NOT on HF when this was written** — checked, three candidate paths,
none existed, so it was then the only copy of the archive. That is why the migration had to
precede any reclaim. **Current partition counts and migration state: `docs/STATUS.md` §2** — this
file records the design and its measurements, never the live state.

## 17a — the per-partition pipeline

```
FETCH(daily zip) → VERIFY zip sha256 against the venue's published checksum
                 → NORMALIZE to parquet, compute parquet sha256
                 → UPLOAD to HF
                 → READ BACK from hf:// and recompute sha256
                 → sha256 matches?  ── no ──→ KEEP LOCAL, record failure, continue
                                     └─ yes ─→ DELETE LOCAL
```

**Peak disk = one partition, ~107 MB conservative.** Batch size N multiplies it: N=1 recommended
at current free space; N=4 is ~430 MB and still fits.

**The load-bearing rule: local deletion is gated on a REMOTE READ-BACK, never on the upload call
returning success.** "The API said OK" and "the bytes are there and correct" are different
claims, and only the second one licenses a delete. This is the same distinction §10 drew between
fidelity and completeness, applied to a write path.

sha256 read-back costs a re-download (~5 MB/day, ~1.5 GB total for 295 days). A cheaper check —
comparing `count(*)`, `id_min`, `id_max`, `id_distinct` against the manifest — catches truncation
but **not** silent corruption, so it is the fallback, not the default.

## 17b — constraints that must be in the design because they have already happened

### Partial ZSTD failures

`2026-07-05` and `2026-07-13` failed to read from HF with `InvalidInputException` — a ZSTD
decompression failure on already-published partitions. Two requirements follow:

1. **Per-day reads, never a single glob over everything.** One corrupt partition must not kill a
   query over 2,000 good ones. This is the pattern the 26-day spread aggregate already used:
   *"1 partition, 2026-07-13, failed to read — it is excluded and counted, not silently dropped."*
2. **The failure must be enumerable**, so a later query can list exactly which days are missing
   and why. Absence that cannot be distinguished from "nothing looked" is blindness class B.

### Upload failure mid-way

Every partition ends in exactly one recorded state, and the state names the stage:

| state | local retained? | meaning |
|---|---|---|
| `ok` | no — deleted after verified read-back | on HF, sha256 confirmed |
| `absent` | n/a | the venue does not publish that day |
| `fetch_failed` | n/a | network or HTTP error |
| `checksum_mismatch` | yes | the venue's zip failed its own published checksum |
| `normalize_failed` | yes | includes ZSTD and parse failures |
| `upload_failed` | **yes** | never delete on an unconfirmed upload |
| `remote_verify_failed` | **yes** | uploaded but the read-back disagreed — the dangerous case |

**`remote_verify_failed` is the state that matters.** It is the only one where a naive
implementation would have deleted local data that is not safely remote.

### Idempotence

Re-running must be safe. A partition already `ok` on HF is skipped without re-fetching; a
partition in any retained state is retried from the start. The run is resumable after any
interruption, including another ENOSPC.

## What I could not measure

- **The true daily peak for the missing window.** Only 4 daily manifests exist and they are
  recent, low-volume days; the ~107 MB figure is **[DISIMPULKAN]** by dividing monthly sizes by
  ~30, and October 2025 – July 2026 volumes are not known.
- **Whether HF upload throughput makes 295 daily round trips practical.** Not tested; no upload
  has been attempted.
- **Whether the HF dataset has a size or file-count limit** that the full archive would hit
  (~2,086 partitions as measured 2026-08-06).
- **Whether the two known ZSTD failures are corruption at rest or a transient read error.** Never
  re-tested since they were first seen.
- ~~**Whether `rows_dropped_error: 1` cost a real row.**~~ **ANSWERED in §21** — the stamped log
  locates it exactly: 1 `depth_snapshots` row at `2026-08-05T17:26:26.870Z`, ENOSPC on the WAL,
  inside the LockBox, unrecoverable. Recorded in `reports/lockbox-manifest.json`.

---

# §20 — disk margin BEFORE migration, and a correction to my own runway claim

## The claim that was wrong [DIUKUR]

I reported **"runway ~9 days"** on the basis that the collector writes ~268 MB/day. **That was
wrong, and the repo already contained the evidence.** `scripts/upload_hf.py` states its own rule:

> *"No offsite verification, no local delete."* … *"Today and yesterday are never deleted (§3c):
> they stay local … until it ages out of the keep-local window."*

**The tick store is bounded at two day-files.** Measured now: `2026-08-04.duckdb` 250 MB (closed)
+ `2026-08-05.duckdb` 263 MB (open) = **490 MB, steady state**. `2026-08-03` is already gone,
verified on HF. **The collector consumes ~0 GB/day cumulatively**; it oscillates within ~520 MB.

**So free space is STABLE at 2.4 GB, not draining.** The question is not "how many days until
full" but "how much headroom against a single transient".

**And the correction cuts the other way too:** the remote-first pipeline I designed in §17a is
not a new idea — `upload_hf.py` already implements exactly that discipline for the tick store
(verify offsite, then delete local). The Vision design should **reuse that proven path**, not
reinvent it. I designed something this repo already had.

## 20a — what occupies the volume [DIUKUR, listed only, nothing touched]

195 GB used of 228 GB. `~/Code/btc-quant` is 12 GB of it.

| # | path | size |
|---:|---|---:|
| 1 | **`~/Parallels/Windows 11.pvm`** | **55 GB** |
| 2 | `~/Library/Application Support` | 18 GB |
| 3 | `~/Library/Group Containers` | 17 GB |
| 4 | **`~/Code`** (this repo is 12 GB of it) | 12 GB |
| 5 | `/Library` | 7.3 GB |
| 6 | `~/projects` | 6.7 GB |
| 7 | `~/Library/Parallels` | 4.9 GB |
| 8 | `/Applications` | 4.3 GB |
| 9 | `~/Library/Caches` (total) | 3.6 GB |
| 10 | `~/Library/Containers` | 2.1 GB |
| 11 | `~/.vscode` | 1.7 GB |
| 12 | ↳ `~/Library/Caches/com.microsoft.VSCode.ShipIt` | 1.5 GB |
| 13 | `~/.local` | 1.1 GB |
| 14 | ↳ `~/Library/Caches/ms-playwright` | 1.0 GB |
| 15 | `~/Downloads` | 943 MB |

Also: `~/.gemini` 932 MB · `~/google-cloud-sdk` 661 MB · `~/.cargo` + `~/.rustup` 1.1 GB ·
`~/private/var/folders` 601 MB · `~/.codex` 477 MB · `~/fordiscord-archive` 448 MB.

Rows 12 and 14 are **inside** row 9, not additional. **Nothing was deleted and nothing outside
the repo was touched.** `~/Parallels/Windows 11.pvm` at 55 GB is 4.5× everything else on this
list combined that is plausibly reclaimable, and it is a single file — but whether that VM is
needed is not mine to judge.

## 20b — what freeing space actually buys

Because nothing grows cumulatively, the table is not "days until full" but "what fits":

| free space | covers a full day of tick growth (270 MB) | covers the daily Vision pipeline (~107 MB peak) | covers a MONTHLY fetch (3.1 GB) |
|---:|:--|:--|:--|
| **2.4 GB (now)** | yes, 9× | yes, 22× | **NO** |
| 10 GB | yes, 37× | yes, 93× | yes, 3× |
| 30 GB | yes | yes | yes, 10× |
| 50 GB | yes | yes | yes, 16× |

**14.4 GB arrives for free once Vision migrates to HF** — the 12 GB local copy becomes
deletable, and that is the migration's real payoff. So the answer to *"how much must I free to
stop worrying"* is: **nothing on a schedule.** What is needed is enough headroom that one
transient cannot reach zero, and the migration itself supplies 12 GB of it.

**The single live risk at 2.4 GB is a monthly-granularity fetch (3.1 GB), which is precisely what
caused the original ENOSPC.** The daily-granularity design removes that risk by construction, so
the margin question and the design question have the same answer.

## 20c — why margin comes before migration

**A decision taken under time pressure is a bad decision, and the proof is already in this
repo.** The 12 GB Vision ingest was launched in the background without first checking free space.
It filled the volume, killed itself at `2025-10-01` with ENOSPC, left a **295-day hole in the
archive**, and — nine days later — dropped a row from the **LockBox**, the one slice that can be
looked at only once.

Every one of those consequences came from not measuring 30 seconds of disk state before starting
a multi-hour job. Migrating 295 partitions while the volume sits at 2.4 GB would repeat the
pattern with better intentions. **Margin first, then migrate without a clock running.**

---

# §21 — the LockBox defect, located and durably recorded [DIUKUR]

**This section number was cited by the look counter and §20 before the section existed — the
audit found the hole; this stub heals it and says where the substance lives, because the work
was done as artefacts rather than prose:**

- **`reports/lockbox-manifest.json`** — the defect record: 1 `depth_snapshots` row lost at
  `2026-08-05T17:26:26.870Z` to ENOSPC on the WAL, inside the LockBox, unrecoverable (no venue
  endpoint serves historical book). Recorded there because `/health`'s counter resets on
  restart.
- **`scripts/lockbox_integrity.py`** (`make lockbox-integrity`) — cross-checks the stamped log
  against the manifest with exact matching; 3 negative controls verified it bites.
- **Post-reboot amendment 2026-08-06:** the 05:23-local reboot wiped `/tmp`, taking the log
  line with it. The gate now distinguishes *entry-predates-current-log* (the manifest, which
  quotes the line verbatim, is the surviving record — PASS with a note) from *phantom*
  (entry after log start with no line — still FAIL). The log's `/tmp` location is a standing
  risk; relocating it is an open recommendation.

---

# §22 — ONE PARTITION END-TO-END: the pipeline control [DIUKUR]

**`2026-07-30`, the last partition before the 295-day hole. This whole partition is a CONTROL
for the pipeline, not a result about the data.** All seven states ran, twice: once `--dry-run`
(states 1–6) and once for real (states 1–7).

| state | outcome | time |
|---|---|---:|
| 1–4 fetch → verify zip sha256 vs venue → normalize → sha256 parquet | ok, 834,335 rows, `checksum_verified: True` | 3.3 s |
| 5 upload (parquet + manifest) | 3.19 MB at **0.50 MB/s** | 6.4 s |
| 6 **read back from the hub, recompute sha256** | `a847d540…` = `a847d540…` — **MATCH** | 2.5 s |
| 7 delete local — gated on 6 | deleted 3.17 MB; partitions 2,086 → **2,085** | — |
| | **TOTAL** | **12.3 s** |

**Peak disk MEASURED, not estimated: 70.7 MB**, sampled every 250 ms by a background thread
during the run. Against 2.4 GB free that is **34× headroom**, and it does not grow with the
number of partitions.

**`upload_hf.py` needed NO generalisation.** `hf_upload_file` and `hf_download_file` already take
an arbitrary `path_in_repo`; only the prefix differs. Its discipline was reused verbatim.

## The answers to the three questions that could have cancelled the plan

| # | question | answer |
|---|---|---|
| **c** | is 295 round trips practical at HF upload throughput? | **Yes.** 0.41–0.50 MB/s, 12.3 s/partition end to end |
| **d** | will the full archive (~2,086 partitions, measured 2026-08-06) hit an HF limit? | **No.** The repo holds **278 files** now; +4,172 for full migration = **~4,450**, against HF's ~100,000-file guidance |
| **f** | were the ZSTD failures corruption or a transient read? | **TRANSIENT.** `2026-07-05` (150,174 rows) and `2026-07-13` (240,728 rows) both read **first try**. HF scans need **retry, not re-upload** |

The uploaded partition is queryable through `hf://`: 834,335 rows, `id` span 3397437001–3398271335,
contiguous, matching its manifest exactly.

## The finding I did NOT expect, and it changes the migration plan

**Parquet normalisation is NOT deterministic.** The same archive day, normalised three times,
produced three different files:

| when | sha256 | bytes |
|---|---|---:|
| original ingest, 2026-08-02 | `1523309d…` | 3,168,185 |
| dry run today | `346d41cd…` | ~3,170,xxx |
| real run today | `a847d540…` | ~3,190,000 |

Row count identical at 834,335 every time; **bytes differ**.

**So a manifest sha256 verifies TRANSPORT, not REPRODUCIBILITY.** State 6 remains sound — it
compares the bytes just uploaded against the bytes read back, and that is exactly the claim
needed to license a delete. But a sha256 cannot be used to check that a re-ingest reproduced an
earlier partition.

**Consequence, and it makes the migration cheaper:** the [2026-08-06] locally-held partitions
must be **uploaded as they are**, never re-fetched. Re-fetching would produce bytes that disagree
with their own recorded manifests, which would look like corruption and is not. Upload-only also
skips states 1–4 entirely.

## Extrapolation [DISIMPULKAN from one partition]

| path | per partition | count | total |
|---|---:|---:|---:|
| full (fetch + normalize + upload) — the 295 missing days | 12.3 s | 295 | **~60 min** |
| upload-only — the 2,085 already local | ~8.9 s | 2,085 | **~5.2 h** |
| | | | **~6.2 h combined** |

**Peak disk stays ~71 MB throughout**, which is the property that makes the count irrelevant.
Against §20's finding that free space is stable rather than draining, there is no clock on this.

## What I could not measure

- **Whether 12.3 s is representative.** One partition, one time of day, one network condition.
  October 2025 – July 2026 days may be larger than 2026-07-30's 834,335 rows.
- **Why the parquet is nondeterministic.** Compression nondeterminism and embedded metadata are
  both plausible; not investigated. It matters only if a reproducibility claim is ever made.
- **Whether the ZSTD failures recur.** One successful retest each proves the data is intact at
  rest; it does not prove the read never fails again. Retry logic is still needed.
- **Whether HF throttles sustained upload** over hours. Two uploads is not a rate-limit test.
- **The upload-only path's timing.** The 8.9 s figure subtracts states 1–4 from a measured full
  run; it has not itself been run.

---

# §23 — the upload-only path: declarations, made BEFORE the first batch ran

## ㉓ What the 2,08x old manifest sha256s still prove — and what they no longer do [DISIMPULKAN from §22's measured nondeterminism]

§22 measured parquet normalisation as nondeterministic: the same venue zip, normalised three
times, gave three different byte streams (row count identical each time). Every consequence
below follows from that one measurement.

**Claims each old manifest `normalized.sha256` STILL supports:**

| claim | why it survives |
|---|---|
| **"The local file is byte-identical to what the venue-verified ingest produced"** | hashing the file at rest is deterministic; a mismatch = bit-rot or tamper since ingest. **This is the pre-upload gate** (`local_manifest_mismatch` state). |
| **"A copy of THESE bytes arrived intact"** | hash both ends of the copy. **This is the transport check.** |

**Claims it can NO LONGER support:**

| claim | why it fails |
|---|---|
| "This is *the* correct normalisation of the venue zip" | no canonical byte-form exists — three normalisations, three hashes |
| "A re-ingest can be verified against this hash" | a mismatch there is *expected*, not corruption |
| "Byte-level dedup or reconciliation across ingest generations" | two valid partitions of the same day may differ byte-wise; dedup must key on `date`, never on content hash |

**What carries content proof instead — deterministic and source-bound:** the zip sha256
verified against the venue's own published checksum at ingest (`checksum_verified: true`),
and the normalized stats `rows / id_min / id_max / id_distinct / id_span / id_holes / ts range`
— properties of the *data*, invariant across normalisations [DIUKUR: identical across all three
§22 runs].

**Therefore the upload-only read-back gate is three checks, all required, same run as any
delete:** `sha256(read-back) == sha256(local)` (transport) **AND** content stats(read-back) ==
manifest.normalized (content) **AND** read-back manifest byte-identical to the local manifest.
sha256 answers *"the copy is intact"*; the stats answer *"the content is what the verified zip
contained"*. For 2,08x partitions that distinction is what licenses each delete.

**Worked example already in the store:** `2026-07-30` on HF carries sha `a847d540…` while its
old local manifest recorded `1523309d…` — different bytes, identical stats, both valid. The HF
pair (parquet + freshly-written manifest) is self-consistent; the stale local manifest remains
as the record of bytes that no longer exist. Any future auditor who diffs those hashes must
reach for this section, not for a corruption alarm.

## ㉔ Checkpoint and the sleeping host

**24a — checkpoint:** `reports/vision-migration.jsonl`, append-only, fsync per line. Every
partition ends each run in exactly one recorded state; a crash loses at most the in-flight
partition. **Checkpoint state never licenses a delete** — prior `readback_ok` only
short-circuits the re-UPLOAD; the read-back that licenses a delete happens in the same run as
the delete, every time.

**24b — the host sleeps: BOTH, not either.** `caffeinate -i` wraps the batch (prevents the
documented Maintenance-Sleep interruptions that §14 measured at 1.3 bursts/h), **and** the
checkpoint stands regardless (network drops, HF outages, and process death are not sleep, and
caffeinate does nothing for them). Choosing only caffeinate trusts one failure class; choosing
only the checkpoint accepts fragmentation of a 5 h measurement for no reason.

**Declared contamination rule, BEFORE the first caffeinated batch:** artificially-awake hours
are exactly the confound of §14c's leading candidate ("host awake because in use"), so **any
§14b clean-stretch claim that overlaps a caffeinated window must exclude or flag it**. The
`batch_start`/`batch_end` checkpoint lines carry `caffeinated: true/false` and are the durable
record of those windows. (As it happens, the first post-fix burst arrived at
`2026-08-05 18:21:40Z`, before any caffeinated window existed — that 18.27 h stretch is
uncontaminated and already ended.)

**24c — rate-limit test is part of the first batch:** per-partition upload seconds are recorded
as a series; throttling shows as a rising tail (first-10 mean vs last-10 mean) and as recorded
429/backoff events. One commit per partition (parquet + manifest staged together), so 100
partitions = ~100 commits — a real probe of commit-rate limits, which two §22 uploads were not.

## ㉕ First batch: size and order, with the reasons

**Size 100.** (1) ~100 commits at a ~400/hr instantaneous pace is where anecdotal HF hourly
commit quotas would surface if they exist — large enough to trip a real limit, which is the
point; (2) a 100-point timing series gives the first/last-decile comparison some power;
(3) bounded: ~15–20 min, ~400 MB transferred, **no deletes in that dry run [2026-08-06]**; (4) ~5 % of the
population checks the 8.9 s/partition extrapolation before it is trusted ×2,084.

**Order NEWEST first.** The "oldest first — longest without backup" argument conflates age with
hazard: the risk to these bytes is *this disk failing*, which strikes all partitions equally,
so age measures exposure already survived (sunk), not future risk. What ordering actually
controls is **which prefix is safe if the migration halts partway** — and the valuable prefix
is the one research queries: the recent regime (the CVD precheck's blocks 3–4, any future
C2/C3 work). It is also the instrument-honest choice: the §22 control was a recent daily-form
partition, so newest-first keeps the first batches inside the regime the control actually
measured; the 2020 monthly-ingested partitions (including March 2020) can be measured when the
migration reaches them.

**One nuance recorded rather than implied:** "the only copy in the world" is exact for the
*bytes*; the *content* is probably re-derivable from Binance Vision, which still publishes the
monthly archives [DIASUMSIKAN — not verified, and Binance's retention policy is not a
guarantee]. The migration's urgency rests on the verified claim, not the assumed one.

---

# §24 — FIRST BATCH RESULTS (dry-run, 100 partitions, newest first) [DIUKUR]

**Adversarial review first, then the run.** A 17-agent review (5 lenses, every non-low finding
independently attacked) confirmed **6 findings** before the batch touched HF; all six were fixed:
the `--date` path could delete a live local file whose bytes were never in its verify chain
(normalisation nondeterminism makes them provably different — `--date` now REFUSES locally-held
days); `run_single` recorded `deleted` *before* deleting (state now follows the action, and
`deleted` was removed from the skip set so a stranded file is re-verified, never skipped);
`--plan` silently ran the full fetch path in `--date` mode (now rejected); the throttle detector
confounded size trend with throttling (now size-normalised MB/s); the extrapolation
double-subtracted deleted partitions; and `checksum_verified: true` is a **same-channel** check —
zip and `.CHECKSUM` come from the same host, so it proves channel-consistency, not independent
attestation [label downgraded]. Notably, two verifiers reached opposite verdicts on the same
underlying live-delete issue through different lenses — the redundancy is what caught it.

## The batch

| | |
|---|---:|
| partitions | 100 (`2026-08-01` → `2025-07-02`, newest first) |
| content-checker control (known partition `2026-07-30`) | **MATCH, first number of the run** |
| `readback_ok` (transport sha + content stats + manifest byte-compare) | **96** |
| `upload_failed` | 4 — all `Errno 60`/read timeouts, **home network, not HF** |
| deletes | **0** (dry run — verified against the live tree, 2,084 parquet intact) |
| upload MB/s, size-normalised | median 0.98 · first-10 1.03 · last-10 0.88 — **no throttle trend inside the batch** |
| 429 events inside the batch | 0 |
| **peak disk** | **24.5 MB** |
| wall clock | 1,189.9 s = **11.9 s/partition** |

**Resume logic verified live, not by argument:** a rerun skipped all 96 `readback_ok`, selected
the 4 failures first, and **all 4 cleared in 2.1–2.7 s** — confirming the timeouts were
transient. The retry rule for the real run follows: `retry_hf` should also retry plain network
timeouts (short backoff), not only 429 signatures; a 4 % loss rate to home-network blips is
noise the checkpoint absorbs but should not generate.

## The rate limit EXISTS, and it arrived just past the mandate

**Scope honesty first: the rerun was my selection logic doing what it says, not what I said.**
"First 100 not yet ok" = the 4 retries **plus 96 new partitions**; I described it as a
4-partition resume test, launched it, and killed it at ~partition 31 when the mismatch was
clear. The overrun was a mistake against the stated scope — and it accidentally delivered the
answer the mandated batch was too small to reach:

> **`429 Too Many Requests` on `upload 2025-06-03` — three consecutive, surviving 60/120/180 s
> backoffs — at roughly commit ~128 within ~37 minutes.**

So HF's commit budget at this pace is **~125–130 commits per short window**, and the
one-commit-per-partition design **cannot run 2,084 commits in 6.9 h**. The honest extrapolations:

| design | commits | est. duration |
|---|---:|---:|
| 1 commit/partition, throttle-paced (~125/h sustained [DIASUMSIKAN from one observation]) | 2,084 | **~17 h** |
| **N partitions per commit** (e.g. 25 staged per `hf_upload_folder` call) | 84 | **~6–7 h, throttle-free with margin** |

**Recommendation for the real run: batch ~25 partitions per commit.** Read-back and delete stay
**per-partition** (the licensing step is untouched); only the upload commit granularity changes.
This needs a small, reviewable change to `migrate_one_local`'s upload step and a re-control on
its first batch, per the standing rail: the first number out of the modified instrument is a
control, not a result.

## Standing after this batch

- **[2026-08-06] 128 partitions verified on HF at the end of this batch**, local copies then
  intact (96 + 4 retried + 28 from the overrun before the kill); on the real run they skip upload
  and go straight to read-back → delete. Live counts: `docs/STATUS.md` §2.
- Checkpoint: `reports/vision-migration.jsonl` — 2 `batch_start` (both `caffeinated: true`,
  windows recorded for §14b), 128 `readback_ok`, 4 `upload_failed` (all later cleared),
  3 `throttle` events.
- Free disk rose 3.3 → 5.6 GB during the run for reasons outside the repo [cause UNVERIFIED —
  nothing in `data/` changed size]; the migration itself peaked at 24.5 MB.

## What I could not measure

- **The sustained commit budget.** One 429 episode bounds it (~125–130 commits in ~37 min);
  the recovery window and the hourly quota are not measured — the ~17 h figure assumes the
  observed bound is the steady state.
- **Whether the 4 timeout failures cluster by time of day** (all four sit in the same run;
  no second run at another hour exists).
- **Why free disk rose 2.3 GB mid-run.** Outside the repo; likely macOS purgeable space, not
  established.
- **The `--date` path post-fix, end to end.** The refusal guard is tested; the happy path for a
  genuinely-missing day has not been rerun since the fixes (it will be, as the 295-day
  backfill's own first-partition control).

