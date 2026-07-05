#!/usr/bin/env python3
"""upload_hf.py — Hugging Face data lifecycle for the day-rotated tick store (DESIGN §3c).

Collector v2 rotates the tick store into per-UTC-day files
(``data/ticks/YYYY-MM-DD.duckdb``, event-time routed). A CLOSED day file is
immutable — which is exactly what makes a gap-free offsite copy possible. This
script moves closed days to the primary offsite home, the HF dataset repo
``azulcoder/btc-quant-ticks``:

    export each non-empty table -> <stage>/date=YYYY-MM-DD/<table>.parquet (ZSTD)
    -> re-read verify (count + ts range inside the day)
    -> streamed sha256 into manifests/MANIFEST-<date>.json
    -> upload to data/date=YYYY-MM-DD/ (hive-style) + the manifest
    -> VERIFY ON THE HUB (byte sizes; LFS sha256 when the API exposes it,
       else a re-download spot check of the smallest file)
    -> ONLY THEN delete the local day file.

Unlike the monthly GitHub-Release path (scripts/archive_ticks.py, still
functional but no longer the scheduled lifecycle), this needs NO collector
stop: the writer only ever holds today (+ yesterday during the 5-minute
midnight grace window), and this script only touches days the writer has
already closed. Scheduled daily ~00:20 UTC via
scripts/com.btcquant.hfsync.plist.example (``make hf-sync``).

Honesty rails (DESIGN §0 + §3c, binding — same prune-safety creed as archive_ticks.py)
--------------------------------------------------------------------------------------
* **No offsite verification, no local delete.** A day file dies only after the
  Hub listing confirms every uploaded byte (size for every file, sha256 where
  the API exposes it — plus a re-download spot check when it does not).
* **Date partitions are immutable.** ``data/date=YYYY-MM-DD/`` is never
  overwritten. A partition that already exists on the Hub with DIFFERENT
  content is refused outright; one whose manifest matches the local day
  (same tables, row counts, ts extents) is treated as already-archived and the
  run merely completes the lifecycle (delete the local file once eligible) —
  that is what makes the daily launchd job idempotent.
* **Today and yesterday are never deleted** (§3c): they stay local for the
  BYOD replay API. Yesterday IS uploaded once closed — it just keeps its
  local copy until it ages out of the keep-local window.
* **Gaps stay gaps.** Nothing here fills, merges, or interpolates; a day file
  is exported exactly as recorded, and the manifest states the real ts extent.
* **The token is never printed.** Auth comes from the user's own HF login
  (``hf auth login`` — an account credential, not market-data access; every
  recorded feed is keyless).

Exit codes (mirrors archive_ticks.py where the semantics overlap)
-----------------------------------------------------------------
* 0  — done (including honest no-ops: nothing closed yet, day skipped because
       the writer still holds it, dry-run).
* 1  — a step failed/was refused; nothing was deleted past a verified point.
* 64 — usage error (bad --date, a --date that is still being written, ...).

Usage
-----
    python3 scripts/upload_hf.py --dry-run          # stage + verify only
    python3 scripts/upload_hf.py --yes              # the scheduled lifecycle (make hf-sync)
    python3 scripts/upload_hf.py --date 2026-07-02 --keep-local
    python3 scripts/upload_hf.py --refresh-card     # rewrite the dataset card

Requires the opt-in collector deps (pip install -r requirements-collector.txt,
which now includes huggingface_hub) and a one-time ``hf auth login``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Guarded opt-in imports (requirements-collector.txt) — same discipline as      #
# btcquant/collector.py: importing this file never explodes; the actionable     #
# hint fires only when the store / the Hub is actually touched.                 #
# --------------------------------------------------------------------------- #
try:  # optional dependency — see requirements-collector.txt
    import duckdb  # type: ignore
except Exception:  # noqa: BLE001 — any import failure means "store unavailable"
    duckdb = None  # type: ignore[assignment]

try:  # optional dependency — see requirements-collector.txt
    import huggingface_hub  # type: ignore
except Exception:  # noqa: BLE001
    huggingface_hub = None  # type: ignore[assignment]

_INSTALL_HINT = "pip install -r requirements-collector.txt"
_HF_HINT = (
    f"{_INSTALL_HINT}  (pulls huggingface_hub>=1.0), then a one-time `hf auth login` "
    "— the token stays in your HF credential store and is never printed here"
)

# --------------------------------------------------------------------------- #
# Reuse the PROVEN lifecycle helpers from archive_ticks.py (same directory;    #
# scripts/ is not a package, so load it by path — the test-suite idiom). We    #
# import rather than restate: sha256/export-verify/lock handling drifting      #
# between the two lifecycles is exactly the bug class the seam prevents.       #
# --------------------------------------------------------------------------- #
_spec = importlib.util.spec_from_file_location(
    "archive_ticks", Path(__file__).resolve().with_name("archive_ticks.py")
)
arch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arch)

Abort = arch.ArchiveAbort  # same contract: message + exit code, nothing destroyed
sha256_file = arch.sha256_file
export_parquet_verified = arch.export_parquet_verified
_fmt_ts = arch._fmt_ts
_fmt_bytes = arch._fmt_bytes

EXIT_OK, EXIT_FAIL, EXIT_USAGE = arch.EXIT_OK, arch.EXIT_FAIL, arch.EXIT_USAGE

# --------------------------------------------------------------------------- #
# Constants.                                                                   #
# --------------------------------------------------------------------------- #
DEFAULT_TICKS_DIR = "data/ticks"
DEFAULT_REPO = "azulcoder/btc-quant-ticks"
DEFAULT_OUT = "data/hf-stage"

# Provenance stamp baked into every manifest (§3c wording): keyless public
# feeds, recorded by the collector, event-time day partitions, honest gaps.
PROVENANCE = (
    "keyless public market data recorded by btcquant/collector.py "
    "(DESIGN-orderflow-terminal.md §3c) — event-time UTC day partitions; "
    "closed day files are immutable; gaps in ts_ms are honest collector "
    "downtime, never interpolated"
)

# 'Closed' detection for yesterday's file: the writer keeps yesterday open for
# a 5-minute grace window past UTC midnight (late/out-of-order rows land in the
# right day), then flushes + closes it. 6 minutes = grace + 1 min flush slack.
GRACE_CLOSE_MIN = 6

MS_PER_DAY = 86_400_000
_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Table names come from the day file itself (v2 adds tables; migrated v1 days
# have fewer) — validate the identifier before it is ever interpolated into SQL.
_TABLE_NAME_RE = re.compile(r"[a-z_][a-z0-9_]*")

# v2 schema (DESIGN §3c) in canonical order — used for stable export/card
# ordering; unknown-but-valid extra tables sort after these, alphabetically.
_CANONICAL_TABLES = (
    "trades", "liquidations", "depth_snapshots", "funding_mark",
    "open_interest", "crowding", "dvol", "options_chain",
)


def _now_ms() -> int:
    """Wall clock, epoch ms. A function (not inline) so tests can freeze time."""
    return int(time.time() * 1000)


def day_bounds(date_str: str) -> tuple[int, int]:
    """'YYYY-MM-DD' -> [start_ms, end_ms) in UTC. Raises ValueError on bad input."""
    if not _DAY_RE.fullmatch(date_str):
        raise ValueError(f"bad date {date_str!r} — expected YYYY-MM-DD")
    start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    a = int(start.timestamp() * 1000)
    return a, a + MS_PER_DAY


def _utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Closed-day selection (§3c): all days strictly older than yesterday, plus     #
# yesterday once its file is verifiably closed. Today is NEVER a candidate.    #
# --------------------------------------------------------------------------- #
def _writer_holds_lock(path: Path) -> bool:
    """True iff a live writer holds the DuckDB lock on ``path`` (a read-only
    open fails with a lock conflict). Any non-lock open failure returns False —
    the real open later in the flow surfaces it with a proper message."""
    try:
        con = duckdb.connect(str(path), read_only=True)
        con.close()
        return False
    except duckdb.Error as exc:
        return "lock" in str(exc).lower()


def day_file_closed(path: Path, date_str: str, now_ms: int) -> tuple[bool, str]:
    """Is yesterday's day file closed? (closed, reason).

    Closed = the rotation grace window has elapsed AND (the file's mtime
    predates the grace end — nothing wrote to it after the window — OR no
    writer holds the lock). Inside the grace window nothing is closed yet:
    late event-time rows may still legitimately land in yesterday (§3c).
    """
    grace_end_ms = day_bounds(date_str)[1] + GRACE_CLOSE_MIN * 60_000
    if now_ms < grace_end_ms:
        return False, (
            f"inside the midnight rotation grace window (until {_fmt_ts(grace_end_ms)}) "
            "— late rows may still land in this day"
        )
    if int(path.stat().st_mtime * 1000) < grace_end_ms:
        return True, "mtime predates the grace-window end"
    if not _writer_holds_lock(path):
        return True, "no writer lock on the file"
    return False, "the writer still holds the lock — will retry on the next run"


def list_day_files(ticks_dir: Path) -> dict[str, Path]:
    """{date: path} for every YYYY-MM-DD.duckdb in the rotation directory."""
    return {
        f.stem: f
        for f in sorted(ticks_dir.glob("*.duckdb"))
        if _DAY_RE.fullmatch(f.stem)
    }


def select_days(ticks_dir: Path, now_ms: int) -> tuple[list[str], list[str]]:
    """Default candidates: (dates to sync, honest skip notes).

    Precisely (task/§3c rule): every local day file strictly older than
    yesterday, plus yesterday once its file is closed. Today (and any future-
    dated stray) is never touched — the writer owns it.
    """
    today = _utc_date(now_ms)
    yesterday = _utc_date(now_ms - MS_PER_DAY)
    picked: list[str] = []
    notes: list[str] = []
    for date, path in list_day_files(ticks_dir).items():
        if date >= today:
            notes.append(f"{date}: skipped — {'today' if date == today else 'future-dated?!'}, still the writer's")
        elif date == yesterday:
            closed, why = day_file_closed(path, date, now_ms)
            if closed:
                picked.append(date)
            else:
                notes.append(f"{date}: skipped — not closed yet ({why})")
        else:
            picked.append(date)
    return sorted(picked), notes


# --------------------------------------------------------------------------- #
# Hub seams — EVERY huggingface_hub touch goes through these tiny functions so #
# tests monkeypatch THEM (an in-memory fake) and never hit the network. Same   #
# discipline as archive_ticks.py's _run_gh choke point.                        #
# --------------------------------------------------------------------------- #
def _hf_api():
    """HfApi from the user's existing ``hf auth`` login. The token is resolved
    by huggingface_hub itself (credential store / env) — never handled, logged,
    or printed by this script."""
    if huggingface_hub is None:
        raise Abort(f"huggingface_hub missing — {_HF_HINT}")
    return huggingface_hub.HfApi()


def hf_ensure_repo(repo: str) -> bool:
    """Create the dataset repo if needed (exist_ok). Returns True iff it was
    newly created this call (first creation also gets the dataset card)."""
    api = _hf_api()
    try:
        existed = bool(api.repo_exists(repo_id=repo, repo_type="dataset"))
        api.create_repo(repo_id=repo, repo_type="dataset", exist_ok=True)
        return not existed
    except Exception as exc:  # noqa: BLE001 — translate to an actionable abort
        raise Abort(
            f"cannot reach/create dataset repo {repo!r}: {type(exc).__name__}: {exc} "
            f"— check `hf auth whoami`; if unauthenticated: {_HF_HINT}"
        ) from exc


def hf_list_files(repo: str, prefix: str) -> list[dict]:
    """[{path, size, lfs_sha256|None}] for every FILE under ``prefix`` in the
    dataset repo ([] if the path does not exist). lfs_sha256 is the Hub's own
    content hash for LFS-stored files — the strongest verification the API
    exposes without a re-download."""
    api = _hf_api()
    try:
        items = api.list_repo_tree(
            repo_id=repo, repo_type="dataset", path_in_repo=prefix, recursive=True
        )
        out: list[dict] = []
        for item in items:
            size = getattr(item, "size", None)
            if size is None:
                continue  # folders carry no size — only files verify
            lfs = getattr(item, "lfs", None)
            sha = getattr(lfs, "sha256", None) if lfs is not None else None
            if sha is None and isinstance(lfs, dict):
                sha = lfs.get("sha256")
            out.append({"path": item.path, "size": int(size), "lfs_sha256": sha})
        return out
    except Exception as exc:  # noqa: BLE001 — absent path == empty listing
        if type(exc).__name__ == "EntryNotFoundError":
            return []
        raise Abort(
            f"cannot list {repo}:{prefix}: {type(exc).__name__}: {exc} — {_HF_HINT}"
        ) from exc


def hf_upload_folder(repo: str, folder: Path, path_in_repo: str, message: str) -> None:
    _hf_api().upload_folder(
        repo_id=repo, repo_type="dataset", folder_path=str(folder),
        path_in_repo=path_in_repo, commit_message=message,
    )


def hf_upload_file(repo: str, local: Path, path_in_repo: str, message: str) -> None:
    _hf_api().upload_file(
        repo_id=repo, repo_type="dataset", path_or_fileobj=str(local),
        path_in_repo=path_in_repo, commit_message=message,
    )


def hf_download_file(repo: str, path_in_repo: str, dest_dir: Path) -> Path:
    """Fetch one repo file into ``dest_dir`` (spot-check / manifest read)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = _hf_api().hf_hub_download(
        repo_id=repo, repo_type="dataset", filename=path_in_repo,
        local_dir=str(dest_dir),
    )
    return Path(p)


# --------------------------------------------------------------------------- #
# Staging: export + verify + sha + manifest for ONE closed day.                #
# --------------------------------------------------------------------------- #
def day_tables(con) -> list[str]:
    """Base tables in the day file, canonical §3c order first (v1-migrated days
    have 5 tables, v2 days 8 — export whatever is actually there). Any name
    that is not a sane SQL identifier aborts: table names get interpolated
    into COPY (which cannot bind parameters), so they must be validated."""
    names = [
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
        ).fetchall()
    ]
    bad = [n for n in names if not _TABLE_NAME_RE.fullmatch(n)]
    if bad:
        raise Abort(f"day file has non-collector table name(s) {bad!r} — wrong file?")
    order = {t: i for i, t in enumerate(_CANONICAL_TABLES)}
    return sorted(names, key=lambda n: (order.get(n, len(order)), n))


def stage_day(day_file: Path, date: str, stage_dir: Path, say=print) -> dict:
    """Export every non-empty table of one closed day file to ZSTD parquet
    under ``<stage_dir>/date=<date>/``, re-read verify, sha256, and return the
    manifest dict. Raises Abort on any verification failure (files kept for
    inspection) or if the file is still locked (caller decides skip vs abort).

    The per-date stage dir is recreated fresh each run: the stage is TRANSIENT
    (unlike data/archive, which IS the offsite copy for the GH-Release path) —
    immutability is enforced where this archive actually lives, on the Hub
    partition, not on the scratch copy.
    """
    a, b = day_bounds(date)
    part_dir = stage_dir / f"date={date}"
    shutil.rmtree(part_dir, ignore_errors=True)
    part_dir.mkdir(parents=True, exist_ok=True)

    # connect_readonly is the proven lock-aware open from archive_ticks (a lock
    # conflict raises Abort(EXIT_LOCKED) — the caller turns that into a skip).
    con = arch.connect_readonly(day_file)
    entries: list[dict] = []
    try:
        for table in day_tables(con):
            n, tmin, tmax = con.execute(
                f"SELECT count(*), min(ts_ms), max(ts_ms) FROM {table}"  # noqa: S608 — validated name
            ).fetchone()
            if not n:
                say(f"  [{date}] {table}: empty — skipped (no empty parquet files)")
                continue
            if not (a <= tmin and tmax < b):
                # Event-time routing (§3c) makes this impossible for an honest
                # day file — rows outside the day mean the partition is NOT what
                # its name claims, and uploading it would poison the dataset.
                raise Abort(
                    f"{day_file.name}: {table} holds rows outside its event-time day "
                    f"([{_fmt_ts(tmin)} .. {_fmt_ts(tmax)}] vs [{_fmt_ts(a)} .. {_fmt_ts(b)})) "
                    "— refusing; a day file IS its partition"
                )
            dest = part_dir / f"{table}.parquet"
            rows, ts_min, ts_max, n_bytes = export_parquet_verified(
                con, table, dest, a, b, (n, tmin, tmax)
            )
            entries.append(
                {
                    "table": table, "rows": rows, "ts_min": ts_min, "ts_max": ts_max,
                    "bytes": n_bytes, "sha256": sha256_file(dest),
                }
            )
            say(
                f"  [{date}] {table}: {rows:,} rows -> {dest.name} "
                f"({_fmt_bytes(n_bytes)}, re-read verified, sha256 ok)"
            )
    finally:
        con.close()

    if not entries:
        raise Abort(f"{day_file.name}: every table is empty — nothing to archive for {date}")

    manifest = {
        "date": date,
        "createdMs": _now_ms(),
        "tool": "upload_hf.py",
        "provenance": PROVENANCE,
        "entries": entries,
    }
    man_dir = stage_dir / "manifests"
    man_dir.mkdir(parents=True, exist_ok=True)
    (man_dir / f"MANIFEST-{date}.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def manifests_match(local: dict, remote: Optional[dict]) -> bool:
    """Same data? Compared on (table -> rows, ts_min, ts_max) — NOT on bytes or
    sha256, because parquet bytes are not stable across duckdb/zstd versions;
    row counts and honest ts extents ARE the identity of a recorded day."""
    if not isinstance(remote, dict):
        return False

    def key(m: dict) -> dict:
        return {
            e["table"]: (int(e["rows"]), int(e["ts_min"]), int(e["ts_max"]))
            for e in m.get("entries", [])
        }

    return remote.get("date") == local.get("date") and key(remote) == key(local)


# --------------------------------------------------------------------------- #
# Hub-side verification — the gate every local delete stands behind.           #
# --------------------------------------------------------------------------- #
def verify_on_hub(repo: str, date: str, entries: list[dict], scratch: Path) -> str:
    """Verify the uploaded partition against the local manifest. Returns a
    human summary of HOW it verified; raises Abort (nothing deleted) otherwise.

    Byte sizes must match for every file; the Hub's LFS sha256 is checked
    wherever the API exposes it. If NO file got a sha check (small files are
    stored non-LFS), the smallest file is re-downloaded and sha-verified — a
    size match alone would miss a same-length corruption."""
    listing = {f["path"]: f for f in hf_list_files(repo, f"data/date={date}")}
    sha_checked = 0
    for e in entries:
        path = f"data/date={date}/{e['table']}.parquet"
        item = listing.get(path)
        if item is None:
            raise Abort(f"Hub verification FAILED: {path} missing after upload — nothing deleted")
        if item["size"] != e["bytes"]:
            raise Abort(
                f"Hub verification FAILED for {path}: Hub reports {item['size']} bytes, "
                f"local parquet is {e['bytes']} — nothing deleted"
            )
        if item.get("lfs_sha256"):
            if item["lfs_sha256"] != e["sha256"]:
                raise Abort(
                    f"Hub verification FAILED for {path}: LFS sha256 mismatch — nothing deleted"
                )
            sha_checked += 1
    # Folder listing + client-side filter (a folder prefix is the well-defined
    # tree-endpoint call; a bare file path is not guaranteed across hub versions).
    manifest_path = f"manifests/MANIFEST-{date}.json"
    if not any(f["path"] == manifest_path for f in hf_list_files(repo, "manifests")):
        raise Abort(f"Hub verification FAILED: {manifest_path} missing — nothing deleted")
    if sha_checked == 0:
        smallest = min(entries, key=lambda e: e["bytes"])
        path = f"data/date={date}/{smallest['table']}.parquet"
        got = hf_download_file(repo, path, scratch)
        if sha256_file(got) != smallest["sha256"]:
            raise Abort(
                f"Hub verification FAILED: re-downloaded {path} sha256 mismatch — nothing deleted"
            )
        return (
            f"sizes match ({len(entries)} files); no LFS sha exposed — "
            f"spot-check re-download of {smallest['table']}.parquet sha256-verified"
        )
    return f"sizes match ({len(entries)} files); {sha_checked}/{len(entries)} LFS sha256 verified"


# --------------------------------------------------------------------------- #
# Dataset card (README.md on the dataset repo) — first creation / --refresh.   #
# --------------------------------------------------------------------------- #
def build_dataset_card(repo: str) -> str:
    """Honest dataset card: schema, provenance, rails, cadence, license note."""
    configs = "\n".join(
        f"- config_name: {t}\n  data_files: data/date=*/{t}.parquet" for t in _CANONICAL_TABLES
    )
    return f"""---
pretty_name: btc-quant tick store (BTC perp microstructure, keyless public feeds)
tags:
- bitcoin
- market-data
- order-flow
- time-series
configs:
{configs}
---

# btc-quant ticks — keyless BTC perp microstructure, one partition per UTC day

Raw microstructure recorded by
[btc-quant](https://github.com/azulcoder/btc-quant)'s `btcquant/collector.py`
(DESIGN-orderflow-terminal.md §3c). **Keyless public WS/REST feeds only** —
no accounts, no signed endpoints, no orders. Partitions are **event-time UTC
days** (every row lands in the day of its own `ts_ms`, not its arrival time)
and are **immutable once uploaded**; each ships with
`manifests/MANIFEST-<date>.json` (per-table rows, ts extent, bytes, sha256).

## Layout

```
data/date=YYYY-MM-DD/<table>.parquet   # hive-style, ZSTD
manifests/MANIFEST-YYYY-MM-DD.json     # provenance + checksums per day
```

## Query it in place

```sql
-- duckdb (httpfs): one day
SELECT count(*) FROM read_parquet('hf://datasets/{repo}/data/date=2026-07-04/trades.parquet');
-- every day, hive partitioning
SELECT * FROM read_parquet('hf://datasets/{repo}/data/date=*/trades.parquet', hive_partitioning=1);
```

```python
from datasets import load_dataset
ds = load_dataset("{repo}", "trades", streaming=True)  # one config per table
```

## Schema (all timestamps epoch **ms**, UTC; exchange codes binancef|bybit|coinbase|okx)

| table | columns | notes |
|---|---|---|
| trades | exchange, symbol, trade_id, ts_ms, price, qty, aggressor_buy | aggressor conventions normalized per venue (Coinbase `side` is the MAKER — inverted; Binance `m` true = SELL aggressor; Bybit/OKX taker side as-is); OKX qty = sz x ctVal (coin) |
| liquidations | exchange, symbol, ts_ms, side, price, qty, notional_usd | side = the LIQUIDATED position (`long`/`short`), not the printed order |
| depth_snapshots | exchange, symbol, ts_ms, bids, asks | JSON `[[price,qty]...]` best-first; top-50 (bybit/okx), top-20 (binancef — that is the whole wire); 1/s downsample |
| funding_mark | exchange, symbol, ts_ms, mark, index, funding_rate, next_funding_ts | funding_rate is the raw per-interval decimal, never annualized in storage |
| open_interest | exchange, symbol, ts_ms, oi | contracts/coin as delivered — no silent USD conversion |
| crowding | exchange, symbol, ts_ms, metric, value | binancef futures/data @5m, long format (taker_buy_sell_ratio, top_position_ls_ratio, global_account_ls_ratio, oi_sum_coin, oi_sum_usd) |
| dvol | ts_ms, index_price | Deribit DVOL, 60 s |
| options_chain | ts_ms, name, expiry_ts, strike, cp, iv, oi, volume, mark_price, underlying | Deribit book summary, hourly; iv stored as a DECIMAL (already /100) |

Days migrated from the pre-rotation store may carry only the first five tables.

## Honesty notes (binding rails, DESIGN §0)

- **Gaps stay gaps.** Collector downtime is a real hole in `ts_ms` — never
  interpolated, backfilled, or blended across sources.
- **Event-time partitions.** A 5-minute grace window at UTC midnight lets
  late/out-of-order rows land in the correct day before it closes.
- **Binance futures trades arrive via the REST `aggTrades` poll** (5 s cursor,
  gapless by aggTradeId) — the WS trade topic is filtered on the recording
  network; we record what the wire actually delivers.
- This is **descriptive research data**, not a signal, a backtest input, or
  investment advice.

## Update cadence

Daily, ~00:20 UTC (`make hf-sync` via launchd): every closed local day is
exported, verified, uploaded, verified again on the Hub, and only then removed
from the recording machine.

## License / warranty

Public market data recorded from keyless public exchange endpoints; redistributed
as-is, **no warranty of any kind**, use at your own risk. Exchange terms may
apply to commercial redistribution — check them before building on this.
"""


# --------------------------------------------------------------------------- #
# CLI.                                                                         #
# --------------------------------------------------------------------------- #
class _Parser(argparse.ArgumentParser):
    """Usage errors exit 64 with a clean message (archive_ticks contract) —
    a typo'd flag in the launchd/make wrapper must never read as a data failure."""

    def error(self, message: str):  # noqa: ANN201 — never returns (raises SystemExit)
        self.print_usage(sys.stderr)
        print(f"usage error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="upload_hf.py",
        description=(
            "HF data lifecycle for the day-rotated tick store: export closed UTC "
            "day files to parquet, upload to the HF dataset, verify on the Hub, "
            "and only then delete the local day file (today + yesterday always "
            "stay local). Exit 0 = done, 1 = refused/failed (nothing deleted "
            "past a verified point), 64 = usage error."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--ticks-dir", default=DEFAULT_TICKS_DIR, help="Day-file rotation directory (DESIGN §3c).")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="HF dataset repo id.")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Transient local staging dir for parquet + manifests.")
    parser.add_argument(
        "--date",
        action="append",
        metavar="YYYY-MM-DD",
        help="Day to sync (repeatable). Default: every CLOSED local day — all days "
        "strictly older than yesterday, plus yesterday once its file is closed.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Stage + verify + manifest only: no Hub calls, no deletes.")
    parser.add_argument("--keep-local", action="store_true", help="Upload + verify but never delete local day files.")
    parser.add_argument("--refresh-card", action="store_true", help="(Re)write the dataset card (README.md on the dataset repo).")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirm before local deletes (launchd/cron).")
    return parser


def _confirm_deletes(candidates: list[tuple[str, Path]], yes: bool) -> bool:
    """Confirm the ONLY destructive step up front (per-day deletes then run
    after each day's Hub verification). Returns whether deletes are authorized;
    an unauthorized run still uploads + verifies — it just keeps every file
    (honest degradation for a cron job that forgot --yes, printed loudly)."""
    if not candidates:
        return False
    total = sum(p.stat().st_size for _, p in candidates)
    print(
        f"after Hub-verified upload, {len(candidates)} local day file(s) will be "
        f"DELETED ({_fmt_bytes(total)}):"
    )
    for date, p in candidates:
        print(f"    {p} ({_fmt_bytes(p.stat().st_size)})")
    if yes:
        return True
    if not sys.stdin.isatty():
        print(
            "no tty and no --yes — uploads proceed but local deletes are SKIPPED "
            "this run (pass --yes in the scheduled job to actually free disk)"
        )
        return False
    return input("    type 'yes' to enable the deletes: ").strip().lower() == "yes"


def _delete_day_file(day_file: Path) -> int:
    """Remove one verified-offsite day file (+ any stray WAL). Returns bytes freed."""
    freed = day_file.stat().st_size
    wal = Path(str(day_file) + ".wal")
    if wal.exists():  # a closed day should have none — a crash leftover goes too
        freed += wal.stat().st_size
        wal.unlink()
    day_file.unlink()
    return freed


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if duckdb is None:
        print(f"ERROR: duckdb missing — {_INSTALL_HINT}", file=sys.stderr)
        return EXIT_FAIL

    ticks_dir = Path(args.ticks_dir)
    stage_dir = Path(args.out)
    scratch = stage_dir / ".remote"
    now_ms = _now_ms()
    today = _utc_date(now_ms)
    yesterday = _utc_date(now_ms - MS_PER_DAY)

    try:
        files = list_day_files(ticks_dir) if ticks_dir.is_dir() else {}

        # ---- pick the days ----
        if args.date:
            dates = sorted(set(args.date))
            for d in dates:
                try:
                    day_bounds(d)
                except ValueError as exc:
                    print(f"usage error: {exc}", file=sys.stderr)
                    return EXIT_USAGE
                if d >= today:
                    print(
                        f"usage error: {d} is {'today' if d == today else 'in the future'} — "
                        "the writer still owns it (today + yesterday-in-grace are never synced)",
                        file=sys.stderr,
                    )
                    return EXIT_USAGE
                if d not in files:
                    raise Abort(f"no day file {ticks_dir}/{d}.duckdb — nothing recorded for {d}?")
                if d == yesterday:
                    closed, why = day_file_closed(files[d], d, now_ms)
                    if not closed:
                        raise Abort(f"{d} is not closed yet ({why}) — nothing synced")
        else:
            dates, notes = select_days(ticks_dir, now_ms)
            for note in notes:
                print(f"[select] {note}")

        if not dates and not args.refresh_card:
            print(
                f"nothing to sync — no closed day files in {ticks_dir} "
                "(today + yesterday-in-grace always stay local). (exit 0)"
            )
            return EXIT_OK
        if dates:
            print(f"[plan] {len(dates)} closed day(s): {', '.join(dates)}" + (" (dry-run)" if args.dry_run else ""))

        # ---- authorize deletes up front (they RUN only after per-day verify) ----
        # Yesterday is uploaded but NEVER deleted (§3c: today + yesterday stay
        # local for the BYOD API); it ages into deletion on a later run.
        delete_candidates = (
            [(d, files[d]) for d in dates if d < yesterday]
            if not (args.dry_run or args.keep_local)
            else []
        )
        deletes_ok = _confirm_deletes(delete_candidates, args.yes)
        deletable = {d for d, _ in delete_candidates} if deletes_ok else set()

        # ---- per-day flow ----
        repo_created = False
        hub_touched = False
        freed_total = 0
        synced: list[str] = []
        delete_failures: list[str] = []
        for date in dates:
            day_file = files[date]

            # 1) stage: export + re-read verify + sha256 + manifest.
            try:
                manifest = stage_day(day_file, date, stage_dir)
            except Abort as exc:
                if exc.code == arch.EXIT_LOCKED:
                    # A still-locked file is a skip, not a failure — the whole
                    # point of the daily path is needing NO collector stop.
                    print(f"  [{date}] SKIPPED — {day_file.name} is still locked by a writer")
                    continue
                raise
            entries = manifest["entries"]

            if args.dry_run:
                print(f"  [{date}] dry-run: staged + verified only — nothing uploaded, nothing deleted")
                continue

            # 2) repo (once), card on first creation.
            if not hub_touched:
                repo_created = hf_ensure_repo(args.repo)
                hub_touched = True

            # 3) immutability gate: an existing partition is refused unless its
            #    manifest matches this day exactly (then the run just completes
            #    the lifecycle — the idempotent-cron case).
            existing = hf_list_files(args.repo, f"data/date={date}")
            if existing:
                remote_man = None
                manifest_path = f"manifests/MANIFEST-{date}.json"
                if any(f["path"] == manifest_path for f in hf_list_files(args.repo, "manifests")):
                    got = hf_download_file(args.repo, manifest_path, scratch)
                    remote_man = json.loads(Path(got).read_text())
                if not manifests_match(manifest, remote_man):
                    raise Abort(
                        f"data/date={date}/ already exists on {args.repo} with different/"
                        "unverifiable content — date partitions are IMMUTABLE (§3c). If the "
                        "replacement is truly intended, delete the partition + manifest on "
                        "the Hub first (huggingface_hub delete_folder) and re-run. "
                        "Nothing deleted locally."
                    )
                # Same data offsite: check the Hub copy is internally consistent
                # (listing sizes vs its OWN manifest) before trusting it.
                sizes = {f["path"]: f["size"] for f in existing}
                for e in remote_man["entries"]:
                    path = f"data/date={date}/{e['table']}.parquet"
                    if sizes.get(path) != int(e["bytes"]):
                        raise Abort(
                            f"already-archived {path} does not match its own Hub manifest "
                            f"({sizes.get(path)} vs {e['bytes']} bytes) — refusing to trust "
                            "it; nothing deleted locally"
                        )
                print(f"  [{date}] already on the Hub (manifest matches) — no re-upload")
                how = "pre-existing partition re-verified against its Hub manifest"
            else:
                # 4) upload the partition folder + the manifest, then VERIFY.
                hf_upload_folder(
                    args.repo, stage_dir / f"date={date}", f"data/date={date}",
                    f"ticks {date}: {sum(e['rows'] for e in entries):,} rows across {len(entries)} table(s)",
                )
                hf_upload_file(
                    args.repo, stage_dir / "manifests" / f"MANIFEST-{date}.json",
                    f"manifests/MANIFEST-{date}.json", f"manifest for {date}",
                )
                how = verify_on_hub(args.repo, date, entries, scratch)
                print(f"  [{date}] uploaded -> data/date={date}/ + manifest; Hub verify: {how}")

            # 5) ONLY THEN delete (never today/yesterday; --keep-local keeps all).
            synced.append(date)
            shutil.rmtree(stage_dir / f"date={date}", ignore_errors=True)  # stage is transient
            if date in deletable:
                try:
                    freed = _delete_day_file(day_file)
                except OSError as exc:
                    # The Hub copy is verified — the DATA is safe; only the local
                    # cleanup failed (permissions, disk snapshot, ...). Say exactly
                    # that, keep syncing the remaining days, and exit nonzero at the
                    # end: a raw traceback here would read as a data failure and
                    # abort days that could still complete their lifecycle.
                    delete_failures.append(date)
                    print(
                        f"  [{date}] Hub copy is VERIFIED but the local delete FAILED "
                        f"({exc}) — {day_file} remains on disk; safe to re-run "
                        "(the matching-manifest path completes the lifecycle)"
                    )
                else:
                    freed_total += freed
                    print(f"  [{date}] local day file deleted — freed {_fmt_bytes(freed)}")
            elif date == yesterday:
                print(f"  [{date}] kept local (today + yesterday stay serveable by the BYOD API — §3c)")
            elif not args.dry_run:
                print(f"  [{date}] kept local ({'--keep-local' if args.keep_local else 'deletes not authorized this run'})")

        # ---- dataset card ----
        if args.refresh_card or repo_created:
            if args.dry_run:
                print("[card] skipped — --dry-run makes no Hub calls")
            else:
                if not hub_touched:
                    repo_created = hf_ensure_repo(args.repo)
                    hub_touched = True
                card = stage_dir / "DATASET-CARD.md"
                stage_dir.mkdir(parents=True, exist_ok=True)
                card.write_text(build_dataset_card(args.repo))
                hf_upload_file(args.repo, card, "README.md", "dataset card")
                print(f"[card] dataset card {'created' if repo_created else 'refreshed'} on {args.repo}")

        # ---- summary + query-back ----
        if freed_total:
            print(f"[done] freed {_fmt_bytes(freed_total)} of local disk")
        if synced:
            newest = synced[-1]
            print("[done] the dataset is queryable in place:")
            print(
                f"      SELECT count(*) FROM read_parquet("
                f"'hf://datasets/{args.repo}/data/date={newest}/trades.parquet')"
            )
            print("      # streaming for ML:")
            print("      from datasets import load_dataset")
            print(f"      ds = load_dataset('{args.repo}', 'trades', streaming=True)")
        if delete_failures:
            # Honest partial state: every listed day is verified offsite but still
            # local — exit 1 so the scheduled job surfaces it, without implying
            # any data was lost (nothing ever deletes past a verified point).
            print(
                f"[done-with-errors] {len(delete_failures)} verified day(s) could not be "
                f"deleted locally ({', '.join(delete_failures)}) — re-run after fixing "
                "the local permission/disk issue",
                file=sys.stderr,
            )
            return EXIT_FAIL
        return EXIT_OK

    except Abort as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
