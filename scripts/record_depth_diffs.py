"""record_depth_diffs.py — raw L2 diff tape for binancef BTCUSDT, append-only.

Why this exists
---------------
The collector stores the MERGED book downsampled to one `depth_snapshots` row per second, and
`docs/DIAG-cost-ledger-001.md` §2a-2b established the consequence: queue position cannot be
reconstructed from state snapshots at ANY cadence, and the diff stream cannot be backfilled —
Binance Vision publishes aggTrades only, and no venue here publishes historical depth diffs.
Every day this is not recording is microstructure history lost permanently. This script closes
the gap FORWARD by recording the raw `depthUpdate` wire, unmerged and undownsampled.

[CORRECTION 2026-08-08, `docs/DIAG-data-ceiling-001.md`] The sentence above is kept verbatim
because it is what this file claimed, and half of it is wrong. "Binance Vision publishes
aggTrades only" is FALSE: the complete listing carries nine daily USDS-M dataset families
(aggTrades, bookDepth, bookTicker, indexPriceKlines, klines, markPriceKlines, metrics,
premiumIndexKlines, trades). The load-bearing clause survives measurement: no depth-diff
dataset exists anywhere in that bucket — bookTicker is L1 quotes whose archive stopped at
2024-03-30, and bookDepth is 12 percentage bands sampled every ~30 s, neither of which is a
diff stream. So this recorder's reason to exist is unchanged; only its premise was too narrow.
One venue-level exception, also measured that day: OKX DOES publish keyless daily L2
400-level snapshot+update archives back to 2023-12-04. That is a different venue and changes
nothing here.

What it records (and what it never does)
----------------------------------------
One JSONL.GZ file per UTC day under `data/depth_diffs/binancef/BTCUSDT/date=*/frames.jsonl.gz`
(gitignored — regenerable never, but bulky and machine-local until a sync decision is made).
Row kinds:

* ``frame``     — the raw exchange event, byte-preserved under ``data``, plus ``recv_ms``
                  (local receive time). EVERY frame is recorded, including ones that arrive
                  while the chain is broken: recording is not book-keeping, and a replayer
                  can decide what to do with them. Nothing is dropped, merged or repaired.
* ``snapshot``  — a REST book snapshot (`/fapi/v1/depth`, limit 1000): at start, on every
                  chain break, and every ``SNAPSHOT_EVERY_S`` as a replay anchor.
* ``gap``       — an explicit record that the update chain broke (``expected_pu`` vs
                  ``got_pu``) or the socket dropped. Gaps stay gaps: the record IS the
                  handling, and no interpolation ever happens here or downstream.
* ``start`` / ``stop`` — process lifecycle marks with versions and config.

Chain rule (Binance USDS-M futures, "How to manage a local order book correctly")
----------------------------------------------------------------------------------
The futures rule differs from spot and is encoded in ``classify_frame``: after a snapshot with
``lastUpdateId = L``, drop events whose ``u < L``; the first kept event must STRADDLE it
(``U <= L <= u``); every later event must have ``pu`` equal to the previous event's ``u``.
Any violation is a gap -> new snapshot. The classifier is a pure function so the tests can
exercise every branch without a network.

Keyless: the stream and the snapshot endpoint are public. No credentials exist in this
process, which is also why it is safe to run on a disposable cloud VM.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import signal
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_ROOT = REPO / "data" / "depth_diffs" / "binancef" / "BTCUSDT"
WS_URL = "wss://fstream.binance.com/stream?streams=btcusdt@depth@100ms"
SNAP_URL = "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=1000"
SNAPSHOT_EVERY_S = 900
FLUSH_EVERY = 200                 # frames per gzip append (one gzip member per flush)
UA = {"User-Agent": "btc-quant/1.0 (research; keyless)"}


# --------------------------------------------------------------------------- #
# Pure logic — tested without a network                                        #
# --------------------------------------------------------------------------- #
def classify_frame(ev_U: int, ev_u: int, ev_pu: int,
                   snapshot_last_id: int | None, prev_u: int | None) -> str:
    """FUTURES chain rule. Returns one of:

    ``drop_pre_snapshot`` — event predates the snapshot (u < lastUpdateId); recorded anyway,
                            but the chain state does not advance.
    ``first_ok``          — first event after a snapshot, straddling it (U <= L <= u).
    ``ok``                — pu equals the previous event's u; the chain continues.
    ``gap``               — anything else; the caller must re-snapshot.
    """
    if snapshot_last_id is None:
        return "gap"                       # no snapshot yet: nothing to chain against
    if prev_u is None:
        if ev_u < snapshot_last_id:
            return "drop_pre_snapshot"
        if ev_U <= snapshot_last_id <= ev_u:
            return "first_ok"
        return "gap"                       # snapshot went stale before the stream caught up
    return "ok" if ev_pu == prev_u else "gap"


def day_path(recv_ms: int) -> Path:
    d = time.strftime("%Y-%m-%d", time.gmtime(recv_ms / 1000))
    return OUT_ROOT / f"date={d}" / "frames.jsonl.gz"


# --------------------------------------------------------------------------- #
# I/O                                                                          #
# --------------------------------------------------------------------------- #
class Sink:
    """Append-only gzip JSONL, rotated at UTC midnight. Each flush is one gzip member;
    concatenated members are a valid gzip stream, so a kill between flushes loses at most
    the unflushed buffer and never corrupts the file."""

    def __init__(self) -> None:
        self.buf: list[bytes] = []
        self.buf_first_ms: int | None = None
        self.bytes_written = 0

    def add(self, row: dict) -> None:
        if self.buf_first_ms is None:
            self.buf_first_ms = int(row.get("recv_ms", time.time() * 1000))
        self.buf.append(json.dumps(row, separators=(",", ":")).encode() + b"\n")
        if len(self.buf) >= FLUSH_EVERY:
            self.flush()

    def flush(self) -> None:
        if not self.buf:
            return
        # Rotate by the FIRST buffered row's receive time, not the flush time: a buffer
        # filled at 23:59:59 and flushed at 00:00:01 belongs to the day its frames arrived
        # in. The test that caught this wrote rows stamped 2026-07-30 and found the file
        # under the flush day instead.
        p = day_path(self.buf_first_ms)
        p.parent.mkdir(parents=True, exist_ok=True)
        blob = gzip.compress(b"".join(self.buf))
        with open(p, "ab") as fh:
            fh.write(blob)
        self.bytes_written += len(blob)
        self.buf.clear()
        self.buf_first_ms = None


def read_frames(path: Path):
    """Member-tolerant reader for the tape this module writes.

    A SIGKILL mid-flush can leave a TRUNCATED final gzip member; `gzip.decompress` over the
    whole file then raises EOFError and a naive reader concludes the whole day is corrupt,
    when at most the last unflushed batch (<= FLUSH_EVERY rows, ~20 s) is gone. This reader
    decompresses member by member and stops cleanly at a truncated tail, yielding every row
    of every COMPLETE member. The truncation is data loss and is not hidden: the caller can
    compare the last row's recv_ms against the next day's first to bound it.
    """
    import zlib
    buf = path.read_bytes()
    while buf:
        d = zlib.decompressobj(wbits=31)
        try:
            chunk = d.decompress(buf)
        except zlib.error:
            return                                    # truncated tail: stop, do not invent
        if not d.eof:
            return                                    # incomplete member at the end
        for line in chunk.decode().splitlines():
            if line:
                yield json.loads(line)
        buf = d.unused_data


def rest_snapshot() -> dict:
    with urllib.request.urlopen(
            urllib.request.Request(SNAP_URL, headers=UA), timeout=15) as r:
        return json.loads(r.read())


# --------------------------------------------------------------------------- #
# Main loop                                                                    #
# --------------------------------------------------------------------------- #
async def run(smoke_s: float | None) -> dict:
    import websockets                                     # requirements-collector.txt

    sink = Sink()
    stats = {"frames": 0, "gaps": 0, "snapshots": 0, "drops_pre": 0,
             "chain_ok": 0, "started_ms": int(time.time() * 1000)}
    sink.add({"kind": "start", "recv_ms": stats["started_ms"],
              "ws": WS_URL, "snapshot_every_s": SNAPSHOT_EVERY_S})

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:                       # pragma: no cover
            pass

    async def take_snapshot(reason: str) -> int:
        snap = await asyncio.to_thread(rest_snapshot)
        now = int(time.time() * 1000)
        sink.add({"kind": "snapshot", "recv_ms": now, "reason": reason,
                  "lastUpdateId": snap["lastUpdateId"],
                  "E": snap.get("E"), "T": snap.get("T"),
                  "bids": snap["bids"], "asks": snap["asks"]})
        stats["snapshots"] += 1
        return int(snap["lastUpdateId"])

    deadline = time.time() + smoke_s if smoke_s else None
    prev_u: int | None = None          # visible to the reconnect gap rows above
    while not stop.is_set() and (deadline is None or time.time() < deadline):
        try:
            async with websockets.connect(WS_URL, ping_interval=20,
                                          ping_timeout=20, max_size=2**22) as ws:
                last_id = await take_snapshot("connect")
                prev_u = None
                next_snap = time.time() + SNAPSHOT_EVERY_S
                while not stop.is_set() and (deadline is None or time.time() < deadline):
                    raw = await asyncio.wait_for(ws.recv(),
                                                 timeout=min(30.0, deadline - time.time())
                                                 if deadline else 30.0)
                    recv_ms = int(time.time() * 1000)
                    msg = json.loads(raw)
                    ev = msg.get("data", msg)
                    if ev.get("e") != "depthUpdate":
                        continue
                    # Record FIRST, classify second. The refutation pass found the original
                    # order (classify -> add) meant a malformed frame — one passing the
                    # e == "depthUpdate" check but missing U/u/pu — raised BEFORE it was
                    # written, vanishing from the tape and tearing down the connection for
                    # one bad frame. "EVERY frame is recorded" has to include the broken ones.
                    row = {"kind": "frame", "recv_ms": recv_ms, "chain": "unclassified",
                           "data": ev}
                    try:
                        verdict = classify_frame(int(ev["U"]), int(ev["u"]), int(ev["pu"]),
                                                 last_id, prev_u)
                    except (KeyError, TypeError, ValueError) as e:
                        row["chain"] = f"malformed:{type(e).__name__}"
                        sink.add(row)
                        stats["frames"] += 1
                        continue                       # one bad frame is not a connection fault
                    row["chain"] = verdict
                    sink.add(row)
                    stats["frames"] += 1
                    if verdict in ("first_ok", "ok"):
                        prev_u = int(ev["u"])
                        stats["chain_ok"] += 1
                    elif verdict == "drop_pre_snapshot":
                        stats["drops_pre"] += 1
                    else:                                  # gap -> record + resync
                        stats["gaps"] += 1
                        sink.add({"kind": "gap", "recv_ms": recv_ms,
                                  "expected_pu": prev_u, "got_pu": ev.get("pu"),
                                  "action": "resnapshot"})
                        last_id = await take_snapshot("chain_break")
                        prev_u = None
                    if time.time() >= next_snap:
                        last_id_anchor = await take_snapshot("periodic")
                        # a periodic snapshot is an ANCHOR, not a resync: the chain
                        # continues from prev_u; last_id only matters after a break
                        del last_id_anchor
                        next_snap = time.time() + SNAPSHOT_EVERY_S
        except asyncio.TimeoutError:
            if deadline and time.time() >= deadline:
                break
            sink.add({"kind": "gap", "recv_ms": int(time.time() * 1000),
                      "expected_pu": prev_u, "got_pu": None, "action": "ws_timeout_reconnect"})
            stats["gaps"] += 1
        except Exception as e:  # noqa: BLE001 — the tape must survive transient wire faults
            sink.add({"kind": "gap", "recv_ms": int(time.time() * 1000),
                      "expected_pu": prev_u, "got_pu": None,
                      "action": f"reconnect:{type(e).__name__}"})
            stats["gaps"] += 1
            await asyncio.sleep(2.0)

    sink.add({"kind": "stop", "recv_ms": int(time.time() * 1000), "stats": dict(stats)})
    sink.flush()
    stats["bytes_written"] = sink.bytes_written
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", type=float, default=None,
                    help="run for N seconds, print measured stats, exit (control run)")
    a = ap.parse_args()
    stats = asyncio.run(run(a.smoke))
    dur = max((int(time.time() * 1000) - stats["started_ms"]) / 1000.0, 1e-9)
    mb_day = stats["bytes_written"] / dur * 86400 / 1e6
    print(f"depth-diff recorder — {dur:.1f}s · frames {stats['frames']:,} "
          f"({stats['frames']/dur:.1f}/s) · chain ok {stats['chain_ok']:,} · "
          f"gaps {stats['gaps']} · pre-snapshot drops {stats['drops_pre']} · "
          f"snapshots {stats['snapshots']}")
    print(f"  bytes written {stats['bytes_written']:,} -> extrapolated {mb_day:,.0f} MB/day "
          f"compressed  [DIUKUR over this run only]")
    if a.smoke and stats["frames"] == 0:
        print("  SMOKE FAIL: zero frames received")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
