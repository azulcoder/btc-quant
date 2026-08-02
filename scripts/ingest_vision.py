#!/usr/bin/env python3
"""ingest_vision.py — public Binance archive ``aggTrades`` -> ``data/vision/`` parquet.

DESIGN-orderflow-terminal.md §3d (spec) + §0.7 rails a-d (why this is allowed to
exist at all). STRATEGY.md M7.

THE HONEST LIMIT, FIRST, BEFORE ANYTHING ELSE
---------------------------------------------
``aggTrades`` is **TRADES ONLY**. Gap 1 does not close, it **SPLITS**:

* **trade-derived** families — CVD, footprint, size-bucketed delta, VPIN (its
  volume clock needs only trades) — reach 2,406 published days = 6.587 calendar
  years = **244 % of MinBTL(5)**.
* **book-derived** families — OFI, weighted mid, depth-imbalance slope, walls —
  gain **nothing** and stay at **1.8 % of MinBTL(5)**. The archive publishes no
  book snapshots. ``bookDepth`` is 12 cumulative ±% bands at ~30 s (no levels, no
  price-per-level, no queue size), so it cannot satisfy the repo's
  ``depth_snapshots(bids, asks)`` contract; ``bookTicker`` is L1-only, covers 320
  days ending 2024-03-30 and is discontinuous with the recorded window.

Any output, doc or column that implies otherwise is a defect. A bar frame mixing
both families is only as long as its shortest family.

WHY THIS IS NOT THE MIXED-HISTORY BACKFILL STRATEGY §6 REFUSES
--------------------------------------------------------------
Because it is not another source. The collector's ``binancef-aggTrades`` leg
already polls ``/fapi/v1/aggTrades`` with a gapless ``fromId`` cursor and
normalizes it through :func:`btcquant.collector.normalize_binance_aggtrades` into
``trades(exchange, symbol, trade_id, ts_ms, price, qty, aggressor_buy)`` with
``trade_id = str(a)`` and ``aggressor_buy = not m``. The archive is the **same
venue, same stream, same aggTradeId space** arriving by a slower road. So:

* dedup against recorded rows is **exact** on ``(exchange, symbol, trade_id)``;
* missing history is found by **ID continuity**, not by a timestamp guess.

Measured 2026-08-02 against the recorded day file for 2026-08-01: 399,219 archive
rows, set difference **0 in both directions**, and **0 mismatches** across
``ts_ms`` / ``price`` / ``qty`` / ``aggressor_buy`` on all 399,219 joined rows.
Cross-day seam: ``last_id(2026-07-31) = 3399378199`` -> ``first_id(2026-08-01) =
3399378200``, exactly +1.

RAILS THIS SCRIPT ENFORCES (each one is code, not a comment)
------------------------------------------------------------
1. **Separate tree.** Rows land under ``data/vision/<venue>/<symbol>/<family>/
   date=<YYYY-MM-DD>/trades.parquet`` — provenance is readable from the path
   alone. The output root is refused if it resolves inside the tick store
   (``data/ticks``) or the order-flow cache, and the refusal is asserted by
   :func:`ingest_day` / :func:`ingest_month` themselves, not only by ``main()``:
   a rail only the CLI holds is a habit of the CLI. Nothing here ever writes a
   ``.duckdb`` file or a ``levels.jsonl`` row.
2. **Scope allowlist, instrument included.** Only ``futures/um`` + ``aggTrades``
   + ``BTCUSDT`` is accepted; every other market/family raises with the measured
   reason (see :data:`_REFUSALS`). And downloading is not the same decision as
   writing: :data:`ALLOWED_TARGET` pins which ``(venue, symbol)`` an allowlisted
   vendor object may land under, because the exact-dedup argument holds inside
   ONE aggTradeId space and an id space belongs to an instrument.
3. **``sec_readiness`` is untouched.** The MinBTL countdown counts RECORDED days
   only. This script cannot reach it: it writes neither ``levels.jsonl`` nor any
   day file, and ``check_ticks --vision`` refuses to print a readiness number.
4. **Seven gates** (G1..G7 below). The canonical parquet is never written before
   all seven pass; a failure keeps the bad artifact for inspection, writes
   ``FAILED-<date>.json``, skips the day, continues the run and exits non-zero.
   That holds on the MONTHLY path too — it catches ``Exception``, not only
   ``VisionError``, because ``auto`` routes every whole past month through it and
   a duckdb conversion error used to abort the whole backfill with a traceback
   and no ledger row.
5. **Nothing is fabricated — in either direction.** A day the archive does not
   publish is ``absent``: no parquet, no zero-row file, no interpolation — only a
   ledger row saying it was asked for and not served. And a day the archive DOES
   publish is never written down as absent: a missing MONTHLY object says nothing
   about its days (measured 2026-08-02: the ``2026-08`` bundle was 404 while
   ``2026-08-01`` daily was 200), so a 404 there falls back to the daily objects.
   ID holes are reported, never filled. Seam mismatches are reported, never
   patched.

Usage
-----
    python3 scripts/ingest_vision.py --start 2026-07-25 --end 2026-08-01
    python3 scripts/ingest_vision.py --list --start 2019-12-01 --end 2020-02-01
    python3 scripts/ingest_vision.py --all --yes          # 2,406 days, ~41 GiB zip
    make vision-sync ARGS="--start 2026-07-25 --end 2026-08-01"

Requires the opt-in collector dep:  pip install -r requirements-collector.txt
(duckdb only — HTTP is stdlib ``urllib``, the ``scripts/fetch_econ.py`` idiom).

Exit codes
----------
* 0 — every requested day is ``ok``, ``already`` or ``absent`` (absence is an
      answer, not a failure).
* 1 — at least one day FAILED a gate, or a usage/scope error.
* 2 — the output root is refused (inside the tick store / cache).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from xml.etree import ElementTree

try:  # optional dependency — see requirements-collector.txt
    import duckdb  # type: ignore
except Exception:  # noqa: BLE001 — any import failure means "no store tooling"
    duckdb = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_INSTALL_HINT = "pip install -r requirements-collector.txt"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "vision"

# --------------------------------------------------------------------------- #
# The public archive. Both hosts are Binance's own publication of Binance's own #
# data; no third-party code is involved in reaching either (STRATEGY §6).       #
# --------------------------------------------------------------------------- #
HOST = "https://data.binance.vision"
#: The bucket behind ``data.binance.vision``, used ONLY to enumerate what exists
#: (``--list`` / ``--all``). Enumeration beats guessing dates: a guessed range
#: cannot tell "not published" from "not published *yet*".
LIST_HOST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"

UA = "btc-quant/ingest_vision (research archive mirror; stdlib urllib)"

#: Rail 2. The ONE (market, family, vendor_symbol) triple whose exact-dedup
#: argument has been measured. Widening this set is a design decision with its
#: own evidence, never a convenience edit — each refusal below states what was
#: measured. STRATEGY M7 locks the scope as
#: ``futures/um/{daily,monthly}/aggTrades/BTCUSDT``, INSTRUMENT included: the
#: dedup argument is about one aggTradeId space, and an id space belongs to an
#: instrument, not to a family.
ALLOWED_SCOPE: tuple[tuple[str, str, str], ...] = (
    ("futures/um", "aggTrades", "BTCUSDT"),
)

#: Where an allowlisted vendor object is allowed to LAND: ``(venue, symbol)`` as
#: the repo stores them. Downloading and writing are two different decisions and
#: were previously unrelated — ``--vendor-symbol ETHUSDT --symbol BTCUSDT`` wrote
#: ETH rows into the binancef/BTCUSDT partition ``orderflow`` reads by default,
#: and ``--venue bybit`` wrote Binance rows under a venue whose id space is
#: unrelated, which breaks the exact-dedup argument that licenses M7 at all.
#: A new target needs its own measured argument, registered here.
ALLOWED_TARGET: dict[tuple[str, str, str], tuple[str, str]] = {
    ("futures/um", "aggTrades", "BTCUSDT"): ("binancef", "BTCUSDT"),
}

_REFUSALS: dict[str, str] = {
    "spot": (
        "spot BTCUSDT is a DIFFERENT INSTRUMENT from the perp the collector records, so "
        "the aggTradeId spaces are unrelated and the exact-dedup argument that licenses "
        "this whole item does not hold. Its layout differs too (8 columns incl. "
        "is_best_match, capitalized True/False, and transact_time in MICROSECONDS since "
        "2025-01-01)."
    ),
    "metrics": (
        "metrics has NO unique key and its timestamp convention differs PER METRIC inside "
        "one file — measured against the recorded `crowding` rows for 2026-08-01, "
        "sum_open_interest matches at a +300,000 ms shift while sum_taker_long_short_vol_"
        "ratio matches at 0 ms. Joining it is exactly the mixed-history backfill "
        "STRATEGY §6 refuses. It needs its own item and its own argument."
    ),
    "liquidationSnapshot": (
        "liquidationSnapshot does NOT EXIST for USD-M futures — the prefix "
        "futures/um/daily/liquidationSnapshot/ lists zero keys. It is published for "
        "COIN-M only (futures/cm/, BTCUSD_PERP, 2023-06-25..2024-10-14, discontinued), "
        "and COIN-M is a different instrument."
    ),
    "bookDepth": (
        "bookDepth is NOT a book: 12 cumulative ±% bands at ~30 s "
        "(timestamp,percentage,depth,notional) with no levels, no price-per-level and no "
        "queue size. It cannot satisfy depth_snapshots(bids, asks) and cannot reconstruct "
        "OFI / weighted mid / walls."
    ),
    "bookTicker": (
        "bookTicker is L1-only and covers 2023-05-16..2024-03-30 (320 days = 0.876 yrs = "
        "32.5 % of MinBTL(5)), discontinuous with the recorded window. Interesting for "
        "event-level OFI without the sampling approximation — as a SEPARATE item, not "
        "smuggled in here."
    ),
    "trades": (
        "raw `trades` uses the RAW tradeId space, not the aggTradeId space the collector "
        "records, so dedup against recorded rows would be heuristic rather than exact. "
        "That exactness is the only reason M7 is admissible under DESIGN §0.7."
    ),
}

#: Exactly the 7 columns the USD-M aggTrades CSV carries, in order. A file whose
#: header names or column COUNT differ is refused (G3) — that is what catches the
#: 8-column spot layout before a single row is written.
AGG_COLUMNS: tuple[str, ...] = (
    "agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id",
    "transact_time", "is_buyer_maker",
)

#: ``collector._TABLE_COLUMNS["trades"]`` verbatim. Restated rather than imported
#: for the same reason ``check_ticks.py`` restates the schema: this script must
#: run against a copied tree on a duckdb-only machine. ``tests/test_vision.py``
#: asserts the two still match, so they cannot silently drift.
TRADES_COLUMNS: tuple[tuple[str, str], ...] = (
    ("exchange", "VARCHAR"), ("symbol", "VARCHAR"), ("trade_id", "VARCHAR"),
    ("ts_ms", "BIGINT"), ("price", "DOUBLE"), ("qty", "DOUBLE"),
    ("aggressor_buy", "BOOLEAN"),
)

MS_PER_DAY = 86_400_000
#: G4b. 1e14 ms is year 5138; microsecond timestamps (the spot layout since
#: 2025-01-01) are 16 digits and land near year 58500 if read as ms. The guard is
#: on MAGNITUDE, never on a date cutoff — a cutoff is a rule that rots.
MAX_MS = 100_000_000_000_000

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_CHECKSUM_RE = re.compile(r"^([0-9a-fA-F]{64})\s+(\S+)\s*$")
_VENUE_RE = re.compile(r"^[a-z0-9_]+$")
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9._:\-]+$")

#: Printed on EVERY run, including ``--dry-run``, carried in EVERY ``--json``
#: summary (the machine-readable path is the one most likely to be piped into a
#: report, i.e. exactly where "trades only" most needs to arrive) and in every
#: per-day manifest, and pinned verbatim by a test (the
#: ``orderflow.HONESTY_SENTENCES`` discipline: a load-bearing sentence a refactor
#: can reword is not a rail).
HONEST_LIMIT_SENTENCES: tuple[str, ...] = (
    "HONEST LIMIT: aggTrades is TRADES ONLY. This partition extends the trade-derived "
    "families (CVD, footprint, size-bucketed delta, VPIN) only. OFI, weighted mid, "
    "depth-imbalance slope and walls gain NOTHING — the archive publishes no book.",
    "sec_readiness is UNCHANGED by this run: the MinBTL countdown counts RECORDED days "
    "only (data/ticks/levels.jsonl); nothing here writes to it.",
    "A day the archive does not publish is ABSENT: no parquet, no zero-row file, no "
    "interpolation — only a ledger row saying it was asked for and not served.",
)

PROVENANCE = {
    "tool": "scripts/ingest_vision.py",
    "spec": "DESIGN-orderflow-terminal.md §3d + §0.7 rails a-d; STRATEGY.md M7",
    "host": HOST,
    "publisher": "Binance (public archive of Binance's own market data; keyless)",
    "honest_limit": list(HONEST_LIMIT_SENTENCES),
}


class VisionError(RuntimeError):
    """Raised for a scope/usage/gate refusal. Carries the reason, always."""


# --------------------------------------------------------------------------- #
# Scope allowlist (rail 2)                                                     #
# --------------------------------------------------------------------------- #
def check_scope(market: str, family: str, vendor_symbol: Optional[str] = None) -> None:
    """Refuse anything outside :data:`ALLOWED_SCOPE`, with the measured reason.

    ``vendor_symbol`` is optional only so the (market, family) half can be
    checked before a symbol is known; every write path passes all three.
    """
    pairs = {(m, f) for m, f, _ in ALLOWED_SCOPE}
    allowed = ", ".join(f"{m}/{f}/{s}" for m, f, s in ALLOWED_SCOPE)
    if (market, family) not in pairs:
        hint = _REFUSALS.get(family)
        if hint is None and market.startswith("spot"):
            hint = _REFUSALS["spot"]
        msg = (f"market={market!r} family={family!r} is outside the M7 scope allowlist "
               f"({allowed}).")
        if hint:
            msg += " " + hint
        else:
            msg += (" Nothing outside the allowlist has had its dedup/ID-continuity "
                    "argument measured, and an unmeasured family is exactly the "
                    "mixed-history backfill STRATEGY §6 refuses.")
        raise VisionError(msg)
    if vendor_symbol is not None and (market, family, vendor_symbol) not in ALLOWED_SCOPE:
        raise VisionError(
            f"vendor_symbol={vendor_symbol!r} is outside the M7 scope allowlist "
            f"({allowed}). The dedup that licenses this item is exact only inside ONE "
            "aggTradeId space, and an id space belongs to an INSTRUMENT: BTCUSDT perp "
            "ids say nothing about any other contract. A different instrument needs its "
            "own measured overlap against a recorded day before it can be ingested.")


def check_target(market: str, family: str, vendor_symbol: str, venue: str,
                 symbol: str) -> None:
    """Refuse a vendor object landing under a venue/symbol it is not.

    Downloading and writing were two unrelated decisions: ``--vendor-symbol``
    picked the URL while ``--venue``/``--symbol`` picked the ``exchange`` and
    ``symbol`` COLUMNS and the partition path, and nothing compared them. That
    allowed ETH rows into ``binancef/BTCUSDT`` — the exact partition
    ``orderflow.order_flow_bars`` reads by default — and Binance rows under
    ``bybit``, whose trade-id space is unrelated. ``orderflow`` never reads the
    manifests, only the parquet, so a truthful manifest does not save it.
    """
    check_scope(market, family, vendor_symbol)
    want = ALLOWED_TARGET[(market, family, vendor_symbol)]
    if (venue, symbol) != want:
        raise VisionError(
            f"venue={venue!r} symbol={symbol!r} is not where {market}/{family}/"
            f"{vendor_symbol} may land — the registered target is venue={want[0]!r} "
            f"symbol={want[1]!r}. The archive rows must carry the venue code whose "
            "recorded leg shares their id space (binancef-aggTrades), or the exact "
            "dedup on (exchange, symbol, trade_id) is not exact and M7's whole "
            "admissibility argument (DESIGN §0.7) fails. Register a new mapping in "
            "ALLOWED_TARGET with its own measured overlap.")


# --------------------------------------------------------------------------- #
# Paths + URLs                                                                 #
# --------------------------------------------------------------------------- #
def _prefix(market: str, granularity: str, family: str, vendor_symbol: str) -> str:
    return f"data/{market}/{granularity}/{family}/{vendor_symbol}/"


def archive_url(market: str, granularity: str, family: str, vendor_symbol: str,
                stamp: str) -> str:
    """Canonical zip URL. ``stamp`` is ``YYYY-MM-DD`` (daily) or ``YYYY-MM`` (monthly)."""
    return f"{HOST}/{_prefix(market, granularity, family, vendor_symbol)}" \
           f"{vendor_symbol}-{family}-{stamp}.zip"


def canonical_name(family: str, vendor_symbol: str, stamp: str) -> str:
    return f"{vendor_symbol}-{family}-{stamp}.zip"


def partition_dir(out_root: Path, venue: str, symbol: str, family: str, date: str) -> Path:
    """``<out>/<venue>/<symbol>/<family>/date=<YYYY-MM-DD>`` — rail 1.

    Five facts legible from the path with the file unopened: it is archive (not
    recorded), the venue code the rows carry, the instrument, the upstream family
    verbatim, and the UTC **event** day.
    """
    if not _VENUE_RE.match(venue):
        raise VisionError(f"bad venue code {venue!r}")
    if not _SYMBOL_RE.match(symbol):
        raise VisionError(f"bad symbol id {symbol!r}")
    if not _DATE_RE.match(date):
        raise VisionError(f"bad date {date!r} — expected YYYY-MM-DD")
    return Path(out_root) / venue / symbol / family / f"date={date}"


def manifest_path(out_root: Path, venue: str, symbol: str, family: str, date: str) -> Path:
    return Path(out_root) / venue / symbol / family / "manifests" / f"MANIFEST-{date}.json"


def failed_path(out_root: Path, venue: str, symbol: str, family: str, date: str) -> Path:
    return Path(out_root) / venue / symbol / family / "manifests" / f"FAILED-{date}.json"


def ledger_path(out_root: Path) -> Path:
    return Path(out_root) / "_ledger.jsonl"


def assert_out_root_is_separate(out_root: Path) -> Path:
    """Rail 1, in code: refuse an output root inside the recorded store or cache.

    "It happens to be a different directory today" is not a rail. This makes the
    separation impossible to undo with a stray ``--out`` — the one path by which
    archive rows could reach a recorded relation (or, worse, sit next to
    ``levels.jsonl``, the MinBTL clock's only input).
    """
    root = Path(out_root).expanduser().resolve()
    forbidden: list[tuple[Path, str]] = []
    try:
        from btcquant import collector as _collector  # noqa: WPS433 — optional
        forbidden.append((Path(_collector.DEFAULT_DB).resolve(), "the recorded tick store"))
    except Exception:  # noqa: BLE001 — deps absent: fall back to the known layout
        forbidden.append(((REPO_ROOT / "data" / "ticks").resolve(), "the recorded tick store"))
    forbidden.append(((REPO_ROOT / "data" / "orderflow").resolve(), "the order-flow bar cache"))
    forbidden.append(((REPO_ROOT / "data" / "hf-stage").resolve(), "the HF upload staging tree"))
    for path, what in forbidden:
        if root == path or root.is_relative_to(path) or path.is_relative_to(root):
            raise VisionError(
                f"--out {root} overlaps {what} ({path}). Archive rows live in their own "
                "tree so provenance is legible from the path alone and no recorded "
                "partition can ever union them by accident (DESIGN §0.7 rail a)."
            )
    return root


# --------------------------------------------------------------------------- #
# HTTP — stdlib urllib (scripts/fetch_econ.py idiom), polite by construction    #
# --------------------------------------------------------------------------- #
class HttpAbsent(Exception):
    """404/403: the archive does not publish this object. An ANSWER, not an error."""

    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url


def http_get(url: str, *, timeout: float = 60.0, retries: int = 6,
             opener=None) -> tuple[bytes, dict[str, str]]:
    """GET with exponential backoff + jitter. Returns ``(body, headers)``.

    404/403 raise :class:`HttpAbsent` and are **never retried** — the archive
    answering "I do not publish that" is information, and retrying it six times
    is both wrong and rude. 429/5xx back off ``1,2,4,8,16,32 s`` with jitter and
    honour ``Retry-After`` when the server sends one.
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    _open = opener if opener is not None else urllib.request.urlopen
    last: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        try:
            with _open(req, timeout=timeout) as resp:
                body = resp.read()
                headers = {k.lower(): v for k, v in dict(resp.headers).items()}
                return body, headers
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise HttpAbsent(exc.code, url) from None
            last = exc
            wait = float(exc.headers.get("Retry-After") or 0) if exc.headers else 0.0
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            wait = 0.0
        if attempt == retries - 1:
            break
        backoff = wait or (2.0 ** attempt)
        time.sleep(min(60.0, backoff) * (1.0 + random.random() * 0.25))
    raise VisionError(f"GET {url} failed after {retries} attempt(s): {last}")


def http_download(url: str, dest: Path, *, timeout: float = 300.0, retries: int = 6,
                  opener=None, chunk: int = 4 * 1024 * 1024) -> tuple[int, str, dict[str, str]]:
    """Stream a GET to ``dest``, hashing as it goes. Returns ``(bytes, sha256, headers)``.

    Streaming rather than ``resp.read()`` into memory because the full backfill
    is monthly files: the aggTrades history is 40.94 GiB over 79 monthly objects
    (~530 MB mean, and the largest single day alone is 111.86 MB). Buffering a
    monthly object whole, then its extracted CSV, then a DuckDB load of it, is
    three copies of the same gigabyte for no reason.

    Retry/backoff semantics are :func:`http_get`'s, including "404 is an answer
    and is never retried". A failed attempt truncates ``dest`` and starts over —
    a resumed byte range would need the server's ETag to still match, and a
    silently-spliced file is a worse failure than a re-download.
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    _open = opener if opener is not None else urllib.request.urlopen
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        try:
            h = hashlib.sha256()
            n = 0
            with _open(req, timeout=timeout) as resp, open(dest, "wb") as out:
                headers = {k.lower(): v for k, v in dict(resp.headers).items()}
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    out.write(buf)
                    h.update(buf)
                    n += len(buf)
            return n, h.hexdigest(), headers
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise HttpAbsent(exc.code, url) from None
            last = exc
            wait = float(exc.headers.get("Retry-After") or 0) if exc.headers else 0.0
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            wait = 0.0
        dest.unlink(missing_ok=True)
        if attempt == retries - 1:
            break
        backoff = wait or (2.0 ** attempt)
        time.sleep(min(60.0, backoff) * (1.0 + random.random() * 0.25))
    raise VisionError(f"GET {url} failed after {retries} attempt(s): {last}")


def list_prefix(prefix: str, *, opener=None, sleep: float = 0.2) -> list[tuple[str, int]]:
    """Every ``(key, size)`` under ``prefix`` in the public bucket, paginated.

    Enumeration, not date arithmetic: only the listing can distinguish "the
    archive does not publish this day" from "we guessed a date wrong".
    """
    out: list[tuple[str, int]] = []
    marker = ""
    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    while True:
        q = {"delimiter": "/", "prefix": prefix, "max-keys": "1000"}
        if marker:
            q["marker"] = marker
        body, _ = http_get(f"{LIST_HOST}?{urllib.parse.urlencode(q)}", opener=opener)
        root = ElementTree.fromstring(body)
        keys = []
        for c in root.findall(f"{ns}Contents"):
            key = (c.findtext(f"{ns}Key") or "").strip()
            size = int(c.findtext(f"{ns}Size") or 0)
            if key:
                keys.append((key, size))
        out.extend(keys)
        truncated = (root.findtext(f"{ns}IsTruncated") or "false").strip().lower() == "true"
        if not truncated or not keys:
            break
        marker = root.findtext(f"{ns}NextMarker") or keys[-1][0]
        if sleep:
            time.sleep(sleep)
    return out


def published_days(market: str, family: str, vendor_symbol: str, *, opener=None,
                   sleep: float = 0.2) -> dict[str, int]:
    """``{YYYY-MM-DD: zip bytes}`` for every daily file the archive actually serves."""
    prefix = _prefix(market, "daily", family, vendor_symbol)
    rx = re.compile(re.escape(f"{vendor_symbol}-{family}-") + r"(\d{4}-\d{2}-\d{2})\.zip$")
    out: dict[str, int] = {}
    for key, size in list_prefix(prefix, opener=opener, sleep=sleep):
        m = rx.search(key)
        if m:
            out[m.group(1)] = size
    return out


# --------------------------------------------------------------------------- #
# G1 — CHECKSUM                                                                #
# --------------------------------------------------------------------------- #
def parse_checksum(text: str, expect_name: str) -> str:
    """``<64-hex><ws><filename>`` -> the hex digest, with the NAME field checked.

    Parsed in-process rather than shelled out to ``shasum -a 256 -c``: the
    CHECKSUM file names the canonical object, so ``shasum -c`` on a locally
    renamed download fails with "FAILED open or read" — a false alarm that says
    nothing about the bytes. Measured; hence this function.
    """
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    m = _CHECKSUM_RE.match(line)
    if not m:
        raise VisionError(f"unparseable CHECKSUM line {line!r} — expected '<sha256>  <name>'")
    digest, name = m.group(1).lower(), m.group(2)
    if Path(name).name != expect_name:
        raise VisionError(
            f"CHECKSUM names {name!r} but this object is {expect_name!r} — refusing: a "
            "checksum for a different file proves nothing about this one")
    return digest


def sha256_bytes(blob: bytes) -> str:
    h = hashlib.sha256()
    h.update(blob)
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# G2/G3 — zip shape + header sniff                                             #
# --------------------------------------------------------------------------- #
def open_single_csv(zip_src, expect_stem: str) -> tuple[zipfile.ZipFile, str]:
    """G2: exactly one entry, named ``<expect_stem>.csv``. Returns (zf, member).

    Accepts a ``bytes`` blob or a path — the ingest path streams to disk (see
    :func:`http_download`), the tests hand it bytes.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_src) if isinstance(zip_src, (bytes, bytearray))
                             else str(zip_src))
    except zipfile.BadZipFile as exc:
        raise VisionError(f"not a zip archive: {exc}") from None
    names = zf.namelist()
    if len(names) != 1:
        raise VisionError(f"expected exactly 1 zip entry, got {len(names)}: {names!r}")
    want = f"{expect_stem}.csv"
    if names[0] != want:
        raise VisionError(f"zip entry is {names[0]!r}, expected {want!r}")
    return zf, names[0]


def sniff_header(first_line: str) -> bool:
    """G3: does line 1 carry column NAMES rather than a trade?

    **Measured, and the spec was wrong about it.** STRATEGY M7 said header
    presence "differed by year"; it differs per FILE. ``2021-01-01`` has one and
    ``2021-01-02`` does not; ``2022-08-10`` has none and ``2022-08-11`` does;
    monthly ``2020-01`` has none and ``2026-07`` does. A year cutoff would lose
    or invent exactly one row on scattered days out of 2,406, silently, and would
    corrupt ``min(agg_trade_id)`` for those days. So: sniff, always.

    The rule is structural — the first field of a data row is a base-10 integer
    aggTradeId and the first field of a header row is the literal
    ``agg_trade_id``.
    """
    head = (first_line or "").lstrip("﻿").strip()
    if not head:
        raise VisionError("empty first line — refusing to guess whether it was a header")
    return not _INT_RE.match(head.split(",", 1)[0].strip())


def check_columns(first_line: str, header_present: bool) -> None:
    """G3 continued: 7 columns, and a present header must match names exactly."""
    fields = [f.strip() for f in (first_line or "").lstrip("﻿").strip().split(",")]
    if len(fields) != len(AGG_COLUMNS):
        raise VisionError(
            f"expected {len(AGG_COLUMNS)} columns {AGG_COLUMNS}, found {len(fields)}: "
            f"{fields!r}. An 8-column row is the SPOT layout (extra is_best_match) — a "
            "different instrument with a different aggTradeId space; refusing.")
    if header_present and tuple(fields) != AGG_COLUMNS:
        raise VisionError(f"header names {tuple(fields)} != expected {AGG_COLUMNS}")


# --------------------------------------------------------------------------- #
# The normalizer, pure. This is the DEFINITION; the DuckDB path below is the    #
# fast route and a test pins the two together on real fixture rows.             #
# --------------------------------------------------------------------------- #
def normalize_agg_row(fields: Iterable[str], venue: str, symbol: str) -> tuple:
    """One archive CSV row -> one ``trades`` tuple.

    Mirrors :func:`btcquant.collector.normalize_binance_aggtrades` field for
    field, including the §0.6 aggressor convention: ``is_buyer_maker`` true means
    the BUYER was the maker, i.e. the aggressor SOLD, so
    ``aggressor_buy = NOT is_buyer_maker``. ``trade_id = str(agg_trade_id)``,
    identical to the collector's ``str(t["a"])``.

    ``tests/test_vision.py`` drives both functions over the same trade and
    asserts the 7-tuples are equal — that is what makes "same stream" mechanical
    rather than prose.
    """
    f = [str(x).strip() for x in fields]
    if len(f) != len(AGG_COLUMNS):
        raise VisionError(f"expected {len(AGG_COLUMNS)} fields, got {len(f)}: {f!r}")
    ts = int(f[5])
    if not (0 < ts < MAX_MS):  # G4b, per row
        raise VisionError(
            f"transact_time {ts} is not epoch MILLISECONDS (>= 1e14 means microseconds — "
            "the spot layout since 2025-01-01; read as ms it lands in year ~58500)")
    flag = f[6].strip().lower()
    if flag not in ("true", "false"):
        raise VisionError(f"is_buyer_maker {f[6]!r} is not a boolean literal")
    return (
        venue,
        symbol,
        str(int(f[0])),
        ts,
        float(f[1]),
        float(f[2]),
        flag == "false",  # buyer-is-maker -> SELL aggressor (DESIGN §0.6)
    )


def _int_or_none(x: Any) -> Optional[int]:
    return None if x is None else int(x)


def _day_bounds(date: str) -> tuple[int, int]:
    if not _DATE_RE.match(date):
        raise VisionError(f"bad date {date!r} — expected YYYY-MM-DD")
    a = int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    return a, a + MS_PER_DAY


def _day_of(ts_ms: int) -> str:
    """UTC day of an epoch-ms timestamp by INTEGER arithmetic.

    Never a local-timezone date function: ``strftime(to_timestamp(ts/1000))`` in
    DuckDB formats in the session timezone, which silently mis-buckets rows near
    midnight (measured: 53,006 vs the true 71,359 rows for 2020-01-01 under
    Asia/Jakarta). Integer division has no timezone.
    """
    return datetime.fromtimestamp((ts_ms // MS_PER_DAY) * 86400, tz=timezone.utc) \
        .strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# CSV -> parquet, with gates G4..G7                                            #
# --------------------------------------------------------------------------- #
def _require_duckdb() -> None:
    if duckdb is None:
        raise VisionError(f"duckdb missing — {_INSTALL_HINT}")


def _csv_relation_sql(csv_path: Path, header_present: bool) -> str:
    """Explicit column types, explicit header flag. Never sniffed by DuckDB.

    Type inference on a 400k-row CSV is a coin flip we do not need: the layout is
    known and pinned (:data:`AGG_COLUMNS`), and an inferred DOUBLE for
    ``agg_trade_id`` would round ids past 2^53.
    """
    cols = ("{'agg_trade_id':'BIGINT','price':'DOUBLE','quantity':'DOUBLE',"
            "'first_trade_id':'BIGINT','last_trade_id':'BIGINT',"
            "'transact_time':'BIGINT','is_buyer_maker':'BOOLEAN'}")
    esc = str(csv_path).replace("'", "''")
    return (f"read_csv('{esc}', header={'true' if header_present else 'false'}, "
            f"columns={cols})")


def _select_trades_sql(rel: str, venue: str, symbol: str) -> str:
    """The DuckDB mirror of :func:`normalize_agg_row`, same field order."""
    v = venue.replace("'", "''")
    s = symbol.replace("'", "''")
    return (
        f"SELECT '{v}' AS exchange, '{s}' AS symbol, "
        "CAST(agg_trade_id AS VARCHAR) AS trade_id, "
        "transact_time AS ts_ms, price, quantity AS qty, "
        "NOT is_buyer_maker AS aggressor_buy "
        f"FROM {rel}"
    )


def _id_hole_ranges(con: Any, rel: str, limit: int = 64) -> list[list[int]]:
    """G5: the missing ``[lo, hi]`` id runs inside ``[min, max]``. Reported, never filled."""
    rows = con.execute(
        f"""WITH s AS (SELECT CAST(trade_id AS BIGINT) AS i FROM {rel}),
                 g AS (SELECT i, lead(i) OVER (ORDER BY i) AS nx FROM s)
             SELECT i + 1, nx - 1 FROM g WHERE nx IS NOT NULL AND nx > i + 1
             ORDER BY 1 LIMIT {int(limit)}"""
    ).fetchall()
    return [[int(a), int(b)] for a, b in rows]


def write_day_parquet(con: Any, rel: str, dest: Path, date: str, *,
                      keep_bad: bool = True) -> dict[str, Any]:
    """Gates G4a/G4b/G5, write, then G7. Returns the ``normalized`` manifest block.

    The canonical ``trades.parquet`` is written only after every gate passes. A
    failure writes ``trades.parquet.bad`` instead and keeps it for inspection —
    the ``archive_ticks`` creed: nothing is pruned, the evidence stays.
    """
    a, b = _day_bounds(date)
    n, tmin, tmax, ids, imin, imax, bad_ms = con.execute(
        f"""SELECT count(*), min(ts_ms), max(ts_ms),
                   count(DISTINCT trade_id),
                   min(CAST(trade_id AS BIGINT)), max(CAST(trade_id AS BIGINT)),
                   count(*) FILTER (WHERE ts_ms <= 0 OR ts_ms >= {MAX_MS})
            FROM {rel}"""
    ).fetchone()
    if not n:
        raise VisionError(f"{date}: zero rows after normalization — refusing to write an "
                          "empty partition (an absent day is ABSENT, not zero-filled)")
    if bad_ms:
        raise VisionError(
            f"{date}: {bad_ms} row(s) have a transact_time outside epoch MILLISECONDS "
            f"(>= {MAX_MS} means microseconds — the spot layout). Refusing.")
    if not (a <= tmin and tmax < b):
        raise VisionError(
            f"{date}: rows fall outside the event-time day "
            f"([{tmin}, {tmax}] vs [{a}, {b})) — a partition IS its day; refusing")
    if ids != n:
        raise VisionError(
            f"{date}: {n - ids} duplicate agg_trade_id row(s) in the source file "
            "— duplicates are corruption whatever their provenance; refusing")

    span = int(imax) - int(imin) + 1
    holes = span - int(n)
    hole_ranges = _id_hole_ranges(con, rel) if holes else []

    dest.parent.mkdir(parents=True, exist_ok=True)
    bad_dest = dest.with_suffix(dest.suffix + ".bad")
    tmp = bad_dest if keep_bad else dest
    esc = str(tmp).replace("'", "''")
    con.execute(
        f"COPY (SELECT * FROM {rel} ORDER BY ts_ms) TO '{esc}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD)")

    # G7 — re-read verify: the written file, not the relation that produced it.
    resc = esc
    rn, rtmin, rtmax = con.execute(
        f"SELECT count(*), min(ts_ms), max(ts_ms) FROM read_parquet('{resc}')").fetchone()
    if (int(rn), int(rtmin), int(rtmax)) != (int(n), int(tmin), int(tmax)):
        raise VisionError(
            f"{date}: parquet re-read verification FAILED — wrote ({n}, {tmin}, {tmax}), "
            f"read back ({rn}, {rtmin}, {rtmax}); bad file kept at {tmp}")
    if keep_bad:
        os.replace(tmp, dest)

    return {
        "table": "trades",
        "rows": int(n), "ts_min": int(tmin), "ts_max": int(tmax),
        "id_min": int(imin), "id_max": int(imax), "id_distinct": int(ids),
        "id_span": int(span), "id_holes": int(holes), "id_hole_ranges": hole_ranges,
        "bytes": dest.stat().st_size, "sha256": sha256_file(dest),
        "parquet": dest.name,
    }


# --------------------------------------------------------------------------- #
# Seam (G6) + resume state                                                     #
# --------------------------------------------------------------------------- #
def _prev_date(date: str) -> str:
    d = datetime.strptime(date, "%Y-%m-%d").date() - timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def seam_check(out_root: Path, venue: str, symbol: str, family: str, date: str,
               id_min: int) -> dict[str, Any]:
    """G6: is ``first_id(D) == last_id(D-1) + 1``?

    Reported in both manifests, **never patched**. This is the census that makes
    the archive stronger than a timestamp heuristic: an ID discontinuity across a
    day boundary is a fact about the venue's own counter, not an inference about
    silence. Measured across 2026-07-31 -> 2026-08-01: 3399378199 -> 3399378200,
    exactly +1.
    """
    prev = _prev_date(date)
    mp = manifest_path(out_root, venue, symbol, family, prev)
    if not mp.exists():
        return {"prev_date": prev, "prev_present": False, "contiguous": None}
    try:
        prev_man = json.loads(mp.read_text(encoding="utf-8"))
        prev_max = int(prev_man["normalized"]["id_max"])
    except Exception:  # noqa: BLE001 — an unreadable neighbour is not this day's fault
        return {"prev_date": prev, "prev_present": False, "contiguous": None}
    return {
        "prev_date": prev, "prev_present": True, "prev_id_max": prev_max,
        "first_id": int(id_min), "gap": int(id_min) - prev_max - 1,
        "contiguous": int(id_min) == prev_max + 1,
    }


def day_state(out_root: Path, venue: str, symbol: str, family: str,
              date: str) -> Optional[dict[str, Any]]:
    """The manifest of an already-complete day, or ``None``.

    Complete means: manifest present **and** the parquet it names present **and**
    the size it recorded still matching. A truncated or half-written file is not
    "already done" — it is redone.
    """
    mp = manifest_path(out_root, venue, symbol, family, date)
    if not mp.exists():
        return None
    try:
        man = json.loads(mp.read_text(encoding="utf-8"))
        norm = man["normalized"]
        pq = partition_dir(out_root, venue, symbol, family, date) / norm["parquet"]
        if pq.exists() and pq.stat().st_size == int(norm["bytes"]):
            return man
    except Exception:  # noqa: BLE001 — an unreadable manifest means "redo it"
        return None
    return None


def append_ledger(out_root: Path, row: dict[str, Any]) -> None:
    """Append-only, one row per day ATTEMPTED — including absent and failed ones.

    Deliberately NOT ``data/ticks/levels.jsonl``: that registry is the sole input
    to the MinBTL countdown and archive days must never enter it (rail 3).
    """
    p = ledger_path(out_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- #
# One day, end to end                                                          #
# --------------------------------------------------------------------------- #
def ingest_day(*, date: str, out_root: Path, market: str, family: str,
               vendor_symbol: str, venue: str, symbol: str, granularity: str = "daily",
               opener=None, force: bool = False, keep_zip: Optional[Path] = None,
               tmp_dir: Optional[Path] = None, say=print) -> dict[str, Any]:
    """Fetch + gate + normalize + write ONE archive day. Never raises for an
    absent day; a gate failure is captured into the returned row (``status =
    "failed"``) so the run continues to the next day.

    Rails 1 and 2 are asserted HERE, where the write happens, not only in
    ``main()``: a rail that only the CLI enforces is not a rail, it is a habit of
    the CLI. Both checks are cheap and idempotent, so paying them per day costs
    nothing and covers every caller — tests, notebooks, a future scheduler."""
    _require_duckdb()
    check_target(market, family, vendor_symbol, venue, symbol)
    assert_out_root_is_separate(out_root)
    t0 = time.time()
    if not force:
        done = day_state(out_root, venue, symbol, family, date)
        if done is not None:
            return {"date": date, "status": "already", "rows": int(done["normalized"]["rows"]),
                    "bytes": int(done["normalized"]["bytes"]), "ms": 0}

    name = canonical_name(family, vendor_symbol, date)
    url = archive_url(market, granularity, family, vendor_symbol, date)
    part = partition_dir(out_root, venue, symbol, family, date)
    dest = part / "trades.parquet"
    tmpd = Path(tempfile.mkdtemp(prefix="vision-", dir=str(tmp_dir) if tmp_dir else None))
    try:
        # The zip lands under a ``.part`` name and is only ever referred to by
        # that name: nothing is called by its canonical name before G1 passes,
        # so a partial download can never be mistaken for a verified object.
        zip_path = tmpd / (name + ".part")
        try:
            zip_bytes, got, headers = http_download(url, zip_path, opener=opener)
            cks_text, _ = http_get(url + ".CHECKSUM", opener=opener)
        except HttpAbsent as exc:
            # Rail 5: absence is an ANSWER. No file, no zero row, no interpolation.
            row = {"date": date, "status": "absent", "http_status": exc.status, "url": url,
                   "rows": 0, "bytes": 0, "ms": int((time.time() - t0) * 1000)}
            append_ledger(out_root, row | {"family": family, "symbol": symbol,
                                           "venue": venue})
            return row

        # ---- G1 checksum ----
        want = parse_checksum(cks_text.decode("utf-8", "replace"), name)
        if got != want:
            raise VisionError(
                f"{date}: CHECKSUM mismatch — archive says {want}, downloaded bytes hash "
                f"{got} ({zip_bytes} B). Refusing; nothing written.")
        if keep_zip is not None:
            keep_zip.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(zip_path, keep_zip)

        # ---- G2 zip shape + G3 header sniff ----
        zf, member = open_single_csv(zip_path, Path(name).stem)
        with zf.open(member) as fh:
            first = fh.readline().decode("utf-8", "replace")
        header_present = sniff_header(first)
        check_columns(first, header_present)

        csv_path = tmpd / member
        with zf.open(member) as fh, open(csv_path, "wb") as out:
            shutil.copyfileobj(fh, out, length=4 * 1024 * 1024)
        csv_bytes = csv_path.stat().st_size
        zf.close()

        # ---- normalize + G4/G5/G7 ----
        con = duckdb.connect()
        try:
            con.execute("SET enable_progress_bar=false")
            con.execute(
                f"CREATE TEMP TABLE norm AS "
                f"{_select_trades_sql(_csv_relation_sql(csv_path, header_present), venue, symbol)}")
            norm = write_day_parquet(con, "norm", dest, date)
            extents = con.execute(
                f"SELECT min(first_trade_id), max(last_trade_id) FROM "
                f"{_csv_relation_sql(csv_path, header_present)}").fetchone()
        finally:
            con.close()

        # ---- G6 seam ----
        seam = seam_check(out_root, venue, symbol, family, date, norm["id_min"])

        manifest = {
            "date": date,
            "createdMs": int(time.time() * 1000),
            "provenance": PROVENANCE,
            "source": {
                "host": HOST, "url": url, "granularity": granularity, "market": market,
                "family": family, "vendor_symbol": vendor_symbol,
                "zip_bytes": zip_bytes, "zip_sha256": got, "checksum_verified": True,
                "etag": headers.get("etag"), "last_modified": headers.get("last-modified"),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
            "csv": {"member": member, "bytes": csv_bytes,
                    "header_row_present": header_present, "columns": list(AGG_COLUMNS)},
            "normalized": norm | {
                "exchange": venue, "symbol": symbol,
                "first_trade_id_min": int(extents[0]) if extents[0] is not None else None,
                "last_trade_id_max": int(extents[1]) if extents[1] is not None else None,
            },
            "seam": seam,
            "honest_limit": list(HONEST_LIMIT_SENTENCES),
        }
        mp = manifest_path(out_root, venue, symbol, family, date)
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        fp = failed_path(out_root, venue, symbol, family, date)
        if fp.exists():
            fp.unlink()  # this day now has a verified partition; the old failure is history

        row = {"date": date, "status": "ok", "rows": norm["rows"], "bytes": norm["bytes"],
               "zip_bytes": zip_bytes, "id_holes": norm["id_holes"],
               "seam_contiguous": seam.get("contiguous"),
               "header_row_present": header_present,
               "ms": int((time.time() - t0) * 1000)}
        append_ledger(out_root, row | {"family": family, "symbol": symbol, "venue": venue})
        return row
    except Exception as exc:  # noqa: BLE001 — one bad day never kills the run
        fp = failed_path(out_root, venue, symbol, family, date)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps({
            "date": date, "createdMs": int(time.time() * 1000), "url": url,
            "error": str(exc), "provenance": PROVENANCE,
            "note": "no canonical parquet was written for this day; any partial artifact "
                    "is kept as trades.parquet.bad for inspection",
        }, indent=2) + "\n", encoding="utf-8")
        row = {"date": date, "status": "failed", "error": str(exc), "rows": 0, "bytes": 0,
               "ms": int((time.time() - t0) * 1000)}
        append_ledger(out_root, row | {"family": family, "symbol": symbol, "venue": venue})
        say(f"  [{date}] FAILED — {exc}")
        return row
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Monthly: fewer requests, ZERO leniency                                       #
# --------------------------------------------------------------------------- #
def ingest_month(*, month: str, dates: list[str], out_root: Path, market: str, family: str,
                 vendor_symbol: str, venue: str, symbol: str, opener=None,
                 force: bool = False, tmp_dir: Optional[Path] = None,
                 keep_zip: Optional[Path] = None, sleep: float = 0.0,
                 say=print) -> list[dict[str, Any]]:
    """One monthly zip -> the same per-day partitions, through the same gates.

    Monthly buys request count (79 instead of 2,406 for the whole history) and
    nothing else: each day split out of it passes G4a/G4b/G5/G6/G7 exactly as a
    daily file does. Verified set-identical to the daily files it contains
    (2020-01 sliced to 2020-01-01: 71,359 = 71,359 rows, ``EXCEPT`` empty both
    ways). Day bucketing is ``ts_ms // 86_400_000`` — never a local-time function.

    Two things it must NOT do, both learned the hard way:

    * **A missing MONTHLY object is not an absent DAY.** Binance publishes the
      monthly bundle days after the month ends, so a run in the first days of a
      month would record an entire published month as "not served" — a FABRICATED
      ABSENCE, the inverse of rail 5. On a 404 the month falls back to the daily
      objects and lets each day answer for itself.
    * **Re-download a month whose days are already complete.** The daily path
      returns ``already`` before any request; the monthly path pulled ~530 MB
      first and only then checked, so resuming an interrupted ``--all`` re-pulled
      every finished month.
    """
    _require_duckdb()
    check_target(market, family, vendor_symbol, venue, symbol)
    assert_out_root_is_separate(out_root)
    if not _MONTH_RE.match(month):
        raise VisionError(f"bad month {month!r} — expected YYYY-MM")
    t0 = time.time()
    name = canonical_name(family, vendor_symbol, month)
    url = archive_url(market, "monthly", family, vendor_symbol, month)

    def _fallback_daily(reason: str) -> list[dict[str, Any]]:
        """Let each day answer for itself, through the identical daily path."""
        say(f"  [{month}] {reason} — falling back to {len(dates)} daily object(s)")
        out: list[dict[str, Any]] = []
        for i, d in enumerate(dates):
            out.append(ingest_day(
                date=d, out_root=out_root, market=market, family=family,
                vendor_symbol=vendor_symbol, venue=venue, symbol=symbol,
                opener=opener, force=force, tmp_dir=tmp_dir, say=say,
                keep_zip=(keep_zip.parent / canonical_name(family, vendor_symbol, d)
                          if keep_zip else None)))
            if sleep and i + 1 < len(dates):
                time.sleep(sleep)
        return out

    # Resume BEFORE the wire: a month whose every day is already complete costs
    # zero bytes, exactly as the daily path does.
    if not force:
        done = {d: day_state(out_root, venue, symbol, family, d) for d in dates}
        if all(v is not None for v in done.values()):
            say(f"  [{month}] already complete ({len(dates)} day(s)) — nothing downloaded")
            return [{"date": d, "status": "already",
                     "rows": int(done[d]["normalized"]["rows"]),
                     "bytes": int(done[d]["normalized"]["bytes"]), "ms": 0}
                    for d in dates]

    tmpd = Path(tempfile.mkdtemp(prefix="vision-m-", dir=str(tmp_dir) if tmp_dir else None))
    try:
        zip_path = tmpd / (name + ".part")
        try:
            zip_bytes, got, headers = http_download(url, zip_path, opener=opener)
            cks_text, _ = http_get(url + ".CHECKSUM", opener=opener)
        except HttpAbsent as exc:
            # The MONTH bundle is not published. That says nothing about the days
            # — measured 2026-08-02: the 2026-08 monthly object was 404 while
            # every published day of it was 200. Marking them absent here would
            # write a day the archive DOES serve into the provenance ledger as
            # not served.
            return _fallback_daily(f"monthly object not published (HTTP {exc.status})")
        want = parse_checksum(cks_text.decode("utf-8", "replace"), name)
        if got != want:
            raise VisionError(f"{month}: monthly CHECKSUM mismatch ({want} vs {got})")
        if keep_zip is not None:
            keep_zip.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(zip_path, keep_zip)
        zf, member = open_single_csv(zip_path, Path(name).stem)
        with zf.open(member) as fh:
            first = fh.readline().decode("utf-8", "replace")
        header_present = sniff_header(first)
        check_columns(first, header_present)
        csv_path = tmpd / member
        with zf.open(member) as fh, open(csv_path, "wb") as out:
            shutil.copyfileobj(fh, out, length=4 * 1024 * 1024)
        csv_bytes = csv_path.stat().st_size
        zf.close()

        con = duckdb.connect()
        out_rows: list[dict[str, Any]] = []
        try:
            con.execute("SET enable_progress_bar=false")
            con.execute(
                f"CREATE TEMP TABLE mnorm AS "
                f"{_select_trades_sql(_csv_relation_sql(csv_path, header_present), venue, symbol)}")
            present = [str(r[0]) for r in con.execute(
                f"SELECT DISTINCT ts_ms // {MS_PER_DAY} FROM mnorm ORDER BY 1").fetchall()]
            present_days = {_day_of(int(k) * MS_PER_DAY) for k in present}
            # first_trade_id/last_trade_id extents per day, in ONE grouped scan
            # of the CSV. DESIGN §3d says the extents are kept in the manifest;
            # the daily path did it and the monthly path did not, so a
            # month-ingested day carried a structurally poorer manifest than the
            # same day ingested daily — "monthly buys request count, never
            # leniency" has to hold at the manifest level too.
            con.execute(
                f"CREATE TEMP TABLE mext AS SELECT transact_time // {MS_PER_DAY} AS d, "
                f"min(first_trade_id) AS f, max(last_trade_id) AS l FROM "
                f"{_csv_relation_sql(csv_path, header_present)} GROUP BY 1")
            extents_by_day = {int(d): (f, l) for d, f, l in
                              con.execute("SELECT d, f, l FROM mext").fetchall()}
            for date in dates:
                done = None if force else day_state(out_root, venue, symbol, family, date)
                if done is not None:
                    # The TRUE counts, like the daily path returns: a resumed run
                    # reporting "already 31 day(s), 0 rows already on disk" is the
                    # small kind of count-lie the summary split exists to avoid.
                    out_rows.append({"date": date, "status": "already",
                                     "rows": int(done["normalized"]["rows"]),
                                     "bytes": int(done["normalized"]["bytes"]), "ms": 0})
                    continue
                if date not in present_days:
                    # The monthly file exists but carries no row for this day.
                    # ABSENT, never zero-filled.
                    row = {"date": date, "status": "absent", "http_status": 200,
                           "url": url, "rows": 0, "bytes": 0,
                           "note": "monthly file published but carries no row for this day",
                           "ms": 0}
                    append_ledger(out_root, row | {"family": family, "symbol": symbol,
                                                   "venue": venue})
                    out_rows.append(row)
                    continue
                a, b = _day_bounds(date)
                con.execute("DROP TABLE IF EXISTS dnorm")
                con.execute(
                    f"CREATE TEMP TABLE dnorm AS SELECT * FROM mnorm "
                    f"WHERE ts_ms >= {a} AND ts_ms < {b}")
                dest = partition_dir(out_root, venue, symbol, family, date) / "trades.parquet"
                try:
                    norm = write_day_parquet(con, "dnorm", dest, date)
                    seam = seam_check(out_root, venue, symbol, family, date, norm["id_min"])
                    manifest = {
                        "date": date, "createdMs": int(time.time() * 1000),
                        "provenance": PROVENANCE,
                        "source": {
                            "host": HOST, "url": url, "granularity": "monthly",
                            "market": market, "family": family,
                            "vendor_symbol": vendor_symbol, "month": month,
                            "zip_bytes": zip_bytes, "zip_sha256": got,
                            "checksum_verified": True, "etag": headers.get("etag"),
                            "last_modified": headers.get("last-modified"),
                            "fetched_at": datetime.now(timezone.utc).isoformat(),
                        },
                        "csv": {"member": member, "bytes": csv_bytes,
                                "header_row_present": header_present,
                                "columns": list(AGG_COLUMNS)},
                        "normalized": norm | {
                            "exchange": venue, "symbol": symbol,
                            "first_trade_id_min": _int_or_none(
                                extents_by_day.get(a // MS_PER_DAY, (None, None))[0]),
                            "last_trade_id_max": _int_or_none(
                                extents_by_day.get(a // MS_PER_DAY, (None, None))[1]),
                        },
                        "seam": seam,
                        "honest_limit": list(HONEST_LIMIT_SENTENCES),
                    }
                    mp = manifest_path(out_root, venue, symbol, family, date)
                    mp.parent.mkdir(parents=True, exist_ok=True)
                    mp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                    row = {"date": date, "status": "ok", "rows": norm["rows"],
                           "bytes": norm["bytes"], "id_holes": norm["id_holes"],
                           "seam_contiguous": seam.get("contiguous"),
                           "header_row_present": header_present, "ms": 0}
                except Exception as exc:  # noqa: BLE001
                    fp = failed_path(out_root, venue, symbol, family, date)
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(json.dumps({
                        "date": date, "createdMs": int(time.time() * 1000), "url": url,
                        "error": str(exc), "provenance": PROVENANCE}, indent=2) + "\n",
                        encoding="utf-8")
                    row = {"date": date, "status": "failed", "error": str(exc),
                           "rows": 0, "bytes": 0, "ms": 0}
                    say(f"  [{date}] FAILED — {exc}")
                if row["status"] == "ok" and not any(
                        r.get("zip_bytes") for r in out_rows):
                    # One monthly zip covers many days. Charge its bytes ONCE, to
                    # the first day it produced, so a summary total is the bytes
                    # actually pulled off the wire rather than that number times
                    # the number of days in the month.
                    row["zip_bytes"] = zip_bytes
                    row["zip_shared_with_days"] = len(dates)
                append_ledger(out_root, row | {"family": family, "symbol": symbol,
                                               "venue": venue})
                out_rows.append(row)
        finally:
            con.close()
        say(f"  [{month}] monthly {zip_bytes:,} B zip -> {len(out_rows)} day(s) "
            f"in {time.time() - t0:,.1f}s")
        return out_rows
    except Exception as exc:  # noqa: BLE001 — one bad MONTH never kills the run
        # `except VisionError` was too narrow: a duckdb ConversionException from
        # the month-wide CREATE TEMP TABLE, or a zipfile.BadZipFile from the
        # member read, escaped here, escaped main(), and aborted the whole
        # backfill with a traceback — no ledger row, no FAILED json, no exit
        # code. `auto` routes every whole past month through this function, so
        # that was the PRIMARY path of the documented 79-object run. Mirrors
        # ingest_day: each day the month owed gets its FAILED-<date>.json and its
        # ledger row, and the run continues and exits non-zero.
        rows = []
        for d in dates:
            fp = failed_path(out_root, venue, symbol, family, d)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps({
                "date": d, "createdMs": int(time.time() * 1000), "url": url,
                "error": str(exc), "granularity": "monthly", "month": month,
                "provenance": PROVENANCE,
                "note": "the MONTHLY object failed before this day could be written; no "
                        "canonical parquet exists for it",
            }, indent=2) + "\n", encoding="utf-8")
            row = {"date": d, "status": "failed", "error": str(exc), "rows": 0,
                   "bytes": 0, "ms": 0}
            append_ledger(out_root, row | {"family": family, "symbol": symbol,
                                           "venue": venue})
            rows.append(row)
        say(f"  [{month}] FAILED — {exc}")
        return rows
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Range planning                                                               #
# --------------------------------------------------------------------------- #
def dates_inclusive(start: str, end: str) -> list[str]:
    """Every ``YYYY-MM-DD`` in ``[start, end]`` — BOTH ends inclusive.

    Deliberately not the half-open convention the bar API uses: the unit here is
    a file named after a date, not a time window, and "--end 2026-08-01" that
    silently skipped 2026-08-01 would be a trap.
    """
    if not _DATE_RE.match(start) or not _DATE_RE.match(end):
        raise VisionError("--start/--end must be YYYY-MM-DD")
    a = datetime.strptime(start, "%Y-%m-%d").date()
    b = datetime.strptime(end, "%Y-%m-%d").date()
    if b < a:
        raise VisionError(f"--end {end} is before --start {start}")
    out, d = [], a
    while d <= b:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def plan_granularity(dates: list[str], granularity: str,
                     today: Optional[_date] = None) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Split a date list into ``([(month, days)], [daily days])``.

    ``auto``: a month goes monthly only when the range covers **every** day of it
    **and** the month is wholly in the past (a running month's monthly file does
    not exist yet, and a just-ended one can lag). Everything else is daily. That
    is 79 requests instead of 2,406 for the whole history, with no leniency —
    every split day still passes G4..G7.
    """
    if granularity == "daily":
        return [], list(dates)
    if granularity == "monthly":
        by_month: dict[str, list[str]] = {}
        for d in dates:
            by_month.setdefault(d[:7], []).append(d)
        return sorted(by_month.items()), []
    if granularity != "auto":
        raise VisionError(f"granularity={granularity!r} must be daily, monthly or auto")

    today = today or datetime.now(timezone.utc).date()
    cur_month = today.strftime("%Y-%m")
    by_month: dict[str, list[str]] = {}
    for d in dates:
        by_month.setdefault(d[:7], []).append(d)
    months: list[tuple[str, list[str]]] = []
    daily: list[str] = []
    for month, days in sorted(by_month.items()):
        y, m = int(month[:4]), int(month[5:7])
        n_days = (_date(y + (m == 12), (m % 12) + 1, 1) - _date(y, m, 1)).days
        if month < cur_month and len(days) == n_days:
            months.append((month, days))
        else:
            daily.extend(days)
    return months, sorted(daily)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024.0:
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n:,.0f} B"
        n /= 1024.0
    return f"{n:,.1f} PiB"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ingest_vision.py",
        description=(
            "Ingest the PUBLIC Binance aggTrades archive into data/vision/ parquet "
            "(DESIGN §3d). aggTrades is TRADES ONLY: it extends CVD/footprint/delta/VPIN "
            "and gives the book families nothing. Archive rows never enter the recorded "
            "store and never count toward the MinBTL readiness countdown."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--start", help="first UTC day, YYYY-MM-DD (inclusive)")
    p.add_argument("--end", help="last UTC day, YYYY-MM-DD (INCLUSIVE)")
    p.add_argument("--all", action="store_true",
                   help="every published day (resolved from the bucket listing, never "
                        "hard-coded). Needs --yes: it is ~41 GiB of zip.")
    p.add_argument("--granularity", default="auto", choices=("auto", "daily", "monthly"),
                   help="monthly for whole past months (79 requests vs 2,406), daily for "
                        "the ragged ends; every day passes the same gates either way")
    p.add_argument("--market", default="futures/um", help="allowlisted: futures/um")
    p.add_argument("--family", default="aggTrades", help="allowlisted: aggTrades")
    p.add_argument("--vendor-symbol", default="BTCUSDT", help="symbol as the ARCHIVE names it")
    p.add_argument("--symbol", default=None, help="symbol as the repo stores it "
                                                  "(default: --vendor-symbol)")
    p.add_argument("--venue", default="binancef",
                   help="collector venue code written into the `exchange` column")
    p.add_argument("--out", default=str(DEFAULT_OUT),
                   help="archive tree root — refused if it overlaps the tick store")
    p.add_argument("--tmp-dir", default=None,
                   help="scratch dir for the extracted CSV (a monthly file can exceed 2 GB)")
    p.add_argument("--list", action="store_true",
                   help="enumerate what the archive publishes; downloads NOTHING")
    p.add_argument("--dry-run", action="store_true",
                   help="plan + honest-limit report only; downloads NOTHING")
    p.add_argument("--keep-zip", default=None,
                   help="directory to keep each CHECKSUM-VERIFIED zip in (debugging: the "
                        "bytes a partition was built from). Off by default — the full "
                        "history is 41 GiB of zip on top of the parquet.")
    p.add_argument("--force", action="store_true", help="re-ingest days already complete")
    p.add_argument("--max-days", type=int, default=0, help="stop after N days (0 = no cap)")
    p.add_argument("--sleep", type=float, default=0.2, help="seconds between requests")
    p.add_argument("--yes", action="store_true", help="confirm a large --all run")
    p.add_argument("--json", action="store_true", help="machine-readable summary")
    return p


def _report(summary: dict[str, Any], say=print) -> None:
    p = summary["params"]
    say(f"vision ingest — {p['market']} / {p['family']} / {p['vendor_symbol']} "
        f"-> {p['venue']}")
    if summary.get("range"):
        say(f"range {summary['range'][0]} .. {summary['range'][-1]} "
            f"({len(summary['range'])} day(s)); granularity {p['granularity']} -> "
            f"{summary['plan']['months']} monthly file(s) + {summary['plan']['daily']} daily")
    c = summary["counts"]
    say(f"  fetched  {c['ok']:>4} day(s)   {summary['rows']:,} rows   "
        f"{_fmt_bytes(summary['zip_bytes'])} zip -> {_fmt_bytes(summary['bytes'])} parquet")
    say(f"  already  {c['already']:>4} day(s)   {summary['already_rows']:,} rows already on "
        f"disk ({_fmt_bytes(summary['already_bytes'])} parquet; manifest + size verified, "
        "nothing re-downloaded)")
    for row in summary["days"]:
        if row["status"] == "absent":
            say(f"  absent   {row['date']} — not published (HTTP "
                f"{row.get('http_status', '?')}); NO file written, NO zero row")
    say(f"  absent   {c['absent']:>4} day(s)")
    say(f"  failed   {c['failed']:>4} day(s)")
    say(f"id continuity: {summary['id_holes']} in-day hole(s); "
        f"{summary['seams_checked']} seam(s) checked, {summary['seams_contiguous']} contiguous")
    say(f"tree: {summary['tree']}")
    for s in HONEST_LIMIT_SENTENCES:
        say(s)


def main(argv: Optional[list[str]] = None, *, opener=None) -> int:
    args = _build_parser().parse_args(argv)
    symbol = args.symbol or args.vendor_symbol
    venue = args.venue
    try:
        # Scope AND target: what may be downloaded, and where it may land. The
        # writers assert the same thing (a rail only main() holds is not a rail);
        # doing it here too fails a bad invocation before the first request
        # instead of on the first day.
        check_target(args.market, args.family, args.vendor_symbol, venue, symbol)
    except VisionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        out_root = assert_out_root_is_separate(Path(args.out))
    except VisionError as exc:
        # Exit 2, distinct from a scope error: the tree separation is the rail
        # every other rail stands on (DESIGN §0.7 rail a), so it gets its own code.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    params = {"market": args.market, "family": args.family,
              "vendor_symbol": args.vendor_symbol, "symbol": symbol, "venue": venue,
              "granularity": args.granularity, "out": str(out_root)}

    if args.list:
        try:
            days = published_days(args.market, args.family, args.vendor_symbol,
                                  opener=opener, sleep=args.sleep)
        except (VisionError, HttpAbsent) as exc:
            # A 404 on the LISTING is not the same as a 404 on a day: it means the
            # prefix itself does not exist, which is a usage error, not an answer.
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if not days:
            print("archive publishes nothing under that prefix")
            return 1
        keys = sorted(days)
        lo, hi = keys[0], keys[-1]
        want = dates_inclusive(lo, hi)
        if args.start and args.end:
            want = dates_inclusive(args.start, args.end)
        missing = [d for d in want if d not in days]
        total = sum(days[d] for d in want if d in days)
        out = {"earliest": lo, "latest": hi, "published_days": len(days),
               "range_days": len(want), "missing_days": missing,
               "zip_bytes_in_range": total,
               # The limit travels with the NUMBERS, not only with the prose:
               # --json is the path most likely to be piped into a report, i.e.
               # exactly where "trades only" most needs to arrive.
               "honest_limit": list(HONEST_LIMIT_SENTENCES)}
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(f"published {lo} .. {hi}: {len(days):,} daily file(s)")
            print(f"range {want[0]} .. {want[-1]}: {len(want) - len(missing):,} present, "
                  f"{len(missing):,} absent, {_fmt_bytes(total)} of zip")
            if missing:
                print("absent: " + ", ".join(missing[:20]) +
                      (f" (+{len(missing) - 20} more)" if len(missing) > 20 else ""))
            for s in HONEST_LIMIT_SENTENCES:
                print(s)
        return 0

    if args.all:
        try:
            days = published_days(args.market, args.family, args.vendor_symbol,
                                  opener=opener, sleep=args.sleep)
        except (VisionError, HttpAbsent) as exc:
            # A 404 on the LISTING is not the same as a 404 on a day: it means the
            # prefix itself does not exist, which is a usage error, not an answer.
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        keys = sorted(days)
        if not keys:
            print("ERROR: archive publishes nothing under that prefix", file=sys.stderr)
            return 1
        dates = dates_inclusive(keys[0], keys[-1])
        total = sum(days.values())
        print(f"--all: {len(keys):,} published day(s) {keys[0]} .. {keys[-1]}, "
              f"{_fmt_bytes(total)} of zip to download")
        if not args.yes:
            print("refusing without --yes (this is a multi-hour, multi-GiB run)")
            return 1
    elif args.start and args.end:
        dates = dates_inclusive(args.start, args.end)
    else:
        print("ERROR: pass --start/--end, or --all, or --list", file=sys.stderr)
        return 1

    if args.max_days and len(dates) > args.max_days:
        dates = dates[: args.max_days]

    months, daily = plan_granularity(dates, args.granularity)
    summary: dict[str, Any] = {
        "params": params, "range": dates,
        "plan": {"months": len(months), "daily": len(daily)},
        "days": [], "rows": 0, "bytes": 0, "zip_bytes": 0, "id_holes": 0,
        "already_rows": 0, "already_bytes": 0,
        "seams_checked": 0, "seams_contiguous": 0,
        "counts": {"ok": 0, "already": 0, "absent": 0, "failed": 0},
        "tree": str(partition_dir(out_root, venue, symbol, args.family,
                                  dates[0]).parent / "date=*/trades.parquet"),
        # Every --json branch serializes this dict, so the sentences _report
        # prints on every human run ride along on every machine one too.
        "honest_limit": list(HONEST_LIMIT_SENTENCES),
    }

    if args.dry_run:
        summary["dry_run"] = True
        if args.json:
            print(json.dumps({k: v for k, v in summary.items() if k != "range"} |
                             {"range": [dates[0], dates[-1]]}, indent=2))
        else:
            _report(summary, say=print)
            print("dry run — nothing downloaded, nothing written")
        return 0

    tmp_dir = Path(args.tmp_dir) if args.tmp_dir else None
    keep_zip_dir = Path(args.keep_zip) if args.keep_zip else None
    rows: list[dict[str, Any]] = []
    for month, mdays in months:
        rows.extend(ingest_month(
            month=month, dates=mdays, out_root=out_root, market=args.market,
            family=args.family, vendor_symbol=args.vendor_symbol, venue=venue,
            symbol=symbol, opener=opener, force=args.force, tmp_dir=tmp_dir,
            sleep=args.sleep,
            keep_zip=(keep_zip_dir / canonical_name(args.family, args.vendor_symbol, month)
                      if keep_zip_dir else None)))
        if args.sleep:
            time.sleep(args.sleep)
    for date in daily:
        rows.append(ingest_day(
            date=date, out_root=out_root, market=args.market, family=args.family,
            vendor_symbol=args.vendor_symbol, venue=venue, symbol=symbol,
            opener=opener, force=args.force, tmp_dir=tmp_dir,
            keep_zip=(keep_zip_dir / canonical_name(args.family, args.vendor_symbol, date)
                      if keep_zip_dir else None)))
        if args.sleep:
            time.sleep(args.sleep)

    rows.sort(key=lambda r: r["date"])
    for r in rows:
        summary["counts"][r["status"]] = summary["counts"].get(r["status"], 0) + 1
        # Rows/bytes FETCHED this run are kept apart from rows/bytes that were
        # already on disk: a resumed run reporting 1.5 M "fetched" rows it never
        # downloaded is a small lie, and small lies about counts are how a
        # provenance story starts drifting from the tree it describes.
        if r["status"] == "already":
            summary["already_rows"] += int(r.get("rows") or 0)
            summary["already_bytes"] += int(r.get("bytes") or 0)
            continue
        summary["rows"] += int(r.get("rows") or 0)
        summary["bytes"] += int(r.get("bytes") or 0)
        summary["zip_bytes"] += int(r.get("zip_bytes") or 0)
        summary["id_holes"] += int(r.get("id_holes") or 0)
        if r.get("seam_contiguous") is not None:
            summary["seams_checked"] += 1
            summary["seams_contiguous"] += int(bool(r["seam_contiguous"]))
    summary["days"] = rows

    if args.json:
        print(json.dumps(summary | {"range": [dates[0], dates[-1]]}, indent=2))
    else:
        _report(summary)
    return 1 if summary["counts"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
