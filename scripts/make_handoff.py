"""make_handoff.py — generate `docs/HANDOFF.md`, the one file a web session needs.

A session without filesystem access cannot run `/health`, read a day file, or see the disk.
Everything it is allowed to assume must therefore be IN the handoff, and everything it must
NOT assume has to be named explicitly — otherwise it will assume anyway.

**Every field is GENERATED from a source.** Nothing is typed. If a source cannot be read the
field says `UNKNOWN` — never a guess, and never a stale value carried forward. That rule is
the whole point: hand-copied state is the mechanism that produced six rot incidents here.

`make gate` regenerates this file as its last step, so a green gate implies a fresh handoff.
That is also why `scripts/doc_freshness.py` exempts this file from A2/A3: it is machine-written
from the owners rather than a second hand-maintained copy. It is NOT exempt from A1 — a
generated file has no excuse for emitting a line-number pointer.

Read-only except for the one file it writes.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "HANDOFF.md"
UNKNOWN = "UNKNOWN"


def sh(*args: str) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, cwd=str(REPO), timeout=20)
        return r.stdout.strip() if r.returncode == 0 else UNKNOWN
    except Exception:  # noqa: BLE001 — an unreadable source is UNKNOWN, never a guess
        return UNKNOWN


def git_state() -> dict:
    sha = sh("git", "rev-parse", "--short", "HEAD")
    subj = sh("git", "log", "-1", "--format=%s")
    when = sh("git", "log", "-1", "--format=%cI")
    dirty = sh("git", "status", "--porcelain")
    ahead = sh("git", "rev-list", "--count", "origin/main..HEAD")
    return {"sha": sha, "subject": subj[:100] if subj != UNKNOWN else UNKNOWN, "when": when,
            "dirty": UNKNOWN if dirty == UNKNOWN else ("dirty" if dirty else "clean"),
            "unpushed": ahead}


def counter() -> str:
    """Read the look counter from its OWNER. Never re-state it from memory."""
    f = REPO / "docs" / "EDA-microstructure-001.md"
    try:
        rows = re.findall(r"\|\s*\*\*running total\*\*\s*\|\s*\*\*([\d,]+)\*\*\s*\|\s*\*\*([\d,]+)\*\*\s*\|",
                          f.read_text(errors="replace"))
        return f"{rows[-1][0]} diagnostic / {rows[-1][1]} predictive" if rows else UNKNOWN
    except Exception:  # noqa: BLE001
        return UNKNOWN


def migration() -> dict:
    f = REPO / "reports" / "vision-migration.jsonl"
    out = {"states": UNKNOWN, "last": UNKNOWN}
    try:
        counts: dict[str, int] = {}
        last = None
        for line in f.read_text(errors="replace").splitlines():
            try:
                d = json.loads(line)
            except Exception:  # noqa: BLE001 — a torn final line after a crash is expected
                continue
            if "state" in d:
                counts[d["state"]] = counts.get(d["state"], 0) + 1
                last = d
        out["states"] = counts or UNKNOWN
        out["last"] = f"{last.get('date')} -> {last.get('state')} @ {last.get('ts')}" if last else UNKNOWN
    except Exception:  # noqa: BLE001
        pass
    return out


def local_partitions() -> str:
    d = REPO / "data" / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
    try:
        return f"{sum(1 for p in d.glob('date=*') if (p / 'trades.parquet').exists()):,}"
    except Exception:  # noqa: BLE001
        return UNKNOWN


def disk_free() -> str:
    try:
        import shutil
        return f"{shutil.disk_usage(REPO).free / 1e9:.1f} GB"
    except Exception:  # noqa: BLE001
        return UNKNOWN


def collector() -> str:
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8788/health", timeout=5) as r:
            d = json.loads(r.read())
        w = d.get("writer", {})
        return (f"legs {d.get('legs_ok')}/{d.get('legs_total')} · writer {w.get('state')} · "
                f"rows_dropped_error {w.get('rows_dropped_error')} · "
                f"uptime {d.get('uptime_s', 0) / 3600:.1f} h")
    except Exception:  # noqa: BLE001 — not running, or not this machine
        return UNKNOWN


def last_gate() -> str:
    f = REPO / "reports" / "gate-last.json"
    try:
        d = json.loads(f.read_text())
        same = " (this commit)" if d.get("sha") == git_state()["sha"] else " (an EARLIER commit)"
        return f"GREEN at {d.get('utc')} on {d.get('sha')}{same} · {d.get('suite', UNKNOWN)}"
    except Exception:  # noqa: BLE001
        return f"{UNKNOWN} — no green gate has been recorded"


def damage() -> str:
    try:
        d = json.loads((REPO / "reports" / "recorded-damage.json").read_text())
        return " · ".join(f"{e['date']} {e['missing_rows']:,}" for e in d["entries"]) or "none"
    except Exception:  # noqa: BLE001
        return UNKNOWN


def open_decisions() -> list[str]:
    """Lifted from STATUS's own numbered list — one owner, generated here."""
    f = REPO / "docs" / "STATUS.md"
    try:
        txt = f.read_text(errors="replace")
        sec = txt.split("## 5. Open decisions")[1].split("\n## ")[0]
        items = re.findall(r"^\s*\d+\.\s+(.+?)(?=\n\s*\d+\.\s|\Z)", sec, re.S | re.M)
        return [" ".join(i.split())[:260] for i in items]
    except Exception:  # noqa: BLE001
        return [UNKNOWN]


def main() -> int:
    g = git_state()
    mig = migration()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dec = open_decisions()

    L: list[str] = []
    A = L.append
    A("# HANDOFF — generated, do not hand-edit")
    A("")
    A(f"**Generated {now} by `make handoff`.** Every field below is read from a source; an")
    A("unreadable source says `UNKNOWN` rather than carrying a stale value forward. Hand-editing")
    A("this file defeats its purpose — regenerate it instead (`make gate` does so automatically).")
    A("")
    A("## Commit")
    A("")
    A(f"- `{g['sha']}` — {g['subject']}")
    A(f"- authored {g['when']} · working tree **{g['dirty']}** · unpushed commits: {g['unpushed']}")
    A(f"- public repo: <https://github.com/azulcoder/btc-quant> — a web session can fetch it directly")
    A("")
    A("## State [all values generated this run]")
    A("")
    A("| field | value |")
    A("|---|---|")
    A(f"| collector `/health` | {collector()} |")
    A(f"| last GREEN gate | {last_gate()} |")
    A(f"| look counter (owner: `docs/EDA-microstructure-001.md`) | {counter()} |")
    A(f"| vision partitions still local | {local_partitions()} |")
    A(f"| migration states | {mig['states']} |")
    A(f"| migration last record | {mig['last']} |")
    A(f"| disk free | {disk_free()} |")
    A(f"| recorded damage (date, prints) | {damage()} |")
    A("")
    A("**Who may raise the look counter:** only a session that actually ran the look, in the same")
    A("commit that records it. A variant proposed in a web session and later scored here is a look")
    A("that must be counted — if it reaches the repo without a counter increase, the counter is")
    A("wrong and every DSR deflated against it inherits the error.")
    A("")
    A("## Open decisions (generated from `docs/STATUS.md`)")
    A("")
    for i, d in enumerate(dec, 1):
        A(f"{i}. {d}")
    A("")
    A("## Binding rules for ANY agent, in any surface")
    A("")
    A("- **LockBox `2026-08-05 01:00 UTC` onward is never read, queried, or peeked.** It is an")
    A("  evaluate-once slice; a single look destroys its only property. The boundary was moved")
    A("  forward once (`00:00 → 01:00`) for a documented data defect, before any byte was read.")
    A("- **Exploration slice is FROZEN** at `2026-07-05 … 2026-08-03`. Newly collected data goes")
    A("  to the LockBox, so no 30-day table can grow — `N` will not increase by waiting.")
    A("- **Declare before running.** Thresholds, criteria and the interpretation of every outcome")
    A("  are written down before the number exists, in the doc that will hold the result.")
    A("- **A new instrument's first number is a CONTROL**, not a result — it must reproduce a known")
    A("  value by an independent route. Anchors pin to CLOSED data (a live partial bar broke one).")
    A("- **A verifier is tested on cases known to PASS**, not only on known failures.")
    A("- **Conclusions print BESIDE the numbers that produced them.**")
    A("- **Cite grep-able strings, never `file:line`** — enforced by `scripts/doc_freshness.py`.")
    A("- **Label every number**: `[DIUKUR]` measured · `[DISIMPULKAN]` inferred · `[DIASUMSIKAN]`")
    A("  assumed · `[UNVERIFIED]` claimed but unchecked.")
    A("- **No AI attribution** in commits, PRs, code comments, or prose.")
    A("- **Gaps stay gaps** — no backfill, smoothing, or interpolation into a recorded series.")
    A("")
    A("## What a session WITHOUT this machine must not assume")
    A("")
    A("These exist only on the collector host and cannot be inferred from the repo:")
    A("")
    A("- the live collector and its `/health` (leg states, dropped-row counters, uptime)")
    A("- the two local day files (today + yesterday); everything older lives on Hugging Face")
    A("- the stamped collector log (`~/Library/Logs/`), which is the forensic record")
    A("- free disk, and anything about the migration's live progress")
    A("- whether a background job is still running")
    A("")
    A("If a decision depends on any of those, it cannot be settled off-machine — ask for a")
    A("measurement, do not estimate one. **Anything decided elsewhere must return as a commit,")
    A("or it is not durable.**")
    A("")
    A("## Where the records live (no line numbers by design)")
    A("")
    A("| document | holds |")
    A("|---|---|")
    A("| `docs/STATUS.md` | the current-state index — start here |")
    A("| `docs/EDA-microstructure-001.md` | the measurement record and the look counter (its owner) |")
    A("| `docs/EDA-execution-001.md` | maker viability gate for the execution-overlay track |")
    A("| `docs/PLAN-derivative-001.md` | derivative candidates + the cost-drag gate procedure |")
    A("| `docs/PRECHECK-cvd-turnover.md` | the CVD turnover precheck and its anchor correction |")
    A("| `docs/PREREG-pbo-null-001.md` | the PBO replacement: declared, run, verdict inside |")
    A("| `docs/PREREG-scalp-001.md` | scalping pre-registration; the premise is rejected arithmetically |")
    A("| `docs/DESIGN-vision-remote-first.md` | the archive migration design and its measurements |")
    A("| `STRATEGY.md` | the refusals, the blindness ledger, and the taxonomy |")
    A("| `CLAUDE.md` | working rules loaded every session |")
    A("| `reports/*.json[l]` | machine-checked damage, LockBox defects, migration checkpoint |")
    A("")
    OUT.write_text("\n".join(L) + "\n")
    print(f"handoff -> {OUT.relative_to(REPO)} ({len(L)} lines, generated {now})")
    unknowns = sum(1 for line in L if UNKNOWN in line)
    print(f"  fields reported UNKNOWN: {unknowns}"
          + ("  (each one is a source this machine could not read)" if unknowns else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
