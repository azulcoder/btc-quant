"""measure_book_spread_001.py — BOOK-001: make the denominator re-runnable.

Every microstructure claim in this repo that compares a trades-only estimate against "the
book" divides by 0.0156 bps. `docs/DIAG-venue-filter-audit.md` §2 looked for the query behind
that number and found **none** — no script computes it, no document quotes its SQL, only a
caption and a per-venue table. It has been an anchor with no checker.

This script is that checker. It measures the quoted spread from `depth_snapshots` directly,
per venue, and reports enough of the distribution that any future comparison can name which
statistic it is comparing against.

Look classification: PROVENANCE DIAGNOSTIC — it measures a property of the recorded tape and
evaluates no predictive specification. Counted in the diagnostic column.

Why this needs no separate PREREG
---------------------------------
The free choices that a pre-registration would have to pin are already pinned, and pinned
BEFORE any number exists: all three weightings are reported side by side rather than one being
selected, every quantile is reported rather than a favourite, `sqrt(E[c^2])` is mandatory
because that is the quantity Roll targets, and the positive control must reproduce 0.0156 bps
without adjusting any definition to make it fit. Choosing among the three weightings AFTER
seeing them would need a PREREG; reporting all three does not.

Read-only. Frozen exploration slice only (`2026-07-05..2026-08-03`), which ends two days before
the LockBox boundary, so nothing here reads an evaluate-once slice.

RULE: no estimator from `btcquant/hasbrouck.py` is imported or run here. This measures the
book; it does not estimate anything from trades.
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

# Trade-weighting and the episode scan are per-day joins and cost far more than the snapshot
# statistics, so they run on a DECLARED subset rather than the whole slice. Three of these are
# the days DIAG-provenance-001 used, so the numbers line up with what is already recorded; the
# fourth is the day EDA-microstructure-001 §2a's single-day table was built on, which is what
# the positive control has to reproduce.
HEAVY_DAYS = ["2026-07-30", "2026-07-31", "2026-08-01", "2026-08-03"]

EPISODE_TICKS = 63          # the lower bound of the "63-753 tick" claim being tested
BPS = 1e-4


def say(*a):
    print(*a, flush=True)


def slice_dates(con) -> list[str]:
    from huggingface_hub import HfApi
    import re
    fs = HfApi().list_repo_files("azulcoder/btc-quant-ticks", repo_type="dataset")
    have = {m.group(1) for f in fs
            if (m := re.match(r"data/date=(\d{4}-\d{2}-\d{2})/depth_snapshots\.parquet", f))}
    return sorted(d for d in have if SLICE_LO <= d <= SLICE_HI)


BEST = """
    SELECT exchange, symbol, ts_ms,
           CAST(json_extract(bids, '$[0][0]') AS DOUBLE) AS bid,
           CAST(json_extract(asks, '$[0][0]') AS DOUBLE) AS ask
    FROM read_parquet('{url}')
"""


def load_day(con, d: str):
    return con.execute(BEST.format(url=DEPTH.format(d=d))).fetchnumpy()


def wq(x, w, qs):
    """Weighted quantiles. w=None gives the unweighted case through the same code path,
    so (i) and (ii) below cannot differ because of two different quantile conventions."""
    x = np.asarray(x, float)
    if w is None:
        w = np.ones_like(x)
    o = np.argsort(x)
    x, w = x[o], np.asarray(w, float)[o]
    cw = np.cumsum(w)
    cut = (cw - 0.5 * w) / cw[-1]
    return [float(np.interp(q, cut, x)) for q in qs]


def stats(spread_ticks, w=None) -> dict:
    """B2 — every statistic, on the HALF-spread c = spread/2, reported in ticks."""
    c = np.asarray(spread_ticks, float) / 2.0
    if w is None:
        w = np.ones_like(c)
    w = np.asarray(w, float)
    q = wq(c, w, [0.25, 0.50, 0.75, 0.95, 0.99])
    mean = float(np.average(c, weights=w))
    rms = float(np.sqrt(np.average(c * c, weights=w)))
    return {"n": int(c.size), "p25": q[0], "p50": q[1], "p75": q[2], "p95": q[3],
            "p99": q[4], "mean": mean, "rms": rms}


def fmt_row(label, s, tick_bps):
    return (f"  {label:<26}{s['n']:>9,}"
            + "".join(f"{s[k]:>9.4f}" for k in ("p25", "p50", "p75", "p95", "p99", "mean", "rms"))
            + f"   | p50 {s['p50']*tick_bps:>8.5f} bps · rms {s['rms']*tick_bps:>8.5f} bps")


def main() -> int:
    con = duckdb.connect()
    out: dict = {"slice": [SLICE_LO, SLICE_HI], "heavy_days": HEAVY_DAYS,
                 "episode_ticks": EPISODE_TICKS}

    dates = slice_dates(con)
    say("BOOK-001 — quoted spread from depth_snapshots, per venue, never pooled")
    say(f"  slice {SLICE_LO}..{SLICE_HI} · {len(dates)} day(s) with depth_snapshots")
    say(f"  heavy paths (trade-weighted, episodes) on a declared subset: {HEAVY_DAYS}\n")

    # ------------------------------------------------------------------ #
    per_day: dict[tuple[str, str], list] = {}
    quality: dict[tuple[str, str], dict] = {}
    hours: dict[tuple[str, str], dict] = {}
    ticks: dict[tuple[str, str], float] = {}
    mids: dict[tuple[str, str], list] = {}

    for i, d in enumerate(dates, 1):
        try:
            r = load_day(con, d)
        except Exception as e:  # noqa: BLE001 — one unreadable partition is excluded and COUNTED
            say(f"  [{i:>2}/{len(dates)}] {d}  READ FAILED ({type(e).__name__}) — excluded and counted")
            out.setdefault("failed_days", []).append(d)
            continue
        ex = np.array([str(v) for v in r["exchange"]])
        sy = np.array([str(v) for v in r["symbol"]])
        bid, ask, ts = r["bid"], r["ask"], r["ts_ms"]
        for key in sorted(set(zip(ex, sy))):
            m = (ex == key[0]) & (sy == key[1])
            b, a, t = bid[m], ask[m], ts[m]
            o = np.argsort(t, kind="stable")
            b, a, t = b[o], a[o], t[o]
            q = quality.setdefault(key, {"n_raw": 0, "n_empty": 0, "n_crossed": 0,
                                         "n_used": 0, "days": []})
            q["n_raw"] += int(b.size)
            bad = ~np.isfinite(b) | ~np.isfinite(a)
            q["n_empty"] += int(bad.sum())
            b, a, t = b[~bad], a[~bad], t[~bad]
            crossed = b >= a
            q["n_crossed"] += int(crossed.sum())
            b, a, t = b[~crossed], a[~crossed], t[~crossed]
            q["n_used"] += int(b.size)
            q["days"].append(d)
            if b.size < 100:
                continue
            # B1 — tick measured, not assumed: the smallest positive gap between observed levels
            lv = np.unique(np.concatenate([b, a]))
            dl = np.diff(lv)
            dl = dl[dl > 1e-9]
            tk = float(np.min(dl)) if dl.size else float("nan")
            ticks.setdefault(key, tk)
            ticks[key] = min(ticks[key], tk)
            spread_abs = a - b
            mid = 0.5 * (a + b)
            mids.setdefault(key, []).append(float(np.median(mid)))
            gap = np.diff(t.astype(float))
            gap = np.append(gap, np.median(gap) if gap.size else 1000.0)
            per_day.setdefault(key, []).append(
                {"date": d, "spread_abs": spread_abs, "mid": mid, "ts": t, "gap": gap})
            hh = (t // 3_600_000) % 24
            hd = hours.setdefault(key, {})
            for h in range(24):
                mh = hh == h
                if mh.sum() >= 50:
                    hd.setdefault(h, []).extend((spread_abs[mh] / 2.0).tolist())
        say(f"  [{i:>2}/{len(dates)}] {d}  venues {len(set(zip(ex, sy)))}")

    # ------------------------------------------------------------------ #
    say("\n" + "=" * 118)
    say("B1/B2/B3 — half-spread c, in TICKS, per venue. Never pooled across venues.")
    say("=" * 118)

    for key in sorted(per_day):
        tk = ticks[key]
        ref_mid = float(np.median(mids[key]))
        tick_bps = tk / ref_mid / BPS
        say(f"\n{key[0]} / {key[1]}   tick = {tk:.4f} (MEASURED as the smallest positive gap "
            f"between observed levels) · reference mid = {ref_mid:,.2f} · "
            f"1 tick = {tick_bps:.5f} bps")
        say(f"  {'weighting':<26}{'n':>9}{'p25':>9}{'p50':>9}{'p75':>9}{'p95':>9}"
            f"{'p99':>9}{'mean':>9}{'rms':>9}")

        sp = np.concatenate([x["spread_abs"] for x in per_day[key]]) / tk
        gp = np.concatenate([x["gap"] for x in per_day[key]])
        s_i = stats(sp)
        s_ii = stats(sp, w=gp)
        say(fmt_row("(i)  per snapshot", s_i, tick_bps))
        say(fmt_row("(ii) time-weighted", s_ii, tick_bps))

        rec = {"tick": tk, "ref_mid": ref_mid, "tick_bps": tick_bps,
               "i_per_snapshot": s_i, "ii_time_weighted": s_ii,
               "gap_ms": {"p50": float(np.median(gp)), "p95": float(np.percentile(gp, 95)),
                          "p99": float(np.percentile(gp, 99)), "max": float(gp.max()),
                          "mean": float(gp.mean())}}
        say(f"  inter-snapshot gap ms: p50 {rec['gap_ms']['p50']:.0f} · "
            f"p95 {rec['gap_ms']['p95']:.0f} · p99 {rec['gap_ms']['p99']:.0f} · "
            f"max {rec['gap_ms']['max']:.0f} · mean {rec['gap_ms']['mean']:.0f}")

        # ---- (iii) trade-weighted, declared subset ---- #
        tw, ep = [], {"episodes": 0, "snap_in": 0, "trades_in": 0, "trades_total": 0,
                      "dur_ms": []}
        for day in [x for x in per_day[key] if x["date"] in HEAVY_DAYS]:
            try:
                tr = con.execute(f"""
                    SELECT ts_ms FROM read_parquet('{TRADES.format(d=day['date'])}')
                    WHERE exchange='{key[0]}' AND symbol='{key[1]}' ORDER BY ts_ms
                """).fetchnumpy()["ts_ms"]
            except Exception:  # noqa: BLE001
                continue
            idx = np.searchsorted(day["ts"], tr, side="right") - 1
            ok = idx >= 0
            tw.append(day["spread_abs"][idx[ok]] / tk)
            st = day["spread_abs"] / tk
            inep = st >= EPISODE_TICKS
            ep["snap_in"] += int(inep.sum())
            ep["trades_total"] += int(tr.size)
            ep["trades_in"] += int(inep[idx[ok]].sum())
            # contiguous episodes and their wall-clock duration
            if inep.any():
                brk = np.flatnonzero(np.diff(inep.astype(int)) != 0) + 1
                for a0, b0 in zip(np.concatenate([[0], brk]),
                                  np.concatenate([brk, [inep.size]])):
                    if inep[a0]:
                        ep["episodes"] += 1
                        ep["dur_ms"].append(float(day["ts"][b0 - 1] - day["ts"][a0]))
        if tw:
            spt = np.concatenate(tw)
            s_iii = stats(spt)
            say(fmt_row("(iii) trade-weighted", s_iii, tick_bps))
            say(f"  ratio (iii)/(i): p50 {s_iii['p50']/s_i['p50']:.3f} · "
                f"mean {s_iii['mean']/s_i['mean']:.3f} · rms {s_iii['rms']/s_i['rms']:.3f}"
                f"   [reported as measured, not interpreted]")
            rec["iii_trade_weighted"] = s_iii
            rec["ratio_iii_over_i"] = {"p50": s_iii["p50"] / s_i["p50"],
                                       "mean": s_iii["mean"] / s_i["mean"],
                                       "rms": s_iii["rms"] / s_i["rms"]}
        else:
            say("  (iii) trade-weighted: no trades matched on the declared subset")

        # ---- B5 episodes ---- #
        if ep["trades_total"]:
            dur = np.array(ep["dur_ms"]) if ep["dur_ms"] else np.array([0.0])
            say(f"  B5 spread >= {EPISODE_TICKS} ticks (declared subset {HEAVY_DAYS}): "
                f"{ep['episodes']:,} episode(s) · snapshots in {ep['snap_in']:,} · "
                f"median duration {np.median(dur):.0f} ms · max {dur.max():.0f} ms · "
                f"trades inside {ep['trades_in']:,}/{ep['trades_total']:,} "
                f"= {ep['trades_in']/ep['trades_total']:.4%}")
            rec["B5"] = {"episodes": ep["episodes"], "snapshots_in": ep["snap_in"],
                         "median_dur_ms": float(np.median(dur)), "max_dur_ms": float(dur.max()),
                         "trades_in": ep["trades_in"], "trades_total": ep["trades_total"],
                         "trade_frac": ep["trades_in"] / ep["trades_total"]}

        # ---- B6 block bootstrap, block = day ---- #
        days = per_day[key]
        rng = np.random.default_rng(20260807)
        boot = {"p50": [], "mean": [], "rms": []}
        for _ in range(400):
            pick = rng.integers(0, len(days), len(days))
            sr = np.concatenate([days[j]["spread_abs"] for j in pick]) / tk
            st = stats(sr)
            for k in boot:
                boot[k].append(st[k])
        say("  B6 block bootstrap (block = one day, 400 resamples): "
            + " · ".join(f"{k} {np.mean(boot[k]):.4f} ± {np.std(boot[k], ddof=1):.4f} tick"
                         for k in ("p50", "mean", "rms")))
        rec["B6"] = {k: {"mean": float(np.mean(v)), "se": float(np.std(v, ddof=1))}
                     for k, v in boot.items()}
        rec["B6"]["n_day_blocks"] = len(days)

        # ---- B7 hour invariance ---- #
        hd = hours.get(key, {})
        if hd:
            line = " ".join(f"{h:02d}:{np.median(v)/tk:.3f}" for h, v in sorted(hd.items()))
            med = [np.median(v) / tk for v in hd.values()]
            say(f"  B7 p50 c per UTC hour, ticks — range across hours "
                f"{min(med):.4f}..{max(med):.4f} (spread {max(med)-min(med):.4f} tick)")
            say(f"     {line}")
            rec["B7"] = {"per_hour_p50_ticks": {int(h): float(np.median(v) / tk)
                                                for h, v in sorted(hd.items())},
                         "range_ticks": float(max(med) - min(med))}

        q = quality[key]
        say(f"  B4 quality: raw {q['n_raw']:,} · empty side {q['n_empty']:,} "
            f"({q['n_empty']/max(q['n_raw'],1):.4%}) · crossed/locked {q['n_crossed']:,} "
            f"({q['n_crossed']/max(q['n_raw'],1):.4%}) · used {q['n_used']:,} · "
            f"days {len(q['days'])}")
        rec["B4"] = q | {"days": len(q["days"])}
        out.setdefault("venues", {})[f"{key[0]}/{key[1]}"] = rec

    # ------------------------------------------------------------------ #
    say("\n" + "=" * 118)
    say("POSITIVE CONTROL — can any path above reproduce 0.0156 bps (the anchor)?")
    say("=" * 118)
    TARGET = 0.0156
    cands = []
    for name, rec in out.get("venues", {}).items():
        tb = rec["tick_bps"]
        for path, s in (("(i) per snapshot", rec.get("i_per_snapshot")),
                        ("(ii) time-weighted", rec.get("ii_time_weighted")),
                        ("(iii) trade-weighted", rec.get("iii_trade_weighted"))):
            if not s:
                continue
            for stat in ("p50", "mean", "rms"):
                # the anchor is a FULL spread; c is the half, so compare 2c
                v = 2.0 * s[stat] * tb
                cands.append((abs(v - TARGET), v, f"{name} {path} 2*{stat}"))
    cands.sort()
    say(f"  target (EDA-microstructure-001 §2a, full spread): {TARGET} bps")
    for gap, v, label in cands[:6]:
        say(f"    {label:<52} {v:>9.5f} bps   gap {v - TARGET:+.5f} "
            f"({(v/TARGET - 1):+.1%})")
    if cands and cands[0][0] < 5e-5:
        say(f"\n  >>> REPRODUCED by: {cands[0][2]} = {cands[0][1]:.5f} bps")
        out["positive_control"] = {"reproduced": True, "by": cands[0][2], "value": cands[0][1]}
    else:
        say(f"\n  >>> NOT REPRODUCED by any path. Closest: {cands[0][2]} at "
            f"{cands[0][1]:.5f} bps, gap {cands[0][1]-TARGET:+.5f} bps "
            f"({(cands[0][1]/TARGET-1):+.1%}). No definition was adjusted to close it.")
        out["positive_control"] = {"reproduced": False, "closest": cands[0][2],
                                   "value": cands[0][1], "gap": cands[0][1] - TARGET}

    p = REPO / "reports" / "book-spread-001.json"
    p.write_text(json.dumps(out, indent=2, default=float) + "\n")
    say(f"\n  result -> {p.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
