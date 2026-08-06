# PREREG-scalp-001 — scalping at 1–30 s, RR 1:1, $1–5k clip: the gate, the verdict, and what survives

**Status: RESEARCH DOCUMENT — registers a rejection, activates nothing.** No strategy code was
written, no backtest was run, and no forward return was examined in producing this document.
Every empirical number below is cited to the measurement that produced it; the only new numbers
are closed-form arithmetic on those citations, and the arithmetic carries its own positive
control (§2). Contributes **0 diagnostic looks and 0 predictive trials** to the Look counter
(`docs/EDA-microstructure-001.md`, grep `Look counter`).

**The request being registered:** directional scalping, horizon 1–30 seconds, reward:risk 1:1,
clip $1,000–5,000, BTCUSDT perp, with all three execution models (taker/taker, maker-in/
taker-out, maker/maker) costed honestly.

**The one-line answer, so it cannot be buried:** at 1–30 s with RR 1:1, the required hit rate
exceeds 100 % under every execution model — the premise fails arithmetically, before any signal
is considered. §8 states what remains alive.

## Registration block (schema per `STRATEGY.md`, grep `hypothesis_id`)

| field | value |
|---|---|
| `hypothesis_id` | PREREG-scalp-001 |
| `feature` | none activated — see §5; the registered deliverable is the rejection |
| `N_trials` | **0 spent here; ceiling 3 if §5's conditional reformulation is ever activated (binding)** |
| `MinBTL_target` | MinBTL(84) = **1,310 d** [DIUKUR, `risk.min_backtest_length`, programme tally 81 + ceiling 3] |
| kill thresholds | numeric, §5 |
| LockBox slice | `2026-08-05 01:00 UTC` onward, all venues, all tables (`docs/EDA-microstructure-001.md`, grep `AMENDMENT 2026-08-05`) — not touched, not read, not peeked, including by this document |

---

## §1 — Cost model [DIUKUR spread/slippage · DIASUMSIKAN fees]

All figures from `docs/EDA-microstructure-001.md` §2, re-read at source before citation.

**Spread.** The book is pinned at the tick floor: **0.0157 bps** (= $0.10 on ~$63,500), median
0.0156 bps in every UTC hour over **859,264 snapshots across 26 days** (§2a, grep
`The median is 0.0156 bps in every single hour`). One-tick width holds **99.95 %** of the time
(`docs/EDA-execution-001.md`, grep `41,449 (99.95`). Tail: max observed **5.7643 bps** — 370×
the median, still below the round-trip fee (§2a, grep `max observed spread is 5.7643`).

**Slippage at $1–5k.** Depth-walk p50 **0.0079 bps** at every clip in {$1k, $3k, $5k}; the
level-1 ask alone is ≥ $5,000 in 99.7 % of snapshots, so the clip never leaves the touch (§2a,
grep `Slippage for a retail clip is half the spread`).

**Fees [DIASUMSIKAN — published tariff, never measured].** USDT-M perp **taker 5.0 / maker
2.0 bps per side** (§2d, sourced to `RESEARCH.md` (grep `2.12 Volatility targeting / vol scaling — `vol_target``)). No fee table exists in code; actual
tier is an account fact this repo cannot observe.

**Round-trip totals** (§2, grep `cost table ready to replace`):

| execution | round-trip C |
|---|---:|
| taker / taker | **10.02 bps** |
| maker in / taker out | **7.01 bps** |
| maker / maker | **4.00 bps** — a floor, not an estimate: excludes queue risk and adverse selection entirely |

**Engaging `backtest.py`'s look-ahead argument.** The docstring (grep `you do not know the fill
spread until you trade` in `btcquant/backtest.py`) argues a data-derived spread is look-ahead
and that the repo "stores no historical quote/tick series to bound it". The second clause is
stale — `depth_snapshots` exists and `spread_bps_{b}` is computed in `orderflow.py` (already
recorded in EDA §2, grep `is now partly stale`). The first clause is correct and is **resolved,
not dismissed**: this document costs every model with a **lagged distribution quantile** — a
spread statistic estimated over a trailing window that closes strictly before the fill bar —
never the contemporaneous fill spread. On this instrument the resolution is almost free of
consequence, because the spread distribution is degenerate at the tick floor (per-hour median
range 0.0000 bps, §2a): the lagged quantile equals the constant 0.0157 to four decimals. The
tail is handled the same way — a pessimistic variant charges lagged p99 (0.0162) or the
recorded max (5.7643); §7 shows no verdict below moves. Fees dominate the entire
microstructure term by **625×** (§2, grep `fees are **625×**`), so the look-ahead debate is
real in principle and worth ~0.02 bps in practice here.

---

## §2 — The 1:1 gate

EV per round trip with stop distance `S`, target `R·S`, hit rate `p`, cost `C`:
`EV = p·S·R − (1−p)·S − C > 0` iff

```
p > p* = (S + C) / (S · (R + 1))        ; at R = 1:  p* = 0.5 + C / (2S)
```

`p* ≥ 1` is **IMPOSSIBLE** — no predictor, however good, clears it, because `C > S·R` means
the cost exceeds the entire payout of a winner.

**S bound to realised movement, measured not scaled.** `docs/EDA-microstructure-001.md` §4a
measured `|log return|` on 1,936,206 second-bars (30/30 partitions); §4c measured `E|r|`.
sqrt-t scaling was checked and rejected at the short end — it overstates the 1 s move by
2.13× (§4a, grep `the error is at the short end`). Anchors at the scalping horizons [DIUKUR]:

| horizon | p50 | p75 | E\|r\| |
|---|---:|---:|---:|
| 1 s | 0.02 | 0.02 | 0.19 |
| 5 s | 0.18 | 0.99 | 0.68 |
| 30 s | 1.41 | 2.92 | 2.16 |

(§4a table, grep `30 s | 1,848,307`; §4c table, grep `fails 0.22×`.)

**p\* at R = 1, S = p75, all three execution models** [DIUKUR arithmetic on DIUKUR inputs].
Positive control first, per the standing rail: the same identity recomputed here reproduces
three independently published §4b cells to the digit — 30 m taker R=1 **71.6 %**, 30 s
maker/taker R=3 **85.0 %**, 30 s maker/maker R=3 **59.2 %** (grep `71.6` / `85.0` / `59.2` in
`docs/EDA-microstructure-001.md`). The instrument sees the known values; the new cells follow.

| horizon | taker/taker 10.02 | maker/taker 7.01 | maker/maker 4.00 |
|---|---:|---:|---:|
| 1 s | 25,100 % | 17,575 % | 10,050 % |
| 5 s | 556 % | 404 % | 252 % |
| 30 s | **222 %** | **170 %** | **118 %** |

**Every cell at 1–30 s exceeds 100 % under every execution model.** This is not "hard": at
30 s the stop distance that RR 1:1 implies (p75 = 2.92 bps) is smaller than even the
maker/maker cost floor of 4.00 bps, so `C > S·R` and the trade loses in expectation at any
hit rate up to and including 100 %. Anchoring S at E|r| (2.16) or p50 (1.41) makes every cell
worse; there is no anchor choice under which any 1–30 s cell drops below 100 % (§7). This
matches §4b's independently computed row: "RR 1:1 at a 1–30 s horizon, taker execution — is
IMPOSSIBLE at every cell" (grep `IMPOSSIBLE at every`), and extends it: **it is impossible
at every execution model, not only taker**.

**The maker rows are killed twice.** Even where maker arithmetic eventually turns possible at
longer horizons, `docs/EDA-execution-001.md` §E0 closes retail passive execution on its own
grounds [DIUKUR spread · DIASUMSIKAN fees]: at VIP 0 the maker/maker round trip nets
**−3.9843 bps guaranteed, before adverse selection** (grep `net, GUARANTEED`); the median
touch queue is **≈ $444,617** on a book one tick wide 99.95 % of the time, so a $1–5k clip
joins the back of a queue **89–445× its own size** and cannot price-improve (grep
`Median queue ≈`); and fills arrive preferentially when the queue is being swept — the
adverse-selection case by definition. §E0c's verdict: not worth pursuing, and no signal can
change that. The 4.00 and 7.01 bps rows above are therefore **floors used to show the gate
fails even at the floor** — not attainable costs.

**The selectivity caveat, carried honestly.** §4-corr showed the seconds horizon is closed for
a *non-selective* strategy, not closed in principle: 1.636 % of 30 s windows move more than
the taker toll (grep `1.636`). That door does not readmit RR 1:1: fixed 1:1 barriers at
S = p75 stay under the cost floor regardless of which windows are selected, and widening S to
clear C (≥ 10.02 bps at 30 s) means most windows expire untouched — p99 |r| at 30 s is
11.65 bps (§4a). What survives selectivity is a volatility-timing-plus-direction problem, a
strictly harder object than the registered request, and it is recorded in §4-corr as an
identified door, not opened here.

---

## §3 — Data inventory, stated honestly

What could actually score a hypothesis at each horizon [all DIUKUR unless marked]:

- **Trade-derived families** (CVD, signed delta, size-bucketed delta, VPIN): the Vision
  archive spans 2,406 days = **244 % of MinBTL(5)** (`STRATEGY.md`, grep
  `244 % of MinBTL(5)`). On disk: **2,086 of 2,406 partitions**, dense 2020-01-01…2025-10-07,
  then a **296-day hole** (2025-10-08…2026-07-30, the ENOSPC that killed the ingest), then 3
  days (`docs/PRECHECK-cvd-turnover.md`, grep `day hole` — the count itself lives in `docs/STATUS.md`). EDA §2c's earlier "3
  day-partitions on disk" predates that ingest and is superseded by the PRECHECK count.
- **Book-derived families** (OFI, microprice, queue imbalance — the natural seconds-horizon
  predictors): **1.8 % of MinBTL(5)** (`STRATEGY.md`, grep `1.8 % of MinBTL(5)`). The archive
  publishes no book snapshots; book history is bought only with collector uptime.
- **No sub-minute bar constructor exists.** `orderflow.py` (grep `BAR_FREQS`) admits exactly
  `("1min", "5min", "15min", "1h")`, and its own guard states the rail: any extra clock "is an
  extra trial". A seconds clock would be new code and a counted design decision; it is refused
  here because §2 closes the horizon before any bar could be cut.
- **Feed continuity.** On the 18-day window where one instrument carries both a trade and a
  book leg, **52.1 % of wall time is a feed hole** and only 27 of 432 hourly bars are clean
  (`STRATEGY.md`, grep `52.1 % of wall time is a feed hole`). Book coverage is
  session-skewed: 44.6 % of (date, hour, venue) cells are OFF outright and UTC 00–11 is
  effectively unstudiable with book data (`docs/EDA-microstructure-001.md` §0a/§0c, grep
  `Cell census`).
- **Recorded seconds data** (1,936,206 second-bars over 30 days, §4a) is sufficient to
  *measure* the |r| distributions used in §2 — and roughly 30/985 = 3 % of the span needed to
  *score* a 5-trial hypothesis (MinBTL(5) = 985 d [DIUKUR, `risk.min_backtest_length`]).

Consequence: even if §2's arithmetic were somehow evaded, **no 1–30 s hypothesis could be
scored on the recorded data**, and the features most plausibly informative at that horizon
(book-derived) are the ones with 1.8 % of the required history.

---

## §4 — Candidates (max 3), graded by testable data, prior rejections confronted

Prior rejections on the record, so nothing below re-proposes them silently:

- Hourly `sign(ΔCVD)`: OOS Sharpe **−3.1 to −10.3**, "a statement about transaction costs"
  (`RESEARCH-orderflow-runlog.md`, grep `a statement about transaction costs`); cost drag
  measured later at **45,336 bps/yr** (`docs/PRECHECK-cvd-turnover.md`, grep `45,336`).
- Daily `sign(ΔCVD)` (C1): **AMBIGUOUS at 8.66×** the fair binary anchor, stays NOT PROPOSED
  (`docs/PRECHECK-cvd-turnover.md`, grep `8.66`).
- `vwap_reversion`: **KILL — stands** at the corrected cost, DSR 0.00
  (`docs/EDA-microstructure-001.md` §4bis-B, grep `vwap_reversion 48`).

| # | candidate | horizon / execution | gate (§2) | data (§3) | status |
|---|---|---|---|---|---|
| C-1 | trades-only feature (size-bucketed delta or VPIN burst), 5-min bars, triple-barrier **R=3**, taker/taker | 5 m / taker | p\* = **51.1 %** (§4b, grep `51.1`) — alive | Vision trades, 244 % MinBTL(5); `5min` in `BAR_FREQS` | **only registrable shape** — §5, conditional |
| C-2 | maker-entry / taker-exit, 5-min bars, R=2 | 5 m / maker-taker | p\* = 57.7 % (§4b, grep `57.7`) — alive on paper | same as C-1 | **BLOCKED on §E1**: fill probability and markout unmeasured; $445k queue, adverse selection unquantified (`docs/EDA-execution-001.md`, grep `§E1`) — not registrable until measured |
| C-3 | any 1–30 s candidate, any execution (incl. the 30 s maker/maker R=3 cell at 59.2 %) | 1–30 s / all | **p\* > 100 % at R=1 every cell**; the one sub-minute cell at any R is maker/maker, killed by §E0 | book features at 1.8 % MinBTL; no seconds bars | **REJECTED — do not re-propose** (the registered deliverable) |

No fourth candidate is admitted. A smoothed/deadbanded CVD variant remains explicitly
un-proposed per `docs/PRECHECK-cvd-turnover.md` (grep `I am not proposing it`) — it would be
spec chosen after seeing its parent fail.

---

## §5 — Pre-registration

**What is registered here is C-3's rejection.** At 1–30 s, RR 1:1, $1–5k, nothing survives an
honest cost under any execution model. The documented rejection is the product
(`STRATEGY.md` §6: what to refuse is the product, not a limitation).

**Falsifiability of the rejection, stated numerically.** The rejection is overturned iff one of
its two measured inputs is wrong by the required factor: (a) total round-trip cost at the best
*attainable* execution falls below `S·R` = 2.92 bps — i.e. below the maker/maker fee floor,
which per §E0a requires a maker fee ≤ ~1.45 bps/side *with guaranteed fills*, a condition §E0
shows does not exist at retail; or (b) the 30 s p75 |move| rises above 10.02 bps — a
sustained ≥ 3.4× volatility regime shift versus the 30-day measurement. Either would be
visible in a re-run of the §2a/§4a queries and would justify amending this document; nothing
short of that does.

**The ONE surviving reformulation — DECLARED, NOT ACTIVATED.** C-1 is the only shape all three
gates leave alive: **trades-only, minutes-horizon, taker/taker** — explicitly no longer
scalping as requested. Its falsifiable one-liner, fixed now so it cannot drift:

> H(scalp-001-r): a pre-specified trades-only activity feature on 5-min bars (binancef
> aggTrades), triple-barrier with R=3 and S = trailing p75 |r_5m|, taker/taker at 10.02 bps
> round-trip, achieves an OOS barrier hit rate > 51.1 % and OOS DSR > 0.95 net-of-cost over
> the Vision span.

Activation requirements, all prior: (i) the exact feature named in an activation amendment
**before** any of its numbers exist; (ii) the 296-day Vision hole either closed by ingest or
handled by the PRECHECK partition-indexing rule; (iii) the 45-trial dispute adjudicated
(`docs/EDA-microstructure-001.md`, grep `45-trial dispute`), since it moves this hypothesis's
deflation denominator.

**N_trials — binding.** This document spends **0**. If activated, the C-1 family is capped at
**3 predictive trials total** (one feature, one horizon, one barrier config, walk-forward
counted per house convention; the cap includes any §6 meta-model). Programme tally at
declaration: **81 predictive trials** (`docs/EDA-microstructure-001.md`, grep
`running total`); ceiling-inclusive deflation target MinBTL(84) = **1,310 d** [DIUKUR,
`risk.min_backtest_length`], against a 2,406 d trade-derived span = 184 % — the data clears
the target with margin.

**Kill criteria — numeric, declared before any number exists:**

| # | criterion | action |
|---|---|---|
| K1 | exploration-slice barrier hit rate ≤ **51.1 %** (its own p\*) | KILL before LockBox is approached |
| K2 | OOS DSR < **0.50** on the first walk-forward | KILL the family, no variant re-entry |
| K3 | verdict does not survive C = **12 bps** (fee-tier uncertainty: the orderflow runlog's own round-turn) | KILL — an edge thinner than a fee tier is a bet on the fee tier |
| K4 | PBO clause per `docs/PREREG-pbo-null-001.md` as declared there (abstention handling included) | as declared |

**LockBox — named as the eventual holdout.** `2026-08-05 01:00 UTC` onward, all venues, all
tables. Evaluated once, only after K1–K4 pass on exploration, per the standing declaration.
This document has read zero bytes of it.

---

## §6 — Where ML goes, placed honestly

**Meta-labelling only, and it counts.** The only admissible ML shape here is AFML-style
meta-labelling on C-1 *if activated*: the primary entry rule stays the pre-registered
mechanical one; a classifier may only size or veto its entries. Every fitted meta-model
configuration counts inside the 3-trial cap — a hyperparameter grid is a trial per cell, not a
free lunch.

**No ML at 1–30 s, for an arithmetic reason, not a taste.** A classifier optimises `p`. §2
shows `p* > 1` at every seconds cell: a model that is right 100 % of the time still loses
`C − S·R > 0` per trade. There is no accuracy for ML to reach, so proposing ML there would be
decoration on a closed gate. Secondarily, the features that could plausibly predict at that
horizon (queue imbalance, microprice — §4-corr, grep `identified door`) sit on 1.8 % of the
history needed to validate anything.

---

## §7 — Sensitivity plan (plan only — nothing here has been run)

To run only if C-1 is activated; declared now so the checks cannot be selected later:

1. **Cost anchor:** every verdict at C ∈ {10.02 measured; 12 runlog; 24 `backtest.py`
   default}. K3 makes the 12 bps row binding.
2. **S anchor:** p50 / p75 / E|r|. Already checked for §2's rejection: all three leave every
   1–30 s cell above 100 % (p50 and E|r| are stricter than p75 — arithmetic in §2).
3. **Spread model:** tick floor 0.0157 / lagged p99 0.0162 / recorded max 5.7643 bps. The max
   variant moves the taker round trip from 10.02 to 15.76 — the seconds verdict cannot
   flip in the favourable direction under any of the three.
4. **Path effect:** the p\* identity is marginal, not path-aware; true barrier-touch hit
   rates are lower, so every p\* here is optimistic and the sensitivity is one-sided against
   the candidate (§4, grep `the true bar is higher`). Any activated run must estimate the
   path-corrected hit rate and report both.
5. **Quantile uncertainty:** §4a uses overlapping pairs and claims no CI; an activated run
   reports block-bootstrap CIs on the |r| quantiles before scoring anything.

---

## §8 — Verdict

**The premise FAILS arithmetically.** Scalping BTCUSDT perp at 1–30 s with RR 1:1 and a
$1–5k clip requires a hit rate above 100 % under taker/taker (222 % at 30 s), maker/taker
(170 %), and maker/maker (118 %) — and the maker models are independently dead at retail via
§E0 (−3.98 bps guaranteed per round trip at VIP 0; $445k median queue). No signal, feature,
or model is exempt: the bound binds at 100 % accuracy. This confirms and extends
`docs/EDA-microstructure-001.md` §4b (grep `IMPOSSIBLE at every`) from taker-only to all
three execution models, and it was reached without spending a single predictive trial.

**(R, horizon, execution) triples still mathematically alive** — `p* < 100 %` at S = p75
[DIUKUR arithmetic; §4b values cited where published, remainder recomputed by the controlled
identity above]; the 60 % screening line is §4d's [DIASUMSIKAN] threshold:

| execution | R=1 | R=2 | R=3 | first plausible (p\* < 60 %) |
|---|---|---|---|---|
| taker/taker | ≥ 30 m (71.6 %); < 60 % from 4 h (57.1 %) | ≥ 30 m (47.7 %) | ≥ 5 m (51.1 %) | **5 m at R=3; 30 m at R=2; 4 h at R=1** |
| maker/taker ᵇ | ≥ 5 m (86.5 %); < 60 % from 4 h (55.0 %) | ≥ 5 m (57.7 %) | 30 s possible but 85.0 % | 5 m at R=2 — **blocked on §E1** |
| maker/maker | ≥ 1 m (97.7 %) on paper | — | 30 s (59.2 %) on paper | **none — killed by §E0 regardless of arithmetic** |

ᵇ maker-entry rows assume a passive fill whose probability and markout are unmeasured
(`docs/EDA-execution-001.md` §E1 not yet run); they are arithmetic ceilings, not offers.

**Nothing at 1–30 s survives.** The nearest live shape is C-1 — trades-only, 5-min bars,
R=3, taker/taker, p\* 51.1 % — declared in §5 and not activated. Everything at seconds is
refused, and per `STRATEGY.md` §6 that refusal, with its arithmetic attached, is the
deliverable.
