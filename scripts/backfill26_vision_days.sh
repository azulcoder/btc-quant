#!/bin/bash
# One-shot backfill of the 26 published-but-never-ingested Vision days found by the
# PREREG-markout-001 item-1 census (2026-08-08). These are NOT part of the 295-day
# ENOSPC hole (2025-10-08..2026-07-29, docs/STATUS.md §2) — they are scattered days
# the venue published but the original migration never picked up, a defect that was
# recorded nowhere until the census cross-joined the Vision listing against the store.
#
# The first day doubles as the `--date` happy-path control DESIGN §24 promised.
# Result of the actual run: 26 ok / 0 failed; 28,683,093 aggTrades rows; all 26
# verified present on HF afterwards via parquet_file_metadata row counts [DIUKUR].
# Ledger rows: reports/vision-migration.jsonl (append-only), state=migrated_no_local.
#
# Vision archive is admissible per §0.7: same venue/stream/ID space, exact-key
# matched, one source per day-file — and it never counts toward sec_readiness.
cd "$(dirname "$0")/.." || exit 1
DAYS="2020-04-14 2020-04-18 2020-05-04 2020-05-06 2020-05-13 2020-05-27 2020-05-29 2020-11-03 2020-11-05 2020-11-09 2020-11-12 2020-11-21 2020-11-25 2020-11-27 2020-11-30 2022-08-28 2022-08-29 2022-08-30 2022-09-01 2022-09-10 2022-09-12 2022-09-13 2022-10-29 2022-11-07 2022-11-14 2023-05-09"
LOG="${TMPDIR:-/tmp}/backfill26.log"
ok=0; fail=0
for d in $DAYS; do
  if python3 scripts/vision_to_hf.py --date "$d" >> "$LOG" 2>&1; then
    ok=$((ok+1)); echo "[$ok ok / $fail fail] $d done"
  else
    fail=$((fail+1)); echo "[$ok ok / $fail fail] $d FAILED (see $LOG)"
  fi
done
echo "BACKFILL26 COMPLETE: $ok ok, $fail failed"
