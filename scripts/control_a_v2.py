"""control_a_v2.py — the reconstruction control, rebuilt after v1 was proven unsound.

What v1 got wrong, all of it measured on a tape that should have passed
---------------------------------------------------------------------
v1 asked "does the whole top-N match EXACTLY", and answered 29.3 % on the Tokyo tape whose
diff chain is separately attested at zero gaps and zero resyncs. Four defects, in the order
they were found and each one measured [DIUKUR 2026-08-08]:

1. **Positional frame selection.** The recorder appends the snapshot row when its REST call
   RETURNS, so frames arriving during that call land after it and were sliced away.
   29.3 % -> 34.1 %.
2. **The straddling end event was discarded** even though its updates are already inside the
   closing snapshot. 34.1 % -> 41.5 %.
3. **Pairs spanning a recording gap were scored.** Twelve of 41 pairs straddled a restart,
   during which no frame was ever received; they scored 0.0 % at every depth by construction.
   Excluding them: 41.5 % -> 59.3 % on the remaining 27.
4. **The criterion itself was impossible.** A REST snapshot is a point in time; a diff event
   is an ATOMIC range `[U, u]` aggregating thousands of individual updates. In 27 of 27
   clean pairs the snapshot's `lastUpdateId` fell STRICTLY INSIDE an event — never on a
   boundary — so no implementation can reproduce it exactly. Excluding the straddler
   under-applies; including it over-applies; the event cannot be split.

So v1 demanded something structurally unachievable and then blamed the data. What it
actually showed, once counted per LEVEL instead of all-or-nothing per pair, is **97.8 %
of levels matching exactly** — a single stale level in twenty was failing an entire pair.

What v2 measures instead
------------------------
* **Per-level agreement rate** at each depth. One bad level costs one level, not a pair.
* **The difference distribution** (median, p05, p95, fraction positive). A comparison
  instant that lands mid-event produces SYMMETRIC noise; a systematic misapplication of
  updates produces skew. This is what separates the two hypotheses instead of asserting one.
* **The H1 test**: for every mismatched level, was that price ever touched by an update in
  the window? If mismatches concentrate on NEVER-updated levels, the stream has a depth
  horizon and levels crossing it go stale. Measured on Binance: 0 % of mismatches were
  never-updated, so H1 is refuted there.
* **A negative control** (updates applied in shuffled order), because a rate with nothing to
  compare against is not evidence. The verdict is the SEPARATION, printed side by side.

No threshold is declared here. v1's failure was inventing one; v2 reports rates and their
negative control and lets the separation speak.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import importlib.util
import io
import json
import random
import statistics
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEPTHS = (1, 5, 20, 100)
UA = {"User-Agent": "btc-quant/1.0 (research; keyless)"}


def _mod(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------------- #
# Venue adapters: each yields (snapshot_rows, update_rows) in a common shape.  #
# --------------------------------------------------------------------------- #
def binance(tape_dir: Path):
    gc = _mod(REPO / "deploy" / "gcp" / "gcs_common.py", "gcs_common")
    parts = sorted((p for p in tape_dir.iterdir() if "chunk-" in p.name),
                   key=lambda p: int(p.name.split("chunk-")[1].split("-")[0]))
    out = tape_dir / "_r.gz"
    out.write_bytes(b"".join(p.read_bytes() for p in parts))
    rows = []
    for kind, _e, payload in gc.walk_tape(out, 0):
        if kind == "hole":
            continue
        for line in payload.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    out.unlink(missing_ok=True)

    def norm_snap(r):
        return {"key": int(r["lastUpdateId"]),
                "bids": {l[0]: l[1] for l in r["bids"]},
                "asks": {l[0]: l[1] for l in r["asks"]}}

    def norm_upd(r):
        ev = r["data"]
        return {"key": int(ev["u"]), "lo": int(ev["U"]),
                "bids": ev.get("b", []), "asks": ev.get("a", [])}

    snaps, upds, marks = [], [], []
    for i, r in enumerate(rows):
        k = r.get("kind")
        if k == "snapshot":
            snaps.append((i, norm_snap(r)))
        elif k == "frame" and "u" in r.get("data", {}):
            upds.append((i, norm_upd(r)))
        elif k in ("gap", "start", "stop"):
            marks.append(i)
    return snaps, upds, marks


def okx(date: str, mb: int):
    v = _mod(REPO / "scripts" / "okx_l2_verify.py", "okx_l2_verify")
    rows = list(v.rows_from_prefix(v.fetch_prefix(date, mb)))
    snaps, upds = [], []
    for i, r in enumerate(rows):
        norm = {"key": int(r["ts"]), "lo": int(r["ts"]),
                "bids": r.get("bids", []), "asks": r.get("asks", [])}
        if r.get("action") == "snapshot":
            snaps.append((i, {"key": int(r["ts"]),
                              "bids": {l[0]: l[1] for l in r["bids"]},
                              "asks": {l[0]: l[1] for l in r["asks"]}}))
        else:
            upds.append((i, norm))
    return snaps, upds, []          # OKX carries no lifecycle markers at all


# --------------------------------------------------------------------------- #
def apply(book: dict, upd: dict) -> None:
    for side in ("bids", "asks"):
        for lv in upd[side]:
            if float(lv[1]) == 0.0:
                book[side].pop(lv[0], None)
            else:
                book[side][lv[0]] = lv[1]


def top(d: dict, side: str, k: int):
    return sorted(d[side], key=float, reverse=(side == "bids"))[:k]


def measure(snaps, upds, marks, shuffle_seed=None):
    keys = [u["key"] for _, u in upds]
    marks = sorted(marks)
    per_depth = {k: [0, 0] for k in DEPTHS}          # [match, total]
    diffs, mis_rank, mis_touched, pairs = [], [], [], 0
    for (i0, s0), (i1, s1) in zip(snaps, snaps[1:]):
        if s1["key"] <= s0["key"]:
            continue
        # defect 3: never score across a recording gap — no frame existed to apply
        if any(i0 < m < i1 for m in marks):
            continue
        lo = bisect.bisect_right(keys, s0["key"])
        hi = bisect.bisect_right(keys, s1["key"])
        sel = [u for _, u in upds[lo:hi]]
        if shuffle_seed is not None:
            sel = list(sel)
            random.Random(shuffle_seed + pairs).shuffle(sel)
        book = {"bids": dict(s0["bids"]), "asks": dict(s0["asks"])}
        touched = {"bids": set(), "asks": set()}
        for u in sel:
            for side in ("bids", "asks"):
                for lv in u[side]:
                    touched[side].add(lv[0])
            apply(book, u)
        pairs += 1
        for k in DEPTHS:
            for side in ("bids", "asks"):
                for rank, px in enumerate(top(s1, side, k), 1):
                    want = float(s1[side][px])
                    got = float(book[side].get(px, 0.0))
                    per_depth[k][1] += 1
                    if abs(want - got) < 1e-12:
                        per_depth[k][0] += 1
                    elif k == max(DEPTHS):           # record detail once, at the widest
                        diffs.append(want - got)
                        mis_rank.append(rank)
                        mis_touched.append(px in touched[side])
    return {"pairs": pairs, "per_depth": per_depth, "diffs": diffs,
            "mis_rank": mis_rank, "mis_touched": mis_touched}


def report(name: str, r: dict) -> None:
    print(f"{name}: {r['pairs']} clean pair(s)")
    if not r["pairs"]:
        return
    line = " · ".join(f"top-{k} {r['per_depth'][k][0] / max(r['per_depth'][k][1], 1):.2%}"
                      for k in DEPTHS)
    print(f"  per-LEVEL agreement: {line}")
    d = sorted(r["diffs"])
    if d:
        print(f"  difference (snapshot - rebuild), n={len(d):,}: "
              f"median {statistics.median(d):+.4f} · p05 {d[len(d) // 20]:+.4f} · "
              f"p95 {d[19 * len(d) // 20]:+.4f} · positive {sum(1 for x in d if x > 0) / len(d):.1%}")
        t = r["mis_touched"]
        print(f"  H1 test — mismatched levels NEVER updated in the window: "
              f"{sum(1 for x in t if not x)}/{len(t)} = {sum(1 for x in t if not x) / len(t):.1%}")
        mr = sorted(r["mis_rank"])
        print(f"  mismatch rank: median {statistics.median(mr):.0f} · "
              f"p05 {mr[len(mr) // 20]} · p95 {mr[19 * len(mr) // 20]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--venue", choices=("binance", "okx"), required=True)
    ap.add_argument("--tape-dir")
    ap.add_argument("--date", default="2024-01-07")
    ap.add_argument("--mb", type=int, default=24)
    ap.add_argument("--seed", type=int, default=20260808)
    a = ap.parse_args()
    if a.venue == "binance":
        snaps, upds, marks = binance(Path(a.tape_dir))
    else:
        snaps, upds, marks = okx(a.date, a.mb)
    print(f"=== control A v2 · {a.venue} · {len(snaps)} snapshot(s), "
          f"{len(upds):,} update(s) ===\n")
    report("intact  ", measure(snaps, upds, marks))
    print()
    report("shuffled", measure(snaps, upds, marks, shuffle_seed=a.seed))
    print("\nRead the SEPARATION between the two blocks. A rate with no negative control "
          "is not evidence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# --------------------------------------------------------------------------- #
# Streaming path: a FULL day, in constant memory                              #
# --------------------------------------------------------------------------- #
def okx_stream(tar_path, shuffle_seed=None, max_pairs=None):
    """Control A v2 over a whole OKX day without holding the rows.

    A full day decompresses to ~2.9 GB and ~8.5 M JSON lines; materialising them was never
    an option, and quietly falling back to a 24 MB prefix would have been the dishonest
    version of the same constraint. The book itself is ~800 entries, so streaming one line
    at a time keeps memory flat regardless of how long the day is: rows are consumed, a
    book is carried, and each `snapshot` row closes the pair before the next one opens.

    `shuffle_seed` cannot be honestly supported in a single pass — shuffling requires the
    updates of an interval in hand — so it buffers ONE interval at a time, which is bounded
    by the 60 s snapshot cadence rather than by the day.
    """
    import tarfile
    per_depth = {k: [0, 0] for k in DEPTHS}
    diffs, mis_rank, mis_touched = [], [], []
    pairs = 0
    book = None
    touched = {"bids": set(), "asks": set()}
    buf = []
    with tarfile.open(tar_path, "r|gz") as tf:
        for member in tf:
            if not member.isfile():
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            # Hand-rolled line splitting, not TextIOWrapper: a "r|gz" stream member is not
            # seekable and TextIOWrapper asks it whether it is, which raises. Reading fixed
            # blocks and carrying the partial last line keeps memory flat and works on a
            # non-seekable stream, which is the whole point of streaming a 2.9 GB day.
            def _lines(f):
                tail = b""
                while True:
                    blk = f.read(1 << 22)
                    if not blk:
                        if tail:
                            yield tail
                        return
                    blk = tail + blk
                    *whole, tail = blk.split(b"\n")
                    for ln in whole:
                        yield ln
            for raw_b in _lines(fh):
                raw = raw_b.decode("utf-8", "ignore").strip()
                if not raw.startswith("{"):
                    continue
                try:
                    r = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if r.get("action") == "snapshot":
                    if book is not None:
                        if shuffle_seed is not None:
                            random.Random(shuffle_seed + pairs).shuffle(buf)
                        for u in buf:
                            apply(book, u)
                        pairs += 1
                        want = {"bids": {l[0]: l[1] for l in r["bids"]},
                                "asks": {l[0]: l[1] for l in r["asks"]}}
                        for k in DEPTHS:
                            for side in ("bids", "asks"):
                                for rank, px in enumerate(top(want, side, k), 1):
                                    w = float(want[side][px])
                                    g = float(book[side].get(px, 0.0))
                                    per_depth[k][1] += 1
                                    if abs(w - g) < 1e-12:
                                        per_depth[k][0] += 1
                                    elif k == max(DEPTHS):
                                        diffs.append(w - g)
                                        mis_rank.append(rank)
                                        mis_touched.append(px in touched[side])
                        if max_pairs and pairs >= max_pairs:
                            book = None
                            break
                    book = {"bids": {l[0]: l[1] for l in r["bids"]},
                            "asks": {l[0]: l[1] for l in r["asks"]}}
                    touched = {"bids": set(), "asks": set()}
                    buf = []
                elif book is not None:
                    u = {"bids": r.get("bids", []), "asks": r.get("asks", [])}
                    for side in ("bids", "asks"):
                        for lv in u[side]:
                            touched[side].add(lv[0])
                    if shuffle_seed is None:
                        apply(book, u)
                    else:
                        buf.append(u)
            break                                   # one data member per archive
    return {"pairs": pairs, "per_depth": per_depth, "diffs": diffs,
            "mis_rank": mis_rank, "mis_touched": mis_touched}
