# Deflated-Sharpe trial-variance convention — run-log (decision aid)

Research only. Not financial advice. A backtest is not a forecast. This log is a
**non-destructive** decision aid: the production DSR convention stays **A** (M6 —
empirical cross-strategy variance) and is UNCHANGED. `compare.py` / `backtest.py` /
`risk.py` are not touched. `scripts/dsr_ab.py` only *reports* B1/B2 beside A.

Reproduce:

```
python3 scripts/dsr_ab.py --research --start 2018-01-01     # N=8 (research board)
python3 scripts/dsr_ab.py            --start 2018-01-01     # N=5 (public board)
python3 scripts/dsr_ab.py --research --json                 # machine-readable
pytest tests/test_dsr_ab.py -q                              # 5 deterministic teeth
```

---

## The question (pre-registered)

The M6 leaderboard deflates every strategy's Sharpe against the expected maximum of
`N` skill-less trials (Bailey & López de Prado 2014). The benchmark is

    sr0(V, N) = √V · ( (1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e)) ),   γ = 0.5772156649015329
    DSR_i     = PSR(SR_i ; sr0) = Φ( (SR_i − sr0)·√(n_i−1) / √(1 − skew_i·SR_i + (kurt_i−1)/4·SR_i²) )

Under convention **A** the trial variance `V` is a **single shared scalar** — the
empirical `ddof=1` variance of the `N` ranked strategies' OOS per-period Sharpes
(`compare._empirical_var_sr`). That scalar enters `sr0` for *every* strategy, so a
strategy's leaderboard DSR is **coupled** to every peer's realized Sharpe.

This coupling is not hypothetical — it was surfaced by audit **M9** (delta-neutral
pairs P&L). Making `pairs_coint` / `pairs_ou` net the ETH leg's price move dropped the
two pairs' OOS Sharpes; because their SRs are two of the `N` samples in the shared `V`,
the empirical cross-trial variance rose (`V 0.000602 → 0.000991`) and **every other
strategy's leaderboard DSR moved down** — with the peers' own P&L byte-identical:

    tsmom      0.82 → 0.64      tsmom_dir  0.75 → 0.56      buy_and_hold 0.61 → 0.41
    tsmom_ls   0.58 → 0.37      ma_trend   0.53 → 0.34      (research board, N=8)

Peer OOS SR / MaxDD unchanged, no rank change, no upward flip. tsmom's DSR fell a
third of a point because of a *pairs* code fix. **Is a shared-V, peer-coupled DSR the
right `V` for a board of distinct strategies?** This log lays the three conventions
side by side on the real leaderboard so the choice can be made on evidence.

---

## The three conventions (they differ ONLY in `V`; `N` is identical for all)

| | `V` fed to `sr0(V,N)` | coupling | literature | what it ASSUMES |
|---|---|---|---|---|
| **A** (production) | `var(SR_1..SR_N, ddof=1)` — one shared scalar | **COUPLED** | Bailey & LdP 2014 (as written) | the `N` trials are a **selection**; their realized dispersion *measures the cherry-picking reach* — how far the best of `N` could stray by luck |
| **B1** | `1/n_periods` — per strategy | decoupled | Lo 2002 (asymptotic `Var(ŜR) → 1/n` under `SR=0`, iid-normal) | each trial's `SR` is a draw from the **null sampling distribution** (skill-less, Gaussian) |
| **B2** | `(1 − skew_i·SR_i + (kurt_i−1)/4·SR_i²)/(n_i−1)` — per strategy | decoupled | Lo 2002 / Mertens 2002 (skew-kurt-corrected `ŜR` variance — the *same* numerator the PSR denominator uses) | each trial's `SR` is a draw from **its own** finite-sample sampling distribution, plug-in higher moments |

The distinction is what "the `N` trials" *are*. **A** reads them as `N` overfitting
variants of essentially one search: the spread of their realized Sharpes IS the
selection reach, exactly the quantity Bailey-LdP's `sr0` wants. **B** reads each trial
as an independent draw from a skill-less null: `V` is that draw's theoretical sampling
variance, which depends only on the strategy's own length (B1) or own moments (B2). In
both B variants the "how many things did I try" signal survives — it is still carried
by `N` through the expected-max term `(1−γ)Φ⁻¹(1−1/N)+γΦ⁻¹(1−1/(Ne))`; only the
per-trial dispersion `√V` is decoupled.

---

## The real numbers (verified independently — see "Verification" below)

`scripts/dsr_ab.py`, BTC-USD 1d, 2018-01-01 → 2026-07-12, 3115 bars, 5 folds, cost
10.0+2.0 bps/side. `n` (OOS periods) = 2596 for every strategy. `V_B1 = 1/n = 0.000385`
for all. Column **A reproduces the production leaderboard by construction** (same
`risk.deflated_sharpe_ratio`, same shared `V`, same `N`).

### RESEARCH board — N=8, shared `V_A = 0.000990`, `sr0_A = 0.0459`

| strategy | OOS SR | **DSR_A** [prod] | DSR_B1 [1/n] | DSR_B2 [own] | skew | kurt | V_B2 |
|---|---|---|---|---|---|---|---|
| tsmom | 1.01 | **0.6439** | 0.8990 | 0.9069 | 1.35 | 16.65 | 0.000362 |
| tsmom_voltarget | 1.01 | **0.6439** | 0.8990 | 0.9069 | 1.35 | 16.65 | 0.000362 |
| tsmom_dir | 0.93 | **0.5550** | 0.8478 | 0.8515 | 0.57 | 11.80 | 0.000377 |
| buy_and_hold | 0.79 | **0.4140** | 0.7428 | 0.7373 | −0.43 | 14.04 | 0.000394 |
| tsmom_ls | 0.76 | **0.3719** | 0.7157 | 0.7255 | 1.11 | 13.01 | 0.000370 |
| ma_trend_filter | 0.72 | **0.3377** | 0.6719 | 0.6624 | −0.72 | 28.01 | 0.000399 |
| pairs_ou | −0.18 | **0.0023** | 0.0255 | 0.0262 | −1.95 | 80.59 | 0.000379 |
| pairs_coint | −0.59 | **0.0000** | 0.0000 | 0.0001 | −22.11 | 838.23 | 0.000199 |

### PUBLIC board — N=5, shared `V_A = 0.001124`, `sr0_A = 0.0400`

| strategy | OOS SR | **DSR_A** [prod] | DSR_B1 [1/n] | DSR_B2 [own] |
|---|---|---|---|---|
| tsmom | 1.01 | **0.7516** | 0.9395 | 0.9439 |
| buy_and_hold | 0.79 | **0.5321** | 0.8200 | 0.8163 |
| tsmom_ls | 0.76 | **0.4921** | 0.8000 | 0.8066 |
| ma_trend_filter | 0.72 | **0.4511** | 0.7601 | 0.7534 |
| pairs_coint | −0.59 | **0.0000** | 0.0001 | 0.0004 |

`DSR_A` matches the live `compare.py` public leaderboard exactly at its 2dp print
(tsmom 0.75, b&h 0.53, tsmom_ls 0.49, ma_trend 0.45, pairs 0.00; `V=0.001124`, `N=5`).

**Reading the columns.** The rank order is identical under all three — the coupling
does not reshuffle the board, it *rescales the confidence*. But the level gap is large:
tsmom is **0.64 under A vs 0.91 under B** (research). Under A tsmom carries the full
selection penalty of a board whose realized Sharpes span −0.59…+1.01 (a wide empirical
`√V ≈ 0.031`); under B the same tsmom is judged against its own ~2600-observation
sampling error (`√V ≈ 0.020`) and looks far more distinguishable from luck. Neither is
"more correct" in the abstract — they answer different questions (below).

---

## Coupling sensitivity — the structural difference, measured

Perturb `pairs_coint`'s OOS Sharpe by ×1.5 and ×0.5 (a genuine returns-level additive
drift: shift every return by `(scale−1)·mean`, so `SR' = scale·SR` exactly while
`n, σ, skew, kurt` are held fixed), recompute all three columns, and measure how far
**every other strategy's** DSR moves (research board, N=8):

```
scale ×1.5:  pairs_coint SR −0.03087 → −0.04630   |   shared V_A 0.000990 → 0.001284
  peer                 ΔDSR_A       ΔDSR_B1     ΔDSR_B2
  tsmom               −1.31e-01     +0.0e+00    +0.0e+00
  tsmom_dir           −1.30e-01     +0.0e+00    +0.0e+00
  buy_and_hold        −1.19e-01     +0.0e+00    +0.0e+00
  tsmom_ls            −1.17e-01     +0.0e+00    +0.0e+00
  ma_trend_filter     −1.07e-01     +0.0e+00    +0.0e+00
  pairs_ou            −1.49e-03     +0.0e+00    +0.0e+00
  → max |Δ|            1.30e-01                  0.00e+00

scale ×0.5:  pairs_coint SR −0.03087 → −0.01543   |   shared V_A 0.000990 → 0.000755
  → max |Δ|            1.18e-01                  0.00e+00   (all peer B1/B2 deltas ≡ 0)
```

Under **A**, moving one peer's Sharpe shifts every other strategy's DSR by **up to
0.13** — a leaderboard DSR is not a property of that strategy alone. Under **B1/B2**
every peer delta is **bit-identical zero** (`< 1e-12`, the tool asserts it), because
each strategy's `V` reads only its own returns. This is exactly the M9 event
generalized: fix the pairs, move tsmom.

---

## The honest tradeoff

- **A is the literal Bailey-LdP prescription — when the `N` trials are overfitting
  variants of one strategy.** In that setting the empirical dispersion of the trial
  Sharpes *is* the selection reach: you tried `N` knobs on one idea, the spread of what
  they produced tells you how high a skill-less max could reach, and deflating by it is
  the whole point. `tsmom` and `tsmom_voltarget` and `tsmom_dir` on this board are
  arguably that case (near-duplicate momentum variants — M6/B1 already found
  `tsmom_voltarget` corr 1.00 to `tsmom`).

- **For a board of genuinely DISTINCT edges, A conflates two things.** The empirical
  cross-strategy variance mixes *skill-spread* (buy-and-hold, momentum, and a
  mean-reverting pairs trade genuinely have different true Sharpes) with
  *selection-noise* (finite-sample luck). When a distinct, unrelated strategy (pairs)
  changes, it drags the momentum strategies' confidence with it, which is hard to
  defend as a statement about momentum. That is the M9 discomfort.

- **B decouples but discards the empirical "how wide is my search" dispersion signal.**
  B1/B2 do not stop deflating for the count of trials — `N` still raises `sr0` through
  the expected-max term — but they replace the *realized* dispersion with a null/own
  sampling variance. If the board really is a wide overfitting search, B under-deflates.

- **Selection is already guarded separately.** PBO (CSCV, currently 0.63 → "ranking is
  essentially noise") and MinBTL (2.70 yrs vs 8.5 yrs of data) both live in the same
  `compare.py` output and speak directly to overfitting/selection. So the DSR is not
  the only line of defense against cherry-picking — which weakens the case that DSR's
  `V` must carry the selection-reach signal by itself.

---

## Recommendation frame — azul's call

**My lean, presented as your decision, not a decree:** I would lean **B2** for the
public board of distinct strategies, because the board's members are advertised as
*different edges*, and a DSR that changes when an unrelated pairs trade is refactored is
a confusing thing to publish — B2 makes each strategy's headline number a property of
that strategy's own returns (own length, own skew/kurt), which is what a reader assumes
it is, while `N` still honestly penalizes the trial count and PBO/MinBTL still guard
selection. B2 over B1 because B2 uses the strategy's actual fat-tailed moments (crypto
kurtosis 12–840 here) rather than the Gaussian `1/n` null, and it reuses the *exact*
variance already inside the PSR denominator, so it is the most internally consistent.
The counter-argument I would want you to weigh: the momentum cluster (`tsmom*`) really
is a near-duplicate search, and for *that* subset A's empirical dispersion is the
textbook-correct selection penalty — so if you read the board primarily as "how many
momentum knobs did I turn," A is defensible and B under-deflates. **This is a
methodology-preference call, not a correctness call — both are literature-backed. You
decide.**

Switching is cheap and reversible: it is a **one-line change of the `V` source** in
`compare.py` (swap `_empirical_var_sr(srs)` for the per-strategy `(1 − skew·SR +
(kurt−1)/4·SR²)/(n−1)`), the matching one-line mirror in `dashboard/.../quant.js`, and a
parity re-pin. No strategy P&L changes — **only the displayed DSR**. The historical
record stays intact: this run-log, the M6/M9 AUDIT_LOG entries, and every prior
`RESEARCH-*` log keep their **A-numbers** as the as-of-then record, so nothing is
rewritten if you switch.

---

## Pre-register — IF azul chooses B

Bounded, reversible follow-up (no new research, only the displayed `V`):

1. `compare.py` — change the leaderboard `V` source (one line: `_empirical_var_sr` →
   per-strategy B2 own-variance); keep `N` = strategies ranked; keep the `1/n_periods`
   null fallback + caveat for the `<2` finite-SR case.
2. `dashboard/.../quant.js` — mirror the same `V` in `deflatedSharpe`; keep semantics
   identical (N=1 case, `bias=False` kurtosis).
3. **Parity re-pin** — regenerate the unsaturated DSR parity pins; `check_parity` must
   stay green (currently 63 fields, worst |Δ| = 1.63e-7).
4. **AUDIT_LOG M6-amendment entry** — before/after leaderboard DSR table (A→B),
   explicit note that rank order and every KILL verdict are re-checked for no upward
   flip, and that P&L is byte-identical.
5. Gates that must not regress: pytest, `check_terminal` 46 groups, `check_parity` 63
   fields, `node --check` on the mirror.

Kill-verdict continuity must be re-verified on `--research`: B *reduces* deflation
relative to A here (own-variance `√V ≈ 0.020` < empirical `√V ≈ 0.031`), so DSRs move
**up** — the pre-registered check is that **no Tier-B/Tharp KILL flips to a PASS** at
its 0.95 bar (all current KILLs sit ≤ 0.34 on the folds-DSR, far below 0.95, so headroom
is large — but re-run and confirm, do not assume).

---

## Verification (independent — this log was not written on the tool's word)

Every number above was recomputed from scratch in an independent scratchpad using only
`scipy.stats.norm.ppf/cdf` and the raw closed forms (no import of `dsr_ab`'s functions),
from the printed per-strategy `SR/n/skew/kurt`:

- **`sr0` and all three DSR columns** for `tsmom`, `buy_and_hold`, `pairs_coint`,
  `tsmom_dir`, `ma_trend_filter` (research + public) matched the tool to **0.00e+00**
  (well under the 1e-6 gate). `V_A`, `V_B1 = 1/n`, `V_B2` re-derived independently, all
  match.
- **Coupling** independently reproduced: perturbing `pairs_coint` shifts `tsmom`'s
  `DSR_A` by −0.131 (×1.5) / +0.106 (×0.5) while `DSR_B1/B2` move by exactly 0 — matched
  the tool's coupling block to 0.00e+00.
- **Production tie**: `compare.py --start 2018-01-01` public leaderboard prints tsmom
  0.75 / b&h 0.53 / tsmom_ls 0.49 / ma_trend 0.45 / pairs 0.00 at `V=0.001124, N=5` —
  identical to column A (full-precision equality holds by construction: same function,
  same `V`, same `N`, same walk-forward stats).
- **No math defect found** in `scripts/dsr_ab.py` or `tests/test_dsr_ab.py` — no edit
  was needed. `pytest` 183 (178 baseline + 5 new), `check_terminal` 46 groups,
  `check_parity` 63 fields — all green; no JS changed.

References: Bailey & López de Prado 2014 (Deflated Sharpe / expected-max-of-N benchmark,
*JPM* 40(5); SSRN 2460551); Lo 2002 (Sharpe-ratio estimator variance, *FAJ*); Mertens
2002 (skew-kurtosis-corrected Sharpe variance — the form the repo PSR denominator uses).
