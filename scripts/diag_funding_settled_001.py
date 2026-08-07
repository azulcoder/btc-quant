"""diag_funding_settled_001.py — is the recorded funding rate SETTLED or PREDICTED?

`DIAG-cost-ledger-001` reported 1.8441 bps/day held on binancef, the second-largest term in
the whole ledger. Its shape was suspicious: three independent venues whose maximum is exactly
1.0000 bps to four decimals, with a median BELOW that.

Look: PROVENANCE DIAGNOSTIC. No returns, no P&L, no estimator.
Read-only, frozen slice `2026-07-05..2026-08-03`.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import duckdb, numpy as np
from collections import Counter

REPO = Path(__file__).resolve().parent.parent
D = "hf://datasets/azulcoder/btc-quant-ticks/data/date={d}/funding_mark.parquet"
LO, HI = "2026-07-05", "2026-08-03"
BPS = 1e-4
SETTLE_HOURS = (0, 8, 16)


def say(*a): print(*a, flush=True)


def dates():
    from huggingface_hub import HfApi
    import re
    fs = HfApi().list_repo_files("azulcoder/btc-quant-ticks", repo_type="dataset")
    return sorted(m.group(1) for f in fs
                  if (m := re.match(r"data/date=(\d{4}-\d{2}-\d{2})/funding_mark\.parquet", f))
                  and LO <= m.group(1) <= HI)


def main() -> int:
    con = duckdb.connect(); out = {}
    ds = dates()
    say(f"diag_funding_settled_001 — slice {LO}..{HI}, {len(ds)} days\n")

    raw = {}
    for d in ds:
        try:
            r = con.execute(f"""
                SELECT exchange, symbol, ts_ms, funding_rate, next_funding_ts
                FROM read_parquet('{D.format(d=d)}') WHERE funding_rate IS NOT NULL
            """).fetchnumpy()
        except Exception:  # noqa: BLE001
            out.setdefault("failed_days", []).append(d); continue
        ex = np.array([str(v) for v in r["exchange"]]); sy = np.array([str(v) for v in r["symbol"]])
        for k in sorted(set(zip(ex, sy))):
            m = (ex == k[0]) & (sy == k[1])
            raw.setdefault(k, {"ts": [], "fr": [], "nft": []})
            raw[k]["ts"].append(r["ts_ms"][m]); raw[k]["fr"].append(r["funding_rate"][m])
            raw[k]["nft"].append(r["next_funding_ts"][m])
    for k in raw:
        for f in ("ts", "fr", "nft"):
            raw[k][f] = np.concatenate(raw[k][f])

    # ---------------- 1b ---------------- #
    say("=" * 96); say("1b. DISTINCT VALUES — how much does the rate actually move?"); say("=" * 96)
    say(f"  {'venue':<24}{'samples':>11}{'distinct':>10}{'top value':>14}{'share':>9}")
    for k in sorted(raw):
        fr = raw[k]["fr"]; c = Counter(np.round(fr, 12).tolist())
        top, n = c.most_common(1)[0]
        say(f"  {k[0]+'/'+k[1]:<24}{fr.size:>11,}{len(c):>10,}{top/BPS:>13.4f}b{n/fr.size:>9.1%}")
        say("      10 most frequent (bps · share): "
            + " · ".join(f"{v/BPS:.4f}:{n2/fr.size:.1%}" for v, n2 in c.most_common(10)))
        out.setdefault("distinct", {})[f"{k[0]}/{k[1]}"] = {
            "n": int(fr.size), "n_distinct": len(c),
            "top10": [[float(v / BPS), n2 / fr.size] for v, n2 in c.most_common(10)]}

    # ---------------- 1c ---------------- #
    say("\n" + "=" * 96)
    say("1c. TWO ROUTES SIDE BY SIDE — last-before-next_funding_ts vs nearest-to-settlement-clock")
    say("=" * 96)
    say(f"  {'venue':<22}{'route':<34}{'n':>6}{'p05':>9}{'p50':>9}{'p95':>9}{'max':>9}"
        f"{'%neg':>7}{'bps/day':>9}")
    for k in sorted(raw):
        ts, fr, nft = raw[k]["ts"], raw[k]["fr"], raw[k]["nft"]
        # route A — exactly what DIAG-cost-ledger-001 did
        a = {}
        for t, f, n in zip(ts, fr, nft):
            if n not in a or t > a[n][0]:
                a[n] = (t, f)
        va = np.array([v[1] for v in a.values()], float)
        # route B — the sample nearest each 00/08/16 UTC boundary, |dt| <= 5 min
        hb = (ts // 3_600_000) % 24
        mn = ts % 3_600_000
        near = np.zeros(ts.size, bool)
        for h in SETTLE_HOURS:
            near |= ((hb == h) & (mn <= 300_000)) | ((hb == (h - 1) % 24) & (mn >= 3_300_000))
        keyb = {}
        for t, f, ok in zip(ts, fr, near):
            if not ok:
                continue
            slot = int(round(t / 28_800_000.0))
            dt = abs(t - slot * 28_800_000)
            if slot not in keyb or dt < keyb[slot][0]:
                keyb[slot] = (dt, f)
        vb = np.array([v[1] for v in keyb.values()], float)
        for lab, v in (("A last-before-next_funding_ts", va),
                       ("B nearest 00/08/16 UTC (<=5min)", vb)):
            if v.size < 3:
                say(f"  {'':<22}{lab:<34}{v.size:>6}   (too few samples)"); continue
            p = [float(np.percentile(v, q)) / BPS for q in (5, 50, 95, 100)]
            say(f"  {(k[0]+'/'+k[1]) if lab.startswith('A') else '':<22}{lab:<34}{v.size:>6}"
                + "".join(f"{x:>9.4f}" for x in p)
                + f"{float((v<0).mean()):>7.1%}{float(np.median(v))*3/BPS:>9.4f}")
            out.setdefault("routes", {}).setdefault(f"{k[0]}/{k[1]}", {})[lab] = {
                "n": int(v.size), "p05": p[0], "p50": p[1], "p95": p[2], "max": p[3],
                "frac_neg": float((v < 0).mean()), "bps_per_day": float(np.median(v)) * 3 / BPS}

    # ---------------- 1d ---------------- #
    say("\n" + "=" * 96); say("1d. Does the maximum stay exactly 1.0000 bps?"); say("=" * 96)
    still = []
    for name, per in out.get("routes", {}).items():
        for lab, v in per.items():
            flag = abs(v["max"] - 1.0) < 1e-6
            say(f"  {name:<24}{lab:<34} max {v['max']:.6f} bps  "
                f"{'EXACTLY 1.0000' if flag else 'not 1.0000'}")
            if flag and lab.startswith("B"):
                still.append(name)
    out["max_exactly_one_after_route_B"] = still
    p = REPO / "reports" / "funding-settled-001.json"
    p.write_text(json.dumps(out, indent=2, default=float) + "\n")
    say(f"\n  result -> {p.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
