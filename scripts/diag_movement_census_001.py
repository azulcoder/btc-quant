"""diag_movement_census_001.py — realized |price move| per horizon, against the fee hurdles.

The external feasibility report (docs/EXTERNAL-scalping-feasibility-001.md) estimates RMS
moves per horizon from "typical BTC volatility" and says outright: recompute from your own
data. This does that. It is a CENSUS of a tape property — no signal is evaluated, nothing is
selected, and no viability verdict is attached: the thresholds below are published fee
constants declared before the run, and any GO/NO-GO built on this table belongs to a PREREG.

Look: PROVENANCE DIAGNOSTIC (diagnostic column). No returns, no P&L, no strategy.
Read-only; frozen exploration slice only, damaged days excluded and counted.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import duckdb, numpy as np

REPO = Path(__file__).resolve().parent.parent
REC = "hf://datasets/azulcoder/btc-quant-ticks/data/date={d}/trades.parquet"
LO, HI = "2026-07-05", "2026-08-03"
EXCLUDE = {"2026-07-13": "ZSTD read failure (EDA §2a)",
           "2026-08-03": "recorded-damage entry (28,428 prints)"}
HORIZONS = [1, 10, 60, 300, 900, 3600, 14400]          # seconds
THRESH_BPS = [4.0, 7.0, 10.0, 20.0]                     # maker RT, maker-in/taker-out, taker RT, 2x
STALE_FRAC = 0.10                                       # endpoint must be within max(2s, 10% of tau)
BPS = 1e-4


def say(*a): print(*a, flush=True)


def dates():
    from huggingface_hub import HfApi
    import re
    fs = HfApi().list_repo_files("azulcoder/btc-quant-ticks", repo_type="dataset")
    return sorted(m.group(1) for f in fs
                  if (m := re.match(r"data/date=(\d{4}-\d{2}-\d{2})/trades\.parquet", f))
                  and LO <= m.group(1) <= HI and m.group(1) not in EXCLUDE)


def main() -> int:
    con = duckdb.connect()
    ds = dates()
    say(f"diag_movement_census_001 — |move| per horizon, binancef/BTCUSDT")
    say(f"  slice {LO}..{HI} · {len(ds)} day(s) after exclusions {sorted(EXCLUDE)}")
    say(f"  horizons {HORIZONS} s · thresholds {THRESH_BPS} bps (published fee constants)\n")

    acc = {t: [] for t in HORIZONS}
    skips = {t: 0 for t in HORIZONS}
    for i, d in enumerate(ds, 1):
        try:
            r = con.execute(f"""
                SELECT (ts_ms // 1000) AS sec, arg_max(price, ts_ms) AS px
                FROM read_parquet('{REC.format(d=d)}')
                WHERE exchange='binancef' AND symbol='BTCUSDT'
                GROUP BY 1 ORDER BY 1
            """).fetchnumpy()
        except Exception as e:  # noqa: BLE001 — excluded and COUNTED, never silent
            say(f"  [{i:>2}/{len(ds)}] {d}  READ FAILED ({type(e).__name__}) — counted")
            continue
        sec = r["sec"].astype(np.int64)
        px = r["px"].astype(float)
        if sec.size < 2:
            # 2026-07-23/24/25 hold zero binancef trades (known feed hole) — counted, not fatal
            say(f"  [{i:>2}/{len(ds)}] {d}  EMPTY for binancef — counted and skipped")
            continue
        lp = np.log(px)
        t0, t1 = int(sec[0]), int(sec[-1])
        for tau in HORIZONS:
            tol = max(2, int(tau * STALE_FRAC))
            anchors = np.arange(t0, t1 - tau, tau)      # non-overlapping windows
            ia = np.searchsorted(sec, anchors, side="right") - 1
            ib = np.searchsorted(sec, anchors + tau, side="right") - 1
            ok = (ia >= 0) & (ib >= 0)
            # the endpoint price must be FRESH: a stale endpoint would measure a feed hole,
            # not the market (gaps stay gaps — a move across a hole is not a move)
            ok &= (anchors - sec[np.clip(ia, 0, None)]) <= tol
            ok &= ((anchors + tau) - sec[np.clip(ib, 0, None)]) <= tol
            ok &= ib > ia
            mv = np.abs(lp[ib[ok]] - lp[ia[ok]]) / BPS
            acc[tau].append(mv)
            skips[tau] += int((~ok).sum())
        say(f"  [{i:>2}/{len(ds)}] {d}  seconds with trades {sec.size:,}")

    say("\n" + "=" * 108)
    say("REALIZED |move|, bps of log price — binancef/BTCUSDT, per horizon  [DIUKUR]")
    say("=" * 108)
    hdr = f"  {'tau':>7}{'n':>10}{'skipped':>9}{'p50':>9}{'p90':>9}{'p99':>9}{'RMS':>9}{'RMS/sqrt(tau)':>15}"
    hdr += "".join(f"{'>'+str(int(x))+'bps':>9}" for x in THRESH_BPS)
    say(hdr)
    out = {}
    for tau in HORIZONS:
        a = np.concatenate(acc[tau]) if acc[tau] else np.array([])
        if a.size == 0:
            say(f"  {tau:>7}  no samples"); continue
        rms = float(np.sqrt(np.mean(a * a)))
        row = {"n": int(a.size), "skipped": skips[tau],
               "p50": float(np.percentile(a, 50)), "p90": float(np.percentile(a, 90)),
               "p99": float(np.percentile(a, 99)), "rms": rms,
               "rms_per_sqrt_tau": rms / np.sqrt(tau),
               "frac_gt": {str(x): float((a > x).mean()) for x in THRESH_BPS}}
        out[str(tau)] = row
        say(f"  {tau:>6}s{a.size:>10,}{skips[tau]:>9,}"
            f"{row['p50']:>9.3f}{row['p90']:>9.3f}{row['p99']:>9.3f}{rms:>9.3f}"
            f"{row['rms_per_sqrt_tau']:>15.4f}"
            + "".join(f"{row['frac_gt'][str(x)]:>9.2%}" for x in THRESH_BPS))
    say("\n  RMS/sqrt(tau) constant would mean pure diffusion; the report ASSUMED that scaling.")
    say("  No verdict is attached here: thresholds are declared fee constants, and any GO/NO-GO")
    say("  that conditions on this table belongs to a PREREG with its own look accounting.")
    (REPO / "reports" / "movement-census-001.json").write_text(
        json.dumps({"slice": [LO, HI], "excluded": EXCLUDE, "days_used": len(ds),
                    "stale_frac": STALE_FRAC, "horizons_s": HORIZONS,
                    "thresholds_bps": THRESH_BPS, "rows": out},
                   indent=2, default=float) + "\n")
    say("  result -> reports/movement-census-001.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
