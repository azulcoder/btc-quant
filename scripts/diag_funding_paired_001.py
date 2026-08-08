"""diag_funding_paired_001.py — settle the 3c failure by PAIRING, not by comparing medians.

`BOOK-002` §3c compared the median of the settled series against the median of route B and got
-2.7%, -23.8% and -7.1%. Two medians over DIFFERENT sample sets cannot say whether the values
disagree — route B holds 54 of bybit's 91 settlements, and the 37 it misses were chosen by
collector uptime. This pairs them on the settlement slot and asks a different question: where
both sources see the SAME settlement, do they agree?

Look: PROVENANCE DIAGNOSTIC. No returns, no P&L. Cost side only.
"""
from __future__ import annotations
import datetime as dt, json, sys
from pathlib import Path
import duckdb, numpy as np

REPO = Path(__file__).resolve().parent.parent
FM = "hf://datasets/azulcoder/btc-quant-ticks/data/date={d}/funding_mark.parquet"
LO, HI = "2026-07-05", "2026-08-03"
BPS = 1e-4
SLOT = 28_800_000                      # 8 h in ms
VEN = {"binancef": "BTCUSDT", "bybit": "BTCUSDT", "okx": "BTC-USDT-SWAP"}


def say(*a): print(*a, flush=True)


def dates():
    from huggingface_hub import HfApi
    import re
    fs = HfApi().list_repo_files("azulcoder/btc-quant-ticks", repo_type="dataset")
    return sorted(m.group(1) for f in fs
                  if (m := re.match(r"data/date=(\d{4}-\d{2}-\d{2})/funding_mark\.parquet", f))
                  and LO <= m.group(1) <= HI)


def route_b(con):
    """Route B EXACTLY as BOOK-002 built it, but keyed by slot so it can be paired."""
    out: dict[str, dict[int, float]] = {}
    for d in dates():
        try:
            r = con.execute(f"""SELECT exchange, symbol, ts_ms, funding_rate
                                FROM read_parquet('{FM.format(d=d)}')
                                WHERE funding_rate IS NOT NULL""").fetchnumpy()
        except Exception:  # noqa: BLE001
            continue
        ex = np.array([str(v) for v in r["exchange"]])
        for v, sym in VEN.items():
            m = ex == v
            ts, fr = r["ts_ms"][m], r["funding_rate"][m]
            hb, mn = (ts // 3_600_000) % 24, ts % 3_600_000
            near = np.zeros(ts.size, bool)
            for h in (0, 8, 16):
                near |= ((hb == h) & (mn <= 300_000)) | ((hb == (h - 1) % 24) & (mn >= 3_300_000))
            best: dict[int, tuple[int, float]] = out.setdefault(v, {}) and {}
            for t, f, ok in zip(ts, fr, near):
                if not ok:
                    continue
                slot = int(round(t / SLOT))
                dtm = abs(int(t) - slot * SLOT)
                cur = best.get(slot)
                if cur is None or dtm < cur[0]:
                    best[slot] = (dtm, float(f))
            tgt = out.setdefault(v, {})
            for slot, (_, f) in best.items():
                tgt[slot] = f
    return out


def settled():
    out: dict[str, dict[int, float]] = {}
    for v in VEN:
        f = REPO / "data" / "funding_history" / f"{v}.jsonl"
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            out.setdefault(v, {})[int(round(d["ts_ms"] / SLOT))] = float(d["rate"])
    return out


def main() -> int:
    con = duckdb.connect()
    b, s = route_b(con), settled()
    res: dict = {}

    say("1a. PAIRED ON THE SAME SETTLEMENT SLOT  [DIUKUR]\n")
    say(f"  {'venue':<10}{'routeB n':>10}{'paired':>8}{'exact':>8}"
        f"{'|d| p50':>10}{'|d| p95':>10}{'|d| max':>10}{'signed p50':>12}")
    closes = True
    for v in VEN:
        bb, ss = b.get(v, {}), s.get(v, {})
        lo_s = int(dt.datetime(2026, 7, 5, tzinfo=dt.timezone.utc).timestamp() * 1000 / SLOT)
        hi_s = int(dt.datetime(2026, 8, 4, tzinfo=dt.timezone.utc).timestamp() * 1000 / SLOT)
        common = sorted(k for k in bb if k in ss and lo_s <= k <= hi_s)
        if not common:
            say(f"  {v:<10} no paired slots"); closes = False; continue
        d = np.array([(bb[k] - ss[k]) / BPS for k in common])
        ex = int(np.sum(np.abs(d) < 1e-9))
        say(f"  {v:<10}{len(bb):>10}{len(common):>8}{ex:>8}"
            f"{np.percentile(np.abs(d),50):>10.4f}{np.percentile(np.abs(d),95):>10.4f}"
            f"{np.abs(d).max():>10.4f}{np.percentile(d,50):>+12.4f}")
        res.setdefault("paired", {})[v] = {
            "route_b_n": len(bb), "paired": len(common), "exact": ex,
            "abs_p50": float(np.percentile(np.abs(d), 50)),
            "abs_p95": float(np.percentile(np.abs(d), 95)),
            "abs_max": float(np.abs(d).max()),
            "signed_p50": float(np.percentile(d, 50))}
        if np.percentile(np.abs(d), 50) > 0.02:
            closes = False

    say("\n1d. ARE THE SETTLEMENTS ROUTE B MISSES RANDOM?  [DIUKUR]\n")
    say(f"  {'venue':<10}{'captured':>10}{'missed':>8}"
        f"{'|rate| p50 capt':>17}{'|rate| p50 miss':>17}{'|rate| p95 capt':>17}{'|rate| p95 miss':>17}")
    for v in VEN:
        bb, ss = b.get(v, {}), s.get(v, {})
        lo_s = int(dt.datetime(2026, 7, 5, tzinfo=dt.timezone.utc).timestamp() * 1000 / SLOT)
        hi_s = int(dt.datetime(2026, 8, 4, tzinfo=dt.timezone.utc).timestamp() * 1000 / SLOT)
        win = [k for k in ss if lo_s <= k <= hi_s]
        cap = np.array([abs(ss[k]) / BPS for k in win if k in bb])
        mis = np.array([abs(ss[k]) / BPS for k in win if k not in bb])
        if cap.size == 0 or mis.size == 0:
            say(f"  {v:<10}{cap.size:>10}{mis.size:>8}   (one side empty)"); continue
        say(f"  {v:<10}{cap.size:>10}{mis.size:>8}"
            f"{np.percentile(cap,50):>17.4f}{np.percentile(mis,50):>17.4f}"
            f"{np.percentile(cap,95):>17.4f}{np.percentile(mis,95):>17.4f}")
        res.setdefault("missing_bias", {})[v] = {
            "captured": int(cap.size), "missed": int(mis.size),
            "capt_p50": float(np.percentile(cap, 50)), "miss_p50": float(np.percentile(mis, 50)),
            "capt_p95": float(np.percentile(cap, 95)), "miss_p95": float(np.percentile(mis, 95)),
            "miss_over_capt_p50": float(np.percentile(mis, 50) / max(np.percentile(cap, 50), 1e-12))}

    say("\n" + "=" * 92)
    if closes:
        say("  >>> PAIRED DIFFERENCES ~0 -> the 3c discrepancy is SELECTION, not disagreement.")
        say("      SETTLED is the correct source. Route B is RETIRED as a funding estimator.")
    else:
        say("  >>> PAIRED DIFFERENCES ARE NOT ~0 -> which source is right is NOT decided here.")
        say("      Sign and magnitude reported above. STOP at item 1.")
    res["closes_with_settled"] = bool(closes)
    (REPO / "reports" / "funding-paired-001.json").write_text(
        json.dumps(res, indent=2, default=float) + "\n")
    say(f"  result -> reports/funding-paired-001.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
