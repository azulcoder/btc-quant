"""collector.py — O-0 tick collector daemon (DESIGN-orderflow-terminal.md §3).

Keyless, research-only accumulation of BTC perp microstructure into a local DuckDB
file (``data/ticks.duckdb``, gitignored). **No API keys, no authenticated endpoints,
no orders** — every stream below is a public WS/REST feed, verified live from this
machine on 2026-07-03 (frames captured to ``scripts/fixtures_ws.json``; the
normalizers here are written against those *actual wire shapes*, not remembered docs).

Honesty rails (DESIGN §0, binding)
----------------------------------
* Running this collector changes the tick-data families (CVD, liquidations, OI,
  funding accrual) from *un-ingested* to **time-gated**, NOT to *validated* (§0.3).
  Nothing recorded here may enter the OOS harness until accumulated history clears
  MinBTL for the intended trial count AND a pre-registered hypothesis passes the
  DEVELOPMENT.md §6 greenlight. Until then this file buys *optionality*, not signals.
* **Gaps stay gaps** (§3 resilience). A reconnect, a stalled stream, or a downed
  process leaves a hole in ``ts_ms`` — we never interpolate, backfill from a second
  source into the same series, or otherwise fabricate rows (§0.7).
* **Aggressor/side conventions are per-exchange and normalized explicitly** (§0.6):
  Bybit ``publicTrade.S`` is already the *taker* side (used as-is); Bybit
  ``allLiquidation.S == "Buy"`` means a **short** position was liquidated (the
  printed order is the forced buy-back). Each normalizer documents its convention.
* **Empirical stream reality** (§0.2): Binance Futures WS topic-filters this network
  — only ``depth20@100ms`` flows; trades/mark on the same socket deliver sub-acks and
  nothing else. So Bybit v5 is the primary WS feed and Binance contributes depth WS
  plus REST polls (``premiumIndex`` 5 s, ``openInterest`` 60 s). We collect what the
  wire actually delivers. Binance futures *trades* are NOT collected — documented,
  not proxied. The Coinbase spot tape leg (DESIGN §3) is terminal-only for now and
  deliberately not wired here yet; asking for it raises instead of silently no-oping.

Dependencies (opt-in, like MLflow/DVC — requirements-collector.txt)
-------------------------------------------------------------------
``duckdb`` and ``websockets`` are imported behind guards so that *importing this
module never fails* (pytest collection, ``import btcquant``, the normalizer-only
paths). Only actually *running* the daemon (or opening a DB) raises a clear
``RuntimeError`` with the install hint. ``requests`` is a core dependency already
(btcquant/data.py) and is used for the Binance REST polls via ``asyncio.to_thread``
so the event loop never blocks on HTTP.

Storage contract (DESIGN §3 schema — all timestamps epoch **ms**, UTC)
----------------------------------------------------------------------
Single writer process, batched inserts (flush every 500 ms or 500 rows, whichever
first), graceful final flush on SIGINT. Keep-all retention by default — the whole
point is accumulating research history; ``retention_days`` opts into a daily DELETE.
Honest sizing note (§3): BTC perp trades ≈ 0.5–1.5 M rows/day → order-of ~0.5–1
GB/month; depth@1s adds ~0.1 GB/month. Disk is the user's budget — documented, not
hidden. The optional BYOD HTTP API (stdlib, OFF unless an api_port is given) serves
the same file for later replay; all DuckDB access (reads AND writes) is serialized
through one ``threading.Lock`` because the API threads share the writer's connection.
"""

from __future__ import annotations

import asyncio
import json
import random
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

import requests

# --------------------------------------------------------------------------- #
# Guarded opt-in imports (requirements-collector.txt).                         #
# Import of THIS module must always succeed (pytest collection safety); the    #
# hard failure is deferred to the moment the daemon/DB is actually used.       #
# --------------------------------------------------------------------------- #
try:  # optional dependency — see requirements-collector.txt
    import duckdb  # type: ignore
except Exception:  # noqa: BLE001 — any import failure means "store unavailable"
    duckdb = None  # type: ignore[assignment]

try:  # optional dependency — see requirements-collector.txt
    import websockets  # type: ignore
except Exception:  # noqa: BLE001
    websockets = None  # type: ignore[assignment]

_INSTALL_HINT = "pip install -r requirements-collector.txt"

__all__ = [
    "DEFAULT_DB",
    "BatchWriter",
    "BybitBook",
    "Downsampler",
    "make_api_server",
    "normalize_binance_depth",
    "normalize_binance_open_interest",
    "normalize_binance_premium_index",
    "normalize_bybit_liq",
    "normalize_bybit_ticker",
    "normalize_bybit_trade",
    "open_db",
    "run",
]

# Default store location — ``data/`` is gitignored (and data/ticks.duckdb explicitly).
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "ticks.duckdb"

# Public endpoints (keyless — DESIGN §2 data matrix, all verified 2026-07-03).
_BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
# Combined-stream endpoint: frames arrive WRAPPED as {"stream": ..., "data": {...}}
# (fixture ``binancef_depth20`` — normalize_binance_depth unwraps this).
_BINANCEF_WS = "wss://fstream.binance.com/stream?streams={streams}"
_BINANCE_FAPI = "https://fapi.binance.com"

# A polite, identifiable UA so public endpoints don't 403 a bare client (data.py idiom).
_USER_AGENT = "btc-quant/0.1 (research collector; keyless; no-trading)"

# Writer batching (DESIGN §3): flush every 500 ms or 500 rows, whichever first.
FLUSH_INTERVAL_S = 0.5
FLUSH_MAX_ROWS = 500

# Resilience knobs (DESIGN §3): capped exponential backoff + jitter per stream,
# and a stalled-stream watchdog — no frame for 60 s forces a reconnect.
WATCHDOG_S = 60.0
_BACKOFF_BASE_S = 1.0
_BACKOFF_CAP_S = 30.0
# Bybit v5 public WS expects an application-level {"op":"ping"} heartbeat ~every 20 s
# (protocol-level pings are not enough to keep the v5 session alive).
_APP_PING_S = 20.0

# REST poll cadence (DESIGN §3): premiumIndex 5 s, openInterest 60 s.
_PREMIUM_POLL_S = 5.0
_OI_POLL_S = 60.0

# Depth/funding downsample: store at most one row per second per (source, kind).
# The 100 ms depth firehose is a UI concern, not a storage one (DESIGN §3).
DOWNSAMPLE_MS = 1000

# Exchange codes accepted by run() — short codes per DESIGN §3 schema note.
# 'coinbase' is in the §3 stream list but deliberately NOT wired yet (spot tape is
# terminal-only for now); rejecting it loudly beats silently recording nothing.
_ACCEPTED_EXCHANGES = ("binancef", "bybit")


# --------------------------------------------------------------------------- #
# Schema (DESIGN §3 — verbatim; epoch-ms BIGINT everywhere; trade_id VARCHAR    #
# because Bybit trade ids are UUIDs; indexes on (symbol, ts_ms) per table).     #
# NOTE: ``index`` (funding_mark) is quoted — it is a keyword in SQL.            #
# --------------------------------------------------------------------------- #
_SCHEMA_DDL = (
    """CREATE TABLE IF NOT EXISTS trades (
        exchange VARCHAR, symbol VARCHAR, trade_id VARCHAR, ts_ms BIGINT,
        price DOUBLE, qty DOUBLE, aggressor_buy BOOLEAN)""",
    """CREATE TABLE IF NOT EXISTS liquidations (
        exchange VARCHAR, symbol VARCHAR, ts_ms BIGINT, side VARCHAR,
        price DOUBLE, qty DOUBLE, notional_usd DOUBLE)""",
    """CREATE TABLE IF NOT EXISTS depth_snapshots (
        exchange VARCHAR, symbol VARCHAR, ts_ms BIGINT, bids VARCHAR, asks VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS funding_mark (
        exchange VARCHAR, symbol VARCHAR, ts_ms BIGINT, mark DOUBLE,
        "index" DOUBLE, funding_rate DOUBLE, next_funding_ts BIGINT)""",
    """CREATE TABLE IF NOT EXISTS open_interest (
        exchange VARCHAR, symbol VARCHAR, ts_ms BIGINT, oi DOUBLE)""",
    "CREATE INDEX IF NOT EXISTS idx_trades_symbol_ts ON trades (symbol, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_liquidations_symbol_ts ON liquidations (symbol, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_depth_symbol_ts ON depth_snapshots (symbol, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_funding_symbol_ts ON funding_mark (symbol, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_oi_symbol_ts ON open_interest (symbol, ts_ms)",
)

# Column order is the row-tuple contract every normalize_* function follows.
_TABLE_COLUMNS = {
    "trades": ("exchange", "symbol", "trade_id", "ts_ms", "price", "qty", "aggressor_buy"),
    "liquidations": ("exchange", "symbol", "ts_ms", "side", "price", "qty", "notional_usd"),
    "depth_snapshots": ("exchange", "symbol", "ts_ms", "bids", "asks"),
    "funding_mark": ("exchange", "symbol", "ts_ms", "mark", "index", "funding_rate", "next_funding_ts"),
    "open_interest": ("exchange", "symbol", "ts_ms", "oi"),
}

_INSERT_SQL = {
    table: "INSERT INTO {t} VALUES ({q})".format(t=table, q=", ".join("?" * len(cols)))
    for table, cols in _TABLE_COLUMNS.items()
}


def _require_deps(*, need_ws: bool) -> None:
    """Raise an actionable RuntimeError iff the opt-in deps are missing.

    Called only when the collector actually *runs* (or opens a DB) — importing the
    module stays safe for pytest collection and normalizer-only use.
    """
    missing = []
    if duckdb is None:
        missing.append("duckdb")
    if need_ws and websockets is None:
        missing.append("websockets")
    if missing:
        raise RuntimeError(
            f"collector dependencies missing ({', '.join(missing)}) — {_INSTALL_HINT}"
        )


# --------------------------------------------------------------------------- #
# Pure normalization: parsed frame dict -> list of row tuples.                 #
# One function per wire shape, written against scripts/fixtures_ws.json (REAL  #
# captured frames), each documenting its side/aggressor convention (§0.6).     #
# --------------------------------------------------------------------------- #
def normalize_bybit_trade(frame: dict) -> list[tuple]:
    """Bybit v5 ``publicTrade.<SYM>`` frame -> ``trades`` rows.

    Wire shape (fixture ``bybit_publicTrade``): ``data`` is a LIST of trades with
    ``T`` (epoch ms), ``s`` symbol, ``S`` side, ``v`` qty, ``p`` price, ``i`` a
    UUID trade id (hence trade_id VARCHAR in the schema).

    Convention (§0.6): Bybit ``S`` is already the **taker** (aggressor) side —
    used as-is, no inversion. ``S == "Buy"`` -> aggressor_buy True.
    """
    rows: list[tuple] = []
    for t in frame.get("data") or []:
        rows.append(
            (
                "bybit",
                t["s"],
                str(t["i"]),
                int(t["T"]),
                float(t["p"]),
                float(t["v"]),
                t["S"] == "Buy",
            )
        )
    return rows


def normalize_bybit_liq(frame: dict) -> list[tuple]:
    """Bybit v5 ``allLiquidation.<SYM>`` frame -> ``liquidations`` rows.

    Same envelope as ``publicTrade`` (``data`` list with ``T``/``s``/``S``/``v``/
    ``p``) — fixture ``bybit_allLiquidation`` holds REAL captured prints (BTC liqs
    are sparse; the 2026-07-03 capture window caught JUP/BEAT/1000PEPE prints,
    identical shape).

    Side convention (§0.6 / DESIGN §3 schema note): the printed order is the
    exchange's forced *close* of the losing position, so printed side ``Buy``
    means a **SHORT** was liquidated (forced buy-back) and ``Sell`` means a
    **LONG** was liquidated. We store the *liquidated position* side
    (``long|short``), not the print side. ``notional_usd = price * qty``
    (linear USDT contract — qty is in BTC).
    """
    rows: list[tuple] = []
    for liq in frame.get("data") or []:
        price = float(liq["p"])
        qty = float(liq["v"])
        side = "short" if liq["S"] == "Buy" else "long"
        rows.append(("bybit", liq["s"], int(liq["T"]), side, price, qty, price * qty))
    return rows


def normalize_bybit_ticker(
    frame: dict, snapshot: Optional[dict]
) -> tuple[list[tuple], list[tuple], dict]:
    """Bybit v5 ``tickers.<SYM>`` frame -> (funding_mark rows, open_interest rows, merged snapshot).

    THE critical wire reality (fixtures ``bybit_tickers_snapshot`` /
    ``bybit_tickers_delta``, DESIGN §2): after one full ``snapshot``, Bybit sends
    **partial deltas carrying ONLY the changed fields** — a delta that only moved
    ``bid1Size`` omits ``markPrice``/``fundingRate``/``openInterest`` entirely.
    Reading deltas without merging would fabricate NaNs or drop rows. So this is a
    stateful merge: pass ``snapshot=None`` for a fresh stream, then feed each
    returned ``merged`` dict back in as the next call's ``snapshot``.

    Honesty guard: rows are emitted only when the merged state actually contains
    every field the row needs (a delta arriving *before* any snapshot yields no
    rows — we never invent a mark price). Row timestamps use the frame's ``ts``
    (Bybit's own epoch-ms send time), so merged-but-unchanged fields are honestly
    re-stamped as "still true at ts" observations of exchange state.
    """
    data = frame.get("data") or {}
    if frame.get("type") == "snapshot" or snapshot is None:
        # Full snapshot replaces state outright (never merge INTO a snapshot —
        # stale keys from a previous connection must not survive).
        merged = dict(data)
    else:
        merged = dict(snapshot)
        merged.update(data)  # delta: only the changed fields are present

    ts = int(frame.get("ts") or 0)
    symbol = merged.get("symbol") or ""

    funding_rows: list[tuple] = []
    if all(k in merged for k in ("markPrice", "indexPrice", "fundingRate", "nextFundingTime")):
        funding_rows.append(
            (
                "bybit",
                symbol,
                ts,
                float(merged["markPrice"]),
                float(merged["indexPrice"]),
                float(merged["fundingRate"]),
                int(merged["nextFundingTime"]),
            )
        )

    oi_rows: list[tuple] = []
    if "openInterest" in merged:
        # ``openInterest`` is in contracts (BTC for the linear perp); the value
        # column stores it as-is — no USD conversion here (that would silently
        # depend on which price you multiplied by).
        oi_rows.append(("bybit", symbol, ts, float(merged["openInterest"])))

    return funding_rows, oi_rows, merged


class BybitBook:
    """Stateful Bybit ``orderbook.50`` book: ``snapshot`` then ``delta`` frames.

    Wire reality (fixtures ``bybit_orderbook_snapshot`` / ``_delta``): a delta
    lists only touched levels; qty ``"0"`` DELETES a level, any other qty upserts
    it. The collector stores the *merged* book, downsampled to at most one
    ``depth_snapshots`` row per second (DESIGN §3) — the raw delta stream is a UI
    concern, not a storage one.
    """

    def __init__(self) -> None:
        self.symbol: Optional[str] = None
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

    def apply(self, frame: dict) -> None:
        """Apply one ``orderbook.50`` frame (snapshot replaces; delta merges)."""
        data = frame.get("data") or {}
        if frame.get("type") == "snapshot":
            self.bids.clear()
            self.asks.clear()
        self.symbol = data.get("s") or self.symbol
        for key, book in (("b", self.bids), ("a", self.asks)):
            for price_s, qty_s in data.get(key) or []:
                price = float(price_s)
                qty = float(qty_s)
                if qty == 0.0:
                    book.pop(price, None)  # qty "0" deletes the level (fixture-verified)
                else:
                    book[price] = qty

    def depth_row(self, ts_ms: int, n_levels: int = 20) -> Optional[tuple]:
        """Current book -> one ``depth_snapshots`` row (top-N, JSON, best-first).

        Top-20 to match the Binance leg and keep storage bounded (DESIGN §3
        stores top-20 even though the stream carries 50 levels). Returns None
        until a snapshot has populated the book — never emit an empty book as if
        it were an observation.
        """
        if self.symbol is None or (not self.bids and not self.asks):
            return None
        bids = sorted(self.bids.items(), key=lambda kv: -kv[0])[:n_levels]
        asks = sorted(self.asks.items(), key=lambda kv: kv[0])[:n_levels]
        return (
            "bybit",
            self.symbol,
            int(ts_ms),
            json.dumps([[p, q] for p, q in bids], separators=(",", ":")),
            json.dumps([[p, q] for p, q in asks], separators=(",", ":")),
        )


def normalize_binance_depth(frame: dict) -> list[tuple]:
    """Binance fstream ``<sym>@depth20@100ms`` frame -> ``depth_snapshots`` rows.

    Wire shape (fixture ``binancef_depth20``): on the combined endpoint each
    frame arrives WRAPPED as ``{"stream": ..., "data": {...}}`` — unwrap first
    (a bare payload is also accepted for robustness). Unlike Bybit's book,
    every ``depth20`` frame is a FULL 20-level snapshot: bids descending, asks
    ascending, already best-first — stored as delivered. ``ts_ms`` uses ``T``
    (matching-engine transaction time) rather than ``E`` (event/push time): it
    is the closest to "when this book state was true".

    The 100 ms cadence is downsampled to 1/s by the caller (DESIGN §3) — this
    function stays pure and per-frame.
    """
    data = frame.get("data") or frame
    bids = [[float(p), float(q)] for p, q in data.get("b") or []]
    asks = [[float(p), float(q)] for p, q in data.get("a") or []]
    return [
        (
            "binancef",
            data["s"],
            int(data["T"]),
            json.dumps(bids, separators=(",", ":")),
            json.dumps(asks, separators=(",", ":")),
        )
    ]


def normalize_binance_premium_index(payload: dict) -> list[tuple]:
    """Binance ``/fapi/v1/premiumIndex`` REST payload -> ``funding_mark`` rows.

    Wire shape (fixture ``binancef_rest_premiumIndex``): flat dict with string
    decimals and epoch-ms ints. ``lastFundingRate`` is the current period's rate
    (decimal, e.g. ``"0.00010000"`` == 1 bp per 8 h interval — stored as the raw
    decimal, NOT annualized; annualization is a presentation concern).
    """
    return [
        (
            "binancef",
            payload["symbol"],
            int(payload["time"]),
            float(payload["markPrice"]),
            float(payload["indexPrice"]),
            float(payload["lastFundingRate"]),
            int(payload["nextFundingTime"]),
        )
    ]


def normalize_binance_open_interest(payload: dict) -> list[tuple]:
    """Binance ``/fapi/v1/openInterest`` REST payload -> ``open_interest`` rows.

    Wire shape (fixture ``binancef_rest_openInterest``): ``openInterest`` is in
    contracts (BTC), stored as-is — same no-USD-conversion rule as the Bybit leg.
    """
    return [
        (
            "binancef",
            payload["symbol"],
            int(payload["time"]),
            float(payload["openInterest"]),
        )
    ]


# --------------------------------------------------------------------------- #
# DuckDB store: schema + batched writer.                                       #
# --------------------------------------------------------------------------- #
def open_db(path: Any) -> "duckdb.DuckDBPyConnection":
    """Open (creating parents/tables/indexes as needed) the tick store.

    Single-writer contract (DESIGN §3): the daemon owns the file; no cross-process
    readers while it runs — the BYOD API exists precisely so replay reads go
    through this process instead of a second DuckDB handle on the same file.
    """
    _require_deps(need_ws=False)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p))
    for ddl in _SCHEMA_DDL:
        con.execute(ddl)
    return con


class BatchWriter:
    """Batched inserts: buffer rows, flush every 500 ms or 500 rows (DESIGN §3).

    ``add`` is only ever called from the asyncio thread (single producer);
    ``flush`` takes the shared lock because the BYOD API threads read through the
    SAME connection — one ``threading.Lock`` serializes every DuckDB touch.
    The 500-row trigger fires inside ``add``; the 500 ms trigger is the ``run``
    task. Final flush on shutdown is the caller's job (SIGINT rail).
    """

    def __init__(
        self,
        con: "duckdb.DuckDBPyConnection",
        lock: threading.Lock,
        max_rows: int = FLUSH_MAX_ROWS,
    ) -> None:
        self._con = con
        self._lock = lock
        self._max_rows = max_rows
        self._buffers: dict[str, list[tuple]] = {t: [] for t in _INSERT_SQL}
        self._pending = 0
        # Honest ops counter (surfaces in logs; the DB itself is the source of truth).
        self.rows_written: dict[str, int] = {t: 0 for t in _INSERT_SQL}

    def add(self, table: str, rows: list[tuple]) -> None:
        """Buffer rows for ``table``; auto-flush once >= max_rows are pending."""
        if not rows:
            return
        self._buffers[table].extend(rows)
        self._pending += len(rows)
        if self._pending >= self._max_rows:
            self.flush()

    def flush(self) -> int:
        """Write every buffered row; return how many were written."""
        if self._pending == 0:
            return 0
        written = 0
        with self._lock:
            for table, buf in self._buffers.items():
                if not buf:
                    continue
                self._con.executemany(_INSERT_SQL[table], buf)
                self.rows_written[table] += len(buf)
                written += len(buf)
                buf.clear()
        self._pending = 0
        return written

    async def run(self, stop_event: asyncio.Event, interval_s: float = FLUSH_INTERVAL_S) -> None:
        """Periodic-flush task (the 500 ms half of the flush contract)."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass
            self.flush()


class Downsampler:
    """Keep at most one row per ``interval_ms`` per key (DESIGN §3: depth 1/s).

    Timestamps are the exchange's own epoch-ms — so a reconnect that replays an
    older frame cannot sneak in extra rows, and a stalled stream simply stops
    producing (gaps stay gaps; no synthetic ticks to "fill" the second grid).
    """

    def __init__(self, interval_ms: int = DOWNSAMPLE_MS) -> None:
        self.interval_ms = interval_ms
        self._last: dict[Any, int] = {}

    def ready(self, key: Any, ts_ms: int) -> bool:
        last = self._last.get(key)
        if last is not None and ts_ms - last < self.interval_ms:
            return False
        self._last[key] = int(ts_ms)
        return True


# --------------------------------------------------------------------------- #
# Resilient stream plumbing: reconnect w/ capped exp backoff + jitter, and a   #
# stalled-stream watchdog (no frame > 60 s -> force reconnect). Mirror of the  #
# dashboard makeSocket semantics (DESIGN §3).                                  #
# --------------------------------------------------------------------------- #
async def _sleep_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    """Sleep, but wake immediately if shutdown is requested."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def _ws_stream(
    name: str,
    url: str,
    on_frame,
    *,
    stop_event: asyncio.Event,
    subscribe: Optional[dict] = None,
    app_ping: Optional[dict] = None,
    log=print,
) -> None:
    """One resilient WS leg: connect, subscribe, pump frames into ``on_frame``.

    Honesty rail (DESIGN §3): every disconnect/backoff window is a HOLE in the
    recorded series. We log it and move on — no interpolation, no replay-fill.
    """
    attempt = 0
    while not stop_event.is_set():
        try:
            async with websockets.connect(
                url, open_timeout=15, close_timeout=5, user_agent_header=_USER_AGENT
            ) as ws:
                if subscribe is not None:
                    await ws.send(json.dumps(subscribe))
                attempt = 0  # a successful connect resets the backoff ladder
                last_frame = time.monotonic()
                while not stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_APP_PING_S)
                    except asyncio.TimeoutError:
                        # Watchdog: a socket that is "open" but silent for 60 s is
                        # treated as dead — force the reconnect path (§3).
                        if time.monotonic() - last_frame > WATCHDOG_S:
                            raise TimeoutError(
                                f"no frame in {WATCHDOG_S:.0f}s — watchdog reconnect"
                            )
                        if app_ping is not None:  # Bybit v5 app-level heartbeat
                            await ws.send(json.dumps(app_ping))
                        continue
                    last_frame = time.monotonic()
                    try:
                        frame = json.loads(raw)
                    except (TypeError, ValueError):
                        continue  # non-JSON frame: skip, never guess at contents
                    try:
                        on_frame(frame)
                    except Exception as exc:  # noqa: BLE001 — one bad frame must not kill the leg
                        log(f"[collector] {name}: frame handler error: {exc!r}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — reconnect on ANY transport failure
            if stop_event.is_set():
                break
            attempt += 1
            # Capped exponential backoff with jitter (0.5x–1.5x) so a fleet of
            # streams doesn't thundering-herd the endpoint after an outage.
            delay = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** min(attempt, 8)))
            delay *= 0.5 + random.random()
            log(
                f"[collector] {name}: {exc!r} — reconnecting in {delay:.1f}s "
                f"(attempt {attempt}; the gap stays a gap)"
            )
            await _sleep_or_stop(stop_event, delay)


async def _rest_poll(
    name: str,
    fetch,
    on_payload,
    interval_s: float,
    stop_event: asyncio.Event,
    log=print,
) -> None:
    """One resilient REST poll leg. ``fetch`` is a blocking callable (requests)
    run via ``asyncio.to_thread`` so the event loop never blocks on HTTP.
    A failed poll is logged and skipped — the missing sample stays missing.
    """
    while not stop_event.is_set():
        try:
            payload = await asyncio.to_thread(fetch)
            on_payload(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed poll must not kill the leg
            log(f"[collector] {name}: poll failed: {exc!r} (the gap stays a gap)")
        await _sleep_or_stop(stop_event, interval_s)


def _fetch_binance_premium_index(symbol: str) -> dict:
    """Blocking GET ``/fapi/v1/premiumIndex`` (run inside asyncio.to_thread)."""
    resp = requests.get(
        f"{_BINANCE_FAPI}/fapi/v1/premiumIndex",
        params={"symbol": symbol},
        timeout=10.0,
        headers={"User-Agent": _USER_AGENT},
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_binance_open_interest(symbol: str) -> dict:
    """Blocking GET ``/fapi/v1/openInterest`` (run inside asyncio.to_thread)."""
    resp = requests.get(
        f"{_BINANCE_FAPI}/fapi/v1/openInterest",
        params={"symbol": symbol},
        timeout=10.0,
        headers={"User-Agent": _USER_AGENT},
    )
    resp.raise_for_status()
    return resp.json()


async def _retention_loop(
    con: "duckdb.DuckDBPyConnection",
    lock: threading.Lock,
    retention_days: int,
    stop_event: asyncio.Event,
    log=print,
) -> None:
    """Optional daily DELETE of rows older than ``retention_days``.

    Keep-all is the DEFAULT (DESIGN §3 — the whole point is accumulating research
    history, unlike cryexc-history's 24 h cap); this loop only exists when the
    user explicitly opts into a cap.
    """
    while not stop_event.is_set():
        cutoff = int(time.time() * 1000) - int(retention_days) * 86_400_000
        with lock:
            for table in _TABLE_COLUMNS:
                con.execute(f"DELETE FROM {table} WHERE ts_ms < ?", [cutoff])
        log(f"[collector] retention: pruned rows older than {retention_days}d")
        await _sleep_or_stop(stop_event, 86_400.0)


# --------------------------------------------------------------------------- #
# BYOD HTTP API (DESIGN §3) — stdlib only, OFF unless an api_port is given.    #
# Serves the SAME DuckDB connection as the writer; every read takes the shared #
# threading.Lock (single-process; no cross-process readers on a live file).    #
# --------------------------------------------------------------------------- #
_API_ROUTES = {
    "/v1/trades": "trades",
    "/v1/liquidations": "liquidations",
    "/v1/funding": "funding_mark",
    "/v1/oi": "open_interest",
    "/v1/depth": "depth_snapshots",
}
_API_DEFAULT_LIMIT = 1000
_API_MAX_LIMIT = 10_000


def _query_table(
    con: "duckdb.DuckDBPyConnection",
    lock: threading.Lock,
    table: str,
    qs: dict[str, str],
) -> dict:
    """Build + run one bounded, parameterized read; return a JSON-able dict.

    Params: ``symbol``, ``start_ms``, ``end_ms`` (inclusive bounds), ``limit``
    (default 1000, hard cap 10000 — a replay client pages by ts_ms, it does not
    slurp the file). All values go through ``?`` placeholders; table/column names
    come only from our own whitelists.
    """
    cols = _TABLE_COLUMNS[table]
    select_cols = ", ".join(f'"{c}"' for c in cols)  # "index" needs the quotes
    sql = f"SELECT {select_cols} FROM {table}"  # noqa: S608 — table from whitelist
    clauses: list[str] = []
    params: list[Any] = []
    if "symbol" in qs:
        clauses.append("symbol = ?")
        params.append(qs["symbol"])
    if "start_ms" in qs:
        clauses.append("ts_ms >= ?")
        params.append(int(qs["start_ms"]))
    if "end_ms" in qs:
        clauses.append("ts_ms <= ?")
        params.append(int(qs["end_ms"]))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    limit = max(1, min(int(qs.get("limit", _API_DEFAULT_LIMIT)), _API_MAX_LIMIT))
    sql += " ORDER BY ts_ms ASC LIMIT ?"
    params.append(limit)
    with lock:
        rows = con.execute(sql, params).fetchall()
    # depth bids/asks stay JSON *strings* here (stored form) — the client parses.
    return {"table": table, "n": len(rows), "rows": [dict(zip(cols, r)) for r in rows]}


def make_api_server(
    con: "duckdb.DuckDBPyConnection",
    lock: threading.Lock,
    info: dict,
    port: int,
    host: str = "127.0.0.1",
) -> ThreadingHTTPServer:
    """Build (not start) the BYOD ThreadingHTTPServer. ``port=0`` -> ephemeral
    (tests read the bound port off ``server.server_address``). Caller runs
    ``serve_forever`` in a daemon thread and ``shutdown()``s it on exit.
    Binds loopback by default — this is a local research store, not a service.
    """

    class Handler(BaseHTTPRequestHandler):
        server_version = "btcq-collector/0.1"

        def log_message(self, fmt, *args):  # noqa: A002, ARG002
            pass  # quiet: request logging is noise for a long-running daemon

        def _send_json(self, obj: dict, status: int = 200) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            # The terminal page (a localhost file server on another port) is the
            # intended replay client — permissive CORS on a loopback-only, read-only,
            # keyless store is fine.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — http.server API
            parts = urlsplit(self.path)
            path = parts.path.rstrip("/") or "/"
            qs = {k: v[-1] for k, v in parse_qs(parts.query).items()}
            try:
                if path == "/health":
                    self._send_json({"ok": True, "ts_ms": int(time.time() * 1000)})
                elif path == "/v1/info":
                    counts = {}
                    with lock:
                        for table in _TABLE_COLUMNS:
                            counts[table] = con.execute(
                                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 — whitelist
                            ).fetchone()[0]
                    self._send_json({**info, "row_counts": counts})
                elif path in _API_ROUTES:
                    self._send_json(_query_table(con, lock, _API_ROUTES[path], qs))
                else:
                    self._send_json(
                        {"error": "not found", "routes": ["/health", "/v1/info", *_API_ROUTES]},
                        status=404,
                    )
            except ValueError as exc:  # bad start_ms/end_ms/limit -> client error
                self._send_json({"error": f"bad parameter: {exc}"}, status=400)
            except Exception as exc:  # noqa: BLE001 — never let a read kill the thread
                self._send_json({"error": repr(exc)}, status=500)

    return ThreadingHTTPServer((host, port), Handler)


# --------------------------------------------------------------------------- #
# Daemon orchestration.                                                        #
# --------------------------------------------------------------------------- #
async def _run_async(
    symbol: str,
    exchanges: tuple[str, ...],
    db: Any,
    api_port: Optional[int],
    retention_days: Optional[int],
    log=print,
) -> None:
    """Wire streams -> normalizers -> batched writer; run until SIGINT/SIGTERM."""
    con = open_db(db)
    lock = threading.Lock()
    writer = BatchWriter(con, lock)
    down = Downsampler()
    book = BybitBook()
    ticker_state: dict[str, Optional[dict]] = {"snap": None}  # bybit tickers merge state

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass  # non-unix / nested loop: KeyboardInterrupt fallback in run()

    # --- frame handlers (asyncio thread only; writer.add is single-producer) ---
    def on_bybit(frame: dict) -> None:
        topic = frame.get("topic") or ""  # sub-acks/pongs have no topic -> ignored
        if topic.startswith("publicTrade."):
            writer.add("trades", normalize_bybit_trade(frame))
        elif topic.startswith("allLiquidation."):
            writer.add("liquidations", normalize_bybit_liq(frame))
        elif topic.startswith("orderbook."):
            book.apply(frame)
            # cts = matching-engine time (preferred); ts = gateway send time.
            ts = int(frame.get("cts") or frame.get("ts") or 0)
            if ts and down.ready(("bybit", "depth"), ts):
                row = book.depth_row(ts)
                if row is not None:
                    writer.add("depth_snapshots", [row])
        elif topic.startswith("tickers."):
            funding_rows, oi_rows, ticker_state["snap"] = normalize_bybit_ticker(
                frame, ticker_state["snap"]
            )
            # tickers ticks ~100 ms; funding_mark is stored at 1/s (DESIGN §3) and
            # the OI that rides the same stream gets the same 1/s cap.
            if funding_rows and down.ready(("bybit", "funding"), funding_rows[0][2]):
                writer.add("funding_mark", funding_rows)
            if oi_rows and down.ready(("bybit", "oi"), oi_rows[0][2]):
                writer.add("open_interest", oi_rows)

    def on_binance_depth(frame: dict) -> None:
        if "data" not in frame and "b" not in frame:
            return  # combined-endpoint control frames (e.g. {"result":null,"id":..})
        for row in normalize_binance_depth(frame):
            # row[2] is ts_ms — 100 ms firehose stored at 1/s (DESIGN §3).
            if down.ready(("binancef", "depth"), row[2]):
                writer.add("depth_snapshots", [row])

    # --- tasks ---
    tasks: list[asyncio.Task] = [
        asyncio.create_task(writer.run(stop_event), name="writer-flush"),
    ]
    if "bybit" in exchanges:
        subscribe = {
            "op": "subscribe",
            "args": [
                f"publicTrade.{symbol}",
                f"orderbook.50.{symbol}",
                f"tickers.{symbol}",
                f"allLiquidation.{symbol}",
            ],
        }
        tasks.append(
            asyncio.create_task(
                _ws_stream(
                    "bybit-ws",
                    _BYBIT_WS,
                    on_bybit,
                    stop_event=stop_event,
                    subscribe=subscribe,
                    app_ping={"op": "ping"},
                    log=log,
                ),
                name="bybit-ws",
            )
        )
    if "binancef" in exchanges:
        # depth20@100ms is the ONLY Binance futures WS topic that flows on this
        # network (§0.2) — trades/mark are topic-filtered, hence the REST polls.
        url = _BINANCEF_WS.format(streams=f"{symbol.lower()}@depth20@100ms")
        tasks.append(
            asyncio.create_task(
                _ws_stream("binancef-ws", url, on_binance_depth, stop_event=stop_event, log=log),
                name="binancef-ws",
            )
        )
        tasks.append(
            asyncio.create_task(
                _rest_poll(
                    "binancef-premiumIndex",
                    lambda: _fetch_binance_premium_index(symbol),
                    lambda p: writer.add("funding_mark", normalize_binance_premium_index(p)),
                    _PREMIUM_POLL_S,
                    stop_event,
                    log=log,
                ),
                name="binancef-premiumIndex",
            )
        )
        tasks.append(
            asyncio.create_task(
                _rest_poll(
                    "binancef-openInterest",
                    lambda: _fetch_binance_open_interest(symbol),
                    lambda p: writer.add("open_interest", normalize_binance_open_interest(p)),
                    _OI_POLL_S,
                    stop_event,
                    log=log,
                ),
                name="binancef-openInterest",
            )
        )
    if retention_days is not None:
        tasks.append(
            asyncio.create_task(
                _retention_loop(con, lock, retention_days, stop_event, log=log),
                name="retention",
            )
        )

    server: Optional[ThreadingHTTPServer] = None
    if api_port is not None:
        info = {
            "symbol": symbol,
            "exchanges": list(exchanges),
            "db": str(db),
            "started_ms": int(time.time() * 1000),
            "retention_days": retention_days,  # None == keep-all (the default)
        }
        server = make_api_server(con, lock, info, port=api_port)
        threading.Thread(target=server.serve_forever, name="byod-api", daemon=True).start()
        log(f"[collector] BYOD API on http://127.0.0.1:{server.server_address[1]}")

    log(
        f"[collector] recording {symbol} from {', '.join(exchanges)} -> {db} "
        f"(retention: {'keep-all' if retention_days is None else f'{retention_days}d'}; "
        f"Ctrl-C flushes and exits cleanly)"
    )

    await stop_event.wait()

    # --- graceful shutdown: stop legs, FINAL FLUSH, close (SIGINT rail, §3) ---
    log("[collector] shutdown requested — final flush")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    writer.flush()
    if server is not None:
        server.shutdown()
        server.server_close()
    with lock:
        con.close()
    log(f"[collector] closed. rows this session: {writer.rows_written}")


def run(
    symbol: str = "BTCUSDT",
    exchanges: tuple[str, ...] = ("binancef", "bybit"),
    db: Any = DEFAULT_DB,
    api_port: Optional[int] = None,
    retention_days: Optional[int] = None,
    log=print,
) -> None:
    """Run the collector daemon until SIGINT/SIGTERM (blocking entry point).

    Raises
    ------
    RuntimeError
        If the opt-in deps (duckdb/websockets) are missing — with the exact
        install command. Raised HERE, at run time, never at import time.
    ValueError
        On an unknown exchange code. ``coinbase`` is rejected explicitly (spot
        tape leg is terminal-only for now — DESIGN §3 deviation, documented in
        the module docstring) rather than silently recording nothing.
    """
    _require_deps(need_ws=True)
    bad = [e for e in exchanges if e not in _ACCEPTED_EXCHANGES]
    if bad:
        raise ValueError(
            f"unknown exchange code(s) {bad!r}; accepted: {list(_ACCEPTED_EXCHANGES)} "
            "(coinbase spot tape is terminal-only for now — not collected)"
        )
    if retention_days is not None and retention_days < 1:
        raise ValueError("retention_days must be >= 1 (omit it for keep-all, the default)")
    try:
        asyncio.run(_run_async(symbol, exchanges, db, api_port, retention_days, log=log))
    except KeyboardInterrupt:
        # Fallback path when add_signal_handler is unavailable; the batched tail
        # (< 500 ms of rows) may be lost here — the unix signal path flushes fully.
        log("[collector] interrupted")


if __name__ == "__main__":  # allow `python -m btcquant.collector` for quick smokes
    run()
