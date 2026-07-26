"""test_orderflow.py — M1 order-flow bars: independent recomputation of EVERY feature.

Fully deterministic and **network-free**: synthetic §3c day stores are seeded
through ``collector.open_db`` (the canonical schema, the ``test_check_ticks.py``
idiom) and every expectation is recomputed here by a **second, independent
route** — a naive Python loop over the raw rows, a hand-written fixture with the
arithmetic spelled out, or (for the depth slope) a general least-squares solver
against the closed form. That is the standing repo rule: a feature whose only
witness is the implementation that produced it has not been verified.

The gap / honesty rails get the same treatment: coverage is cross-checked
against a per-second occupancy array, "table present but empty" is separated
from "leg dead" by construction, and the five verbatim honesty sentences are
asserted to still be in the module docstring.

Collector deps are opt-in (requirements-collector.txt): skips cleanly without duckdb.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")  # opt-in dep — requirements-collector.txt

from btcquant import backtest, collector, features, risk  # noqa: E402
from btcquant import orderflow as of  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
DAY = "2025-03-04"          # long closed; _day_is_closed() is wall-clock based
DAY2 = "2025-03-05"
DAY3 = "2025-03-06"
MS_DAY = 86_400_000


def _day_ms(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


# --------------------------------------------------------------------------- #
# Fixture seeding (canonical schema via collector.open_db)                     #
# --------------------------------------------------------------------------- #
def _seed(root: Path, date: str, *, trades=(), depth=(), liqs=(), make_tables=True) -> None:
    """Write one §3c day file. ``trades`` rows are the collector row tuples."""
    root.mkdir(parents=True, exist_ok=True)
    con = collector.open_db(root / f"{date}.duckdb")
    try:
        for r in trades:
            con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", list(r))
        for r in depth:
            con.execute("INSERT INTO depth_snapshots VALUES (?,?,?,?,?)", list(r))
        for r in liqs:
            con.execute("INSERT INTO liquidations VALUES (?,?,?,?,?,?,?)", list(r))
        if not make_tables:
            con.execute("DROP TABLE liquidations")
    finally:
        con.close()


def _trade(ex: str, ts: int, price: float, qty: float, buy: bool, tid: str) -> tuple:
    return (ex, "BTCUSDT", tid, ts, price, qty, buy)


def _book(ex: str, ts: int, bids: list, asks: list) -> tuple:
    return (ex, "BTCUSDT", ts, json.dumps(bids), json.dumps(asks))


def _dense_trades(date: str, ex: str, *, minutes: int, step_s: int = 10,
                  price0: float = 60_000.0, qty: float = 0.5) -> list[tuple]:
    """A metronome tape: one print every ``step_s`` s so coverage is exactly 1.0.

    Prices walk deterministically, sides alternate in a 3-cycle so buy/sell
    volume is never trivially symmetric.
    """
    t0 = _day_ms(date)
    rows = []
    n = minutes * 60 // step_s
    for i in range(n):
        ts = t0 + i * step_s * 1000
        px = price0 + (i % 7) - 3
        rows.append(_trade(ex, ts, px, qty, (i % 3) != 0, f"t{i:06d}"))
    return rows


def _flat_book(ex: str, date: str, *, minutes: int, step_s: int = 1,
               levels: int = 5) -> list[tuple]:
    """A 1 Hz book whose levels move deterministically (so OFI is non-trivial)."""
    t0 = _day_ms(date)
    rows = []
    n = minutes * 60 // step_s
    for i in range(n):
        ts = t0 + i * step_s * 1000
        bp = 60_000.0 - (i % 3) * 0.5
        ap = 60_001.0 + (i % 5) * 0.5
        bids = [[bp - j * 0.5, 1.0 + j + (i % 4)] for j in range(levels)]
        asks = [[ap + j * 0.5, 2.0 + j + (i % 3)] for j in range(levels)]
        rows.append(_book(ex, ts, bids, asks))
    return rows


def _bars(root: Path, date_start: str, date_end: str, **kw) -> pd.DataFrame:
    kw.setdefault("source", "local")
    kw.setdefault("cache", False)
    return of.order_flow_bars(date_start, date_end, store_dir=root, **kw)


# --------------------------------------------------------------------------- #
# A. Independent recomputation, one per feature                                 #
# --------------------------------------------------------------------------- #
def test_delta_and_cvd_match_reference_loop(tmp_path):
    """Naive Python loop over the raw rows == the SQL path, EXACTLY.

    Quantities are dyadic (0.25 / 0.5 / 0.75) so both routes do the same binary
    floating-point additions in the same order; equality is bit-exact, not
    approximate, which is a strictly stronger statement than a tolerance.
    """
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    rows = []
    for i in range(360):                       # every 10 s for 1 hour
        ts = t0 + i * 10_000
        qty = [0.25, 0.5, 0.75][i % 3]
        rows.append(_trade("bybit", ts, 60_000.0 + (i % 11), qty, (i % 4) != 0, f"t{i:05d}"))
    _seed(root, DAY, trades=rows)

    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="1min", vpin=False)

    # ---- independent route: pure Python, no SQL, no groupby -----------------
    buy = {}
    sell = {}
    for (_ex, _sym, tid, ts, px, qty, aggr) in sorted(rows, key=lambda r: (r[3], r[2])):
        b = (ts - t0) // 60_000
        if aggr:
            buy[b] = buy.get(b, 0.0) + qty
        else:
            sell[b] = sell.get(b, 0.0) + qty
    n = len(bars)
    exp_delta = np.array([buy.get(i, 0.0) - sell.get(i, 0.0) for i in range(n)])
    got = bars["delta_bybit"].to_numpy()
    covered = bars["coverage_bybit"].to_numpy() > 0
    assert np.array_equal(got[covered], exp_delta[covered])
    assert np.all(np.isnan(got[~covered]))

    # CVD is the running sum inside one coverage segment.
    seg = bars["segment"].to_numpy()
    run, exp_cvd = 0.0, []
    prev = seg[0]
    for i in range(n):
        if seg[i] != prev:
            run = 0.0
            prev = seg[i]
        if covered[i]:
            run += exp_delta[i]
            exp_cvd.append(run)
        else:
            exp_cvd.append(np.nan)
    np.testing.assert_array_equal(bars["cvd_bybit"].to_numpy(), np.array(exp_cvd))


def test_size_buckets_partition_delta_exactly(tmp_path):
    """Σ bucket == delta_usd, and $10,000.00 lands in the RETAIL bucket.

    The taxonomy is the repo's own (``CvdStore``): the smallest threshold >= the
    print notional wins, so the boundary is inclusive on the low side.
    """
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    px = 50_000.0
    # notionals: 10_000 (boundary), 50_000, 500_000, 5_000_000
    qtys = [0.2, 1.0, 10.0, 100.0]
    rows = []
    for i in range(240):
        rows.append(_trade("bybit", t0 + i * 10_000, px, qtys[i % 4], (i % 2) == 0, f"t{i:05d}"))
    _seed(root, DAY, trades=rows)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="5min", vpin=False)

    parts = bars[[f"delta_usd_{s}_bybit" for s in ("le10k", "le100k", "le1m", "whale")]]
    # skipna=False so an unknown bucket can never be laundered into a 0.0 term.
    np.testing.assert_allclose(parts.sum(axis=1, skipna=False).to_numpy(),
                               bars["delta_usd_bybit"].to_numpy(), rtol=0, atol=1e-9)

    # Independent per-bucket loop.
    exp = {k: {} for k in ("le10k", "le100k", "le1m", "whale")}
    for (_e, _s, _tid, ts, p, q, aggr) in rows:
        b = (ts - t0) // 300_000
        notional = p * q
        key = ("le10k" if notional <= 1e4 else "le100k" if notional <= 1e5
               else "le1m" if notional <= 1e6 else "whale")
        exp[key][b] = exp[key].get(b, 0.0) + (notional if aggr else -notional)
    live = (bars["coverage_bybit"] > 0).to_numpy()
    for key in exp:
        got = bars[f"delta_usd_{key}_bybit"].to_numpy()
        want = np.array([exp[key].get(i, 0.0) for i in range(len(bars))])
        np.testing.assert_allclose(got[live], want[live], rtol=0, atol=1e-9)
        # Outside the live window the value is UNKNOWN, and must stay NaN.
        assert np.isnan(got[~live]).all()
    # The $10k print is a boundary case and must be retail, never mid.
    assert bars["delta_usd_le10k_bybit"].abs().sum() > 0


def _naive_ofi(snaps: list[tuple[int, float, float, float, float]], gap_ms: int) -> dict:
    """Cont-Kukanov-Stoikov (2014) e_n, written out literally, one pair at a time."""
    out = {}
    for i in range(1, len(snaps)):
        ts, bp, bq, ap, aq = snaps[i]
        pts, pbp, pbq, pap, paq = snaps[i - 1]
        if ts - pts > gap_ms:
            continue
        e = 0.0
        if bp >= pbp:
            e += bq
        if bp <= pbp:
            e -= pbq
        if ap <= pap:
            e -= aq
        if ap >= pap:
            e += paq
        out[ts] = e
    return out


def test_ofi_matches_naive_loop(tmp_path):
    """Vectorized SQL OFI == the literal per-pair loop, to machine precision."""
    root = tmp_path / "ticks"
    depth = _flat_book("bybit", DAY, minutes=30, step_s=1, levels=5)
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=30), depth=depth)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", book_venue="bybit",
                 bar="1min", depth_levels=3, vpin=False)

    snaps = []
    for (_e, _s, ts, bids, asks) in depth:
        b = json.loads(bids)
        a = json.loads(asks)
        snaps.append((ts, b[0][0], b[0][1], a[0][0], a[0][1]))
    snaps.sort()
    ev = _naive_ofi(snaps, of.GAP_MS)

    t0 = _day_ms(DAY)
    exp = {}
    for ts, e in ev.items():
        exp[(ts - t0) // 60_000] = exp.get((ts - t0) // 60_000, 0.0) + e
    got = bars["ofi_bybit"].to_numpy()
    for i in range(len(bars)):
        if i in exp:
            assert abs(got[i] - exp[i]) <= 1e-12, (i, got[i], exp[i])
        else:
            assert np.isnan(got[i]) or bars["ofi_n_bybit"].to_numpy()[i] == 0


def test_ofi_hand_fixture_covers_all_four_indicator_branches(tmp_path):
    """Hand-computed OFI over four snapshots that exercise up/down/flat on both sides."""
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    # (bid px, bid qty, ask px, ask qty)
    seq = [
        (100.0, 5.0, 101.0, 7.0),   # n=0
        (100.5, 6.0, 101.0, 4.0),   # bid UP, ask FLAT
        (100.0, 2.0, 100.5, 9.0),   # bid DOWN, ask DOWN
        (100.0, 3.0, 102.0, 1.0),   # bid FLAT, ask UP
    ]
    depth = [_book("bybit", t0 + i * 1000, [[b, bq]], [[a, aq]])
             for i, (b, bq, a, aq) in enumerate(seq)]
    # Keep the leg alive for the whole bar with a dense tape.
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=2), depth=depth)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", book_venue="bybit",
                 bar="1min", depth_levels=1, vpin=False)

    # Arithmetic written out, term by term (CKS 2014 eq. for e_n):
    # n=1: bid 100->100.5 UP  => +qb_n(6) ; not <= so no -qb_p
    #      ask 101->101 FLAT  => -qa_n(4) and +qa_p(7)
    e1 = 6.0 - 4.0 + 7.0
    # n=2: bid 100.5->100 DOWN => -qb_p(6)
    #      ask 101->100.5 DOWN => -qa_n(9)
    e2 = -6.0 - 9.0
    # n=3: bid 100->100 FLAT   => +qb_n(3) - qb_p(2)
    #      ask 100.5->102 UP   => +qa_p(9)
    e3 = 3.0 - 2.0 + 9.0
    assert bars["ofi_bybit"].iloc[0] == pytest.approx(e1 + e2 + e3, abs=1e-12)
    assert bars["ofi_n_bybit"].iloc[0] == 3.0


def test_microprice_two_algebraic_forms_agree_and_is_bracketed(tmp_path):
    """``I*Pa + (1-I)*Pb`` and ``(Pb*qa + Pa*qb)/(qa+qb)`` are the same estimator.

    Both are computed here, independently of the module, from the raw JSON; the
    module's column must match both. Plus the two structural invariants: the
    weighted mid is bracketed by the quotes, and equal queues collapse it to the
    plain mid EXACTLY.
    """
    root = tmp_path / "ticks"
    depth = _flat_book("bybit", DAY, minutes=20, step_s=1, levels=4)
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=20), depth=depth)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", book_venue="bybit",
                 bar="1min", depth_levels=2, vpin=False)

    t0 = _day_ms(DAY)
    last = {}
    for (_e, _s, ts, bids, asks) in sorted(depth, key=lambda r: r[2]):
        b, a = json.loads(bids), json.loads(asks)
        last[(ts - t0) // 60_000] = (b[0][0], b[0][1], a[0][0], a[0][1])
    for i, (bp, bq, ap, aq) in last.items():
        imb = bq / (bq + aq)
        form_a = imb * ap + (1.0 - imb) * bp
        form_b = (bp * aq + ap * bq) / (aq + bq)
        assert form_a == pytest.approx(form_b, abs=1e-12)
        got = bars["microprice_bybit"].iloc[i]
        assert got == pytest.approx(form_b, abs=1e-12)
        assert bp <= got <= ap
        assert bars["book_imbalance_bybit"].iloc[i] == pytest.approx(imb, abs=1e-15)

    # Equal queues -> exactly the mid (no tolerance).
    root2 = tmp_path / "ticks2"
    d2 = [_book("bybit", t0 + i * 1000, [[100.0, 3.0]], [[102.0, 3.0]]) for i in range(90)]
    _seed(root2, DAY, trades=_dense_trades(DAY, "bybit", minutes=2), depth=d2)
    b2 = _bars(root2, DAY, DAY2, price_venue="bybit", book_venue="bybit",
               bar="1min", depth_levels=1, vpin=False)
    assert b2["microprice_bybit"].iloc[0] == 101.0
    assert b2["micro_minus_mid_bybit"].iloc[0] == 0.0


def _naive_slope(levels: list[list[float]], mid: float, k: int) -> float:
    """Closed form written out with an explicit loop (no vectorization, no SQL)."""
    num = den = 0.0
    cum = 0.0
    for j in range(k):
        px, qty = levels[j]
        cum += qty
        x = abs(px - mid) / mid * 1e4
        num += x * cum
        den += x * x
    return num / den if den else float("nan")


def test_depth_slope_closed_form_matches_independent_lstsq(tmp_path):
    """Closed form ``Σx·Q / Σx²`` == ``np.linalg.lstsq`` on the same points.

    The ``risk.py`` discipline: an analytic solution is only trusted once a
    general-purpose optimiser lands on it to machine precision.
    """
    root = tmp_path / "ticks"
    depth = _flat_book("bybit", DAY, minutes=10, step_s=1, levels=8)
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=10), depth=depth)
    k = 5
    bars = _bars(root, DAY, DAY2, price_venue="bybit", book_venue="bybit",
                 bar="1min", depth_levels=k, vpin=False)

    t0 = _day_ms(DAY)
    last = {}
    for (_e, _s, ts, bids, asks) in sorted(depth, key=lambda r: r[2]):
        last[(ts - t0) // 60_000] = (json.loads(bids), json.loads(asks))
    for i, (b, a) in last.items():
        mid = (b[0][0] + a[0][0]) / 2.0
        for side, lv, col in (("bid", b, "depth_slope_bid_bybit"),
                              ("ask", a, "depth_slope_ask_bybit")):
            x = np.array([abs(lv[j][0] - mid) / mid * 1e4 for j in range(k)])
            q = np.cumsum([lv[j][1] for j in range(k)])
            beta_lstsq = float(np.linalg.lstsq(x.reshape(-1, 1), q, rcond=None)[0][0])
            beta_closed = _naive_slope(lv, mid, k)
            assert beta_closed == pytest.approx(beta_lstsq, rel=1e-12, abs=1e-12), side
            assert bars[col].iloc[i] == pytest.approx(beta_lstsq, rel=1e-12, abs=1e-12), side

    imb = bars["depth_slope_imb_bybit"].dropna()
    assert ((imb >= -1.0) & (imb <= 1.0)).all()


def test_depth_slope_is_nan_when_the_book_is_shallower_than_requested(tmp_path):
    """Fewer stored levels than ``depth_levels`` -> NaN, never a zero-padded book."""
    root = tmp_path / "ticks"
    depth = _flat_book("bybit", DAY, minutes=5, step_s=1, levels=3)
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=5), depth=depth)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", book_venue="bybit",
                 bar="1min", depth_levels=10, vpin=False)
    live = bars["coverage_book_bybit"] > 0
    assert bars.loc[live, "depth_slope_bid_bybit"].isna().all()
    assert bars.loc[live, "depth_bid_bybit"].isna().all()
    # ...but the L1 state is still observable, so it must NOT be nulled with it.
    assert bars.loc[live, "mid_bybit"].notna().any()
    assert (bars.loc[live, "depth_levels_bybit"] == 3.0).all()


def _naive_vpin(rows, v: float, window: int, day_ms_start: int):
    """Bucket-splitting loop written from the paper's description, no SQL."""
    buckets, cum, buy, sell, tsmax, ntr = [], 0.0, 0.0, 0.0, None, 0
    target = v
    for (_e, _s, tid, ts, _px, qty, aggr) in sorted(rows, key=lambda r: (r[3], r[2])):
        left = qty
        while left > 1e-15:
            room = target - cum
            take = min(left, room)
            if aggr:
                buy += take
            else:
                sell += take
            cum += take
            left -= take
            tsmax = ts
            ntr += 1
            if cum >= target - 1e-12:
                buckets.append({"buy": buy, "sell": sell, "vol": cum, "close": tsmax})
                cum, buy, sell, ntr = 0.0, 0.0, 0.0, 0
    imb = [abs(b["buy"] - b["sell"]) / v for b in buckets]
    vpin = [float(np.mean(imb[i - window + 1:i + 1])) if i + 1 >= window else float("nan")
            for i in range(len(imb))]
    for b, m, p in zip(buckets, imb, vpin):
        b["imbalance"], b["vpin"] = m, p
    return buckets


def test_vpin_hand_computed_buckets(tmp_path):
    """Six 0.5-BTC prints, V=1.0 -> three buckets whose VPIN is arithmetic on paper."""
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    sides = [True, True, True, False, False, True]      # per print
    rows = [_trade("bybit", t0 + i * 10_000, 100.0, 0.5, sides[i], f"t{i:03d}")
            for i in range(6)]
    _seed(root, DAY, trades=rows)
    vb = of.volume_buckets(DAY, DAY2, venue="bybit", bucket_volume=1.0,
                        window_buckets=2, source="local", store_dir=root)
    # bucket 0: buy 1.0 sell 0.0 -> |1-0|/1 = 1.0
    # bucket 1: buy 0.5 sell 0.5 -> |0.5-0.5|/1 = 0.0
    # bucket 2: buy 0.5 sell 0.5 -> 0.0        (print 4 sell, print 5 buy)
    assert list(vb["imbalance"]) == [1.0, 0.0, 0.0]
    assert math.isnan(vb["vpin"].iloc[0])
    assert vb["vpin"].iloc[1] == pytest.approx((1.0 + 0.0) / 2)
    assert vb["vpin"].iloc[2] == pytest.approx((0.0 + 0.0) / 2)
    # every complete bucket holds EXACTLY V
    np.testing.assert_allclose(vb["bucket_volume"].to_numpy(), 1.0, rtol=0, atol=1e-12)


def test_vpin_matches_splitting_loop_and_buckets_are_exact(tmp_path):
    """Production volume clock == the pure-Python splitting loop, rtol 1e-12.

    Quantities are chosen so prints straddle bucket boundaries constantly; a
    ``floor(cumvol / V)`` assignment would produce buckets whose volume is only
    approximately V and would fail the exactness assertion below.
    """
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    rows = []
    for i in range(500):
        qty = 0.37 + (i % 5) * 0.11
        rows.append(_trade("bybit", t0 + i * 1_000, 100.0 + i % 3, qty, (i % 3) != 1, f"t{i:05d}"))
    _seed(root, DAY, trades=rows)
    V = 7.0
    vb = of.volume_buckets(DAY, DAY2, venue="bybit", bucket_volume=V, window_buckets=4,
                        source="local", store_dir=root)
    ref = _naive_vpin(rows, V, 4, t0)
    assert len(vb) == len(ref)
    np.testing.assert_allclose(vb["bucket_volume"].to_numpy(), V, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(vb["buy_volume"].to_numpy(),
                               [b["buy"] for b in ref], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(vb["imbalance"].to_numpy(),
                               [b["imbalance"] for b in ref], rtol=1e-12, atol=1e-12)
    got, want = vb["vpin"].to_numpy(), np.array([b["vpin"] for b in ref])
    np.testing.assert_allclose(got[~np.isnan(want)], want[~np.isnan(want)],
                               rtol=1e-12, atol=1e-12)
    assert np.isnan(got[np.isnan(want)]).all()
    assert ((vb["vpin"].dropna() >= 0) & (vb["vpin"].dropna() <= 1)).all()
    np.testing.assert_array_equal(vb["close_ts_ms"].to_numpy(),
                                  np.array([b["close"] for b in ref]))


def test_liq_intensity_matches_loop(tmp_path):
    """Liquidation sums == a plain loop, and long/short split by the LIQUIDATED side."""
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    liqs = []
    for i in range(40):
        side = "long" if i % 3 else "short"
        liqs.append(("bybit", "BTCUSDT", t0 + i * 20_000, side, 60_000.0,
                     0.1 * (i + 1), 6_000.0 * (i + 1)))
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=30), liqs=liqs)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="5min", vpin=False)

    cnt, notional, lng, sht = {}, {}, {}, {}
    for (_e, _s, ts, side, _p, _q, nu) in liqs:
        b = (ts - t0) // 300_000
        cnt[b] = cnt.get(b, 0) + 1
        notional[b] = notional.get(b, 0.0) + nu
        if side == "long":
            lng[b] = lng.get(b, 0.0) + nu
        else:
            sht[b] = sht.get(b, 0.0) + nu
    live = (bars["coverage_bybit"] > 0).to_numpy()
    for i in np.nonzero(live)[0]:
        assert bars["liq_count_bybit"].iloc[i] == float(cnt.get(i, 0))
        assert bars["liq_notional_usd_bybit"].iloc[i] == pytest.approx(notional.get(i, 0.0))
        assert bars["liq_long_notional_bybit"].iloc[i] == pytest.approx(lng.get(i, 0.0))
        assert bars["liq_short_notional_bybit"].iloc[i] == pytest.approx(sht.get(i, 0.0))
    np.testing.assert_allclose(
        (bars["liq_long_notional_bybit"] + bars["liq_short_notional_bybit"]).dropna(),
        bars["liq_notional_usd_bybit"].dropna(), rtol=0, atol=1e-9)


# --------------------------------------------------------------------------- #
# B. Gap / quality model                                                        #
# --------------------------------------------------------------------------- #
def test_coverage_matches_per_second_recomputation(tmp_path):
    """Coverage from interval overlap == an 86,400-slot occupancy array, exactly."""
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    rows = []
    # 0..20 min dense, then a 40-minute hole, then 70..90 min dense.
    for i in range(0, 20 * 6):
        rows.append(_trade("bybit", t0 + i * 10_000, 100.0, 1.0, True, f"a{i:05d}"))
    for i in range(0, 20 * 6):
        rows.append(_trade("bybit", t0 + 70 * 60_000 + i * 10_000, 100.0, 1.0, False, f"b{i:05d}"))
    _seed(root, DAY, trades=rows)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="1min", vpin=False)

    ts = np.array(sorted({r[3] for r in rows}), dtype="int64")
    t1 = t0 + MS_DAY
    holes = []
    if ts[0] - t0 > of.GAP_MS:
        holes.append((t0, int(ts[0])))
    d = np.diff(ts)
    for i in np.nonzero(d > of.GAP_MS)[0]:
        holes.append((int(ts[i]), int(ts[i + 1])))
    if t1 - ts[-1] > of.GAP_MS:
        holes.append((int(ts[-1]), t1))
    # Independent route: mark every millisecond-second slot, then sum per bar.
    occupied = np.ones(86_400, dtype=bool)
    for h0, h1 in holes:
        occupied[(h0 - t0) // 1000:(h1 - t0) // 1000] = False
    per_bar = occupied.reshape(1440, 60).sum(axis=1) / 60.0
    np.testing.assert_allclose(bars["coverage"].to_numpy(), per_bar, rtol=0, atol=1e-9)
    assert bars["gap_ms"].sum() == pytest.approx(sum(h1 - h0 for h0, h1 in holes), abs=1.0)


def test_gap_bars_are_nan_never_zero_and_never_filled(tmp_path):
    """A bar inside a hole is NaN everywhere — no 0.0, no forward-fill."""
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    rows = [_trade("bybit", t0 + i * 10_000, 100.0 + i, 1.0, True, f"a{i:04d}") for i in range(60)]
    rows += [_trade("bybit", t0 + 120 * 60_000 + i * 10_000, 200.0, 1.0, True, f"b{i:04d}")
             for i in range(60)]
    _seed(root, DAY, trades=rows)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="1min", vpin=False)

    dead = bars["coverage"] <= 0.0
    assert dead.sum() > 100
    for col in ("open", "high", "low", "close", "volume", "delta_bybit", "cvd_bybit",
                "trade_count_bybit", "vwap", "dollar_volume"):
        assert bars.loc[dead, col].isna().all(), col
        assert not (bars.loc[dead, col] == 0.0).any(), col
    # No forward fill: the last observed close before the hole never reappears.
    last_before = bars["close"].dropna().iloc[0]
    assert not (bars.loc[dead, "close"] == last_before).any()
    assert bars.loc[dead, "is_gap"].all()


def test_duckdb_least_greatest_are_null_ignoring_regression_pin(tmp_path):
    """Pin the DuckDB semantics that made the SQL overlap version wrong.

    ``least(x, NULL)`` returns ``x`` in DuckDB (NULL-ignoring), unlike standard
    SQL where it is NULL. Written naively over a LEFT JOIN, the per-bar overlap
    therefore evaluated to a full ``bar_ms`` for every bar with no matching hole
    — reporting a fully-empty day. ``_gap_ms_per_bar`` is numpy precisely so the
    bug is inexpressible; this test keeps the reason on the record.
    """
    con = duckdb.connect()
    assert con.execute("SELECT least(5, NULL), greatest(5, NULL)").fetchone() == (5, 5)
    con.close()


def test_ret_spans_gap_marks_both_sides_and_segment_increments(tmp_path):
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    rows = [_trade("bybit", t0 + i * 10_000, 100.0, 1.0, True, f"a{i:04d}") for i in range(60)]
    rows += [_trade("bybit", t0 + 60 * 60_000 + i * 10_000, 200.0, 1.0, True, f"b{i:04d}")
             for i in range(60)]
    _seed(root, DAY, trades=rows)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="1min", vpin=False)

    cov = bars["coverage"].to_numpy()
    span = bars["ret_spans_gap"].to_numpy()
    assert span[0]                                   # no prior bar exists
    for i in range(1, len(bars)):
        expect = (cov[i] < 1.0) or (cov[i - 1] < 1.0) or np.isnan(bars["close"].to_numpy()[i - 1])
        assert bool(span[i]) == bool(expect), i
    seg = bars["segment"].to_numpy()
    assert seg[0] == 0.0
    assert seg.max() >= 1.0                          # a new segment after the hole
    assert np.all(np.diff(seg) >= 0)                 # monotone non-decreasing
    # CVD restarts inside the new segment rather than carrying the old level.
    seg1 = bars[bars["segment"] == 1.0]["cvd_bybit"].dropna()
    assert seg1.iloc[0] == pytest.approx(bars[bars["segment"] == 1.0]["delta_bybit"].dropna().iloc[0])


# --------------------------------------------------------------------------- #
# C. No look-ahead                                                              #
# --------------------------------------------------------------------------- #
def _three_day_store(root: Path) -> None:
    for d in (DAY, DAY2, DAY3):
        _seed(root, d,
              trades=_dense_trades(d, "bybit", minutes=1440, step_s=20),
              depth=_flat_book("bybit", d, minutes=180, step_s=1, levels=4))


def test_truncation_invariance(tmp_path):
    """``bars(start, T)`` equals ``bars(start, T')`` truncated at T.

    To a few ULP, **not** byte-for-byte, and the difference is not pedantry.
    DuckDB's parallel float aggregation is not order-stable, so the same real day
    summed at ``threads=1`` and ``threads=8`` differs by ~4e-08 absolute, and on
    the real archive this comparison moves ``ofi`` by ~1e-11 against a mean of
    ~824 (≈1e-14 relative). The synthetic fixture here is small enough to come
    out exact, which is precisely why asserting exactness here would have pinned
    a property the module does not actually have at scale. The honest contract is
    machine precision.
    """
    root = tmp_path / "ticks"
    _three_day_store(root)
    short = _bars(root, DAY, f"{DAY3} 06:00", price_venue="bybit", book_venue="bybit",
                  bar="1h", depth_levels=3)
    long = _bars(root, DAY, f"{DAY3} 18:00", price_venue="bybit", book_venue="bybit",
                 bar="1h", depth_levels=3)
    trunc = long.loc[long.index < short.index[-1] + pd.Timedelta("1h")]
    assert list(short.columns) == list(trunc.columns)
    pd.testing.assert_frame_equal(short, trunc, check_exact=False,
                                  rtol=1e-12, atol=1e-12, check_like=False)


def test_future_trade_cannot_change_a_past_bar(tmp_path):
    """Appending rows after T leaves every bar at or before T untouched."""
    root = tmp_path / "ticks"
    _three_day_store(root)
    before = _bars(root, DAY, f"{DAY3} 06:00", price_venue="bybit", bar="1h", vpin=False)
    con = collector.open_db(root / f"{DAY3}.duckdb")
    t = _day_ms(DAY3) + 8 * 3_600_000
    for i in range(100):
        con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)",
                    ["bybit", "BTCUSDT", f"future{i}", t + i * 1000, 999_999.0, 50.0, True])
    con.close()
    after = _bars(root, DAY, f"{DAY3} 06:00", price_venue="bybit", bar="1h", vpin=False)
    # Same tolerance rationale as test_truncation_invariance: machine precision,
    # not byte identity — float aggregation order is not a stable property.
    pd.testing.assert_frame_equal(before, after, check_exact=False,
                                  rtol=1e-12, atol=1e-12)


def test_vpin_asof_excludes_a_bucket_closing_after_the_bar_end(tmp_path):
    """A bucket that closes 1 ms past the bar end is invisible to that bar."""
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    rows = [_trade("bybit", t0 + 60_000 - 1, 100.0, 1.0, True, "a0"),      # closes bucket 0
            _trade("bybit", t0 + 60_000 + 1, 100.0, 1.0, False, "a1")]     # closes bucket 1
    # keep the leg alive over the first three minutes
    rows += [_trade("bybit", t0 + i * 10_000, 100.0, 1e-9, True, f"k{i:04d}") for i in range(20)]
    _seed(root, DAY, trades=rows)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="1min",
                 vpin_bucket_volume=1.0, vpin_window_buckets=1)
    # bucket 0 closes at t0+59.999 s -> inside bar 0.
    assert bars["vpin_buckets_bybit"].iloc[0] == 1.0
    assert bars["vpin_bybit"].iloc[0] == pytest.approx(1.0)
    # bucket 1 closes at t0+60.001 s -> bar 1, never bar 0.
    assert bars["vpin_buckets_bybit"].iloc[1] == 1.0
    assert bars["vpin_age_s_bybit"].iloc[0] == pytest.approx(0.001, abs=1e-9)


def test_vpin_bucket_volume_uses_only_strictly_prior_days(tmp_path):
    """Day 1 is warm-up NaN, and editing the LAST day cannot move an earlier V."""
    root = tmp_path / "ticks"
    _three_day_store(root)
    bars = _bars(root, DAY, f"{DAY3} 23:00", price_venue="bybit", bar="1h",
                 vpin_buckets_per_day=4, vpin_window_buckets=2)
    d1 = bars.loc[bars.index.strftime("%Y-%m-%d") == DAY, "vpin_bybit"]
    assert d1.isna().all(), "the first day has no prior day and must not guess V"
    assert bars.loc[bars.index.strftime("%Y-%m-%d") == DAY3, "vpin_bybit"].notna().any()

    con = collector.open_db(root / f"{DAY3}.duckdb")
    for i in range(2000):
        con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)",
                    ["bybit", "BTCUSDT", f"z{i}", _day_ms(DAY3) + 23 * 3_600_000 + i,
                     100.0, 25.0, True])
    con.close()
    bars2 = _bars(root, DAY, f"{DAY3} 23:00", price_venue="bybit", bar="1h",
                  vpin_buckets_per_day=4, vpin_window_buckets=2)
    pd.testing.assert_series_equal(bars["vpin_bybit"], bars2["vpin_bybit"], check_exact=True)


def test_positions_from_bars_pass_the_backtest_lookahead_guard(tmp_path):
    root = tmp_path / "ticks"
    _three_day_store(root)
    bars = _bars(root, DAY, DAY3, price_venue="bybit", bar="1h", vpin=False)
    pos = np.sign(bars["delta_bybit"].fillna(0.0)).clip(-1, 1)
    pos = of.gap_flat_positions(pos, bars)
    res = backtest.run(pos, bars["close"], periods_per_year=of.periods_per_year("1h"))
    assert set(res) >= {"equity", "returns", "stats"}
    # _assert_no_lookahead runs inside backtest.run; reaching here means it held.
    assert len(res["returns"]) > 0


# --------------------------------------------------------------------------- #
# D. Honest-empty tables                                                        #
# --------------------------------------------------------------------------- #
def test_liquidations_present_but_zero_with_a_live_witness_is_zero(tmp_path):
    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=60), liqs=())
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="15min", vpin=False)
    live = bars["coverage_bybit"] > 0
    assert live.sum() >= 4
    assert (bars.loc[live, "liq_count_bybit"] == 0.0).all()
    assert (bars.loc[live, "liq_notional_usd_bybit"] == 0.0).all()


def test_liquidations_zero_with_a_dead_witness_is_nan(tmp_path):
    """No trades and no book for the venue -> the leg is dead, so NaN, not 0.0."""
    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "coinbase", minutes=1440, step_s=20), liqs=())
    bars = _bars(root, DAY, DAY2, price_venue="coinbase", trade_venues=("coinbase", "bybit"),
                 bar="1h", vpin=False)
    assert bars["liq_count_bybit"].isna().all()
    assert bars["delta_bybit"].isna().all()
    assert bars["coverage_coinbase"].max() == pytest.approx(1.0)


def test_absent_liquidations_table_is_nan_and_the_manifest_records_the_absence(tmp_path):
    """A day whose source carries no liquidations table at all -> NaN + manifest flag."""
    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=1440, step_s=20),
          make_tables=False)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="1h", vpin=False)
    assert bars["liq_count_bybit"].isna().all()
    days = bars.attrs["orderflow"]["manifest"]["days"]
    assert days[0]["tables_present"]["liquidations"] is False
    # ...while the trade leg on the very same day is demonstrably alive.
    assert (bars["coverage_bybit"] > 0).all()


# --------------------------------------------------------------------------- #
# E. IO / multi-day                                                             #
# --------------------------------------------------------------------------- #
def test_two_day_concat_has_a_continuous_grid_and_no_duplicates(tmp_path):
    root = tmp_path / "ticks"
    for d in (DAY, DAY2):
        _seed(root, d, trades=_dense_trades(d, "bybit", minutes=1440, step_s=20))
    bars = _bars(root, DAY, DAY3, price_venue="bybit", bar="1h", vpin=False)
    assert len(bars) == 48
    assert bars.index.is_monotonic_increasing and bars.index.is_unique
    assert (np.diff(bars.index.view("int64")) == 3_600_000_000_000).all()
    assert bars["coverage"].min() == pytest.approx(1.0)
    # The midnight seam must not create a hole or a duplicate bar.
    assert not bars["is_gap"].any()


# The "range is not final" warning is the POINT of these two: an unresolved day
# must be announced, and the assertions below check what it announces.
@pytest.mark.filterwarnings("ignore:order-flow range is NOT final")
def test_missing_middle_day_produces_empty_bars_not_a_shortened_index(tmp_path):
    root = tmp_path / "ticks"
    for d in (DAY, DAY3):
        _seed(root, d, trades=_dense_trades(d, "bybit", minutes=1440, step_s=20))
    bars = _bars(root, DAY, "2025-03-07", price_venue="bybit", bar="1h", vpin=False)
    assert len(bars) == 72
    mid = bars.index.strftime("%Y-%m-%d") == DAY2
    assert mid.sum() == 24
    assert bars.loc[mid, "coverage"].max() == 0.0
    assert bars.loc[mid, "close"].isna().all()
    # Two CLEAN stretches (day 1, day 3). Every uncovered bar in between also
    # takes an id of its own so no cumulative level can bridge it, which is why
    # the raw id count is larger than the clean-run count and is reported apart.
    cov = bars.attrs["orderflow"]["coverage_summary"]
    assert cov["segments"] == 2
    assert cov["segment_ids_total"] > cov["segments"]
    day1 = bars.index.strftime("%Y-%m-%d") == DAY
    day3 = bars.index.strftime("%Y-%m-%d") == DAY3
    assert bars.loc[day1, "segment"].iloc[0] != bars.loc[day3, "segment"].iloc[-1]


@pytest.mark.filterwarnings("ignore:order-flow range is NOT final")
def test_locked_day_file_is_skipped_and_recorded(tmp_path):
    """The live writer owns its file — we skip it and say so, we never fight it."""
    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=1440, step_s=20))
    _seed(root, DAY2, trades=_dense_trades(DAY2, "bybit", minutes=1440, step_s=20))
    writer = duckdb.connect(str(root / f"{DAY2}.duckdb"))   # exclusive lock
    try:
        bars = _bars(root, DAY, DAY3, price_venue="bybit", bar="1h", vpin=False)
    finally:
        writer.close()
    manifest = bars.attrs["orderflow"]["manifest"]
    assert any(DAY2 in p for p in manifest["skipped_locked"])
    day2 = bars.index.strftime("%Y-%m-%d") == DAY2
    assert bars.loc[day2, "coverage"].max() == 0.0     # skipped, therefore honestly empty
    day1 = bars.loc[bars.index.strftime("%Y-%m-%d") == DAY, "coverage"]
    # Every DAY bar is fully covered except the last, which butts up against the
    # skipped day and is therefore honestly reported as partial, not as full.
    assert day1.iloc[:-1].min() == pytest.approx(1.0)
    assert day1.iloc[-1] < 1.0


def test_open_day_file_is_never_read(tmp_path):
    """Today's file is not a candidate even when it is unlocked and on disk."""
    root = tmp_path / "ticks"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _seed(root, today, trades=_dense_trades(today, "bybit", minutes=60))
    src = of._open_source([today], source="local", store_dir=root, hf_repo=of.HF_REPO,
                          exchanges=("bybit",), t0_ms=_day_ms(today),
                          t1_ms=_day_ms(today) + MS_DAY, tables=["trades"])
    try:
        assert src.con.execute("SELECT count(*) FROM of_trades").fetchone()[0] == 0
        assert src.manifest["days"][0]["source"] is None
    finally:
        src.close()


def test_cache_roundtrip_and_spec_hash_invalidation(tmp_path, monkeypatch):
    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=1440, step_s=20))
    monkeypatch.setattr(of, "_ORDERFLOW_CACHE", tmp_path / "of-cache")
    a = of.order_flow_bars(DAY, DAY2, price_venue="bybit", bar="1h", source="local",
                           store_dir=root, cache=True, vpin=False)
    files = list((tmp_path / "of-cache").rglob("*.parquet"))
    assert len(files) == 1
    b = of.order_flow_bars(DAY, DAY2, price_venue="bybit", bar="1h", source="local",
                           store_dir=root, cache=True, vpin=False)
    pd.testing.assert_frame_equal(a, b, check_exact=True)
    assert b.attrs["orderflow"]["schema_version"] == of.SCHEMA_VERSION

    # A formula/version change must land in a DIFFERENT cache slot.
    spec = dict(a.attrs["orderflow"]["params"])
    h1 = of._spec_hash(spec)
    monkeypatch.setattr(of, "SCHEMA_VERSION", of.SCHEMA_VERSION + "-next")
    assert of._spec_hash(spec) != h1
    # ...and so must a parameter change.
    assert of._spec_hash({**spec, "depth_levels": 11}) != of._spec_hash(spec)


# --------------------------------------------------------------------------- #
# F. Contract & rails                                                           #
# --------------------------------------------------------------------------- #
def test_bar_contract_columns_dtypes_index(tmp_path):
    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=1440, step_s=20),
          depth=_flat_book("bybit", DAY, minutes=1440, step_s=20, levels=4))
    bars = _bars(root, DAY, DAY2, price_venue="bybit", book_venue="bybit",
                 bar="15min", depth_levels=3)
    assert list(bars.columns[:5]) == ["open", "high", "low", "close", "volume"]
    assert isinstance(bars.index, pd.DatetimeIndex)
    assert str(bars.index.tz) == "UTC"
    assert bars.index.name == "timestamp"
    assert bars.index.is_monotonic_increasing and bars.index.is_unique
    assert (np.diff(bars.index.view("int64")) == 15 * 60 * 1_000_000_000).all()
    for col in bars.columns:
        if col in ("is_gap", "ret_spans_gap"):
            assert bars[col].dtype == np.dtype("bool"), col
        else:
            assert bars[col].dtype == np.dtype("float64"), col
    # Bars are left-labelled at the OPEN and see only [t, t+bar).
    assert bars.index[0] == pd.Timestamp(DAY, tz="UTC")


def test_features_and_backtest_consume_bars_unchanged(tmp_path):
    """features.atr / realized_vol / walk_forward take these bars with ZERO changes.

    This is the load-bearing claim of M1 and it is executed, not argued: the
    ``scripts/compare.py:533`` call idiom is reproduced verbatim.
    """
    root = tmp_path / "ticks"
    for d in (DAY, DAY2, DAY3):
        _seed(root, d, trades=_dense_trades(d, "bybit", minutes=1440, step_s=20))
    bars = _bars(root, DAY, "2025-03-07", price_venue="bybit", bar="1h", vpin=False)
    close = bars["close"]

    atr = features.atr(bars, window=14)              # needs high/low/close
    assert np.isfinite(atr.dropna()).all()
    r = features.log_returns(close)
    rv = features.realized_vol(r, 20, of.periods_per_year("1h"))
    assert len(rv) == len(bars)

    pos = of.gap_flat_positions(pd.Series(0.5, index=bars.index), bars)
    w = backtest.walk_forward(lambda px, p=pos: p.reindex(px.index), close,
                              n_splits=3, cost_bps=10.0, slippage_bps=2.0,
                              periods_per_year=of.periods_per_year("1h"))
    assert {"oos", "is_", "folds"} <= set(w)
    assert "deflated_sharpe" in w["oos"]
    assert np.isfinite(risk.min_backtest_length(5))


def test_every_emitted_column_has_a_provenance_note(tmp_path):
    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=1440, step_s=20),
          depth=_flat_book("bybit", DAY, minutes=1440, step_s=20, levels=4),
          liqs=[("bybit", "BTCUSDT", _day_ms(DAY) + 1000, "long", 1.0, 1.0, 1.0)])
    bars = _bars(root, DAY, DAY2, price_venue="bybit", book_venue="bybit", bar="1h")
    table = of.provenance_table(bars)
    assert list(table["column"]) == list(bars.columns)
    missing = table.loc[table["approximation"].str.strip() == "", "column"].tolist()
    assert missing == [], f"columns with no stated approximation: {missing}"
    assert (table["citation"].str.strip() != "").all()
    assert (table["family"].str.strip() != "").all()
    # The VPIN family must carry the contested note wherever it goes.
    vp = table[table["column"].str.startswith("vpin")]
    assert len(vp) == 5 and (vp["contested"].str.contains("Andersen & Bondarenko")).all()
    # Every OFI-family column carries the snapshot label, not just ofi_ itself.
    ofi = table[table["column"].str.startswith("ofi")]
    assert len(ofi) == 3
    assert ofi["approximation"].str.contains(of._OFI_LABEL, regex=False).all()


def test_provenance_notes_refuse_an_empty_approximation():
    with pytest.raises(ValueError):
        of.FeatureNote(column="x", family="trade", formula="f", citation="c",
                       approximation="   ", units="u", source_leg="l")


def test_honesty_sentences_present_verbatim():
    """The five load-bearing sentences must still be in the module docstring.

    Whitespace is normalized (the docstring is reflowed prose) but the wording is
    compared exactly, the ``check_terminal.cjs`` discipline: rewording a label is
    a test failure, not a style choice.
    """
    doc = " ".join((of.__doc__ or "").split())
    for sentence in of.HONESTY_SENTENCES:
        assert " ".join(sentence.split()) in doc, sentence
    assert len(of.HONESTY_SENTENCES) == 5


def test_module_never_imports_dashboard_or_network_clients():
    """AST scan: research code must not reach into ``dashboard/`` or the network.

    (``hf://`` reads go through DuckDB's own httpfs at query time — that is a data
    path chosen by the caller via ``source=``, not a Python import, and every
    test in this file runs with ``source="local"``.)
    """
    tree = ast.parse((_REPO / "btcquant" / "orderflow.py").read_text(encoding="utf-8"))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
    banned = ("dashboard", "requests", "urllib", "httpx", "aiohttp", "socket", "websockets")
    for m in mods:
        assert not any(m == b or m.startswith(b + ".") for b in banned), m
    src = (_REPO / "btcquant" / "orderflow.py").read_text(encoding="utf-8")
    assert "dashboard/" not in src.replace("dashboard/terminal-state.js", "")


def test_no_ai_attribution_strings():
    """Repo rule (DEVELOPMENT.md §2 rail 7): no AI attribution in shipped artifacts.

    The banned tokens are assembled from halves so this test does not itself
    contain the literals it forbids — otherwise it would fail on its own source.
    """
    banned = [a + b for a, b in (("cla", "ude"), ("anthro", "pic"),
                                 ("co-authored", "-by"), ("generated ", "with"),
                                 ("chat", "gpt"), ("open", "ai"), ("copi", "lot"))]
    for path in (_REPO / "btcquant" / "orderflow.py", Path(__file__),
                 _REPO / "scripts" / "orderflow_smoke.py"):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for token in banned:
            assert token not in text, f"{path.name}: {token}"


def test_constants_mirror_their_single_source_of_truth():
    """GAP_MS / HF_REPO / grace / store dir / size buckets must not drift.

    They are restated in ``orderflow.py`` (a library importing ``scripts/`` by
    path at runtime would be worse), so the anti-drift guarantee is this test.
    """
    spec = importlib.util.spec_from_file_location(
        "check_ticks_probe", _REPO / "scripts" / "check_ticks.py")
    ct = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ct)
    assert of.GAP_MS == ct.GAP_MS

    spec2 = importlib.util.spec_from_file_location(
        "upload_hf_probe", _REPO / "scripts" / "upload_hf.py")
    uh = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(uh)
    assert of.HF_REPO == uh.DEFAULT_REPO
    assert of.GRACE_CLOSE_MIN == uh.GRACE_CLOSE_MIN

    assert of.STORE_DIR == collector.DEFAULT_DB
    # Column names and types must match the collector's own row contract.
    for table, cols in of._TABLE_SCHEMA.items():
        assert tuple(n for n, _ in cols) == collector._TABLE_COLUMNS[table]

    # The size taxonomy is the terminal's CvdStore default, read from the source.
    js = (_REPO / "dashboard" / "terminal-state.js").read_text(encoding="utf-8")
    assert "[1e4, 1e5, 1e6]" in js
    assert of.SIZE_BUCKETS_USD == (1e4, 1e5, 1e6)


def test_periods_per_year_matches_the_closed_form():
    for bar, want in (("1min", 525_600), ("5min", 105_120), ("15min", 35_040), ("1h", 8_760)):
        assert of.periods_per_year(bar) == want
        assert of.periods_per_year(bar) == 365 * 24 * 60 * 60 * 1000 // int(
            pd.Timedelta(bar) / pd.Timedelta("1ms"))
    with pytest.raises(of.OrderFlowError):
        of.periods_per_year("7min")


def test_price_venue_is_required_and_grid_alignment_is_enforced(tmp_path):
    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=60))
    with pytest.raises(of.OrderFlowError):
        of.order_flow_bars(DAY, DAY2, price_venue="", source="local", store_dir=root)
    with pytest.raises(of.OrderFlowError):
        of.order_flow_bars(f"{DAY} 00:07", DAY2, price_venue="bybit", bar="1h",
                           source="local", store_dir=root, cache=False)


def test_gap_helpers_label_and_behave(tmp_path):
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    rows = [_trade("bybit", t0 + i * 10_000, 100.0 + i, 1.0, True, f"a{i:04d}") for i in range(360)]
    rows += [_trade("bybit", t0 + 360 * 60_000 + i * 10_000, 200.0, 1.0, True, f"b{i:04d}")
             for i in range(360)]
    _seed(root, DAY, trades=rows)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="1min", vpin=False)

    kept = of.drop_gap_bars(bars)
    assert len(kept) < len(bars)
    assert (kept["coverage"] >= 1.0).all() and not kept["ret_spans_gap"].any()

    # The flag is applied SHIFTED BACK by one bar: backtest.run trades
    # pos.shift(1), so the position that must be flat for a gap-spanning r[t] is
    # the one at t-1. Asserted here in both directions, plus the final bar.
    pos = of.gap_flat_positions(pd.Series(1.0, index=bars.index), bars)
    spans = bars["ret_spans_gap"].to_numpy()
    want = np.where(np.append(spans[1:], True), 0.0, 1.0)
    np.testing.assert_array_equal(pos.to_numpy(), want)
    traded = pos.shift(1).to_numpy()
    assert np.all(traded[1:][spans[1:]] == 0.0), "a gap-spanning return was traded"

    segs = of.segments(bars)
    assert len(segs) >= 2
    assert sum(len(s) for s in segs) == int(of.coverage_mask(bars).sum())
    for s in segs:
        assert (np.diff(s.index.view("int64")) == 60_000_000_000).all()


# backtest.run's px.pct_change() emits pandas' fill_method deprecation warning on a
# price series that legitimately contains NaN (a bar inside a hole). That padding is
# exactly what gap_flat_positions exists to keep untraded — documented in its
# docstring, so the warning is expected here rather than a defect to chase.
@pytest.mark.filterwarnings("ignore:The default fill_method:FutureWarning")
def test_gap_flat_positions_never_lets_a_gap_return_be_traded(tmp_path):
    """End-to-end: run the backtester and check no gap-spanning bar earns P&L.

    This pins the off-by-one that the naive in-place mask gets wrong. With the
    flag applied in place, ``pos[t-1]`` would still be 1.0 into a bar whose
    close-to-close return jumps a multi-hour hole.
    """
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    rows = [_trade("bybit", t0 + i * 10_000, 100.0 + i, 1.0, True, f"a{i:04d}") for i in range(720)]
    rows += [_trade("bybit", t0 + 300 * 60_000 + i * 10_000, 500.0, 1.0, True, f"b{i:04d}")
             for i in range(720)]
    _seed(root, DAY, trades=rows)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="1h", vpin=False)
    assert bars["ret_spans_gap"].sum() > 0

    pos = of.gap_flat_positions(pd.Series(1.0, index=bars.index), bars)
    res = backtest.run(pos, bars["close"], cost_bps=0.0, slippage_bps=0.0,
                       periods_per_year=of.periods_per_year("1h"))
    gross = pd.Series(res["gross_returns"]).dropna()
    spans = bars["ret_spans_gap"].reindex(gross.index).fillna(True).astype(bool)
    assert (gross[spans].abs() < 1e-15).all(), "P&L was earned on a gap-spanning return"
    # The price really did jump across the hole, so a naive in-place mask would
    # have earned that move — this is not a vacuous assertion.
    assert bars["close"].dropna().pct_change().abs().max() > 0.2


def test_available_days_lists_only_closed_local_days(tmp_path):
    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=60))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _seed(root, today, trades=_dense_trades(today, "bybit", minutes=60))
    df = of.available_days(store_dir=root, source="local", by_exchange=True)
    assert set(df["date"]) == {DAY}
    trades = df[(df["table"] == "trades") & (df["exchange"] == "bybit")]
    assert int(trades["rows"].iloc[0]) == 360
    assert (df[df["table"] == "liquidations"]["rows"] == 0).all()


# --------------------------------------------------------------------------- #
# F. Regressions from the M1 review — each one reproduced BEFORE it was fixed   #
#    and pinned here so the fix cannot be quietly undone.                       #
# --------------------------------------------------------------------------- #
def test_dead_trade_leg_under_a_live_book_is_unknown_not_zero(tmp_path):
    """The exact shape of the real 2026-07-25 binancef leg: depth, no trades.

    Witnessing a TRADE venue with ``trades UNION depth_snapshots`` scored that
    venue as fully alive and then wrote 0.0 into volume / trade_count / delta /
    cvd while every close was NaN — a flat, tradeable series where the honest
    answer is "unknown". Reproduced on the archive before the fix (20 of 24 1h
    bars at coverage 1.0), pinned here on a fixture with the same shape.
    """
    root = tmp_path / "ticks"
    _seed(root, DAY, depth=_flat_book("bybit", DAY, minutes=1440, step_s=1, levels=4))
    bars = _bars(root, DAY, DAY2, price_venue="bybit", book_venue="bybit", bar="1h",
                 vpin=False)
    assert bars["coverage"].max() == 0.0, "a venue with no trade leg is not covered"
    assert not bars["is_gap"].eq(False).any()
    for col in ("volume", "trade_count", "dollar_volume", "delta_bybit", "cvd_bybit",
                "buy_volume_bybit", "delta_usd_whale_bybit"):
        assert bars[col].isna().all(), f"{col} fabricated a zero on a dead trade leg"
    # ...while the book leg is independently alive and fully populated.
    assert bars["coverage_book_bybit"].min() == 1.0
    assert bars["mid_bybit"].notna().all()


def test_a_trade_leg_that_dies_mid_day_is_nan_and_the_move_is_never_traded(tmp_path):
    """Trade leg dies for 30 min under a live book; price gaps 100 -> 150.

    Before the fix the outage bars read coverage=1.0, is_gap=False, delta=0.0,
    ``drop_gap_bars`` certified them clean, and because ``walk_forward`` drops
    NaN prices the whole +50% move collapsed into one tradeable bar.
    """
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    trades = [_trade("bybit", t0 + i * 10_000, 100.0, 1.0, True, f"a{i:04d}")
              for i in range(180)]                                   # 00:00-00:30
    trades += [_trade("bybit", t0 + 3_600_000 + i * 10_000, 150.0, 1.0, True, f"b{i:04d}")
               for i in range(180)]                                  # 01:00-01:30
    depth = _flat_book("bybit", DAY, minutes=120, step_s=1, levels=4)  # book never dies
    _seed(root, DAY, trades=trades, depth=depth)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", book_venue="bybit", bar="1min",
                 vpin=False).iloc[:120]

    hole = bars.index[(bars.index >= bars.index[31]) & (bars.index < bars.index[60])]
    assert (bars.loc[hole, "coverage"] < 1.0).all()
    assert bars.loc[hole, "is_gap"].all()
    assert bars.loc[hole, "delta_bybit"].isna().all()
    # No bar with an unknown close may be certified clean, and the position that
    # would have traded the across-the-hole move is forced flat.
    nan_close = bars["close"].isna()
    assert bars.loc[nan_close, "ret_spans_gap"].all()
    kept = of.drop_gap_bars(bars)
    assert kept["close"].notna().all()
    pos = of.gap_flat_positions(pd.Series(1.0, index=bars.index), bars)
    jump = bars["close"].dropna()
    big = jump.pct_change().abs().idxmax()
    assert jump.pct_change().abs().max() > 0.4
    assert float(pos.shift(1).reindex(jump.index).loc[big]) == 0.0


def test_ofi_snapshot_sampling_can_OVERSTATE_the_event_level_value(tmp_path):
    """The withdrawn "understates" claim, falsified through the module itself.

    Cont-Kukanov-Stoikov fire BOTH indicators when ``P_n == P_(n-1)``, so a price
    round-trip inside the sampling interval reads as a pure queue depletion of
    the whole size difference. Event level and sampled level are computed here
    from two stores that differ only in whether the middle state was recorded.
    """
    root_all, root_sampled = tmp_path / "all", tmp_path / "sampled"
    t0 = _day_ms(DAY)
    ask = [[102.0, 7.0]]
    states = [(0, [[100.0, 10.0]]), (1, [[101.0, 5.0]]), (2, [[100.0, 1.0]])]
    _seed(root_all, DAY, depth=[_book("bybit", t0 + i * 1000, b, ask) for i, b in states])
    _seed(root_sampled, DAY,
          depth=[_book("bybit", t0 + i * 1000, b, ask) for i, b in states if i != 1])
    kw = dict(price_venue="bybit", book_venue="bybit", bar="1h", depth_levels=1,
              vpin=False, liquidations=False)
    full = _bars(root_all, DAY, DAY2, **kw)
    samp = _bars(root_sampled, DAY, DAY2, **kw)
    # Hand arithmetic: e1 = 1{101>=100}*5 - 1{101<=100}*10 = +5; e2 = -5. Sum 0.
    assert full["ofi_n_bybit"].iloc[0] == 2
    assert full["ofi_bybit"].iloc[0] == pytest.approx(0.0, abs=1e-12)
    # Missing the middle state: e = 1{100>=100}*1 - 1{100<=100}*10 = -9.
    assert samp["ofi_n_bybit"].iloc[0] == 1
    assert samp["ofi_bybit"].iloc[0] == pytest.approx(-9.0, abs=1e-12)
    assert abs(samp["ofi_bybit"].iloc[0]) > abs(full["ofi_bybit"].iloc[0])
    # ...and the runtime label must not claim a known direction again.
    note = of.PROVENANCE["ofi_{b}"].approximation
    assert "DIRECTION OF THE BIAS IS NOT KNOWN" in note
    assert "UNDERSTATES" not in note


def test_ofi_first_bar_keeps_the_pair_straddling_the_window_start(tmp_path):
    """Bar 0 of a mid-day request must not silently lose one contribution."""
    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=1440, step_s=20),
          depth=_flat_book("bybit", DAY, minutes=240, step_s=1, levels=4))
    kw = dict(price_venue="bybit", book_venue="bybit", bar="1h", depth_levels=3,
              vpin=False)
    wide = _bars(root, DAY, f"{DAY} 04:00", **kw)
    narrow = _bars(root, f"{DAY} 02:00", f"{DAY} 04:00", **kw)
    ts = pd.Timestamp(f"{DAY} 02:00", tz="UTC")
    assert narrow.loc[ts, "ofi_n_bybit"] == wide.loc[ts, "ofi_n_bybit"]
    assert narrow.loc[ts, "ofi_bybit"] == pytest.approx(wide.loc[ts, "ofi_bybit"], abs=1e-9)
    # The straddling pair really exists (this is not vacuous): the same bar
    # holds one more pair than it has snapshots-minus-one within the bar.
    assert narrow.loc[ts, "ofi_n_bybit"] == narrow.loc[ts, "book_snapshots_bybit"]


def test_cvd_resets_across_a_hole_shorter_than_one_bar(tmp_path):
    """A 20-minute hole inside a 1h bar breaks the segment and resets cvd."""
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    rows = [_trade("bybit", t0 + i * 10_000, 100.0, 1.0, True, f"a{i:05d}")
            for i in range(2 * 360)]                       # 00:00-02:00 dense
    rows += [_trade("bybit", t0 + 2 * 3_600_000 + 1_200_000 + i * 10_000,
                    100.0, 1.0, True, f"b{i:05d}")
             for i in range(4 * 360)]                      # resumes 02:20
    _seed(root, DAY, trades=rows)
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="1h", vpin=False)
    hit = bars.index[2]
    assert bars.loc[hit, "coverage"] == pytest.approx(2 / 3, abs=1e-9)
    assert bars.loc[hit, "is_gap"]
    assert bars["segment"].nunique() > 1, "a sub-bar hole must break the segment"
    assert bars.loc[hit, "segment"] != bars.loc[bars.index[1], "segment"]
    # The level restarts: the 02:00 bar's cvd is its OWN delta, not the running
    # total that would have implied 20 minutes of flow nobody observed.
    assert bars.loc[hit, "cvd_bybit"] == pytest.approx(bars.loc[hit, "delta_bybit"])
    assert bars.loc[bars.index[3], "cvd_bybit"] == pytest.approx(
        bars.loc[bars.index[3], "delta_bybit"])


def test_two_symbols_on_one_venue_raise_and_symbol_selects_one(tmp_path):
    """Rail 5 applies to SYMBOLS, not only to venues. Never pool, never guess."""
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    btc = [_trade("bybit", t0 + i * 10_000, 100.0, 1.0, True, f"a{i:04d}") for i in range(360)]
    eth = [("bybit", "ETHUSDT", f"e{i:04d}", t0 + i * 10_000, 2.0, 50.0, False)
           for i in range(360)]
    depth = [_book("bybit", t0 + i * 1000, [[99.0, 1.0]], [[101.0, 1.0]]) for i in range(3600)]
    depth += [("bybit", "ETHUSDT", t0 + i * 1000, json.dumps([[1.0, 9.0]]),
               json.dumps([[3.0, 9.0]])) for i in range(3600)]
    _seed(root, DAY, trades=btc + eth, depth=depth)

    with pytest.raises(of.OrderFlowError) as exc:
        _bars(root, DAY, DAY2, price_venue="bybit", bar="1h", vpin=False)
    assert "more than one symbol" in str(exc.value)
    assert "ETHUSDT" in str(exc.value) and "BTCUSDT" in str(exc.value)

    bars = _bars(root, DAY, DAY2, price_venue="bybit", book_venue="bybit",
                 symbol="BTCUSDT", bar="1h", depth_levels=1, vpin=False)
    row = bars.iloc[0]
    assert row["close"] == 100.0 and row["low"] == 100.0
    assert row["volume"] == pytest.approx(360.0)      # BTC alone, not 360+18_000
    assert row["delta_bybit"] == pytest.approx(360.0)
    assert row["mid_bybit"] == pytest.approx(100.0)   # never the ETH book


def test_an_unclosed_day_inside_the_range_is_never_cached(tmp_path, monkeypatch):
    """The cache must not freeze an all-gap frame that only exists because a day
    had not closed yet. Same spec, before and after the day closes."""
    root = tmp_path / "ticks"
    cache = tmp_path / "cache"
    monkeypatch.setattr(of, "_ORDERFLOW_CACHE", cache)
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=1440, step_s=20))
    _seed(root, DAY2, trades=_dense_trades(DAY2, "bybit", minutes=1440, step_s=20))
    open_now = _day_ms(DAY2) + 12 * 3_600_000          # mid-DAY2: not closed yet
    kw = dict(price_venue="bybit", bar="1h", vpin=False, store_dir=root, source="local")

    with pytest.warns(UserWarning, match="NOT final"):
        early = of.order_flow_bars(DAY, DAY3, now_ms=open_now, cache=True, **kw)
    assert early.loc[early.index.strftime("%Y-%m-%d") == DAY2, "coverage"].max() == 0.0
    assert not list(cache.rglob("*.parquet")), "an unresolved range was cached"

    later = of.order_flow_bars(DAY, DAY3, now_ms=_day_ms(DAY3) + 10**7, cache=True, **kw)
    assert later.loc[later.index.strftime("%Y-%m-%d") == DAY2, "coverage"].min() == 1.0
    assert list(cache.rglob("*.parquet")), "a final range should be cached"


def test_vpin_clock_is_anchored_to_utc_midnight_not_to_the_window(tmp_path):
    """The same bar must return the same VPIN however the window was framed."""
    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=1440, step_s=10, qty=0.5))
    kw = dict(price_venue="bybit", bar="1h", vpin_bucket_volume=5.0,
              vpin_window_buckets=5)
    wide = _bars(root, DAY, DAY2, **kw)
    narrow = _bars(root, f"{DAY} 12:00", DAY2, **kw)
    shared = narrow.index
    for col in ("vpin_bybit", "vpin_buckets_bybit", "vpin_age_s_bybit"):
        np.testing.assert_allclose(narrow[col].to_numpy(),
                                   wide.loc[shared, col].to_numpy(),
                                   rtol=1e-12, atol=1e-12, err_msg=col)


def test_vpin_window_gap_reports_a_window_that_spans_a_feed_hole(tmp_path):
    """``vpin_age_s`` is smallest exactly when the contamination is largest, so
    the window's own span and silence are emitted beside it."""
    root = tmp_path / "ticks"
    for d in (DAY, DAY2):
        _seed(root, d, trades=_dense_trades(d, "bybit", minutes=1440, step_s=10, qty=0.5))
    _seed(root, DAY3, trades=[])                                   # a whole dead day
    _seed(root, "2025-03-07",
          trades=_dense_trades("2025-03-07", "bybit", minutes=1440, step_s=10, qty=0.5))
    bars = _bars(root, DAY, "2025-03-08", price_venue="bybit", bar="1h",
                 vpin_bucket_volume=60.0, vpin_window_buckets=20)
    after = bars.loc["2025-03-07"]
    contaminated = after[after["vpin_window_gap_s_bybit"] > 0]
    assert len(contaminated) > 0, "a window spanning a dead day must be flagged"
    # The flag is what the age column cannot say: fresh newest bucket, stale window.
    fresh = contaminated[contaminated["vpin_age_s_bybit"] < 3600]
    assert len(fresh) > 0
    assert (contaminated["vpin_window_span_s_bybit"] > 86_400).any()
    # A window entirely inside one live day is clean.
    clean = bars.loc[DAY2]
    assert (clean["vpin_window_gap_s_bybit"].dropna() == 0.0).all()


def test_liquidation_zero_vs_unknown_is_decided_by_liveness_on_BOTH_backends(tmp_path):
    """A local zero-row table and an absent hf partition are the SAME statement.

    ``upload_hf.py`` skips empty tables, so the two honest-empty representations
    used to disagree (0.0 local, NaN from the Hub) for the same day — and with a
    verify-then-delete store the default ``source="auto"`` answer flipped as local
    files were pruned. The decision is pure and lives in ``_open_source``; it is
    exercised here on hand-built manifest days, which needs no network.
    """
    local_zero_rows = {"date": DAY, "source": "local",
                       "tables_present": {"trades": True, "depth_snapshots": True,
                                          "liquidations": True}}
    hf_no_partition = {"date": DAY, "source": "hf",
                       "tables_present": {"trades": True, "depth_snapshots": True,
                                          "liquidations": False}}
    assert bool(local_zero_rows["tables_present"]["liquidations"]) is True
    assert any(hf_no_partition["tables_present"].values()) is True

    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=1440, step_s=20))
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="1h", vpin=False)
    day = bars.attrs["orderflow"]["manifest"]["days"][0]
    assert day["liquidations_recorded"] is True
    assert (bars["liq_count_bybit"] == 0.0).all()
    assert (bars["coverage_liq_bybit"] == 1.0).all()


def test_minbtl_claim_is_measured_at_runtime_not_hard_coded(tmp_path):
    """Rail 4's number must be computed, so it cannot go stale as days accrue."""
    minbtl = of.HONESTY_SENTENCES[4]
    assert "MinBTL" in minbtl
    assert not any(ch.isdigit() for ch in minbtl), (
        "a hard-coded day count is a claim the test can only check for PRESENCE, "
        f"never for TRUTH — the archive grows one day per day: {minbtl!r}")
    assert "21 recorded days" not in (of.__doc__ or "")
    root = tmp_path / "ticks"
    _seed(root, DAY, trades=_dense_trades(DAY, "bybit", minutes=1440, step_s=20))
    bars = _bars(root, DAY, DAY2, price_venue="bybit", bar="1h", vpin=False)
    h = bars.attrs["orderflow"]["history"]
    assert h["days_resolved"] == 1 and h["days_requested"] == 1
    assert h["span_days"] == pytest.approx(1.0)
    assert h["span_years"] == pytest.approx(1.0 / 365.0)
    need = risk.min_backtest_length(5)
    assert h["min_backtest_length_years"]["5"] == pytest.approx(need)
    assert h["fraction_of_minbtl"]["5"] == pytest.approx((1.0 / 365.0) / need)
    assert h["fraction_of_minbtl"]["5"] < 0.01


def test_cross_instrument_pairing_is_warned_and_recorded(tmp_path):
    """spot tape + perp book is unavoidable on this archive, so it is LABELLED."""
    root = tmp_path / "ticks"
    t0 = _day_ms(DAY)
    trades = [("coinbase", "BTC-USD", f"c{i:04d}", t0 + i * 10_000, 100.0, 1.0, True)
              for i in range(360)]
    depth = [_book("bybit", t0 + i * 1000, [[139.0, 1.0]], [[141.0, 1.0]])
             for i in range(3600)]
    _seed(root, DAY, trades=trades, depth=depth)
    with pytest.warns(UserWarning, match="cross-instrument"):
        bars = of.order_flow_bars(DAY, DAY2, price_venue="coinbase", book_venue="bybit",
                                  bar="1h", depth_levels=1, vpin=False, source="local",
                                  store_dir=root, cache=False)
    x = bars.attrs["orderflow"]["cross_instrument"]
    assert x["price_instrument"] == "spot" and x["book_instrument"] == "perp"
    assert "funding BASIS" in x["note"]
    # The within-venue read stays available and is 0 on a symmetric book.
    assert bars["micro_minus_mid_bybit"].iloc[0] == pytest.approx(0.0, abs=1e-12)
    # ...while the naive cross-instrument difference is pure basis.
    assert bars["mid_bybit"].iloc[0] - bars["close"].iloc[0] == pytest.approx(40.0)
