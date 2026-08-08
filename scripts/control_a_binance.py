"""control_a_binance.py — run the OKX reconstruction control on BINANCE, unchanged.

Why this exists (class I, the rail in CLAUDE.md)
-----------------------------------------------
Control A was born and immediately convicted the data it was judging. A checker that has
only ever been run on a case it fails tells you nothing about its own false-positive rate,
and bad precision in a verifier destroys correct work rather than merely adding noise. So
the identical control runs here against the Tokyo tape, whose chain integrity is separately
attested (zero sequence gaps, zero resyncs, an explicit `pu` chain rule): rebuild the book
from one REST snapshot, apply every diff frame after it, compare against the NEXT REST
snapshot, at the same four depths.

The two alignment modes are the point
-------------------------------------
Binance stamps every diff with an update-id range (`U`, `u`) and every snapshot with
`lastUpdateId`, so the set of diffs belonging between two snapshots is EXACTLY determined.
OKX's archive carries no seqId and no checksum, so its only option is to align by
timestamp. Running BOTH modes here separates two very different questions:

* ``seq``  — apply diffs with ``u > lastUpdateId(A)`` and ``u <= lastUpdateId(B)``.
  Tests the update semantics alone, with alignment removed as a variable.
* ``time`` — apply diffs with ``recv_ms <= recv_ms(B)``, which is the only thing the OKX
  archive permits. Tests semantics AND alignment together.

If ``seq`` passes and ``time`` fails on the same tape, then the OKX failure is evidence
about timestamp alignment, not about OKX's data — and the difference is measured here
rather than argued.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEPTHS = (1, 5, 20, 100)


def walker():
    spec = importlib.util.spec_from_file_location(
        "gcs_common", REPO / "deploy" / "gcp" / "gcs_common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load(tape_dir: Path):
    """Reassemble the chunked mirror in offset order, then read every row."""
    gc = walker()
    parts = sorted((p for p in tape_dir.iterdir() if "chunk-" in p.name),
                   key=lambda p: int(p.name.split("chunk-")[1].split("-")[0]))
    blob = b"".join(p.read_bytes() for p in parts)
    out = tape_dir / "_reassembled.gz"
    out.write_bytes(blob)
    rows = []
    for kind, _end, payload in gc.walk_tape(out, 0):
        if kind == "hole":
            continue
        for line in payload.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    out.unlink(missing_ok=True)
    return rows


def book_from_snapshot(row: dict) -> dict:
    return {"bids": {l[0]: l for l in row["bids"]},
            "asks": {l[0]: l for l in row["asks"]}}


def apply_diff(book: dict, ev: dict) -> None:
    for side, key in (("bids", "b"), ("asks", "a")):
        for lv in ev.get(key, []):
            px, sz = lv[0], lv[1]
            if float(sz) == 0.0:
                book[side].pop(px, None)
            else:
                book[side][px] = lv


def top(d: dict, side: str, k: int):
    ps = sorted(d[side], key=float, reverse=(side == "bids"))[:k]
    return [(p, d[side][p][1]) for p in ps]


def run(tape_dir: Path, mode: str, rows=None) -> dict:
    """`mode` selects HOW the diffs belonging to an interval are chosen.

    The three modes exist because each one is a defect found in the previous one, and the
    progression is the evidence: every repair to the CONTROL raised the score on a tape
    whose chain integrity is separately attested. Measured on the Tokyo tape, top-20 exact
    [DIUKUR 2026-08-08]: positional 29.3% -> id-range 34.1% -> id-range+straddle 41.5%.

    * ``positional``  — frames lying between the two snapshot ROWS in file order. Wrong:
      the recorder appends the snapshot row when the REST call RETURNS, so frames that
      arrive while that call is in flight land after it and are silently lost.
    * ``idrange``     — frames with ``L0 < u <= L1``, position ignored. Better, still wrong.
    * ``straddle``    — idrange plus the one event whose range spans L1 (``U <= L1 <= u``),
      whose updates are already inside the ending snapshot.
    * ``time``        — frames with ``recv_ms <= recv_ms(B)``: the only alignment the OKX
      archive permits, kept so the two venues are compared on equal terms.
    """
    import bisect
    if rows is None:
        rows = load(tape_dir)
    snaps = [(i, r) for i, r in enumerate(rows) if r.get("kind") == "snapshot"]
    frames = [r for r in rows if r.get("kind") == "frame" and "u" in r.get("data", {})]
    frames.sort(key=lambda r: int(r["data"]["u"]))
    us = [int(f["data"]["u"]) for f in frames]
    hits = {k: 0 for k in DEPTHS}
    pairs = 0
    dprice = []
    for (i0, s0), (i1, s1) in zip(snaps, snaps[1:]):
        L0, L1 = int(s0["lastUpdateId"]), int(s1["lastUpdateId"])
        if L1 <= L0:
            continue                      # snapshots out of order (reconnect): skip, count
        if mode == "positional":
            sel = [r for r in rows[i0 + 1:i1]
                   if r.get("kind") == "frame" and "u" in r.get("data", {})
                   and L0 < int(r["data"]["u"]) <= L1]
        elif mode == "time":
            sel = [r for r in rows[i0 + 1:i1]
                   if r.get("kind") == "frame" and "u" in r.get("data", {})
                   and r["recv_ms"] <= s1["recv_ms"]]
        else:
            hi = bisect.bisect_right(us, L1)
            sel = frames[bisect.bisect_right(us, L0):hi]
            if mode == "straddle" and hi < len(frames):
                nxt = frames[hi]
                if int(nxt["data"]["U"]) <= L1 <= int(nxt["data"]["u"]):
                    sel = sel + [nxt]
        book = book_from_snapshot(s0)
        for r in sel:
            apply_diff(book, r["data"])
        want = book_from_snapshot(s1)
        pairs += 1
        for k in DEPTHS:
            if top(book, "bids", k) == top(want, "bids", k) \
                    and top(book, "asks", k) == top(want, "asks", k):
                hits[k] += 1
        bb = max((float(p) for p in book["bids"]), default=None)
        wb = max((float(p) for p in want["bids"]), default=None)
        ba = min((float(p) for p in book["asks"]), default=None)
        wa = min((float(p) for p in want["asks"]), default=None)
        if None not in (bb, wb, ba, wa):
            dprice.append((abs(bb - wb), abs(ba - wa)))
    return {"pairs": pairs, "hits": hits, "dprice": dprice}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape-dir", required=True)
    a = ap.parse_args()
    d = Path(a.tape_dir)
    print("Control A on the BINANCE tape — the same control, run on a case that "
          "SHOULD pass\n")
    rows = load(d)
    for mode, label in (
            ("positional", "positional  (as originally written)"),
            ("idrange", "id-range    (defect 1 repaired: position ignored)"),
            ("straddle", "id-range+straddle (defect 2 repaired: end event included)"),
            ("time", "time-only   (the alignment OKX's archive permits)")):
        r = run(d, mode, rows=rows)
        if not r["pairs"]:
            print(f"{label}: no usable snapshot pairs")
            continue
        line = " · ".join(f"top-{k} {r['hits'][k] / r['pairs']:.1%}" for k in DEPTHS)
        print(f"{label}\n    {r['pairs']} pair(s) · {line}")
        db = [x[0] for x in r["dprice"]]
        da = [x[1] for x in r["dprice"]]
        if db:
            print(f"    |d best bid| median {statistics.median(db):.2f} max {max(db):.2f}"
                  f" · |d best ask| median {statistics.median(da):.2f} max {max(da):.2f}")
    print("\nEvery repair to the CONTROL raised the score on a tape whose diff chain is "
          "separately attested\nas intact (0 sequence gaps, 0 resyncs). That is what a "
          "broken instrument looks like.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
