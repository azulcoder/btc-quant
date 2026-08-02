# M7 — public-archive trade ingest (`data/vision/`): run-log

**What was added.** `scripts/ingest_vision.py`, a `--vision` mode in
`scripts/check_ticks.py`, and a `"vision"` provenance class in
`btcquant/orderflow.py`. Together they let the trade-derived order-flow families
read **6.587 calendar years** of Binance's own published `aggTrades` archive
instead of the 28 recorded days the collector has managed so far — and they are
built so that this cannot quietly become the mixed-history backfill STRATEGY §6
refuses.

Everything below was measured on 2026-08-02 against the live archive and the
real recorded store. Numbers that are estimates say so.

---

## 0. The grounding corrections — the spec was wrong in three places

Measured before a line of production code was written, because "don't take a
spec on faith" is the rail that has caught every previous brief.

| Claim in STRATEGY.md M7 | What the archive actually serves |
|---|---|
| "whether a header row is present has **differed by year**" | It differs **per FILE**. `2021-01-01` has a header, `2021-01-02` does not. `2022-08-10` does not, `2022-08-11` does. Monthly `2020-01` does not, monthly `2026-07` does. A year cutoff would lose or invent exactly one row on scattered days out of 2,406, **silently**, and corrupt `min(agg_trade_id)` on those days. The reader sniffs line 1 instead: first field not a base-10 integer ⇒ header. |
| "three of those map onto tables the repo already has (`depth_snapshots`, `liquidations`, `funding_mark`/`open_interest`)" | **False for `liquidations`.** The prefix `futures/um/daily/liquidationSnapshot/` lists **zero keys**. The family exists for COIN-M only (`futures/cm/`, `BTCUSD_PERP`, 2023-06-25 .. 2024-10-14, discontinued) — a different instrument. USD-M liquidations gain nothing. |
| implied: `bookDepth`/`metrics` narrow the trade-vs-book split | **`bookDepth` is not a book**: `timestamp,percentage,depth,notional`, 12 cumulative ±% bands at ~30 s — no levels, no price-per-level, no queue size. It cannot satisfy `depth_snapshots(bids, asks)` and cannot reconstruct OFI / weighted mid / walls. **`metrics` must not ride along**: no unique key, and the timestamp convention differs **per metric inside one file** — joined against the recorded `crowding` rows for 2026-08-01, `sum_open_interest` matches at a **+300,000 ms** shift (242 rows, 0 mismatches) while `sum_taker_long_short_vol_ratio` matches at **0 ms**. A keyless time series with per-metric time conventions is precisely the mixed-history backfill §6 refuses. |

All three are corrected in STRATEGY.md with the old text marked `[SUPERSEDED]`.
The scope is locked in **code**, not in a comment:
`ingest_vision.ALLOWED_SCOPE == (("futures/um", "aggTrades"),)`, with every
refusal carrying its measured reason (`tests/test_vision.py::test_scope_allowlist_refuses_everything_but_futures_um_aggtrades`).

A fourth trap, hit for real during the work and now a coded rail: DuckDB's
`strftime(to_timestamp(ts/1000))` formats in the **session timezone**. Bucketing
2020-01-01 that way under `Asia/Jakarta` gave 53,006 rows instead of the true
71,359. Day bucketing is integer (`ts_ms // 86_400_000`) everywhere, pinned by
`test_utc_day_bucketing_is_integer_not_localtime`.

---

## 1. The load-bearing claim, verified twice by independent routes

M7 is admissible under DESIGN §0.7 for exactly one reason: the archive is the
**same venue, same stream, same aggTradeId space** the collector's
`binancef-aggTrades` leg already records (REST `/fapi/v1/aggTrades`, gapless
`fromId` cursor, `trade_id = str(a)`, `aggressor_buy = not m`). If that identity
fails, dedup becomes heuristic and the item collapses into the thing §6 refuses.

Test day **2026-08-01** (recorded day file closed and immutable; the live
collector held 2026-08-02 and was never touched — comparisons ran read-only
against a copy):

```
archive rows                     : 399,219   (distinct agg_trade_id 399,219)
archive id range                 : 3399378200 .. 3399777418
archive id contiguity in-day     : max-min+1 = 399,219  -> 0 holes
archive ts range                 : 1785542400081 .. 1785628799912

recorded binancef/BTCUSDT rows   : 400,190   (distinct trade_id 399,219)
recorded ts range                : 1785542400081 .. 1785628799912   (identical)

archive \ recorded               : 0
recorded \ archive               : 0

FIELD JOIN on agg_trade_id = trade_id, n = 399,219:
  ts mismatches    : 0
  max |Δ price|    : 0.0
  max |Δ qty|      : 0.0
  side mismatches  : 0     (NOT is_buyer_maker  vs  aggressor_buy)
```

Zero set difference in both directions; zero mismatches across all four fields
on all 399,219 rows. Run once straight from the extracted CSV and once from the
written parquet, by two code paths.

**Cross-day seam:** `last_id(2026-07-31) = 3399378199` →
`first_id(2026-08-01) = 3399378200`, exactly +1. So ID continuity works *through*
day boundaries, not only inside them — which is what makes the archive's gap
census categorically stronger than the 30 s-silence heuristic used everywhere
else in this repo. A hole there is stated by the venue's own monotonic counter;
there is no threshold to argue about.

**The convention is proven, not assumed.** `tests/test_vision.py::test_aggressor_convention_matches_collector_normalizer`
drives `collector.normalize_binance_aggtrades` and `ingest_vision.normalize_agg_row`
over the same real archive rows and asserts the **7-tuples are identical**, with
both aggressor values present so it cannot pass on a constant. "Same stream" is
now mechanical rather than prose.

The standing check lives in `tests/test_vision_overlap.py` (network + a closed
recorded day; skips cleanly otherwise, `BTCQ_SKIP_NETWORK_TESTS=1` to force).

---

## 2. What the archive holds — enumerated, not assumed

`futures/um/daily/aggTrades/BTCUSDT`, from the bucket listing:

| | |
|---|---|
| earliest / latest | **2019-12-31 .. 2026-08-01** (2019-12-30 → 404; the boundary is hard) |
| calendar days in range | 2,406 |
| days actually published | 2,406 |
| **missing days** | **0** |
| total zip | 40.94 GiB (43,957,874,998 B, summed from the listing) |
| per-day zip | min 1.01 MB · median 16.25 MB · max 111.86 MB |
| publication latency | ~D+1 07:00–07:30 UTC (so "today" always 404s, and that is an ANSWER) |
| checksums | 1:1 companion `.CHECKSUM` for every zip, `<64 hex><2 spaces><name>` |

Arithmetic against the MinBTL countdown (`check_ticks.sec_readiness`, run
locally):

| | days | 2,406 archive days as % |
|---|---|---|
| MinBTL(N=5) = 2.70 yrs | 985 | **244.3 %** |
| MinBTL(N=20) = 3.15 yrs | 1,151 | **209.0 %** |
| MinBTL(N=100) = 3.64 yrs | 1,328 | **181.2 %** |
| recorded, today | 28 | 2.8 % |

**Concrete proof this is recovery, not optimisation.** 2026-07-25 has **0**
binancef trade rows in the recorded store (runlog §7 defect 1: the leg died while
74,575 depth rows kept printing). The archive publishes 325,319 rows for that day,
id `3392954217..3393279535`, 0 ID holes, checksum verified. No amount of future
collector uptime brings those back.

---

## 3. Gap 1 SPLITS — it does not close

This is the sentence that had to exist in the docs before the code did, and it is
now in STRATEGY.md Gap 1, DESIGN §0.7 rail c, DESIGN §3d, the module docstring of
both `ingest_vision.py` and `orderflow.py`, the CLI output of every run
(including `--dry-run`), every MANIFEST, and `orderflow.HONESTY_SENTENCES[5]`.

* **trade-derived** — CVD, footprint, size-bucketed delta, VPIN (its volume clock
  needs only trades), and OHLCV itself: **2,406 d = 6.587 yrs = 244 % of
  MinBTL(5)**.
* **book-derived** — OFI, weighted mid, book imbalance, depth sums, the
  depth-imbalance slope, walls: **unchanged at 1.8 %**. The archive publishes no
  book snapshots at all.
  * `bookDepth` (2023-01-01 →, 3 missing days) is 12 cumulative bands, not a book.
  * `bookTicker` **is** real L1 with quantities and would give event-level OFI
    without the 1 Hz sampling approximation — but it covers **2023-05-16 ..
    2024-03-30 only: 320 d = 0.876 yrs = 32.5 % of MinBTL(5)**, discontinuous with
    the recorded window, with no depth beyond the touch. A separate item with its
    own argument, not a closer of this gap.
* **liquidations** — nothing, for USD-M (see §0).

A bar frame mixing both families is only as long as its shortest family. That is
reported per call in `attrs["orderflow"]["history"]["split_statement"]` with both
spans computed from the days that actually resolved, so the claim cannot go stale
as the archive grows.

---

## 4. The seven gates

A day is not trusted until all seven pass, and the canonical parquet is never
written before they do.

| | Gate | On failure |
|---|---|---|
| G1 | sha256 matches the companion `.CHECKSUM`, **and** its filename field is the canonical name | abort |
| G2 | zip holds exactly one entry, named `<stem>.csv` | abort |
| G3 | header sniffed; a present header must match the 7 expected names; column count ≠ 7 aborts | abort |
| G4a | every `ts_ms ∈ [day_start, day_end)`, integer epoch bounds | abort |
| G4b | `ts_ms < 1e14` — 16 digits is microseconds (spot layout) | abort |
| G5 | rows = distinct ids (**duplicates abort**); `max−min+1 − count` recorded as `id_holes` + `id_hole_ranges` | holes **reported, never filled** |
| G6 | seam: `first_id(D) == last_id(D−1)+1` when `D−1` is present | mismatch **reported, never patched** |
| G7 | re-read the written parquet and match `(rows, ts_min, ts_max)` | abort, `.bad` kept for inspection |

Two deliberate refusals of convenience:

* **G1 parses the hex in-process** rather than shelling out to `shasum -a 256 -c`.
  Measured: the CHECKSUM file names the *canonical* object, so `shasum -c` against
  a locally renamed download fails with "FAILED open or read" — a false alarm that
  says nothing about the bytes.
* **G3 sniffs**, and refuses a date cutoff, for the reason in §0.

Failure keeps `trades.parquet.bad`, writes `FAILED-<date>.json`, skips the day,
**continues** the run, and exits non-zero. A 404 is `status="absent"` in
`data/vision/_ledger.jsonl` with **no file at all** — never a zero-row parquet.
404/403 are never retried; 429/5xx back off with jitter and honour `Retry-After`.

---

## 5. Real ingest — measured numbers

Into a temp tree, live archive, 2026-08-02:

```
range 2026-07-30 .. 2026-08-02 (4 days); granularity auto -> 4 daily
  fetched  3 days   2,340,418 rows   27.8 MiB zip -> 8.3 MiB parquet
  absent   1 day    2026-08-02 — not published (HTTP 404); NO file, NO zero row
  failed   0
id continuity: 0 in-day holes; 2 seams checked, 2 contiguous
```

| day | zip | CSV | rows | parquet ZSTD | B/row |
|---|---|---|---|---|---|
| 2026-07-30 | 10,339,170 | — | 834,335 | 3,168,185 | 3.80 |
| 2026-07-31 | 13,739,269 | 73,536,283 | 1,106,864 | 4,044,513 | 3.65 |
| 2026-08-01 | 5,049,749 | 26,515,205 | 399,219 | 1,540,447 | 3.86 |
| 2020-01-01 | 1,010,368 | 4,318,043 | 71,359 | 433,621 | 6.08 |

Extrapolated to the full 2,406 days: **~13 GB of parquet** (labelled an
extrapolation — early days cost ~6 B/row, recent ones ~3.7). Local pipeline cost
per day is ~0.36 s (unzip 0.13 s + load 0.17 s + COPY 0.06 s on the 1.1 M-row
day), so the run is **100 % network-bound**: 4.5 MB/s single-stream measured,
≈2.7 h for the whole history.

The zip **streams to a scratch `.part` file and is hashed in the same pass**, so
a ~530 MB monthly object is never buffered whole (the full history is 79 monthly
objects at ~530 MB mean) and G1 verifies exactly the bytes that landed. No file
ever carries the canonical name before G1 passes, because the download never
leaves the scratch directory. A failed attempt truncates and restarts rather than
splicing a byte range — a silently spliced file is a worse failure than a
re-fetch. Pinned by `test_download_streams_and_hashes_without_buffering` and
`test_download_truncates_a_failed_attempt_rather_than_splicing`.

**Monthly ≡ daily, proven not assumed.** `--granularity monthly` on 2020-01
(102,586,079 B zip) produced 31 day partitions, 7,436,296 rows, 30/30 seams
contiguous. Against separately-ingested daily files, on all seven columns:

```
2020-01-01: monthly 71,359 = daily 71,359   EXCEPT 0 both ways
2020-01-02: monthly 160,454 = daily 160,454  EXCEPT 0 both ways
```

So `auto` (79 monthly requests instead of 2,406 daily ones for the full history)
buys request count and **no leniency** — every split day still passes G4–G7.

---

## 6. The guard-rails, and how each one is held

| # | Rail | How it is enforced (not "how it is intended") |
|---|---|---|
| 1 | **Separate tree** | `data/vision/<venue>/<symbol>/<family>/date=<YYYY-MM-DD>/trades.parquet` — five provenance facts legible from the path with the file unopened. `assert_out_root_is_separate()` **refuses** an `--out` that resolves inside `data/ticks`, `data/orderflow` or `data/hf-stage` (exit 2), asserted in `ingest_day` / `ingest_month` themselves and not only in `main()` (§9.6). And the path's claim is now CHECKED rather than trusted: a `date=D` partition holding foreign rows is refused at read time and FAILs L3 (§9.1). Parquet, not `.duckdb`, specifically so a stray `ATTACH` cannot union it into a recorded relation. Tests: `test_out_root_refuses_to_be_inside_the_tick_store`, `test_ingest_writes_no_duckdb_file_and_no_levels_registry`. |
| 2 | **Recorded-only default** | `SOURCE_ALIASES["auto"] == ("local", "hf")` — for `volume_buckets` too, which now takes the same class list and carries the same labels (§9.5). Opting in is `source=("local","hf","vision")` at the call site. Test seeds recorded **and** archive for the same day with **different volumes** and asserts the default returns the recorded number — a numeric fact, not an absence of error. Precedence `local > hf > vision` means opting in never changes a day the recorded store already answers. Tests: `test_normalize_source_keeps_auto_recorded_only`, `test_default_source_excludes_vision`, `test_recorded_wins_over_vision_for_the_same_day`. |
| 3 | **`sec_readiness` untouched** | Structural (the ingester writes no `levels.jsonl` and no `*.duckdb`) *and* asserted: `check_ticks --vision` **refuses to compute a readiness number at all** — `computed: False`, `span_days: None`, `minbtl: {}`, and the test asserts no percentage and no "toward" line, so there is nothing screenshot-able. Plus an end-to-end snapshot: readiness section before vs after planting a 2019–2026 archive tree beside the store — **equal dicts**. Tests: `test_vision_mode_refuses_to_print_a_readiness_number`, `test_recorded_mode_readiness_is_unchanged_by_an_archive_tree`. |
| 4 | **L3 QA covers the archive** | `check_ticks --vision` runs the SAME sections — one gate definition, never two. Duplicate `trade_id` still **FAILs** (exit 1), verified on a seeded duplicate. Two honest downgrades rather than vacuous passes: the four missing tables are `[INFO] absent by construction` (not `FAIL`, not "empty"), and the ts-inversion check is `[INFO] NOT APPLICABLE` because the parquet is ts-sorted and arrival order is not recoverable. Two vision-only sections: `sec_partition_containment` (rows outside the day their path claims — **FAIL**, §9.1) and `sec_id_continuity`, which reports in-day holes and adjacent-day seam gaps — WARN, never filled, never patched — while ids belonging to days nobody ingested are labelled a request choice, not missing data (§9.4). Tests: `test_vision_mode_fails_on_a_duplicate_trade_id`, `test_vision_mode_reports_id_holes_and_seam_gaps_never_fills`, `test_vision_mode_does_not_claim_arrival_order_it_cannot_have`. |
| 5 | **Nothing fabricated** | 404 ⇒ ledger `status="absent"`, zero files written (`list(tmp.rglob("*.parquet")) == []`). The inverse holds too, which it did not before: a missing MONTHLY object no longer marks published DAYS absent — it falls back to the daily objects (§9.2). ID holes are counted and ranged, and the parquet contains exactly the rows that exist. Seam gaps recorded in both manifests, never closed. Test: `test_absent_day_is_absent`, `test_id_continuity_reports_holes_and_never_fills`, `test_seam_is_reported_never_patched`. |

**One bar never mixes provenance classes — and it is true *by construction*.**
Two halves: (a) `order_flow_bars` already refuses a window not aligned to the bar
grid; (b) `_bar_ms` now refuses any clock that does not divide 86,400,000 ms. All
four `BAR_FREQS` do (1440 / 288 / 96 / 24 bars per day), so no bar can straddle
midnight and provenance — resolved per UTC day — is exact per bar rather than a
summary. A future `7min` clock would break the property, so the guard raises
instead. Tests: `test_bars_never_mix_sources_within_a_bar`,
`test_bar_ms_refuses_a_clock_that_does_not_divide_the_day`.

**The frame says which side of the split it is on, five ways.** A `source_code`
column emitted on **every** run (so its presence is never itself a signal) with
`source_labels` / `archive_mask` / `drop_archive_bars`; a `vision_contribution`
column in `provenance_table` naming the archive per column
(`"none — aggTrades carries no book; this column is NaN on every archive bar"`);
an `attrs["orderflow"]["archive"]` block with day/bar counts, the fraction, and
`book_bars_lost`; and a mandatory warning quoting the exact counts — re-emitted on
a cache hit, since `cache=True` is the default (§9.5). On a mixed
range, every book column is asserted NaN on every archive bar — `coverage_book_*`
included, and `coverage_liq_*` with it: a witness measure reading 0.0 where no
stream was ever published is a fabricated zero (§9.5) — and non-NaN on
every recorded one, while `volume`/`delta` span both
(`test_book_columns_are_nan_on_archive_days`).

`SCHEMA_VERSION` 2 → 3, so no pre-M7 cached bar can be read back into a frame
that now carries `source_code`. The source class list is part of the cache spec
hash, so an opted-in run can never share a cache entry with a default one — while
`"auto"` and its expansion `("local","hf")` correctly share one
(`test_cache_never_shares_an_entry_between_opted_in_and_default`).

---

## 7. A defect this work uncovered, which is NOT M7's to fix

The recorded store currently **FAILs L3**. On 2026-08-01:

```
duplicate (exchange, symbol, trade_id):
  binancef : 971 keys, 971 surplus rows — all exactly 2 copies, every field identical,
             one contiguous run id 3399454462..3399455432, 05:40:04Z..05:44:58Z
  coinbase :  68 keys,  68 surplus rows
  total    : 1,039 keys / 1,039 surplus rows   ->  check_ticks verdict FAIL (exit 1)
```

Mechanism: `trades` has no unique constraint (`collector.py:390`) and the
aggTrades dedup guard is **in-memory** (`last_id` in `_aggtrades_loop`), so a
process restart re-seeds a `fromId` range that was already written. The 971-id
contiguous run over 4 m 54 s is exactly that shape.

The archive has **zero** duplicates and zero ID holes on every day checked, so
guard-rail 4 is met trivially on the archive side — the failing side is the
recorded one. Two consequences, both stated rather than acted on here:

1. `tests/test_vision_overlap.py` compares the recorded side through
   `SELECT DISTINCT` **and prints the surplus**. That is an accommodation, not a
   cure: absorbing it silently would let a recorded defect hide inside a passing
   archive test.
2. The archive now *defines* the true row set for any published day, so it is
   tempting to let it "repair" the recorded store. **Refused in this phase.** That
   would violate day-file immutability and needs its own argument. Separate item.

---

## 8. Honest verdict

**Plus.** The claim the whole item rests on survived independent re-measurement
without residue: 399,219 rows, zero set difference both ways, zero mismatches on
ts/price/qty/side, seam exactly +1. Because the key is unique and the ID space is
shared, dedup is **exact** and gaps are found **by construction** rather than by a
timestamp threshold — which is the only thing separating M7 from the backfill §6
refuses. 2,406 published days with **zero** missing, per-file SHA-256, monthly
proven set-identical to daily, and 2026-07-25 alone recovering 325,319 rows that
collector uptime could never have returned. The "one bar, one provenance class"
property turned out to be makeable **structural** rather than conventional, for
the price of a one-line guard.

**Minus.** Gap 1 does not close; it splits, and the book half is exactly where it
was — 1.8 % of MinBTL(5), buyable only with N4 uptime. Three factual errors in the
brief had to be corrected first (header per-file, `liquidationSnapshot` absent for
USD-M, `metrics` ineligible), which is a reminder of how the third one would have
read had nobody checked: a silent join at the wrong 5-minute bucket. And the
recorded store FAILs L3 today with 1,039 duplicate rows — independent of M7, but
it contaminates every future overlap comparison until it is fixed. Nothing here
has been dogfooded past a 3-day and a 31-day ingest; the 2.7-hour full backfill is
untested at full length.

**[UPDATED 2026-08-02, post-review]** Two sentences of that verdict were too kind and
§9 says so with numbers: the "one bar, one provenance class" property was structural only
as far as the WRITE side, and the read side would have accepted a partition that was not
its own day (§9.1); and "nothing is fabricated" held for absence but not for its inverse —
a missing monthly bundle marked published days as not served (§9.2). Both are fixed and
both now have a test that fails without the fix. The rest of the verdict stands unchanged.

**Recommendation.** Stop and dogfood before chaining anything. Pull one month
(`make vision-sync ARGS="--start 2026-07-01 --end 2026-07-31"`), run
`make check-vision`, then build bars with the explicit opt-in over a range that
straddles recorded and archive days and read `source_code` +
`provenance_table(bars)["vision_contribution"]` by eye — the question worth
answering with a human is whether the empty book half is genuinely unmistakable,
because that is the only failure mode of this design that a test cannot catch.
Two items stay **separate and must not be smuggled in**: (1) the recorded
duplicate defect and a unique constraint on `trades`; (2) `bookTicker`'s 320 days
of event-level L1 — research-interesting, but 32.5 % of MinBTL(5) and
discontinuous with the recorded window, so it is not the book half's answer.

---

## 9. Post-review round (2026-08-02) — what the review found and what was measured

A full adversarial review of the diff produced 22 findings (several duplicates from
independent passes). Everything below is a MEASURED before/after, not a claim.

### 9.1 The critical one: a partition that is not its own day

The per-UTC-day provenance design assumes `date=D` holds day-D rows. That was enforced
only at WRITE time (G4a). Nothing re-checked it at read time, and `check_ticks --vision`
never compared a row's timestamp with the date in its own path — while the tree is
explicitly designed to be copied between machines.

Reproduced on the real trees, with **no duplicate ids anywhere**, so nothing else could
flag it: 1,000 genuine 2026-08-01 rows planted inside `date=2026-07-30/trades.parquet`.

| | before | after |
|---|---|---|
| `check_ticks --vision` | `OK`, exit 0, every section OK | `FAIL`, exit 1, `partition containment (archive)` |
| `order_flow_bars(source=(...,"vision"))` | built bars; 2026-08-01 labelled `['local']`, `trade_count` 401,190 vs a recorded-only 400,190 | `OrderFlowError: archive partition date=2026-07-30 ... holds rows outside its own UTC day` |

Two mechanisms, deliberately: the reader REFUSES (parquet row-group `min`/`max` on
`ts_ms` — exhaustive, one metadata read), and each partition is additionally fenced to
its own day in the SQL so a softened check still cannot leak. The QA section grades FAIL,
the same grade the duplicate-`trade_id` check gets.

### 9.2 Fabricated absence: a missing MONTHLY object marked published DAYS absent

`HEAD` measured 2026-08-02: `monthly/.../BTCUSDT-aggTrades-2026-08.zip` → **404**;
`daily/.../BTCUSDT-aggTrades-2026-08-01.zip` → **200**. Binance publishes the bundle days
after the month ends, so a `--all --yes` in the first days of a month wrote an entire
published month into the ledger as `absent, http_status 404` — and exited 0. That is the
inverse of rail 5: a day the archive DOES serve, recorded as not served.

After (real run, `--granularity monthly --start 2026-08-01 --end 2026-08-02`):

```
[2026-08] monthly object not published (HTTP 404) — falling back to 2 daily object(s)
  fetched     1 day(s)   399,219 rows   4.8 MiB zip -> 1.5 MiB parquet
  absent   2026-08-02 — not published (HTTP 404); NO file written, NO zero row
```

The ledger row for the genuinely-unpublished day now names the **daily** URL it was
actually asked for.

### 9.3 The monthly path, which had no tests at all and three more defects

Verified against the real 2020-01 object (102,586,079 B):

* **Resume before the wire.** First run: 22.2 s, 97.8 MiB, 3 day partitions
  (71,359 / 160,454 / 291,080 rows, 0 in-day id holes, 2/2 contiguous seams — the
  71,359 matches §3d's independently-measured figure). Re-run: `already complete
  (3 day(s)) — nothing downloaded`, 1.1 s, 0 B, and it reports the true
  `522,893 rows already on disk` instead of 0.
* **`except VisionError` → `except Exception`.** A duckdb `ConversionException` (or
  `zipfile.BadZipFile`) escaped the month, escaped `main()`, and aborted the run with a
  traceback, no ledger row and no exit code — on the path `auto` routes every whole past
  month through. Now each day the month owed gets `FAILED-<date>.json` + a ledger row.
* **Manifest parity.** The monthly path now records `first_trade_id_min` /
  `last_trade_id_max` like the daily one (measured on 2020-01-01: 25,247,504 /
  25,349,374), computed in one extra grouped scan rather than by holding two more BIGINT
  columns for a month.

### 9.4 The ID census was exact but counted a range choice as missing data

`sec_id_continuity` computed `max−min+1−count` over the whole union. A tree holding
2026-07-30 and 2026-08-01 (07-31 deliberately not ingested) reported
`1,106,864 missing id(s)` — exactly the row count of the un-ingested day. It is now per
day and summed, with adjacency deciding:

```
[OK] binancef/BTCUSDT: 1,233,554 rows over 2 day(s) -> 0 missing id(s)
     (0 inside days, 0 across adjacent days) — REPORTED, never filled
[INFO] binancef/BTCUSDT: 1,106,864 further id(s) lie between NON-ADJACENT ingested days
     — that is the un-ingested range, a request choice, not missing data
```

The genuine case still WARNs: an injected 3-id in-day hole plus an 80-id gap across two
ADJACENT days reports 83 (`tests/test_check_ticks.py`). On the real 4-day mixed tree
(2020-01-01..03 + 2026-08-01) the phantom would have been **3,380,481,140**.

### 9.5 Honesty outputs that were wrong about their own frame

* `coverage_book_<venue>` was listed in the warned sentence as "NaN on all N archive
  bar(s)" while it was **0.0** — measured 48/48 non-NaN. 0.0 is a witness statement ("the
  leg was observed and was silent"), which on an archive day is a fabricated zero: the
  depth stream was never published. It is now **NaN** on archive bars, like the
  liquidation columns already were, and `coverage_liq_*` follows for the same reason (its
  trades∪depth witness would otherwise read ~1.0 next to an all-NaN `liq_count`). The
  attrs list is split into `book_columns_nan_on_archive_bars` and
  `quality_columns_nan_on_archive_bars`, and the repo's own test no longer special-cases
  the discrepancy — it asserts every named column is NaN.
* `attrs["history"]["fraction_of_minbtl"]` divided the **requested window** by MinBTL. A
  two-day frame asking for six years reported `2.4412` — numerically the same 244 % the
  docs quote for the whole 2,406-day archive. All four keys now name their basis
  (`_requested_window`, `_trade_derived`, `_book_derived`, and the unqualified one =
  shortest family), and `orderflow_smoke.py` prints both.
* An archive day that RESOLVED but contributed zero rows (ask for `coinbase`; the
  partition holds `binancef`) still lengthened `trade_derived_span_years`. Spans now count
  days that actually contributed rows; `days_resolved_without_rows` and `days_no_rows`
  make the difference visible, and the warning says so.
* The mandatory archive warning was skipped on a **cache hit**, and `cache=True` is the
  default.
* `volume_buckets` — the VPIN table, i.e. the trade-derived family the archive extends
  furthest — read the archive with no `source_code`, no archive block, no warning and no
  `vision_root` parameter. It now carries all five labels, per bucket.

### 9.6 Rails that only the CLI held

* `assert_out_root_is_separate` had exactly ONE call site (`main()`). `ingest_day` /
  `ingest_month` would write parquet, manifests and `_ledger.jsonl` inside `data/ticks/`,
  next to `levels.jsonl`. Both writers now assert it before anything is written —
  important because the day's own error handler would otherwise have written
  `FAILED-<date>.json` INTO the forbidden root.
* `--vendor-symbol` (what is downloaded) and `--venue`/`--symbol` (what is written into
  the `exchange`/`symbol` columns and the partition path) were never compared:
  `--vendor-symbol ETHUSDT --symbol BTCUSDT` wrote ETH rows into `binancef/BTCUSDT`, the
  partition `order_flow_bars` reads by default; the mirror wrote Binance rows as
  `bybit/ETHUSDT`, which breaks the exact-dedup argument that licenses M7. The instrument
  is now part of `ALLOWED_SCOPE`, and `ALLOWED_TARGET` registers where an allowlisted
  object may land.
* `--json` (all three branches) dropped the honest-limit sentences that `_report` prints
  on every human run. They now ride in the JSON.

### 9.7 Tests: the suite was not network-free, and said it was

Eight M7 tests in `tests/test_orderflow.py` passed `source=("local","hf","vision")` over
days with no local file, so `_hf_partitions` globbed `hf://datasets/...` live. Under a
real HTTP 429 they failed, surfacing through `pytest.warns` as `DID NOT WARN`.

Fixed with a `no_hub` fixture that stubs the Hub LISTING rather than dropping `"hf"` — the
precedence branch under test is exactly `hf_has or not want_vision`, and an empty Hub is
the hermetic way to exercise it. Proof: the whole suite with every non-loopback TCP
connect refused → **343 passed, 3 skipped** (the three being `test_vision_overlap.py`'s
deliberately-networked tests, which skip cleanly). With network available: **346 passed**
in 111 s, up from 331 in ~190 s.

### 9.8 Rejected

* *"`make orderflow-smoke` does not exit honestly."* It crashed on an `hf://` read
  (`ZSTD Decompression failure` on a 19-day multi-file scan; the same partition reads fine
  alone). That is a Hub transport failure on a path this diff does not touch —
  `source="auto"` never sets `want_vision`, and `scripts/orderflow_smoke.py` was unmodified
  by M7. Making the smoke degrade to a verdict on a transport error would convert an
  unreadable source into a printed number, which is the opposite of the rail. Left as is,
  and recorded here so it is not re-discovered as new.
* *The recorded store's 1,039 duplicate `trade_id` L3 FAIL* stays out of scope (§7). What
  WAS fixed is the doc arithmetic it distorts: DESIGN §0.7 now states that the 399,219-row
  join is against the DISTINCT recorded relation, and that a plain join yields 400,190 rows
  with 0 mismatches either way.
