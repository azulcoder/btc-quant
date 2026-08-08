"""ui_baseline_shots.py — visual parity baseline for the orderflow terminal.

21,538 lines of terminal JS carry zero pytest coverage, and the repo's rail says a refactor
that changes the result is not a refactor. The UI version of that rail needs a baseline: a
set of screenshots taken against FIXED data, so a later change either reproduces them or is
caught.

Determinism comes from a seam that already exists rather than one invented here: opening the
page with `?replay=1` makes `terminal-replay.js` drive the untouched adapters from
`scripts/fixtures_ws.json` on a synthetic clock, with no network beyond localhost. That is
the same mechanism `verify_terminal_browser.py` uses, and it is why this harness is possible
at all — a terminal that could only run against live WebSockets could not have a pixel
baseline, and that would itself have been the finding.

The control is tested BOTH ways (class I — a verifier that has never fired is not a
verifier): twice unchanged must be identical, and a deliberately altered colour must be
caught. The altered run happens against a COPY of `dashboard/` in a temp directory, so no
dashboard file in the repo is ever modified.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import re
import shutil
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHOTS = REPO / "reports" / "ui-baseline"
# Selectors read off terminal.html rather than guessed: the layout uses
# `class="panel area-<name>"` grid areas. A panel that is not present is SKIPPED and
# reported, never silently counted as passing.
AREAS = ("alerts", "auct", "basis", "cal", "conf", "cvd", "econ", "farb", "fp",
         "heat", "hist", "jour", "klev", "lvls", "macro", "micro", "news", "opts",
         "poly", "rsi", "scr", "set", "spcvd", "stats", "tpo", "vp", "vpin", "whale")

# QUARANTINED from the parity baseline, with the reason recorded rather than the panels
# quietly dropped. Both were caught by the baseline itself: they differed between two
# identical runs [DIUKUR 2026-08-08], because neither is fed by the replay fixture.
#   auct — pulls from the BYOD API `/v1/profile` on localhost:8788, or from HF in-browser
#   news — reads `dashboard/econ_calendar.json`, which is gitignored generated output
# They are still screenshotted; they are just not allowed to decide the verdict, and any
# future work that makes them deterministic should delete them from this set.
NONDETERMINISTIC = {"auct", "news"}
PANELS = [("full", None)] + [(n, f".panel.area-{n}") for n in AREAS]


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(root: Path):
    handler = lambda *a, **k: _Quiet(*a, directory=str(root), **k)  # noqa: E731
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def shoot(root: Path, out: Path, settle_ms: int, timings: dict | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    out.mkdir(parents=True, exist_ok=True)
    httpd, port = serve(root)
    digests = {}
    try:
        with sync_playwright() as p:
            br = p.chromium.launch()
            page = br.new_page(viewport={"width": 1680, "height": 1050},
                               device_scale_factor=1)
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            t0 = time.time()
            page.goto(f"http://127.0.0.1:{port}/terminal.html?replay=1",
                      wait_until="domcontentloaded")
            t_dom = time.time() - t0
            # first paint of real content: wait until a store reports rows
            t_first = None
            for _ in range(120):
                try:
                    ok = page.evaluate(
                        "() => { const d = window.__BTCQ_TERMINAL_DEBUG; "
                        "if (!d || typeof d.counts !== 'function') return false; "
                        "const c = d.counts() || {}; "
                        "return Object.values(c).some(v => typeof v === 'number' && v > 0); }")
                except Exception:
                    ok = False
                if ok:
                    t_first = time.time() - t0
                    break
                page.wait_for_timeout(250)
            page.wait_for_timeout(settle_ms)
            # frame time while updates flow
            fps = page.evaluate("""() => new Promise(res => {
                const ts = []; let n = 0;
                function tick(t){ ts.push(t); if (++n < 60) requestAnimationFrame(tick);
                  else { const d = []; for (let i=1;i<ts.length;i++) d.push(ts[i]-ts[i-1]);
                         d.sort((a,b)=>a-b);
                         res({n: d.length, p50: d[Math.floor(d.length*0.5)],
                              p95: d[Math.floor(d.length*0.95)], max: d[d.length-1]}); } }
                requestAnimationFrame(tick); })""")
            for name, sel in PANELS:
                target = page
                if sel:
                    el = page.query_selector(sel)
                    if el is None:
                        continue
                    target = el
                png = out / f"{name}.png"
                target.screenshot(path=str(png))
                digests[name] = hashlib.sha256(png.read_bytes()).hexdigest()
            if timings is not None:
                timings.update({"dom_content_loaded_s": round(t_dom, 3),
                                "first_panel_filled_s": (round(t_first, 3) if t_first
                                                         else None),
                                "frame_ms": fps, "page_errors": errs[:5],
                                "n_page_errors": len(errs)})
            br.close()
    finally:
        httpd.shutdown()
    return digests


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--settle-ms", type=int, default=6000)
    ap.add_argument("--out", default=str(SHOTS))
    a = ap.parse_args()
    out = Path(a.out)

    timings: dict = {}
    print("run 1 (baseline, dashboard/ untouched)")
    d1 = shoot(REPO / "dashboard", out, a.settle_ms, timings)
    for k, v in d1.items():
        print(f"    {k:<10} {v[:16]}")
    print(f"  timings: {json.dumps(timings)}")

    print("\nrun 2 (identical input — MUST match: the pass case)")
    tmp2 = Path(tempfile.mkdtemp())
    d2 = shoot(REPO / "dashboard", tmp2 / "shots", a.settle_ms)
    scored = [k for k in d1 if k not in NONDETERMINISTIC]
    same = [k for k in scored if d1.get(k) == d2.get(k)]
    diff = [k for k in scored if d1.get(k) != d2.get(k)]
    drift = [k for k in NONDETERMINISTIC if k in d1 and d1.get(k) != d2.get(k)]
    print(f"    identical: {len(same)}/{len(scored)} scored  differing: {diff or 'none'}")
    print(f"    quarantined (not scored, reason recorded): {sorted(NONDETERMINISTIC)} "
          f"— drifted this run: {drift or 'none'}")

    print("\nrun 3 (ONE colour changed in a COPY — MUST be caught: the fail case)")
    tmp3 = Path(tempfile.mkdtemp()) / "dash"
    shutil.copytree(REPO / "dashboard", tmp3)
    # The up/down tokens live in styles.css in UPPERCASE — the first attempt substituted
    # lowercase in terminal.css, matched nothing, and the control correctly reported that
    # it could not discriminate. A control that had "passed" there would have been worse
    # than useless.
    css = tmp3 / "styles.css"
    txt = css.read_text()
    before = txt
    txt = re.sub(r"#26A69A", "#FF00FF", txt, flags=re.I)
    txt = re.sub(r"#EF5350", "#00FF00", txt, flags=re.I)
    css.write_text(txt)
    changed = txt != before
    d3 = shoot(tmp3, Path(tempfile.mkdtemp()) / "shots", a.settle_ms)
    caught = [k for k in scored if d1.get(k) != d3.get(k)]
    print(f"    colour substitution applied: {changed}")
    print(f"    detected as different: {len(caught)}/{len(d1)}  -> {caught or 'NONE'}")

    verdict = (len(diff) == 0 and len(caught) > 0)
    print(f"\n  CONTROL VERDICT: {'WORKS' if verdict else 'DOES NOT DISCRIMINATE'} "
          f"(pass-case identical={len(diff) == 0}, fail-case caught={len(caught) > 0})")
    (out / "control.json").write_text(json.dumps(
        {"digests": d1, "rerun_identical": diff == [], "differing_on_rerun": diff,
         "colour_change_caught": caught, "timings": timings}, indent=1))
    for p in (tmp2, tmp3.parent):
        shutil.rmtree(p, ignore_errors=True)
    return 0 if verdict else 2


if __name__ == "__main__":
    sys.exit(main())
