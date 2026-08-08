"""okx_regime_prefix.py — run control A v2 across the declared regime days.

Two coverage modes, and which day got which is RECORDED per day rather than averaged over:

* **full**   — the whole day, streamed from a file already mirrored on Hugging Face. Used
  where the day is already acquired, because HF serves it ~20x faster than the OKX CDN
  currently does.
* **prefix** — the first N MB of the day, fetched by HTTP Range from the OKX CDN. A
  truncated gzip decompresses cleanly up to the cut, so this is a real slice of the day,
  not a resampling of it.

Why the split exists at all, measured rather than assumed [DIUKUR 2026-08-08]: after ~2 GB
of acquisition the OKX CDN throttled from 1.4 MB/s to **0.07 MB/s**, a factor of 20. Nine
full days at that rate is ~15 hours. The instruction permitted prefixes as long as nothing
silently falls back to one, so the mode is a column in the output and appears in every
table this produces.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import statistics
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEL = REPO / "reports" / "okx-regime-days.json"
OUT = REPO / "reports" / "okx-regime-control.jsonl"
HF_REPO = "azulcoder/btc-quant-ticks"
HF_PREFIX = "okx_l2/BTC-USDT-SWAP"
INST = "BTC-USDT-SWAP"
UA = {"User-Agent": "btc-quant/1.0 (research; keyless)"}
DEPTHS = (1, 5, 20, 100)


def _mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def hf_full(date: str, dest: Path) -> bool:
    """Pull an already-mirrored day from HF. Short reads are detected, not trusted."""
    path = f"{HF_PREFIX}/date={date}/{INST}-L2orderbook-400lv-{date}.tar.gz"
    url = f"https://huggingface.co/datasets/{HF_REPO}/resolve/main/{urllib.parse.quote(path)}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r:
                declared = int(r.headers.get("content-length") or 0)
                n = 0
                with open(dest, "wb") as fh:
                    while True:
                        blk = r.read(1 << 20)
                        if not blk:
                            break
                        fh.write(blk)
                        n += len(blk)
            if declared and n != declared:
                raise IOError(f"short read {n}/{declared}")
            return True
        except Exception:                            # noqa: BLE001
            time.sleep(2 * (attempt + 1))
    return False


def okx_prefix_rows(date: str, mb: int):
    v = _mod(REPO / "scripts" / "okx_l2_verify.py", "okx_l2_verify")
    return list(v.rows_from_prefix(v.fetch_prefix(date, mb)))


def measure_rows(rows, ctl, shuffle_seed=None):
    snaps, upds = [], []
    for i, r in enumerate(rows):
        if r.get("action") == "snapshot":
            snaps.append((i, {"key": int(r["ts"]),
                              "bids": {l[0]: l[1] for l in r["bids"]},
                              "asks": {l[0]: l[1] for l in r["asks"]}}))
        else:
            upds.append((i, {"key": int(r["ts"]), "lo": int(r["ts"]),
                             "bids": r.get("bids", []), "asks": r.get("asks", [])}))
    return ctl.measure(snaps, upds, [], shuffle_seed=shuffle_seed)


def emit(row: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "a") as fh:
        fh.write(json.dumps(row) + "\n")


def summarise(r: dict, mode: str, date: str, rr, secs) -> dict:
    row = {"date": date, "mode": mode, "realized_range": rr, "pairs": r["pairs"],
           "agreement": {str(k): (r["per_depth"][k][0] / r["per_depth"][k][1]
                                  if r["per_depth"][k][1] else None) for k in DEPTHS},
           "levels": {str(k): r["per_depth"][k][1] for k in DEPTHS},
           "seconds": round(secs, 1)}
    d = sorted(r["diffs"])
    if d:
        row["diff_median"] = statistics.median(d)
        row["diff_frac_positive"] = sum(1 for x in d if x > 0) / len(d)
        t = r["mis_touched"]
        row["h1_never_updated_frac"] = sum(1 for x in t if not x) / len(t)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--full", nargs="*", default=[], help="days to run at FULL coverage")
    ap.add_argument("--prefix-mb", type=int, default=24)
    ap.add_argument("--shuffle-day", default=None)
    a = ap.parse_args()
    ctl = _mod(REPO / "scripts" / "control_a_v2.py", "control_a_v2")
    sel = json.loads(SEL.read_text())
    rr = sel["realized_range"]
    seen = {json.loads(l)["date"] + json.loads(l).get("mode", "")
            for l in OUT.read_text().splitlines()} if OUT.exists() else set()
    stage = REPO / "data" / "okx-stage"
    stage.mkdir(parents=True, exist_ok=True)

    for date in a.full:
        if date + "full" in seen:
            continue
        p = stage / f"{INST}-{date}.tar.gz"
        t0 = time.time()
        if not hf_full(date, p):
            print(f"  {date}  FULL: not on HF / fetch failed")
            continue
        r = ctl.okx_stream(p)
        row = summarise(r, "full", date, rr.get(date), time.time() - t0)
        if a.shuffle_day == date:
            sh = ctl.okx_stream(p, shuffle_seed=20260808)
            row["shuffled"] = {str(k): (sh["per_depth"][k][0] / sh["per_depth"][k][1]
                                        if sh["per_depth"][k][1] else None) for k in DEPTHS}
        p.unlink(missing_ok=True)
        emit(row)
        print(f"  {date}  FULL   rr={row['realized_range']:.4f} pairs={r['pairs']:>4} "
              + " · ".join(f"top-{k} {row['agreement'][str(k)]:.2%}" for k in DEPTHS))

    for date in sel["selected"]:
        if date + "prefix" in seen or date in a.full:
            continue
        t0 = time.time()
        try:
            rows = okx_prefix_rows(date, a.prefix_mb)
        except Exception as e:                       # noqa: BLE001
            emit({"date": date, "mode": "prefix", "state": f"fetch_failed:{type(e).__name__}"})
            print(f"  {date}  PREFIX fetch failed: {type(e).__name__}")
            continue
        r = measure_rows(rows, ctl)
        row = summarise(r, "prefix", date, rr.get(date), time.time() - t0)
        row["prefix_mb"] = a.prefix_mb
        if a.shuffle_day == date:
            sh = measure_rows(rows, ctl, shuffle_seed=20260808)
            row["shuffled"] = {str(k): (sh["per_depth"][k][0] / sh["per_depth"][k][1]
                                        if sh["per_depth"][k][1] else None) for k in DEPTHS}
        emit(row)
        print(f"  {date}  PREFIX rr={row['realized_range']:.4f} pairs={r['pairs']:>4} "
              + " · ".join(f"top-{k} {row['agreement'][str(k)]:.2%}" for k in DEPTHS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
