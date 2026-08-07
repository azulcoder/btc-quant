"""diag_book_resolution_001.py — what the 1-per-second book can and cannot resolve.

Four measurements, no construction. BOOK-001 established the anchor and produced one number
that separates binancef from the other venues — the trade-weighted `sqrt(E[c^2])` of 0.02460 bps.
That number rests on an ASOF match to the last snapshot at or before each trade, and the match
was never tested. Items 1 and 3 test it; items 2 and 4 report what the cadence permits.

Look classification: PROVENANCE DIAGNOSTIC. No estimator from `btcquant/hasbrouck.py` is
imported, no Roll is run, nothing is built. Counted in the diagnostic column.

Read-only, frozen exploration slice only (`2026-07-05..2026-08-03`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import numpy as np

REPO = Path(__file__).resolve().parent.parent
DEPTH = "hf://datasets/azulcoder/btc-quant-ticks/data/date={d}/depth_snapshots.parquet"
TRADES = "hf://datasets/azulcoder/btc-quant-ticks/data/date={d}/trades.parquet"
SLICE_LO, SLICE_HI = "2026-07-05", "2026-08-03"

# The same subset BOOK-001 used for its trade-weighted path, so the numbers line up with what
# is already recorded rather than being a different sample wearing the same name.
HEAVY_DAYS = ["2026-07-30", "2026-07-31", "2026-08-01", "2026-08-03"]
STALENESS_MS = [100, 10]
BPS = 1e-4
TICK = 0.1


def say(*a):
    print(*a, flush=True)


BEST = """
    SELECT exchange, symbol, ts_ms,
           CAST(json_extract(bids, '$[0][0]') AS DOUBLE) AS bid,
           CAST(json_extract(asks, '$[0][0]') AS DOUBLE) AS ask
    FROM read_parquet('{url}')
"""


def slice_dates() -> list[str]:
    from huggingface_hub import HfApi
    import re
    fs = HfApi().list_repo_files("azulcoder/btc-quant-ticks", repo_type="dataset")
    have = {m.group(1) for f in fs
            if (m := re.match(r"data/date=(\d{4}-\d{2}-\d{2})/depth_snapshots\.parquet", f))}
    return sorted(d for d in have if SLICE_LO <= d <= SLICE_HI)


def clean_day(con, d):
    """Per-venue arrays of (ts, half-spread in ticks), sorted, with bad rows dropped."""
    r = con.execute(BEST.format(url=DEPTH.format(d=d))).fetchnumpy()
    ex = np.array([str(v) for v in r["exchange"]])
    sy = np.array([str(v) for v in r["symbol"]])
    out = {}
    for key in sorted(set(zip(ex, sy))):
        m = (ex == key[0]) & (sy == key[1])
        b, a, t = r["bid"][m], r["ask"][m], r["ts_ms"][m]
        ok = np.isfinite(b) & np.isfinite(a) & (b < a)
        b, a, t = b[ok], a[ok], t[ok]
        o = np.argsort(t, kind="stable")
        out[key] = (t[o], (a[o] - b[o]) / TICK / 2.0)      # c in ticks
    return out


def rms(c):
    c = np.asarray(c, float)
    return float(np.sqrt(np.mean(c * c))) if c.size else float("nan")


def main() -> int:
    con = duckdb.connect()
    out: dict = {"slice": [SLICE_LO, SLICE_HI], "heavy_days": HEAVY_DAYS}
    dates = slice_dates()
    say("diag_book_resolution_001 — four measurements, nothing built")
    say(f"  slice {SLICE_LO}..{SLICE_HI} ({len(dates)} days) · "
        f"ASOF subset {HEAVY_DAYS}\n")

    # =============================================================== #
    # 1 + 3 need snapshots; 1 also needs trades on the heavy subset.
    # =============================================================== #
    gaps: dict = {}
    exact: dict = {}
    stale: dict = {}
    c_by_stale: dict = {}
    tradegap: dict = {}

    for i, d in enumerate(dates, 1):
        try:
            day = clean_day(con, d)
        except Exception as e:  # noqa: BLE001 — excluded and COUNTED, never silent
            say(f"  [{i:>2}/{len(dates)}] {d}  READ FAILED ({type(e).__name__})")
            out.setdefault("failed_days", []).append(d)
            continue
        for key, (t, c) in day.items():
            if t.size < 100:
                continue
            gaps.setdefault(key, []).append(np.diff(t.astype(float)))
            e = exact.setdefault(key, [0, 0])
            e[0] += int(np.sum(np.isclose(c, 0.5, atol=1e-9)))
            e[1] += int(c.size)
            if d in HEAVY_DAYS:
                try:
                    tr = con.execute(f"""
                        SELECT ts_ms FROM read_parquet('{TRADES.format(d=d)}')
                        WHERE exchange='{key[0]}' AND symbol='{key[1]}' ORDER BY ts_ms
                    """).fetchnumpy()["ts_ms"].astype(np.int64)
                except Exception:  # noqa: BLE001
                    continue
                if tr.size < 2:
                    continue
                idx = np.searchsorted(t, tr, side="right") - 1
                ok = idx >= 0
                st = (tr[ok] - t[idx[ok]]).astype(float)
                stale.setdefault(key, []).append(st)
                c_by_stale.setdefault(key, []).append(c[idx[ok]])
                # item 4: consecutive trades with NO snapshot strictly between them
                pos = np.searchsorted(t, tr, side="right")     # snapshots at or before each trade
                same_interval = np.diff(pos) == 0              # no snapshot fell between them
                tg = tradegap.setdefault(key, [0, 0])
                tg[0] += int(same_interval.sum())
                tg[1] += int(same_interval.size)
        say(f"  [{i:>2}/{len(dates)}] {d}")

    # =============================================================== #
    say("\n" + "=" * 104)
    say("1. STALENESS of the ASOF match, and whether sqrt(E[c^2]) survives bounding it")
    say("=" * 104)
    say(f"  subset {HEAVY_DAYS} · staleness = ts_trade - ts_snapshot (ms)\n")
    say(f"  {'venue':<24}{'n trades':>12}{'p50':>8}{'p90':>8}{'p99':>8}{'max':>12}")
    for key in sorted(stale):
        s = np.concatenate(stale[key])
        say(f"  {key[0]+'/'+key[1]:<24}{s.size:>12,}{np.percentile(s,50):>8.0f}"
            f"{np.percentile(s,90):>8.0f}{np.percentile(s,99):>8.0f}{s.max():>12,.0f}")
        out.setdefault("staleness_ms", {})[f"{key[0]}/{key[1]}"] = {
            "n": int(s.size), "p50": float(np.percentile(s, 50)),
            "p90": float(np.percentile(s, 90)), "p99": float(np.percentile(s, 99)),
            "max": float(s.max())}

    say(f"\n  sqrt(E[c^2]) trade-weighted, in TICKS and bps, as the staleness bound tightens:")
    say(f"  {'venue':<24}{'bound':>10}{'trades kept':>14}{'% kept':>9}"
        f"{'rms tick':>11}{'rms bps':>10}{'vs unbounded':>14}")
    for key in sorted(stale):
        s = np.concatenate(stale[key])
        c = np.concatenate(c_by_stale[key])
        base = rms(c)
        tick_bps = TICK / 64_000.0 / BPS      # reference mid pinned; BOOK-001 measured ~64k
        rows = [("none", s.size, 1.0, base)]
        for b in STALENESS_MS:
            m = s <= b
            rows.append((f"<= {b} ms", int(m.sum()), float(m.mean()), rms(c[m])))
        for lab, n, frac, v in rows:
            say(f"  {(key[0]+'/'+key[1]) if lab=='none' else '':<24}{lab:>10}{n:>14,}"
                f"{frac:>8.2%}{v:>11.4f}{v*tick_bps:>10.5f}"
                f"{(v/base):>13.3f}x")
        out.setdefault("rms_by_staleness", {})[f"{key[0]}/{key[1]}"] = {
            lab: {"n": n, "frac_kept": frac, "rms_tick": v, "rms_bps": v * tick_bps,
                  "ratio_to_unbounded": v / base} for lab, n, frac, v in rows}

    # =============================================================== #
    say("\n" + "=" * 104)
    say("3. How degenerate is the distribution?")
    say("=" * 104)
    say(f"  {'venue':<24}{'snapshots':>13}{'c == 0.5 tick':>16}{'fraction':>12}")
    for key in sorted(exact):
        n_exact, n_tot = exact[key]
        say(f"  {key[0]+'/'+key[1]:<24}{n_tot:>13,}{n_exact:>16,}{n_exact/n_tot:>12.4%}")
        out.setdefault("exact_half_tick", {})[f"{key[0]}/{key[1]}"] = {
            "n_exact": n_exact, "n_total": n_tot, "fraction": n_exact / n_tot}
    fr = [v["fraction"] for v in out["exact_half_tick"].values()]
    if min(fr) > 0.99:
        say(f"\n  >>> Every venue is above 99% (min {min(fr):.4%}). EVERY QUANTILE OF THE BOOK")
        say("      IS A CONSTANT STATISTIC and cannot distinguish any regime, any hour, or any")
        say("      day. The hour-invariance result and the SE = 0 on p50 in BOOK-001 are")
        say("      CONSEQUENCES of this, not independent findings.")
        out["quantiles_are_constant"] = True
    else:
        say(f"\n  >>> Not all venues exceed 99% (min {min(fr):.4%}); quantiles retain some"
            " ability to vary.")
        out["quantiles_are_constant"] = False

    # =============================================================== #
    say("\n" + "=" * 104)
    say("4. P4 FEASIBILITY — inter-snapshot cadence, and unobserved trade intervals")
    say("=" * 104)
    say(f"  {'venue':<24}{'n gaps':>12}{'p50':>8}{'p90':>8}{'p99':>9}{'max':>14}{'> 5 s':>9}")
    for key in sorted(gaps):
        g = np.concatenate(gaps[key])
        say(f"  {key[0]+'/'+key[1]:<24}{g.size:>12,}{np.percentile(g,50):>8.0f}"
            f"{np.percentile(g,90):>8.0f}{np.percentile(g,99):>9.0f}{g.max():>14,.0f}"
            f"{(g>5000).mean():>9.3%}")
        out.setdefault("gap_ms", {})[f"{key[0]}/{key[1]}"] = {
            "n": int(g.size), "p50": float(np.percentile(g, 50)),
            "p90": float(np.percentile(g, 90)), "p99": float(np.percentile(g, 99)),
            "max": float(g.max()), "frac_over_5s": float((g > 5000).mean())}

    say(f"\n  Fraction of CONSECUTIVE TRADE PAIRS with NO snapshot between them.")
    say("  Definition: for trades t_i < t_i+1, count the pair when no snapshot timestamp falls")
    say("  in (t_i, t_i+1]. Those two trades cannot be told apart by the book, so this is an")
    say("  UPPER BOUND on the share of order lifecycle the snapshots cannot resolve.")
    say(f"\n  {'venue':<24}{'pairs':>14}{'no snapshot between':>22}{'fraction':>12}")
    for key in sorted(tradegap):
        n_same, n_tot = tradegap[key]
        say(f"  {key[0]+'/'+key[1]:<24}{n_tot:>14,}{n_same:>22,}{n_same/n_tot:>12.4%}")
        out.setdefault("trades_sharing_interval", {})[f"{key[0]}/{key[1]}"] = {
            "pairs": n_tot, "no_snapshot_between": n_same, "fraction": n_same / n_tot}

    p = REPO / "reports" / "book-resolution-001.json"
    p.write_text(json.dumps(out, indent=2, default=float) + "\n")
    say(f"\n  result -> {p.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
