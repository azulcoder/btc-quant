"""okx_l2_verify.py — schema report and the two controls declared in SAMPLE-okx-l2-001.

Nothing predictive is computed. This answers only: what is actually in the file, and can a
book be rebuilt from it correctly. Both controls were fixed in the rule document before any
byte was downloaded.

Control A (internal, deterministic): rebuild the book from one `snapshot` row, apply every
`update` row after it, and compare against the NEXT `snapshot` row at the moment it occurs.
Correct update semantics make this exact, so a 100% bar is legitimate here — it is not a
statistical threshold. Anything short of exact is reported as a finding, never softened.

Control B (semi-independent, statistical): trades from the SEPARATE OKX trades archive must
price inside the reconstructed `[bid, ask]`. This one is statistical, so instead of inventing
a pass threshold — the failure class this repo hit three times in one PREREG — it is read
against a NEGATIVE CONTROL: the identical pipeline with the update order shuffled. The
verdict is the SEPARATION between the two, and both numbers are printed side by side.

To keep peak disk at one file, the L2 day is streamed from a byte-range prefix rather than
downloaded whole: a truncated gzip decompresses cleanly up to the cut, which yields many
snapshot boundaries — enough to exercise both controls many times over.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import random
import statistics
import sys
import urllib.request
import zipfile
from collections import Counter

INST = "BTC-USDT-SWAP"
BASES = (
    "https://static.okx.com/cdn/okx/match/orderbook/pro/L2/400lv/daily",
    "https://static.okx.com/cdn/okx/match/orderbook/L2/400lv/daily",
)
TRADES = "https://www.okx.com/cdn/okex/traderecords/trades/daily"
UA = {"User-Agent": "btc-quant/1.0 (research; keyless)"}


def fetch_prefix(date: str, mb: int) -> bytes:
    name = f"{INST}-L2orderbook-400lv-{date}.tar.gz"
    for base in BASES:
        url = f"{base}/{date.replace('-', '')}/{name}"
        try:
            req = urllib.request.Request(
                url, headers={**UA, "Range": f"bytes=0-{mb * 1024 * 1024 - 1}"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read()
        except Exception:                            # noqa: BLE001
            continue
    raise RuntimeError(f"no L2 file for {date}")


def rows_from_prefix(blob: bytes):
    """Decompress as far as the truncation allows; stop cleanly at the cut."""
    d = gzip.GzipFile(fileobj=io.BytesIO(blob))
    buf = b""
    try:
        while True:
            chunk = d.read(1 << 20)
            if not chunk:
                break
            buf += chunk
    except Exception:                                # noqa: BLE001 — truncated tail expected
        pass
    # tar: 512-byte header blocks; the payload starts after the first header
    start = buf.find(b"\n{")                          # first JSON line inside the member
    if start < 0:
        start = buf.find(b"{")
    text = buf[start:].decode("utf-8", "ignore")
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def apply_update(book: dict, side: str, levels) -> None:
    for lv in levels:
        px, sz = lv[0], lv[1]
        if float(sz) == 0.0:
            book[side].pop(px, None)
        else:
            book[side][px] = lv


def best(book: dict) -> tuple[float | None, float | None]:
    b = max((float(p) for p in book["bids"]), default=None)
    a = min((float(p) for p in book["asks"]), default=None)
    return b, a


def run(date: str, mb: int, seed: int) -> int:
    print(f"=== OKX L2 {date} — prefix {mb} MB ===")
    rows = list(rows_from_prefix(fetch_prefix(date, mb)))
    if not rows:
        print("no rows parsed")
        return 2
    kinds = Counter(r.get("action") for r in rows)
    print(f"rows parsed: {len(rows):,}  actions: {dict(kinds)}")

    # ---- 2d: schema, verbatim ------------------------------------------------
    snap = next(r for r in rows if r.get("action") == "snapshot")
    upd = next(r for r in rows if r.get("action") == "update")
    print("\n-- schema --")
    print(f"  top-level keys : {sorted(snap.keys())}")
    print(f"  instId         : {snap.get('instId')}")
    print(f"  ts             : {snap.get('ts')} (ms epoch, {len(str(snap.get('ts')))} digits)")
    print(f"  level entry    : {snap['asks'][0]}  -> [price, size, order_count]"
          if len(snap["asks"][0]) == 3 else f"  level entry    : {snap['asks'][0]}")
    print(f"  levels/side    : snapshot bids={len(snap['bids'])} asks={len(snap['asks'])}"
          f" · update bids={len(upd['bids'])} asks={len(upd['asks'])}")
    pxs = sorted({float(l[0]) for l in snap["asks"][:60]})
    ticks = sorted({round(b - a, 6) for a, b in zip(pxs, pxs[1:]) if b > a})
    print(f"  tick size      : min gap {ticks[0] if ticks else '?'} "
          f"(distinct gaps in top 60 asks: {ticks[:5]})")
    ts = [int(r["ts"]) for r in rows if r.get("action") == "update"]
    gaps = sorted(b - a for a, b in zip(ts, ts[1:]) if b >= a)
    if gaps:
        print(f"  update cadence : median {statistics.median(gaps)} ms · "
              f"p90 {gaps[int(.9 * len(gaps))]} ms · max {gaps[-1]} ms "
              f"(n={len(gaps):,})")

    # ---- control A: reconstruction vs next snapshot ---------------------------
    print("\n-- control A: rebuild vs next snapshot (deterministic, bar = exact) --")
    idx = [i for i, r in enumerate(rows) if r.get("action") == "snapshot"]
    pairs = ok = 0
    first_fail = None
    for i0, i1 in zip(idx, idx[1:]):
        book = {"bids": {l[0]: l for l in rows[i0]["bids"]},
                "asks": {l[0]: l for l in rows[i0]["asks"]}}
        for r in rows[i0 + 1:i1]:
            apply_update(book, "bids", r.get("bids", []))
            apply_update(book, "asks", r.get("asks", []))
        want = {"bids": {l[0]: l for l in rows[i1]["bids"]},
                "asks": {l[0]: l for l in rows[i1]["asks"]}}
        pairs += 1
        # compare the top 20 levels per side, which is what any book consumer reads
        def top(d, side):
            ps = sorted(d[side], key=float, reverse=(side == "bids"))[:20]
            return [(p, d[side][p][1]) for p in ps]
        same = top(book, "bids") == top(want, "bids") and top(book, "asks") == top(want, "asks")
        ok += same
        if not same and first_fail is None:
            first_fail = (rows[i0]["ts"], rows[i1]["ts"])
    if pairs:
        print(f"  snapshot pairs tested : {pairs}")
        print(f"  exact top-20 matches  : {ok}/{pairs} = {ok / pairs:.1%}"
              f"  -> {'PASS' if ok == pairs else 'FAIL (reported, not softened)'}")
        if first_fail:
            print(f"  first mismatch between ts {first_fail[0]} and {first_fail[1]}")
    else:
        print("  only one snapshot in this prefix — control A needs two; "
              "increase --mb (reported, not skipped silently)")

    # ---- control B: trades inside the reconstructed spread --------------------
    print("\n-- control B: trades inside spread, vs shuffled-update negative control --")
    try:
        tu = f"{TRADES}/{date.replace('-', '')}/{INST}-trades-{date}.zip"
        with urllib.request.urlopen(urllib.request.Request(tu, headers=UA), timeout=300) as r:
            zb = r.read()
        zf = zipfile.ZipFile(io.BytesIO(zb))
        lines = zf.read(zf.namelist()[0]).decode("utf-8", "ignore").splitlines()
        hdr = lines[0].split(",")
        ipx, its = hdr.index("price"), hdr.index("created_time")
        trades = []
        for ln in lines[1:]:
            f = ln.split(",")
            if len(f) > max(ipx, its):
                try:
                    trades.append((int(f[its]), float(f[ipx])))
                except ValueError:
                    pass
        trades.sort()
        print(f"  trades archive: {len(trades):,} rows [independent file, independent pipeline]")
    except Exception as e:                           # noqa: BLE001
        print(f"  trades archive unavailable ({type(e).__name__}) — control B cannot run")
        return 0

    def violations(shuffled: bool) -> list[float]:
        i0 = idx[0]
        upds = [r for r in rows[i0 + 1:] if r.get("action") == "update"]
        if shuffled:
            random.Random(seed).shuffle(upds)
        book = {"bids": {l[0]: l for l in rows[i0]["bids"]},
                "asks": {l[0]: l for l in rows[i0]["asks"]}}
        lo, hi = int(rows[i0]["ts"]), int(upds[-1]["ts"]) if upds else int(rows[i0]["ts"])
        tr = [t for t in trades if lo <= t[0] <= hi]
        out, j = [], 0
        for r in upds:
            t_r = int(r["ts"])
            while j < len(tr) and tr[j][0] <= t_r:
                b, a = best(book)
                if b is not None and a is not None:
                    px = tr[j][1]
                    out.append(0.0 if b <= px <= a else (px - a if px > a else b - px))
                j += 1
            apply_update(book, "bids", r.get("bids", []))
            apply_update(book, "asks", r.get("asks", []))
        return out

    for label, sh in (("intact ", False), ("shuffled", True)):
        v = violations(sh)
        if not v:
            print(f"  {label}: no trades in the reconstructed window")
            continue
        inside = sum(1 for x in v if x == 0.0) / len(v)
        nz = sorted(x for x in v if x > 0)
        print(f"  {label}: n={len(v):,} · inside spread {inside:.2%} · "
              f"median viol {statistics.median(nz) if nz else 0:.2f} · "
              f"p99 {nz[int(.99 * len(nz))] if nz else 0:.2f} · max {nz[-1] if nz else 0:.2f}")
    print("  verdict is read from the SEPARATION between the two rows above; if they are "
          "not clearly apart this control has no discriminating power and must be said so.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default="2024-01-07")
    ap.add_argument("--mb", type=int, default=48)
    ap.add_argument("--seed", type=int, default=20260808)
    a = ap.parse_args()
    return run(a.date, a.mb, a.seed)


if __name__ == "__main__":
    sys.exit(main())
