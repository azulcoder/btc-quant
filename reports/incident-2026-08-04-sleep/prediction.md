# Pre-registration — host-sleep experiment, 2026-08-04

Written **before** `disablesleep` is applied and before any post-change data exists. Same rule
as every other pre-registration in this repo: **the numbers below are binding.** If they miss,
that is the finding. Thresholds are not revised after seeing the result.

**Hypothesis (falsifiable).** macOS Maintenance Sleep is the dominant cause of the collector's
gap bursts and of the ~45 % of seconds with no book data. Removing host sleep removes most of
both.

**Baseline it is measured against:** `baseline.md`, same directory.
Burst rate during the dense regime: **40 bursts / 11 h = 3.64 per hour**, so a null result
(nothing changes) predicts **≈22 bursts in 6 hours**.

---

## Measurement procedure — fixed now, so it cannot be chosen later

- **Window:** the 6 hours beginning at the moment `pmset -g | grep SleepDisabled` first reports
  `1`. Start timestamp recorded before measuring.
- **Coverage:** unique seconds with ≥1 `depth_snapshots` row per venue, divided by 21,600.
  From **stored rows only** — `data/ticks/*.duckdb`, live file by byte copy. Never
  `gaps.jsonl`, never `/health`.
- **Bursts:** `gaps.jsonl` events in the window, collapsed at a **30 s** join — identical to the
  baseline method.
- **Host power:** count of `Entering Sleep state` in `pmset -g log` inside the window.

---

## Predictions — binding

| # | quantity | predicted |
|---|---|---|
| P1 | gap bursts in 6 h | **≤ 3** (vs ≈22 under the null) |
| P2 | okx depth coverage | **≥ 90 %** |
| P3 | binancef depth coverage | **≥ 90 %** |
| P4 | bybit depth coverage | **≥ 85 %** (its best hour ever measured is 91.0 %, so 90 % is not a fair bar) |
| P5 | `Entering Sleep state` events in window | **0** |

P5 is the control. **If P5 fails, the whole experiment is void** — the setting did not take, and
nothing about P1–P4 can be attributed either way.

---

## Refutation — what makes candidate #1 lose and #3 (home network) win

Given P5 holds (host genuinely never slept), candidate #1 is **REFUTED** if **either**:

- **R1 — bursts ≥ 12 in 6 h.** More than half the baseline rate survives the removal of its
  alleged cause, so sleep was not dominant.
- **R2 — any venue's depth coverage < 70 %.** Substantial loss persists with the host awake.

Either one alone is sufficient. If R1 or R2 fires, the ranking flips to **#3 home network /
transport** and the next measurement is DNS + TLS failure timing, not power.

## The ambiguous band — named now, not after the fact

**4–11 bursts, or coverage 70–90 %** confirms nothing and refutes nothing. It means sleep was
*a* cause but not the only one. The honest response is a **longer window (24 h)**, not a
re-reading of these thresholds. Writing this down now is the point: without it, any middling
result gets narrated toward whichever conclusion is already preferred.

---

## What this experiment cannot settle

- **Whether the two causes are separable at all.** Host sleep and a home-network drop both cut
  every leg simultaneously and leave the same signature. This test can only show whether
  *removing one* removes the symptom — it cannot decompose a mixture.
- **Anything about the historical blackouts** (2026-07-23 … 08-02). Those show a *selective*
  pattern (bybit + okx dead, binancef alive) that host sleep cannot produce. Handled separately
  under the closure-bug check; this experiment says nothing about them.
- **True downtime per disconnect**, until the collector log carries timestamps.
