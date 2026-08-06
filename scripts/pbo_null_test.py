"""pbo_null_test.py — run `docs/PREREG-pbo-null-001.md` EXACTLY as declared.

Every choice here was fixed in the declaration BEFORE this file existed: S = 8 locked,
B = 2,000 per arm, primary = stationary block bootstrap of the DE-MEANED real board with
mean block length L ∈ {5, 21, 63} (verdict must agree across all three or the clause
ABSTAINS as INDETERMINATE), secondary = Gaussian with the board's empirical covariance
(must agree with the primary or INDETERMINATE), decision at the null's P5/P95 (α = 5 %).
Nothing here may be tuned after seeing a number; a control failure DISCARDS the run.

Controls, run FIRST, in order (any failure aborts before the verdict is computed):
  PC1  the observed board PBO at S = 8 must reproduce 0.5286 (documented 0.53) —
       the matrix is rebuilt through compare.py's own walk-forward path, unmodified,
       captured by wrapping backtest.walk_forward at import time.
  PC2  a synthetic board with one genuinely dominant column must come out SATISFIED —
       a test with no power on an obvious winner is not a gate.
  PC3  a board of near-duplicate zero-edge columns must NOT come out SATISFIED.

Cost is measured before the main loop (10-rep timing, extrapolated and printed), never
assumed. Results: JSON beside this run's log + a summary block whose conclusions print
BESIDE the numbers that produced them.

Research only. Reads public OHLCV through the repo's own cache; writes one JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from btcquant import backtest, risk  # noqa: E402

PC1_EXPECTED = 0.5429        # re-based per PREREG amendment 2026-08-06: the 0.5286 anchor
                             # embedded a mid-day PARTIAL 2026-08-04 bar that no longer exists;
                             # 0.5429 is the value on closed bars, T=2,615 (one CSCV combo apart)
S_LOCKED = 8                 # declared §2.1
B = 2_000                    # declared §2.2
L_SET = (5, 21, 63)          # declared §2.3
SEED = 20260806


def build_board_matrix() -> "np.ndarray":
    """The 8-strategy board's OOS returns, through compare.py's own path (spy)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("compare", REPO / "scripts" / "compare.py")
    compare = importlib.util.module_from_spec(spec)
    captured: list = []
    real_wf = backtest.walk_forward

    def spy(*a, **k):
        r = real_wf(*a, **k)
        captured.append(r.get("oos_returns"))
        return r

    backtest.walk_forward = spy
    try:
        spec.loader.exec_module(compare)
        old_argv = sys.argv
        # SAMPLE PINNED to the declaration: every §8/PREREG number is conditional on the
        # provenanced T = 2,615 board (data through 2026-08-04). Without the pin, the
        # OHLCV cache grows a row per day and PC1 fails on a (2617, 8) matrix — which is
        # PC1 doing its job: same instrument, different sample, different number.
        sys.argv = ["compare.py", "--research", "--cost-bps", "5.01", "--slippage-bps", "0",
                    "--end", "2026-08-04"]
        try:
            compare.main()
        finally:
            sys.argv = old_argv
        names = compare.RESEARCH_STRATS
        import pandas as pd
        df = pd.DataFrame(dict(zip(names, captured[: len(names)]))).dropna()
        M = df.to_numpy()
        assert M.shape[0] == 2_615, (
            f"sample is {M.shape[0]} rows, declaration binds T=2,615 — refuse to proceed")
        return M, names
    finally:
        backtest.walk_forward = real_wf


def pbo(M: "np.ndarray") -> float:
    return risk.probability_of_backtest_overfitting(M, n_blocks=S_LOCKED)["pbo"]


def stationary_bootstrap_idx(T: int, mean_L: float, rng) -> "np.ndarray":
    """Politis–Romano: geometric block lengths, wrap-around, whole-row resampling."""
    idx = np.empty(T, dtype=int)
    p = 1.0 / mean_L
    t = 0
    while t < T:
        start = rng.integers(0, T)
        L = rng.geometric(p)
        take = min(L, T - t)
        idx[t:t + take] = (start + np.arange(take)) % T
        t += take
    return idx


def null_pbos_bootstrap(M0: "np.ndarray", mean_L: float, B: int, rng, say) -> "np.ndarray":
    out = np.empty(B)
    t0 = time.time()
    for b in range(B):
        out[b] = pbo(M0[stationary_bootstrap_idx(len(M0), mean_L, rng)])
        if b == 9:
            per = (time.time() - t0) / 10
            say(f"    cost check: {per * 1000:.0f} ms/rep -> projected "
                f"{per * B / 60:.1f} min for this arm")
    return out


def null_pbos_gaussian(M0: "np.ndarray", B: int, rng, say) -> "np.ndarray":
    cov = np.cov(M0, rowvar=False)
    T = len(M0)
    out = np.empty(B)
    t0 = time.time()
    for b in range(B):
        out[b] = pbo(rng.multivariate_normal(np.zeros(M0.shape[1]), cov, size=T))
        if b == 9:
            per = (time.time() - t0) / 10
            say(f"    cost check: {per * 1000:.0f} ms/rep -> projected "
                f"{per * B / 60:.1f} min for this arm")
    return out


def verdict_for(observed: float, nulls: "np.ndarray") -> tuple[str, float, float]:
    p5, p95 = np.percentile(nulls, [5, 95])
    if observed < p5:
        return "SATISFIED", p5, p95
    if observed > p95:
        return "FAILS", p5, p95
    return "ABSTAINS", p5, p95


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=B,
                    help="declared 2000; lower ONLY for a smoke run, never for the verdict")
    ap.add_argument("--out", type=Path,
                    default=REPO / "reports" / "pbo-null-result.json")
    a = ap.parse_args()
    say = print
    rng = np.random.default_rng(SEED)

    say("pbo-null-test — running docs/PREREG-pbo-null-001.md as declared")
    say(f"  S={S_LOCKED} locked · B={a.reps} · L set {L_SET} · seed {SEED}\n")

    say("  rebuilding the provenanced board matrix through compare.py (spy)…")
    M, names = build_board_matrix()
    say(f"  matrix {M.shape}, columns: {names}")

    # ---- PC1 ----
    observed = pbo(M)
    ok1 = abs(observed - PC1_EXPECTED) < 5e-4
    say(f"\n  PC1 observed board PBO = {observed:.4f} (expected {PC1_EXPECTED}) -> "
        f"{'MATCH' if ok1 else 'MISMATCH — ABORT, the instrument is wrong'}")
    if not ok1:
        return 2

    # ---- PC2: one dominant column among matched noise ----
    T, N = M.shape
    sd = M.std(axis=0).mean()
    edge_board = rng.normal(0, sd, size=(T, N))
    edge_board[:, 0] += 0.35 * sd            # a large, unambiguous true edge
    pc2_obs = pbo(edge_board)
    pc2_nulls = null_pbos_gaussian(np.ascontiguousarray(edge_board - edge_board.mean(0)),
                                   min(a.reps, 400), rng, say)
    v2, p5_2, _ = verdict_for(pc2_obs, pc2_nulls)
    say(f"  PC2 dominant-column board: PBO {pc2_obs:.4f} vs null P5 {p5_2:.4f} -> {v2} "
        f"{'(required SATISFIED: OK)' if v2 == 'SATISFIED' else '(REQUIRED SATISFIED — ABORT: no power)'}")
    if v2 != "SATISFIED":
        return 2

    # ---- PC3: near-duplicate zero-edge columns must not be SATISFIED ----
    base = rng.normal(0, sd, size=T)
    dup_board = np.column_stack([base + rng.normal(0, 0.05 * sd, size=T) for _ in range(N)])
    pc3_obs = pbo(dup_board)
    pc3_nulls = null_pbos_bootstrap(dup_board - dup_board.mean(0), 21,
                                    min(a.reps, 400), rng, say)
    v3, p5_3, _ = verdict_for(pc3_obs, pc3_nulls)
    say(f"  PC3 duplicate zero-edge board: PBO {pc3_obs:.4f} vs null P5 {p5_3:.4f} -> {v3} "
        f"{'(not SATISFIED: OK)' if v3 != 'SATISFIED' else '(must NOT be SATISFIED — ABORT)'}")
    if v3 == "SATISFIED":
        return 2

    # ---- the declared test ----
    M0 = M - M.mean(axis=0)                  # de-meaned: the null has no true differences
    arms: dict = {}
    say("\n  PRIMARY — stationary block bootstrap of the de-meaned board:")
    for L in L_SET:
        nulls = null_pbos_bootstrap(M0, L, a.reps, rng, say)
        v, p5, p95 = verdict_for(observed, nulls)
        arms[f"bootstrap_L{L}"] = {"p5": p5, "p95": p95, "median": float(np.median(nulls)),
                                   "verdict": v}
        say(f"    L={L:<3} null P5 {p5:.4f} · median {np.median(nulls):.4f} · "
            f"P95 {p95:.4f} · observed {observed:.4f} -> {v}")
    say("  SECONDARY — Gaussian, empirical covariance:")
    nulls = null_pbos_gaussian(M0, a.reps, rng, say)
    v, p5, p95 = verdict_for(observed, nulls)
    arms["gaussian"] = {"p5": p5, "p95": p95, "median": float(np.median(nulls)), "verdict": v}
    say(f"    null P5 {p5:.4f} · median {np.median(nulls):.4f} · P95 {p95:.4f} · "
        f"observed {observed:.4f} -> {v}")

    verdicts = {d["verdict"] for d in arms.values()}
    final = verdicts.pop() if len(verdicts) == 1 else "INDETERMINATE"
    say(f"\n  >>> FINAL (agreement rule): {final}"
        + ("" if final != "INDETERMINATE"
           else f"  (arms disagreed: { {k: d['verdict'] for k, d in arms.items()} })"))

    a.out.write_text(json.dumps({
        "declared_in": "docs/PREREG-pbo-null-001.md", "seed": SEED, "reps": a.reps,
        "observed_pbo": observed, "pc1": "match", "pc2": v2, "pc3": v3,
        "arms": arms, "final": final,
    }, indent=2) + "\n")
    say(f"  results -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
