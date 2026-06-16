# BTC-QUANT — Audit & Improvement Spec (for Claude Code)

**Purpose.** A repeatable audit of the `btc-quant` repository for *correctness, leakage,
cost-realism, validation rigor, and over-fitting discipline* — then targeted, verified
improvements. Run this with Claude Code from the repo root.

**How to use.**
1. Save this file at the repo root as `AUDIT.md`.
2. In Claude Code: `claude` → "Follow `AUDIT.md`. Start with Section 0, then produce the
   findings report in Section 11 BEFORE changing any code."
3. Reference by path, don't paste big files (Claude Code reads selectively).

**Honesty / scope note for Claude Code.** This spec was written without access to the
current repo. **Section 0 (inventory) is ground truth** — if the repo's actual contents
differ from any assumption below, trust the repo and adapt. Do NOT invent results, do NOT
report a metric you did not compute, and do NOT claim a module is "validated" without
showing the test that proves it. This is not financial advice; it is a code-quality and
statistical-methodology audit.

**Working discipline (matches the user's standing preference + the `quant-research-partner`
skill).** Validation over velocity. One module at a time. Produce a severity-ranked
findings report first; get sign-off before sweeping edits. Keep a running `AUDIT_LOG.md`
of what changed and why. For every claimed fix, show the before/after and the test that
demonstrates it.

---

## Section 0 — Inventory & reproducibility (do this first, change nothing)

- Map the repo: package layout, entry points, data sources/ingestion, feature pipeline,
  models, backtest/sim engine, options module, perp/futures module, dashboard, tests, CI.
- Record: Python version, dependency manifest (pyproject/requirements), lockfile presence,
  data provenance (live API vs cached vs vendored), and how results are reproduced.
- Verify it runs: install, run the test suite, run the smallest end-to-end path. Note
  anything that fails, is non-deterministic, or needs undocumented secrets/data.
- Output a one-page repo map + a "can a stranger reproduce the headline result?" verdict.

## Section 1 — Look-ahead & leakage (HIGHEST PRIORITY)

The single most common way a quant repo lies to itself. Check, with file:line evidence:

- **Future data in features.** No feature at time *t* uses information from > *t*
  (no centered rolling windows, no `shift(-k)`, no full-series resample that leaks).
- **Label/return overlap.** If labels are k-step forward returns, training rows whose
  label window overlaps the test set must be **purged**, with an **embargo** after each
  test fold (López de Prado, AFML Ch. 7). Confirm this is implemented, not assumed.
- **Fit-on-train-only.** Scalers/normalizers/PCA/Box-Cox/winsorization/feature-selection
  are fit on the *training* slice only, never on the full sample. Grep for `.fit(` on
  whole-dataset objects.
- **Target leakage.** No feature is a transform of the label or of contemporaneous future
  bars; no "winsorize using global quantiles" computed over the test period.
- **Resampling/indicator warm-up.** Indicators with internal state (EWMA, Kalman, filters)
  are computed causally; no `bfill`; warm-up rows excluded from scoring.
- **Survivorship / point-in-time.** Any cross-sectional or universe logic uses
  point-in-time membership, not today's listing.
- Add **leakage unit tests** (e.g., shifting the label by +1 should destroy IC; a
  future-shuffled target should yield ~0 IC). Make these part of CI.

## Section 2 — Mathematical correctness (per module)

Audit each quantitative routine against primary references; fix or flag:

- **Options vol surface (SVI/SSVI).** Calendar + butterfly arbitrage-free constraints
  enforced (Gatheral–Jacquier); parameter bounds; fit residuals reported. Breeden–Litzenberger
  risk-neutral density must be **non-negative** and integrate to ~1 — check the second-derivative
  estimator and smoothing; report where density goes negative.
- **Greeks (Black-76).** Re-verify against a known reference (the user notes these were
  previously audited — re-run the check, don't assume). Units, forward vs spot, day-count.
- **DVOL / vol replication.** Confirm the static replication weights and truncation match
  the model-free implied-variance formula; document the truncation error.
- **Perp funding / basis carry.** Funding accrual timing (8h stamps), sign conventions,
  basis = perp − index, annualization. The delta-neutral carry PnL must net funding,
  fees, and rebalancing cost.
- **Any Hurst / fractional-diff / entropy estimators.** Short-window bias (R/S upward-biased;
  DFA needs long series); FFD weights `w_k = -w_{k-1}(d-k+1)/k` and the ADF-optimal `d`
  choice; treat short-window estimates as coarse, not precise.
- **Robust stats.** MAD scaling constants (0.6745 / 1.4826) correct; correlations use
  enough points; no divide-by-zero / NaN propagation.

## Section 3 — Cost & execution realism

- Fees modeled per side (Binance USDⓈ-M maker ≈ 0.02% / taker ≈ 0.05%, BNB discount),
  **funding** for held perp positions, realistic **slippage**, and borrow where relevant.
- Headline PnL/Sharpe must be reported **net of all costs**; show gross vs net side by side.
- Fills are realistic (no fills at the exact extreme; limit orders may not fill; next-bar
  execution for signals computed on close).
- Sweep holding horizon and show where net edge is maximized vs destroyed by cost (the
  weak-edge-vs-cost problem: a small per-trade gross edge can be entirely eaten by fees).

## Section 4 — Validation rigor

- **Out-of-sample lockbox.** A final slice touched once; confirm it isn't being iterated on.
- **CPCV** (combinatorial purged cross-validation) producing a *distribution* of metrics,
  not a single path; with purge + embargo (Section 1).
- **Deflated Sharpe Ratio + PBO** (Bailey–López de Prado): discount the best Sharpe by the
  number of configurations tried and for non-normality; report Probability of Backtest
  Overfitting and Minimum Backtest Length. Count **all** trials/configs that were searched.
- **IC with honest error bars.** Newey–West / HAC standard errors for autocorrelated
  overlapping returns; bootstrap CIs; report the lower CI bound, not just the point estimate.
- **Parameter stability.** Edge should be a plateau across nearby parameters, not a spike.

## Section 5 — Over-fitting & model-complexity discipline

- Prefer the simplest model that works; justify every added feature/parameter by its
  *out-of-sample* contribution (DeMiguel: 1/N often beats optimized weights OOS).
- Flag any model with high parameter count relative to effective sample size, any in-sample
  weight optimization on correlated signals, and any "we tried N variants and kept the best"
  without multiple-testing correction.
- Report feature **decorrelation / effective breadth**: pairwise correlation of signals;
  highly correlated features inflate apparent breadth.

## Section 6 — Sizing & risk

- Position sizing: volatility targeting (e.g., EWMA/GARCH vol forecast → size ∝ 1/σ) and/or
  **fractional Kelly** (quarter-Kelly default for crypto); never full Kelly on an uncertain edge.
- Drawdown control / circuit breaker; position and leverage limits; funding-stamp awareness.
- Expectancy (avg R per trade) and tail risk reported — not just win rate.

## Section 7 — Statistical hygiene

- Stationarity checks where models assume it; document non-stationary inputs.
- Multiple-comparisons awareness across everything searched.
- No p-hacking: pre-register the hypothesis per experiment; the `quant-research-partner`
  hypothesis-first + sensitivity-case + run-log discipline applies.

## Section 8 — Engineering / MLOps

- Determinism: seeds fixed; runs reproducible; document any irreducible nondeterminism.
- Config management (no magic numbers buried in code); data versioning/caching with provenance.
- Test coverage for the math (Section 2) and for leakage (Section 1) as first-class CI gates.
- Structured logging + a run-log so every reported number traces to a commit + config.

## Section 9 — Dashboard / reporting correctness

- Every displayed metric is computed the way its label claims (no mislabeled or stale values).
- Live vs historical consistency (no look-ahead in the live path).
- Clearly separate **proven** (OOS-validated) from **decorative/experimental** components.

## Section 10 — (Optional) targeted improvements to propose

Only after the findings report, and only where they raise *net-of-cost OOS* performance or
correctness — propose, with a test each:
- EWMA/GARCH vol-targeting for sizing.
- Cost-aware no-trade band (act only where expected edge > expected cost).
- Genuine, *decorrelated* features (e.g., funding-rate signal for perps) — must pass OOS validation.
- Replace any weak proxy with a better-grounded estimator; cut redundant/decorative components.

## Section 11 — Required deliverables

1. **Repo map** (Section 0) + reproducibility verdict.
2. **Findings report**, severity-ranked (Critical / High / Medium / Low), each with
   file:line, why it's wrong, and the fix — leakage and cost-realism findings first.
3. **Leakage & math test additions** (the tests, runnable in CI).
4. A short **"what is actually validated vs not"** statement for the whole repo.
5. `AUDIT_LOG.md` capturing every change and its justification.

> Reminder: report only what you computed. If a check can't be run (missing data/secrets),
> say so explicitly rather than guessing. Iterate module by module; confirm before large edits.
