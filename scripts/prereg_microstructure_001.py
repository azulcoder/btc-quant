"""prereg_microstructure_001.py — run docs/PREREG-microstructure-001.md EXACTLY as declared.

The positive control: does the identified interval for `c`, computed from aggTrades ALONE,
contain the half-spread already measured directly from the order book?

Every choice below was fixed in the declaration BEFORE this file existed — the hour window,
the exclusions, the dedup, the segmentation, the pooling, the target and the four verdicts.
Nothing here may be tuned after seeing a number.

Research only. Reads the recorded tick store from HF, writes one JSON. No LockBox access:
the declared dates all sit inside the frozen exploration slice, which ends 2026-08-03,
two days before the LockBox boundary.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from btcquant import hasbrouck as hb  # noqa: E402

SLICE_LO, SLICE_HI = "2026-07-05", "2026-08-03"
HOURS = (12, 23)                       # declared: the book was measured on UTC 12-23 only
EXCLUDE = {
    "2026-07-13": "ZSTD read failure recorded in EDA-microstructure-001.md 2a",
    "2026-08-03": "carries a recorded-damage entry (28,428 prints)",
}
TARGET_BPS = 0.0156 / 2.0              # declared: book p50 is the FULL spread; c is the half
BPS = 1e-4


def eligible_dates() -> tuple[list[str], dict]:
    from huggingface_hub import HfApi
    import re
    fs = HfApi().list_repo_files("azulcoder/btc-quant-ticks", repo_type="dataset")
    have = {m.group(1) for f in fs
            if (m := re.match(r"data/date=(\d{4}-\d{2}-\d{2})/trades\.parquet", f))}
    inslice = sorted(d for d in have if SLICE_LO <= d <= SLICE_HI)
    keep = [d for d in inslice if d not in EXCLUDE]
    return keep, {"in_slice": len(inslice), "excluded": EXCLUDE, "kept": len(keep)}


def day_moments(con, date: str) -> dict:
    """Pooled moments for one date, segmented so no lag-1 pair straddles a splice."""
    url = f"hf://datasets/azulcoder/btc-quant-ticks/data/date={date}/trades.parquet"
    rows = con.execute(f"""
        SELECT DISTINCT ON (trade_id)
               CAST(trade_id AS BIGINT) AS tid,
               (ts_ms // 3600000) % 24  AS hr,
               price
        FROM read_parquet('{url}')
        WHERE exchange = 'binancef' AND symbol = 'BTCUSDT'
          AND (ts_ms // 3600000) % 24 BETWEEN {HOURS[0]} AND {HOURS[1]}
        ORDER BY tid
    """).fetchnumpy()
    tid, hr, px = rows["tid"], rows["hr"], rows["price"]
    if tid.size < 8:
        return {"date": date, "n": int(tid.size), "segments": 0, "s0": 0.0, "s1": 0.0,
                "ssq": 0.0, "n0": 0, "n1": 0}
    lp = np.log(px.astype(float))
    # a new segment starts wherever the id is not consecutive OR the hour changes
    brk = np.flatnonzero((np.diff(tid) != 1) | (np.diff(hr) != 0)) + 1
    seg_bounds = np.concatenate(([0], brk, [tid.size]))
    s0 = ssq = s1 = 0.0
    n0 = n1 = nseg = 0
    for a, b in zip(seg_bounds[:-1], seg_bounds[1:]):
        if b - a < 3:                    # need at least 2 dp to form one lag-1 pair
            continue
        dp = np.diff(lp[a:b])
        s0 += float(dp.sum()); ssq += float((dp * dp).sum()); n0 += dp.size
        s1 += float((dp[1:] * dp[:-1]).sum()); n1 += dp.size - 1
        nseg += 1
    return {"date": date, "n": int(tid.size), "segments": nseg,
            "s0": s0, "ssq": ssq, "s1": s1, "n0": n0, "n1": n1}


def interval_from_moments(g0: float, g1: float) -> dict:
    """The declared estimator, from POOLED moments rather than a concatenated series.

    Same closed form as hasbrouck.identified_interval_c; cross-checked against that function
    in main() on a simulated series, which is the control on this pooling code itself.
    """
    if g1 >= 0:
        return {"verdict": "INDETERMINATE",
                "reason": f"pooled gamma_1 = {g1:+.6e} is not negative"}
    disc = g0 * g0 - 4.0 * g1 * g1
    if disc < 0:
        return {"verdict": "INDETERMINATE", "reason": "no real MA(1) representation"}
    d = np.sqrt(disc)
    c2_lo, c2_hi = 0.5 * (g0 - d), -g1
    return {"verdict": "OK", "c2_lo": c2_lo, "c2_hi": c2_hi,
            "c_lo": float(np.sqrt(max(c2_lo, 0.0))), "c_hi": float(np.sqrt(c2_hi))}


def main() -> int:
    say = print
    say("PREREG-microstructure-001 — running as declared\n")

    # ---- control on the pooling code itself, BEFORE any real data ----
    rng = np.random.default_rng(20260806)
    c, lam, su, n = 4e-4, 3e-4, 2e-4, 400_000
    q = rng.choice([-1.0, 1.0], size=n)
    dp_sim = np.diff(np.cumsum(lam * q + rng.normal(0, su, n)) + c * q)
    g = hb.autocovariances(dp_sim, 2)
    mine = interval_from_moments(float(g[0]), float(g[1]))
    theirs = hb.identified_interval_c(dp_sim)
    ok = (abs(mine["c_lo"] - theirs["c_lo"]) < 1e-15
          and abs(mine["c_hi"] - theirs["c_hi"]) < 1e-15)
    say(f"  CONTROL on the pooling code: moments path vs hasbrouck.identified_interval_c -> "
        f"{'MATCH' if ok else 'MISMATCH — ABORT'}")
    if not ok:
        return 2

    dates, meta = eligible_dates()
    say(f"  dates in slice {SLICE_LO}..{SLICE_HI}: {meta['in_slice']} · "
        f"excluded {list(EXCLUDE)} · kept {meta['kept']}")
    say(f"  window UTC {HOURS[0]}-{HOURS[1]} · target c_book = {TARGET_BPS:.4f} bps\n")

    con = duckdb.connect()
    per_day, t0 = [], time.time()
    for i, d in enumerate(dates, 1):
        try:
            m = day_moments(con, d)
        except Exception as e:  # noqa: BLE001 — one unreadable partition must not kill the run
            say(f"  [{i:>2}/{len(dates)}] {d}  READ FAILED: {type(e).__name__} — excluded and counted")
            per_day.append({"date": d, "failed": str(e)[:120]})
            continue
        per_day.append(m)
        say(f"  [{i:>2}/{len(dates)}] {d}  n {m['n']:>9,}  segments {m['segments']:>5}  "
            f"pairs {m['n1']:>9,}")

    good = [m for m in per_day if "failed" not in m and m["n1"] > 0]
    N0 = sum(m["n0"] for m in good)
    N1 = sum(m["n1"] for m in good)
    mu = sum(m["s0"] for m in good) / N0
    g0 = sum(m["ssq"] for m in good) / N0 - mu * mu
    g1 = sum(m["s1"] for m in good) / N1 - mu * mu

    say(f"\n  pooled over {len(good)} dates: dp {N0:,} · lag-1 pairs {N1:,} "
        f"({time.time() - t0:.0f}s)")
    say(f"  gamma_0 = {g0:.6e}   gamma_1 = {g1:+.6e}   sigma2_w = {g0 + 2 * g1:.6e}")

    iv = interval_from_moments(g0, g1)
    if iv["verdict"] != "OK":
        say(f"\n  >>> VERDICT: INDETERMINATE — {iv['reason']}")
        verdict, detail = "INDETERMINATE", iv["reason"]
    else:
        lo, hi = iv["c_lo"] / BPS, iv["c_hi"] / BPS
        say(f"  identified interval for c: [{lo:.5f}, {hi:.5f}] bps")
        say(f"  target (book half-spread) : {TARGET_BPS:.5f} bps")
        if lo <= TARGET_BPS <= hi:
            verdict = "PASS"
            detail = "the trades-only interval contains the book-measured half-spread"
        elif lo > TARGET_BPS:
            verdict = "FAIL-HIGH"
            detail = "interval lies entirely above the target — Roll overstates"
        else:
            verdict = "FAIL-LOW"
            detail = "interval lies entirely below the target — no known mechanism predicts this"
        say(f"\n  >>> VERDICT: {verdict} — {detail}")

    out = REPO / "reports" / "prereg-microstructure-001-result.json"
    out.write_text(json.dumps({
        "declared_in": "docs/PREREG-microstructure-001.md",
        "window_utc_hours": list(HOURS), "slice": [SLICE_LO, SLICE_HI],
        "excluded": EXCLUDE, "dates_used": [m["date"] for m in good],
        "n_dp": N0, "n_pairs": N1, "gamma_0": g0, "gamma_1": g1, "sigma2_w": g0 + 2 * g1,
        "interval_bps": ([iv.get("c_lo", float('nan')) / BPS, iv.get("c_hi", float('nan')) / BPS]
                         if iv["verdict"] == "OK" else None),
        "target_bps": TARGET_BPS, "verdict": verdict, "detail": detail,
        "per_day": per_day,
    }, indent=2, default=float) + "\n")
    say(f"  result -> {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
