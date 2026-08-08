"""okx_regime_run.py — execute SAMPLE-okx-l2-002: acquire the declared regime days and run
control A v2 on each FULL day, one network job at a time.

Download, control, upload, delete — in that order, per day. The order matters twice: the
control runs on the staged file so no day is fetched twice, and the local copy is released
before the next download starts, so peak disk stays one file (~0.5 GB) on a machine with
16 GiB free that has already hit ENOSPC twice.

The days are not chosen here. They come from `reports/okx-regime-days.json`, produced by the
rule committed in `docs/SAMPLE-okx-l2-002-regime-days.md` before any of them was inspected,
ranked by daily klines from a venue that is not the one being tested.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEL = REPO / "reports" / "okx-regime-days.json"
OUT = REPO / "reports" / "okx-regime-control.jsonl"
STAGE = REPO / "data" / "okx-stage"
DEPTHS = (1, 5, 20, 100)


def _mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def done() -> set[str]:
    if not OUT.exists():
        return set()
    return {json.loads(l)["date"] for l in OUT.read_text().splitlines() if l.strip()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--shuffle-day", default=None,
                    help="run the negative control on this one day as the anchor")
    a = ap.parse_args()

    acq = _mod(REPO / "scripts" / "okx_l2_acquire.py", "okx_l2_acquire")
    ctl = _mod(REPO / "scripts" / "control_a_v2.py", "control_a_v2")
    uh = _mod(REPO / "scripts" / "upload_hf.py", "upload_hf")

    sel = json.loads(SEL.read_text())
    days = [d for d in sel["selected"] if d not in done()]
    if a.limit:
        days = days[: a.limit]
    STAGE.mkdir(parents=True, exist_ok=True)
    print(f"regime run: {len(days)} day(s) to process")

    for date in days:
        got = acq.resolve(date)
        if got is None:
            OUT.open("a").write(json.dumps({"date": date, "state": "absent"}) + "\n")
            print(f"  {date}  ABSENT (counted, not substituted)")
            continue
        url, size = got
        local = STAGE / f"{acq.INST}-L2orderbook-400lv-{date}.tar.gz"
        t0 = time.time()
        sha = acq.download(url, local, size)
        t_dl = time.time() - t0

        t0 = time.time()
        r = ctl.okx_stream(local)                    # FULL day, constant memory
        t_ctl = time.time() - t0
        row = {"date": date, "state": "measured", "bytes": size, "sha256": sha,
               "realized_range": sel["realized_range"].get(date),
               "pairs": r["pairs"],
               "agreement": {str(k): (r["per_depth"][k][0] / r["per_depth"][k][1]
                                      if r["per_depth"][k][1] else None)
                             for k in DEPTHS},
               "levels": {str(k): r["per_depth"][k][1] for k in DEPTHS},
               "full_day": True, "seconds_download": round(t_dl, 1),
               "seconds_control": round(t_ctl, 1)}
        d = sorted(r["diffs"])
        if d:
            row["diff_median"] = statistics.median(d)
            row["diff_frac_positive"] = sum(1 for x in d if x > 0) / len(d)
            t = r["mis_touched"]
            row["h1_never_updated_frac"] = sum(1 for x in t if not x) / len(t)
        if a.shuffle_day == date:
            row["shuffled"] = {
                str(k): (lambda z: z[0] / z[1] if z[1] else None)(
                    ctl.okx_stream(local, shuffle_seed=20260808)["per_depth"][k])
                for k in DEPTHS}
        OUT.open("a").write(json.dumps(row) + "\n")

        dest = f"{acq.HF_PREFIX}/date={date}/{local.name}"
        uh.hf_upload_file(acq.HF_REPO, local, dest, f"okx l2 regime day: {date}")
        receipt = acq.hf_receipt_sha(dest)
        acq.log({"date": date, "state": "stored" if receipt == sha else "receipt_mismatch",
                 "url": url, "bytes": size, "sha256": sha, "hf_path": dest,
                 "hf_receipt_sha256": receipt, "receipt_ok": receipt == sha,
                 "opens": True, "first_member": "streamed by control",
                 "seconds": round(t_dl, 1)})
        local.unlink(missing_ok=True)
        ag = " · ".join(f"top-{k} {row['agreement'][str(k)]:.2%}" for k in DEPTHS)
        print(f"  {date}  rr={row['realized_range']:.4f} · {r['pairs']} pairs · {ag} "
              f"· dl {t_dl:.0f}s ctl {t_ctl:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
