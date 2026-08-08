"""prereg_markout_001.py — run docs/PREREG-markout-001.md EXACTLY as declared.

Controls FIRST: the positive control (planted truth on simulation) and the negative control
(shuffled aggressor signs through the identical pipeline) both run before a single real
markout is computed, and either failing stops the run without touching real data.

Everything here was fixed in the declaration before this file existed: the 8x4 grid, the
calendar sampling frame, the trade-price proxy and its tolerance, the aggregation, the
bootstrap, and the exhaustive verdict table with its catch-all. Nothing may be tuned after
seeing a number.

Look: PREDICTIVE (recorded in the predictive column, same commit as the result).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np

REPO = Path(__file__).resolve().parent.parent
V = "hf://datasets/azulcoder/btc-quant-ticks/vision/binancef/BTCUSDT/aggTrades/date={d}/trades.parquet"
HORIZONS = [1, 5, 15, 60, 300, 900, 3600, 14400]            # seconds
OFFSETS_MS = [0, 10, 50, 200]
ANCHORS_PER_DAY = 20_000
SEED_BASE = 20260808
NEG_SEED = 424243        # amendment 3: fresh realization, not the one that motivated the rule change
NEG_DAYS = 30
BOOT = 400
BPS = 1e-4
TAKER, MAKER = 10.0, 4.0


def say(*a): print(*a, flush=True)


def frame_days() -> list[str]:
    """Calendar-deterministic: days 1, 8, 15, 22 of each month inside the archive span,
    intersected with what the Hub actually holds AT RUNTIME (skips counted)."""
    from huggingface_hub import HfApi
    import re
    fs = HfApi().list_repo_files("azulcoder/btc-quant-ticks", repo_type="dataset")
    have = {m.group(1) for f in fs
            if (m := re.match(r"vision/binancef/BTCUSDT/aggTrades/date=(\d{4}-\d{2}-\d{2})/trades\.parquet", f))}
    want = []
    d = dt.date(2019, 12, 31)
    while d <= dt.date(2026, 8, 1):
        if d.day in (1, 8, 15, 22):
            want.append(d.isoformat())
        d += dt.timedelta(days=1)
    used = [x for x in want if x in have]
    return used, len(want) - len(used)


def day_cells(ts_ms, px, sign, rng) -> dict:
    """All 32 cells for one day. Returns {(h, L): (median, mean, n)}. Pure numpy."""
    n = ts_ms.size
    out = {}
    max_need = (max(HORIZONS) + 1) * 1000 + max(OFFSETS_MS)
    eligible = np.flatnonzero(ts_ms <= ts_ms[-1] - max_need)
    # anchors eligible for the LARGEST horizon are used for every cell, so cell ns differ
    # only through tolerance skips, not through different anchor pools (declared frame).
    if eligible.size == 0:
        return {(h, L): (np.nan, np.nan, 0) for h in HORIZONS for L in OFFSETS_MS}
    take = min(ANCHORS_PER_DAY, eligible.size)
    idx = np.sort(rng.choice(eligible, size=take, replace=False))
    t0 = ts_ms[idx].astype(np.int64)
    s0 = sign[idx].astype(float)
    for h in HORIZONS:
        tol = min(max(200.0, 0.05 * h * 1000), 5000.0)       # declared: max(0.2s, 0.05h) cap 5s
        for L in OFFSETS_MS:
            t_ref = t0 + L
            t_tgt = t0 + L + h * 1000
            i_ref = np.searchsorted(ts_ms, t_ref, side="right")       # first trade ts > t_ref
            i_tgt = np.searchsorted(ts_ms, t_tgt, side="left")        # first trade ts >= t_tgt
            ok = (i_ref < n) & (i_tgt < n)
            ir = np.clip(i_ref, 0, n - 1); it = np.clip(i_tgt, 0, n - 1)
            ok &= (ts_ms[ir] - t_ref) <= tol
            ok &= (ts_ms[it] - t_tgt) <= tol
            ok &= it > ir
            if not ok.any():
                out[(h, L)] = (np.nan, np.nan, 0); continue
            m = s0[ok] * (px[it[ok]] - px[ir[ok]]) / px[ir[ok]] / BPS
            out[(h, L)] = (float(np.median(m)), float(np.mean(m)), int(m.size))
    return out


def pooled(day_stats: list[dict]) -> dict:
    """Headline per cell: median of day-medians; mean of day-means; total n; bootstrap CI."""
    rng = np.random.default_rng(7)
    res = {}
    for h in HORIZONS:
        for L in OFFSETS_MS:
            med = np.array([d[(h, L)][0] for d in day_stats if d[(h, L)][2] > 0])
            mea = np.array([d[(h, L)][1] for d in day_stats if d[(h, L)][2] > 0])
            ntot = int(sum(d[(h, L)][2] for d in day_stats))
            if med.size < 3:
                res[(h, L)] = {"median": np.nan, "mean": np.nan, "n": ntot,
                               "lo": np.nan, "hi": np.nan, "days": int(med.size)}
                continue
            boots = [float(np.median(med[rng.integers(0, med.size, med.size)]))
                     for _ in range(BOOT)]
            res[(h, L)] = {"median": float(np.median(med)), "mean": float(np.mean(mea)),
                           "n": ntot, "lo": float(np.percentile(boots, 2.5)),
                           "hi": float(np.percentile(boots, 97.5)), "days": int(med.size)}
    return res


def show(res: dict, title: str):
    say(f"\n  {title}")
    say(f"  {'h':>7}" + "".join(f"{f'L={L}ms':>26}" for L in OFFSETS_MS))
    for h in HORIZONS:
        row = f"  {h:>6}s"
        for L in OFFSETS_MS:
            c = res[(h, L)]
            row += (f"  {c['median']:+7.3f} [{c['lo']:+7.3f},{c['hi']:+7.3f}]"
                    if np.isfinite(c["median"]) else f"  {'(no data)':>24}")
        say(row)
    say(f"  {'n/sel':>7}" + "".join(f"{res[(HORIZONS[0], L)]['n']:>26,}" for L in OFFSETS_MS)
        + "   (baris h=1s; sel lain sebanding)")


# --------------------------------------------------------------------------- #
def positive_control() -> bool:
    """Control v3 per AMENDMENT 2. v2's algebra was right and its POWER was not: the
    per-window noise of the planted impact walk is ~lambda*2j/sqrt(n), so at long horizons
    the demanded 25% tolerance was inside the measurement's own noise. v3 fixes power the
    same way the real pipeline does — many independent days, aggregated by the median of
    day-medians — and confines the control to the short-horizon cells where signal/SE is
    real. The planted world is unchanged."""
    say("=" * 100)
    say("KONTROL POSITIF v3 (AMANDEMEN 2) — 30 hari simulasi, sel 5 s & 15 s")
    say("=" * 100)
    CTRL_H = [5, 15]
    N_DAYS, N_TR = 30, 200_000

    def sim_day(rho: float, lam_bps: float, c_bps: float, seed: int) -> dict:
        rng = np.random.default_rng(seed)
        lam, c, su = lam_bps * BPS, c_bps * BPS, 2.65e-5
        if rho == 0.0:
            q = rng.choice((-1.0, 1.0), size=N_TR)
        else:
            flips = np.where(rng.random(N_TR) < (1.0 + rho) / 2.0, 1.0, -1.0)
            flips[0] = 1.0
            q = np.cumprod(flips)                    # vectorized AR(1) sign chain
        m = 60_000.0 * np.exp(np.cumsum(lam * q + rng.normal(0, su, N_TR)))
        px = m * (1 + c * q)
        ts = np.cumsum(rng.exponential(100.0, N_TR)).astype(np.int64)
        return day_cells(ts, px, q, np.random.default_rng(seed + 1))

    def pooled_ctrl(rho, lam_bps, c_bps, seed0):
        days = [sim_day(rho, lam_bps, c_bps, seed0 + 10 * i) for i in range(N_DAYS)]
        meds = [np.median([d[(h, L)][0] for d in days if d[(h, L)][2] > 0])
                for h in CTRL_H for L in OFFSETS_MS]
        return float(np.median(meds))

    ok_all = True
    for lam_bps in (0.0, 2.0):
        med = pooled_ctrl(0.0, lam_bps, 4.0, 1000 + int(lam_bps))
        passed = abs(med) < 0.3
        ok_all &= passed
        say(f"  nol-recovery  iid, lambda={lam_bps}: median gabungan {med:+.3f} bps "
            f"(|.| < 0.3) -> {'PASS' if passed else 'FAIL'}")
    want = 2.0 * 0.36 / 0.4 - 0.5 * 0.6
    med = pooled_ctrl(0.6, 2.0, 0.5, 3000)
    passed = abs(med - want) <= 0.25 * want
    ok_all &= passed
    say(f"  sinyal-tanam  AR(1) rho=0.6: plateau tanam {want:+.2f}, terukur {med:+.3f} "
        f"(±25%) -> {'PASS' if passed else 'FAIL'}")
    return ok_all


def negative_control(days: list[str], con) -> tuple[bool, list]:
    say("\n" + "=" * 100)
    say(f"KONTROL NEGATIF — {len(days)} hari pertama kerangka, sisi agresor DIACAK dalam-hari")
    say("=" * 100)
    rng_shuffle = np.random.default_rng(NEG_SEED)
    day_stats = []
    for i, d in enumerate(days):
        try:
            r = con.execute(f"SELECT ts_ms, price, aggressor_buy FROM read_parquet('{V.format(d=d)}') "
                            f"ORDER BY ts_ms").fetchnumpy()
        except Exception:  # noqa: BLE001
            continue
        sign = np.where(r["aggressor_buy"], 1.0, -1.0)
        rng_shuffle.shuffle(sign)                       # the ONLY difference from the real run
        day_stats.append(day_cells(r["ts_ms"].astype(np.int64), r["price"].astype(float),
                                   sign, np.random.default_rng(SEED_BASE + i)))
        if (i + 1) % 10 == 0:
            say(f"  [{i+1:>3}/{len(days)}]")
    res = pooled(day_stats)
    show(res, "markout ACAK (harus ~0 di seluruh 32 sel):")
    # PASS rule v2 (amendment 3): the CI clause for every cell; the 0.1 bps magnitude
    # clause only where the statistic's own noise is measured well below it (h <= 900 s).
    # At 4 h a day holds ~6 independent windows and the CI half-width is ~0.5 bps — the
    # old rule demanded sub-noise precision, the third instance of that class in this file.
    ok = all(
        (res[(h, L)]["lo"] <= 0 <= res[(h, L)]["hi"])
        and (h > 900 or abs(res[(h, L)]["median"]) < 0.1)
        for h in HORIZONS for L in OFFSETS_MS if np.isfinite(res[(h, L)]["median"]))
    say(f"\n  >>> kontrol negatif: {'PASS — pipeline tidak bocor' if ok else 'FAIL — pipeline BOCOR, berhenti'}")
    return ok, day_stats


def main() -> int:
    con = duckdb.connect()
    days, skipped = frame_days()
    say(f"prereg_markout_001 — kerangka {len(days)} hari tersedia, {skipped} hari kerangka "
        f"hilang dari Hub (dilewati dan dihitung)")

    if not positive_control():
        say("\nKONTROL POSITIF GAGAL — instrumen tidak melihat parameter tanam. BERHENTI.")
        return 2
    neg_ok, _ = negative_control(days[:NEG_DAYS], con)
    if not neg_ok:
        return 2

    say("\n" + "=" * 100)
    say("DATA NYATA — persis pipeline yang sama, tanpa pengacakan")
    say("=" * 100)
    day_stats, used, failed = [], [], []
    t0 = time.time()
    for i, d in enumerate(days):
        try:
            r = con.execute(f"SELECT ts_ms, price, aggressor_buy FROM read_parquet('{V.format(d=d)}') "
                            f"ORDER BY ts_ms").fetchnumpy()
        except Exception as e:  # noqa: BLE001 — one unreadable day is counted, never silent
            failed.append(d); continue
        sign = np.where(r["aggressor_buy"], 1.0, -1.0)
        day_stats.append(day_cells(r["ts_ms"].astype(np.int64), r["price"].astype(float),
                                   sign, np.random.default_rng(SEED_BASE + i)))
        used.append(d)
        if (i + 1) % 20 == 0:
            say(f"  [{i+1:>3}/{len(days)}]  {time.time()-t0:6.0f}s")
    res = pooled(day_stats)
    show(res, f"MARKOUT NYATA — {len(used)} hari, median-dari-median-harian [95% CI], bps:")

    # ---- verdict, exhaustive, exactly as declared ----
    clear_taker = [(h, L) for h in HORIZONS for L in OFFSETS_MS
                   if np.isfinite(res[(h, L)]["lo"]) and res[(h, L)]["lo"] > TAKER]
    clear_maker = [(h, L) for h in HORIZONS for L in OFFSETS_MS
                   if np.isfinite(res[(h, L)]["lo"]) and res[(h, L)]["lo"] > MAKER]
    all_below = all(np.isfinite(res[(h, L)]["hi"]) and res[(h, L)]["hi"] < MAKER
                    for h in HORIZONS for L in OFFSETS_MS)
    if clear_taker:
        verdict, detail = "LOLOS-TAKER", f"sel {clear_taker}"
    elif clear_maker:
        verdict, detail = "LOLOS-MAKER", f"sel {clear_maker}"
    elif all_below:
        verdict, detail = "GAGAL", "seluruh 32 interval sepenuhnya di bawah 4 bps"
    else:
        verdict, detail = "INDETERMINATE", ("ada interval yang melintasi ambang, atau cabang "
                                            "yang tidak diantisipasi — amandemen wajib")
    say(f"\n  >>> VONIS: {verdict} — {detail}")

    (REPO / "reports" / "prereg-markout-001-result.json").write_text(json.dumps({
        "declared_in": "docs/PREREG-markout-001.md",
        "days_used": used, "days_failed": failed, "frame_skipped": skipped,
        "cells": {f"{h}|{L}": res[(h, L)] for h in HORIZONS for L in OFFSETS_MS},
        "verdict": verdict, "detail": detail,
    }, indent=2, default=float) + "\n")
    say("  hasil -> reports/prereg-markout-001-result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
