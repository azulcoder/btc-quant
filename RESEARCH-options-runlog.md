# Options-deepening run-log — pre-registered, descriptive-only

Research only. Not financial advice. These are **structural / descriptive** analytics of option
market structure — never trade signals. One keyless venue (Deribit), one public call
(`get_book_summary_by_currency`), MARK greeks (`mark_iv` only — no per-contract bid/ask IV). Each
panel below is pre-registered *before* building: what it claims a reader can take from it, how we
would know it is misleading, the keep/reject call, and how it is validated. Same discipline as
[RESEARCH-partB-runlog.md](RESEARCH-partB-runlog.md). A panel that cannot be framed honestly as
descriptive context does not ship.

Conventions (so a quant can audit): **Black-76**, forward `F = underlying_price`, `r = interest_rate`
if present else `0` (BTC ≈ 0), `σ = mark_iv / 100`. `d1 = [ln(F/K) + ½σ²T]/(σ√T)`, `d2 = d1 − σ√T`,
`T` = ACT/365 to 08:00 UTC expiry. φ = standard-normal pdf, Φ = cdf.

---

## P1 — Open interest by strike + max-pain

**What a reader can take from it.** Where call/put open interest is clustered; the *max-pain*
settlement strike = `argmin_K [ Σ_calls max(F−K,0)·OI + Σ_puts max(K−F,0)·OI ]` (the strike that
minimizes total intrinsic paid to option holders at settlement); the put/call OI ratio. Per the
nearest sizeable expiry.

**Honest limit / literature.** Max-pain is a **positioning descriptor, not a forecast**. Equity
"pinning" near expiry has some empirical support (Ni, Pearson & Poteshman 2005) but is driven by
delta-hedging/manipulation mechanics and is weak; for BTC there is **no credible evidence** that
price gravitates to max-pain. We build it as *where OI sits*, and the caption must NOT imply a magnet
or price target.

**How we'd know it's misleading.** If the caption (or a reader) treats max-pain as a price the
market will move toward. Mitigation: `DESCRIPTIVE · positioning` tag + explicit "not predictive,
not a magnet" wording.

**Verdict.** KEEP — pure OI×intrinsic, no greeks, honestly framed.

---

## P2 — Black-76 greeks surface (delta / gamma / vega by strike)

**What a reader can take from it.** The shape of delta/gamma/vega across strikes for an expiry —
descriptive surface structure (where gamma/vega are largest, how delta rolls across moneyness).

**Honest limit.** Gamma and theta **blow up as T→0**; deep-OTM greeks are dominated by IV noise.
Near-expiry and deep-wing values are not meaningful. Handling: restrict to expiries with **T ≥ ~1
day** and contracts with **|delta| ∈ [0.05, 0.95]** (the smile's wing discipline); never render the
T→0 spike. MARK greeks only (`mark_iv`; no bid/ask IV). One venue.

**How we'd know it's misleading.** If a singular near-expiry gamma spike or a noisy deep-wing value
is rendered as if meaningful. Mitigation: the T/|delta| filters above, stated in the caption.

**Verdict.** KEEP **iff it passes the Deribit greeks validation below.** If delta/gamma/vega do not
agree with Deribit's own greeks within tolerance, this panel (and P3) do **not** ship.

**Math.** Black (1976). call δ = Φ(d1), put δ = Φ(d1)−1; γ = φ(d1)/(F·σ·√T); vega = F·φ(d1)·√T·0.01
(per 1 vol-point). (r=0 in the dashboard; the validation quantifies that approximation vs Deribit's
`interest_rate`.)

---

## P3 — Unsigned gamma concentration by strike

**Definition (written out).** `GC(K) = Σ_{contracts at K} |gamma| · open_interest` — **gamma density
from open interest.** This is **NOT dealer positioning**: who is long vs short gamma is unknowable
from any keyless public feed, so there is **no signed GEX and no flip / pin level** here.

**What a reader can take from it.** Where option gamma is densest across strikes — structural context.

**How we'd know it's misleading.** If read as support/resistance, a price magnet, or a "gamma flip"
level. Mitigation: `DESCRIPTIVE · structure` tag + a loud caveat explicitly forbidding that reading
and stating the dealer sign is unknown.

**Verdict.** KEEP (gated on the same greeks validation as P2) — the honest substitute for GEX.

---

## REJECTED — signed dealer GEX / gamma-flip level

Gamma is computable, but **signed** dealer gamma exposure and any "flip level" require knowing dealer
positioning (who is short vs long gamma). **No keyless public feed provides this**; retail GEX simply
*assumes* dealers are short gamma, and the resulting flip level is an artifact of that unverifiable
assumption that reads as support/resistance. It cannot be framed as honest descriptive context, so it
is **not shipped** — logged here as a deliberate rejection (the credibility statement). GEX is a
practitioner construct (e.g. SqueezeMetrics) without peer-reviewed validation; we decline to imply a
directional claim the data can't support. We compete on methodology integrity on one keyless venue,
not on coverage breadth (cf. multi-venue paid-feed commercial tools).

---

## Greeks validation vs Deribit ticker (mandatory gate)

Independent ground truth — desks never trust self-computed greeks unvalidated. One-off local
harness (`/tmp`, untracked; NOT extra live dashboard calls): pull a spread of contracts via Deribit
`get_ticker`, feed each its own `mark_iv` + `interest_rate` + `underlying_price` into our Black-76,
and compare delta/gamma/vega against Deribit's reported greeks, reconciling unit conventions
explicitly. **Gate:** P2 + P3 ship only if agreement is within tolerance.

**Results** (`/tmp/validate_greeks.py`, 12 contracts across 4 expiries × moneyness, Deribit
`public/ticker`, T aligned to each quote's `timestamp`, fed Deribit's own `mark_iv` + `interest_rate`):

| greek | agreement | tol | verdict |
|---|---|---|---|
| **delta** | max\|mine − Deribit\| = **9.1e-06** | < 5e-3 | ✅ essentially exact |
| **vega** | mean ratio **1.0002**, max\|r−1\| **1.8e-03** | < 2% | ✅ 0.2% |
| **gamma** | mean ratio **0.9913**, max\|r−1\| **1.7e-02** (near-ATM) | < 3% | ✅ within rounding |

Convention reconciliation: Deribit's `delta` is the standard dimensionless delta (calls 0..1, puts
−1..0) and matched directly; `vega` is per-1-vol-point in USD and matched our `F·φ(d1)·√T·0.01`;
`gamma` is per-$1 and matched our `φ(d1)/(F·σ·√T)`. **No unit factor needed** — same conventions.
Deribit publishes `gamma` rounded to ~5 decimals, so only near-ATM strikes (|gamma| > 2e-4) carry
enough precision for a ratio (n=2 here: 0.983, 1.000); the deep-wing "mismatches" were pure rounding
(`deribit_gamma` = 0.00000–0.00001), **not** a convention error. Gamma's correctness also follows
analytically: it shares `d1` and `φ(d1)` with the validated delta and vega, so a correct delta+vega
forces a correct gamma. **GATE: PASS — P2 + P3 ship.** (Dashboard uses r=0; the harness used Deribit's
`interest_rate` ≈ 0, and delta still matched to 9e-6, confirming the r=0 approximation is negligible.)

---

## JS↔Python parity (fixed synthetic chain, `/tmp/opt_parity*`)

`features.{black76_greeks,max_pain,gamma_concentration}` (Python) vs `Q.{black76Greeks,maxPain,
gammaConcentration}` (quant.js — the requireable JS mirror; app.js renders from it):
**max_pain exact (Δ=0), gamma_concentration exact (2.6e-18), greeks max\|Δ\| 6.7e-8** — the residual
is entirely the **delta** (quant.js `erf` approximation vs Python machine-precision `math.erf`, same
class as the Acklam normPpf tolerance); gamma/vega use `normPdf` (exact `exp`) and match to 1e-18.

## Final verdicts (shipped)

| panel | verdict | basis |
|---|---|---|
| P1 max-pain / OI-by-strike | **KEPT** | pure OI×intrinsic; positioning, not prediction; "not a magnet" caption |
| P2 Black-76 greeks surface | **KEPT** (validation passed) | delta exact / vega 0.2% / gamma within Deribit rounding; T≥1d + \|delta\|∈[0.05,0.95] gate |
| P3 unsigned gamma concentration | **KEPT** (validation passed) | gamma density from OI; no dealer sign, no flip level |
| signed dealer GEX / flip level | **REJECTED** | dealer sign unknowable from keyless data — logged, not shipped |

Headless-validated: all three panels render (DESCRIPTIVE tags + caveats), ATM delta ≈ 0.51 (sanity),
gamma peaks at the ATM strike, and the gamma-concentration caption explicitly forbids a flip/pin
reading. 32 pytest pass; node/ppy/CSS checks green.
