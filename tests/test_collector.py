"""test_collector.py — fixture-driven tests for the tick collector (DESIGN §3 + §3c).

Fully deterministic, **no network**. Every wire-shaped input is a REAL frame
captured live on 2026-07-03/05 (``scripts/fixtures_ws.json`` — see the provenance
note in that file and DESIGN-orderflow-terminal.md §2), so the normalizers are
tested against what the wire actually delivers, not remembered docs. The OKX and
Coinbase frames are the SAME ones the JS adapters were proven against.

What is asserted
----------------
1. **Aggressor/side conventions per §0.6** — Bybit ``publicTrade.S`` is the taker
   side used as-is; Bybit ``allLiquidation`` printed side ``Buy`` == a **short**
   was liquidated (the forced buy-back); Coinbase ``side`` is the MAKER side
   (inverted); Binance aggTrades ``m`` true -> SELL aggressor; OKX ``side`` is
   the taker used as-is.
2. **Bybit ``tickers`` partial-delta MERGE** — deltas omit unchanged fields; a
   merged row keeps the snapshot's mark when the delta only moved the index, and
   a delta arriving before any snapshot yields NO rows (never invent a mark).
3. **Book semantics** — Bybit ``orderbook.50`` (top-50 stored, §3c) and OKX
   ``books`` (ctVal-scaled, top-50) snapshot+delta, qty/sz ``"0"`` deletes.
4. **Binance shapes** — combined-endpoint ``{stream,data}`` unwrap for depth;
   REST ``premiumIndex``/``openInterest``/``aggTrades``/crowding endpoints map to
   exact schema tuples (crowding is LONG format; oi_hist -> coin AND usd rows).
5. **Deribit** — DVOL row; chain name parsing (DDMMMYY -> 08:00 UTC), iv stored
   as mark_iv/100 DECIMAL, unparseable names skipped + counted.
6. **Store contract** — schema create + insert/query roundtrip on a tmp DB; the
   500-row auto-flush; the 1/s downsampler gate.
7. **§3c rotation** — event-time day routing (incl. a batch straddling UTC
   midnight), the 5-minute grace window, closed == immutable (late rows dropped
   + counted), migrate-legacy count conservation.
8. **BYOD API** — /health, /v1/info, filters (symbol/start_ms/end_ms/limit),
   400 on bad params, 404 on unknown paths (legacy mode, contract UNCHANGED);
   rotation mode: cross-day-file reads, aggregated /v1/info row_counts, and the
   410 'archived' answer with the hf:// hint for pre-local ranges.
9. **§4f auction endpoints + levels registry** — /v1/profile aggregation vs a
   hand-written reference SQL (exact), POC/VAH/VAL parity with the JS
   ProfileStore constructed-levels fixture (check_terminal group 9: poc 150 /
   vah 200 / val 130), buckets_usd splits, two-day-file union, 400 on garbage;
   /v1/vwap vs the batch formula; the rotation hook's levels.jsonl append +
   idempotence; naked-POC derivation both ways; backfill_levels skip-existing
   (HF reader seams monkeypatched — network-free).
10. **Canonical symbol expansion (the 643d3be BYOD caveat, fixed)** —
    /v1/trades?symbol=BTCUSDT returns the okx ('BTC-USDT-SWAP') and coinbase
    ('BTC-USD') legs stored under their NATIVE ids (rows keep the stored id,
    §0.7); an explicit native-id query stays narrow (no silent aliasing); the
    expansion rides the shared SELECT (so /v1/funding widens identically) and
    reuses run()'s _symbol_legs derivation; §4f profile/vwap widen only the
    symbol filter while ``exchange`` still selects the leg; /v1/info
    advertises ``symbol_aliases``.

Collector deps are opt-in (requirements-collector.txt): this module skips
cleanly when duckdb is absent. The normalizers themselves need neither dep.
"""

from __future__ import annotations

import asyncio
import http.client
import importlib.util
import json
import math
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")  # opt-in dep — requirements-collector.txt

from btcquant import collector  # noqa: E402

# --------------------------------------------------------------------------- #
# Real captured frames (scripts/fixtures_ws.json, 2026-07-03) — no network.    #
# --------------------------------------------------------------------------- #
_FIXTURES = json.loads(
    (Path(__file__).resolve().parent.parent / "scripts" / "fixtures_ws.json").read_text()
)


def _copy(obj):
    """Deep copy via JSON round-trip so tests never mutate the shared fixtures."""
    return json.loads(json.dumps(obj))


# --------------------------------------------------------------------------- #
# 1. Bybit publicTrade — taker side used AS-IS (§0.6)                          #
# --------------------------------------------------------------------------- #
def test_normalize_bybit_trade_exact_row():
    """The captured Buy print maps to the exact trades-schema tuple."""
    rows = collector.normalize_bybit_trade(_FIXTURES["bybit_publicTrade"][0])
    assert rows == [
        (
            "bybit",
            "BTCUSDT",
            "ebc3fe68-97c4-5243-b076-09a6d33e5b53",  # UUID -> trade_id VARCHAR
            1783076486560,  # data[0].T (epoch ms), not the envelope ts
            61855.20,
            0.008,
            True,  # S == "Buy" is already the TAKER side — no inversion (§0.6)
        )
    ]


def test_normalize_bybit_trade_sell_aggressor_and_batch():
    """S == "Sell" -> aggressor_buy False; every captured frame yields its row."""
    frame = _copy(_FIXTURES["bybit_publicTrade"][1])
    frame["data"][0]["S"] = "Sell"  # fixture window was all buys; flip one copy
    rows = collector.normalize_bybit_trade(frame)
    assert rows[0][6] is False

    all_rows = [
        row
        for f in _FIXTURES["bybit_publicTrade"]
        for row in collector.normalize_bybit_trade(f)
    ]
    assert len(all_rows) == 3
    assert [r[3] for r in all_rows] == [1783076486560, 1783076486566, 1783076486575]
    assert all(r[6] is True for r in all_rows)


# --------------------------------------------------------------------------- #
# 2. Bybit allLiquidation — printed Buy == a SHORT was liquidated (§0.6)       #
# --------------------------------------------------------------------------- #
def test_normalize_bybit_liq_side_convention():
    """Printed side maps to the LIQUIDATED position, not the print.

    Real captured prints (BTC liqs are sparse — the capture window caught
    JUP/BEAT/1000PEPE prints, identical v5 envelope to ``publicTrade``).
    """
    frames = _FIXTURES["bybit_allLiquidation"]

    # Printed "Sell" is the exchange's forced SELL-OUT -> a LONG got liquidated.
    assert collector.normalize_bybit_liq(frames[0]) == [
        ("bybit", "JUPUSDT", 1783077117606, "long", 0.24695, 513.0, 0.24695 * 513.0)
    ]
    # Printed "Buy" is the exchange's forced BUY-BACK -> a SHORT got liquidated.
    assert collector.normalize_bybit_liq(frames[2]) == [
        ("bybit", "BEATUSDT", 1783077166060, "short", 2.71970, 1078.0, 2.71970 * 1078.0)
    ]
    # Every captured frame yields exactly one row; ts comes from data[0].T.
    all_rows = [r for f in frames for r in collector.normalize_bybit_liq(f)]
    assert len(all_rows) == 4
    assert {r[3] for r in all_rows} == {"long", "short"}

    assert collector.normalize_bybit_liq({"topic": "allLiquidation.BTCUSDT", "data": []}) == []


# --------------------------------------------------------------------------- #
# 3. Bybit tickers — partial deltas MUST merge against the last snapshot       #
# --------------------------------------------------------------------------- #
def test_normalize_bybit_ticker_snapshot_rows():
    """A full snapshot emits complete funding_mark + open_interest rows."""
    snap_frame = _FIXTURES["bybit_tickers_snapshot"][0]
    funding, oi, state = collector.normalize_bybit_ticker(snap_frame, None)
    assert funding == [
        ("bybit", "BTCUSDT", 1783076453510, 61850.90, 61875.77, 0.00008186, 1783094400000)
    ]
    assert oi == [("bybit", "BTCUSDT", 1783076453510, 57881.107)]
    assert state["markPrice"] == "61850.90"  # merged state carries the raw strings


def test_normalize_bybit_ticker_delta_merge():
    """THE delta case: changed fields update, omitted fields carry over."""
    _, _, state = collector.normalize_bybit_ticker(_FIXTURES["bybit_tickers_snapshot"][0], None)

    # Delta #2 moves indexPrice (61876.89) but omits markPrice/fundingRate/OI —
    # the merged row must keep the snapshot's mark and re-stamp at the delta ts.
    funding, oi, state = collector.normalize_bybit_ticker(
        _FIXTURES["bybit_tickers_delta"][1], state
    )
    assert funding == [
        ("bybit", "BTCUSDT", 1783076453710, 61850.90, 61876.89, 0.00008186, 1783094400000)
    ]
    assert oi == [("bybit", "BTCUSDT", 1783076453710, 57881.107)]

    # Delta #3 only moves bid1Size — funding fields all carry over unchanged.
    funding, oi, state = collector.normalize_bybit_ticker(
        _FIXTURES["bybit_tickers_delta"][2], state
    )
    assert funding[0][3:] == (61850.90, 61876.89, 0.00008186, 1783094400000)
    assert funding[0][2] == 1783076453810
    assert oi[0][3] == 57881.107


def test_normalize_bybit_ticker_delta_before_snapshot_emits_nothing():
    """A delta with no prior snapshot must NOT fabricate a funding/OI row."""
    funding, oi, state = collector.normalize_bybit_ticker(
        _FIXTURES["bybit_tickers_delta"][0], None
    )
    assert funding == [] and oi == []  # missing markPrice etc. -> no invented row
    assert state["bid1Size"] == "8.733"  # but the partial state is retained


def test_normalize_bybit_ticker_does_not_mutate_prior_state():
    """The merge returns a NEW dict; the caller's previous state stays intact."""
    _, _, state1 = collector.normalize_bybit_ticker(_FIXTURES["bybit_tickers_snapshot"][0], None)
    _, _, state2 = collector.normalize_bybit_ticker(_FIXTURES["bybit_tickers_delta"][1], state1)
    assert state1["indexPrice"] == "61875.77"  # untouched
    assert state2["indexPrice"] == "61876.89"
    assert state2 is not state1


# --------------------------------------------------------------------------- #
# 4. Bybit orderbook.50 — snapshot + deltas; qty "0" deletes; top-50 rows (§3c) #
# --------------------------------------------------------------------------- #
def test_bybit_book_snapshot_plus_deltas():
    book = collector.BybitBook()
    assert book.depth_row(1) is None  # never emit an empty book as an observation

    book.apply(_FIXTURES["bybit_orderbook_snapshot"][0])
    assert book.bids[61855.00] == 4.588  # best bid from the snapshot
    assert book.asks[61855.10] == 1.773  # best ask

    for delta in _FIXTURES["bybit_orderbook_delta"]:
        book.apply(delta)
    assert book.bids[61854.40] == 0.002  # inserted by delta 1
    assert 61844.80 not in book.bids  # qty "0" deleted by delta 1
    assert 61857.10 not in book.asks  # deleted by delta 3
    assert book.asks[61858.10] == 0.002  # inserted by delta 3
    assert 61863.50 not in book.asks  # added (delta 2) then deleted (delta 3)
    assert book.asks[61863.60] == 1.407  # deleted (delta 2) then re-added (delta 3)

    row = book.depth_row(1783076454366)
    assert row[:3] == ("bybit", "BTCUSDT", 1783076454366)
    bids, asks = json.loads(row[3]), json.loads(row[4])
    # §3c: the stream IS orderbook.50 — store all of it (was top-20 pre-v2).
    assert len(bids) == 50 and len(asks) == 50
    assert bids[0] == [61855.00, 4.588]  # best-first: bids descending
    assert asks[0] == [61855.10, 1.773]  # best-first: asks ascending
    assert bids == sorted(bids, key=lambda lvl: -lvl[0])
    assert asks == sorted(asks, key=lambda lvl: lvl[0])


# --------------------------------------------------------------------------- #
# 5. Binance shapes — combined-endpoint unwrap + REST poll payloads            #
# --------------------------------------------------------------------------- #
def test_normalize_binance_depth_unwraps_combined_frame():
    rows = collector.normalize_binance_depth(_FIXTURES["binancef_depth20"][0])
    assert len(rows) == 1
    ex, sym, ts, bids_json, asks_json = rows[0]
    assert (ex, sym) == ("binancef", "BTCUSDT")
    assert ts == 1783076545359  # data.T (transaction time), not envelope/event time
    bids, asks = json.loads(bids_json), json.loads(asks_json)
    assert len(bids) == 20 and len(asks) == 20  # each frame is a FULL 20-level book
    assert bids[0] == [61883.60, 7.026]
    assert asks[0] == [61883.70, 1.705]
    assert bids == sorted(bids, key=lambda lvl: -lvl[0])  # delivered best-first
    assert asks == sorted(asks, key=lambda lvl: lvl[0])

    # A bare (unwrapped) payload is accepted too — same row out.
    assert collector.normalize_binance_depth(_FIXTURES["binancef_depth20"][0]["data"]) == rows


def test_normalize_binance_premium_index_exact_row():
    rows = collector.normalize_binance_premium_index(_FIXTURES["binancef_rest_premiumIndex"])
    assert rows == [
        (
            "binancef",
            "BTCUSDT",
            1783076807011,
            61980.00034783,
            62003.48413043,
            0.0001,  # lastFundingRate "0.00010000" — raw decimal, NOT annualized
            1783094400000,
        )
    ]


def test_normalize_binance_open_interest_exact_row():
    rows = collector.normalize_binance_open_interest(_FIXTURES["binancef_rest_openInterest"])
    assert rows == [("binancef", "BTCUSDT", 1783076802518, 107936.535)]


# --------------------------------------------------------------------------- #
# 6. Downsampler — at most one row per second per key; gaps stay gaps          #
# --------------------------------------------------------------------------- #
def test_downsampler_one_per_second_gate():
    down = collector.Downsampler(interval_ms=1000)
    assert down.ready("k", 10_000) is True
    assert down.ready("k", 10_999) is False  # < 1 s later -> dropped
    assert down.ready("k", 11_000) is True  # exactly 1 s -> stored
    assert down.ready("other", 10_500) is True  # keys are independent
    # After a 5 s stall the NEXT frame passes — but nothing is invented for the hole.
    assert down.ready("k", 16_000) is True


# --------------------------------------------------------------------------- #
# 7. Store: schema create + insert/query roundtrip; 500-row auto-flush         #
# --------------------------------------------------------------------------- #
def test_schema_roundtrip(tmp_path):
    con = collector.open_db(tmp_path / "t.duckdb")
    try:
        tables = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        assert {
            "trades",
            "liquidations",
            "depth_snapshots",
            "funding_mark",
            "open_interest",
            "crowding",  # §3c new tables
            "dvol",
            "options_chain",
        } <= tables
        # (symbol, ts_ms) index per table (DESIGN §3); ts-only for the two
        # Deribit single-instrument tables (§3c — they carry no symbol column).
        idx = {r[0] for r in con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}
        assert {
            "idx_trades_symbol_ts",
            "idx_liquidations_symbol_ts",
            "idx_depth_symbol_ts",
            "idx_funding_symbol_ts",
            "idx_oi_symbol_ts",
            "idx_crowding_symbol_ts",
            "idx_dvol_ts",
            "idx_options_chain_ts",
        } <= idx

        lock = threading.Lock()
        writer = collector.BatchWriter(con, lock)
        trade_rows = collector.normalize_bybit_trade(_FIXTURES["bybit_publicTrade"][0])
        funding_rows = collector.normalize_binance_premium_index(
            _FIXTURES["binancef_rest_premiumIndex"]
        )
        oi_rows = collector.normalize_binance_open_interest(
            _FIXTURES["binancef_rest_openInterest"]
        )
        depth_rows = collector.normalize_binance_depth(_FIXTURES["binancef_depth20"][0])
        liq_rows = [("bybit", "BTCUSDT", 1783076500000, "short", 61000.0, 0.5, 30500.0)]
        crowding_rows = collector.normalize_binance_taker_ls(
            _FIXTURES["binancef_rest_taker_ls"], "BTCUSDT"
        )
        dvol_rows = collector.normalize_deribit_dvol(_FIXTURES["deribit_rest_dvol"])
        chain_rows, _ = collector.normalize_deribit_chain(_FIXTURES["deribit_rest_book_summary"])

        writer.add("trades", trade_rows)
        writer.add("funding_mark", funding_rows)
        writer.add("open_interest", oi_rows)
        writer.add("depth_snapshots", depth_rows)
        writer.add("liquidations", liq_rows)
        writer.add("crowding", crowding_rows)
        writer.add("dvol", dvol_rows)
        writer.add("options_chain", chain_rows)
        # Below both flush triggers -> rows are still only buffered.
        assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
        assert writer.flush() == 5 + len(crowding_rows) + len(dvol_rows) + len(chain_rows)

        assert con.execute("SELECT * FROM trades").fetchall() == trade_rows
        assert con.execute("SELECT * FROM funding_mark").fetchall() == funding_rows
        assert con.execute("SELECT * FROM open_interest").fetchall() == oi_rows
        assert con.execute("SELECT * FROM depth_snapshots").fetchall() == depth_rows
        assert con.execute("SELECT * FROM liquidations").fetchall() == liq_rows
        assert con.execute("SELECT * FROM crowding").fetchall() == crowding_rows
        assert con.execute("SELECT * FROM dvol").fetchall() == dvol_rows
        assert con.execute("SELECT * FROM options_chain").fetchall() == chain_rows
        assert writer.flush() == 0  # nothing pending after a flush
    finally:
        con.close()


def test_batchwriter_autoflushes_at_max_rows(tmp_path):
    """The 500-row half of the "500 ms or 500 rows" contract fires inside add()."""
    con = collector.open_db(tmp_path / "t.duckdb")
    try:
        writer = collector.BatchWriter(con, threading.Lock())
        row = collector.normalize_bybit_trade(_FIXTURES["bybit_publicTrade"][0])[0]
        writer.add("trades", [row] * collector.FLUSH_MAX_ROWS)
        # No explicit flush() call — the threshold already wrote everything out.
        assert (
            con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            == collector.FLUSH_MAX_ROWS
        )
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# 8. BYOD API — real ThreadingHTTPServer on port 0, queried via http.client    #
# --------------------------------------------------------------------------- #
def _get_json(port: int, path: str) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def test_api_endpoints_against_seeded_db(tmp_path):
    con = collector.open_db(tmp_path / "t.duckdb")
    lock = threading.Lock()
    writer = collector.BatchWriter(con, lock)
    for frame in _FIXTURES["bybit_publicTrade"]:  # ts 1783076486560/566/575
        writer.add("trades", collector.normalize_bybit_trade(frame))
    writer.add(
        "funding_mark",
        collector.normalize_binance_premium_index(_FIXTURES["binancef_rest_premiumIndex"]),
    )
    writer.flush()

    info = {
        "symbol": "BTCUSDT",
        "exchanges": ["bybit", "binancef"],
        "db": str(tmp_path / "t.duckdb"),
        "started_ms": 0,
        "retention_days": None,
    }
    server = collector.make_api_server(con, lock, info, port=0)  # 0 -> ephemeral port
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get_json(port, "/health")
        assert status == 200 and body["ok"] is True and body["ts_ms"] > 0

        status, body = _get_json(port, "/v1/info")
        assert status == 200
        assert body["symbol"] == "BTCUSDT"
        assert body["retention_days"] is None  # keep-all default, stated
        assert body["row_counts"]["trades"] == 3
        assert body["row_counts"]["funding_mark"] == 1

        # limit + ordering: ascending ts_ms, first two of three trades.
        status, body = _get_json(port, "/v1/trades?symbol=BTCUSDT&limit=2")
        assert status == 200 and body["n"] == 2
        assert [r["ts_ms"] for r in body["rows"]] == [1783076486560, 1783076486566]
        first = body["rows"][0]
        assert first["exchange"] == "bybit"
        assert first["trade_id"] == "ebc3fe68-97c4-5243-b076-09a6d33e5b53"
        assert first["price"] == 61855.20 and first["qty"] == 0.008
        assert first["aggressor_buy"] is True

        # start_ms/end_ms are inclusive bounds — the window picks the middle trade.
        status, body = _get_json(
            port, "/v1/trades?start_ms=1783076486561&end_ms=1783076486570"
        )
        assert status == 200 and body["n"] == 1
        assert body["rows"][0]["ts_ms"] == 1783076486566

        # funding row exposes the quoted "index" column faithfully.
        status, body = _get_json(port, "/v1/funding?symbol=BTCUSDT")
        assert status == 200 and body["n"] == 1
        frow = body["rows"][0]
        assert frow["mark"] == 61980.00034783
        assert frow["index"] == 62003.48413043
        assert frow["funding_rate"] == 0.0001
        assert frow["next_funding_ts"] == 1783094400000

        # unknown symbol -> empty result, not an error (an honest zero).
        status, body = _get_json(port, "/v1/trades?symbol=NOPE")
        assert status == 200 and body["n"] == 0

        # empty tables answer honestly too.
        status, body = _get_json(port, "/v1/liquidations")
        assert status == 200 and body["n"] == 0
        status, body = _get_json(port, "/v1/oi")
        assert status == 200 and body["n"] == 0
        status, body = _get_json(port, "/v1/depth")
        assert status == 200 and body["n"] == 0

        # bad params -> 400; unknown route -> 404 with the route list.
        status, body = _get_json(port, "/v1/trades?limit=abc")
        assert status == 400 and "bad parameter" in body["error"]
        status, body = _get_json(port, "/v1/nope")
        assert status == 404 and "/v1/trades" in body["routes"]
    finally:
        server.shutdown()
        server.server_close()
        con.close()


# --------------------------------------------------------------------------- #
# 9. Dependency guard — actionable hint at RUN time, import stays safe         #
# --------------------------------------------------------------------------- #
def test_run_raises_actionable_hint_when_deps_missing(monkeypatch):
    """run() must fail fast with the install command, before touching any file."""
    monkeypatch.setattr(collector, "websockets", None)
    with pytest.raises(RuntimeError, match="requirements-collector.txt"):
        collector.run(symbol="BTCUSDT", db="never_created.duckdb")


def test_run_rejects_unknown_exchange_codes():
    """Unknown codes fail loudly; v2 accepts all five §3c venues by name."""
    with pytest.raises(ValueError, match="kraken"):
        collector.run(exchanges=("kraken",), db="never_created.duckdb")
    with pytest.raises(ValueError, match="retention_days"):
        collector.run(retention_days=0, db="never_created.duckdb")
    # Rotation + retention is refused: closed-day pruning is the HF lifecycle's
    # verify-then-delete job (§3c), never an in-place DELETE on immutable files.
    with pytest.raises(ValueError, match="rotation"):
        collector.run(retention_days=30, db="never_created_dir")


# --------------------------------------------------------------------------- #
# 10. OKX trades + books (§3c) — CONTRACTS × ctVal(0.01) -> coin; taker as-is  #
# --------------------------------------------------------------------------- #
def test_normalize_okx_trade_ctval_and_taker_side():
    """The §3c pin: sz 200 CONTRACTS -> 2.00 BTC, taker side used AS-IS (§0.6)."""
    rows = collector.normalize_okx_trade(_FIXTURES["okx_trades"][0])
    assert rows == [
        ("okx", "BTC-USDT-SWAP", "2760725237", 1783079412162, 62010.0, 2.0, True)
    ]
    # side 'sell' -> aggressor_buy False; 4.49 contracts -> 0.0449 BTC.
    rows = collector.normalize_okx_trade(_FIXTURES["okx_trades"][1])
    assert rows == [
        ("okx", "BTC-USDT-SWAP", "2760725239", 1783079412468, 62009.9, 0.0449, False)
    ]


def test_okx_book_snapshot_updates_and_ctval_scaling():
    book = collector.OkxBook()
    assert book.depth_row(1) is None  # never emit an empty book as an observation

    ts = book.apply(_FIXTURES["okx_books_snapshot"][0])
    assert ts == 1783079411709  # row ts, not arrival time
    assert book.bids[62009.9] == 8.8358  # 883.58 CONTRACTS × 0.01 -> BTC
    assert book.asks[62010.0] == pytest.approx(1.9013)  # 190.13 × 0.01

    for update in _FIXTURES["okx_books_update"]:
        ts = book.apply(update)
    assert ts == 1783079412009  # ts advances with each applied update frame
    assert 62009.2 not in book.bids  # sz "0" tombstone deleted the level
    assert 62008.7 not in book.bids  # deleted by update 1
    assert book.asks[62010.0] == pytest.approx(3.356)  # upserted by the last update

    row = book.depth_row(ts)
    assert row[:3] == ("okx", "BTC-USDT-SWAP", 1783079412009)
    bids, asks = json.loads(row[3]), json.loads(row[4])
    assert len(asks) == 50  # top-50 storage (§3c) — the whole point of the leg
    # Best bid survives; its qty was UPSERTED by the last update (614.58 × 0.01).
    assert bids[0][0] == 62009.9 and bids[0][1] == pytest.approx(6.1458)
    assert bids == sorted(bids, key=lambda lvl: -lvl[0])
    assert asks == sorted(asks, key=lambda lvl: lvl[0])


# --------------------------------------------------------------------------- #
# 11. OKX REST funding / OI (§3c) — 60 s polls; oi = oiCcy COIN                #
# --------------------------------------------------------------------------- #
def test_normalize_okx_funding_exact_row_and_null_mark():
    """fundingTime is the UPCOMING settlement; mark/index are honest NULLs."""
    rows = collector.normalize_okx_funding(_FIXTURES["okx_rest_funding"])
    assert rows == [
        (
            "okx",
            "BTC-USDT-SWAP",
            1783119985285,
            None,  # this endpoint has no mark price — NULL, never invented (§0.7)
            None,
            float("0.0000387369202921"),
            1783123200000,  # fundingTime (upcoming settlement), NOT nextFundingTime
        )
    ]
    assert collector.normalize_okx_funding({"code": "1", "data": []}) == []  # error code


def test_normalize_okx_oi_uses_coin_not_contracts():
    rows = collector.normalize_okx_oi(_FIXTURES["okx_rest_oi"])
    # oiCcy (COIN) — the raw `oi` field is CONTRACTS and would overstate 100x.
    assert rows == [("okx", "BTC-USDT-SWAP", 1783120004270, float("31337.2794000001118"))]
    assert rows[0][3] != float("3133727.94000001118")  # NOT the contracts field
    assert collector.normalize_okx_oi(None) == []


# --------------------------------------------------------------------------- #
# 12. Coinbase market_trades (§3c) — MAKER-side inversion + seed/dedup rules   #
# --------------------------------------------------------------------------- #
def test_normalize_coinbase_trades_inversion_and_ordering():
    """§0.6 gotcha: side is the MAKER's — side=SELL means an aggressive BUYER."""
    trades = _FIXTURES["coinbase_market_trades_snapshot"][0]["events"][0]["trades"]
    rows = collector.normalize_coinbase_trades(trades)
    assert len(rows) == 100
    ids = [int(r[2]) for r in rows]
    assert ids == sorted(ids)  # wire is NEWEST-first; rows come out oldest-first
    by_id = {r[2]: r for r in rows}
    # side=BUY (maker bought — a resting bid was hit) -> aggressor_buy False.
    assert by_id["1049465696"] == (
        "coinbase", "BTC-USD", "1049465696", 1783076454619, 61805.6, 3e-08, False
    )
    # side=SELL (a resting ask was lifted) -> aggressor_buy True.
    assert by_id["1049465694"][6] is True


def test_coinbase_tape_seed_once_and_dedupe():
    """Port of the proven JS rules: first snapshot seeds, later ones are skipped,
    updates before the seed are ignored, ids never repeat across batches."""
    tape = collector.CoinbaseTape()
    # An update BEFORE any snapshot is ignored (wait for the seed — JS rule).
    assert tape.apply(_FIXTURES["coinbase_market_trades_update"][0]) == []

    rows = tape.apply(_FIXTURES["coinbase_market_trades_snapshot"][0])
    assert len(rows) == 100 and tape.seeded is True
    # A reconnect re-fires the snapshot: skipped — never re-dump the batch.
    assert tape.apply(_FIXTURES["coinbase_market_trades_snapshot"][0]) == []

    upd = tape.apply(_FIXTURES["coinbase_market_trades_update"][0])
    assert [r[2] for r in upd] == ["1049465697"]
    assert upd[0][6] is False  # side=BUY -> maker bought -> SELL aggressor
    # Replaying the same update: deduped by monotonic trade_id.
    assert tape.apply(_FIXTURES["coinbase_market_trades_update"][0]) == []
    # Heartbeats are liveness, not rows.
    assert tape.apply(_FIXTURES["coinbase_heartbeats"][0]) == []


# --------------------------------------------------------------------------- #
# 13. Binance aggTrades REST (§3c) — m-flag inversion + gapless fromId cursor  #
# --------------------------------------------------------------------------- #
def test_normalize_binance_aggtrades_m_flag_and_cursor():
    payload = _FIXTURES["binancef_rest_aggtrades"]
    rows, next_from_id = collector.normalize_binance_aggtrades(payload, "BTCUSDT")
    assert rows == [
        # m=false: buyer was the TAKER -> aggressor_buy True.
        ("binancef", "BTCUSDT", "3371157745", 1783235902382, 62746.70, 0.026, True),
        # m=true: buyer was the MAKER -> the aggressor SOLD (§0.6).
        ("binancef", "BTCUSDT", "3371157746", 1783235902536, 62746.60, 0.002, False),
        ("binancef", "BTCUSDT", "3371157747", 1783235902803, 62746.70, 0.012, True),
    ]
    # Cursor arithmetic: next fromId = last `a` + 1 (gapless by aggTradeId).
    assert next_from_id == 3371157747 + 1
    # An empty poll advances NOTHING — the caller keeps its cursor (never skip ahead).
    assert collector.normalize_binance_aggtrades([], "BTCUSDT") == ([], None)


def test_aggtrades_loop_dedupes_overlap_logs_gap_keeps_cursor(monkeypatch):
    """_aggtrades_loop honesty guards (§3c/§0.6/§0.7), driven with canned polls:

    * a failed poll keeps the cursor — the next fetch retries the SAME fromId;
    * re-served ids (retried seed / misbehaving server) are DEDUPED and the
      dedupe is logged — duplicate prints would silently inflate CVD/volume;
    * ids resuming ahead of the cursor are LOGGED as a gap, never papered over.
    """

    def _agg(a, ts):
        return {"a": a, "p": "60000.0", "q": "0.01", "T": ts, "m": False}

    responses = [
        [_agg(100, 1_000), _agg(101, 2_000)],  # seed -> cursor 102
        RuntimeError("net down"),  # failure: cursor must NOT advance
        [_agg(101, 2_000), _agg(102, 3_000)],  # id 101 re-served -> dedupe, keep 102
        [_agg(110, 9_000)],  # 103..109 never arrived -> honest GAP log
    ]
    calls: list = []
    stop = asyncio.Event()

    def fake_fetch(symbol, from_id):
        calls.append(from_id)
        if not responses:
            stop.set()  # canned polls drained — end the loop
            raise RuntimeError("drained")
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def fast_sleep(event, seconds):  # noqa: ARG001 — signature parity
        await asyncio.sleep(0)  # no real 5 s waits in a unit test

    monkeypatch.setattr(collector, "_fetch_binance_aggtrades", fake_fetch)
    monkeypatch.setattr(collector, "_sleep_or_stop", fast_sleep)

    class _Writer:
        def __init__(self):
            self.rows: list[tuple] = []

        def add(self, table, rows):
            assert table == "trades"
            self.rows.extend(rows)

    writer = _Writer()
    logs: list[str] = []
    asyncio.run(collector._aggtrades_loop("BTCUSDT", writer, stop, log=logs.append))

    # Each aggTradeId written exactly ONCE, in id order — 101 was not re-printed.
    assert [r[2] for r in writer.rows] == ["100", "101", "102", "110"]
    # The failed poll retried the SAME fromId (102) — never skipped ahead.
    assert calls[:4] == [None, 102, 102, 103]
    assert any("GAP" in ln and "7 aggTrade id(s) missing" in ln for ln in logs)
    assert any("deduped 1 re-served row(s)" in ln for ln in logs)


# --------------------------------------------------------------------------- #
# 14. Crowding endpoints (§3c) — long format, the five pinned metric names     #
# --------------------------------------------------------------------------- #
def test_normalize_crowding_long_format_five_metrics():
    taker = collector.normalize_binance_taker_ls(_FIXTURES["binancef_rest_taker_ls"], "BTCUSDT")
    assert taker[0] == ("binancef", "BTCUSDT", 1783235100000, "taker_buy_sell_ratio", 1.6465)
    assert taker[1][4] == 1.7040

    top = collector.normalize_binance_top_pos_ls(_FIXTURES["binancef_rest_top_pos_ls"], "BTCUSDT")
    assert top[0] == ("binancef", "BTCUSDT", 1783235400000, "top_position_ls_ratio", 1.2438)

    glob = collector.normalize_binance_global_ls(_FIXTURES["binancef_rest_global_ls"], "BTCUSDT")
    assert glob[0] == ("binancef", "BTCUSDT", 1783235400000, "global_account_ls_ratio", 1.4752)

    # oi_hist produces BOTH rows per entry — coin AND usd (§3c: deriving one
    # from the other needs a price we did not observe at that ts).
    oi = collector.normalize_binance_oi_hist(_FIXTURES["binancef_rest_oi_hist"][:1], "BTCUSDT")
    assert oi == [
        ("binancef", "BTCUSDT", 1783235400000, "oi_sum_coin", 105698.762),
        ("binancef", "BTCUSDT", 1783235400000, "oi_sum_usd", 6631445198.9942),
    ]

    metrics = {r[3] for r in taker + top + glob + oi}
    assert metrics == {  # exactly the five §3c metric names
        "taker_buy_sell_ratio",
        "top_position_ls_ratio",
        "global_account_ls_ratio",
        "oi_sum_coin",
        "oi_sum_usd",
    }


# --------------------------------------------------------------------------- #
# 15. Deribit (§3c) — DVOL number; chain name-parse + iv DECIMAL rail          #
# --------------------------------------------------------------------------- #
def test_normalize_deribit_dvol_exact_row():
    rows = collector.normalize_deribit_dvol(_FIXTURES["deribit_rest_dvol"])
    # usIn is MICROseconds -> ms; DVOL stays in vol points (38.68 == 38.68% ann).
    assert rows == [(1783186480233, 38.68)]
    assert collector.normalize_deribit_dvol({"error": {"code": 1}}) == []


def test_parse_deribit_option_name_conventions():
    # DDMMMYY -> 08:00 UTC expiry (Deribit European cash settlement).
    assert collector.parse_deribit_option_name("BTC-28AUG26-105000-C") == (
        1787904000000,  # 2026-08-28T08:00:00Z
        105000.0,
        "C",
    )
    # Single-digit days parse too (JS-parity: 'BTC-6JUL26-54000-P').
    assert collector.parse_deribit_option_name("BTC-6JUL26-54000-P") == (
        1783324800000,  # 2026-07-06T08:00:00Z
        54000.0,
        "P",
    )
    # Futures / spot / garbage fall out as None — counted by the caller.
    assert collector.parse_deribit_option_name("BTC-25SEP26") is None
    assert collector.parse_deribit_option_name("BTC_USDC") is None
    assert collector.parse_deribit_option_name("BTC-28XXX26-1000-C") is None


def test_normalize_deribit_chain_exact_row_and_skip_count():
    rows, skipped = collector.normalize_deribit_chain(_FIXTURES["deribit_rest_book_summary"])
    assert skipped == 0 and len(rows) == 10
    ts = _FIXTURES["deribit_rest_book_summary"]["usIn"] // 1000  # one snapshot ts
    by_name = {r[1]: r for r in rows}
    # The §3c pin: mark_iv 48.58 PERCENT -> 0.4858 DECIMAL (the /100 rail).
    assert by_name["BTC-28AUG26-105000-C"] == (
        ts, "BTC-28AUG26-105000-C", 1787904000000, 105000.0, "C",
        0.4858, 161.3, 0.0, 0.00026511, 63358.41,
    )

    # An unparseable name is skipped AND counted — never silently dropped (§0).
    doctored = _copy(_FIXTURES["deribit_rest_book_summary"])
    doctored["result"].append({"instrument_name": "BTC-25SEP26", "mark_iv": 50.0})
    rows2, skipped2 = collector.normalize_deribit_chain(doctored)
    assert skipped2 == 1 and len(rows2) == 10


# --------------------------------------------------------------------------- #
# 16. §3c rotation — event-time day routing, grace window, closed == immutable #
# --------------------------------------------------------------------------- #
_MIDNIGHT = 1783209600000  # 2026-07-05T00:00:00Z — a UTC day boundary
_D_TODAY = "2026-07-05"
_D_YDAY = "2026-07-04"


def _trade_at(ts_ms: int) -> tuple:
    return ("bybit", "BTCUSDT", f"t-{ts_ms}", ts_ms, 61000.0, 0.001, True)


def _count(path, table="trades") -> int:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


def test_utc_day_is_event_time_pure():
    assert collector.utc_day(_MIDNIGHT - 1) == _D_YDAY
    assert collector.utc_day(_MIDNIGHT) == _D_TODAY
    assert collector.utc_day(0) == "1970-01-01"


def test_rotation_routes_batch_straddling_midnight(tmp_path):
    """One add() with rows on both sides of UTC midnight -> two day files."""
    root = tmp_path / "ticks"
    manager = collector.DayFileManager(root)
    clock = {"now": _MIDNIGHT + 60_000}  # 00:01 — inside the 5-min grace window
    writer = collector.RotatingWriter(
        manager, threading.Lock(), now_ms=lambda: clock["now"], log=lambda *_: None
    )

    writer.add(
        "trades",
        [_trade_at(_MIDNIGHT - 5_000), _trade_at(_MIDNIGHT + 5_000), _trade_at(_MIDNIGHT - 1)],
    )
    assert writer.flush() == 3
    assert manager.open_days() == [_D_YDAY, _D_TODAY]  # yesterday held open (grace)
    assert writer.rows_written["trades"] == 3

    manager.close_all()
    # Event-time routing: each row sits in the file of ITS OWN ts_ms day.
    assert _count(root / f"{_D_YDAY}.duckdb") == 2
    assert _count(root / f"{_D_TODAY}.duckdb") == 1


def test_rotation_grace_window_close_and_immutability(tmp_path):
    """Yesterday: writable in grace -> final-flushed + closed after -> immutable."""
    root = tmp_path / "ticks"
    manager = collector.DayFileManager(root)
    clock = {"now": _MIDNIGHT + 60_000}
    logs: list[str] = []
    writer = collector.RotatingWriter(
        manager, threading.Lock(), now_ms=lambda: clock["now"], log=logs.append
    )

    writer.add("trades", [_trade_at(_MIDNIGHT - 5_000)])  # yesterday, inside grace
    writer.flush()
    assert manager.open_days() == [_D_YDAY]

    # Grace lapses. A row buffered before the close still makes the FINAL flush,
    # after which the day is sealed (flush-then-close, §3c).
    clock["now"] = _MIDNIGHT + collector.GRACE_WINDOW_MS + 1_000
    writer.add("trades", [_trade_at(_MIDNIGHT - 4_000)])
    writer.flush()
    assert manager.open_days() == []  # yesterday closed
    assert any("closed" in line for line in logs)
    assert _count(root / f"{_D_YDAY}.duckdb") == 2

    # Closed == immutable: a straggler for yesterday is DROPPED and counted.
    writer.add("trades", [_trade_at(_MIDNIGHT - 3_000), _trade_at(_MIDNIGHT + 5_000)])
    writer.flush()
    assert writer.rows_dropped_closed == 1
    assert any("DROPPED" in line for line in logs)
    assert _count(root / f"{_D_YDAY}.duckdb") == 2  # unchanged — that is the point
    manager.close_all()
    assert _count(root / f"{_D_TODAY}.duckdb") == 1  # today's row still landed


def test_migrate_legacy_count_conservation(tmp_path):
    """--migrate-legacy: split by event day, counts conserved, original untouched."""
    legacy = tmp_path / "ticks.duckdb"
    con = collector.open_db(legacy)
    trades = [
        _trade_at(_MIDNIGHT - 10_000),  # 2026-07-04
        _trade_at(_MIDNIGHT - 5_000),  # 2026-07-04
        _trade_at(_MIDNIGHT + 5_000),  # 2026-07-05
    ]
    # A second table proves the split walks EVERY table, not just trades.
    funding = [
        ("okx", "BTC-USDT-SWAP", _MIDNIGHT - 7_000, None, None, 3.9e-05, _MIDNIGHT + 3_600_000)
    ]
    con.executemany(collector._INSERT_SQL["trades"], trades)
    con.executemany(collector._INSERT_SQL["funding_mark"], funding)
    con.close()

    logs: list[str] = []
    dest = tmp_path / "ticks"
    per_day = collector.migrate_legacy(legacy, dest, log=logs.append)

    assert per_day[_D_YDAY]["trades"] == 2 and per_day[_D_TODAY]["trades"] == 1
    assert per_day[_D_YDAY]["funding_mark"] == 1
    assert _count(dest / f"{_D_YDAY}.duckdb") == 2
    assert _count(dest / f"{_D_TODAY}.duckdb") == 1
    assert _count(dest / f"{_D_YDAY}.duckdb", "funding_mark") == 1
    # Never auto-delete: the original is intact and the rm hint was printed.
    assert legacy.exists() and _count(legacy) == 3
    assert any("rm '" in line for line in logs)
    # One-shot: a re-run would double every row — refused on existing day files.
    with pytest.raises(ValueError, match="one-shot"):
        collector.migrate_legacy(legacy, dest)
    # And a .duckdb destination is a usage error, not a mangled store.
    with pytest.raises(ValueError, match="DIRECTORY"):
        collector.migrate_legacy(legacy, tmp_path / "other.duckdb")


# --------------------------------------------------------------------------- #
# 17. BYOD API in rotation mode (§3c) — same contract, day-file union, 410     #
# --------------------------------------------------------------------------- #
def test_api_rotation_aggregates_and_answers_410(tmp_path):
    root = tmp_path / "ticks"
    # Two synthetic CLOSED day files, seeded through the canonical schema.
    for day, ts in ((_D_YDAY, _MIDNIGHT - 5_000), (_D_TODAY, _MIDNIGHT + 5_000)):
        con = collector.open_db(root / f"{day}.duckdb")
        con.executemany(collector._INSERT_SQL["trades"], [_trade_at(ts)])
        con.close()

    manager = collector.DayFileManager(root)
    lock = threading.Lock()
    info = {"symbol": "BTCUSDT", "exchanges": ["bybit"], "db": str(root), "started_ms": 0}
    server = collector.make_api_server(manager, lock, info, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # /v1/info aggregates row_counts ACROSS the local day files.
        status, body = _get_json(port, "/v1/info")
        assert status == 200
        assert body["mode"] == "rotation"
        assert body["days"] == [_D_YDAY, _D_TODAY]
        assert body["row_counts"]["trades"] == 2
        assert body["row_counts"]["liquidations"] == 0

        # A range spanning midnight unions both day files, ts-ascending.
        status, body = _get_json(
            port,
            f"/v1/trades?start_ms={_MIDNIGHT - 10_000}&end_ms={_MIDNIGHT + 10_000}",
        )
        assert status == 200 and body["n"] == 2
        assert [r["ts_ms"] for r in body["rows"]] == [_MIDNIGHT - 5_000, _MIDNIGHT + 5_000]

        # limit still bounds the union (contract UNCHANGED vs legacy mode).
        status, body = _get_json(
            port, f"/v1/trades?start_ms={_MIDNIGHT - 10_000}&limit=1"
        )
        assert status == 200 and body["n"] == 1
        assert body["rows"][0]["ts_ms"] == _MIDNIGHT - 5_000

        # A range starting BEFORE the oldest local day -> 410 + the HF hint
        # (that data was uploaded and pruned; an empty 200 would be a lie).
        old_ms = _MIDNIGHT - 30 * 86_400_000  # 2026-06-05
        status, body = _get_json(port, f"/v1/trades?start_ms={old_ms}")
        assert status == 410
        assert body["error"] == "archived"
        assert body["hint"] == (
            "hf://datasets/azulcoder/btc-quant-ticks/data/date=2026-06-05/trades.parquet"
        )

        # Health + 404 behave exactly as in legacy mode.
        status, body = _get_json(port, "/health")
        assert status == 200 and body["ok"] is True
        status, body = _get_json(port, "/v1/nope")
        assert status == 404
        assert "/v1/profile" in body["routes"]  # §4f routes are advertised
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# 18. §4f /v1/profile — hand-SQL exactness, ProfileStore VA parity, buckets,   #
#     two-day-file union, 400 on garbage                                       #
# --------------------------------------------------------------------------- #
def _mk_trade(ts_ms, price, qty, buy=True, symbol="BTCUSDT", exchange="bybit"):
    """Synthetic trades-schema tuple (never derived from data/ticks/)."""
    return (exchange, symbol, f"{exchange}-{ts_ms}-{price}", int(ts_ms),
            float(price), float(qty), bool(buy))


def _seed_day(root: Path, day: str, rows: list[tuple]) -> None:
    """Write synthetic trades into a CLOSED day file under a tmp rotation root."""
    con = collector.open_db(root / f"{day}.duckdb")
    con.executemany(collector._INSERT_SQL["trades"], rows)
    con.close()


@contextmanager
def _rotation_server(root: Path):
    """Rotation-mode BYOD server over a tmp root; yields the ephemeral port."""
    manager = collector.DayFileManager(root)
    lock = threading.Lock()
    info = {"symbol": "BTCUSDT", "exchanges": ["bybit"], "db": str(root), "started_ms": 0}
    server = collector.make_api_server(manager, lock, info, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def test_profile_matches_hand_sql_exactly(tmp_path):
    """/v1/profile levels == an independent hand-written reference aggregation
    (round(price/tick)*tick grid, buy/sell/prints per level), run directly on
    the same synthetic day file; vwap/sigma == the batch formulas."""
    root = tmp_path / "ticks"
    t0 = _MIDNIGHT + 3_600_000  # 01:00 UTC today — one synthetic day file
    trades = [
        _mk_trade(t0 + 0, 61853.7, 0.010, True),
        _mk_trade(t0 + 1, 61856.1, 0.020, False),
        _mk_trade(t0 + 2, 61847.3, 0.500, True),
        _mk_trade(t0 + 3, 61844.9, 0.125, False),
        _mk_trade(t0 + 4, 61862.0, 0.075, True),
        _mk_trade(t0 + 5, 61858.4, 0.300, False),
    ]
    decoys = [
        _mk_trade(t0 + 6, 61850.0, 9.9, True, symbol="ETHUSDT"),  # other symbol
        _mk_trade(t0 + 7, 61850.0, 9.9, True, exchange="okx"),  # other exchange
        _mk_trade(t0 + 11, 61850.0, 9.9, True),  # outside [start_ms, end_ms]
    ]
    _seed_day(root, _D_TODAY, trades + decoys)

    # Reference aggregation, written independently of the endpoint's SQL.
    ref_con = duckdb.connect(str(root / f"{_D_TODAY}.duckdb"), read_only=True)
    try:
        expected_levels = ref_con.execute(
            "SELECT round(price / 10) * 10 AS lvl, "
            "SUM(CASE WHEN aggressor_buy THEN qty ELSE 0 END) AS buy_vol, "
            "SUM(CASE WHEN NOT aggressor_buy THEN qty ELSE 0 END) AS sell_vol, "
            "COUNT(*) AS prints FROM trades "
            "WHERE exchange = 'bybit' AND symbol = 'BTCUSDT' "
            "AND ts_ms >= ? AND ts_ms <= ? GROUP BY 1 ORDER BY 1",
            [t0, t0 + 10],
        ).fetchall()
    finally:
        ref_con.close()
    assert len(expected_levels) >= 2  # the fixture really exercises >1 level

    with _rotation_server(root) as port:
        status, body = _get_json(
            port,
            f"/v1/profile?symbol=BTCUSDT&exchange=bybit&start_ms={t0}"
            f"&end_ms={t0 + 10}&tick=10",
        )
    assert status == 200
    assert body["levels"] == [
        {"lvl": lvl, "buy_vol": b, "sell_vol": s, "prints": p}
        for lvl, b, s, p in expected_levels
    ]

    # vwap/sigma/total_vol vs the batch formulas over the in-range trades.
    q = sum(t[5] for t in trades)
    vwap = sum(t[4] * t[5] for t in trades) / q
    sigma = math.sqrt(sum(t[5] * (t[4] - vwap) ** 2 for t in trades) / q)
    assert body["total_vol"] == pytest.approx(q, abs=1e-12)
    assert body["vwap"] == pytest.approx(vwap, abs=1e-9)
    assert body["sigma"] == pytest.approx(sigma, abs=1e-9)


def test_profile_value_area_parity_with_profilestore_fixture(tmp_path):
    """VA parity pin (§4f 'one convention: mirror ProfileStore's'): the
    constructed-levels case from scripts/check_terminal.cjs group 9 — 101
    one-unit levels at 100..200 plus one extra unit at 150 — must produce the
    SAME poc/vah/val the JS ProfileStore produces (verified by running the JS:
    poc 150, vah 200, val 130; ties expand upward, so the up side is absorbed
    to 200 first, then 20 down-levels reach the 71.4 target)."""
    root = tmp_path / "ticks"
    t0 = _MIDNIGHT + 1_000
    rows = [_mk_trade(t0 + p, float(p), 1.0, True) for p in range(100, 201)]
    rows.append(_mk_trade(t0 + 999, 150.0, 1.0, False))
    _seed_day(root, _D_TODAY, rows)

    with _rotation_server(root) as port:
        status, body = _get_json(
            port,
            f"/v1/profile?symbol=BTCUSDT&exchange=bybit&start_ms={t0}"
            f"&end_ms={t0 + 2_000}&tick=1",
        )
    assert status == 200
    assert body["poc"] == 150.0
    assert body["vah"] == 200.0
    assert body["val"] == 130.0
    assert body["total_vol"] == 102.0
    assert len(body["levels"]) == 101
    # Same ±5pp band check_terminal asserts on the JS side.
    va_vol = sum(
        lv["buy_vol"] + lv["sell_vol"]
        for lv in body["levels"]
        if body["val"] <= lv["lvl"] <= body["vah"]
    )
    assert 0.65 <= va_vol / body["total_vol"] <= 0.75


def test_profile_buckets_usd_split(tmp_path):
    """buckets_usd=1000,10000 -> b0 (notional <= 1000, boundary INCLUSIVE:
    'smallest threshold >= price*qty'), b1 (<= 10000), b2 (overflow last);
    per level the buckets partition the volume exactly."""
    root = tmp_path / "ticks"
    t0 = _MIDNIGHT + 1_000
    _seed_day(root, _D_TODAY, [
        _mk_trade(t0 + 0, 100.0, 5.0, True),  # $500 -> b0
        _mk_trade(t0 + 1, 100.0, 10.0, False),  # $1000 exactly -> b0 (inclusive)
        _mk_trade(t0 + 2, 100.0, 50.0, True),  # $5000 -> b1
        _mk_trade(t0 + 3, 100.0, 200.0, False),  # $20000 -> b2 overflow
        _mk_trade(t0 + 4, 200.0, 2.0, True),  # second level, $400 -> b0
    ])
    with _rotation_server(root) as port:
        status, body = _get_json(
            port,
            f"/v1/profile?symbol=BTCUSDT&exchange=bybit&start_ms={t0}"
            f"&end_ms={t0 + 10}&tick=10&buckets_usd=1000,10000",
        )
    assert status == 200
    by_lvl = {lv["lvl"]: lv for lv in body["levels"]}
    assert by_lvl[100.0]["b0"] == 15.0
    assert by_lvl[100.0]["b1"] == 50.0
    assert by_lvl[100.0]["b2"] == 200.0
    assert by_lvl[200.0]["b0"] == 2.0 and by_lvl[200.0]["b1"] == 0.0
    for lv in body["levels"]:  # buckets PARTITION the level's volume
        assert lv["b0"] + lv["b1"] + lv["b2"] == pytest.approx(
            lv["buy_vol"] + lv["sell_vol"], abs=1e-12
        )


def test_profile_unions_two_day_files_and_answers_410(tmp_path):
    """A range spanning UTC midnight merges levels ACROSS day files (sums are
    exact — disjoint trade sets); a range predating the oldest local day gets
    the same honest 410 the row endpoints give."""
    root = tmp_path / "ticks"
    _seed_day(root, _D_YDAY, [
        _mk_trade(_MIDNIGHT - 5_000, 61000.0, 1.0, True),
        _mk_trade(_MIDNIGHT - 4_000, 61010.0, 0.5, False),
    ])
    _seed_day(root, _D_TODAY, [_mk_trade(_MIDNIGHT + 5_000, 61000.0, 2.0, True)])

    with _rotation_server(root) as port:
        status, body = _get_json(
            port,
            f"/v1/profile?symbol=BTCUSDT&exchange=bybit"
            f"&start_ms={_MIDNIGHT - 10_000}&end_ms={_MIDNIGHT + 10_000}&tick=10",
        )
        assert status == 200
        by_lvl = {lv["lvl"]: lv for lv in body["levels"]}
        assert by_lvl[61000.0]["buy_vol"] == 3.0  # 1.0 (yday) + 2.0 (today)
        assert by_lvl[61000.0]["prints"] == 2
        assert by_lvl[61010.0]["sell_vol"] == 0.5
        assert body["total_vol"] == 3.5
        assert body["poc"] == 61000.0

        old_ms = _MIDNIGHT - 30 * 86_400_000
        status, body = _get_json(
            port,
            f"/v1/profile?symbol=BTCUSDT&start_ms={old_ms}"
            f"&end_ms={_MIDNIGHT}&tick=10",
        )
        assert status == 410 and body["error"] == "archived"


def test_profile_and_vwap_reject_garbage_params(tmp_path):
    """§4f: params validated -> 400 on garbage (tick<=0/NaN, end<start,
    missing symbol/range/anchor, unparseable buckets)."""
    root = tmp_path / "ticks"
    _seed_day(root, _D_TODAY, [_mk_trade(_MIDNIGHT + 1_000, 61000.0, 1.0, True)])
    bad = [
        f"/v1/profile?symbol=B&start_ms={_MIDNIGHT}&end_ms={_MIDNIGHT + 1}&tick=0",
        f"/v1/profile?symbol=B&start_ms={_MIDNIGHT}&end_ms={_MIDNIGHT + 1}&tick=-5",
        f"/v1/profile?symbol=B&start_ms={_MIDNIGHT}&end_ms={_MIDNIGHT + 1}&tick=abc",
        f"/v1/profile?symbol=B&start_ms={_MIDNIGHT}&end_ms={_MIDNIGHT + 1}&tick=nan",
        f"/v1/profile?symbol=B&start_ms={_MIDNIGHT + 9}&end_ms={_MIDNIGHT}&tick=10",
        "/v1/profile?symbol=B&tick=10",  # missing range
        f"/v1/profile?start_ms={_MIDNIGHT}&end_ms={_MIDNIGHT + 1}",  # missing symbol
        f"/v1/profile?symbol=B&start_ms={_MIDNIGHT}&end_ms={_MIDNIGHT + 1}"
        "&buckets_usd=abc",
        f"/v1/profile?symbol=B&start_ms={_MIDNIGHT}&end_ms={_MIDNIGHT + 1}"
        "&buckets_usd=-5,100",
        "/v1/vwap?symbol=B",  # missing anchor_ms
        f"/v1/vwap?anchor_ms={_MIDNIGHT}",  # missing symbol
        f"/v1/vwap?symbol=B&anchor_ms={_MIDNIGHT + 9}&end_ms={_MIDNIGHT}",
        "/v1/vwap?symbol=B&anchor_ms=abc",
    ]
    with _rotation_server(root) as port:
        for path in bad:
            status, body = _get_json(port, path)
            assert status == 400, f"{path} -> {status} {body}"
            assert "bad parameter" in body["error"]


# --------------------------------------------------------------------------- #
# 19. §4f /v1/vwap — anchored VWAP ± σ vs the batch formula                    #
# --------------------------------------------------------------------------- #
def test_vwap_anchored_matches_batch_formula(tmp_path):
    """Anchored window (spanning two day files) == the batch Σp·q/Σq and
    volume-weighted σ; end_ms omitted extends through the newest local trade;
    an empty window answers honest nulls + n=0."""
    root = tmp_path / "ticks"
    yday = [
        _mk_trade(_MIDNIGHT - 9_000, 61840.0, 0.4, True),  # BEFORE the anchor
        _mk_trade(_MIDNIGHT - 5_000, 61850.0, 1.0, True),
        _mk_trade(_MIDNIGHT - 4_000, 61870.0, 0.25, False),
    ]
    today = [
        _mk_trade(_MIDNIGHT + 5_000, 61900.0, 0.5, True),
        _mk_trade(_MIDNIGHT + 9_000, 61820.0, 2.0, False),  # AFTER end_ms
    ]
    _seed_day(root, _D_YDAY, yday)
    _seed_day(root, _D_TODAY, today)

    anchor, end = _MIDNIGHT - 6_000, _MIDNIGHT + 6_000
    in_win = [t for t in yday + today if anchor <= t[3] <= end]
    q = sum(t[5] for t in in_win)
    vwap = sum(t[4] * t[5] for t in in_win) / q
    sigma = math.sqrt(sum(t[5] * (t[4] - vwap) ** 2 for t in in_win) / q)

    with _rotation_server(root) as port:
        status, body = _get_json(
            port, f"/v1/vwap?symbol=BTCUSDT&exchange=bybit&anchor_ms={anchor}&end_ms={end}"
        )
        assert status == 200
        assert body["n"] == len(in_win) == 3
        assert body["vwap"] == pytest.approx(vwap, abs=1e-9)
        assert body["sigma"] == pytest.approx(sigma, abs=1e-9)

        # end_ms omitted -> through the newest LOCAL trade (all 4 from anchor).
        status, body = _get_json(
            port, f"/v1/vwap?symbol=BTCUSDT&anchor_ms={anchor}"
        )
        assert status == 200 and body["n"] == 4
        all_from = [t for t in yday + today if t[3] >= anchor]
        q4 = sum(t[5] for t in all_from)
        assert body["vwap"] == pytest.approx(sum(t[4] * t[5] for t in all_from) / q4, abs=1e-9)

        # Empty window: nulls + n=0, never a fabricated number.
        status, body = _get_json(
            port, f"/v1/vwap?symbol=BTCUSDT&anchor_ms={_MIDNIGHT + 86_000_000}"
        )
        assert status == 200
        assert body == {"vwap": None, "sigma": None, "n": 0}


# --------------------------------------------------------------------------- #
# 20. §4f levels registry — rotation hook append + idempotence, naked serve    #
# --------------------------------------------------------------------------- #
def test_rotation_hook_records_day_levels_row(tmp_path):
    """When the grace window lapses and the manager closes yesterday, ONE
    levels.jsonl row appears — o/h/l/c by event time, poc/vah/val on the fixed
    $10 grid (bybit leg only; the okx decoy must not leak in)."""
    root = tmp_path / "ticks"
    manager = collector.DayFileManager(root)
    clock = {"now": _MIDNIGHT + 60_000}  # 00:01 — inside grace
    logs: list[str] = []
    writer = collector.RotatingWriter(
        manager, threading.Lock(), now_ms=lambda: clock["now"], log=logs.append
    )
    y = _MIDNIGHT - 3_600_000  # 23:00 yesterday
    writer.add("trades", [
        _mk_trade(y + 0, 61843.0, 2.0, True),  # open; lvl 61840
        _mk_trade(y + 1, 61851.0, 3.0, True),  # lvl 61850 -> POC
        _mk_trade(y + 2, 61862.0, 1.0, False),  # high; lvl 61860
        _mk_trade(y + 3, 61838.0, 0.5, False),  # low + close; lvl 61840
        _mk_trade(y + 4, 99999.0, 9.0, True, exchange="okx"),  # NOT the bybit leg
    ])
    writer.flush()
    assert not manager.levels_path().exists()  # nothing closed yet — no row

    clock["now"] = _MIDNIGHT + collector.GRACE_WINDOW_MS + 1_000
    writer.flush()  # grace lapsed -> close fires the §4f hook
    rows = collector.read_levels_registry(manager.levels_path())
    # Hand-computed: levels 61840 (2.5), 61850 (3 -> POC), 61860 (1); target
    # 0.7*6.5 = 4.55; expansion absorbs the HEAVIER neighbor (61840, 2.5) ->
    # covered 5.5 >= 4.55 -> vah 61850, val 61840. o/h/l/c/vol bybit-only.
    assert rows == [{
        "date": _D_YDAY, "o": 61843.0, "h": 61862.0, "l": 61838.0, "c": 61838.0,
        "poc": 61850.0, "vah": 61850.0, "val": 61840.0, "vol": 6.5,
    }]
    assert any("levels registry +=" in line for line in logs)
    # 'naked' is DERIVED at serve time — never stored in the file.
    assert "naked" not in manager.levels_path().read_text()
    manager.close_all()

    # Idempotence: re-appending the same date is a no-op (rotation hook and
    # backfill can both see a date — one recorded day, one row, forever).
    assert collector.append_levels_row(manager.levels_path(), rows[0]) is False
    assert len(collector.read_levels_registry(manager.levels_path())) == 1


def test_rotation_hook_skips_already_registered_day(tmp_path):
    """A pre-seeded registry row for the closing day survives untouched — the
    hook must not duplicate or overwrite it (idempotent by date)."""
    root = tmp_path / "ticks"
    root.mkdir()
    pre = {"date": _D_YDAY, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5,
           "poc": 1.0, "vah": 2.0, "val": 0.5, "vol": 42.0}
    (root / "levels.jsonl").write_text(json.dumps(pre) + "\n")

    manager = collector.DayFileManager(root)
    clock = {"now": _MIDNIGHT + 60_000}
    writer = collector.RotatingWriter(
        manager, threading.Lock(), now_ms=lambda: clock["now"], log=lambda *_: None
    )
    writer.add("trades", [_mk_trade(_MIDNIGHT - 5_000, 61000.0, 1.0, True)])
    writer.flush()
    clock["now"] = _MIDNIGHT + collector.GRACE_WINDOW_MS + 1_000
    writer.flush()  # closes yesterday; hook sees the date already recorded
    assert collector.read_levels_registry(root / "levels.jsonl") == [pre]
    manager.close_all()


def test_compute_day_levels_none_without_bybit_trades(tmp_path):
    """No bybit trades that day -> None, never an invented flat row (§0.7)."""
    con = collector.open_db(tmp_path / "d.duckdb")
    try:
        con.executemany(
            collector._INSERT_SQL["trades"],
            [_mk_trade(_MIDNIGHT + 1_000, 100.0, 1.0, True, exchange="okx")],
        )
        assert collector.compute_day_levels(con, _D_TODAY) is None
    finally:
        con.close()


def test_levels_endpoint_derives_naked_both_ways(tmp_path):
    """/v1/levels: naked iff NO LATER recorded day's [l, h] contains the POC —
    a revisited POC un-nakes (day1), an untouched one stays naked (day2), the
    newest day is vacuously naked (day3). File order is deliberately shuffled
    to prove the serve sorts by date."""
    root = tmp_path / "ticks"
    root.mkdir()
    d1 = {"date": "2026-07-01", "o": 61200.0, "h": 62000.0, "l": 61000.0,
          "c": 61900.0, "poc": 61500.0, "vah": 61900.0, "val": 61200.0, "vol": 10.0}
    d2 = {"date": "2026-07-02", "o": 61900.0, "h": 62500.0, "l": 61600.0,
          "c": 62400.0, "poc": 62000.0, "vah": 62400.0, "val": 61800.0, "vol": 12.0}
    d3 = {"date": "2026-07-03", "o": 61750.0, "h": 61800.0, "l": 61400.0,
          "c": 61500.0, "poc": 61700.0, "vah": 61780.0, "val": 61450.0, "vol": 8.0}

    with _rotation_server(root) as port:
        # Registry file absent -> an honest empty registry, not an error.
        status, body = _get_json(port, "/v1/levels")
        assert status == 200 and body == {"n": 0, "days": []}

        (root / "levels.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in (d3, d1, d2))  # shuffled on disk
        )
        status, body = _get_json(port, "/v1/levels")
    assert status == 200 and body["n"] == 3
    assert [r["date"] for r in body["days"]] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    naked = {r["date"]: r["naked"] for r in body["days"]}
    # day1 POC 61500 sits inside day3's [61400, 61800] -> revisited, NOT naked
    # (day2's [61600, 62500] misses it — only day3 un-nakes it).
    assert naked["2026-07-01"] is False
    # day2 POC 62000 is above day3's high -> never revisited -> naked.
    assert naked["2026-07-02"] is True
    # newest day: no later day exists -> vacuously naked.
    assert naked["2026-07-03"] is True
    # Row payload = stored fields + derived naked, nothing dropped.
    assert body["days"][0] == {**d1, "naked": False}


# --------------------------------------------------------------------------- #
# 21. §4f backfill_levels.py — HF seams monkeypatched; skip-existing;          #
#     chronological registry; idempotent second run; NO network, NO day files  #
# --------------------------------------------------------------------------- #
_REPO_DIR = Path(__file__).resolve().parent.parent
_bfl_spec = importlib.util.spec_from_file_location(
    "backfill_levels", _REPO_DIR / "scripts" / "backfill_levels.py"
)
bfl = importlib.util.module_from_spec(_bfl_spec)
_bfl_spec.loader.exec_module(bfl)


def _lv(date, poc):
    return {"date": date, "o": poc, "h": poc + 100, "l": poc - 100, "c": poc,
            "poc": poc, "vah": poc + 50, "val": poc - 50, "vol": 1.0}


def test_backfill_levels_skips_existing_and_is_idempotent(tmp_path, monkeypatch):
    reg = tmp_path / "levels.jsonl"
    existing = _lv("2026-07-02", 61500.0)
    reg.write_text(json.dumps(existing) + "\n")

    hf_rows = {
        "2026-07-01": _lv("2026-07-01", 61000.0),
        "2026-07-02": _lv("2026-07-02", 99999.0),  # must NOT replace the local row
        "2026-07-03": _lv("2026-07-03", 62000.0),
        "2026-07-04": None,  # archived day without bybit trades -> no row
    }
    computed: list[str] = []

    def fake_dates(con, repo):  # noqa: ARG001 — seam signature parity
        return sorted(hf_rows)

    def fake_day(con, repo, date):  # noqa: ARG001
        computed.append(date)
        return hf_rows[date]

    monkeypatch.setattr(bfl, "hf_dates", fake_dates)
    monkeypatch.setattr(bfl, "hf_day_levels", fake_day)

    rc = bfl.main(["--registry", str(reg), "--repo", "azulcoder/btc-quant-ticks"])
    assert rc == bfl.EXIT_OK
    # Skip-existing: 2026-07-02 was never recomputed (it is in the registry).
    assert computed == ["2026-07-01", "2026-07-03", "2026-07-04"]
    rows = collector.read_levels_registry(reg)
    assert [r["date"] for r in rows] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert rows[1] == existing  # the local row won, not the HF recompute
    # On-disk order is chronological even though 07-01 was backfilled AFTER
    # 07-02 existed (atomic sorted rewrite).
    on_disk = [json.loads(ln)["date"] for ln in reg.read_text().splitlines()]
    assert on_disk == ["2026-07-01", "2026-07-02", "2026-07-03"]
    # No day files were created or touched anywhere (hf:// reads only).
    assert list(tmp_path.glob("**/*.duckdb")) == []

    # Second run: every recorded date is skipped; only the row-less day is
    # honestly re-checked (it never became 'present'); registry byte-identical.
    before = reg.read_text()
    computed.clear()
    rc = bfl.main(["--registry", str(reg), "--repo", "azulcoder/btc-quant-ticks"])
    assert rc == bfl.EXIT_OK
    assert computed == ["2026-07-04"]
    assert reg.read_text() == before


def test_backfill_levels_rejects_malformed_repo_id(tmp_path):
    rc = bfl.main(["--registry", str(tmp_path / "l.jsonl"), "--repo", "not a repo id"])
    assert rc == bfl.EXIT_USAGE


# --------------------------------------------------------------------------- #
# 22. Canonical symbol expansion (the 643d3be BYOD caveat, fixed) — one        #
#     canonical query, every venue leg; native ids stay narrow                 #
# --------------------------------------------------------------------------- #
def test_expand_symbol_shares_run_derivation():
    """_expand_symbol IS _symbol_legs' derivation (one mapping, shared — the
    recorder and the replay API can never disagree): the canonical id widens
    to exactly run()'s okx/coinbase leg ids; a '-' id is an explicit NATIVE
    request and passes through alone; the deribit leg is absent because its
    tables carry no symbol column (§3c schema)."""
    legs = collector._symbol_legs("BTCUSDT")
    assert collector._expand_symbol("BTCUSDT") == ["BTCUSDT", legs["okx"], legs["coinbase"]]
    assert collector._expand_symbol("BTCUSDT") == ["BTCUSDT", "BTC-USDT-SWAP", "BTC-USD"]
    assert collector._expand_symbol("ETHUSDT") == ["ETHUSDT", "ETH-USDT-SWAP", "ETH-USD"]
    # Explicit native ids: verbatim, alone — no silent aliasing.
    assert collector._expand_symbol("BTC-USDT-SWAP") == ["BTC-USDT-SWAP"]
    assert collector._expand_symbol("BTC-USD") == ["BTC-USD"]
    assert legs["deribit"] not in collector._expand_symbol("BTCUSDT")


def test_api_canonical_symbol_returns_all_venue_rows(tmp_path):
    """/v1/trades?symbol=BTCUSDT (the CANONICAL id) returns the okx and
    coinbase legs too — stored under their NATIVE ids, which the pre-fix
    ``symbol = ?`` filter silently dropped from BYOD replay. A NATIVE-id
    query stays narrow; rows keep their STORED symbol (§0.7 — never rewritten
    to the canonical); the expansion rides the shared bounded SELECT, so
    /v1/funding widens identically; /v1/info advertises ``symbol_aliases``."""
    root = tmp_path / "ticks"
    t0 = _MIDNIGHT + 1_000
    con = collector.open_db(root / f"{_D_TODAY}.duckdb")
    con.executemany(collector._INSERT_SQL["trades"], [
        _mk_trade(t0 + 0, 61850.0, 0.5, True),  # bybit — native == canonical
        _mk_trade(t0 + 1, 61851.0, 0.25, False, exchange="binancef"),
        _mk_trade(t0 + 2, 61852.0, 2.0, True, symbol="BTC-USDT-SWAP", exchange="okx"),
        _mk_trade(t0 + 3, 61853.0, 0.1, False, symbol="BTC-USD", exchange="coinbase"),
        _mk_trade(t0 + 4, 61854.0, 9.9, True, symbol="ETHUSDT"),  # other family
    ])
    con.executemany(collector._INSERT_SQL["funding_mark"], [
        ("okx", "BTC-USDT-SWAP", t0 + 5, None, None, 3.9e-05, t0 + 3_600_000),
    ])
    con.close()

    with _rotation_server(root) as port:
        # Canonical id -> ALL venue legs, ts-ascending, native symbols KEPT
        # (the ETHUSDT decoy is another canonical family — excluded).
        status, body = _get_json(
            port, f"/v1/trades?symbol=BTCUSDT&start_ms={t0}&end_ms={t0 + 10}"
        )
        assert status == 200 and body["n"] == 4
        assert [(r["exchange"], r["symbol"]) for r in body["rows"]] == [
            ("bybit", "BTCUSDT"),
            ("binancef", "BTCUSDT"),
            ("okx", "BTC-USDT-SWAP"),
            ("coinbase", "BTC-USD"),
        ]

        # Explicit NATIVE ids stay narrow — no silent aliasing of a query
        # that named one venue's tape.
        status, body = _get_json(port, "/v1/trades?symbol=BTC-USDT-SWAP")
        assert status == 200 and body["n"] == 1
        assert body["rows"][0]["exchange"] == "okx"
        status, body = _get_json(port, "/v1/trades?symbol=BTC-USD")
        assert status == 200 and body["n"] == 1
        assert body["rows"][0]["exchange"] == "coinbase"

        # The expansion rides the SHARED bounded SELECT -> /v1/funding (and
        # every other row route) widens identically.
        status, body = _get_json(port, "/v1/funding?symbol=BTCUSDT")
        assert status == 200 and body["n"] == 1
        assert body["rows"][0]["symbol"] == "BTC-USDT-SWAP"  # stored id kept

        # /v1/info advertises exactly the expansion the filters apply.
        status, body = _get_json(port, "/v1/info")
        assert status == 200
        assert body["symbol_aliases"] == {
            "BTCUSDT": ["BTCUSDT", "BTC-USDT-SWAP", "BTC-USD"]
        }


def test_profile_and_vwap_canonical_symbol_selects_native_leg(tmp_path):
    """§4f endpoints under the expansion: ``exchange`` still selects ONE leg;
    the canonical symbol only widens which STORED id that leg is found under —
    exchange=okx&symbol=BTCUSDT aggregates the rows stored as 'BTC-USDT-SWAP',
    the default bybit leg is untouched, and venues are never blended."""
    root = tmp_path / "ticks"
    t0 = _MIDNIGHT + 1_000
    okx = [
        _mk_trade(t0 + 0, 100.0, 1.0, True, symbol="BTC-USDT-SWAP", exchange="okx"),
        _mk_trade(t0 + 1, 200.0, 3.0, False, symbol="BTC-USDT-SWAP", exchange="okx"),
    ]
    bybit = [_mk_trade(t0 + 2, 300.0, 0.5, True)]
    _seed_day(root, _D_TODAY, okx + bybit)

    q = sum(t[5] for t in okx)
    vwap = sum(t[4] * t[5] for t in okx) / q
    sigma = math.sqrt(sum(t[5] * (t[4] - vwap) ** 2 for t in okx) / q)

    with _rotation_server(root) as port:
        # /v1/profile — the okx leg found under its native id; both passes
        # (levels scan + the two-pass sigma) filter by the SAME expanded set.
        status, body = _get_json(
            port,
            f"/v1/profile?symbol=BTCUSDT&exchange=okx&start_ms={t0}"
            f"&end_ms={t0 + 10}&tick=10",
        )
        assert status == 200
        assert {lv["lvl"]: lv["buy_vol"] + lv["sell_vol"] for lv in body["levels"]} == {
            100.0: 1.0, 200.0: 3.0,
        }
        assert body["total_vol"] == q
        assert body["vwap"] == pytest.approx(vwap, abs=1e-9)
        assert body["sigma"] == pytest.approx(sigma, abs=1e-9)

        # /v1/vwap — same leg selection through the expansion.
        status, body = _get_json(
            port,
            f"/v1/vwap?symbol=BTCUSDT&exchange=okx&anchor_ms={t0}&end_ms={t0 + 10}",
        )
        assert status == 200 and body["n"] == 2
        assert body["vwap"] == pytest.approx(vwap, abs=1e-9)
        assert body["sigma"] == pytest.approx(sigma, abs=1e-9)

        # Default exchange (bybit) still answers ONLY the bybit tape — the
        # symbol expansion widens ids, never the venue selection.
        status, body = _get_json(
            port, f"/v1/vwap?symbol=BTCUSDT&anchor_ms={t0}&end_ms={t0 + 10}"
        )
        assert status == 200 and body["n"] == 1
        assert body["vwap"] == 300.0

        # NATIVE symbol on the WRONG leg -> an honest empty (narrow filter).
        status, body = _get_json(
            port, f"/v1/vwap?symbol=BTC-USDT-SWAP&exchange=bybit&anchor_ms={t0}"
        )
        assert status == 200 and body == {"vwap": None, "sigma": None, "n": 0}
