---
name: prereg-research
description: Use when starting ANY new measurement, research question, or candidate evaluation in btc-quant. Enforces the repo's declaration-first procedure, positive controls, look counter, and labels — the standard that has caught every wrong number this project produced.
---

# Pre-registered research procedure (btc-quant)

Work order for every new measurement or research question. Skipping a step is how
each entry in the instrument-blindness ledger (STRATEGY.md §6) happened.

## Before running anything

1. **Declare in the target doc first**: the question, the method, thresholds/criteria,
   and the interpretation of each possible outcome — written BEFORE any number exists.
   Free parameters (windows, block counts, grids) are named and either fixed by a cited
   convention or covered by a sensitivity rule (verdict must hold across the set, else
   ABSTAIN). A verdict that flips on a free methodological choice is NOT a verdict
   (tie-break rail, STRATEGY.md §6).
2. **Plan the positive control**: the first number out of a new instrument is a CONTROL,
   not a result — it must reproduce a known value via an independent route. Anchors pin
   to CLOSED data only (a live partial bar broke PC1 once; PREREG-pbo-null-001 amendment).
3. **Plan the negative control for any verifier** (class I): test it on a case known to
   PASS, not only on known failures — bad precision destroys correct work.

## While running

4. Conclusions print BESIDE the numbers that produced them, same block (this placement
   caught a hard-coded wrong conclusion and a float-division bug within seconds).
5. Check the class-H trap list in CLAUDE.md before trusting any library call
   (DuckDB `/`, CAST rounding, session-TZ strftime, get_ohlcv 300-bar default,
   paginated API caps that differ per endpoint).
6. **LockBox (`2026-08-05 01:00Z` onward) is never read.** Exploration slice is frozen
   at `2026-07-05..2026-08-03`.

## After running

7. Label every number: [DIUKUR] / [DISIMPULKAN] / [DIASUMSIKAN] / [UNVERIFIED].
8. **Update the Look counter** in docs/EDA-microstructure-001.md — every look counted,
   trial classification argued (not assumed), totals never reduced retroactively.
9. Write the mandatory "what I could not measure" section.
10. End with plus / minus / recommendation. Update docs/STATUS.md if any standing state
    changed. Superseded claims get struck through with a pointer, never rewritten.
11. Commit with NO AI attribution (DEVELOPMENT.md:61), push, and update the repo-scope
    memory if operational state changed.

## Where records live

`docs/STATUS.md` (current-state index, start here) · `docs/EDA-*.md` (measurements) ·
`docs/PREREG-*.md` (declared-before-run) · `docs/PLAN-*.md` (not-yet-run designs) ·
`reports/*.json[l]` (machine-checked damage/defect/checkpoint records) ·
`STRATEGY.md` §6 (rails + blindness ledger) · `make gate` (the green/red answer).
