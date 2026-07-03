"""test_collector.py — fixture-driven tests for the O-0 tick collector (DESIGN §3).

Fully deterministic, **no network**. Every wire-shaped input is a REAL frame
captured live on 2026-07-03 (``scripts/fixtures_ws.json`` — see the provenance
note in that file and DESIGN-orderflow-terminal.md §2), so the normalizers are
tested against what the wire actually delivers, not remembered docs.

What is asserted
----------------
1. **Aggressor/side conventions per §0.6** — Bybit ``publicTrade.S`` is the taker
   side used as-is; Bybit ``allLiquidation`` printed side ``Buy`` == a **short**
   was liquidated (the forced buy-back).
2. **Bybit ``tickers`` partial-delta MERGE** — deltas omit unchanged fields; a
   merged row keeps the snapshot's mark when the delta only moved the index, and
   a delta arriving before any snapshot yields NO rows (never invent a mark).
3. **Bybit ``orderbook.50`` snapshot+delta semantics** — qty ``"0"`` deletes a
   level; depth rows are top-20, best-first.
4. **Binance shapes** — combined-endpoint ``{stream,data}`` unwrap for depth;
   REST ``premiumIndex``/``openInterest`` map to exact schema tuples.
5. **Store contract** — schema create + insert/query roundtrip on a tmp DB; the
   500-row auto-flush; the 1/s downsampler gate.
6. **BYOD API** — /health, /v1/info, filters (symbol/start_ms/end_ms/limit),
   400 on bad params, 404 on unknown paths — via http.client against a real
   ThreadingHTTPServer on an ephemeral port.

Collector deps are opt-in (requirements-collector.txt): this module skips
cleanly when duckdb is absent. The normalizers themselves need neither dep.
"""

from __future__ import annotations

import http.client
import json
import threading
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
# 4. Bybit orderbook.50 — snapshot + deltas; qty "0" deletes; top-20 rows      #
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
    assert len(bids) == 20 and len(asks) == 20  # top-20 storage (DESIGN §3)
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
        } <= tables
        # (symbol, ts_ms) index per table (DESIGN §3)
        idx = {r[0] for r in con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}
        assert {
            "idx_trades_symbol_ts",
            "idx_liquidations_symbol_ts",
            "idx_depth_symbol_ts",
            "idx_funding_symbol_ts",
            "idx_oi_symbol_ts",
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

        writer.add("trades", trade_rows)
        writer.add("funding_mark", funding_rows)
        writer.add("open_interest", oi_rows)
        writer.add("depth_snapshots", depth_rows)
        writer.add("liquidations", liq_rows)
        # Below both flush triggers -> rows are still only buffered.
        assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
        assert writer.flush() == 5

        assert con.execute("SELECT * FROM trades").fetchall() == trade_rows
        assert con.execute("SELECT * FROM funding_mark").fetchall() == funding_rows
        assert con.execute("SELECT * FROM open_interest").fetchall() == oi_rows
        assert con.execute("SELECT * FROM depth_snapshots").fetchall() == depth_rows
        assert con.execute("SELECT * FROM liquidations").fetchall() == liq_rows
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
    """coinbase is terminal-only for now — a loud refusal beats a silent no-op."""
    with pytest.raises(ValueError, match="coinbase"):
        collector.run(exchanges=("coinbase",), db="never_created.duckdb")
    with pytest.raises(ValueError, match="retention_days"):
        collector.run(retention_days=0, db="never_created.duckdb")
