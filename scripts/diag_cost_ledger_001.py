"""diag_cost_ledger_001.py — every cost term in bps, from the data that exists.

Measures the terms a live system pays and puts them in one ordered table. It does not
recommend anything, does not touch any estimator, and builds nothing.

Look classification: PROVENANCE DIAGNOSTIC. Counted in the diagnostic column.
Read-only, frozen exploration slice only (`2026-07-05..2026-08-03`).

Three of the four terms come out of the tape. The fourth — venue fees — is a published rate
and cannot be measured from data at all, so it is CITED and labelled, never derived.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import numpy as np

REPO = Path(__file__).resolve().parent.parent
D = "hf://datasets/azulcoder/btc-quant-ticks/data/date={d}/{t}.parquet"
SLICE_LO, SLICE_HI = "2026-07-05", "2026-08-03"
NOTIONALS = [10_000.0, 100_000.0, 1_000_000.0]
BPS = 1e-4
# Walking the book and the depth-stability scan read every level of every snapshot, which is
# far heavier than the top-of-book work BOOK-001 did. Declared subset, same days as BOOK-001.
HEAVY_DAYS = ["2026-07-30", "2026-07-31", "2026-08-01", "2026-08-03"]


def say(*a):
    print(*a, flush=True)


def dates(table: str) -> list[str]:
    from huggingface_hub import HfApi
    import re
    fs = HfApi().list_repo_files("azulcoder/btc-quant-ticks", repo_type="dataset")
    have = {m.group(1) for f in fs
            if (m := re.match(rf"data/date=(\d{{4}}-\d{{2}}-\d{{2}})/{table}\.parquet", f))}
    return sorted(x for x in have if SLICE_LO <= x <= SLICE_HI)


# --------------------------------------------------------------------------- #
def item_1b(con, out):
    say("\n" + "=" * 100)
    say("1b. FUNDING — realized rate per 8h interval, frozen slice")
    say("=" * 100)
    ds = dates("funding_mark")
    rows = {}
    for d in ds:
        try:
            r = con.execute(f"""
                SELECT exchange, symbol, next_funding_ts, arg_max(funding_rate, ts_ms) fr
                FROM read_parquet('{D.format(d=d, t="funding_mark")}')
                WHERE funding_rate IS NOT NULL
                GROUP BY 1,2,3
            """).fetchall()
        except Exception as e:  # noqa: BLE001 — excluded and COUNTED
            out.setdefault("funding_failed_days", []).append(d)
            continue
        for ex, sy, nft, fr in rows_iter(r):
            rows.setdefault((ex, sy), {})[nft] = fr
    say(f"  {'venue':<24}{'intervals':>11}{'p05':>11}{'p50':>11}{'p95':>11}{'max':>11}"
        f"{'% neg':>9}{'bps/day held':>14}")
    for key in sorted(rows):
        v = np.array(sorted(rows[key].values()), dtype=float)
        if v.size < 5:
            continue
        # one interval is 8h, so a fully-held position pays three of them per day
        p = [float(np.percentile(v, q)) for q in (5, 50, 95, 100)]
        per_day_bps = float(np.median(v)) * 3.0 / BPS
        say(f"  {key[0]+'/'+key[1]:<24}{v.size:>11,}"
            + "".join(f"{x/BPS:>11.4f}" for x in p)
            + f"{float((v < 0).mean()):>8.1%}{per_day_bps:>14.4f}")
        out.setdefault("funding", {})[f"{key[0]}/{key[1]}"] = {
            "n_intervals": int(v.size), "p05_bps": p[0] / BPS, "p50_bps": p[1] / BPS,
            "p95_bps": p[2] / BPS, "max_bps": p[3] / BPS,
            "frac_negative": float((v < 0).mean()), "bps_per_day_held": per_day_bps}
    say("  Each row is ONE settlement (deduped by next_funding_ts, last quote before it).")
    say("  A funding payment is signed: positive means longs pay shorts.")


def rows_iter(r):
    for x in r:
        yield x[0], x[1], x[2], x[3]


# --------------------------------------------------------------------------- #
def walk(bids_json, asks_json, notional):
    """bps beyond the mid to fill `notional` USD by walking one side. Returns (buy, sell)."""
    import json as _j
    out = []
    for side, arr in (("buy", asks_json), ("sell", bids_json)):
        lv = _j.loads(arr)
        if not lv:
            out.append(np.nan)
            continue
        px = np.array([float(x[0]) for x in lv])
        qt = np.array([float(x[1]) for x in lv])
        val = px * qt
        cum = np.cumsum(val)
        if cum[-1] < notional:              # book too thin: NOT extrapolated, reported as NaN
            out.append(np.nan)
            continue
        k = int(np.searchsorted(cum, notional))
        take = qt.copy()
        if k > 0:
            rem = notional - cum[k - 1]
        else:
            rem = notional
        take[k] = rem / px[k]
        take[k + 1:] = 0.0
        vwap = float(np.sum(px[: k + 1] * take[: k + 1]) / np.sum(take[: k + 1]))
        out.append(vwap)
    return out


def item_1c_and_2c(con, out):
    say("\n" + "=" * 100)
    say("1c. WALKING THE BOOK — cost beyond the mid, and beyond the half-spread")
    say("=" * 100)
    say(f"  declared subset {HEAVY_DAYS} · notionals {[f'${int(n):,}' for n in NOTIONALS]}")
    imp = {}
    stab = {}
    for d in HEAVY_DAYS:
        try:
            r = con.execute(f"""
                SELECT exchange, symbol, ts_ms, bids, asks
                FROM read_parquet('{D.format(d=d, t="depth_snapshots")}')
                ORDER BY exchange, symbol, ts_ms
            """).fetchall()
        except Exception:  # noqa: BLE001
            out.setdefault("depth_failed_days", []).append(d)
            continue
        import json as _j
        prev = {}
        for ex, sy, ts, b, a in r:
            key = (ex, sy)
            try:
                bl, al = _j.loads(b), _j.loads(a)
            except Exception:  # noqa: BLE001
                continue
            if not bl or not al:
                continue
            bid, ask = float(bl[0][0]), float(al[0][0])
            if bid >= ask:
                continue
            mid = 0.5 * (bid + ask)
            half = (ask - bid) / 2.0
            for n in NOTIONALS:
                vb, vs = walk(b, a, n)
                for lab, v, sign in (("buy", vb, +1.0), ("sell", vs, -1.0)):
                    if not np.isfinite(v):
                        imp.setdefault((key, n), {"beyond_half": [], "thin": 0})["thin"] += 1
                        continue
                    total = sign * (v - mid) / mid / BPS          # bps from mid
                    beyond = total - (half / mid / BPS)           # bps beyond half-spread
                    imp.setdefault((key, n), {"beyond_half": [], "thin": 0})["beyond_half"].append(beyond)
            # 2c — top-5 depth stability between consecutive snapshots
            top5 = sum(float(x[1]) for x in bl[:5]) + sum(float(x[1]) for x in al[:5])
            if key in prev and prev[key][1] > 0:
                dt = ts - prev[key][0]
                if 0 < dt <= 2000:                                # consecutive, not across a hole
                    stab.setdefault(key, []).append(abs(top5 - prev[key][1]) / prev[key][1])
            prev[key] = (ts, top5)
        say(f"  {d} done")

    say(f"\n  {'venue':<24}{'notional':>12}{'n':>9}{'thin':>8}"
        f"{'p50 bps beyond half':>22}{'p95':>10}")
    for (key, n), v in sorted(imp.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        arr = np.array(v["beyond_half"], float)
        if arr.size == 0:
            say(f"  {key[0]+'/'+key[1]:<24}${int(n):>11,}{0:>9}{v['thin']:>8}"
                f"{'BOOK TOO THIN AT EVERY SNAPSHOT':>22}")
            continue
        say(f"  {key[0]+'/'+key[1]:<24}${int(n):>11,}{arr.size:>9,}{v['thin']:>8,}"
            f"{np.percentile(arr,50):>22.4f}{np.percentile(arr,95):>10.4f}")
        out.setdefault("walk", {})[f"{key[0]}/{key[1]}|{int(n)}"] = {
            "n": int(arr.size), "n_thin": int(v["thin"]),
            "p50_bps_beyond_half": float(np.percentile(arr, 50)),
            "p95_bps_beyond_half": float(np.percentile(arr, 95))}
    say("  'thin' = snapshots whose visible book could not fill the notional. NOT extrapolated.")

    say("\n" + "=" * 100)
    say("2c. DEPTH STABILITY — |change| in top-5 depth between consecutive snapshots")
    say("=" * 100)
    say(f"  {'venue':<24}{'n pairs':>11}{'p50':>10}{'p95':>10}")
    for key in sorted(stab):
        s = np.array(stab[key], float)
        say(f"  {key[0]+'/'+key[1]:<24}{s.size:>11,}{np.percentile(s,50):>9.2%}"
            f"{np.percentile(s,95):>10.2%}")
        out.setdefault("depth_stability", {})[f"{key[0]}/{key[1]}"] = {
            "n": int(s.size), "p50": float(np.percentile(s, 50)),
            "p95": float(np.percentile(s, 95))}
    say("  Pairs separated by more than 2000 ms are excluded — those cross a feed hole,")
    say("  not a cadence step, and would measure the hole rather than the book.")


# --------------------------------------------------------------------------- #
def item_3(out):
    say("\n" + "=" * 100)
    say("3. CROSS-CHECK — does 2 x 0.0078 bps x 64,000 come back to one tick?")
    say("=" * 100)
    for anchor, lab in ((0.0078, "0.0078 (as quoted in PREREG-microstructure-001)"),
                        (0.01561 / 2, "0.007805 (half of BOOK-001's measured 0.01561)")):
        got = 2.0 * anchor * BPS * 64_000.0
        say(f"  2 x {anchor:.6f} bps x 64,000 = ${got:.6f}   vs one tick $0.10   "
            f"-> {'MATCH' if abs(got - 0.10) < 5e-4 else 'MISMATCH'} "
            f"({(got/0.10 - 1):+.3%})   [{lab}]")
        out.setdefault("cross_check", {})[lab] = {"usd": got, "vs_tick": got / 0.10 - 1}


# --------------------------------------------------------------------------- #
def main() -> int:
    con = duckdb.connect()
    out: dict = {"slice": [SLICE_LO, SLICE_HI], "heavy_days": HEAVY_DAYS}
    say("diag_cost_ledger_001 — cost terms in bps, from the data that exists")
    item_1b(con, out)
    item_1c_and_2c(con, out)
    item_3(out)
    p = REPO / "reports" / "cost-ledger-001.json"
    p.write_text(json.dumps(out, indent=2, default=float) + "\n")
    say(f"\n  result -> {p.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
