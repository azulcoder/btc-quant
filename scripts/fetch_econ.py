#!/usr/bin/env python3
"""fetch_econ.py — local econ-calendar mirror for the terminal's EconView
(DESIGN-orderflow-terminal.md §4e).

WHY THIS FILE EXISTS (§4e empirical map, probed 2026-07-05): the faireconomy
ForexFactory-mirror JSON serves NO CORS header, so the browser CANNOT fetch it
directly — unlike Polymarket (CORS *) and Tree of Alpha (CORS *), which the
page polls itself. The design is therefore a local same-origin mirror: this
stdlib-only script pulls `thisweek` + `nextweek` (thisweek can lag around week
boundaries — fixtures `_o5_notes` — so both are fetched and the panel filters
by date client-side), merges + sorts them, stamps `fetchedTs`, and writes
`dashboard/econ_calendar.json` (gitignored). terminal-hist.js
`fetchEconLocal()` reads that file; the EconView shows its fetch age and a
"run `make econ`" note when it is absent/stale.

Honesty rails: events are passed through AS-IS (title/country/date/impact/
forecast/previous — the exact keys the browser normalizer and the fixture
`ff_econ_sample` pin); nothing is renamed, coerced, or filled in here. The
one added field is `fetchedTs` (epoch ms) — the staleness stamp the panel is
REQUIRED to show (§4e: a calendar of unknown age is a stale-data trap).

Run:  python3 scripts/fetch_econ.py        (or: make econ)
Exit: 0 on success (idempotent — safe to re-run any time);
      1 on network/parse failure with a clear message and the previous file
      left untouched (a stale-but-stamped mirror beats a destroyed one).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "..", "dashboard", "econ_calendar.json")

# Both weeks when available (§4e/_o5_notes: thisweek.json can lag around the
# week boundary — fetching next week too keeps the upcoming-events panel
# populated; the browser filters by date, not by which file a row came from).
# EMPIRICAL (probed 2026-07-05, this machine): `nextweek` 404s while
# `thisweek` serves 200 — the mirror seems to publish it only around week
# boundaries (if ever). So thisweek is REQUIRED (its failure exits 1) and
# nextweek is BEST-EFFORT: its absence is reported, never papered over and
# never fatal (§0.7 — say what's missing rather than fail what exists).
URL_THISWEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
URL_NEXTWEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

# Some CDN fronts 403 the default urllib UA; a plain descriptive UA is honest
# and sufficient (keyless, no auth — §0.2).
UA = "btc-quant-econ-mirror/1.0 (local research terminal; stdlib urllib)"


def fetch_week(url: str) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        rows = json.load(resp)
    if not isinstance(rows, list):
        raise ValueError(f"{url}: expected a JSON array, got {type(rows).__name__}")
    return rows


def main() -> int:
    try:
        events = fetch_week(URL_THISWEEK)
    except (urllib.error.URLError, ValueError, json.JSONDecodeError) as e:
        # Nonzero + clear message + previous file untouched: the panel keeps
        # showing the OLD mirror with its honest fetch-age stamp.
        print(f"fetch_econ: FAILED on {URL_THISWEEK}: {e}", file=sys.stderr)
        print("fetch_econ: dashboard/econ_calendar.json NOT rewritten "
              "(previous mirror, if any, left as-is).", file=sys.stderr)
        return 1
    try:
        events.extend(fetch_week(URL_NEXTWEEK))
    except (urllib.error.URLError, ValueError, json.JSONDecodeError) as e:
        # Best-effort leg (see URL note above): reported, not fatal.
        print(f"fetch_econ: note — nextweek unavailable ({e}); "
              "mirror carries thisweek only.", file=sys.stderr)

    # Sort by the source's own ISO date strings converted lexically-safely via
    # the browser-identical criterion? No — keep it simple and honest: the
    # rows carry ISO-8601 dates WITH offsets ('2026-06-28T08:15:00-04:00');
    # the browser normalizer (normalizeEconLocal) Date.parse()s and re-sorts
    # anyway, so the sort here is a readability courtesy for anyone opening
    # the JSON, keyed on the raw string (same-offset feeds sort correctly).
    events.sort(key=lambda r: str(r.get("date", "")))

    # De-dup across the two week files (the boundary week can appear in both):
    # identity = (date, title, country) — the natural key of a calendar row.
    seen = set()
    unique = []
    for r in events:
        key = (str(r.get("date", "")), str(r.get("title", "")), str(r.get("country", "")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    payload = {
        # Epoch ms — the fetch-age stamp EconView must display (§4e).
        "fetchedTs": int(time.time() * 1000),
        "events": unique,
    }
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT_PATH)  # atomic: the browser never reads a half-written file
    print(f"fetch_econ: wrote {len(unique)} events "
          f"({len(events) - len(unique)} boundary duplicates dropped) -> "
          f"{os.path.relpath(OUT_PATH, os.path.join(HERE, '..'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
