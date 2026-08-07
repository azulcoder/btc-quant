"""diag_provenance_001.py — is the anomalous ACF on Delta p a fact about the market or an
artefact of the tape?

Runs the four blocks declared in `docs/DIAG-provenance-001.md`, whose PREDICTIONS were
committed before this file existed (`8196b5a`). Measure, report, stop: this script fixes
nothing and changes nothing. Every number it prints is [DIUKUR].

Look classification: PROVENANCE DIAGNOSTIC. It inspects tape integrity — row counts,
duplicates, ordering, distribution shape, and an A/B against the venue's own archive. No
returns, no positions, no predictive specification. Counted in the diagnostic column.

Read-only. Touches no LockBox data: the slice ends 2026-08-03, two days before the boundary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from btcquant import hasbrouck as hb  # noqa: E402

REC = "hf://datasets/azulcoder/btc-quant-ticks/data/date={d}/trades.parquet"
ARC = "hf://datasets/azulcoder/btc-quant-ticks/vision/binancef/BTCUSDT/aggTrades/date={d}/trades.parquet"
SLICE_LO, SLICE_HI = "2026-07-05", "2026-08-03"
HOURS = (12, 23)                 # the window PREREG-001 used
BOTH_DAYS = ["2026-07-30", "2026-07-31", "2026-08-01"]   # in BOTH sources
FOCUS = "2026-07-30"             # blocks B and D run here, so C is directly comparable
TICK = 0.1
LAGS = 8


def acf(dp: np.ndarray, lags: int = LAGS) -> list[float]:
    g = hb.autocovariances(dp, lags)
    return [float(v / g[0]) for v in g[1:]] if g[0] > 0 else [float("nan")] * lags


def fmt(vals, w=8, p=3):
    return "".join(f"{v:>{w}.{p}f}" for v in vals)


# --------------------------------------------------------------------------- #
def block_a(con, out):
    print("\n" + "=" * 78)
    print("BLOCK A — input series audit")
    print("=" * 78)

    g = REC.format(d="2026-0*")
    print("\nA1. (exchange, symbol) pairs present in the slice, NO filter applied:")
    rows = con.execute(f"""
        SELECT exchange, symbol, count(*) n FROM read_parquet('{g}', hive_partitioning=1)
        WHERE date BETWEEN DATE '{SLICE_LO}' AND DATE '{SLICE_HI}'
        GROUP BY 1,2 ORDER BY n DESC
    """).fetchall()
    for e, s, n in rows:
        print(f"    {e:<12} {s:<14} {n:>12,}")
    out["A1_pairs"] = [{"exchange": e, "symbol": s, "n": int(n)} for e, s, n in rows]
    print(f"    -> {len(rows)} distinct (exchange, symbol) pair(s)")

    print("\nA2. duplicate (exchange, symbol, trade_id) per day, binancef/BTCUSDT:")
    rows = con.execute(f"""
        SELECT date, count(*) n, count(*) - count(DISTINCT trade_id) dup
        FROM read_parquet('{g}', hive_partitioning=1)
        WHERE date BETWEEN DATE '{SLICE_LO}' AND DATE '{SLICE_HI}'
          AND exchange='binancef' AND symbol='BTCUSDT'
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    tot = dtot = 0
    worst = []
    for d, n, dup in rows:
        tot += n; dtot += dup
        if dup: worst.append((str(d), int(n), int(dup)))
    print(f"    days {len(rows)} · rows {tot:,} · duplicate rows {dtot:,} "
          f"({dtot / max(tot,1):.4%})")
    for d, n, dup in worst[:8]:
        print(f"      {d}  {dup:>7,} of {n:>10,}  ({dup/n:.4%})")
    out["A2"] = {"days": len(rows), "rows": tot, "dups": dtot,
                 "frac": dtot / max(tot, 1), "per_day": worst}

    print(f"\nA3/A4. ordering and timestamp ties, per day (binancef/BTCUSDT, UTC {HOURS[0]}-{HOURS[1]}):")
    a34 = []
    for d in BOTH_DAYS:
        r = con.execute(f"""
            WITH s AS (
              SELECT CAST(trade_id AS BIGINT) tid, ts_ms
              FROM read_parquet('{REC.format(d=d)}')
              WHERE exchange='binancef' AND symbol='BTCUSDT'
                AND (ts_ms // 3600000) % 24 BETWEEN {HOURS[0]} AND {HOURS[1]}
            ), o AS (SELECT tid, ts_ms, lag(tid) OVER (ORDER BY ts_ms) prev_tid,
                            lag(ts_ms) OVER (ORDER BY ts_ms) prev_ts FROM s)
            SELECT count(*) n,
                   count(*) FILTER (WHERE prev_tid IS NOT NULL AND tid < prev_tid) viol,
                   count(*) FILTER (WHERE prev_ts = ts_ms) same_ts,
                   count(DISTINCT ts_ms) distinct_ts
            FROM o
        """).fetchone()
        n, viol, same, dts = int(r[0]), int(r[1]), int(r[2]), int(r[3])
        grp = n / max(dts, 1)
        print(f"    {d}  n {n:>9,} · id-order violations after ORDER BY ts {viol:>8,} "
              f"({viol/max(n,1):>6.1%}) · rows sharing ts {same/max(n,1):>6.1%} · "
              f"mean ts-group {grp:.2f}")
        a34.append({"date": d, "n": n, "violations": viol, "viol_frac": viol/max(n,1),
                    "same_ts_frac": same/max(n,1), "mean_ts_group": grp})
    out["A3_A4"] = a34

    print("\nA5. is `price` the aggTrade price?")
    print("    Already established by an independent route: tests/test_vision_overlap.py")
    print("    joins the recorded store against the venue's own aggTrades archive and")
    print("    measured max|dprice| = 0.0 with ts_mismatch 0 and side_mismatch 0.")
    print("    -> CONFIRMED, and not re-derived here.")
    out["A5"] = "CONFIRMED via test_vision_overlap (max|dprice| = 0.0 vs the venue archive)"


# --------------------------------------------------------------------------- #
def load(con, url, order_sql, dedup=True, hours=HOURS):
    dd = "DISTINCT ON (CAST(trade_id AS BIGINT))" if dedup else ""
    ob_dd = "ORDER BY CAST(trade_id AS BIGINT)" if dedup else ""
    inner = f"""
        SELECT {dd} CAST(trade_id AS BIGINT) tid, ts_ms, price
        FROM read_parquet('{url}')
        WHERE exchange='binancef' AND symbol='BTCUSDT'
          AND (ts_ms // 3600000) % 24 BETWEEN {hours[0]} AND {hours[1]}
        {ob_dd}
    """
    q = f"SELECT tid, ts_ms, price FROM ({inner}) {order_sql}"
    r = con.execute(q).fetchnumpy()
    return r["price"].astype(float)


def block_b(con, out):
    print("\n" + "=" * 78)
    print(f"BLOCK B — ordering sensitivity, {FOCUS}, UTC {HOURS[0]}-{HOURS[1]}")
    print("=" * 78)
    url = REC.format(d=FOCUS)
    variants = [
        ("(i)   no ORDER BY at all", ""),
        ("(ii)  ORDER BY trade_id", "ORDER BY tid"),
        ("(iii) ORDER BY ts, trade_id", "ORDER BY ts_ms, tid"),
        ("(iv)  ORDER BY ts only", "ORDER BY ts_ms"),
    ]
    print(f"\n  {'variant':<30}{'n':>10}" + "".join(f"{'rho'+str(k):>8}" for k in range(1, LAGS+1)))
    res = {}
    for label, ob in variants:
        px = load(con, url, ob)
        dp = np.diff(np.log(px))
        a = acf(dp)
        print(f"  {label:<30}{dp.size:>10,}{fmt(a)}")
        res[label] = {"n": int(dp.size), "acf": a}
    r1 = [res[l]["acf"][0] for l, _ in variants]
    spread = max(r1) - min(r1)
    print(f"\n  rho_1 across orderings: min {min(r1):.4f} max {max(r1):.4f} "
          f"-> spread {spread:.4f}   (rule fires at > 0.05)")
    out["B"] = {"variants": res, "rho1_spread": spread}
    return spread


# --------------------------------------------------------------------------- #
def block_c(con, out):
    print("\n" + "=" * 78)
    print("BLOCK C — recorded store vs the venue archive, same days, same window")
    print("=" * 78)
    print(f"\n  {'date':<12}{'source':<10}{'n':>10}{'gamma_0':>13}" +
          "".join(f"{'rho'+str(k):>8}" for k in range(1, 5)))
    rows = []
    for d in BOTH_DAYS:
        entry = {"date": d}
        for name, url in (("recorded", REC.format(d=d)), ("archive", ARC.format(d=d))):
            try:
                px = load(con, url, "ORDER BY ts_ms, tid")
            except Exception as e:  # noqa: BLE001
                print(f"  {d:<12}{name:<10}READ FAILED: {type(e).__name__}")
                entry[name] = {"failed": str(e)[:100]}
                continue
            dp = np.diff(np.log(px))
            g0 = float(hb.autocovariances(dp, 1)[0])
            a = acf(dp)
            print(f"  {d:<12}{name:<10}{dp.size:>10,}{g0:>13.4e}{fmt(a[:4])}")
            entry[name] = {"n": int(dp.size), "gamma_0": g0, "acf": a}
        if "recorded" in entry and "archive" in entry \
           and "failed" not in entry["recorded"] and "failed" not in entry["archive"]:
            d1 = abs(entry["recorded"]["acf"][0] - entry["archive"]["acf"][0])
            dn = entry["archive"]["n"] - entry["recorded"]["n"]
            entry["abs_rho1_diff"] = d1
            entry["row_diff_archive_minus_recorded"] = dn
            print(f"  {'':<12}{'DIFF':<10}{dn:>+10,}{'':>13}{d1:>8.4f}  <- |rho1 gap| "
                  f"(rule fires at > 0.05)")
        rows.append(entry)
    out["C"] = rows
    gaps = [r["abs_rho1_diff"] for r in rows if "abs_rho1_diff" in r]
    return max(gaps) if gaps else float("nan")


# --------------------------------------------------------------------------- #
def block_d(con, out):
    print("\n" + "=" * 78)
    print(f"BLOCK D — distribution shape and concentration of gamma_1, {FOCUS}")
    print("=" * 78)
    px = load(con, REC.format(d=FOCUS), "ORDER BY ts_ms, tid")
    draw = np.diff(px)
    dp = np.diff(np.log(px))

    print("\nD1. |price change| in ticks (tick = 0.1 USDT):")
    ticks = np.abs(draw) / TICK
    edges = [0, 1, 2, 3, 5, 10, 20, 30]
    d1 = {}
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else None
        f = float(((ticks >= lo) & (ticks < hi)).mean()) if hi else float((ticks >= lo).mean())
        lab = f"{lo}" if hi == lo + 1 else (f"{lo}-{hi-1}" if hi else f"{lo}+")
        d1[lab] = f
        print(f"    {lab:>7} tick : {f:7.3%}")
    print(f"    mean |dp| in ticks: {ticks.mean():.2f} · sd(dp) = {dp.std():.3e} "
          f"= {dp.std()/1e-4:.3f} bps = {dp.std()*px.mean()/TICK:.2f} ticks")
    out["D1"] = d1

    print("\nD2. concentration of sum(dp_t * dp_{t+1}):")
    prod = dp[1:] * dp[:-1]
    tot = float(prod.sum())
    order = np.argsort(-np.abs(prod))
    d2 = {}
    for frac in (0.001, 0.01, 0.05):
        k = max(int(len(prod) * frac), 1)
        share = float(prod[order[:k]].sum() / tot) if tot != 0 else float("nan")
        d2[f"top_{frac:.3%}"] = share
        print(f"    top {frac:>6.1%} of |products| ({k:>7,} of {len(prod):,}) "
              f"contributes {share:>7.1%} of the sum")
    out["D2"] = d2

    print("\nD3. rho_1 after winsorising dp:")
    base = acf(dp, 4)
    print(f"    {'none':<12}{fmt(base)}")
    d3 = {"none": base}
    for q in (99.9, 99.0):
        lo, hi = np.percentile(dp, [100 - q, q])
        w = np.clip(dp, lo, hi)
        a = acf(w, 4)
        d3[f"p{q}"] = a
        print(f"    {'p'+str(q):<12}{fmt(a)}   move in rho_1: {a[0]-base[0]:+.4f}")
    out["D3"] = d3

    print("\nD4. rho_1 on rows with dp != 0 only:")
    nz = dp[dp != 0]
    a = acf(nz, 4)
    print(f"    dropped {1-len(nz)/len(dp):.3%} of rows -> {fmt(a)}   "
          f"move in rho_1: {a[0]-base[0]:+.4f}")
    out["D4"] = {"acf": a, "dropped_frac": 1 - len(nz) / len(dp),
                 "rho1_move": a[0] - base[0]}
    return d2["top_1.000%"], min(abs(d3["p99.0"][0] - base[0]), abs(d3["p99.9"][0] - base[0])), \
        d3["p99.0"][0] - base[0]


# --------------------------------------------------------------------------- #
def main() -> int:
    print("diag_provenance_001 — PROVENANCE DIAGNOSTIC, measure and stop")
    print(f"  slice {SLICE_LO}..{SLICE_HI} · window UTC {HOURS[0]}-{HOURS[1]} · focus {FOCUS}")
    print("  predictions were committed BEFORE this file existed: docs/DIAG-provenance-001.md")
    con = duckdb.connect()
    out: dict = {}

    block_a(con, out)
    b_spread = block_b(con, out)
    c_gap = block_c(con, out)
    d2_top1, _, d3_move = block_d(con, out)

    print("\n" + "=" * 78)
    print("VERDICT, by the rule declared before any number existed")
    print("=" * 78)
    npairs = len(out["A1_pairs"])
    triggers = []
    if npairs > 1:
        triggers.append(f"A1: {npairs} (exchange, symbol) pairs present")
    if b_spread > 0.05:
        triggers.append(f"B: rho_1 spread across orderings {b_spread:.4f} > 0.05")
    if np.isfinite(c_gap) and c_gap > 0.05:
        triggers.append(f"C: |rho1(archive) - rho1(recorded)| {c_gap:.4f} > 0.05")
    if d2_top1 > 0.5:
        triggers.append(f"D2: top 1% contributes {d2_top1:.1%} > 50%")
    if d3_move > 0.10:
        triggers.append(f"D3: winsorising moved rho_1 toward zero by {d3_move:+.4f}")

    for t in triggers:
        print(f"  TRIGGER  {t}")
    if triggers:
        verdict = "ARTEFAK"
    else:
        rec = [r for r in out["C"] if "abs_rho1_diff" in r]
        agree = rec and all(r["recorded"]["acf"][0] < -0.3 and r["recorded"]["acf"][1] > 0
                            for r in rec)
        verdict = "PASAR" if agree else "TIDAK KONKLUSIF"
        print(f"  no trigger fired -> {verdict}")
    print(f"\n  >>> VERDICT: {verdict}")
    out["verdict"] = verdict
    out["triggers"] = triggers

    p = REPO / "reports" / "diag-provenance-001.json"
    p.write_text(json.dumps(out, indent=2, default=float) + "\n")
    print(f"  result -> {p.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
