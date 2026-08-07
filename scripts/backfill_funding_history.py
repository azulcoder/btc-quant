"""backfill_funding_history.py — SETTLED funding history from each venue's public REST API.

`funding_mark` holds the PREDICTED rate for the upcoming settlement, sampled on every poll —
quoted in `docs/DIAG-funding-and-turnover-001.md` §1a. That is a different quantity from what
is actually paid. This pulls the SETTLED series and keeps it in a SEPARATE, APPEND-ONLY store
so the two can never be confused by a later reader.

Keyless: every endpoint here is public and unauthenticated, which is the repo's standing rail.

Look: PROVENANCE DIAGNOSTIC. No returns, no P&L, no estimator.
"""
from __future__ import annotations

import datetime as dt
import json, sys, time, urllib.parse, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STORE = REPO / "data" / "funding_history"
UA = {"User-Agent": "btc-quant/1.0 (research; keyless)"}
BPS = 1e-4

# All three paginate BACKWARD from now. The first attempt walked binancef FORWARD from
# startTime=0 and got the most recent 500 rows instead of the oldest — `startTime=0` does not
# mean "from the beginning" on that endpoint — and then stopped because the page came back
# short. Backward paging is uniform across the three and terminates on "no new rows".
VENUES = {
    "binancef": dict(
        url="https://fapi.binance.com/fapi/v1/fundingRate",
        params=lambda cur: {"symbol": "BTCUSDT", "endTime": cur, "limit": 1000},
        rows=lambda j: [(int(r["fundingTime"]), float(r["fundingRate"])) for r in j]),
    "bybit": dict(
        url="https://api.bybit.com/v5/market/funding/history",
        params=lambda cur: {"category": "linear", "symbol": "BTCUSDT",
                            "endTime": cur, "limit": 200},
        rows=lambda j: [(int(r["fundingRateTimestamp"]), float(r["fundingRate"]))
                        for r in j.get("result", {}).get("list", [])]),
    "okx": dict(
        url="https://www.okx.com/api/v5/public/funding-rate-history",
        params=lambda cur: {"instId": "BTC-USDT-SWAP", "after": cur, "limit": 100},
        rows=lambda j: [(int(r["fundingTime"]),
                         float(r["realizedRate"] or r["fundingRate"]))
                        for r in j.get("data", [])]),
}


def say(*a): print(*a, flush=True)


def get(url, params, tries=4):
    q = f"{url}?{urllib.parse.urlencode(params)}"
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(q, headers=UA), timeout=25) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001 — transient; a permanent failure is reported
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))
    return None


def pull(name: str, cfg: dict) -> list[tuple[int, float]]:
    """Walk backward until a page brings nothing new. Empty pages END the walk — the first
    version computed the next cursor from `rows` BEFORE checking it was non-empty, and both
    bybit and okx died on `min()`/`max()` of an empty sequence."""
    out: dict[int, float] = {}
    cur = int(time.time() * 1000)
    while True:
        j = get(cfg["url"], cfg["params"](cur))
        rows = cfg["rows"](j)
        if not rows:                       # guard BEFORE the cursor is derived from rows
            say(f"    {name}: empty page at cursor {cur} — walk ends")
            break
        new = {t: r for t, r in rows if t not in out}
        out.update(new)
        earliest = min(out)
        say(f"    {name}: +{len(new):>4} (total {len(out):>6,}) "
            f"earliest {dt.datetime.utcfromtimestamp(earliest / 1000).date()}")
        if not new:
            say(f"    {name}: no new settlements — walk ends")
            break
        cur = min(t for t, _ in rows) - 1
        time.sleep(0.35)
    return sorted(out.items())


def main() -> int:
    STORE.mkdir(parents=True, exist_ok=True)
    say("backfill_funding_history — SETTLED rates, public REST, keyless")
    say("  kept SEPARATE from funding_mark on purpose: that table holds the PREDICTED rate\n")
    summary = {}
    for name, cfg in VENUES.items():
        say(f"  {name} …")
        try:
            rows = pull(name, cfg)
        except Exception as e:  # noqa: BLE001 — one venue failing is reported, not fatal
            say(f"    {name}: FAILED — {type(e).__name__}: {str(e)[:110]}")
            summary[name] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            continue
        # append-only: never rewrite, only add settlements not already present
        f = STORE / f"{name}.jsonl"
        seen = set()
        if f.exists():
            for line in f.read_text().splitlines():
                if line.strip():
                    seen.add(json.loads(line)["ts_ms"])
        added = 0
        with f.open("a") as fh:
            for t, r in rows:
                if t in seen:
                    continue
                fh.write(json.dumps({"venue": name, "ts_ms": t, "rate": r}) + "\n")
                added += 1
        say(f"    {name}: {len(rows):,} settlements pulled, {added:,} appended -> {f.name}")
        summary[name] = {"pulled": len(rows), "appended": added, "file": str(f.name)}
    (REPO / "reports" / "funding-history-backfill.json").write_text(
        json.dumps(summary, indent=2, default=float) + "\n")
    say("\n  raw store -> data/funding_history/*.jsonl (append-only, never rewritten)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
