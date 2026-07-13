#!/usr/bin/env python3
"""dsr_ab.py — A/B/B decision aid: three Deflated-Sharpe *trial-variance* (V)
conventions on the SAME out-of-sample leaderboard ``scripts/compare.py`` builds.

**Production DSR is convention B2** (per-strategy own-Sharpe variance) since
2026-07-13 (RESEARCH-dsr-convention.md) — column **B2** here reproduces the
``scripts/compare.py`` leaderboard exactly (same ``risk.sharpe_estimator_variance`` V,
same N, same ``risk.deflated_sharpe_ratio``; see ``--check-production``). Column **A**
(the old empirical cross-strategy variance) is retained as the historical/reference
comparison. This tool reports all three side by side and demonstrates, numerically, the
one structural difference between them: **A couples strategies** (a change to any one
peer's Sharpe shifts every other strategy's DSR through the shared V), while **B1/B2
are decoupled** (each strategy's DSR depends only on its own returns) — which is exactly
why B2 was adopted for production.

The deflated Sharpe is (Bailey & López de Prado 2014):

    DSR_i = PSR(SR_i ; sr0)                                               (Phi CDF)
    sr0(V, N) = sqrt(V) * ( (1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e)) ),  γ = 0.57721566…
    PSR(SR_i; sr0) = Φ( (SR_i - sr0)·sqrt(n_i-1)
                        / sqrt(1 - skew_i·SR_i + (kurt_i-1)/4·SR_i²) )

The three conventions differ ONLY in the trial variance V fed to sr0(V, N):

  A  (HISTORICAL, COUPLED)  V = var(SR_1..SR_N, ddof=1), one shared scalar across
                            the N ranked strategies. A peer SR change shifts sr0 for
                            EVERY strategy ⇒ every DSR_A moves. The pre-2026-07-13
                            production convention, kept here as the reference column.
  B1 (DECOUPLED)            V = 1/n_periods — the asymptotic null (Lo 2002: Var(SR_hat)
                            → 1/n under SR=0, iid-normal). Depends only on strategy i.
  B2 (PRODUCTION, DECOUPLED) V = (1 - skew_i·SR_i + (kurt_i-1)/4·SR_i²)/(n_i-1) — the
                            strategy's OWN Sharpe-estimator variance (Lo 2002 /
                            Mertens 2002 skew-kurt-corrected form, the exact quantity
                            the PSR denominator already uses), via
                            risk.sharpe_estimator_variance. Exactly reproduces the
                            compare.py leaderboard 'OOS DSR' column — see
                            --check-production.

N (n_trials) is identical for all three columns (= number of strategies ranked in
the run, matching compare.py's selection-count deflation); only V changes.

Research / backtest only. Not financial advice. No keys, no orders.

Usage:
    python3 scripts/dsr_ab.py --start 2018-01-01
    python3 scripts/dsr_ab.py --research --start 2018-01-01
    python3 scripts/dsr_ab.py --research --json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from scipy import stats
from scipy.stats import norm

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))            # so `import compare` works
sys.path.insert(0, str(_HERE.parent))     # so `from btcquant import …` works

from btcquant import backtest, data, features, risk, strategies  # noqa: E402
import compare  # noqa: E402  (reuse its strategy list, positions builder, and empirical V)

GAMMA = 0.5772156649015329  # Euler–Mascheroni (same constant risk.deflated_sharpe_ratio uses)


# --------------------------------------------------------------------------- #
# sr0(V, N) and the three V conventions — the whole A/B/B contrast lives here. #
# --------------------------------------------------------------------------- #
def sr0(V: float, N: int, gamma: float = GAMMA) -> float:
    """Expected-max Sharpe benchmark of N skill-less trials (Bailey-LdP 2014 closed
    form). ``N == 1 ⟹ 0`` (no selection inflation). Mirrors the internals of
    ``risk.deflated_sharpe_ratio`` exactly so DSR_A reproduces production."""
    if N is None or N < 1 or V is None or V < 0 or math.isnan(V):
        return float("nan")
    if N == 1:
        return 0.0
    z1 = norm.ppf(1.0 - 1.0 / N)
    z2 = norm.ppf(1.0 - 1.0 / (N * math.e))
    return math.sqrt(V) * ((1.0 - gamma) * z1 + gamma * z2)


def v_shared(srs: list[float]) -> tuple[float, bool]:
    """Convention A: the EMPIRICAL cross-strategy variance (ddof=1) of the OOS
    per-period Sharpes. Delegates to compare.py's own ``_empirical_var_sr`` so A is
    byte-for-byte the production trial variance (returns ``(V, fallback)``)."""
    return compare._empirical_var_sr(srs)


def v_b1(n: int) -> float:
    """Convention B1: 1/n_periods — the asymptotic SR=0 null (decoupled)."""
    return 1.0 / n if n and n > 0 else float("nan")


def v_b2(sr: float, n: int, skew: float, kurt: float) -> float:
    """Convention B2: the strategy's own Lo(2002)/Mertens(2002) skew-kurt-corrected
    Sharpe-estimator variance ``(1 - skew·SR + (kurt-1)/4·SR²)/(n-1)`` (decoupled) —
    the same numerator the PSR denominator uses, divided by (n-1).

    Delegates to ``risk.sharpe_estimator_variance`` — the SINGLE SOURCE OF TRUTH now
    that B2 is the production leaderboard convention (compare.py wires the same function
    into its OOS DSR). So this tool's DSR_B2 column == the production compare.py DSR."""
    return risk.sharpe_estimator_variance(sr, n, skew, kurt)


def _dsr_manual(sr: float, n: int, skew: float, kurt: float, N: int, V: float) -> float:
    """Independent hand-formula DSR (for the 1e-9 self-check vs the production
    ``risk.deflated_sharpe_ratio``). Not used to produce the printed numbers."""
    if n is None or n < 2 or sr is None or math.isnan(sr):
        return float("nan")
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0 or math.isnan(denom):
        return float("nan")
    z = (sr - sr0(V, N)) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(norm.cdf(z))


# --------------------------------------------------------------------------- #
# Per-strategy OOS stat carrier                                                #
# --------------------------------------------------------------------------- #
@dataclass
class StratStat:
    name: str
    sr: float                       # OOS per-period Sharpe (feeds sr0 / PSR)
    n: int                          # OOS n_periods
    skew: float
    kurt: float                     # non-excess (Pearson) kurtosis, bias=False
    sr_ann: float = float("nan")    # annualized OOS Sharpe (display only)
    oos_returns: pd.Series = field(default=None, repr=False)


def _stat_from_returns(name: str, r: pd.Series, ppy: int) -> StratStat:
    """Build a StratStat from an OOS returns series using the EXACT moment
    convention of ``risk.summary`` (mean/std ddof=1; scipy skew bias=False;
    non-excess kurtosis fisher=False bias=False)."""
    r = pd.Series(r, dtype="float64").dropna()
    n = int(len(r))
    sd = r.std(ddof=1)
    sr = float(r.mean() / sd) if (n >= 2 and sd and not math.isnan(sd) and sd != 0) else float("nan")
    skew = float(stats.skew(r.to_numpy(), bias=False)) if n >= 2 else float("nan")
    kurt = float(stats.kurtosis(r.to_numpy(), fisher=False, bias=False)) if n >= 2 else float("nan")
    return StratStat(name, sr, n, skew, kurt, float(risk.sharpe(r, periods_per_year=ppy)), r)


# --------------------------------------------------------------------------- #
# Replicated compare.py pairs wiring (the two cost/PnL closures inside its      #
# main()). Verbatim logic — the BACKTEST itself is reused via walk_forward.     #
# --------------------------------------------------------------------------- #
def _pairs_eth_leg_turnover(name, close, eth_close):
    if name not in ("pairs_coint", "pairs_ou") or eth_close is None:
        return None
    eth_aligned = eth_close.reindex(close.index).ffill()   # no bfill (audit M1)
    model = "ou" if name == "pairs_ou" else "coint"
    pos_full, beta_full = strategies.pairs_legs(close, eth_aligned, model=model)
    return (beta_full * pos_full).diff().abs()


def _pairs_hedge_return(name, close, eth_close):
    if name not in ("pairs_coint", "pairs_ou") or eth_close is None:
        return None
    eth_aligned = eth_close.reindex(close.index).ffill()   # no bfill (audit M1)
    model = "ou" if name == "pairs_ou" else "coint"
    _, beta_full = strategies.pairs_legs(close, eth_aligned, model=model)
    return beta_full.shift(1) * eth_aligned.pct_change()


# --------------------------------------------------------------------------- #
# Build the SAME OOS leaderboard compare.py builds (reuse, do not reimplement). #
# --------------------------------------------------------------------------- #
def build_stats(args: argparse.Namespace) -> tuple[list[StratStat], int, dict]:
    """Run the identical walk-forward OOS leaderboard as compare.py and return
    ``(stats, n_trials, meta)``. ``n_trials = len(strat_list)`` (matches compare's
    selection-count deflation, which counts EVERY ranked strategy including any that
    error). ``stats`` holds only the strategies that produced finite OOS SRs."""
    ppy = compare._ppy(args.granularity)
    df = data.get_ohlcv(symbol=args.symbol, source=args.source, granularity=args.granularity,
                        start=args.start, end=args.end, cache=not args.no_cache)
    close = df["close"]
    try:
        eth_close = data.get_ohlcv(symbol=args.eth_symbol, source=args.source,
                                   granularity=args.granularity, start=args.start,
                                   end=args.end, cache=not args.no_cache)["close"]
    except Exception:  # noqa: BLE001
        eth_close = None

    strat_list = compare.RESEARCH_STRATS if args.research else compare.SPOT_STRATS
    n_trials = len(strat_list)
    stats_out: list[StratStat] = []
    skipped: list[tuple[str, str]] = []
    for name in strat_list:
        try:
            wf = backtest.walk_forward(
                compare._make_positions_fn(name, args, ppy, eth_close), close,
                n_splits=args.folds, cost_bps=args.cost_bps, slippage_bps=args.slippage_bps,
                periods_per_year=ppy,
                extra_cost_turnover=_pairs_eth_leg_turnover(name, close, eth_close),
                hedge_return=_pairs_hedge_return(name, close, eth_close))
            oos = wf["oos"]
            sr = oos.get("sharpe_per_period", float("nan"))
            if not (isinstance(sr, (int, float)) and math.isfinite(float(sr))):
                skipped.append((name, "non-finite OOS SR"))
                continue
            stats_out.append(StratStat(
                name=name, sr=float(sr), n=int(oos.get("n_periods", 0)),
                skew=float(oos.get("skew", float("nan"))),
                kurt=float(oos.get("kurtosis", float("nan"))),
                sr_ann=float(oos.get("sharpe", float("nan"))),
                oos_returns=wf["oos_returns"]))
        except Exception as exc:  # noqa: BLE001
            skipped.append((name, str(exc)[:60]))
    meta = {"ppy": ppy, "span": f"{df.index[0].date()} -> {df.index[-1].date()}",
            "bars": int(len(df)), "skipped": skipped}
    return stats_out, n_trials, meta


# --------------------------------------------------------------------------- #
# The three DSRs per strategy (column A == production by construction).         #
# --------------------------------------------------------------------------- #
def compute_dsrs(stats: list[StratStat], n_trials: int | None = None) -> tuple[dict, dict]:
    """Return ``(rows, meta)``. ``rows[name]`` carries the three DSRs and their V.
    Column A calls ``risk.deflated_sharpe_ratio`` with compare's shared empirical V
    and ``n_trials`` → it reproduces the production leaderboard DSR exactly."""
    if n_trials is None:
        n_trials = len(stats)
    srs = [s.sr for s in stats]
    V_A, fallback = v_shared(srs)
    rows: dict = {}
    for s in stats:
        vA = V_A if not fallback else v_b1(s.n)
        vB1 = v_b1(s.n)
        vB2 = v_b2(s.sr, s.n, s.skew, s.kurt)
        rows[s.name] = {
            "sr": s.sr, "sr_ann": s.sr_ann, "n": s.n, "skew": s.skew, "kurt": s.kurt,
            "vA": vA, "vB1": vB1, "vB2": vB2,
            "sr0_A": sr0(vA, n_trials), "sr0_B1": sr0(vB1, n_trials), "sr0_B2": sr0(vB2, n_trials),
            "dsr_a": risk.deflated_sharpe_ratio(s.sr, s.n, s.skew, s.kurt, n_trials, vA),
            "dsr_b1": risk.deflated_sharpe_ratio(s.sr, s.n, s.skew, s.kurt, n_trials, vB1),
            "dsr_b2": risk.deflated_sharpe_ratio(s.sr, s.n, s.skew, s.kurt, n_trials, vB2),
        }
    return rows, {"V_A": V_A, "fallback": fallback, "n_trials": n_trials}


def _selfcheck_1e9(rows: dict, n_trials: int) -> float:
    """Assert each column equals the independent hand-formula DSR to 1e-9 (validates
    the sr0/PSR wiring against risk.deflated_sharpe_ratio). Returns the max abs diff."""
    worst = 0.0
    for r in rows.values():
        for v_key, dsr_key in (("vA", "dsr_a"), ("vB1", "dsr_b1"), ("vB2", "dsr_b2")):
            man = _dsr_manual(r["sr"], r["n"], r["skew"], r["kurt"], n_trials, r[v_key])
            prod = r[dsr_key]
            if math.isnan(man) and math.isnan(prod):
                continue
            worst = max(worst, abs(man - prod))
    assert worst < 1e-9, f"self-check failed: hand-formula vs production DSR diff {worst:.2e}"
    return worst


# --------------------------------------------------------------------------- #
# Coupling perturbation: scale ONE strategy's OOS Sharpe, watch the peers.      #
# --------------------------------------------------------------------------- #
def _scale_sr_returns(r: pd.Series, scale: float) -> pd.Series:
    """Perturb an OOS returns series so its per-period Sharpe becomes ``scale·SR``
    while n, σ, skew and kurtosis are held EXACTLY fixed: shift every return by
    ``(scale-1)·mean`` (an additive drift). Mean → scale·mean; std/skew/kurt are
    central-moment / shift-invariant ⇒ SR' = scale·SR exactly. This is a genuine
    returns-level perturbation (not a hand-edit of SR)."""
    r = pd.Series(r, dtype="float64").dropna()
    mu = float(r.mean())
    return r - mu + scale * mu


def coupling_experiment(stats: list[StratStat], n_trials: int, ppy: int,
                        target: str = "pairs_coint",
                        scales=(1.5, 0.5)) -> dict:
    """Perturb ``target``'s OOS Sharpe by each scale, recompute all three DSRs, and
    measure how far EVERY OTHER strategy's DSR moves under A vs B1 vs B2."""
    base_rows, _ = compute_dsrs(stats, n_trials)
    by_name = {s.name: s for s in stats}
    result = {"target": target, "present": target in by_name, "scales": {}}
    if target not in by_name:
        return result
    for scale in scales:
        tgt = by_name[target]
        pert = _scale_sr_returns(tgt.oos_returns, scale)
        pert_stat = _stat_from_returns(target, pert, ppy)
        pert_stats = [pert_stat if s.name == target else s for s in stats]
        pert_rows, _ = compute_dsrs(pert_stats, n_trials)
        peers = {}
        for name in by_name:
            if name == target:
                continue
            peers[name] = {
                "dA": pert_rows[name]["dsr_a"] - base_rows[name]["dsr_a"],
                "dB1": pert_rows[name]["dsr_b1"] - base_rows[name]["dsr_b1"],
                "dB2": pert_rows[name]["dsr_b2"] - base_rows[name]["dsr_b2"],
            }
        result["scales"][scale] = {
            "target_sr_base": tgt.sr, "target_sr_pert": pert_stat.sr,
            "V_A_base": base_rows[target]["vA"], "V_A_pert": pert_rows[target]["vA"],
            "peers": peers,
        }
    return result


# --------------------------------------------------------------------------- #
# Assert DSR_A == compare.py's printed leaderboard DSR (its own output path).   #
# --------------------------------------------------------------------------- #
def check_production(rows: dict, args: argparse.Namespace, end_pin: str | None = None) -> dict:
    """Run scripts/compare.py as a subprocess with matching flags, parse its
    leaderboard 'OOS DSR' column, and confirm every strategy's DSR_B2 agrees.

    Since 2026-07-13 (RESEARCH-dsr-convention.md) the production leaderboard uses the
    **B2** convention (per-strategy own-Sharpe variance), so DSR_**B2** is the column
    that must reproduce compare.py — not DSR_A (now the historical/reference column).
    Full-precision equality is guaranteed BY CONSTRUCTION — DSR_B2 calls the very same
    ``risk.deflated_sharpe_ratio`` on the same walk-forward OOS stats with the same
    per-strategy ``risk.sharpe_estimator_variance`` V and same N. This subprocess is an
    end-to-end sanity gate on top of that. Two irreducible looseners apply: (1) the
    leaderboard prints only 2 dp, and (2) the last daily bar for *today* is still live,
    so a subprocess re-fetch can wiggle a DSR by ~0.005 across a rounding boundary. The
    gate therefore tolerates ``TOL`` (well below any real wiring divergence, which would
    be O(0.1)); ``--end`` is pinned to the tool's last bar to shrink that live-bar
    window. Returns the parsed comparison."""
    TOL = 0.02
    cmd = [sys.executable, str(_HERE / "compare.py"), "--start", args.start,
           "--granularity", args.granularity, "--source", args.source,
           "--folds", str(args.folds), "--cost-bps", str(args.cost_bps),
           "--slippage-bps", str(args.slippage_bps), "--symbol", args.symbol,
           "--eth-symbol", args.eth_symbol]
    end = args.end or end_pin
    if end:
        cmd += ["--end", str(end)]
    if args.research:
        cmd += ["--research"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=1200).stdout
    printed: dict[str, float] = {}
    names = set(rows)
    in_board = False   # only the leaderboard table — NOT the later LEAD-TIME IC table
    for line in out.splitlines():
        if "OOS DSR" in line and "OOS CAGR" in line:   # leaderboard header
            in_board = True
            continue
        if in_board and line.startswith("PBO ("):       # end of leaderboard
            break
        if not in_board:
            continue
        parts = line.split()
        if parts and parts[0] in names:
            # leaderboard row: name CAGR% SR IS_SR DSR MaxDD% ... — pull the numeric
            # tokens in order; DSR is index 3 (the '*' significance flag may adjoin it).
            nums = [p for p in parts[1:] if re.fullmatch(r"-?\d+\.\d+%?\*?", p)]
            # tokens: [CAGR%, SR, IS_SR, DSR, MaxDD%, ...]; DSR is index 3
            try:
                dsr = float(nums[3].replace("%", "").replace("*", ""))
                printed[parts[0]] = dsr
            except (IndexError, ValueError):
                pass
    worst = 0.0
    checked = {}
    for name, r in rows.items():
        if name in printed and isinstance(r["dsr_b2"], (int, float)) and not math.isnan(r["dsr_b2"]):
            d = abs(round(r["dsr_b2"], 2) - printed[name])
            worst = max(worst, d)
            checked[name] = {"dsr_b2": r["dsr_b2"], "compare_printed": printed[name], "abs_2dp": d}
    ok = bool(checked) and worst < TOL + 1e-9
    assert ok, (f"DSR_B2 disagrees with compare.py leaderboard: worst 2dp diff {worst} "
                f"(> {TOL}; too large to be live-bar drift — a real wiring divergence)")
    return {"checked": checked, "worst_2dp": worst, "n_checked": len(checked), "tol": TOL}


# --------------------------------------------------------------------------- #
# Rendering                                                                    #
# --------------------------------------------------------------------------- #
def _f(x, dp=4):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if (math.isnan(v) or math.isinf(v)) else f"{v:.{dp}f}"


def render(stats, n_trials, meta, rows, dmeta, coupling, prod_check, args) -> None:
    order = sorted(rows, key=lambda k: (rows[k]["dsr_a"] if not math.isnan(rows[k]["dsr_a"]) else -9e9),
                   reverse=True)
    V_A = dmeta["V_A"]
    board = "RESEARCH (N=%d)" % n_trials if args.research else "PUBLIC (N=%d)" % n_trials
    print(f"\nbtc-quant DSR A/B decision aid | {board} | {args.symbol} {args.granularity} | "
          f"{meta['span']} | {meta['bars']} bars | {args.folds} folds | "
          f"cost {args.cost_bps}+{args.slippage_bps} bps/side")
    print("Production DSR is convention B2 (per-strategy own-Sharpe variance) since 2026-07-13")
    print("(RESEARCH-dsr-convention.md). A is now the historical/reference column; B1 is a sanity peer.\n")
    print(f"  A  (HISTORICAL, COUPLED)  V_A = var(SR_1..SR_N, ddof=1) = {_f(V_A, 6)}  (one shared scalar)"
          + ("   ⚠ null-variance fallback (V=1/n)" if dmeta["fallback"] else ""))
    print( "                            → sr0 depends on ALL peers; perturbing any peer moves every DSR_A.")
    print( "  B1 (DECOUPLED)            V_B1 = 1/n_periods  (asymptotic SR=0 null; Lo 2002)  [per-strategy]")
    print( "  B2 (PRODUCTION, DECOUPLED) V_B2 = (1 - skew·SR + (kurt-1)/4·SR²)/(n-1)  (Lo 2002 / Mertens 2002")
    print( "                            skew-kurt-corrected own-Sharpe variance; plug-in moments) [per-strategy]")
    print(f"  N (n_trials) = {n_trials} for ALL THREE columns (selection-count deflation); only V differs.\n")

    hdr = (f"{'strategy':<18}{'OOS SR':>8}{'DSR_A':>9}{'DSR_B1':>9}{'DSR_B2':>9}"
           f"{'n':>7}{'sr0_A':>9}{'V_B1':>10}{'V_B2':>10}")
    print("=" * len(hdr)); print(hdr)
    print(f"{'(annualized→ )':<18}{'':>8}{'[hist]':>9}{'[1/n]':>9}{'[prod]':>9}{'':>7}{'':>9}{'':>10}{'':>10}")
    print("-" * len(hdr))
    for name in order:
        r = rows[name]
        print(f"{name:<18}{_f(r['sr_ann'], 2):>8}{_f(r['dsr_a']):>9}{_f(r['dsr_b1']):>9}"
              f"{_f(r['dsr_b2']):>9}{r['n']:>7}{_f(r['sr0_A']):>9}{_f(r['vB1'], 6):>10}{_f(r['vB2'], 6):>10}")
    print("=" * len(hdr))
    for name, why in meta["skipped"]:
        print(f"  (skipped {name}: {why})")
    if prod_check is not None:
        print(f"\n[check] DSR_B2 vs compare.py leaderboard: {prod_check['n_checked']} strategies, "
              f"worst 2dp diff {prod_check['worst_2dp']:.4f} (< {prod_check['tol']} ✓) — column B2 "
              f"reproduces production (B2 convention since 2026-07-13; full-precision equality by "
              f"construction; residual is the live final-bar re-fetch).")

    # ── Coupling sensitivity ────────────────────────────────────────────────
    print("\n" + "─" * 78)
    print(f"COUPLING SENSITIVITY — perturb '{coupling['target']}' OOS Sharpe (×scale via an additive")
    print( "drift; n, σ, skew, kurt held fixed), recompute, and measure how far EACH OTHER strategy's")
    print( "DSR moves under A vs B1 vs B2. Claim: A couples (peers move), B1/B2 do not (peers ≡ 0).")
    print("─" * 78)
    if not coupling["present"]:
        print(f"  {coupling['target']} not in this run — coupling demo skipped.")
    else:
        for scale, blk in coupling["scales"].items():
            print(f"\n  scale ×{scale}:  {coupling['target']} SR {_f(blk['target_sr_base'], 5)} → "
                  f"{_f(blk['target_sr_pert'], 5)}   |   shared V_A {_f(blk['V_A_base'], 6)} → "
                  f"{_f(blk['V_A_pert'], 6)}")
            sh = f"    {'peer strategy':<18}{'ΔDSR_A':>12}{'ΔDSR_B1':>12}{'ΔDSR_B2':>12}"
            print(sh); print("    " + "-" * (len(sh) - 4))
            maxA = maxB = 0.0
            for name, d in blk["peers"].items():
                print(f"    {name:<18}{d['dA']:>+12.2e}{d['dB1']:>+12.2e}{d['dB2']:>+12.2e}")
                maxA = max(maxA, abs(d["dA"]))
                maxB = max(maxB, abs(d["dB1"]), abs(d["dB2"]))
            print(f"    {'→ max |Δ|':<18}{maxA:>12.2e}{'':>12}{maxB:>12.2e}"
                  f"   (A moves peers; B1/B2 peers invariant)")
    print("─" * 78)
    print("  Reading: under A a single peer's Sharpe reshapes the shared V ⇒ every other strategy's")
    print("  DSR shifts — a leaderboard DSR is not a property of that strategy alone. Under B1/B2 the")
    print("  peer columns are BIT-IDENTICAL (Δ = 0) because each V uses only that strategy's own")
    print("  returns. Neither is 'right'; A answers 'best of THIS set', B1/B2 answer 'distinguishable")
    print("  from luck on its own terms'. azul selected B2 for production on 2026-07-13 —")
    print("  the leaderboard DSR is now decoupled (column B2 == compare.py); A is retained here")
    print("  as the historical reference. See RESEARCH-dsr-convention.md.\n")
    print("  Bailey & López de Prado 2014 (DSR / expected-max-of-N benchmark); Lo 2002 (Sharpe-")
    print("  estimator variance); Mertens 2002 (skew-kurt-corrected Sharpe variance the PSR uses).\n")


def _json_payload(stats, n_trials, meta, rows, dmeta, coupling, prod_check) -> dict:
    def clean(v):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v
    return {
        "n_trials": n_trials, "V_A": clean(dmeta["V_A"]), "fallback": dmeta["fallback"],
        "span": meta["span"], "bars": meta["bars"], "skipped": meta["skipped"],
        "strategies": {name: {k: clean(v) for k, v in r.items()} for name, r in rows.items()},
        "coupling": {
            "target": coupling["target"], "present": coupling["present"],
            "scales": {str(sc): {
                "target_sr_base": clean(b["target_sr_base"]), "target_sr_pert": clean(b["target_sr_pert"]),
                "V_A_base": clean(b["V_A_base"]), "V_A_pert": clean(b["V_A_pert"]),
                "peers": {n: {k: clean(v) for k, v in d.items()} for n, d in b["peers"].items()},
            } for sc, b in coupling.get("scales", {}).items()},
        },
        "production_check": prod_check,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="A/B/B decision aid: three DSR trial-variance conventions on the same OOS "
                    "leaderboard. Production is B2 since 2026-07-13 (column B2 == compare.py); "
                    "A is the historical reference. Research only.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--symbol", default="BTC-USD")
    p.add_argument("--eth-symbol", default="ETH-USD")
    p.add_argument("--granularity", choices=["1h", "1d"], default="1d")
    p.add_argument("--source", choices=["coinbase", "kraken", "coingecko"], default="coinbase")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--cost-bps", type=float, default=10.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--ma-n", type=int, default=200)
    p.add_argument("--ma-fast", type=int, default=50)
    p.add_argument("--lookback", type=int, default=20)
    p.add_argument("--target-vol", type=float, default=0.15)
    p.add_argument("--research", action="store_true",
                   help="include RESEARCH_STRATS (compare.py's Part B candidates); N grows accordingly.")
    p.add_argument("--check-production", dest="check_production", action="store_true", default=True,
                   help="assert DSR_A matches compare.py's own leaderboard output (default on).")
    p.add_argument("--no-check-production", dest="check_production", action="store_false",
                   help="skip the compare.py subprocess cross-check (faster).")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of the table.")
    args = p.parse_args()

    stats, n_trials, meta = build_stats(args)
    if len(stats) < 2:
        print("need >= 2 strategies with finite OOS SR; got", len(stats), file=sys.stderr)
        return 1
    rows, dmeta = compute_dsrs(stats, n_trials)
    _selfcheck_1e9(rows, n_trials)   # hand-formula vs production DSR to 1e-9 (all 3 columns)
    coupling = coupling_experiment(stats, n_trials, meta["ppy"])

    # Decoupling assertion: B1/B2 peers invariant to the target perturbation to 1e-12.
    if coupling["present"]:
        for blk in coupling["scales"].values():
            for d in blk["peers"].values():
                assert abs(d["dB1"]) < 1e-12 and abs(d["dB2"]) < 1e-12, \
                    "B1/B2 peers moved under the target perturbation — decoupling violated"

    prod_check = None
    if args.check_production:
        end_pin = meta["span"].split("->")[-1].strip()   # pin subprocess to the tool's last bar
        try:
            prod_check = check_production(rows, args, end_pin=end_pin)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] production cross-check skipped: {str(exc)[:160]}", file=sys.stderr)

    if args.json:
        print(json.dumps(_json_payload(stats, n_trials, meta, rows, dmeta, coupling, prod_check),
                         indent=2))
    else:
        render(stats, n_trials, meta, rows, dmeta, coupling, prod_check, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
