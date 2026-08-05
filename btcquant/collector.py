"""collector.py — tick collector daemon (DESIGN-orderflow-terminal.md §3 + §3c).

Keyless, research-only accumulation of BTC perp microstructure into a local DuckDB
store (default: a **directory of per-UTC-day files** ``data/ticks/YYYY-MM-DD.duckdb``
— §3c daily rotation; a ``.duckdb`` FILE path selects the legacy single-file mode).
**No API keys, no authenticated endpoints, no orders** — every stream below is a
public WS/REST feed, verified live from this machine on 2026-07-03/05 (frames
captured to ``scripts/fixtures_ws.json``; the normalizers here are written against
those *actual wire shapes*, not remembered docs).

Honesty rails (DESIGN §0, binding)
----------------------------------
* Running this collector changes the tick-data families (CVD, liquidations, OI,
  funding accrual) from *un-ingested* to **time-gated**, NOT to *validated* (§0.3).
  Nothing recorded here may enter the OOS harness until accumulated history clears
  MinBTL for the intended trial count AND a pre-registered hypothesis passes the
  DEVELOPMENT.md §6 greenlight. Until then this file buys *optionality*, not signals.
* **Gaps stay gaps** (§3 resilience). A reconnect, a stalled stream, or a downed
  process leaves a hole in ``ts_ms`` — we never interpolate, backfill from a second
  source into the same series, or otherwise fabricate rows (§0.7). A watchdog
  restart is one of those holes: it is logged, counted in ``/health`` and appended
  to ``data/ticks/gaps.jsonl``, which EXPLAINS the hole and never fills it.
* **Per-leg watchdog** (§3 resilience). Process-level supervision is not enough:
  on 2026-07-24/25 this daemon stayed alive, answered ``/health`` with
  ``{"ok": true}`` and kept the day file's mtime moving while six of its ~16 legs
  had been dead for 40 hours (an ENOSPC made ``print()`` itself raise inside every
  leg's ``except`` block; the exceptions were parked in un-awaited Tasks and
  evaporated). Two rails now: a log sink that cannot kill a leg (:func:`_safe_log`,
  plus guarded flush/prune loops), and :class:`LegSupervisor`, which judges every
  leg BY SYMPTOM — task ended, or no ROWS for longer than that leg type tolerates
  (:data:`LEG_BUDGET_S`, per-leg-type and grounded in measured cadence so a
  legitimately sparse stream can never cry wolf) — then restarts it on a bounded
  ladder AND reports it. ``/health`` reports what it observes; ``/health/ready``
  is the 200/503 probe surface.
* **Aggressor/side conventions are per-exchange and normalized explicitly** (§0.6):
  Bybit ``publicTrade.S`` is already the *taker* side (used as-is); Bybit
  ``allLiquidation.S == "Buy"`` means a **short** position was liquidated (the
  printed order is the forced buy-back). Each normalizer documents its convention.
* **Empirical stream reality** (§0.2): Binance Futures WS topic-filters this network
  — only ``depth20@100ms`` flows; trades/mark on the same socket deliver sub-acks and
  nothing else. So Bybit v5 is the primary WS feed and Binance contributes depth WS
  plus REST polls (``premiumIndex`` 5 s, ``openInterest`` 60 s). We collect what the
  wire actually delivers. Binance futures *trades* arrive via the REST ``aggTrades``
  poll (§3c — the WS topic-filter does not apply to REST; a ``fromId`` cursor keeps
  the tape gapless by aggTradeId).
* **Rotation immutability** (§3c): day files are dataset partitions and partitions
  mean EVENT time — every row is routed by the UTC day of its own ``ts_ms``, never
  by arrival time. Yesterday's file stays open for a 5-minute grace window after UTC
  midnight (late/out-of-order rows still land correctly), then it is final-flushed
  and closed. A closed day file is **immutable** — that is what makes the HF upload
  gap-free; a row arriving for an already-closed day is DROPPED and counted, never
  written (an honest loss beats a corrupted partition).

Dependencies (opt-in, like MLflow/DVC — requirements-collector.txt)
-------------------------------------------------------------------
``duckdb`` and ``websockets`` are imported behind guards so that *importing this
module never fails* (pytest collection, ``import btcquant``, the normalizer-only
paths). Only actually *running* the daemon (or opening a DB) raises a clear
``RuntimeError`` with the install hint. ``requests`` is a core dependency already
(btcquant/data.py) and is used for the Binance REST polls via ``asyncio.to_thread``
so the event loop never blocks on HTTP.

Storage contract (DESIGN §3 + §3c schema — all timestamps epoch **ms**, UTC)
-----------------------------------------------------------------------------
Single writer process, batched inserts (flush every 500 ms or 500 rows, whichever
first), graceful final flush on SIGINT. Keep-all retention by default — the whole
point is accumulating research history; ``retention_days`` opts into a daily DELETE
(legacy single-file mode only — in rotation mode pruning belongs to the HF
lifecycle's verify-then-delete flow). Honest sizing note (§3): BTC perp trades ≈
0.5–1.5 M rows/day → order-of ~0.5–1 GB/month; depth@1s adds ~0.1 GB/month. Disk
is the user's budget — documented, not hidden. The optional BYOD HTTP API (stdlib,
OFF unless an api_port is given) serves the same store for later replay — its
paths/params/shapes are IDENTICAL in both modes (§3c: the contract is UNCHANGED);
in rotation mode it unions the local day files covering the requested range and
answers 410 + an ``hf://`` hint for ranges older than the oldest local day. Every
``symbol`` filter accepts the CANONICAL id: 'BTCUSDT' expands server-side to the
venue-native id set (okx 'BTC-USDT-SWAP', coinbase 'BTC-USD') via the SAME
``_symbol_legs`` derivation the daemon records with (see ``_expand_symbol``); an
explicit native id stays narrow, and rows keep their STORED native symbol (§0.7
— recorded data is never rewritten). All DuckDB access (reads AND writes) is
serialized through one ``threading.Lock`` because the API threads share the
writer's connections.

§4f Institutional Auction Suite — read-side additions (same lock/contract
discipline; every derived read stays DESCRIPTIVE, §0.1): ``/v1/profile`` is a
tick-exact volume profile aggregated in SQL over the local day files, with
POC/VAH/VAL computed server-side by the SAME 70 %-expansion convention as the
terminal's ProfileStore (one convention, ported not re-invented); ``/v1/vwap``
is an anchored VWAP ± volume-weighted sigma; ``/v1/levels`` serves the recorded
UTC-day levels registry ``data/ticks/levels.jsonl`` — maintained by the
rotation hook when :class:`DayFileManager` closes a day (idempotent append) and
by ``scripts/backfill_levels.py`` for days already archived to HF — with the
``naked`` POC flag DERIVED at serve time, never stored.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import math
import random
import re
import shutil
import signal
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
    "GAPS_FILENAME",
    "GRACE_WINDOW_MS",
    "LEG_BUDGET_S",
    "LEVELS_TICK",
    "OKX_CTVAL",
    "BatchWriter",
    "BybitBook",
    "CoinbaseTape",
    "DayFileManager",
    "Downsampler",
    "LegState",
    "LegSupervisor",
    "OkxBook",
    "RotatingWriter",
    "append_gap_row",
    "append_levels_row",
    "compute_day_levels",
    "derive_naked",
    "make_api_server",
    "migrate_legacy",
    "normalize_binance_aggtrades",
    "normalize_binance_depth",
    "normalize_binance_global_ls",
    "normalize_binance_oi_hist",
    "normalize_binance_open_interest",
    "normalize_binance_premium_index",
    "normalize_binance_taker_ls",
    "normalize_binance_top_pos_ls",
    "normalize_bybit_liq",
    "normalize_bybit_ticker",
    "normalize_bybit_trade",
    "normalize_coinbase_trades",
    "normalize_deribit_chain",
    "normalize_deribit_dvol",
    "normalize_okx_funding",
    "normalize_okx_oi",
    "normalize_okx_trade",
    "open_db",
    "parse_deribit_option_name",
    "read_gap_ledger",
    "read_levels_registry",
    "run",
    "utc_day",
]

# Default store location (§3c): a DIRECTORY of per-UTC-day files — data/ticks/ is
# gitignored. A ``.duckdb`` FILE path selects the legacy single-file mode instead.
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "ticks"

# Public endpoints (keyless — DESIGN §2 data matrix, all verified 2026-07-03/05).
_BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
# Combined-stream endpoint: frames arrive WRAPPED as {"stream": ..., "data": {...}}
# (fixture ``binancef_depth20`` — normalize_binance_depth unwraps this).
_BINANCEF_WS = "wss://fstream.binance.com/stream?streams={streams}"
_BINANCE_FAPI = "https://fapi.binance.com"
_OKX_WS = "wss://ws.okx.com:8443/ws/v5/public"
_OKX_REST = "https://www.okx.com"
_COINBASE_WS = "wss://advanced-trade-ws.coinbase.com"
_DERIBIT_REST = "https://www.deribit.com/api/v2"

# HF dataset the lifecycle uploads closed day files to (§3c) — used only for the
# BYOD API's honest 410 hint; this module never touches the network for HF.
_HF_DATASET = "azulcoder/btc-quant-ticks"

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
# Every await inside a leg must be BOUNDED. On 2026-08-02 bybit-ws and okx-ws each
# sat ESTABLISHED (verified: lsof FD 7/10, Recv-Q=0, Send-Q=0) for 2.5 h producing
# zero rows, while an independent probe of the same endpoints with the same
# subscribe payload returned 1,197 and 339 frames in 25 s. The venues were fine;
# the leg was wedged. `ws.send()` was the only unbounded await on the path, and it
# is reached ONLY by the legs carrying app_ping -- exactly the two that hung.
# Whether or not the send was the wedge, an unbounded await on the data lifeline
# is a defect: a send that cannot complete in 10 s means the socket is gone, and
# raising here hands the leg to the reconnect rail that already exists below.
_WS_SEND_TIMEOUT_S = 10.0
# Library-level RFC 6455 keepalive, applied ONLY to legs with no app_ping of
# their own (binancef, coinbase -- both healthy on it for days). These ARE the
# websockets>=14 defaults today; pinned because for those legs it is the one
# mechanism that detects a half-open peer, and it does not get to change under us
# on a library bump. Legs that DO carry app_ping pass ping_interval=None: see the
# connect() call for the measurement that forced that split.
# --------------------------------------------------------------------------- #
# aggTrades cursor survival (fix 2026-08-05). MEASURED failure: `_aggtrades_loop` #
# initialised `cursor = None` at loop start, so every watchdog restart re-seeded  #
# at the LIVE EDGE and silently dropped the whole backlog — and because the       #
# id-gap warning was guarded on `cursor is not None`, the drop was not even       #
# logged. That is how binancef tape coverage sat at 67.5 % in book-OFF hours      #
# against 78.3 % in book-ON hours (EDA-microstructure-001 §0b).                   #
#                                                                                 #
# The cursor now lives at MODULE scope, keyed by symbol, because the supervisor   #
# recreates the coroutine but never the module. Restart therefore resumes.        #
_AGGTRADES_CURSOR: dict[str, int] = {}
# Seek-back ceiling. Derived, not picked: `aggTrades` serves 1000 rows per request
# at weight 20, the poll runs every 5 s (12 polls/min = 240 weight/min, 10 % of the
# 2400/min futures budget), and BTCUSDT prints ~11.6 aggTrades/s (~696/min). Net
# catch-up is therefore ~11,300 trades/min, so 50,000 rows — about 72 minutes of
# tape — is caught up in ~4.4 minutes without raising the weight footprint.
# Past that the leg jumps to the live edge, because a leg spending an hour
# catching up is a leg not recording the present.
_AGGTRADES_SEEK_BACK_MAX = 50_000

_WS_PING_INTERVAL_S = 20.0
_WS_PING_TIMEOUT_S = 20.0

# REST poll cadence (DESIGN §3 + §3c): premiumIndex 5 s, openInterest 60 s,
# aggTrades 5 s, OKX funding/OI 60 s, crowding 5 m, DVOL 60 s, chain hourly.
_PREMIUM_POLL_S = 5.0
_OI_POLL_S = 60.0
_AGGTRADES_POLL_S = 5.0
_OKX_REST_POLL_S = 60.0
_CROWDING_POLL_S = 300.0
_DVOL_POLL_S = 60.0
_CHAIN_POLL_S = 3600.0

# Depth/funding downsample: store at most one row per second per (source, kind).
# The 100 ms depth firehose is a UI concern, not a storage one (DESIGN §3).
DOWNSAMPLE_MS = 1000

# Daily rotation (§3c): yesterday's day file stays writable for 5 minutes after
# UTC midnight (late/out-of-order rows land correctly), then it is closed for good.
GRACE_WINDOW_MS = 5 * 60 * 1000
_DAY_MS = 86_400_000

# OKX SWAP sizes are in CONTRACTS — BTC-USDT-SWAP ctVal = 0.01 BTC (verified via
# /api/v5/public/instruments, pinned in fixtures ``_okx_ctval_note``). Skipping the
# multiply would overstate OKX flow 100x against the BTC-denominated legs (§4b rail).
OKX_CTVAL = 0.01

# Exchange codes accepted by run() — short codes per DESIGN §3/§3c schema note.
_ACCEPTED_EXCHANGES = ("binancef", "bybit", "okx", "coinbase", "deribit")


# --------------------------------------------------------------------------- #
# PER-LEG WATCHDOG (DESIGN §3 resilience) — the 2026-07-24/25 outage rail.     #
#                                                                             #
# Measured incident (not hypothetical): the daemon ran 7 d 14 h and recorded   #
# ZERO trades for the last 40 h while every signal said healthy — process      #
# alive, /health {"ok":true}, day-file mtime advancing, 8.1 GiB free. Six legs #
# (bybit-ws, okx-ws, binancef-aggTrades, binancef-premiumIndex, okx-oi,        #
# deribit-dvol) had died inside a live process during the 07-23 ENOSPC window; #
# the surviving REST pollers kept touching the day file and masked it. Nothing #
# supervised the ~16 tasks: a task that raises parks its exception in the Task #
# object, nobody awaits it, the failure evaporates.                            #
#                                                                             #
# Two rails, both required:                                                    #
#   * root cause — a log sink that raises (print() on a full disk) must never  #
#     kill a leg; see _safe_log and the flush guards below;                    #
#   * symptom detection — a leg is unhealthy if its task ended OR it produced  #
#     no ROWS for longer than that leg type tolerates. Symptom, never cause:   #
#     disk failures, venue changes, silent stalls and reconnect-storm livelock #
#     (socket churns, task alive, zero data) all land in the same net.         #
# --------------------------------------------------------------------------- #
SUPERVISOR_TICK_S = 10.0
# Restart ladder, indexed by how many CONSECUTIVE restarts this leg has had.
# A dead leg comes back fast (5 s); a leg that keeps dying is slowed down so a
# permanently-broken venue cannot spin the process.
LEG_RESTART_BACKOFF_S = (5.0, 15.0, 60.0, 300.0)
# After this many consecutive restarts the watchdog STOPS restarting and stays
# LOUD forever (/health not-ok, a rate-limited log line). Auto-heal that hides a
# systemic problem is how 40 h of silence happened; give-up is deliberate.
LEG_RESTART_CAP = 6
# A leg healthy for this long resets its ladder (a venue outage last week must
# not make this week's first hiccup a "6th consecutive" restart).
LEG_RESTART_DECAY_S = 3600.0
LEG_GIVEUP_LOG_S = 300.0  # rate-limit the give-up line: loud, not spam
# A given-up leg is re-probed on this slow cadence. This is NOT auto-heal and it
# does not soften the 2026-07-23 lesson: /health stays not-ok, the loud line keeps
# printing, and the leg REMAINS `given-up` until real rows arrive -- a re-probe is
# an attempt, never a claim of health. What it removes is the other failure mode,
# measured on 2026-08-02: both bybit-ws and okx-ws held wedged-but-ESTABLISHED
# sockets for 2.5 h while their venues were verifiably healthy, because give-up
# left the broken task running and never touched it again. Staying loud and
# staying broken are separable; only the first one is the design intent.
LEG_GIVEUP_REPROBE_S = 900.0
# The periodic-flush task does not produce rows of its own, so it is judged on
# "did a flush cycle complete recently" — 120x the 500 ms flush interval.
WRITER_FLUSH_BUDGET_S = 60.0
# Watchdog restarts are recorded here, beside levels.jsonl. A restart is a REAL
# hole in the tape (§0.7): the ledger EXPLAINS the hole, it never fills it.
GAPS_FILENAME = "gaps.jsonl"

# Every WS leg carries its OWN reconnect rail: a silent-socket watchdog at
# WATCHDOG_S (60 s) plus capped backoff at _BACKOFF_CAP_S (30 s). The supervisor
# must always be the SECOND responder, so the stream budget sits strictly above
# 60 + 30 = 90 s. 120 s = that floor + 30 s margin, and it is ~40x the worst
# inter-row gap actually measured on a healthy day (bybit p99 0.74 s / max 2.44 s,
# okx 0.57/1.73, coinbase 0.83/3.82, binancef depth 1.28 s at its 1/s downsample).
_STREAM_BUDGET_S = 120.0

# Per-leg staleness budgets, in seconds, keyed by the leg name used in
# _run_async. None == "task-state only": a budget would CRY WOLF on this leg, so
# it is supervised for liveness (task ended -> restart) and nothing else.
#
# NEVER CRY WOLF (the N5 lesson). The budget attaches to the LEG (one task), not
# to a TABLE — a sparse stream cannot witness its own liveness. Liquidations are
# the proof: over 2026-07-05..08-03 the median day carries 309 rows and 58.2 % of
# all (day, hour) cells are ZERO, with the daily count spanning 3 to 1,219.
# Query: docs/EDA-microstructure-001.md §12 (HF mirror, hive-partitioned scan).
# `liquidations` therefore has no budget of its own; it rides bybit-ws, whose
# publicTrade flow is the witness. Same for bybit funding_mark/open_interest
# (sub-topics of bybit-ws) and okx books (sub-topic of okx-ws).
#
# THIS COMMENT USED TO CITE 2026-07-25 AS AN HONEST ZERO. IT WAS NOT. On that day
# the bybit carrier wrote 0 rows in 0 of 24 hours — the leg was dark all day, so
# the store's zero was the collector's absence, not the market's silence (§12).
# The example offered as PROOF that sparsity is reported honestly was itself an
# instance of the blindness this comment describes. Kept as the correction rather
# than quietly swapped, because the failure mode is the lesson: prose claims have
# no tests, so they rot while looking authoritative.
LEG_BUDGET_S: dict[str, Optional[float]] = {
    # --- continuous WS streams: see _STREAM_BUDGET_S above ---
    "bybit-ws": _STREAM_BUDGET_S,
    "okx-ws": _STREAM_BUDGET_S,
    "coinbase-ws": _STREAM_BUDGET_S,  # heartbeats keep the SOCKET alive -> only
    #   rows may vouch for this leg, never frames (that is why the verdict reads
    #   last_data_ms and the heartbeat frames stamp last_alive_ms only).
    "binancef-ws": _STREAM_BUDGET_S,  # depth20@100ms stored at 1/s
    # --- 5 s REST polls: 24 consecutive failed polls before we act; anything
    #     shorter is ordinary network flap, which the leg already logs. ---
    "binancef-premiumIndex": 120.0,
    "binancef-aggTrades": 120.0,
    # --- 60 s REST polls: 5 missed samples. Grounded on the live measurement,
    #     not on the nominal cadence — okx-funding's worst observed inter-row gap
    #     is 125.6 s (rate-limited endpoint), binancef-openInterest 66.1 s. 300 s
    #     clears the worst measured sample by >2x. ---
    "binancef-openInterest": 300.0,
    "okx-funding": 300.0,
    "okx-oi": 300.0,
    "deribit-dvol": 300.0,
    # --- 5 m crowding buckets: the poll re-serves the open bucket until it
    #     closes and the Downsampler dedupes it, so ROWS arrive once per bucket
    #     (measured p50 = p95 = p99 = 300.0 s exactly). 1500 s = 5 buckets. ---
    "binancef-takerlongshortRatio": 1500.0,
    "binancef-topLongShortPositionRatio": 1500.0,
    "binancef-globalLongShortAccountRatio": 1500.0,
    "binancef-openInterestHist": 1500.0,
    # --- hourly option-chain snapshot: measured p50 3602 s, and 8931 s on a
    #     degraded day. 21600 s = 6 snapshots, 2.4x that degraded worst case.
    #     Generous on purpose: an outright DEAD task is caught at the next 10 s
    #     tick whatever the budget says, so the budget only governs the
    #     alive-but-silent case — where being late costs little and a false
    #     alarm costs a real hole (a restart IS a gap). On a slow leg, be later
    #     and be right. ---
    "deribit-chain": 21600.0,
    # --- internal tasks: no rows of their own -> task-state only. The flush task
    #     gets its own freshness check (WRITER_FLUSH_BUDGET_S) in the writer
    #     block of /health; retention fires once a DAY, so any staleness budget
    #     would be a false alarm 99.99 % of the time. ---
    "writer-flush": None,
    "retention": None,
}

# The leg whose task is currently running. Set INSIDE the task body, so it is
# task-local by construction (asyncio.create_task copies the context at creation;
# a set() inside the coroutine writes to that task's own copy). This is the one
# implicit mechanism in the watchdog, chosen deliberately: the explicit
# alternative (a per-leg writer proxy) would have to be threaded through every
# frame-handler closure, and touching six handlers to add liveness plumbing is a
# far bigger blast radius than one contextvar read inside writer.add.
_CURRENT_LEG: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "btcq_leg", default=None
)

# Log-sink failures are SWALLOWED (a leg must not die because print() failed on a
# full disk) but never silent: they are counted and surfaced in /health. What is
# swallowed must be counted.
_LOG_DROPS = 0


def _stamped(log):
    """Wrap a log sink so every line carries a UTC timestamp with milliseconds.

    Added 2026-08-04 after a measurement was BLOCKED by its absence: the log had
    0 of 2,123 lines carrying a date, so socket-drop -> reconnect duration could
    not be recovered from it. The gap ledger measures time-since-last-ROW, which
    is a different quantity, and the difference between the two is exactly the
    number the reconnect rail needs to be judged on.

    ISO-8601 UTC with ms, deliberately: the whole store is event-time UTC epoch-ms
    (DESIGN §3), so a log line and a `ts_ms` row can be aligned without a
    conversion step or a timezone assumption. `time.time()` and not the event
    clock, because this stamps when the PROCESS said something -- that is a wall
    -clock fact about the host, and treating it as event time would be the
    category error the replay rail exists to prevent.

    Wraps whatever sink was passed rather than hard-coding print, so a test that
    supplies its own list still gets stamped lines and cannot silently diverge
    from what the daemon writes.
    """
    def _emit(msg: str) -> None:
        t = time.gmtime()
        ms = int((time.time() % 1) * 1000)
        log(f"{time.strftime('%Y-%m-%dT%H:%M:%S', t)}.{ms:03d}Z {msg}")
    return _emit


def _safe_log(log, msg: str) -> None:
    """``log(msg)`` that cannot kill its caller — the 2026-07-23 root cause.

    On a full disk ``print()`` raises ``OSError(28)``. Every leg loop called
    ``log()`` from INSIDE its own ``except`` block with no handler above it, so
    the raise escaped the ``while``, the coroutine finished, and the exception
    was parked in an un-awaited Task — six legs died exactly this way while the
    process stayed alive. Reproduced against this file for _ws_stream (both the
    frame-handler and the transport path), _rest_poll and _aggtrades_loop.
    """
    global _LOG_DROPS
    try:
        log(msg)
    except Exception:  # noqa: BLE001 — a broken sink must never kill a leg
        _LOG_DROPS += 1


def log_drop_count() -> int:
    """How many log lines this process failed to emit (surfaced in /health)."""
    return _LOG_DROPS


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
    # §3c NEW tables. crowding is LONG format — one row per metric name — so new
    # binance futures/data endpoints never need a schema migration.
    """CREATE TABLE IF NOT EXISTS crowding (
        exchange VARCHAR, symbol VARCHAR, ts_ms BIGINT, metric VARCHAR, value DOUBLE)""",
    # dvol/options_chain are Deribit-only, single-instrument feeds — no exchange/
    # symbol columns by design (§3c schema, verbatim).
    "CREATE TABLE IF NOT EXISTS dvol (ts_ms BIGINT, index_price DOUBLE)",
    """CREATE TABLE IF NOT EXISTS options_chain (
        ts_ms BIGINT, name VARCHAR, expiry_ts BIGINT, strike DOUBLE, cp VARCHAR,
        iv DOUBLE, oi DOUBLE, volume DOUBLE, mark_price DOUBLE, underlying DOUBLE)""",
    "CREATE INDEX IF NOT EXISTS idx_trades_symbol_ts ON trades (symbol, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_liquidations_symbol_ts ON liquidations (symbol, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_depth_symbol_ts ON depth_snapshots (symbol, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_funding_symbol_ts ON funding_mark (symbol, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_oi_symbol_ts ON open_interest (symbol, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_crowding_symbol_ts ON crowding (symbol, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_dvol_ts ON dvol (ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_options_chain_ts ON options_chain (ts_ms)",
)

# Column order is the row-tuple contract every normalize_* function follows.
_TABLE_COLUMNS = {
    "trades": ("exchange", "symbol", "trade_id", "ts_ms", "price", "qty", "aggressor_buy"),
    "liquidations": ("exchange", "symbol", "ts_ms", "side", "price", "qty", "notional_usd"),
    "depth_snapshots": ("exchange", "symbol", "ts_ms", "bids", "asks"),
    "funding_mark": ("exchange", "symbol", "ts_ms", "mark", "index", "funding_rate", "next_funding_ts"),
    "open_interest": ("exchange", "symbol", "ts_ms", "oi"),
    "crowding": ("exchange", "symbol", "ts_ms", "metric", "value"),
    "dvol": ("ts_ms", "index_price"),
    "options_chain": (
        "ts_ms", "name", "expiry_ts", "strike", "cp",
        "iv", "oi", "volume", "mark_price", "underlying",
    ),
}

_INSERT_SQL = {
    table: "INSERT INTO {t} VALUES ({q})".format(t=table, q=", ".join("?" * len(cols)))
    for table, cols in _TABLE_COLUMNS.items()
}

# Rotation routing (§3c): the writer routes every row by the UTC day of the row's
# OWN ts_ms — this maps each table to where that ts_ms sits in the row tuple.
_TS_INDEX = {table: cols.index("ts_ms") for table, cols in _TABLE_COLUMNS.items()}


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

    def depth_row(self, ts_ms: int, n_levels: int = 50) -> Optional[tuple]:
        """Current book -> one ``depth_snapshots`` row (top-N, JSON, best-first).

        Top-50 (§3c): the stream IS ``orderbook.50`` — store all of it; truncating
        to 20 was throwing away levels the wire already delivered. The binancef
        leg stays top-20 because ``depth20`` is the whole wire there. Returns None
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


def normalize_binance_aggtrades(payload: list, symbol: str) -> tuple[list[tuple], Optional[int]]:
    """Binance ``/fapi/v1/aggTrades`` REST payload -> (``trades`` rows, next fromId).

    Wire shape (fixture ``binancef_rest_aggtrades``): list of dicts with ``a``
    (aggTradeId — increments gaplessly per symbol), ``p``/``q`` string decimals,
    ``T`` epoch ms, ``m`` isBuyerMaker. The REST path exists because the WS trade
    topics are filtered on this network (§0.2) — REST is not.

    Convention (§0.6): ``m`` true means the BUYER was the maker, i.e. the
    aggressor SOLD -> ``aggressor_buy = not m``. ``trade_id = str(a)``.

    Cursor contract (§3c): the second return value is ``last a + 1`` — the next
    poll's ``fromId``. The tape stays gapless by aggTradeId as long as the caller
    only advances the cursor on a successful poll (on failure: keep it, retry the
    SAME fromId, never skip ahead). An empty payload returns ``None`` (no advance).
    """
    rows: list[tuple] = []
    for t in payload or []:
        rows.append(
            (
                "binancef",
                symbol,
                str(t["a"]),
                int(t["T"]),
                float(t["p"]),
                float(t["q"]),
                not t["m"],  # buyer-is-maker -> SELL aggressor (§0.6)
            )
        )
    next_from_id = int(payload[-1]["a"]) + 1 if payload else None
    return rows, next_from_id


# ---- binance futures/data crowding endpoints (§3c) — long format, one row per
# metric, exchange-stated denominations preserved (coin AND usd for OI hist). ----
def normalize_binance_taker_ls(payload: list, symbol: str) -> list[tuple]:
    """``/futures/data/takerlongshortRatio`` -> ``crowding`` rows.

    Wire shape (fixture ``binancef_rest_taker_ls``): ``buySellRatio`` string
    decimal + epoch-ms ``timestamp`` (5 m buckets; NO symbol field — the caller's
    symbol is trusted, it is the query param that produced the payload).
    Metric name per §3c: ``taker_buy_sell_ratio``.
    """
    return [
        ("binancef", symbol, int(r["timestamp"]), "taker_buy_sell_ratio", float(r["buySellRatio"]))
        for r in payload or []
    ]


def normalize_binance_top_pos_ls(payload: list, symbol: str) -> list[tuple]:
    """``/futures/data/topLongShortPositionRatio`` -> ``crowding`` rows.

    Wire shape (fixture ``binancef_rest_top_pos_ls``): ``longShortRatio`` string
    decimal, ``symbol`` echoed in each row (used when present — provenance from
    the wire beats the caller's argument). Metric: ``top_position_ls_ratio``.
    """
    return [
        (
            "binancef",
            r.get("symbol") or symbol,
            int(r["timestamp"]),
            "top_position_ls_ratio",
            float(r["longShortRatio"]),
        )
        for r in payload or []
    ]


def normalize_binance_global_ls(payload: list, symbol: str) -> list[tuple]:
    """``/futures/data/globalLongShortAccountRatio`` -> ``crowding`` rows.

    Same shape as the top-trader endpoint (fixture ``binancef_rest_global_ls``);
    metric per §3c: ``global_account_ls_ratio``.
    """
    return [
        (
            "binancef",
            r.get("symbol") or symbol,
            int(r["timestamp"]),
            "global_account_ls_ratio",
            float(r["longShortRatio"]),
        )
        for r in payload or []
    ]


def normalize_binance_oi_hist(payload: list, symbol: str) -> list[tuple]:
    """``/futures/data/openInterestHist`` -> ``crowding`` rows (TWO per entry).

    Wire shape (fixture ``binancef_rest_oi_hist``): ``sumOpenInterest`` (COIN) and
    ``sumOpenInterestValue`` (USD). §3c stores BOTH — ``oi_sum_coin`` and
    ``oi_sum_usd`` — because deriving one from the other needs a price we did not
    observe at that timestamp (the no-silent-conversion rule, same as the OI legs).
    """
    rows: list[tuple] = []
    for r in payload or []:
        sym = r.get("symbol") or symbol
        ts = int(r["timestamp"])
        rows.append(("binancef", sym, ts, "oi_sum_coin", float(r["sumOpenInterest"])))
        rows.append(("binancef", sym, ts, "oi_sum_usd", float(r["sumOpenInterestValue"])))
    return rows


def normalize_okx_trade(frame: dict, ct_val: float = OKX_CTVAL) -> list[tuple]:
    """OKX v5 ``trades`` frame -> ``trades`` rows.

    Wire shape (fixture ``okx_trades`` — the SAME frames the JS adapter was
    proven against): ``data`` list with ``px``/``sz``/``side``/``ts`` (numeric
    strings) and ``tradeId``.

    UNIT RAIL (§4b/§3c, honesty-critical): ``sz`` is in CONTRACTS —
    ``qty = sz * ct_val`` (BTC-USDT-SWAP ctVal = 0.01 BTC, fixtures
    ``_okx_ctval_note``; fixture pin: sz 200 -> 2.00 BTC).

    Convention (§0.6 family): OKX ``side`` ('buy'/'sell') is the TAKER
    (aggressor) side — used as-is, NO inversion (Bybit convention, NOT the
    Coinbase maker-side gotcha).
    """
    rows: list[tuple] = []
    for t in frame.get("data") or []:
        rows.append(
            (
                "okx",
                t["instId"],
                str(t["tradeId"]),
                int(t["ts"]),
                float(t["px"]),
                float(t["sz"]) * ct_val,  # CONTRACTS -> coin (§4b unit rail)
                t["side"] == "buy",
            )
        )
    return rows


class OkxBook:
    """Stateful OKX ``books`` book: ``action`` 'snapshot' then 'update' frames.

    Same store-side semantics as :class:`BybitBook` (fixtures ``okx_books_snapshot``
    / ``_update``): an update lists only touched levels, sz ``"0"`` DELETES a level.
    Level tuples are ``[px, sz, deprecated, nOrders]`` — only px/sz are consumed.
    ``checksum``/``seqId`` are deliberately ignored: the only remedy for a checksum
    miss is a resubscribe, and the WS leg already re-subscribes on every reconnect
    (after which OKX resends a full snapshot) — same choice as the JS adapter.

    UNIT RAIL: level ``sz`` is in CONTRACTS -> stored qty = sz * ct_val (0 * ct_val
    is still 0, so tombstones survive the scaling).
    """

    def __init__(self, ct_val: float = OKX_CTVAL) -> None:
        self.ct_val = float(ct_val)
        self.symbol: Optional[str] = None
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

    def apply(self, frame: dict) -> Optional[int]:
        """Apply one ``books`` frame; return the frame's row ts_ms (or None)."""
        arg = frame.get("arg") or {}
        self.symbol = arg.get("instId") or self.symbol
        if frame.get("action") == "snapshot":
            self.bids.clear()  # snapshot replaces outright (stale levels must not survive)
            self.asks.clear()
        last_ts: Optional[int] = None
        for row in frame.get("data") or []:
            for key, book in (("bids", self.bids), ("asks", self.asks)):
                for level in row.get(key) or []:
                    price = float(level[0])
                    qty = float(level[1])
                    if qty == 0.0:
                        book.pop(price, None)  # sz "0" deletes the level (fixture-verified)
                    else:
                        book[price] = qty * self.ct_val  # CONTRACTS -> coin
            if row.get("ts") is not None:
                last_ts = int(row["ts"])
        return last_ts

    def depth_row(self, ts_ms: int, n_levels: int = 50) -> Optional[tuple]:
        """Current book -> one ``depth_snapshots`` row (ex='okx', top-50, §3c)."""
        if self.symbol is None or (not self.bids and not self.asks):
            return None  # never emit an empty book as if it were an observation
        bids = sorted(self.bids.items(), key=lambda kv: -kv[0])[:n_levels]
        asks = sorted(self.asks.items(), key=lambda kv: kv[0])[:n_levels]
        return (
            "okx",
            self.symbol,
            int(ts_ms),
            json.dumps([[p, q] for p, q in bids], separators=(",", ":")),
            json.dumps([[p, q] for p, q in asks], separators=(",", ":")),
        )


def normalize_okx_funding(payload: dict) -> list[tuple]:
    """OKX ``/api/v5/public/funding-rate`` REST payload -> ``funding_mark`` rows.

    Wire shape (fixture ``okx_rest_funding``): status ``code`` is a STRING '0';
    one data row with string decimals. OKX gotcha (proven in terminal-hist.js
    normalizeOkxFunding): ``fundingTime`` is the UPCOMING settlement of the
    displayed rate — that is our ``next_funding_ts``; ``nextFundingTime`` is the
    settlement AFTER that.

    Honesty (§0.7): this endpoint carries NO mark/index price, so those columns
    are stored as NULL — never proxied from another feed into the same row.
    """
    if not payload or payload.get("code") != "0":
        return []
    rows: list[tuple] = []
    for r in payload.get("data") or []:
        rows.append(
            (
                "okx",
                r["instId"],
                int(r["ts"]),
                None,  # mark: not on this endpoint — NULL, never invented (§0.7)
                None,  # index: same
                float(r["fundingRate"]),
                int(r["fundingTime"]),  # the UPCOMING settlement (gotcha above)
            )
        )
    return rows


def normalize_okx_oi(payload: dict) -> list[tuple]:
    """OKX ``/api/v5/public/open-interest`` REST payload -> ``open_interest`` rows.

    UNIT RAIL (§3c, same gotcha as the WS leg): the raw ``oi`` field is in
    CONTRACTS; ``oiCcy`` is the COIN amount — we store oiCcy so OKX OI is
    denominated like every other venue's (contracts would overstate it 100x).
    """
    if not payload or payload.get("code") != "0":
        return []
    return [
        ("okx", r["instId"], int(r["ts"]), float(r["oiCcy"]))  # COIN, not contracts
        for r in payload.get("data") or []
    ]


def _iso_to_ms(iso: str) -> int:
    """Coinbase ISO-8601 UTC timestamp -> epoch ms ('Z' normalized for fromisoformat)."""
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def normalize_coinbase_trades(trades: list) -> list[tuple]:
    """Coinbase ``market_trades`` trade list -> ``trades`` rows, oldest-first.

    Wire shape (fixtures ``coinbase_market_trades_snapshot`` / ``_update`` — the
    SAME frames the JS adapter was proven against): batches arrive NEWEST-first;
    rows are emitted sorted by monotonic numeric trade_id so downstream
    accumulators see time order (the proven JS rule).

    Convention (§0.6, THE Coinbase gotcha): ``side`` is the **MAKER**'s side, not
    the aggressor (verified live: side=BUY prints tick DOWN). Aggressor is the
    INVERSE — ``side == "SELL"`` means a resting ask was lifted by an aggressive
    BUYER -> ``aggressor_buy = True``. Reading ``side`` as the aggressor flips
    the tape and negates CVD.
    """
    parsed = []
    for t in trades or []:
        parsed.append(
            (
                int(t["trade_id"]),  # numeric + monotonic on this feed — sort key
                (
                    "coinbase",
                    t["product_id"],
                    str(t["trade_id"]),
                    _iso_to_ms(t["time"]),
                    float(t["price"]),
                    float(t["size"]),
                    t["side"] == "SELL",  # MAKER side inverted -> aggressor (§0.6)
                ),
            )
        )
    parsed.sort(key=lambda p: p[0])  # oldest -> newest
    return [row for _, row in parsed]


class CoinbaseTape:
    """Stateful Coinbase ``market_trades`` handler (port of the proven JS rules).

    Coinbase re-fires the FULL snapshot on every re-subscribe/reconnect. Seed from
    the FIRST snapshot only; skip later ones so a reconnect never re-dumps the
    whole batch into the store; updates before the seed are ignored (JS rule).
    Updates are deduped by monotonic numeric trade_id across overlapping batches.
    """

    def __init__(self) -> None:
        self.seeded = False
        self.last_trade_id = -1

    def apply(self, frame: dict) -> list[tuple]:
        """One ``market_trades`` frame -> deduped ``trades`` rows (may be [])."""
        if frame.get("channel") != "market_trades":
            return []  # heartbeats/acks: liveness concerns, not rows
        out: list[tuple] = []
        for ev in frame.get("events") or []:
            if ev.get("type") == "snapshot":
                if self.seeded:
                    continue  # reconnect snapshot — already seeded, skip (JS rule)
                self.seeded = True
            elif not self.seeded:
                continue  # wait for the seed snapshot first (JS rule)
            for row in normalize_coinbase_trades(ev.get("trades")):
                trade_id = int(row[2])
                if trade_id <= self.last_trade_id:
                    continue  # dedupe across overlapping batches
                self.last_trade_id = trade_id
                out.append(row)
        return out


def normalize_deribit_dvol(payload: dict) -> list[tuple]:
    """Deribit ``get_index_price?index_name=btcdvol_usdc`` -> ``dvol`` rows.

    Wire shape (fixture ``deribit_rest_dvol``): JSON-RPC envelope; ``usIn`` is
    the server receive time in MICROseconds -> //1000 to epoch ms. DVOL is the
    30-day BTC implied-vol index in VOL POINTS (38.68 == 38.68% annualized) —
    stored as delivered, never /100'd (it is an index level, not a per-strike iv).
    """
    result = (payload or {}).get("result") or {}
    if "index_price" not in result or "usIn" not in (payload or {}):
        return []  # JSON-RPC errors carry no result — no invented row
    return [(int(payload["usIn"]) // 1000, float(result["index_price"]))]


# Deribit option-name month tokens (mirrors terminal-hist.js parseDeribitOptionName
# — the collector must parse names identically to the terminal or the stored chain
# and the rendered chain would disagree about expiries).
_DERIBIT_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_deribit_option_name(name: Any) -> Optional[tuple[int, float, str]]:
    """Parse ``CCY-DDMMMYY-STRIKE-C|P`` -> (expiry_ts_ms, strike, cp) | None.

    expiry = 08:00 UTC on the contract date — the Deribit convention (European
    cash-settled options; same rule as the JS parser, incl. single-digit days
    like 'BTC-6JUL26-54000-P'). Futures ('BTC-25SEP26') and spot pairs fall out
    at the 4-part check and are counted by the caller, never guessed at.
    """
    parts = str(name).split("-")
    if len(parts) != 4:
        return None
    date_tok, cp = parts[1], parts[3].upper()
    if cp not in ("C", "P") or len(date_tok) < 6:  # D MMM YY needs >= 6 chars
        return None
    month = _DERIBIT_MONTHS.get(date_tok[-5:-2].upper())
    if month is None:
        return None
    try:
        day = int(date_tok[:-5])
        year = 2000 + int(date_tok[-2:])
        strike = float(parts[2])
        expiry = datetime(year, month, day, 8, 0, 0, tzinfo=timezone.utc)  # 08:00 UTC
    except ValueError:
        return None
    return int(expiry.timestamp() * 1000), strike, cp


def normalize_deribit_chain(payload: dict) -> tuple[list[tuple], int]:
    """Deribit ``get_book_summary_by_currency`` (kind=option) -> (``options_chain`` rows, skipped).

    IV PERCENT TRAP (DEVELOPMENT.md §5 / §3c DECIMAL rail): ``mark_iv`` arrives
    in PERCENT (fixture: 48.58 for BTC-28AUG26-105000-C) -> stored as the
    DECIMAL ``mark_iv / 100`` (0.4858). A missing/non-numeric mark_iv stores
    NULL but the row is KEPT — PCR/max-pain research consumes oi/volume and
    needs no iv; dropping the row would silently bias those.

    Rows with UNPARSEABLE names are skipped and COUNTED (returned ``skipped``)
    — state what the wire delivered, including what we could not read (§0).
    ``ts_ms`` is the envelope ``usIn`` (µs -> ms): ONE snapshot timestamp for
    the whole chain, so an hourly snapshot groups cleanly by ts_ms.
    """
    result = (payload or {}).get("result")
    if not isinstance(result, list):
        return [], 0
    ts_ms = int(payload["usIn"]) // 1000 if "usIn" in payload else None
    rows: list[tuple] = []
    skipped = 0
    for r in result:
        parsed = parse_deribit_option_name(r.get("instrument_name")) if r else None
        if parsed is None:
            skipped += 1
            continue
        expiry_ts, strike, cp = parsed
        try:
            iv = float(r["mark_iv"]) / 100.0  # PERCENT -> decimal (the /100 rail)
        except (KeyError, TypeError, ValueError):
            iv = None  # kept as NULL — see docstring
        rows.append(
            (
                ts_ms if ts_ms is not None else int(r.get("creation_timestamp") or 0),
                r["instrument_name"],
                expiry_ts,
                strike,
                cp,
                iv,
                float(r.get("open_interest") or 0.0),  # contracts (BTC)
                float(r.get("volume") or 0.0),  # 24h contracts
                float(r.get("mark_price") or 0.0),  # in BTC (Deribit coin-quotes options)
                float(r.get("underlying_price") or 0.0),  # per-expiry synthetic future
            )
        )
    return rows, skipped


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
        log=print,
    ) -> None:
        self._con = con
        self._lock = lock
        self._max_rows = max_rows
        self._log = log
        self._buffers: dict[str, list[tuple]] = {t: [] for t in _INSERT_SQL}
        self._pending = 0
        # Honest ops counter (surfaces in logs; the DB itself is the source of truth).
        self.rows_written: dict[str, int] = {t: 0 for t in _INSERT_SQL}
        # Watchdog seams (see LEG_BUDGET_S). ``leg_sink`` is called with the row
        # count whenever rows are ACCEPTED — the one choke point where a leg's
        # data becomes real. ``last_flush_ok_ms`` / ``flush_failures`` let
        # /health tell the truth about the periodic-flush task itself.
        self.leg_sink = None
        self.last_flush_ok_ms = 0
        self.flush_failures = 0
        self.rows_dropped_error = 0
        self.last_error_drop_ms = 0

    def add(self, table: str, rows: list[tuple]) -> None:
        """Buffer rows for ``table``; auto-flush once >= max_rows are pending."""
        if not rows:
            return
        self._buffers[table].extend(rows)
        self._pending += len(rows)
        # Stamp liveness for the leg whose task is running RIGHT NOW, at the
        # moment the rows are handed over (mirrors the aggTrades "mark BEFORE
        # add" rail: buffered rows ARE handed over). An EMPTY batch never
        # stamps — that is deliberate, and it is exactly what stops a Coinbase
        # heartbeat frame or a deduped crowding bucket from faking liveness.
        if self.leg_sink is not None:
            self.leg_sink(len(rows))
        if self._pending >= self._max_rows:
            self.flush()

    def flush(self) -> int:
        """Write every buffered row; return how many were written."""
        if self._pending == 0:
            self.last_flush_ok_ms = int(time.time() * 1000)
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
        self.last_flush_ok_ms = int(time.time() * 1000)
        return written

    async def run(self, stop_event: asyncio.Event, interval_s: float = FLUSH_INTERVAL_S) -> None:
        """Periodic-flush task (the 500 ms half of the flush contract).

        The flush is guarded: a raising flush (ENOSPC, an invalidated DuckDB
        handle) must not end this task. A dead flush task means every leg keeps
        buffering into RAM while the store silently stops growing — counted in
        ``flush_failures`` and surfaced in /health instead.
        """
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass
            try:
                self.flush()
            except Exception as exc:  # noqa: BLE001 — the flush task must survive
                self.flush_failures += 1
                _safe_log(
                    self._log,
                    f"[collector] writer flush FAILED: {exc!r} (retrying next tick)",
                )


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
# Daily rotation (§3c): a directory of per-UTC-day files, routed by EVENT time. #
# --------------------------------------------------------------------------- #
def utc_day(ts_ms: int) -> str:
    """UTC calendar day ('YYYY-MM-DD') of an epoch-ms timestamp.

    Pure integer arithmetic from the epoch — no local timezone can leak in
    (day files are dataset partitions; a partition boundary that moved with the
    host's tz would silently mis-shard rows).
    """
    return (date(1970, 1, 1) + timedelta(days=int(ts_ms) // _DAY_MS)).isoformat()


_DAY_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # day-file stems, nothing else


# --------------------------------------------------------------------------- #
# §4f Institutional Auction Suite — server-side auction math + levels registry #
# (DESIGN §4f, binding). Everything here is DESCRIPTIVE (§0.1): profiles,      #
# VWAPs and day levels describe what traded; none of it is a signal.           #
# --------------------------------------------------------------------------- #
# The levels registry stores its profile fields (poc/vah/val) at a FIXED $10
# tick (§4f: "bybit leg, tick=10"). Fixed on purpose: registry rows must stay
# comparable across days — the 'naked POC' derivation asks whether a LATER
# day's range revisited an EARLIER day's POC, and that question is only
# well-posed when every day's POC sits on the same price grid. $10 is the grid
# the §4f contract's own /v1/profile example pins for the BTC perp.
LEVELS_TICK = 10.0
LEVELS_FILENAME = "levels.jsonl"
# The registry summarizes ONE venue (§4f: "bybit leg") — bybit is the primary
# WS feed (§0.2) with the fullest tape; mixing venues into one o/h/l/c/profile
# row would blur per-source provenance (§0.7).
LEVELS_EXCHANGE = "bybit"


def _day_bounds_ms(day: str) -> tuple[int, int]:
    """'YYYY-MM-DD' -> [start_ms, end_ms) in UTC — the exact inverse of utc_day
    (pure epoch arithmetic; no host tz can leak into a partition boundary)."""
    lo = (date.fromisoformat(day) - date(1970, 1, 1)).days * _DAY_MS
    return lo, lo + _DAY_MS


def _poc_va(levels: list) -> tuple:
    """(poc, vah, val) from ascending ``(lvl, weight)`` pairs — ProfileStore PARITY.

    This is a deliberate line-for-line port of dashboard/terminal-state.js
    ``ProfileStore.profile()`` / ``valueArea70`` (§4f binding: 70 % value-area
    expansion server-side, "one convention: mirror ProfileStore's"):

    * POC = the max-weight level; ties resolve to the LOWEST price (first
      strict max in the ascending scan) — deterministic.
    * Value area = 70 % EXPANSION FROM POC, one row at a time: compare the
      single next level ABOVE the accepted range vs the single next level
      BELOW and absorb whichever carries more weight — ties expand UPWARD
      (``vu >= vd``); an exhausted side uses a ``-1`` sentinel that always
      loses — until >= 70 % of total weight is inside. VAH/VAL are the extreme
      accepted prices.

    tests/test_collector.py pins the check_terminal group-9 constructed-levels
    case (poc 150 / vah 200 / val 130) against this port, so the JS and Python
    conventions cannot drift apart silently.
    """
    if not levels:
        return None, None, None
    n = len(levels)
    weights = [w for _lvl, w in levels]
    poc_idx = 0
    for i in range(1, n):
        if weights[i] > weights[poc_idx]:
            poc_idx = i
    target = 0.7 * sum(weights)
    covered = weights[poc_idx]
    up, dn = poc_idx + 1, poc_idx - 1
    while covered < target and (up < n or dn >= 0):
        vu = weights[up] if up < n else -1.0
        vd = weights[dn] if dn >= 0 else -1.0
        if vu >= vd:
            covered += vu
            up += 1
        else:
            covered += vd
            dn -= 1
    return levels[poc_idx][0], levels[up - 1][0], levels[dn + 1][0]


def compute_day_levels(
    con: Any,
    day: str,
    tick: float = LEVELS_TICK,
    exchange: str = LEVELS_EXCHANGE,
) -> Optional[dict]:
    """One §4f levels-registry row ``{date, o,h,l,c, poc, vah, val, vol}`` for
    ``day``, computed from the ``trades`` table behind ``con``.

    Levels snap with ``round(price/tick)*tick`` (the §4f grid rule — same as
    /v1/profile) and POC/VAH/VAL use the ProfileStore-parity expansion
    (:func:`_poc_va`). Returns ``None`` when the venue printed no trades that
    day — an absent row is honest; an invented flat row would poison the naked
    derivation (§0.7). The store records one symbol per run (§3), but if a file
    ever carries several, the busiest one is summarized (deterministic tie by
    name) rather than blending symbols into one fake OHLC.
    """
    lo, hi = _day_bounds_ms(day)
    sym_row = con.execute(
        "SELECT symbol FROM trades WHERE exchange = ? AND ts_ms >= ? AND ts_ms < ? "
        "GROUP BY symbol ORDER BY COUNT(*) DESC, symbol LIMIT 1",
        [exchange, lo, hi],
    ).fetchone()
    if sym_row is None:
        return None
    symbol = sym_row[0]
    where = "WHERE exchange = ? AND symbol = ? AND ts_ms >= ? AND ts_ms < ?"
    # Deduped on trade_id for exactly the reason /v1/profile is (see
    # _profile_sql): a reconnect can replay its recent tape, and those rows are
    # byte-identical duplicates of trades already stored. Undeduped they inflated
    # `vol` — and this row is not display-only, it is the PRIOR-DAY reference the
    # value-migration read and the `vs prior-day value` confluence row are built
    # on, so it has to be at least as exact as the display path.
    #
    # POC/VAH/VAL barely move (they are about the relative shape), but `vol` is
    # reported as a number and was 0.2 % high on the busiest leg. Rows already in
    # levels.jsonl keep their old values until `make backfill-levels` recomputes
    # them; that is deliberate — silently rewriting recorded history would be the
    # opposite of the audit trail this file exists to keep.
    dedup = (
        "(SELECT trade_id, ANY_VALUE(ts_ms) AS ts_ms, ANY_VALUE(price) AS price, "
        f"ANY_VALUE(qty) AS qty FROM trades {where} GROUP BY trade_id)"
    )
    o, h, low, c, vol = con.execute(
        # arg_min/arg_max by ts_ms = first/last print of the day (event time).
        f"SELECT arg_min(price, ts_ms), max(price), min(price), "
        f"arg_max(price, ts_ms), SUM(qty) FROM {dedup}",  # noqa: S608 — fixed cols
        [exchange, symbol, lo, hi],
    ).fetchone()
    lvl_rows = con.execute(
        f"SELECT round(price / ?) * ? AS lvl, SUM(qty) FROM {dedup} "  # noqa: S608
        "GROUP BY 1 ORDER BY 1",
        [tick, tick, exchange, symbol, lo, hi],
    ).fetchall()
    poc, vah, val = _poc_va([(r[0], r[1]) for r in lvl_rows])
    return {
        "date": day, "o": o, "h": h, "l": low, "c": c,
        "poc": poc, "vah": vah, "val": val, "vol": vol,
    }


def read_levels_registry(path: Any) -> list[dict]:
    """Parse ``levels.jsonl`` -> day rows, sorted ascending by date.

    A missing file is an EMPTY registry (honest: no recorded day has closed
    yet), not an error. Sorting on read keeps consumers (naked derivation,
    /v1/levels) correct regardless of on-disk append order — the rotation hook
    appends as days close while the backfill script may add older dates.
    """
    p = Path(path)
    if not p.exists():
        return []
    rows = [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows.sort(key=lambda r: r.get("date") or "")
    return rows


def append_levels_row(path: Any, row: dict) -> bool:
    """Append one registry row iff its date is not already recorded.

    Idempotence is the whole contract (§4f): the rotation hook can fire again
    for a day the backfill already wrote (or vice versa) and the registry must
    not grow a duplicate line — one recorded day, one row, forever.
    Returns True iff a line was written.
    """
    p = Path(path)
    if any(r.get("date") == row.get("date") for r in read_levels_registry(p)):
        return False  # already recorded — skip, never duplicate
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return True


def read_gap_ledger(path: Any) -> list[dict]:
    """Parse ``gaps.jsonl`` -> watchdog gap events, in recorded (append) order.

    A missing file is an EMPTY ledger — honest: no watchdog restart has been
    recorded yet, which is the healthy case. Mirrors :func:`read_levels_registry`
    except for the ordering: a gap ledger is an EVENT LOG, so recorded order is
    the meaningful order and it is never re-sorted.
    """
    p = Path(path)
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def append_gap_row(path: Any, row: dict) -> bool:
    """Append one watchdog gap event. Append-ONLY, never deduped or rewritten.

    Deliberately NOT idempotent (unlike :func:`append_levels_row`): two restarts
    of the same leg are two distinct holes in the tape and must both be on the
    record. Existing lines are never touched — the ledger is an audit trail, and
    a rewritten audit trail is worthless. Returns True iff a line was written;
    a failure to write is reported to the caller rather than raised, because a
    ledger problem must never stop a recovery (the DATA is the source of truth
    for where the holes are — ``orderflow._holes_from_ts`` derives coverage/gap
    spans straight from recorded ts_ms; this ledger only EXPLAINS them).
    """
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        return True
    except Exception:  # noqa: BLE001 — recovery outranks bookkeeping
        return False


def derive_naked(rows: list[dict]) -> list[dict]:
    """Add the serve-time ``naked`` flag (§4f: DERIVED at serve time, NEVER
    stored): a day's POC is naked iff NO LATER recorded day's [l, h] range
    contains it — price has not traded back through that level on any later
    recorded day. The newest day is vacuously naked. Derived (not stored)
    because every new recorded day can un-nake an OLD row — a stored flag
    would freeze a claim that later data legitimately falsifies.
    """
    ordered = sorted(rows, key=lambda r: r.get("date") or "")
    out: list[dict] = []
    for i, row in enumerate(ordered):
        poc = row.get("poc")
        naked = poc is not None and not any(
            later.get("l") is not None
            and later.get("h") is not None
            and later["l"] <= poc <= later["h"]
            for later in ordered[i + 1:]
        )
        out.append({**row, "naked": naked})
    return out


class DayFileManager:
    """Owns the open per-day DuckDB connections under a rotation root (§3c).

    Policy, verbatim from the design: a day file is writable while its day is
    today (or later — event timestamps may sit marginally ahead of our clock),
    or while it is yesterday within the 5-minute grace window after UTC
    midnight. Once :meth:`close_expired` closes a day it is IMMUTABLE for the
    rest of this process — it is never reopened for writing, so the HF lifecycle
    can trust that a closed file no longer changes underneath an upload.

    All ``now_ms`` values are passed IN (no hidden ``time.time()``) so rotation
    behavior is deterministic under test.
    """

    def __init__(self, root: Any, grace_ms: int = GRACE_WINDOW_MS) -> None:
        _require_deps(need_ws=False)
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.grace_ms = int(grace_ms)
        self._cons: dict[str, Any] = {}  # day -> open RW connection
        self._closed: set[str] = set()  # days closed THIS session — immutable

    def path_for(self, day: str) -> Path:
        return self.root / f"{day}.duckdb"

    def writable(self, day: str, now_ms: int) -> bool:
        """Is ``day`` a legal write target at wall-clock ``now_ms``? (§3c policy)"""
        if day in self._closed:
            return False  # closed == immutable, even if the clock says otherwise
        today = utc_day(now_ms)
        if day >= today:
            return True  # today, or event-time marginally ahead of our clock
        in_grace = (int(now_ms) % _DAY_MS) < self.grace_ms
        return in_grace and day == utc_day(int(now_ms) - _DAY_MS)  # yesterday, in grace

    def con_for(self, day: str, now_ms: int) -> Optional[Any]:
        """Open (or return the open) connection for ``day``; None if not writable.

        An already-open connection is returned even if the grace window lapsed a
        moment ago — the caller flushes THROUGH it and then ``close_expired``
        seals the day, which is exactly the 'final flush then close' contract.
        """
        con = self._cons.get(day)
        if con is not None:
            return con
        if not self.writable(day, now_ms):
            return None
        con = open_db(self.path_for(day))  # SAME schema as every store (§3c)
        self._cons[day] = con
        return con

    def evict(self, day: str) -> bool:
        """Drop a POISONED connection so the next write reopens the day file.

        DuckDB invalidates a connection when a transaction fails hard (the
        07-23 ENOSPC produced 1 492 commit failures). ``con_for`` hands back a
        cached handle without re-validating it, so one poisoned handle would
        keep failing forever. Evicting turns a permanent poison into a retry
        that can heal itself. The day is NOT marked closed — it is still a legal
        write target; only the handle was bad. Returns True iff a handle was
        dropped.
        """
        con = self._cons.pop(day, None)
        if con is None:
            return False
        try:
            con.close()
        except Exception:  # noqa: BLE001 — an already-broken handle may refuse to close
            pass
        return True

    def levels_path(self) -> Path:
        """``<root>/levels.jsonl`` — the §4f day-levels registry, kept beside
        the day files it summarizes (one store, one registry)."""
        return self.root / LEVELS_FILENAME

    def gaps_path(self) -> Path:
        """``<root>/gaps.jsonl`` — the watchdog gap ledger, kept beside the day
        files whose holes it explains (§0.7: gaps stay gaps; the ledger records
        WHY a hole exists, it never fills one)."""
        return self.root / GAPS_FILENAME

    def close_expired(self, now_ms: int, log=print) -> list[str]:
        """Close every open day whose write window has lapsed; return them."""
        closed: list[str] = []
        for day in sorted(self._cons):
            if not self.writable(day, now_ms):
                con = self._cons.pop(day)
                # §4f rotation hook: the caller's contract is flush-THEN-close,
                # so at this moment the day file is final and the writer's own
                # connection is the last honest view of it — compute the day
                # summary now and append one registry line. Idempotent
                # (append_levels_row skips an already-recorded date) and
                # non-fatal: a summary failure must never stop the close —
                # immutability outranks the registry.
                try:
                    row = compute_day_levels(con, day)
                    if row is not None and append_levels_row(self.levels_path(), row):
                        _safe_log(
                            log,
                            f"[collector] levels registry += {day} (poc {row['poc']}, "
                            f"va [{row['val']}, {row['vah']}], vol {row['vol']}) — §4f",
                        )
                except Exception as exc:  # noqa: BLE001 — registry is best-effort
                    _safe_log(
                        log,
                        f"[collector] levels registry: {day} summary FAILED: "
                        f"{exc!r} — the day still closes (§3c immutability first)",
                    )
                try:
                    con.close()
                except Exception as exc:  # noqa: BLE001 — a poisoned handle may refuse
                    _safe_log(
                        log,
                        f"[collector] day file {day}: close FAILED: {exc!r} — "
                        "the day is sealed anyway (§3c immutability first)",
                    )
                self._closed.add(day)
                closed.append(day)
                _safe_log(log, f"[collector] day file {day} closed — immutable from here (§3c)")
        return closed

    def open_days(self) -> list[str]:
        return sorted(self._cons)

    def local_days(self) -> list[str]:
        """Every day file present on disk (open or closed), sorted ascending."""
        return sorted(
            p.stem for p in self.root.glob("*.duckdb") if _DAY_FILE_RE.match(p.stem)
        )

    def close_all(self) -> None:
        """Shutdown: close every open connection (final flush is the caller's job)."""
        for day in sorted(self._cons):
            self._cons.pop(day).close()


class RotatingWriter:
    """Day-routing batched writer (§3c) — same interface as :class:`BatchWriter`.

    ``add`` splits every batch by the UTC day of each row's OWN ``ts_ms`` (a
    batch straddling midnight lands in two files); ``flush`` writes per (day,
    table) and then lets the manager close lapsed days — so 'final flush, then
    close' holds by construction. Rows for an already-closed day are DROPPED and
    counted (``rows_dropped_closed``), never written: immutability outranks
    completeness for a partition that may already be uploaded.

    ``now_ms`` is injectable for deterministic rotation tests; production uses
    the wall clock (the GRACE decision is about our clock, not event time).
    """

    def __init__(
        self,
        manager: DayFileManager,
        lock: threading.Lock,
        max_rows: int = FLUSH_MAX_ROWS,
        now_ms=None,
        log=print,
    ) -> None:
        self._manager = manager
        self._lock = lock
        self._max_rows = max_rows
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._log = log
        self._buffers: dict[tuple[str, str], list[tuple]] = {}  # (day, table) -> rows
        self._pending = 0
        self.rows_written: dict[str, int] = {t: 0 for t in _INSERT_SQL}
        self.rows_dropped_closed = 0
        # Watchdog seams — see BatchWriter for the contract.
        self.leg_sink = None
        self.last_flush_ok_ms = 0
        self.flush_failures = 0
        # Rows lost to a WRITE ERROR (as opposed to the expected, honest
        # closed-day drop). Non-zero means the store is refusing rows RIGHT NOW
        # — that is the signal that was missing on 07-23, when 1 492 commit
        # failures never reached any health surface.
        self.rows_dropped_error = 0
        self.last_error_drop_ms = 0

    def add(self, table: str, rows: list[tuple]) -> None:
        """Buffer rows routed by EVENT day; auto-flush once >= max_rows pending."""
        if not rows:
            return
        ts_i = _TS_INDEX[table]
        for row in rows:
            self._buffers.setdefault((utc_day(row[ts_i]), table), []).append(row)
        self._pending += len(rows)
        if self.leg_sink is not None:  # per-leg liveness stamp — see BatchWriter.add
            self.leg_sink(len(rows))
        if self._pending >= self._max_rows:
            self.flush()

    def flush(self) -> int:
        """Write every buffered row into its day file; then close lapsed days.

        Every (day, table) entry is isolated: one poisoned day file must not
        strand the whole buffer. On a write error the entry is dropped and
        COUNTED (``rows_dropped_error``, same honesty precedent as
        ``rows_dropped_closed``) and the day's connection is EVICTED so the next
        flush reopens it — DuckDB invalidates a handle after a failed commit, so
        without the evict a single ENOSPC would poison the day forever. Buffer
        reset and ``close_expired`` sit in ``finally``: whatever happens above,
        this writer cannot wedge with an ever-growing buffer that never rotates.
        """
        now = self._now_ms()
        written = 0
        try:
            with self._lock:
                for (day, table) in sorted(self._buffers):
                    buf = self._buffers[(day, table)]
                    if not buf:
                        continue
                    con = self._manager.con_for(day, now)
                    if con is None:  # day already closed — immutable (§3c)
                        self.rows_dropped_closed += len(buf)
                        _safe_log(
                            self._log,
                            f"[collector] DROPPED {len(buf)} {table} row(s) for closed day "
                            f"{day} — arrived after the grace window; the gap stays a gap",
                        )
                        continue
                    try:
                        con.executemany(_INSERT_SQL[table], buf)
                    except Exception as exc:  # noqa: BLE001 — one bad day, not the batch
                        self.rows_dropped_error += len(buf)
                        self.last_error_drop_ms = now
                        self._manager.evict(day)  # poisoned handle -> reopen next flush
                        _safe_log(
                            self._log,
                            f"[collector] DROPPED {len(buf)} {table} row(s) for {day}: "
                            f"{exc!r} — connection evicted; the gap stays a gap",
                        )
                        continue
                    self.rows_written[table] += len(buf)
                    written += len(buf)
        finally:
            self._buffers.clear()
            self._pending = 0
            # Even an idle flush tick must seal lapsed days (a quiet overnight
            # stream would otherwise hold yesterday open past the grace window).
            with self._lock:
                self._manager.close_expired(now, log=self._log)
            self.last_flush_ok_ms = int(time.time() * 1000)
        return written

    async def run(self, stop_event: asyncio.Event, interval_s: float = FLUSH_INTERVAL_S) -> None:
        """Periodic-flush task (the 500 ms half of the flush contract).

        Guarded exactly like :meth:`BatchWriter.run`: this task dying silently
        would stop the store growing while every leg kept buffering into RAM.
        """
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass
            try:
                self.flush()
            except Exception as exc:  # noqa: BLE001 — the flush task must survive
                self.flush_failures += 1
                _safe_log(
                    self._log,
                    f"[collector] writer flush FAILED: {exc!r} (retrying next tick)",
                )


def migrate_legacy(src: Any, dest_root: Any, log=print) -> dict[str, dict[str, int]]:
    """One-shot split of a legacy single-file store into per-day files (§3c).

    Prune-safety creed applied to migration: rows are COPIED per UTC day (via a
    read-only ATTACH), per-table day counts are verified to sum EXACTLY to the
    original's, and the original file is left untouched with a printed ``rm``
    hint — this function never deletes anything.

    Returns ``{day: {table: rows_copied}}``. Raises ``ValueError`` on a missing
    source / a ``.duckdb`` destination / pre-existing target day files (a second
    run would silently double every row — refused, same rule as the archive
    overlap check), and ``RuntimeError`` if the count conservation check fails.
    """
    _require_deps(need_ws=False)
    src = Path(src)
    if not src.is_file():
        raise ValueError(f"legacy store not found (need an existing .duckdb FILE): {src}")
    dest = Path(dest_root)
    if dest.suffix == ".duckdb":
        raise ValueError(f"destination must be a rotation DIRECTORY, not a .duckdb file: {dest}")

    # Pass 1 (read-only): which tables exist, their totals, and which days occur.
    src_con = duckdb.connect(str(src), read_only=True)
    try:
        present = {
            r[0] for r in src_con.execute("SELECT table_name FROM duckdb_tables()").fetchall()
        } & set(_TABLE_COLUMNS)
        totals = {
            t: src_con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 — whitelist
            for t in sorted(present)
        }
        day_indexes: set[int] = set()
        for t in sorted(present):
            day_indexes |= {
                int(r[0])
                for r in src_con.execute(
                    f"SELECT DISTINCT ts_ms // {_DAY_MS} FROM {t}"  # noqa: S608 — whitelist
                ).fetchall()
                if r[0] is not None
            }
    finally:
        src_con.close()

    days = [utc_day(idx * _DAY_MS) for idx in sorted(day_indexes)]
    conflicts = [d for d in days if (dest / f"{d}.duckdb").exists()]
    if conflicts:
        raise ValueError(
            f"target day file(s) already exist under {dest}: {conflicts} — "
            "migrate-legacy is one-shot; a re-run would double-count rows (refused)"
        )

    # Pass 2: copy day by day INSIDE DuckDB (ATTACH read-only — no fetchall of a
    # multi-GB store through Python), counting as we go.
    src_sql = str(src).replace("'", "''")
    per_day: dict[str, dict[str, int]] = {}
    migrated = {t: 0 for t in sorted(present)}
    for idx in sorted(day_indexes):
        day = utc_day(idx * _DAY_MS)
        lo, hi = idx * _DAY_MS, (idx + 1) * _DAY_MS
        con = open_db(dest / f"{day}.duckdb")  # canonical schema + indexes
        try:
            con.execute(f"ATTACH '{src_sql}' AS legacy (READ_ONLY)")
            counts: dict[str, int] = {}
            for t in sorted(present):
                cols = ", ".join(f'"{c}"' for c in _TABLE_COLUMNS[t])
                con.execute(
                    f"INSERT INTO {t} SELECT {cols} FROM legacy.{t} "  # noqa: S608 — whitelist
                    "WHERE ts_ms >= ? AND ts_ms < ?",
                    [lo, hi],
                )
                n = con.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE ts_ms >= ? AND ts_ms < ?",  # noqa: S608
                    [lo, hi],
                ).fetchone()[0]
                counts[t] = n
                migrated[t] += n
            con.execute("DETACH legacy")
        finally:
            con.close()
        per_day[day] = counts
        log(f"[migrate] {day}: " + ", ".join(f"{t}={n}" for t, n in counts.items() if n))

    # Count conservation — the verification that makes the rm hint safe to print.
    if migrated != totals:
        raise RuntimeError(
            f"migration count mismatch — original {totals} vs day files {migrated}; "
            f"the original at {src} is UNTOUCHED, day files under {dest} are suspect"
        )
    log(
        f"[migrate] verified: per-day counts sum to the original for every table "
        f"({totals}). The original is untouched — remove it yourself when satisfied:\n"
        f"[migrate]   rm '{src}'"
    )
    return per_day


# --------------------------------------------------------------------------- #
# PER-LEG WATCHDOG — LegState (the honest record) + LegSupervisor (the rail).  #
# See the LEG_BUDGET_S block for the incident and the design constraints.      #
# --------------------------------------------------------------------------- #
@dataclass
class LegState:
    """One supervised leg's honest record. Every field is observed, none derived.

    Two clocks, kept apart on purpose:

    * ``last_alive_ms`` — a frame was parsed / a poll returned. DIAGNOSTIC ONLY.
      It NEVER decides health. A socket that is open and chatty while producing
      nothing is precisely the failure mode that looked healthy for 40 hours.
    * ``last_data_ms`` — ROWS reached the writer. This is the only basis for a
      staleness verdict.
    """

    name: str
    kind: str  # 'stream' | 'poll' | 'internal'
    budget_s: Optional[float] = None  # None == task-state only (would cry wolf)
    tables: tuple[str, ...] = ()  # which series this leg feeds (for the ledger)
    task: Optional[Any] = None  # asyncio.Task once spawned
    started_ms: int = 0  # spawn OR last restart — start-grace anchor
    last_alive_ms: int = 0
    last_data_ms: int = 0
    rows: int = 0
    restarts: int = 0
    restarts_consecutive: int = 0
    last_restart_ms: int = 0
    last_error: Optional[str] = None
    gap_events: int = 0
    gap_ms_total: int = 0
    give_up_since_ms: Optional[int] = None
    # Last time a GIVEN-UP leg's wedged task was cancelled and re-spawned. Kept
    # separate from last_restart_ms so the re-probe cadence can never be confused
    # with -- or reset -- the restart ladder that produced the give-up.
    last_reprobe_ms: int = 0
    reprobes: int = 0
    # When the supervisor first SAW this leg unhealthy. Exists so the loud line
    # is printed once per incident, not once per 10 s tick — an alarm that spams
    # is an alarm that gets ignored, and the ladder can hold a leg for 300 s.
    alarm_since_ms: Optional[int] = None

    # -- verdict ------------------------------------------------------------ #
    def verdict(self, now_ms: int) -> str:
        """PURE state from the recorded facts. No I/O, no wall clock.

        ``running``   producing inside its budget (or unbudgeted and alive)
        ``starting``  spawned, inside one budget of start-grace, nothing yet
        ``restarting``restarted and has NOT produced since — known-broken, not
                      yet known-fixed. Deliberately NOT ok: "we kicked it" is
                      not "it works".
        ``stale``     alive but silent past its budget (catches reconnect-storm
                      livelock, which task-state alone cannot see)
        ``dead``      the task ended (raised, returned, or was cancelled)
        ``given-up``  restart cap reached — loud forever, operator required
        """
        if self.give_up_since_ms is not None:
            return "given-up"
        if self.task is None:
            return "starting"
        if self.task.done():
            return "dead"
        if self.budget_s is None:
            return "running"  # task-state only: a budget here would cry wolf
        budget_ms = self.budget_s * 1000.0
        produced_since_start = self.last_data_ms >= self.started_ms and self.last_data_ms > 0
        if not produced_since_start:
            # Nothing yet since spawn/restart: one full budget of grace, so a
            # freshly restarted task is never judged before it can act.
            if now_ms - self.started_ms <= budget_ms:
                return "restarting" if self.restarts else "starting"
            return "stale"
        return "stale" if now_ms - self.last_data_ms > budget_ms else "running"

    def data_age_ms(self, now_ms: int) -> int:
        """Time since rows last reached the writer; since spawn if never any."""
        ref = self.last_data_ms if self.last_data_ms else self.started_ms
        return max(0, int(now_ms) - int(ref))

    def to_json(self, now_ms: int) -> dict:
        task = self.task
        if task is None:
            task_state = "unspawned"
        elif not task.done():
            task_state = "running"
        elif getattr(task, "cancelled", lambda: False)():
            task_state = "cancelled"
        else:
            task_state = "finished"
        return {
            "kind": self.kind,
            "state": self.verdict(now_ms),
            "task": task_state,
            "budget_s": self.budget_s,
            "last_data_age_s": round(self.data_age_ms(now_ms) / 1000.0, 1),
            "last_alive_age_s": (
                round((now_ms - self.last_alive_ms) / 1000.0, 1) if self.last_alive_ms else None
            ),
            "rows": self.rows,
            "restarts": self.restarts,
            "restarts_consecutive": self.restarts_consecutive,
            # Attempts made while GIVEN UP. Reported separately from `restarts`
            # because a re-probe is not a restart: it does not advance the ladder
            # and it does not make the leg ok. A rising count with `state` still
            # `given-up` is the signal that something needs a human.
            "reprobes": self.reprobes,
            "last_error": self.last_error,
            "gap_events": self.gap_events,
            "gap_ms_total": self.gap_ms_total,
            "tables": list(self.tables),
        }


class LegSupervisor:
    """Watches every collector leg by SYMPTOM and restarts what it can.

    Detection is deliberately symptom-based, never cause-based (constraint #1):
    a leg is unhealthy when its task ENDED or when it produced NO ROWS for
    longer than that leg type tolerates. Enumerating failure modes is how you
    miss the next one — the 07-23 killer was ``print()`` raising ``OSError(28)``,
    which no failure-mode list would have contained.

    It never fights the existing rails. A WS leg owns its own silent-socket
    watchdog (60 s) and capped backoff (30 s); every stream budget sits above
    60 + 30 = 90 s, so the leg always gets first refusal. ``stop_event`` is the
    first check in every loop, so the supervisor never races cancellation during
    a clean shutdown.

    Recovery is paired with reporting, never silent (constraint #3): a restart
    is logged LOUD, counted in /health, and written to ``gaps.jsonl``. The hole
    it leaves is a REAL hole and stays one (§0.7) — nothing here backfills.
    """

    def __init__(
        self,
        *,
        stop_event: asyncio.Event,
        log=print,
        gaps_path: Any = None,
        writer: Any = None,
        disk_path: Any = None,
        now_ms=None,
        tick_s: float = SUPERVISOR_TICK_S,
    ) -> None:
        self._stop = stop_event
        self._log = log
        self._gaps_path = Path(gaps_path) if gaps_path is not None else None
        self._writer = writer
        self._disk_path = Path(disk_path) if disk_path is not None else None
        self._now = now_ms or (lambda: int(time.time() * 1000))
        self._tick_s = tick_s
        self.legs: dict[str, LegState] = {}
        self._factories: dict[str, Any] = {}
        self.started_ms = self._now()
        self._giveup_logged_ms: dict[str, int] = {}
        self.restarts_total = 0
        self.ledger_failures = 0

    # -- registration ------------------------------------------------------- #
    def spawn(self, name: str, kind: str, factory, budget_s=None, tables=()) -> LegState:
        """Create the leg's task and register it. ``factory`` is a zero-arg
        callable returning a FRESH coroutine — that is the whole reason the
        watchdog can restart a leg at all, and the only structural change the
        rail imposes on ``_run_async``."""
        now = self._now()
        leg = LegState(
            name=name,
            kind=kind,
            budget_s=budget_s,
            tables=tuple(tables),
            started_ms=now,
            last_alive_ms=now,
        )
        self.legs[name] = leg
        self._factories[name] = factory
        leg.task = self._create_task(name, factory)
        return leg

    def _create_task(self, name: str, factory):
        async def _run_leg():
            # Task-local by construction: create_task copies the context, this
            # set() writes into that copy. Nested sync callbacks (frame
            # handlers -> writer.add) see it; the parent never does.
            _CURRENT_LEG.set(name)
            await factory()

        return asyncio.create_task(_run_leg(), name=name)

    # -- heartbeats --------------------------------------------------------- #
    def mark_alive(self, name: str) -> None:
        leg = self.legs.get(name)
        if leg is not None:
            leg.last_alive_ms = self._now()

    def mark_data(self, name: str, n_rows: int) -> None:
        """Rows reached the writer for ``name`` — the ONLY liveness that counts."""
        if n_rows <= 0:
            return  # an empty batch is not evidence of anything
        leg = self.legs.get(name)
        if leg is None:
            return
        now = self._now()
        leg.last_data_ms = now
        leg.last_alive_ms = now
        leg.rows += int(n_rows)

    def mark_alive_current(self) -> None:
        name = _CURRENT_LEG.get()
        if name is not None:
            self.mark_alive(name)

    def mark_rows_current(self, n_rows: int) -> None:
        """Writer seam: attribute rows to the leg whose task is running now."""
        name = _CURRENT_LEG.get()
        if name is not None:
            self.mark_data(name, n_rows)

    # -- inspection --------------------------------------------------------- #
    def tasks(self) -> list:
        """The CURRENT task objects (a restart replaces them) — the shutdown
        rail cancels exactly what is running now."""
        return [leg.task for leg in self.legs.values() if leg.task is not None]

    def unhealthy(self, now_ms: Optional[int] = None) -> list[str]:
        now = self._now() if now_ms is None else now_ms
        return sorted(
            name
            for name, leg in self.legs.items()
            if leg.verdict(now) not in ("running", "starting")
        )

    def ok(self) -> bool:
        return bool(self.snapshot()["ok"])

    def _writer_json(self, now_ms: int) -> Optional[dict]:
        w = self._writer
        if w is None:
            return None
        last_ok = int(getattr(w, "last_flush_ok_ms", 0) or 0)
        age_s = round((now_ms - last_ok) / 1000.0, 1) if last_ok else None
        drop_ms = int(getattr(w, "last_error_drop_ms", 0) or 0)
        dropping_now = bool(drop_ms) and (now_ms - drop_ms) <= WRITER_FLUSH_BUDGET_S * 1000
        if dropping_now:
            # Rows are being LOST right now (this is what 1 492 ENOSPC commit
            # failures looked like from the inside). Legs still stamp liveness —
            # they handed their rows over honestly — so without this check the
            # whole daemon would report ok while writing nothing.
            state = "dropping"
        elif age_s is None or age_s > WRITER_FLUSH_BUDGET_S:
            state = "stale"
        else:
            state = "running"
        return {
            "state": state,
            "last_flush_ok_age_s": age_s,
            "flush_failures": int(getattr(w, "flush_failures", 0) or 0),
            "rows_written": dict(getattr(w, "rows_written", {}) or {}),
            "rows_dropped_closed": int(getattr(w, "rows_dropped_closed", 0) or 0),
            "rows_dropped_error": int(getattr(w, "rows_dropped_error", 0) or 0),
        }

    def _disk_free_bytes(self) -> Optional[int]:
        """Free bytes on the store's volume — the signal that would have warned
        BEFORE the 07-23 ENOSPC instead of 40 hours after it."""
        if self._disk_path is None:
            return None
        try:
            return int(shutil.disk_usage(str(self._disk_path)).free)
        except Exception:  # noqa: BLE001 — a missing path must not break /health
            return None

    def snapshot(self) -> dict:
        """The /health body. Truthful by construction: every claim here is a
        recorded observation, and ``ok`` is a function of them, not a constant."""
        now = self._now()
        legs: dict[str, dict] = {}
        counts = {"running": 0, "starting": 0, "restarting": 0, "stale": 0,
                  "dead": 0, "given-up": 0}
        unhealthy: list[str] = []
        for name in sorted(self.legs):
            leg = self.legs[name]
            payload = leg.to_json(now)
            legs[name] = payload
            counts[payload["state"]] = counts.get(payload["state"], 0) + 1
            if payload["state"] not in ("running", "starting"):
                unhealthy.append(name)
        writer = self._writer_json(now)
        ok = not unhealthy and (writer is None or writer["state"] == "running")
        if ok:
            status = "ok"
        elif counts["given-up"]:
            status = "given-up"
        else:
            status = "degraded"
        return {
            "ok": ok,
            "ts_ms": now,
            "status": status,
            "uptime_s": round((now - self.started_ms) / 1000.0, 1),
            "legs_total": len(self.legs),
            "legs_ok": counts["running"] + counts["starting"],
            "legs_stale": counts["stale"],
            "legs_restarting": counts["restarting"],
            "legs_dead": counts["dead"],
            "legs_given_up": counts["given-up"],
            "unhealthy": unhealthy,
            "restarts_total": self.restarts_total,
            "gap_events_total": sum(leg.gap_events for leg in self.legs.values()),
            "log_drops": log_drop_count(),
            "ledger_failures": self.ledger_failures,
            "disk_free_bytes": self._disk_free_bytes(),
            "writer": writer,
            "legs": legs,
        }

    # -- the loop ----------------------------------------------------------- #
    async def run(self) -> None:
        """Watchdog loop. Returns on ``stop_event`` — shutdown owns the tasks."""
        while not self._stop.is_set():
            await _sleep_or_stop(self._stop, self._tick_s)
            if self._stop.is_set():
                return
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the watchdog outlives its own bugs
                _safe_log(self._log, f"[collector] WATCHDOG tick FAILED: {exc!r}")

    async def check(self) -> None:
        """One supervision pass. Safe to call directly (tests drive it)."""
        if self._stop.is_set():
            return  # shutdown owns the tasks now — never fight cancellation
        now = self._now()
        for name in list(self.legs):
            if self._stop.is_set():
                return
            leg = self.legs[name]
            if leg.give_up_since_ms is not None:
                await self._check_given_up(leg, now)
                continue
            self._decay(leg, now)
            task = leg.task
            if task is None:
                continue
            if task.done():
                # RETRIEVE the exception (constraint #6) — an un-awaited Task
                # parks it and the failure evaporates. Never again.
                leg.last_error = self._epitaph(task)
                reason = "task-died"
                detail = f"task ENDED ({leg.last_error}) after {leg.rows} row(s)"
            elif leg.verdict(now) == "stale":
                reason = "stalled"
                detail = (
                    f"STALLED — no rows for {leg.data_age_ms(now) / 1000.0:.0f}s "
                    f"(budget {leg.budget_s:.0f}s) while the task is still alive"
                )
            else:
                if leg.alarm_since_ms is not None:  # healed on its own rail
                    leg.alarm_since_ms = None
                    _safe_log(
                        self._log,
                        f"[collector] WATCHDOG {leg.name}: producing again — "
                        "recovered on its own reconnect rail, no restart needed",
                    )
                continue
            if leg.alarm_since_ms is None:  # one loud line per incident, not per tick
                leg.alarm_since_ms = now
                _safe_log(
                    self._log,
                    f"[collector] WATCHDOG {leg.name}: {detail} — the gap stays a gap",
                )
            await self._restart(leg, reason, now)

    async def _check_given_up(self, leg: LegState, now_ms: int) -> None:
        """A given-up leg never goes quiet, and is never abandoned.

        Two ways back, and NEITHER of them lies about health:

        1. Its own reconnect rail produced rows again — supervision resumes.
        2. Nothing has arrived for LEG_GIVEUP_REPROBE_S, so the task is presumed
           WEDGED (alive, socket open, zero rows — the state measured on both
           bybit-ws and okx-ws on 2026-08-02) and is cancelled and re-spawned.

        Path 2 does not clear ``give_up_since_ms``. The leg keeps reporting
        ``given-up``, ``/health`` keeps reporting not-ok, and the loud line keeps
        printing until ROWS arrive and path 1 fires. That distinction is the whole
        point: the operator still has to look, but the tape stops bleeding while
        they get there.
        """
        task = leg.task
        if task is not None and task.done():
            # Constraint #6 applies inside give-up too, and on EVERY tick, not
            # only at re-probe time: an un-awaited Task parks its exception and
            # the failure evaporates. Consuming it here bounds that to one tick.
            leg.last_error = self._epitaph(task)
        alive = task is not None and not task.done()
        if alive and leg.last_data_ms > leg.give_up_since_ms:
            leg.give_up_since_ms = None
            leg.restarts_consecutive = 0
            self._giveup_logged_ms.pop(leg.name, None)
            _safe_log(
                self._log,
                f"[collector] WATCHDOG {leg.name}: producing again after give-up "
                "— supervision resumed",
            )
            return
        last = self._giveup_logged_ms.get(leg.name, 0)
        if now_ms - last >= LEG_GIVEUP_LOG_S * 1000:
            self._giveup_logged_ms[leg.name] = now_ms
            _safe_log(
                self._log,
                f"[collector] WATCHDOG {leg.name}: GIVEN UP after "
                f"{leg.restarts_consecutive} consecutive restarts (last error: "
                f"{leg.last_error}) — still not-ok and still needs a human; "
                f"re-probing every {LEG_GIVEUP_REPROBE_S / 60.0:.0f} min so the "
                "hole stops growing. Every second here is a hole in the tape.",
            )
        # -- path 2: the slow re-probe ---------------------------------------- #
        if self._stop.is_set():
            return
        since = leg.last_reprobe_ms or leg.give_up_since_ms
        if now_ms - since < LEG_GIVEUP_REPROBE_S * 1000:
            return
        old = leg.task
        if old is not None and not old.done():
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)
        # (a task that was already done had its exception consumed above)
        # The hole was real for the whole give-up window and is recorded as such
        # BEFORE anything is re-spawned (§0.7) — a re-probe never backfills.
        from_ms = leg.last_data_ms if leg.last_data_ms else leg.started_ms
        gap_ms = max(0, now_ms - from_ms)
        leg.gap_events += 1
        leg.gap_ms_total += gap_ms
        self._record_gap(leg, "giveup-reprobe", from_ms, now_ms, gap_ms)
        leg.reprobes += 1
        leg.last_reprobe_ms = now_ms
        leg.started_ms = now_ms  # start-grace travels with the new task
        leg.task = self._create_task(leg.name, self._factories[leg.name])
        _safe_log(
            self._log,
            f"[collector] WATCHDOG {leg.name}: GIVEN-UP re-probe #{leg.reprobes} — "
            f"wedged task cancelled and re-spawned after "
            f"{gap_ms / 1000.0:.0f}s with no rows. The leg stays GIVEN UP and "
            "/health stays not-ok until rows actually arrive; this is an attempt, "
            "not a recovery, and the missing data stays missing.",
        )

    def _decay(self, leg: LegState, now_ms: int) -> None:
        """A leg healthy for LEG_RESTART_DECAY_S earns a clean ladder — last
        week's venue outage must not count toward this week's cap."""
        if not leg.restarts_consecutive or not leg.last_restart_ms:
            return
        if (
            now_ms - leg.last_restart_ms > LEG_RESTART_DECAY_S * 1000
            and leg.last_data_ms > leg.last_restart_ms
        ):
            leg.restarts_consecutive = 0

    @staticmethod
    def _epitaph(task) -> str:
        """Retrieve the task's outcome so it can NEVER be swallowed again.

        An un-awaited Task parks its exception and the failure evaporates —
        that is the structural root cause of a dead leg inside a live process.
        Calling ``.exception()`` consumes it; we keep the repr on the record.
        """
        try:
            if task.cancelled():
                return "cancelled"
            exc = task.exception()
        except asyncio.CancelledError:
            return "cancelled"
        except Exception as exc:  # noqa: BLE001 — never let bookkeeping raise
            return f"exception-unavailable: {exc!r}"
        return repr(exc) if exc is not None else "returned (loop exited without error)"

    async def _restart(self, leg: LegState, reason: str, now_ms: int) -> None:
        if self._stop.is_set():
            return
        if leg.restarts_consecutive >= LEG_RESTART_CAP:
            leg.give_up_since_ms = now_ms
            self._giveup_logged_ms[leg.name] = now_ms
            _safe_log(
                self._log,
                f"[collector] WATCHDOG {leg.name}: restart cap ({LEG_RESTART_CAP}) "
                f"reached — GIVING UP. Reason: {reason}; last error: {leg.last_error}. "
                "The leg is NOT restarted again and /health reports not-ok until a "
                "human intervenes (a silent auto-heal would hide the real problem).",
            )
            return
        if leg.last_restart_ms:
            idx = min(max(leg.restarts_consecutive - 1, 0), len(LEG_RESTART_BACKOFF_S) - 1)
            wait_ms = LEG_RESTART_BACKOFF_S[idx] * 1000
            if now_ms - leg.last_restart_ms < wait_ms:
                return  # ladder holds: a broken leg must not spin the process

        old = leg.task
        if old is not None and not old.done():
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)

        # The hole is REAL and is recorded before anything is restarted (§0.7).
        from_ms = leg.last_data_ms if leg.last_data_ms else leg.started_ms
        gap_ms = max(0, now_ms - from_ms)
        leg.gap_events += 1
        leg.gap_ms_total += gap_ms
        self._record_gap(leg, reason, from_ms, now_ms, gap_ms)

        leg.restarts += 1
        leg.restarts_consecutive += 1
        leg.last_restart_ms = now_ms
        leg.started_ms = now_ms  # start-grace restarts with the task
        leg.alarm_since_ms = None  # this incident is answered; the next is new
        self.restarts_total += 1
        leg.task = self._create_task(leg.name, self._factories[leg.name])
        _safe_log(
            self._log,
            f"[collector] WATCHDOG {leg.name}: restarted (#{leg.restarts}, "
            f"{leg.restarts_consecutive} consecutive, reason {reason}) — "
            f"{gap_ms / 1000.0:.1f}s of {'/'.join(leg.tables) or 'data'} is MISSING "
            "and stays missing (no backfill, no interpolation)",
        )

    def _record_gap(
        self, leg: LegState, reason: str, from_ms: int, to_ms: int, gap_ms: int
    ) -> None:
        if self._gaps_path is None:
            return
        row = {
            "ts_ms": to_ms,
            "leg": leg.name,
            "kind": leg.kind,
            "reason": reason,
            "from_ms": from_ms,
            "to_ms": to_ms,
            "gap_ms": gap_ms,
            "restart_n": leg.restarts + 1,
            "error": leg.last_error,
            "exchange": leg.name.split("-", 1)[0] if "-" in leg.name else None,
            "tables": list(leg.tables),
            "rows_before": leg.rows,
        }
        if not append_gap_row(self._gaps_path, row):
            self.ledger_failures += 1
            _safe_log(
                self._log,
                f"[collector] WATCHDOG {leg.name}: gap ledger write FAILED — the "
                "restart proceeds; the hole is still in the recorded ts_ms",
            )


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


def bybit_subscribe(symbol: str) -> dict:
    """Bybit v5 public/linear subscribe frame. Args are TOPIC STRINGS."""
    return {
        "op": "subscribe",
        "args": [
            f"publicTrade.{symbol}",
            f"orderbook.50.{symbol}",
            f"tickers.{symbol}",
            f"allLiquidation.{symbol}",
        ],
    }


def okx_subscribe(inst_id: str) -> list:
    """OKX v5 public subscribe frame. Args are OBJECTS, not strings (§4b)."""
    return [{
        "op": "subscribe",
        "args": [
            {"channel": "trades", "instId": inst_id},
            {"channel": "books", "instId": inst_id},
        ],
    }]


def coinbase_subscribe(product_id: str) -> list:
    """Coinbase Advanced Trade: one frame PER channel, and `type`, not `op`."""
    return [
        {"type": "subscribe", "product_ids": [product_id], "channel": "market_trades"},
        {"type": "subscribe", "product_ids": [product_id], "channel": "heartbeats"},
    ]


async def _app_ping_loop(ws, app_ping, stop_event: asyncio.Event) -> None:
    """Send the venue's application-level heartbeat on a real SCHEDULE.

    It used to be sent only from the recv-timeout branch, which meant it fired
    only when the socket had been IDLE for _APP_PING_S. On a busy socket that
    branch never runs: bybit-ws carries ~48 orderbook frames per second (measured
    1,197 frames in 25 s), so a leg subscribed to a depth channel would go its
    entire life without ever sending the heartbeat its venue documents as
    required. Bybit v5 and OKX both specify a client ping on a clock, not a
    clock-since-last-inbound-frame -- the busier the leg, the more certainly the
    old code starved it.

    Raising here is deliberate: the caller watches this task and reconnects. A
    heartbeat that cannot be sent is a socket that is already gone.
    """
    while not stop_event.is_set():
        await _sleep_or_stop(stop_event, _APP_PING_S)
        if stop_event.is_set():
            return
        payload = app_ping if isinstance(app_ping, str) else json.dumps(app_ping)
        await asyncio.wait_for(ws.send(payload), timeout=_WS_SEND_TIMEOUT_S)


async def _ws_stream(
    name: str,
    url: str,
    on_frame,
    *,
    stop_event: asyncio.Event,
    subscribe: Any = None,
    app_ping: Any = None,
    log=print,
    on_alive=None,
) -> None:
    """One resilient WS leg: connect, subscribe, pump frames into ``on_frame``.

    ``subscribe`` may be one dict or a list of dicts (Coinbase wants two channel
    subscriptions). ``app_ping`` may be a dict (JSON-encoded — Bybit's
    ``{"op":"ping"}``) or a plain string sent raw (OKX prescribes literal
    ``'ping'``; its ``'pong'`` reply fails json.loads below and is skipped —
    ignored BY CONSTRUCTION, same as the JS adapter).

    Honesty rail (DESIGN §3): every disconnect/backoff window is a HOLE in the
    recorded series. We log it and move on — no interpolation, no replay-fill.

    ``on_alive`` (watchdog seam) is called once per PARSED frame. It stamps
    ``last_alive_ms`` — a DIAGNOSTIC only. It never decides health: a Coinbase
    heartbeat frame or a Bybit pong would otherwise make a leg that produces no
    rows look alive, which is exactly the illusion this rail exists to break.
    """
    attempt = 0
    while not stop_event.is_set():
        try:
            async with websockets.connect(
                url,
                open_timeout=15,
                close_timeout=5,
                # A leg that runs its OWN application heartbeat must not also run
                # the library's RFC 6455 keepalive. Measured 2026-08-02 in
                # /tmp/btcquant-collector.log: bybit-ws died over and over with
                # `Close(code=1011, reason='keepalive ping timeout')`, and each
                # death started a reconnect storm that then failed the opening
                # handshake until the leg hit the cap. Only the two app_ping legs
                # (bybit, okx) were ever affected; binancef-ws and coinbase-ws,
                # which rely on the library keepalive, stayed up throughout.
                # For these venues liveness is guarded by the app ping below plus
                # the WATCHDOG_S silent-socket rail — belt and braces, not a third
                # mechanism that closes healthy sockets.
                ping_interval=None if app_ping is not None else _WS_PING_INTERVAL_S,
                ping_timeout=None if app_ping is not None else _WS_PING_TIMEOUT_S,
                user_agent_header=_USER_AGENT,
            ) as ws:
                for sub in subscribe if isinstance(subscribe, list) else [subscribe]:
                    if sub is not None:
                        await asyncio.wait_for(
                            ws.send(json.dumps(sub)), timeout=_WS_SEND_TIMEOUT_S
                        )
                attempt = 0  # a successful connect resets the backoff ladder
                last_frame = time.monotonic()
                pinger = None
                if app_ping is not None:
                    pinger = asyncio.create_task(
                        _app_ping_loop(ws, app_ping, stop_event),
                        name=f"{name}-appping",
                    )
                try:
                    while not stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=_APP_PING_S)
                        except asyncio.TimeoutError:
                            # Watchdog: a socket that is "open" but silent for 60 s
                            # is treated as dead — force the reconnect path (§3).
                            # The app heartbeat is NOT sent from here any more: it
                            # is on its own clock, see _app_ping_loop.
                            if time.monotonic() - last_frame > WATCHDOG_S:
                                raise TimeoutError(
                                    f"no frame in {WATCHDOG_S:.0f}s — watchdog reconnect"
                                )
                            continue
                        last_frame = time.monotonic()
                        try:
                            frame = json.loads(raw)
                        except (TypeError, ValueError):
                            continue  # non-JSON frame: skip, never guess at contents
                        if on_alive is not None:
                            on_alive()
                        try:
                            on_frame(frame)
                        except Exception as exc:  # noqa: BLE001 — one bad frame must not kill the leg
                            # _safe_log, not log: on 2026-07-23 print() itself raised
                            # OSError(28) here, escaped to the outer handler and
                            # killed the leg inside a live process.
                            _safe_log(
                                log, f"[collector] {name}: frame handler error: {exc!r}"
                            )
                        if pinger is not None and pinger.done():
                            # The heartbeat died (send timed out, socket gone). Its
                            # exception is retrieved and re-raised HERE so the leg
                            # reconnects instead of drifting on toward the venue's
                            # own idle-disconnect with no heartbeat at all.
                            exc = pinger.exception()
                            raise exc if exc is not None else TimeoutError(
                                "app heartbeat loop exited — reconnect"
                            )
                finally:
                    if pinger is not None and not pinger.done():
                        pinger.cancel()
                        await asyncio.gather(pinger, return_exceptions=True)
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
            _safe_log(
                log,
                f"[collector] {name}: {exc!r} — reconnecting in {delay:.1f}s "
                f"(attempt {attempt}; the gap stays a gap)",
            )
            await _sleep_or_stop(stop_event, delay)


async def _rest_poll(
    name: str,
    fetch,
    on_payload,
    interval_s: float,
    stop_event: asyncio.Event,
    log=print,
    on_alive=None,
) -> None:
    """One resilient REST poll leg. ``fetch`` is a blocking callable (requests)
    run via ``asyncio.to_thread`` so the event loop never blocks on HTTP.
    A failed poll is logged and skipped — the missing sample stays missing.

    ``on_alive`` (watchdog seam) fires after a SUCCESSFUL fetch — diagnostic
    only; the leg's health verdict is decided by rows reaching the writer.
    """
    while not stop_event.is_set():
        try:
            payload = await asyncio.to_thread(fetch)
            if on_alive is not None:
                on_alive()
            on_payload(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed poll must not kill the leg
            # _safe_log: a raising sink here killed binancef-premiumIndex,
            # okx-oi and deribit-dvol on 2026-07-23 (root cause, reproduced).
            _safe_log(log, f"[collector] {name}: poll failed: {exc!r} (the gap stays a gap)")
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


def _http_get_json(url: str, params: Optional[dict] = None) -> Any:
    """Blocking GET returning parsed JSON (data.py idiom: polite UA, 10 s timeout)."""
    resp = requests.get(url, params=params, timeout=10.0, headers={"User-Agent": _USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def _fetch_binance_aggtrades(symbol: str, from_id: Optional[int]) -> list:
    """Blocking GET ``/fapi/v1/aggTrades``; the first poll (no cursor) seeds from
    the most recent trades, every later poll resumes gaplessly at ``fromId``."""
    params: dict[str, Any] = {"symbol": symbol, "limit": 1000}
    if from_id is not None:
        params["fromId"] = from_id
    return _http_get_json(f"{_BINANCE_FAPI}/fapi/v1/aggTrades", params)


# The four §3c crowding endpoints share one shape family; limit=2 gives one bucket
# of overlap so a slow poll never loses the boundary sample (the exact-ts
# downsampler gate in the daemon dedupes the overlap).
_CROWDING_ENDPOINTS = (
    ("takerlongshortRatio", normalize_binance_taker_ls),
    ("topLongShortPositionRatio", normalize_binance_top_pos_ls),
    ("globalLongShortAccountRatio", normalize_binance_global_ls),
    ("openInterestHist", normalize_binance_oi_hist),
)


def _fetch_binance_crowding(endpoint: str, symbol: str) -> list:
    """Blocking GET one ``/futures/data/<endpoint>`` crowding payload (5 m buckets)."""
    return _http_get_json(
        f"{_BINANCE_FAPI}/futures/data/{endpoint}",
        {"symbol": symbol, "period": "5m", "limit": 2},
    )


def _fetch_okx_funding(inst_id: str) -> dict:
    """Blocking GET OKX ``/api/v5/public/funding-rate``."""
    return _http_get_json(f"{_OKX_REST}/api/v5/public/funding-rate", {"instId": inst_id})


def _fetch_okx_oi(inst_id: str) -> dict:
    """Blocking GET OKX ``/api/v5/public/open-interest``."""
    return _http_get_json(f"{_OKX_REST}/api/v5/public/open-interest", {"instId": inst_id})


def _fetch_deribit_dvol() -> dict:
    """Blocking GET Deribit DVOL index (keyless, CORS-open — §4d empirical)."""
    return _http_get_json(
        f"{_DERIBIT_REST}/public/get_index_price", {"index_name": "btcdvol_usdc"}
    )


def _fetch_deribit_chain(currency: str) -> dict:
    """Blocking GET the full Deribit option book summary (hourly snapshot, §3c)."""
    return _http_get_json(
        f"{_DERIBIT_REST}/public/get_book_summary_by_currency",
        {"currency": currency, "kind": "option"},
    )


async def _aggtrades_loop(
    symbol: str, writer, stop_event: asyncio.Event, log=print, on_alive=None
) -> None:
    """Binance ``aggTrades`` REST poll (5 s) with a gapless ``fromId`` cursor (§3c).

    The cursor advances to ``last a + 1`` ONLY after a successful poll+normalize;
    any failure keeps it where it was so the next attempt re-fetches the same
    range — the tape never skips ahead over an error. Two honesty guards sit on
    the wire's id sequence (§0.6/§0.7 — record what actually arrived, once):

    * **Dedupe**: ids at-or-below the highest id already handed to the writer
      are dropped (and the drop is logged). A retried seed poll (cursor still
      None after a flush error), or a server re-serving a range, would otherwise
      double-print the tape — duplicate trades silently inflate CVD/volume.
      ``last_id`` is marked BEFORE ``writer.add`` because BatchWriter buffers
      rows before its flush can raise: buffered rows ARE handed over.
    * **Gap**: ids resuming AHEAD of the cursor are LOGGED as a gap, never
      papered over — the missing prints stay missing (no second source
      back-fills them; §0.7). The cursor also never moves backwards, so a
      stale/re-served batch cannot regress it into a refetch loop.
    """
    # RESUME, do not re-seed. A restart that starts at the live edge throws away
    # every print since the leg died, and the venue serves that history happily.
    cursor: Optional[int] = _AGGTRADES_CURSOR.get(symbol)
    last_id: Optional[int] = None  # highest aggTradeId ever handed to the writer
    if cursor is not None:
        # Bounded catch-up. One bare poll (weight 20) reads the live edge; if the
        # backlog exceeds the ceiling the leg jumps forward and SAYS SO with the
        # count. The hole stays a hole (§0.7) — this records it, never fills it.
        try:
            edge_payload = await asyncio.to_thread(_fetch_binance_aggtrades, symbol, None)
            edge = max((int(r["a"]) for r in edge_payload), default=None)
            if edge is not None and edge - cursor > _AGGTRADES_SEEK_BACK_MAX:
                skipped = edge - cursor
                _safe_log(
                    log,
                    f"[collector] binancef-aggTrades: backlog {skipped:,} aggTrade id(s) exceeds the "
                    f"{_AGGTRADES_SEEK_BACK_MAX:,} seek-back ceiling — SKIPPING to the live "
                    f"edge at id {edge}. Those {skipped:,} print(s) are MISSING and stay "
                    "missing (no backfill, no interpolation).",
                )
                cursor = edge
                _AGGTRADES_CURSOR[symbol] = cursor
            else:
                _safe_log(
                    log,
                    f"[collector] binancef-aggTrades: resuming at id {cursor}"
                    + (f" ({edge - cursor:,} id(s) of backlog to catch up)" if edge else ""),
                )
        except Exception as exc:  # noqa: BLE001 — a failed probe must not kill the leg
            # Probe failed: keep the cursor and let the normal loop resume from it.
            # Worst case the first poll is a large catch-up, which is bounded by
            # the 1000-row page anyway.
            _safe_log(log, f"[collector] binancef-aggTrades: seek-back probe failed: {exc!r} (cursor kept)")
    while not stop_event.is_set():
        try:
            payload = await asyncio.to_thread(_fetch_binance_aggtrades, symbol, cursor)
            if on_alive is not None:
                on_alive()
            rows, next_from_id = normalize_binance_aggtrades(payload, symbol)
            if rows:
                first_id = min(int(r[2]) for r in rows)
                if cursor is not None and first_id > cursor:
                    _safe_log(
                        log,
                        f"[collector] binancef-aggTrades: id GAP — expected {cursor}, "
                        f"wire resumed at {first_id} ({first_id - cursor} aggTrade "
                        "id(s) missing; the gap stays a gap)",
                    )
                if last_id is not None:
                    fresh = [r for r in rows if int(r[2]) > last_id]
                    if len(fresh) != len(rows):
                        _safe_log(
                            log,
                            f"[collector] binancef-aggTrades: deduped "
                            f"{len(rows) - len(fresh)} re-served row(s) <= id {last_id}",
                        )
                    rows = fresh
            if rows:
                last_id = max(int(r[2]) for r in rows)  # mark BEFORE add (docstring)
                writer.add("trades", rows)
            if next_from_id is not None and (cursor is None or next_from_id > cursor):
                cursor = next_from_id  # advance ONLY on success — never skip ahead
                _AGGTRADES_CURSOR[symbol] = cursor   # survive a leg restart
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed poll must not kill the leg
            # _safe_log: the raising sink here is what killed binancef-aggTrades
            # on 2026-07-23 — 07-25 recorded ZERO binancef trades because of it.
            _safe_log(log, f"[collector] binancef-aggTrades: poll failed: {exc!r} (cursor kept)")
        await _sleep_or_stop(stop_event, _AGGTRADES_POLL_S)


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
        try:
            with lock:
                for table in _TABLE_COLUMNS:
                    con.execute(f"DELETE FROM {table} WHERE ts_ms < ?", [cutoff])
            _safe_log(log, f"[collector] retention: pruned rows older than {retention_days}d")
        except Exception as exc:  # noqa: BLE001 — a failed prune must not kill the leg
            _safe_log(log, f"[collector] retention: prune FAILED: {exc!r} (retrying tomorrow)")
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


class _Archived(Exception):
    """A requested range predates the oldest LOCAL day file (§3c) — HTTP 410.

    Carries the honest redirect: the data is not gone, it lives on HF; the hint
    is the exact hive-style partition path the lifecycle uploads to.
    """

    def __init__(self, day: str, table: str) -> None:
        self.day = day
        self.table = table
        super().__init__(f"day {day} archived")

    def body(self) -> dict:
        return {
            "error": "archived",
            "hint": f"hf://datasets/{_HF_DATASET}/data/date={self.day}/{self.table}.parquet",
        }


def _bounded_limit(qs: dict[str, str]) -> int:
    """The shared limit contract: default 1000, hard cap 10000 (paged replay)."""
    return max(1, min(int(qs.get("limit", _API_DEFAULT_LIMIT)), _API_MAX_LIMIT))


def _expand_symbol(symbol: str) -> list[str]:
    """CANONICAL symbol id -> the venue-native id set the daemon records under.

    THE BYOD replay fix (the 643d3be caveat): venues store their NATIVE ids —
    bybit/binancef 'BTCUSDT', okx 'BTC-USDT-SWAP', coinbase 'BTC-USD' — so a
    query for the canonical 'BTCUSDT' used to silently return only the same-id
    venues and lose the okx+coinbase legs from replay. The expansion reuses
    :func:`_symbol_legs`, the SAME derivation ``run()`` wires the venue legs
    with (one mapping, shared, never duplicated), so recorder and replay can
    never disagree about which native id a canonical symbol maps to.

    A symbol containing '-' is an EXPLICIT venue-native id (okx/coinbase
    style) and is returned alone, verbatim: a query for 'BTC-USDT-SWAP' still
    matches only okx rows — no silent aliasing of an explicit native request
    (the caller named one venue's tape; widening it would hand back
    cross-venue rows they did not ask for).

    The deribit leg is deliberately absent: its tables (dvol/options_chain)
    carry no symbol column (§3c schema), so there is no deribit-native id a
    symbol filter could ever match. Rows keep their STORED native symbol in
    every response (§0.7 — recorded data is never rewritten to the canonical).
    """
    if "-" in symbol:
        return [symbol]  # explicit native id — never silently aliased (above)
    legs = _symbol_legs(symbol)
    # dict.fromkeys: order-stable dedupe, canonical id first.
    return list(dict.fromkeys([symbol, legs["okx"], legs["coinbase"]]))


def _run_bounded_select(con: "duckdb.DuckDBPyConnection", table: str, qs: dict, limit: int) -> list:
    """One bounded, parameterized read on one connection (caller holds any lock).

    Params: ``symbol``, ``start_ms``, ``end_ms`` (inclusive bounds). All values
    go through ``?`` placeholders; table/column names come only from whitelists.
    ``symbol`` takes the canonical id and matches the whole venue-native id set
    (:func:`_expand_symbol`) — every row-serving route shares this SELECT, so
    /v1/trades, /v1/depth, /v1/liquidations, /v1/funding and /v1/oi all widen
    (or stay native-narrow) identically.
    """
    cols = _TABLE_COLUMNS[table]
    select_cols = ", ".join(f'"{c}"' for c in cols)  # "index" needs the quotes
    sql = f"SELECT {select_cols} FROM {table}"  # noqa: S608 — table from whitelist
    clauses: list[str] = []
    params: list[Any] = []
    if "symbol" in qs and "symbol" in cols:
        ids = _expand_symbol(qs["symbol"])
        clauses.append("symbol IN (" + ", ".join("?" * len(ids)) + ")")
        params.extend(ids)
    if "start_ms" in qs:
        clauses.append("ts_ms >= ?")
        params.append(int(qs["start_ms"]))
    if "end_ms" in qs:
        clauses.append("ts_ms <= ?")
        params.append(int(qs["end_ms"]))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY ts_ms ASC LIMIT ?"
    params.append(limit)
    return con.execute(sql, params).fetchall()


def _query_table(
    con: "duckdb.DuckDBPyConnection",
    lock: threading.Lock,
    table: str,
    qs: dict[str, str],
) -> dict:
    """Legacy single-file read — the original BYOD contract, unchanged."""
    cols = _TABLE_COLUMNS[table]
    limit = _bounded_limit(qs)
    with lock:
        rows = _run_bounded_select(con, table, qs, limit)
    # depth bids/asks stay JSON *strings* here (stored form) — the client parses.
    return {"table": table, "n": len(rows), "rows": [dict(zip(cols, r)) for r in rows]}


def _rotation_day_cons(manager: DayFileManager, days: list[str]):
    """Yield ``(day, con, borrowed)`` for local days — the manager's own RW
    connection where one is open (today/yesterday), else a fresh READ-ONLY
    handle (closed days are immutable, so a read-only open is always safe).
    The caller holds the shared lock for the whole walk: manager connections are
    the writer's, and DuckDB refuses a second same-process handle on a file the
    manager still has open — borrowing under the lock is the only correct path.
    """
    for day in days:
        con = manager._cons.get(day)  # noqa: SLF001 — manager and API are one module
        if con is not None:
            yield day, con, True
        else:
            ro = duckdb.connect(str(manager.path_for(day)), read_only=True)
            try:
                yield day, ro, False
            finally:
                ro.close()


def _cons_for_range(store: Any, rotation: bool, start_ms, end_ms, table: str = "trades"):
    """Yield the connection(s) covering [start_ms, end_ms] — THE union seam.

    Rotation mode walks the LOCAL day files whose UTC day intersects the range
    (ascending — day files partition by ts_ms, so per-file ts order composes
    into global ts order) and raises ``_Archived`` (-> HTTP 410 + the hf://
    hint) when the range starts before the oldest local day: that data was
    uploaded + pruned by the HF lifecycle, and an empty 200 would be a lie.
    Legacy single-file mode yields the one shared connection. Every §3c/§4f
    read endpoint goes through this generator so range semantics cannot drift
    between /v1/trades and /v1/profile//v1/vwap. Caller MUST hold the shared
    lock for the whole walk (see _rotation_day_cons).
    """
    if not rotation:
        yield store
        return
    days = store.local_days()
    if start_ms is not None and days and utc_day(start_ms) < days[0]:
        raise _Archived(utc_day(start_ms), table)
    lo_day = utc_day(start_ms) if start_ms is not None else None
    hi_day = utc_day(end_ms) if end_ms is not None else None
    wanted = [
        d for d in days
        if (lo_day is None or d >= lo_day) and (hi_day is None or d <= hi_day)
    ]
    for _day, con, _borrowed in _rotation_day_cons(store, wanted):
        yield con


def _query_rotation(
    manager: DayFileManager,
    lock: threading.Lock,
    table: str,
    qs: dict[str, str],
) -> dict:
    """Rotation-mode read: ATTACH-free union over the LOCAL day files covering
    [start_ms, end_ms] (§3c, via _cons_for_range). Same params/shapes as the
    legacy read — the BYOD contract is UNCHANGED; only the storage moved.
    """
    cols = _TABLE_COLUMNS[table]
    limit = _bounded_limit(qs)
    start_ms = int(qs["start_ms"]) if "start_ms" in qs else None
    end_ms = int(qs["end_ms"]) if "end_ms" in qs else None
    rows: list = []
    with lock:
        for con in _cons_for_range(manager, True, start_ms, end_ms, table):
            remaining = limit - len(rows)
            if remaining <= 0:
                break
            rows.extend(_run_bounded_select(con, table, qs, remaining))
    return {"table": table, "n": len(rows), "rows": [dict(zip(cols, r)) for r in rows]}


# --------------------------------------------------------------------------- #
# §4f endpoints — /v1/profile, /v1/vwap, /v1/levels (DESIGN §4f, binding).     #
# All SQL is parameterized; column/table names are fixed strings. Empirical    #
# basis for doing this server-side at all: DuckDB aggregates a full 2.87 M-    #
# trade day into a 174-level profile in ~19 ms (§4f probes 2026-07-10).       #
# --------------------------------------------------------------------------- #
def _require_pos_finite(value: float, name: str) -> float:
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")
    return value


def _profile_params(qs: dict[str, str]) -> tuple[str, str, int, int, float, list[float]]:
    """Validate /v1/profile params (§4f: 400 on garbage, never a half-answer).

    Raises ValueError — the handler's existing 400 path — on anything unsound:
    missing symbol/range, non-numeric values, tick <= 0, end < start, or a
    buckets_usd list that is not positive finite numbers.
    """
    if "symbol" not in qs:
        raise ValueError("symbol is required")
    if "start_ms" not in qs or "end_ms" not in qs:
        raise ValueError("start_ms and end_ms are required")
    start_ms, end_ms = int(qs["start_ms"]), int(qs["end_ms"])
    if end_ms < start_ms:
        raise ValueError(f"end_ms {end_ms} < start_ms {start_ms}")
    tick = _require_pos_finite(float(qs.get("tick", "10")), "tick")
    buckets: list[float] = []
    if qs.get("buckets_usd"):
        buckets = sorted(
            {
                _require_pos_finite(float(tok), "buckets_usd")
                for tok in qs["buckets_usd"].split(",")
                if tok.strip()
            }
        )
        if not buckets:
            raise ValueError("buckets_usd given but empty")
    return qs["symbol"], qs.get("exchange", "bybit"), start_ms, end_ms, tick, buckets


def _profile_sql(n_buckets: int, n_symbols: int) -> str:
    """The per-day-file profile aggregation (§4f). Levels snap with
    ``round(price/tick)*tick`` (the contract's grid rule); per-level Σp·q
    rides along so the range VWAP comes from the SAME single scan (summing
    exact per-trade products grouped by level loses nothing).
    Bucket columns b0..bn split per-level volume by trade notional: bucket =
    the smallest threshold >= price*qty, with one OVERFLOW column (bn) last
    for notionals above every threshold.
    ``n_symbols`` sizes the ``symbol IN (…)`` placeholders: the exchange param
    still selects ONE leg; the canonical-symbol expansion only widens which
    stored id that leg is found under (:func:`_expand_symbol`).
    """
    cols = [
        "round(price / ?) * ? AS lvl",
        "SUM(CASE WHEN aggressor_buy THEN qty ELSE 0 END) AS buy_vol",
        "SUM(CASE WHEN NOT aggressor_buy THEN qty ELSE 0 END) AS sell_vol",
        "COUNT(*) AS prints",
        "SUM(price * qty) AS pq",
    ]
    if n_buckets:
        cols.append("SUM(CASE WHEN price * qty <= ? THEN qty ELSE 0 END) AS b0")
        for i in range(1, n_buckets):
            cols.append(
                "SUM(CASE WHEN price * qty > ? AND price * qty <= ? "
                f"THEN qty ELSE 0 END) AS b{i}"
            )
        cols.append(f"SUM(CASE WHEN price * qty > ? THEN qty ELSE 0 END) AS b{n_buckets}")
    # DEDUPED on trade_id before aggregation. Measured 2026-08-03 on the
    # 2026-08-01 day file: 971 binancef trade_ids and 68 coinbase trade_ids each
    # appear TWICE, and in every single case the two rows carry a byte-identical
    # (ts_ms, price, qty, aggressor_buy) — so they are the same trade written
    # twice (a reconnect replaying its recent tape), never two trades that
    # collided on an id. Left raw, SUM(qty) counted each of them, inflating the
    # displayed profile volume by 0.198 % (binancef) and 0.098 % (coinbase).
    #
    # Verified precondition: zero NULL and zero empty trade_id across all four
    # venues in the store. That matters — a NULL id would collapse every
    # unidentified trade into ONE row and silently delete data, which is the
    # opposite of the intent.
    #
    # ANY_VALUE, not DISTINCT ON: the payload is identical by construction (and
    # asserted by test), so the choice cannot change a number, and ANY_VALUE
    # needs no ORDER BY to be well-defined. The research path (orderflow.py)
    # already dedupes on (exchange, symbol, trade_id); this brings the display
    # path to the same standard rather than inventing a second one.
    inner = (
        "SELECT trade_id, ANY_VALUE(ts_ms) AS ts_ms, ANY_VALUE(price) AS price, "
        "ANY_VALUE(qty) AS qty, ANY_VALUE(aggressor_buy) AS aggressor_buy "
        "FROM trades "
        "WHERE exchange = ? AND symbol IN (" + ", ".join("?" * n_symbols) + ") "
        "AND ts_ms >= ? AND ts_ms <= ? GROUP BY trade_id"
    )
    return "SELECT " + ", ".join(cols) + " FROM (" + inner + ") GROUP BY 1"


def _bucket_params(buckets: list[float]) -> list[float]:
    """Positional params for _profile_sql's bucket CASEs, in SQL text order."""
    if not buckets:
        return []
    params = [buckets[0]]
    for i in range(1, len(buckets)):
        params += [buckets[i - 1], buckets[i]]
    params.append(buckets[-1])
    return params


def _profile_endpoint(store: Any, lock: threading.Lock, rotation: bool, qs: dict) -> dict:
    """GET /v1/profile (§4f): tick-exact profile over [start_ms, end_ms].

    Aggregation runs per day file and merges by level — exact, because every
    output field is a sum (or count) over disjoint trade sets. POC/VAH/VAL use
    the ProfileStore-parity expansion (_poc_va); vwap/sigma come from the same
    range (volume-weighted mean / standard deviation of trade price).
    """
    symbol, exchange, start_ms, end_ms, tick, buckets = _profile_params(qs)
    symbol_ids = _expand_symbol(symbol)  # canonical widens; native stays narrow
    sql = _profile_sql(len(buckets), len(symbol_ids))
    params = [tick, tick, *_bucket_params(buckets), exchange, *symbol_ids, start_ms, end_ms]
    n_bucket_cols = len(buckets) + 1 if buckets else 0
    acc: dict[float, list[float]] = {}
    with lock:
        for con in _cons_for_range(store, rotation, start_ms, end_ms):
            for row in con.execute(sql, params).fetchall():
                cur = acc.get(row[0])
                if cur is None:
                    acc[row[0]] = list(row[1:])
                else:  # same level in two day files -> sums merge exactly
                    for i, v in enumerate(row[1:]):
                        cur[i] += v
        levels: list[dict] = []
        weights: list[tuple[float, float]] = []
        total_vol = 0.0
        pq = 0.0
        for lvl in sorted(acc):
            vals = acc[lvl]
            buy, sell = vals[0], vals[1]
            entry: dict[str, Any] = {
                "lvl": lvl, "buy_vol": buy, "sell_vol": sell, "prints": int(vals[2]),
            }
            for i in range(n_bucket_cols):
                entry[f"b{i}"] = vals[4 + i]
            levels.append(entry)
            weights.append((lvl, buy + sell))
            total_vol += buy + sell
            pq += vals[3]
        vwap = sigma = None
        if total_vol > 0:
            vwap = pq / total_vol
            sigma = _range_sigma(
                store, rotation, exchange, symbol_ids, start_ms, end_ms, vwap, total_vol
            )
    poc, vah, val = _poc_va(weights)
    return {
        "levels": levels, "poc": poc, "vah": vah, "val": val,
        "total_vol": total_vol, "vwap": vwap, "sigma": sigma,
    }


def _range_sigma(
    store: Any,
    rotation: bool,
    exchange: str,
    symbol_ids: list[str],
    start_ms: Optional[int],
    end_ms: Optional[int],
    vwap: float,
    total_qty: float,
) -> float:
    """Volume-weighted σ around ``vwap`` — the SECOND pass of a two-pass batch.

    WHY two passes: the one-scan E[p²]−E[p]² form is catastrophically
    ill-conditioned at BTC price scale — p² ≈ 4e9 while σ² ≈ tens, so the
    subtraction burns ~8 of a float64's ~16 digits and misses the batch
    formula by ~1e-8 (measured). Σq·(p−vwap)² keeps every term at σ's own
    scale, matching the batch definition (and the terminal AnchoredVwap's
    Welford stream) to ~1e-12. A day scan costs ~19 ms (§4f probes), so the
    extra pass is free. Caller holds the shared lock, guarantees
    ``total_qty > 0``, and passes the ALREADY-expanded ``symbol_ids``
    (:func:`_expand_symbol`) so both passes filter identically by construction.
    """
    sql = (
        "SELECT COALESCE(SUM(qty * (price - ?) * (price - ?)), 0) FROM trades "
        "WHERE exchange = ? AND symbol IN (" + ", ".join("?" * len(symbol_ids)) + ") "
        "AND ts_ms >= ?"
    )
    params: list[Any] = [vwap, vwap, exchange, *symbol_ids, start_ms]
    if end_ms is not None:
        sql += " AND ts_ms <= ?"
        params.append(end_ms)
    m2 = 0.0
    for con in _cons_for_range(store, rotation, start_ms, end_ms):
        m2 += con.execute(sql, params).fetchone()[0]
    return math.sqrt(max(0.0, m2 / total_qty))  # clamp: float round-off only


def _vwap_params(qs: dict[str, str]) -> tuple[str, str, int, Optional[int]]:
    """Validate /v1/vwap params — same 400-on-garbage discipline as _profile_params."""
    if "symbol" not in qs:
        raise ValueError("symbol is required")
    if "anchor_ms" not in qs:
        raise ValueError("anchor_ms is required")
    anchor_ms = int(qs["anchor_ms"])
    end_ms = int(qs["end_ms"]) if "end_ms" in qs else None
    if end_ms is not None and end_ms < anchor_ms:
        raise ValueError(f"end_ms {end_ms} < anchor_ms {anchor_ms}")
    return qs["symbol"], qs.get("exchange", "bybit"), anchor_ms, end_ms


def _vwap_endpoint(store: Any, lock: threading.Lock, rotation: bool, qs: dict) -> dict:
    """GET /v1/vwap (§4f): anchored VWAP ± σ over [anchor_ms, end_ms].

    ``end_ms`` omitted means "through the newest LOCAL trade" — the day walk
    simply has no upper bound, so every local file from the anchor day onward
    contributes. Empty range -> nulls + n=0 (an honest nothing, never a NaN
    smuggled through JSON).
    """
    symbol, exchange, anchor_ms, end_ms = _vwap_params(qs)
    symbol_ids = _expand_symbol(symbol)  # exchange still selects the leg (§4f)
    sql = (
        "SELECT COUNT(*), COALESCE(SUM(qty), 0), COALESCE(SUM(price * qty), 0) "
        "FROM trades WHERE exchange = ? AND symbol IN ("
        + ", ".join("?" * len(symbol_ids)) + ") AND ts_ms >= ?"
    )
    params: list[Any] = [exchange, *symbol_ids, anchor_ms]
    if end_ms is not None:
        sql += " AND ts_ms <= ?"
        params.append(end_ms)
    n = 0
    q = pq = 0.0
    with lock:
        for con in _cons_for_range(store, rotation, anchor_ms, end_ms):
            c_n, c_q, c_pq = con.execute(sql, params).fetchone()
            n += c_n
            q += c_q
            pq += c_pq
        if q <= 0:
            return {"vwap": None, "sigma": None, "n": n}
        vwap = pq / q
        # Two-pass batch σ (see _range_sigma for the conditioning WHY).
        sigma = _range_sigma(store, rotation, exchange, symbol_ids, anchor_ms, end_ms, vwap, q)
    return {"vwap": vwap, "sigma": sigma, "n": n}


def _rotation_row_counts(manager: DayFileManager, lock: threading.Lock) -> dict[str, int]:
    """/v1/info row_counts aggregated across every LOCAL day file (§3c)."""
    counts = {t: 0 for t in _TABLE_COLUMNS}
    with lock:
        for _day, con, _borrowed in _rotation_day_cons(manager, manager.local_days()):
            for table in _TABLE_COLUMNS:
                counts[table] += con.execute(
                    f"SELECT COUNT(*) FROM {table}"  # noqa: S608 — whitelist
                ).fetchone()[0]
    return counts


def make_api_server(
    store: Any,
    lock: threading.Lock,
    info: dict,
    port: int,
    host: str = "127.0.0.1",
    supervisor: Optional["LegSupervisor"] = None,
) -> ThreadingHTTPServer:
    """Build (not start) the BYOD ThreadingHTTPServer. ``port=0`` -> ephemeral
    (tests read the bound port off ``server.server_address``). Caller runs
    ``serve_forever`` in a daemon thread and ``shutdown()``s it on exit.
    Binds loopback by default — this is a local research store, not a service.

    ``store`` is either one DuckDB connection (legacy single-file mode) or a
    :class:`DayFileManager` (rotation, §3c). The HTTP contract — paths, params,
    row shapes — is IDENTICAL in both modes; rotation additionally answers 410
    (``{'error':'archived','hint':'hf://…'}``) for ranges older than the oldest
    local day, and /v1/info aggregates row_counts across the local day files.
    §4f adds /v1/profile and /v1/vwap (both modes, live-local reads) and
    /v1/levels (rotation only — the registry lives beside the day files).

    ``supervisor`` (a :class:`LegSupervisor`) makes ``/health`` TELL THE TRUTH:
    per-leg last-data age, task state, restart counts, writer freshness and free
    disk, with ``ok`` computed from those observations instead of hard-coded.
    Without one — the legacy/test path — ``/health`` keeps its old shape and
    says ``status: "no-supervisor"``, which is the honest statement of "nothing
    is being watched here".

    **``/health`` always answers HTTP 200; the verdict lives in the body.** That
    is decided from the two measured consumers, not from preference:
    ``dashboard/terminal.js`` treats a non-2xx as "collector API offline" and
    would hide panels that are still perfectly answerable, while
    ``scripts/archive_ticks.py`` already reads ANY answer as "collector alive".
    The new ``/health/ready`` route is the probe surface that DOES switch status
    code (200 ok / 503 not ok) — it has no existing consumer, so it cannot
    regress one.
    /v1/info also advertises ``symbol_aliases`` — the canonical -> venue-native
    expansion every symbol filter applies (:func:`_expand_symbol`) — so a
    replay client can see exactly which stored ids a canonical query widens to.
    """
    rotation = isinstance(store, DayFileManager)
    if info.get("symbol"):
        info = {**info, "symbol_aliases": {info["symbol"]: _expand_symbol(info["symbol"])}}

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
                    # Always 200 — see the docstring. The BODY carries the verdict.
                    if supervisor is None:
                        self._send_json(
                            {
                                "ok": True,
                                "ts_ms": int(time.time() * 1000),
                                "status": "no-supervisor",
                            }
                        )
                    else:
                        self._send_json(supervisor.snapshot())
                elif path == "/health/ready":
                    # Probe surface (systemd/k8s/launchd style): the STATUS CODE
                    # carries the verdict. New route, no existing consumer.
                    if supervisor is None:
                        self._send_json(
                            {
                                "ok": True,
                                "ts_ms": int(time.time() * 1000),
                                "status": "no-supervisor",
                                "unhealthy": [],
                            }
                        )
                    else:
                        snap = supervisor.snapshot()
                        self._send_json(
                            {
                                "ok": snap["ok"],
                                "ts_ms": snap["ts_ms"],
                                "status": snap["status"],
                                "unhealthy": snap["unhealthy"],
                            },
                            status=200 if snap["ok"] else 503,
                        )
                elif path == "/v1/info":
                    if rotation:
                        counts = _rotation_row_counts(store, lock)
                        self._send_json(
                            {
                                **info,
                                "row_counts": counts,
                                "mode": "rotation",
                                "days": store.local_days(),  # what is answerable locally
                            }
                        )
                    else:
                        counts = {}
                        with lock:
                            for table in _TABLE_COLUMNS:
                                counts[table] = store.execute(
                                    f"SELECT COUNT(*) FROM {table}"  # noqa: S608 — whitelist
                                ).fetchone()[0]
                        self._send_json({**info, "row_counts": counts})
                elif path == "/v1/profile":  # §4f tick-exact auction profile
                    self._send_json(_profile_endpoint(store, lock, rotation, qs))
                elif path == "/v1/vwap":  # §4f anchored VWAP ± σ
                    self._send_json(_vwap_endpoint(store, lock, rotation, qs))
                elif path == "/v1/levels":  # §4f recorded-day levels registry
                    if rotation:
                        rows = derive_naked(read_levels_registry(store.levels_path()))
                        self._send_json({"n": len(rows), "days": rows})
                    else:
                        # The registry lives beside the day files it summarizes;
                        # a legacy single-file store has no recorded-day notion.
                        self._send_json(
                            {"error": "levels registry is rotation-mode only "
                                      "(data/ticks/levels.jsonl, §4f)"},
                            status=404,
                        )
                elif path in _API_ROUTES:
                    query = _query_rotation if rotation else _query_table
                    self._send_json(query(store, lock, _API_ROUTES[path], qs))
                else:
                    self._send_json(
                        {
                            "error": "not found",
                            "routes": [
                                "/health", "/health/ready", "/v1/info", *_API_ROUTES,
                                "/v1/profile", "/v1/vwap", "/v1/levels",  # §4f
                            ],
                        },
                        status=404,
                    )
            except _Archived as exc:  # range predates local day files -> HF (§3c)
                self._send_json(exc.body(), status=410)
            except ValueError as exc:  # bad start_ms/end_ms/limit -> client error
                self._send_json({"error": f"bad parameter: {exc}"}, status=400)
            except Exception as exc:  # noqa: BLE001 — never let a read kill the thread
                self._send_json({"error": repr(exc)}, status=500)

    return ThreadingHTTPServer((host, port), Handler)


# --------------------------------------------------------------------------- #
# Daemon orchestration.                                                        #
# --------------------------------------------------------------------------- #
def _is_rotation_path(db: Any) -> bool:
    """Directory (or directory-to-be) -> rotation mode (§3c); ``.duckdb`` FILE ->
    legacy single-file mode (back-compat: tests + the GH-release archive path)."""
    p = Path(db)
    if p.is_dir():
        return True
    if p.is_file():
        return False
    return p.suffix != ".duckdb"  # neither exists yet: the name decides


def _symbol_legs(symbol: str) -> dict[str, str]:
    """Map the CLI's exchange-style perp symbol to each venue's identifier.

    'BTCUSDT' -> okx 'BTC-USDT-SWAP', coinbase 'BTC-USD' (spot), deribit 'BTC'.
    Non-USDT symbols keep the raw base heuristically — every derived id is
    logged at startup so a wrong mapping is visible, not silent.

    SHARED with the BYOD API's canonical-symbol expansion (``_expand_symbol``):
    one derivation for record AND replay, so the API can never disagree with
    the daemon about which native id a canonical symbol maps to.
    """
    base = symbol[:-4] if symbol.upper().endswith("USDT") else symbol
    return {
        "okx": f"{base}-USDT-SWAP",
        "coinbase": f"{base}-USD",
        "deribit": base,
    }


async def _run_async(
    symbol: str,
    exchanges: tuple[str, ...],
    db: Any,
    api_port: Optional[int],
    retention_days: Optional[int],
    log=print,
) -> None:
    """Wire streams -> normalizers -> batched writer; run until SIGINT/SIGTERM."""
    lock = threading.Lock()
    rotation = _is_rotation_path(db)
    if rotation:
        manager = DayFileManager(db)
        writer: Any = RotatingWriter(manager, lock, log=log)
        store: Any = manager
    else:
        con = open_db(db)
        writer = BatchWriter(con, lock)
        store = con
    down = Downsampler()
    book = BybitBook()
    okx_book = OkxBook(ct_val=OKX_CTVAL)
    tape = CoinbaseTape()
    legs = _symbol_legs(symbol)
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

    def on_okx(frame: dict) -> None:
        if frame.get("event"):
            return  # sub acks ({"event":"subscribe",...}) and error frames
        channel = (frame.get("arg") or {}).get("channel")
        if channel == "trades":
            writer.add("trades", normalize_okx_trade(frame, ct_val=OKX_CTVAL))
        elif channel == "books":
            ts = okx_book.apply(frame)
            # Same 1/s storage cap as the other depth legs (DESIGN §3).
            if ts and down.ready(("okx", "depth"), ts):
                row = okx_book.depth_row(ts)
                if row is not None:
                    writer.add("depth_snapshots", [row])

    def on_coinbase(frame: dict) -> None:
        # CoinbaseTape enforces the proven JS rules (first-snapshot seed only,
        # monotonic trade_id dedupe, maker-side inversion inside the normalizer).
        writer.add("trades", tape.apply(frame))

    def on_crowding_payload(normalize, payload: list) -> None:
        # 5 m buckets re-served until the next bucket closes: the exact-ts
        # downsampler gate (per metric) dedupes the poll overlap. oi_hist emits
        # coin+usd rows sharing one ts — the per-METRIC key lets both through.
        rows = normalize(payload, symbol)
        fresh = [r for r in rows if down.ready(("binancef", "crowding", r[3]), r[2])]
        writer.add("crowding", fresh)

    def on_chain_payload(payload: dict) -> None:
        rows, skipped = normalize_deribit_chain(payload)
        writer.add("options_chain", rows)
        if skipped:
            # Counted, not silently dropped (§0) — futures/spot names in the
            # option summary, or shapes we could not parse.
            log(f"[collector] deribit-chain: {len(rows)} rows, {skipped} unparseable skipped")

    # --- supervised legs (per-leg watchdog; see LEG_BUDGET_S) ---------------- #
    # Every leg is spawned through the supervisor, which needs a FACTORY (a
    # zero-arg callable returning a fresh coroutine) instead of a coroutine: a
    # coroutine can only be awaited once, so a restartable leg must be
    # re-creatable. That is the only structural change this rail imposes here —
    # the leg set, its order and its arguments are unchanged.
    sup = LegSupervisor(
        stop_event=stop_event,
        log=log,
        gaps_path=(manager.gaps_path() if rotation else None),
        writer=writer,
        disk_path=(Path(db) if rotation else Path(db).parent),
        tick_s=SUPERVISOR_TICK_S,
    )
    # The writer stamps per-leg liveness at the ONE choke point where a leg's
    # data becomes real: rows accepted by the writer. Empty batches never stamp.
    writer.leg_sink = sup.mark_rows_current
    alive = sup.mark_alive_current  # diagnostic heartbeat, never a health verdict

    sup.spawn(
        "writer-flush", "internal",
        lambda: writer.run(stop_event),
        budget_s=LEG_BUDGET_S["writer-flush"],
    )
    if "bybit" in exchanges:
        # Built by a pure function, and passed by DEFAULT ARGUMENT below. Both
        # halves matter. On 2026-08-02 all three venue blocks assigned one shared
        # local named `subscribe`; sup.spawn only CREATES the task, so by the time
        # any lambda body ran the name held the LAST assignment, and bybit-ws and
        # okx-ws each sent Coinbase's market_trades payload to a venue that has
        # never heard of it. They were silently never subscribed and recorded ZERO
        # rows for hours with the socket open and answering pings.
        bybit_sub = bybit_subscribe(symbol)
        sup.spawn(
            "bybit-ws", "stream",
            lambda sub=bybit_sub: _ws_stream(
                "bybit-ws",
                _BYBIT_WS,
                on_bybit,
                stop_event=stop_event,
                subscribe=sub,
                app_ping={"op": "ping"},
                log=log,
                on_alive=alive,
            ),
            budget_s=LEG_BUDGET_S["bybit-ws"],
            # publicTrade is the witness for this whole socket; liquidations ride
            # the same leg precisely BECAUSE they are sparse (0 all day on
            # 2026-07-25, honestly) and cannot witness their own liveness.
            tables=("trades", "liquidations", "depth_snapshots", "funding_mark",
                    "open_interest"),
        )
    if "binancef" in exchanges:
        # depth20@100ms is the ONLY Binance futures WS topic that flows on this
        # network (§0.2) — trades/mark are topic-filtered, hence the REST polls.
        url = _BINANCEF_WS.format(streams=f"{symbol.lower()}@depth20@100ms")
        sup.spawn(
            "binancef-ws", "stream",
            lambda: _ws_stream(
                "binancef-ws", url, on_binance_depth, stop_event=stop_event, log=log,
                on_alive=alive,
            ),
            budget_s=LEG_BUDGET_S["binancef-ws"],
            tables=("depth_snapshots",),
        )
        sup.spawn(
            "binancef-premiumIndex", "poll",
            lambda: _rest_poll(
                "binancef-premiumIndex",
                lambda: _fetch_binance_premium_index(symbol),
                lambda p: writer.add("funding_mark", normalize_binance_premium_index(p)),
                _PREMIUM_POLL_S,
                stop_event,
                log=log,
                on_alive=alive,
            ),
            budget_s=LEG_BUDGET_S["binancef-premiumIndex"],
            tables=("funding_mark",),
        )
        sup.spawn(
            "binancef-openInterest", "poll",
            lambda: _rest_poll(
                "binancef-openInterest",
                lambda: _fetch_binance_open_interest(symbol),
                lambda p: writer.add("open_interest", normalize_binance_open_interest(p)),
                _OI_POLL_S,
                stop_event,
                log=log,
                on_alive=alive,
            ),
            budget_s=LEG_BUDGET_S["binancef-openInterest"],
            tables=("open_interest",),
        )
        # §3c: futures trades via REST aggTrades (the WS topic-filter does not
        # apply to REST) — dedicated loop because it carries the fromId cursor.
        sup.spawn(
            "binancef-aggTrades", "poll",
            lambda: _aggtrades_loop(symbol, writer, stop_event, log=log, on_alive=alive),
            budget_s=LEG_BUDGET_S["binancef-aggTrades"],
            tables=("trades",),
        )
        # §3c crowding endpoints @ 5 m -> long-format crowding table.
        for endpoint, normalize in _CROWDING_ENDPOINTS:
            sup.spawn(
                f"binancef-{endpoint}", "poll",
                lambda ep=endpoint, nz=normalize: _rest_poll(
                    f"binancef-{ep}",
                    lambda: _fetch_binance_crowding(ep, symbol),
                    lambda p: on_crowding_payload(nz, p),
                    _CROWDING_POLL_S,
                    stop_event,
                    log=log,
                    on_alive=alive,
                ),
                budget_s=LEG_BUDGET_S[f"binancef-{endpoint}"],
                tables=("crowding",),
            )
    if "okx" in exchanges:
        # §3c OKX leg: WS trades (ctVal-scaled) + books top-50; funding/OI via
        # REST 60 s (the WS tickers channel is not needed for these two rows).
        okx_sub = okx_subscribe(legs["okx"])
        sup.spawn(
            "okx-ws", "stream",
            lambda sub=okx_sub: _ws_stream(
                "okx-ws",
                _OKX_WS,
                on_okx,
                stop_event=stop_event,
                subscribe=sub,
                app_ping="ping",  # OKX prescribes the PLAIN-TEXT ping (§4b)
                log=log,
                on_alive=alive,
            ),
            budget_s=LEG_BUDGET_S["okx-ws"],
            tables=("trades", "depth_snapshots"),
        )
        sup.spawn(
            "okx-funding", "poll",
            lambda: _rest_poll(
                "okx-funding",
                lambda: _fetch_okx_funding(legs["okx"]),
                lambda p: writer.add("funding_mark", normalize_okx_funding(p)),
                _OKX_REST_POLL_S,
                stop_event,
                log=log,
                on_alive=alive,
            ),
            budget_s=LEG_BUDGET_S["okx-funding"],
            tables=("funding_mark",),
        )
        sup.spawn(
            "okx-oi", "poll",
            lambda: _rest_poll(
                "okx-oi",
                lambda: _fetch_okx_oi(legs["okx"]),
                lambda p: writer.add("open_interest", normalize_okx_oi(p)),
                _OKX_REST_POLL_S,
                stop_event,
                log=log,
                on_alive=alive,
            ),
            budget_s=LEG_BUDGET_S["okx-oi"],
            tables=("open_interest",),
        )
    if "coinbase" in exchanges:
        # §3c Coinbase spot tape leg — market_trades + heartbeats (the liveness
        # channel; ANY frame feeds the _ws_stream watchdog, so heartbeats keep a
        # quiet tape from tripping a false reconnect).
        coinbase_sub = coinbase_subscribe(legs["coinbase"])
        sup.spawn(
            "coinbase-ws", "stream",
            lambda sub=coinbase_sub: _ws_stream(
                "coinbase-ws",
                _COINBASE_WS,
                on_coinbase,
                stop_event=stop_event,
                subscribe=sub,
                log=log,
                on_alive=alive,
            ),
            budget_s=LEG_BUDGET_S["coinbase-ws"],
            tables=("trades",),
        )
    if "deribit" in exchanges:
        # §3c Deribit legs: DVOL @ 60 s; option chain snapshot HOURLY (this is
        # what starts the VRP/skew research clock — time-gated, not validated).
        sup.spawn(
            "deribit-dvol", "poll",
            lambda: _rest_poll(
                "deribit-dvol",
                _fetch_deribit_dvol,
                lambda p: writer.add("dvol", normalize_deribit_dvol(p)),
                _DVOL_POLL_S,
                stop_event,
                log=log,
                on_alive=alive,
            ),
            budget_s=LEG_BUDGET_S["deribit-dvol"],
            tables=("dvol",),
        )
        sup.spawn(
            "deribit-chain", "poll",
            lambda: _rest_poll(
                "deribit-chain",
                lambda: _fetch_deribit_chain(legs["deribit"]),
                on_chain_payload,
                _CHAIN_POLL_S,
                stop_event,
                log=log,
                on_alive=alive,
            ),
            budget_s=LEG_BUDGET_S["deribit-chain"],
            tables=("options_chain",),
        )
    if retention_days is not None and not rotation:
        # Legacy mode only — run() refuses the combination with rotation (§3c:
        # pruning closed days is the HF lifecycle's job, verify-then-delete);
        # the `not rotation` guard keeps a direct _run_async caller honest too.
        sup.spawn(
            "retention", "internal",
            lambda: _retention_loop(con, lock, retention_days, stop_event, log=log),
            budget_s=LEG_BUDGET_S["retention"],
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
        server = make_api_server(store, lock, info, port=api_port, supervisor=sup)
        threading.Thread(target=server.serve_forever, name="byod-api", daemon=True).start()
        log(f"[collector] BYOD API on http://127.0.0.1:{server.server_address[1]}")

    leg_note = ", ".join(f"{ex}={legs[ex]}" for ex in exchanges if ex in legs)
    log(
        f"[collector] recording {symbol} from {', '.join(exchanges)} -> {db} "
        f"({'daily rotation (§3c)' if rotation else 'legacy single file'}; "
        f"retention: {'keep-all' if retention_days is None else f'{retention_days}d'}; "
        f"Ctrl-C flushes and exits cleanly)"
        + (f" [venue ids: {leg_note}]" if leg_note else "")
    )
    log(
        f"[collector] per-leg watchdog: {len(sup.legs)} legs supervised, tick "
        f"{SUPERVISOR_TICK_S:.0f}s"
        + (f", gap ledger -> {manager.gaps_path()}" if rotation else "")
    )

    sup_task = asyncio.create_task(sup.run(), name="watchdog")
    await stop_event.wait()

    # --- graceful shutdown: stop legs, FINAL FLUSH, close (SIGINT rail, §3) ---
    # UNCHANGED contract: SIGTERM still means cancel everything, then flush.
    # The watchdog is cancelled FIRST and returns on stop_event anyway (it checks
    # it before every action), so it can never restart a leg we just cancelled.
    # sup.tasks() is read HERE, after the run, so a leg that was restarted mid-
    # session is cancelled by its CURRENT task object, not a stale one.
    log("[collector] shutdown requested — final flush")
    tasks: list[asyncio.Task] = [sup_task, *sup.tasks()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    writer.flush()
    if server is not None:
        server.shutdown()
        server.server_close()
    with lock:
        if rotation:
            manager.close_all()
        else:
            con.close()
    # Closed-day drops are logged per event during the run; the session total is
    # restated here so a long run's honest losses are visible at a glance (§3c).
    dropped = getattr(writer, "rows_dropped_closed", 0)
    err_dropped = getattr(writer, "rows_dropped_error", 0)
    log(
        f"[collector] closed. rows this session: {writer.rows_written}"
        + (f"; rows DROPPED for closed days: {dropped}" if dropped else "")
        + (f"; rows DROPPED on write errors: {err_dropped}" if err_dropped else "")
    )
    # Watchdog restatement: every restart left a REAL hole. Stating the totals at
    # shutdown is the same honesty rail as the closed-day drop line above — and
    # the per-event detail is on disk in gaps.jsonl (rotation mode).
    restarts = sup.restarts_total
    gap_events = sum(leg.gap_events for leg in sup.legs.values())
    gap_ms = sum(leg.gap_ms_total for leg in sup.legs.values())
    given_up = sorted(n for n, leg in sup.legs.items() if leg.give_up_since_ms is not None)
    if restarts or given_up or log_drop_count():
        log(
            f"[collector] watchdog: {restarts} leg restart(s), {gap_events} recorded "
            f"gap event(s) totalling {gap_ms / 1000.0:.1f}s of MISSING data "
            "(never backfilled)"
            + (f"; GAVE UP on: {', '.join(given_up)}" if given_up else "")
            + (f"; log lines dropped: {log_drop_count()}" if log_drop_count() else "")
        )


def run(
    symbol: str = "BTCUSDT",
    exchanges: tuple[str, ...] = ("binancef", "bybit", "okx", "coinbase", "deribit"),
    db: Any = DEFAULT_DB,
    api_port: Optional[int] = None,
    retention_days: Optional[int] = None,
    log=print,
) -> None:
    """Run the collector daemon until SIGINT/SIGTERM (blocking entry point).

    ``db``: a DIRECTORY (the default, ``data/ticks``) selects §3c daily rotation
    — one file per UTC day, routed by event time; a ``.duckdb`` FILE selects the
    legacy single-file mode (back-compat for tests + the GH-release archive path).

    Raises
    ------
    RuntimeError
        If the opt-in deps (duckdb/websockets) are missing — with the exact
        install command. Raised HERE, at run time, never at import time.
    ValueError
        On an unknown exchange code, a bad retention value, or retention_days
        combined with rotation mode (closed-day pruning belongs to the HF
        lifecycle's verify-then-delete flow, §3c — an in-place DELETE would
        break day-file immutability).
    """
    _require_deps(need_ws=True)
    bad = [e for e in exchanges if e not in _ACCEPTED_EXCHANGES]
    if bad:
        raise ValueError(
            f"unknown exchange code(s) {bad!r}; accepted: {list(_ACCEPTED_EXCHANGES)}"
        )
    if retention_days is not None and retention_days < 1:
        raise ValueError("retention_days must be >= 1 (omit it for keep-all, the default)")
    if retention_days is not None and _is_rotation_path(db):
        raise ValueError(
            "retention_days applies to legacy single-file mode only — in rotation "
            "mode pruning is the HF lifecycle's job (verify offsite, then delete; §3c)"
        )
    try:
        # Stamp at the single bind point, not inside _safe_log: the startup
        # banner, the deribit-chain line and the shutdown line all call log()
        # directly (collector.py ~3549, ~3786-3811), so stamping only the safe
        # wrapper would leave exactly the lines that bracket a session unstamped.
        log = _stamped(log)
        asyncio.run(_run_async(symbol, exchanges, db, api_port, retention_days, log=log))
    except KeyboardInterrupt:
        # Fallback path when add_signal_handler is unavailable; the batched tail
        # (< 500 ms of rows) may be lost here — the unix signal path flushes fully.
        log("[collector] interrupted")


if __name__ == "__main__":  # allow `python -m btcquant.collector` for quick smokes
    run()
