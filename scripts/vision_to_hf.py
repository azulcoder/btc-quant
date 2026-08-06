"""vision_to_hf.py — move Vision day-partitions to HF, then delete them locally.

Remote-first migration for the public-archive partitions (`DESIGN-vision-remote-first.md`).
Nothing here is new discipline: `upload_hf.py` already established it for the tick store —
*"No offsite verification, no local delete"* — and this reuses its primitives unchanged.
`hf_upload_file`/`hf_upload_folder`/`hf_download_file` already take an arbitrary
`path_in_repo`, so **no generalisation of `upload_hf.py` was required**; only the prefix
differs.

Two modes:

  --date YYYY-MM-DD      full path for a day NOT held locally (the 295-day hole):
                         fetch -> verify zip sha256 vs venue -> normalize -> sha256
                         -> upload -> read back -> delete local stage
  --local-batch N        upload-only path for partitions ALREADY local (the 2,08x):
                         never fetches, never re-normalizes. §22 measured parquet
                         normalisation as NONDETERMINISTIC, so a re-fetch would produce
                         bytes disagreeing with the recorded manifests — which would look
                         like corruption and is not. Local bytes are the source of truth.

The upload-only verification chain (㉓ — sha256 alone is NOT enough here):

    pre-upload   sha256(local parquet) == manifest.normalized.sha256
                   -> the local file is byte-identical to what the venue-verified ingest
                      produced (bit-rot gate; the one claim the old sha256 still supports)
    post-upload  sha256(read-back) == sha256(local)          [transport intact]
                 AND rows/id_min/id_max/id_distinct(read-back) == manifest.normalized
                   [content matches what the venue-verified zip contained — these stats
                    are deterministic and source-bound; the sha256 is not]
                 AND read-back manifest byte-identical to the local manifest

    DELETE LOCAL only when all three hold, and only in the SAME RUN as the read-back —
    checkpoint state never licenses a delete on its own.

Checkpoint (`reports/vision-migration.jsonl`, append-only): every partition ends in exactly
one recorded state per run; a crash at any point loses at most the in-flight partition.
Prior `readback_ok`/`deleted` states short-circuit re-UPLOAD, never the delete-licensing
read-back.

Rate limiting: uploads/downloads retry on 429 with recorded backoff; >=5 consecutive
upload failures abort the batch (systemic, not transient). Peak disk is SAMPLED during the
run by a background thread, not estimated afterwards; per-partition read-back downloads are
deleted immediately so the peak does not grow with batch size.

The batch start/end lines record whether the run was under caffeinate (env CAFFEINATED=1):
artificially-awake hours must be excluded from any §14b clean-stretch claim, and the
checkpoint file is the durable record of those windows.

Research only. Deletes local files only after a verified same-run remote read-back.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HF_REPO = "azulcoder/btc-quant-ticks"
VISION_PREFIX = "vision/binancef/BTCUSDT/aggTrades"
LOCAL_ROOT = REPO / "data" / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
STATE_FILE = REPO / "reports" / "vision-migration.jsonl"
STAGE_ROOT = Path("/private/tmp/claude-501/-Users-azul/vision-stage")

# §22's fetch-path control partition, already on HF with verified content. The upload-only
# content checker is a NEW instrument; its first number every run is reproducing these
# known values from the hub, or the batch refuses to start.
CONTENT_CONTROL = {"date": "2026-07-30", "rows": 834_335,
                   "id_min": 3_397_437_001, "id_max": 3_398_271_335}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parquet_stats(p: Path) -> dict:
    """rows / id_min / id_max / id_distinct — deterministic content stats (㉓)."""
    import duckdb
    con = duckdb.connect()
    try:
        con.execute("SET enable_progress_bar=false")
        r = con.execute(
            f"""SELECT count(*), min(CAST(trade_id AS BIGINT)), max(CAST(trade_id AS BIGINT)),
                       count(DISTINCT trade_id)
                FROM read_parquet('{str(p).replace(chr(39), chr(39) * 2)}')""").fetchone()
        return {"rows": r[0], "id_min": r[1], "id_max": r[2], "id_distinct": r[3]}
    finally:
        con.close()


class DiskWatch:
    """Samples free bytes on a thread so peak usage is MEASURED, not inferred."""

    def __init__(self, path: Path, interval: float = 0.25):
        self.path, self.interval = path, interval
        self.start_free = shutil.disk_usage(path).free
        self.min_free = self.start_free
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            self.min_free = min(self.min_free, shutil.disk_usage(self.path).free)
            self._stop.wait(self.interval)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join(timeout=2)

    @property
    def peak_used(self) -> int:
        return self.start_free - self.min_free


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def append_state(obj: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_states() -> dict[str, str]:
    """date -> last recorded state. Append-only file; the last line per date wins."""
    out: dict[str, str] = {}
    if not STATE_FILE.exists():
        return out
    for line in STATE_FILE.read_text().splitlines():
        try:
            d = json.loads(line)
        except Exception:  # noqa: BLE001 — a torn final line after a crash is expected
            continue
        if "date" in d and "state" in d:
            out[d["date"]] = d["state"]
    return out


def retry_hf(fn, what: str, throttle_log: list, tries: int = 4):
    """Retry on rate limits (long backoff) AND transient network timeouts (short).

    The first dry-run batch measured a 4 % loss rate purely to home-network blips
    (`Errno 60` / read timeouts) that the 429-only retry let through; they all
    cleared in 2-3 s on retry, so a short backoff absorbs them cheaply. Anything
    that matches neither signature still raises immediately.
    """
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — inspect, re-raise if unrecognised
            s = str(e)
            throttled = "429" in s or "rate" in s.lower() or "quota" in s.lower()
            timeout = ("timed out" in s.lower() or "timeout" in s.lower()
                       or "errno 60" in s.lower() or "connection reset" in s.lower())
            if not (throttled or timeout):
                raise
            evt = {"ts": now_iso(), "what": what, "try": i + 1,
                   "kind": "throttle" if throttled else "net_timeout", "err": s[:160]}
            if throttled:
                throttle_log.append(evt)
            append_state({"event": "retry", **evt})
            time.sleep((60 * (i + 1)) if throttled else (5 * (i + 1)))
    raise RuntimeError(f"retries exhausted after {tries} tries on {what}")


def _stage_link(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)          # zero extra bytes when same filesystem
    except OSError:
        shutil.copy2(src, dest)


def list_local_partitions(order: str) -> list[str]:
    dates = [p.name.split("=")[1] for p in LOCAL_ROOT.glob("date=*")
             if (p / "trades.parquet").exists()]
    return sorted(dates, reverse=(order == "newest"))


def run_content_control(uh, tmp: Path) -> bool:
    """The content checker's first number is a CONTROL: reproduce §22's partition."""
    d = CONTENT_CONTROL["date"]
    print(f"  CONTROL — content checker vs the known partition {d} on HF:")
    dest = tmp / "control"
    dest.mkdir(parents=True, exist_ok=True)
    got = uh.hf_download_file(HF_REPO, f"{VISION_PREFIX}/date={d}/trades.parquet", dest)
    st = parquet_stats(Path(got))
    ok = (st["rows"] == CONTENT_CONTROL["rows"] and st["id_min"] == CONTENT_CONTROL["id_min"]
          and st["id_max"] == CONTENT_CONTROL["id_max"] and st["id_distinct"] == CONTENT_CONTROL["rows"])
    print(f"    rows {st['rows']:,} (want {CONTENT_CONTROL['rows']:,}) · "
          f"id {st['id_min']}..{st['id_max']} · distinct {st['id_distinct']:,} "
          f"->  {'MATCH' if ok else 'MISMATCH'}")
    shutil.rmtree(dest, ignore_errors=True)
    return ok


def migrate_one_local(date: str, uh, dry_run: bool, skip_upload: bool,
                      throttle_log: list, chunk_up_s: float = 0.0,
                      chunk_size: int = 1) -> dict:
    """Verify-and-delete for one already-local partition (upload happens per CHUNK
    in run_batch). Returns the state record."""
    rec: dict = {"ts": now_iso(), "date": date, "mode": "upload-only", "dry_run": dry_run,
                 "chunk_up_s": round(chunk_up_s, 2), "chunk_size": chunk_size}
    pq = LOCAL_ROOT / f"date={date}" / "trades.parquet"
    mf = LOCAL_ROOT / "manifests" / f"MANIFEST-{date}.json"
    if not mf.exists():
        rec["state"] = "manifest_missing"
        return rec
    man = json.loads(mf.read_text())
    norm = man["normalized"]

    # -- bit-rot gate: the one claim the old sha256 still supports (㉓) --
    t = time.time()
    sha_local = sha256_file(pq)
    rec["sha_local"] = sha_local
    rec["hash_s"] = round(time.time() - t, 2)
    if sha_local != norm["sha256"]:
        rec["state"] = "local_manifest_mismatch"     # bytes changed since ingest: RETAIN, flag
        rec["manifest_sha"] = norm["sha256"]
        return rec

    part_tmp = STAGE_ROOT / f"p-{date}"
    shutil.rmtree(part_tmp, ignore_errors=True)
    try:
        # -- upload: handled by the CHUNK stage in run_batch (N partitions per commit,
        # HF throttles at ~125-130 commits/window — measured). This function only
        # verifies and deletes; rec carries the chunk's shared timing for the series.
        if not skip_upload:
            rec["up_s"] = rec.get("chunk_up_s", 0.0)
        else:
            rec["up_s"] = 0.0
            rec["upload_skipped"] = True

        # -- read-back, ALWAYS in the same run as any delete --
        t = time.time()
        back = part_tmp / "readback"
        back.mkdir(parents=True, exist_ok=True)
        got_pq = retry_hf(lambda: uh.hf_download_file(
            HF_REPO, f"{VISION_PREFIX}/date={date}/trades.parquet", back),
            f"readback parquet {date}", throttle_log)
        got_mf = retry_hf(lambda: uh.hf_download_file(
            HF_REPO, f"{VISION_PREFIX}/manifests/MANIFEST-{date}.json", back),
            f"readback manifest {date}", throttle_log)
        sha_remote = sha256_file(Path(got_pq))
        transport_ok = sha_remote == sha_local
        manifest_ok = sha256_file(Path(got_mf)) == sha256_file(mf)
        st = parquet_stats(Path(got_pq))
        content_ok = (st["rows"] == norm["rows"] and st["id_min"] == norm["id_min"]
                      and st["id_max"] == norm["id_max"]
                      and st["id_distinct"] == norm["id_distinct"])
        rec["rb_s"] = round(time.time() - t, 2)
        rec["transport_ok"], rec["content_ok"], rec["manifest_ok"] = \
            transport_ok, content_ok, manifest_ok
        rec["mb"] = round(pq.stat().st_size / 1e6, 2)

        if not (transport_ok and content_ok and manifest_ok):
            rec["state"] = "remote_verify_failed"    # RETAIN local
            rec["sha_remote"] = sha_remote
            rec["remote_stats"] = st
            return rec

        # -- delete local, gated on the SAME-RUN read-back above --
        if dry_run:
            rec["state"] = "readback_ok"             # would_delete; local retained
        else:
            n = pq.stat().st_size
            pq.unlink()
            try:
                pq.parent.rmdir()
            except OSError:
                pass
            rec["state"] = "deleted"
            rec["deleted_bytes"] = n
        return rec
    finally:
        shutil.rmtree(part_tmp, ignore_errors=True)


def run_batch(a, uh) -> int:
    states = load_states()
    all_parts = list_local_partitions(a.order)
    # A truly deleted partition has no trades.parquet and never enters all_parts, so
    # "deleted" must NOT be a skip state: a date that carries state "deleted" yet still
    # appears here is exactly the stranded-by-crash case and needs re-verification.
    skip_terminal = {"readback_ok"} if a.dry_run else set()
    todo = [d for d in all_parts if states.get(d) not in skip_terminal][:a.local_batch]
    prior_ok = {d for d in todo if states.get(d) == "readback_ok"}

    print(f"vision->hf UPLOAD-ONLY batch  n={len(todo)}  order={a.order}  "
          f"dry_run={a.dry_run}  caffeinated={os.environ.get('CAFFEINATED') == '1'}")
    if todo:
        print(f"  first {todo[0]}  last {todo[-1]}  (prior readback_ok, upload skipped: {len(prior_ok)})")
    if a.plan:
        print("  PLAN ONLY — nothing uploaded, nothing deleted.")
        return 0
    if not todo:
        print("  nothing to do.")
        return 0

    STAGE_ROOT.mkdir(parents=True, exist_ok=True)
    throttle_log: list = []
    append_state({"event": "batch_start", "ts": now_iso(), "n": len(todo),
                  "order": a.order, "dry_run": a.dry_run,
                  "caffeinated": os.environ.get("CAFFEINATED") == "1",
                  "first": todo[0], "last": todo[-1]})

    with DiskWatch(REPO) as watch:
        if not run_content_control(uh, STAGE_ROOT):
            print("  CONTROL FAILED -> refusing to run the batch. The checker is wrong, "
                  "not the data.")
            append_state({"event": "batch_abort", "ts": now_iso(),
                          "reason": "content_control_failed"})
            return 2
        print()

        t0 = time.time()
        recs, consec_fail = [], 0
        COMMIT_BATCH = 25          # HF throttles at ~125-130 commits/window (measured);
                                   # 25/commit puts the full 2,084 at ~84 commits total
        i = 0
        aborted = False
        for c0 in range(0, len(todo), COMMIT_BATCH):
            chunk = todo[c0:c0 + COMMIT_BATCH]
            need_upload = [d for d in chunk if d not in prior_ok]
            chunk_up_s = 0.0
            if need_upload:
                stage = STAGE_ROOT / f"chunk-{c0}"
                shutil.rmtree(stage, ignore_errors=True)
                staged = []
                for d in need_upload:
                    pq = LOCAL_ROOT / f"date={d}" / "trades.parquet"
                    mf = LOCAL_ROOT / "manifests" / f"MANIFEST-{d}.json"
                    if pq.exists() and mf.exists():
                        _stage_link(pq, stage / f"date={d}" / "trades.parquet")
                        _stage_link(mf, stage / "manifests" / f"MANIFEST-{d}.json")
                        staged.append(d)
                if staged:
                    t = time.time()
                    try:
                        retry_hf(lambda: uh.hf_upload_folder(
                            HF_REPO, stage, VISION_PREFIX,
                            f"vision aggTrades {staged[0]}..{staged[-1]} "
                            f"({len(staged)} partitions, upload-only)"),
                            f"upload chunk {staged[0]}..{staged[-1]}", throttle_log)
                        chunk_up_s = time.time() - t
                    except Exception as e:  # noqa: BLE001 — chunk fails, partitions retained
                        for d in chunk:
                            rec = {"ts": now_iso(), "date": d, "state": "upload_failed",
                                   "err": str(e)[:200]}
                            append_state(rec)
                            recs.append(rec)
                        consec_fail += len(chunk)
                        shutil.rmtree(stage, ignore_errors=True)
                        if consec_fail >= 2 * COMMIT_BATCH:
                            print("  two consecutive chunk failures — systemic, aborting.")
                            append_state({"event": "batch_abort", "ts": now_iso(),
                                          "reason": "2_consecutive_chunk_failures"})
                            aborted = True
                            break
                        continue
                shutil.rmtree(stage, ignore_errors=True)
            per_part_up = chunk_up_s / max(len(need_upload), 1)
            for d in chunk:
                i += 1
                try:
                    rec = migrate_one_local(d, uh, a.dry_run, skip_upload=(d in prior_ok),
                                            throttle_log=throttle_log,
                                            chunk_up_s=per_part_up,
                                            chunk_size=len(need_upload))
                except Exception as e:  # noqa: BLE001 — one partition must not kill the batch
                    rec = {"ts": now_iso(), "date": d, "state": "upload_failed",
                           "err": str(e)[:200]}
                append_state(rec)
                recs.append(rec)
                ok = rec["state"] in ("readback_ok", "deleted")
                consec_fail = 0 if ok else consec_fail + 1
                rate = rec.get("mb", 0) / rec["up_s"] if rec.get("up_s") else 0
                print(f"  [{i:>4}/{len(todo)}] {d}  {rec['state']:<22} "
                      f"up {rec.get('up_s', 0):>5.1f}s  rb {rec.get('rb_s', 0):>5.1f}s  "
                      f"{rate:>5.2f} MB/s")
                if consec_fail >= 5:
                    print("  >=5 consecutive failures — systemic, aborting batch.")
                    append_state({"event": "batch_abort", "ts": now_iso(),
                                  "reason": "5_consecutive_failures"})
                    aborted = True
                    break
            if aborted:
                break
        total = time.time() - t0

    append_state({"event": "batch_end", "ts": now_iso(), "total_s": round(total, 1),
                  "peak_disk_mb": round(watch.peak_used / 1e6, 1),
                  "throttle_events": len(throttle_log)})

    # -- summary: conclusions printed BESIDE the numbers that produced them --
    done = [r for r in recs if r["state"] in ("readback_ok", "deleted")]
    ups = [r["up_s"] for r in done if r.get("up_s")]
    n10 = min(10, max(1, len(ups) // 2))
    print(f"\n  === BATCH CONTROL RESULT (n={len(recs)}, ok={len(done)}) ===")
    for st in sorted({r["state"] for r in recs}):
        print(f"    state {st:<24} {sum(1 for r in recs if r['state'] == st)}")
    if ups:
        import statistics as stats
        rates = [r["mb"] / r["up_s"] for r in done if r.get("up_s") and r.get("mb")]
        print(f"    upload s/partition: median {stats.median(ups):.1f} · "
              f"first{n10} mean {stats.mean(ups[:n10]):.1f} · "
              f"last{n10} mean {stats.mean(ups[-n10:]):.1f}   (raw; includes any backoff)")
        if rates:
            print(f"    upload MB/s (size-normalised): median {stats.median(rates):.2f} · "
                  f"first{n10} mean {stats.mean(rates[:n10]):.2f} · "
                  f"last{n10} mean {stats.mean(rates[-n10:]):.2f}  "
                  f"<- throttle = falling MB/s tail; raw seconds confound size trend")
    print(f"    throttle events (429/backoff): {len(throttle_log)}")
    print(f"    TOTAL {total:.1f}s = {total / max(len(recs), 1):.1f}s/partition")
    print(f"    peak disk USED {watch.peak_used / 1e6:.1f} MB · "
          f"free now {shutil.disk_usage(REPO).free / 1e9:.2f} GB")
    # list_local_partitions is a FRESH post-delete scan, so nothing is subtracted:
    # on a real run the deleted files are already absent from it (double-subtraction bug,
    # caught in review); on a dry run nothing was deleted so the full count stands.
    remaining = len(list_local_partitions("newest"))
    print(f"    extrapolation: {remaining:,} partitions x "
          f"{total / max(len(recs), 1):.1f}s = {remaining * total / max(len(recs), 1) / 3600:.1f} h")
    return 0


def run_single(a, iv, uh) -> int:
    """Full path for one day NOT held locally (§22's proven flow, unchanged).

    REFUSES dates held locally (partition OR manifest present). Review finding: this
    path's whole verify chain runs on the freshly-staged bytes, and normalisation is
    nondeterministic (§22) — so the live local file's bytes are provably different and
    were never in the chain. Deleting them here would destroy the only copy matching
    the local manifest, and re-running --date on an already-migrated day would
    overwrite the verified hub copy with different bytes. Those days belong to
    --local-batch, which verifies the LOCAL bytes.
    """
    live_pq = LOCAL_ROOT / f"date={a.date}" / "trades.parquet"
    live_mf = LOCAL_ROOT / "manifests" / f"MANIFEST-{a.date}.json"
    if live_pq.exists() or live_mf.exists():
        print(f"REFUSED: {a.date} is held locally "
              f"({'partition' if live_pq.exists() else 'manifest'} present). "
              f"Use --local-batch for locally-held days; --date is for the archive hole only.")
        return 2
    stage_dir = STAGE_ROOT / "single"
    shutil.rmtree(stage_dir, ignore_errors=True)
    stage_dir.mkdir(parents=True, exist_ok=True)
    times: dict[str, float] = {}
    t0 = time.time()
    print(f"vision->hf  {a.date}  (daily granularity)")
    print(f"  free at start: {shutil.disk_usage(REPO).free / 1e9:.2f} GB\n")
    throttle_log: list = []

    with DiskWatch(REPO) as watch:
        t = time.time()
        row = iv.ingest_day(date=a.date, out_root=stage_dir, market="futures/um",
                            family="aggTrades", vendor_symbol="BTCUSDT",
                            venue="binancef", symbol="BTCUSDT", granularity="daily",
                            say=lambda *x, **k: None)
        times["1-4 fetch+verify+normalize+sha256"] = time.time() - t
        if row.get("status") != "ok":
            print(f"  STATE 1-4 FAILED: status={row.get('status')} — {row}")
            return 2
        base = stage_dir / "binancef" / "BTCUSDT" / "aggTrades"
        pq = base / f"date={a.date}" / "trades.parquet"
        mf = base / "manifests" / f"MANIFEST-{a.date}.json"
        man = json.loads(mf.read_text())
        norm, src = man["normalized"], man["source"]
        local_sha = sha256_file(pq)
        print(f"  1-4 ok  rows={norm['rows']:,}  parquet={pq.stat().st_size / 1e6:.2f} MB")
        print(f"         zip {src['zip_bytes'] / 1e6:.1f} MB · sha256 verified vs venue: "
              f"{src['checksum_verified']}")
        if local_sha != norm["sha256"]:
            print(f"  STATE 4 FAILED: my sha256 {local_sha[:16]} != manifest "
                  f"{norm['sha256'][:16]}")
            return 2

        t = time.time()
        dest = f"{VISION_PREFIX}/date={a.date}/trades.parquet"
        retry_hf(lambda: uh.hf_upload_file(HF_REPO, pq, dest,
                                           f"vision aggTrades {a.date}"),
                 f"upload {a.date}", throttle_log)
        retry_hf(lambda: uh.hf_upload_file(
            HF_REPO, mf, f"{VISION_PREFIX}/manifests/MANIFEST-{a.date}.json",
            f"vision manifest {a.date}"), f"upload manifest {a.date}", throttle_log)
        times["5 upload"] = time.time() - t
        mb = pq.stat().st_size / 1e6
        print(f"  5   uploaded  {mb:.2f} MB in {times['5 upload']:.1f}s "
              f"= {mb / max(times['5 upload'], 1e-9):.2f} MB/s")

        t = time.time()
        back_dir = stage_dir / "readback"
        back_dir.mkdir(exist_ok=True)
        got = retry_hf(lambda: uh.hf_download_file(HF_REPO, dest, back_dir),
                       f"readback {a.date}", throttle_log)
        remote_sha = sha256_file(Path(got))
        times["6 read-back"] = time.time() - t
        match = remote_sha == local_sha
        print(f"  6   read back {Path(got).stat().st_size / 1e6:.2f} MB "
              f"in {times['6 read-back']:.1f}s")
        print(f"      local  {local_sha}")
        print(f"      remote {remote_sha}")
        print(f"      ->  {'MATCH' if match else 'MISMATCH'}")

    if not match:
        print("\n  STATE 6 FAILED -> state = remote_verify_failed.")
        append_state({"ts": now_iso(), "date": a.date, "mode": "full-fetch",
                      "state": "remote_verify_failed"})
        return 2
    # No live copy exists on this path (the guard above refused held-locally dates), so
    # the honest terminal state is migrated_no_local — never "deleted". State is
    # recorded AFTER the facts it describes, mirroring migrate_one_local's ordering.
    append_state({"ts": now_iso(), "date": a.date, "mode": "full-fetch",
                  "dry_run": a.dry_run,
                  "state": "readback_ok" if a.dry_run else "migrated_no_local"})
    print(f"\n  7   {'DRY RUN — ' if a.dry_run else ''}no local copy existed; "
          f"state = {'readback_ok' if a.dry_run else 'migrated_no_local'}")

    shutil.rmtree(stage_dir, ignore_errors=True)
    total = time.time() - t0
    print(f"\n  === CONTROL RESULT (this partition is a control, not a result) ===")
    for k, v in times.items():
        print(f"    {k:<38} {v:>7.1f}s")
    print(f"    {'TOTAL wall clock':<38} {total:>7.1f}s")
    print(f"    peak disk USED during run          {watch.peak_used / 1e6:>7.1f} MB")
    print(f"    free at end                        {shutil.disk_usage(REPO).free / 1e9:>7.2f} GB")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--date", help="full fetch path for one day NOT held locally")
    g.add_argument("--local-batch", type=int, metavar="N",
                   help="upload-only path for N already-local partitions")
    ap.add_argument("--order", choices=["newest", "oldest"], default="newest")
    ap.add_argument("--dry-run", action="store_true",
                    help="everything except the local delete")
    ap.add_argument("--plan", action="store_true",
                    help="print the batch selection and exit; no network")
    a = ap.parse_args()
    if a.date and a.plan:
        ap.error("--plan is only meaningful with --local-batch; refusing the ambiguity "
                 "(a flag advertised as 'no network' must never reach the fetch path)")

    uh = _load("upload_hf", REPO / "scripts" / "upload_hf.py")
    if a.date:
        iv = _load("ingest_vision", REPO / "scripts" / "ingest_vision.py")
        return run_single(a, iv, uh)
    return run_batch(a, uh)


if __name__ == "__main__":
    sys.exit(main())
