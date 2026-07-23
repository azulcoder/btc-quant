#!/usr/bin/env python3
"""verify_terminal_browser.py — browser-level verification of the orderflow
terminal in deterministic fixture-replay mode (DESIGN-orderflow-terminal.md §4;
L1 of the terminal verification system).

What it proves that the Node fixture smoke (``check_terminal.cjs``) cannot: the
REAL page — script load order, adapter→store→view wiring, canvas painting —
boots in a REAL Chromium and renders REAL captured frames end-to-end. The page
is opened with ``?replay=1`` so terminal-replay.js drives the untouched
adapters from ``scripts/fixtures_ws.json`` on a synthetic clock: deterministic,
zero network beyond localhost (the REST poller is skipped in replay), and
honestly labeled — the chips say 'replay' and the banner carries a visible
REPLAY MODE flag, which this harness asserts (§0 honesty rails).

Checks (all must pass, no allowlist):
  a. ZERO console errors and ZERO uncaught page errors.
  b. All 7 venue-matrix legs (bybit lin/spot, binance fut/spot, okx swap/spot,
     coinbase) report 'open' via the read-only ``window.__BTCQ_TERMINAL_DEBUG``
     hook (status msg is 'replay') — the T-2 (§4h) matrix.
  c. Every visible canvas STACK is non-blank: >2% of a 40×40 sample grid
     differs from the page background color (stack = canvases sharing one
     screen rect, composited — see CANVAS_JS note on lightweight-charts'
     blank-until-hover crosshair overlays).
  d. Store counts are sane: tapeRows>0, ladderRows>0, footprintBars>=1,
     aggLevels>0, cvdPoints>0, heatSamples>=3.
  e. The REPLAY MODE honesty flag is present in the permanent banner.

Artifacts: full-page + per-panel screenshots (footprint, dom, tape, aggbook,
bookheat, liqheat, detections, cvd, header), taken TWICE — at ready (t0) and
+8s later (t1) — so session accumulation is visible side by side.

Run:  python3 scripts/verify_terminal_browser.py [--out reports/verify]
      [--keep-server]  (serves the page after the checks for manual poking)
Exit: 0 all green; 1 with a clear FAIL list. Requires playwright + chromium
(``pip install playwright && playwright install chromium``).
"""
from __future__ import annotations

import argparse
import functools
import http.server
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # repo root — MUST be the doc root so the page's
# fetch('../scripts/fixtures_ws.json') resolves from /dashboard/terminal.html.

# Panel name → DOM anchor (ids from dashboard/terminal.html). 'detections'
# maps to #view-detect — the html id is shorter than the panel's spoken name.
PANELS = {
    "footprint": "#view-footprint",
    "dom": "#view-dom",
    "tape": "#view-tape",
    "aggbook": "#view-aggbook",
    "bookheat": "#view-bookheat",
    "liqheat": "#view-liqheat",
    "detections": "#view-detect",
    "cvd": "#view-cvd",
    "header": "#view-header",
    # T-1 (§4g) Trader's Edge panels. tapeint/basis carry replay data (trades /
    # tickers frames); walls/vpin/keylevels legitimately render their honest
    # empty notes in a short replay — the screenshot proves the note, not data.
    "tapeint": "#view-tapeint",
    "walls": "#view-walls",
    "vpin": "#view-vpin",
    "keylevels": "#view-keylevels",
    "basis": "#view-basis",
    # T-2 (§4h) Venue Matrix: the spot-vs-perp CVD strip. Replay drives perp
    # (bybit·lin + okx·swap) and spot (coinbase + the new spot legs), so the
    # strip carries real lines — assert-with-data below, not a bare screenshot.
    "spotperp": "#view-spotperp",
}

READY_TIMEOUT_MS = 45_000  # replay deals ~4 frames/s; heatSamples>=3 needs ~10s

# Ready = the pipeline demonstrably flowed: trades reached the tape AND the
# event-ts-gated depth sampler produced >=3 heatmap columns (the slowest count).
READY_JS = (
    "() => { const d = window.__BTCQ_TERMINAL_DEBUG; if (!d) return false;"
    " const c = d.counts(); return c.tapeRows > 0 && c.heatSamples >= 3; }"
)

# Canvas non-blank probe (check c): sample a 40x40 grid and count pixels that
# differ from the PAGE background (read from computed style — the theme is
# dark, hardcoding a color would rot). Transparent pixels (alpha<16) show the
# page bg through → counted as bg.
#
# Canvases are judged as STACKS, not individually: lightweight-charts (the
# vendored CVD chart) renders every pane as TWO overlapping canvases — a
# painted base layer plus a crosshair/interaction overlay that is EMPTY until
# the pointer hovers. Judging each canvas alone would fail those by-design
# blank overlays; what matters visually is the composite at each screen rect,
# so canvases sharing a bounding rect are sampled together (non-bg if ANY
# opaque layer differs). Stacks under 4000 css-px² are reported but not
# judged. Two elements land there, both legitimately blank at times:
# lightweight-charts' ~80x28 corner stub between the price and time axes
# (never painted by the library), and the T-1 tape-intensity sparkline
# (120x16), which is empty until the first COMPLETED 10 s bucket — a short
# replay may honestly have none at judge time. The t0/t1 panel screenshots
# stay the visual witness for these small canvases.
CANVAS_JS = """
() => {
  const m = getComputedStyle(document.body).backgroundColor
    .match(/rgba?\\(([\\d.]+),\\s*([\\d.]+),\\s*([\\d.]+)/);
  const bg = m ? [+m[1], +m[2], +m[3]] : [0, 0, 0];

  // Group visible canvases by host panel id + css bounding rect (a "stack").
  const groups = new Map();
  document.querySelectorAll('canvas').forEach((cv) => {
    const r = cv.getBoundingClientRect();
    if (r.width < 10 || r.height < 10 || cv.width < 1 || cv.height < 1) return; // hidden → not judged
    const host = cv.closest('[id]');
    const id = cv.id || (host ? host.id : 'anon');
    const key = id + '@' + Math.round(r.left) + ',' + Math.round(r.top)
              + ',' + Math.round(r.width) + 'x' + Math.round(r.height);
    if (!groups.has(key)) {
      groups.set(key, { id, w: Math.round(r.width), h: Math.round(r.height), cvs: [] });
    }
    groups.get(key).cvs.push(cv);
  });

  const out = [];
  for (const g of groups.values()) {
    const base = { id: g.id, w: g.w, h: g.h, layers: g.cvs.length };
    if (g.w * g.h < 4000) { out.push(Object.assign(base, { skipped: true, nonBgPct: null })); continue; }
    const datas = [];
    for (const cv of g.cvs) {
      let ctx = null;
      try { ctx = cv.getContext('2d'); } catch (e) { /* non-2d canvas */ }
      if (ctx) datas.push({ img: ctx.getImageData(0, 0, cv.width, cv.height).data, w: cv.width, h: cv.height });
    }
    if (!datas.length) { out.push(Object.assign(base, { nonBgPct: null, note: 'no 2d context' })); continue; }
    let non = 0, total = 0;
    for (let gy = 0; gy < 40; gy++) {
      for (let gx = 0; gx < 40; gx++) {
        total++;
        for (const d of datas) {   // composite: any opaque non-bg layer counts
          const x = Math.min(d.w - 1, Math.floor((gx + 0.5) * d.w / 40));
          const y = Math.min(d.h - 1, Math.floor((gy + 0.5) * d.h / 40));
          const k = (y * d.w + x) * 4;
          if (d.img[k + 3] < 16) continue;                     // transparent → bg
          const diff = Math.abs(d.img[k] - bg[0]) + Math.abs(d.img[k + 1] - bg[1])
                     + Math.abs(d.img[k + 2] - bg[2]);
          if (diff > 30) { non++; break; }                     // clearly not the page bg
        }
      }
    }
    out.push(Object.assign(base, { nonBgPct: 100 * non / total }));
  }
  return out;
}
"""


def _require_playwright():
    """Import guard with an actionable message — the repo's other checks must
    keep running on machines without browser tooling (same pattern as the
    Node-optional parity check)."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return sync_playwright
    except ImportError as e:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "playwright is required for the browser harness — install with: "
            "pip install playwright && playwright install chromium"
        ) from e


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Serve the repo root without spamming the harness output with GET lines."""

    def log_message(self, fmt, *args):  # noqa: D401 - stdlib override
        pass


def start_server():
    """Serve ROOT on a free localhost port (port 0 = kernel-assigned, no race)
    in a daemon thread. Localhost only — the harness never talks to the net."""
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_QuietHandler, directory=ROOT)
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def shoot_panels(page, out_dir, tag):
    """Full page + one element screenshot per panel, suffixed -<tag>. A panel
    that cannot be shot (missing/zero-size) is reported as a failure — every
    anchor in PANELS is a real element in terminal.html."""
    fails = []
    paths = []
    full = os.path.join(out_dir, f"full-{tag}.png")
    page.screenshot(path=full, full_page=True)
    paths.append(full)
    for name, sel in PANELS.items():
        path = os.path.join(out_dir, f"panel-{name}-{tag}.png")
        try:
            page.locator(sel).screenshot(path=path, timeout=10_000)
            paths.append(path)
        except Exception as e:  # zero-size / detached element = a real page bug
            fails.append(f"screenshot {name} ({sel}) at {tag}: {type(e).__name__}: {e}")
    return paths, fails


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "reports", "verify"),
                    help="screenshot directory (default: reports/verify/)")
    ap.add_argument("--keep-server", action="store_true",
                    help="keep serving after the checks for interactive debugging (Ctrl+C to stop)")
    args = ap.parse_args()

    sync_playwright = _require_playwright()
    os.makedirs(args.out, exist_ok=True)

    server, port = start_server()
    url = f"http://127.0.0.1:{port}/dashboard/terminal.html?replay=1"
    print(f"serving {ROOT} at http://127.0.0.1:{port}/ (localhost only)")
    print(f"opening {url}")

    fails: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    shots: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        # Tall viewport: the terminal grid stacks panels; every PANELS anchor
        # must be laid out (element screenshots need a rendered box).
        page = browser.new_page(viewport={"width": 1680, "height": 1400})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        page.goto(url, wait_until="domcontentloaded")

        # ── ready gate: data flowed through adapters → stores → sampler ──
        try:
            page.wait_for_function(READY_JS, timeout=READY_TIMEOUT_MS)
            print("PASS ready: tapeRows > 0 and heatSamples >= 3 within "
                  f"{READY_TIMEOUT_MS // 1000}s")
        except Exception:
            dbg = page.evaluate(
                "() => window.__BTCQ_TERMINAL_DEBUG"
                " ? { chips: __BTCQ_TERMINAL_DEBUG.chips(), counts: __BTCQ_TERMINAL_DEBUG.counts() }"
                " : null")
            fails.append(f"ready: page never reached tapeRows>0 && heatSamples>=3 "
                         f"within {READY_TIMEOUT_MS}ms (debug state: {dbg})")

        # t0 screenshots at ready — taken even on a failed ready gate: a frozen
        # page's pixels are exactly the evidence a debugging session needs.
        p, f = shoot_panels(page, args.out, "t0")
        shots += p
        fails += f

        # ── (b) chips: all 7 matrix legs 'open' (replay-labeled, never 'live').
        # T-2 (§4h): the venue matrix drives all seven venue×market legs in
        # replay (the new spot legs replay their own 2026-07-23 captures), so
        # every chip must report open — the four T-1 legs keep their frozen ex
        # codes, the three new legs' ex codes ARE their leg keys. ──
        MATRIX_EX = ("bybit", "bybit_spot", "binancef", "binance_spot",
                     "okx", "okx_spot", "coinbase")
        chips = page.evaluate("() => window.__BTCQ_TERMINAL_DEBUG ? __BTCQ_TERMINAL_DEBUG.chips() : null")
        if not chips:
            fails.append("chips: __BTCQ_TERMINAL_DEBUG missing — terminal.js debug hook not installed")
        else:
            for ex in MATRIX_EX:
                kind = chips.get(ex)
                if kind != "open":
                    fails.append(f"chip {ex}: expected 'open' (replay), got {kind!r}")
            if all(chips.get(ex) == "open" for ex in MATRIX_EX):
                print(f"PASS chips: all 7 matrix legs open — {chips}")

        # ── (d) store counts sane ──
        counts = page.evaluate("() => window.__BTCQ_TERMINAL_DEBUG ? __BTCQ_TERMINAL_DEBUG.counts() : null")
        if not counts:
            fails.append("counts: __BTCQ_TERMINAL_DEBUG missing")
        else:
            # T-1 (§4g): basisPoints ≥ 1 — the fixture tickers frames carry
            # mark+index, so BasisSeries must have accrued. walls/vpin/tapeint
            # buckets are NOT asserted: a short replay honestly leaves them 0.
            #
            # T-2 (§4h): the matrix must be alive, not merely booted —
            #   enabledLegs == 7   (all legs default-enabled, none dropped);
            #   aggLegs >= 3       (multiple venue books reach the merged agg
            #                       book — the leg-aware panel's whole point);
            #   spotPerpLive == 1  (the spot-vs-perp strip has a live cumulative
            #                       read: perp = bybit·lin+okx·swap, spot =
            #                       coinbase + the new spot legs both flowed).
            mins = {"tapeRows": 1, "ladderRows": 1, "cvdPoints": 1,
                    "heatSamples": 3, "aggLevels": 1, "footprintBars": 1,
                    "basisPoints": 1, "enabledLegs": 7, "aggLegs": 3,
                    "spotPerpLive": 1}
            bad = [k for k, lo in mins.items() if not (counts.get(k, 0) >= lo)]
            if bad:
                fails.append(f"counts below minimum {bad}: {counts}")
            else:
                print(f"PASS counts: {counts}")

        # ── (d2) T-2 (§4h) matrix surfaces render their DOM, not just stores ──
        # The leg manager lists all 7 legs (seeded at init even while the
        # popover is hidden); the agg-book depth-quality strip composes one
        # cell per leg; the spot-vs-perp strip shows its live composition line.
        leg_rows = page.locator("#legs-list .leg-row").count()
        if leg_rows != 7:
            fails.append(f"leg manager: expected 7 leg rows in #legs-list, got {leg_rows}")
        else:
            print(f"PASS leg manager: 7 leg rows rendered")
        q_legs = page.locator("#agg-quality .q-leg").count()
        if q_legs != 7:
            fails.append(f"agg-book depth-quality strip: expected 7 leg cells, got {q_legs}")
        else:
            print(f"PASS agg-book quality strip: 7 per-leg cells")
        # Spot-vs-perp composition line names the enabled trade legs live; the
        # panel must NOT be in the honest-empty state (replay drove both sides).
        comp = page.text_content(".spcvd-comp") or ""
        empty_spcvd = page.locator(".area-spcvd.panel--empty").count()
        if "perp" in comp and "spot" in comp and empty_spcvd == 0:
            print("PASS spot-vs-perp: live composition rendered, panel has data")
        else:
            fails.append(f"spot-vs-perp: composition/empty-state wrong "
                         f"(comp={comp[:80]!r}, empty_panels={empty_spcvd})")

        # ── (e) REPLAY honesty flag in the permanent banner (§0 rail) ──
        banner = page.text_content(".term-banner") or ""
        if "REPLAY MODE" in banner and page.locator(".replay-flag").count() == 1:
            print("PASS banner: REPLAY MODE honesty flag present")
        else:
            fails.append(f"banner: REPLAY MODE flag missing (banner text: {banner[:120]!r})")

        # ── (e2) the rendered chips must SAY 'replay', never 'live' (§0 rail:
        # replay is clearly not live — terminal.js relabels the view's
        # hardcoded 'live' text in its replay seam; assert it actually did) ──
        chip_texts = page.eval_on_selector_all(
            "#view-header .chip-text", "els => els.map(e => e.textContent)")
        live_chips = [t for t in chip_texts if t.endswith(": live")]
        replay_chips = [t for t in chip_texts if t.endswith(": replay")]
        if live_chips:
            fails.append(f"chip text presents replay as LIVE (§0 violation): {live_chips}")
        elif len(replay_chips) == 7:
            print(f"PASS chip text: all 7 matrix chips labeled 'replay' — {replay_chips}")
        else:
            fails.append(f"chip text: expected 7 ': replay' labels, got {chip_texts}")

        # ── (c) canvas non-blank: >2% of sampled pixels differ from page bg ──
        for c in page.evaluate(CANVAS_JS):
            label = f"[{c['id']}] {c['w']}x{c['h']} ({c['layers']} layer(s))"
            if c.get("skipped"):
                print(f"SKIP canvas {label}: below judged size — lw-charts corner stub or "
                      "tapeint sparkline (legitimately blank at times; screenshots are the witness)")
            elif c["nonBgPct"] is None:
                fails.append(f"canvas {label}: {c.get('note', 'unreadable')}")
            elif c["nonBgPct"] <= 2.0:
                fails.append(f"canvas {label}: blank — {c['nonBgPct']:.2f}% non-background pixels")
            else:
                print(f"PASS canvas {label}: {c['nonBgPct']:.1f}% non-background")

        # ── t1 screenshots +8s later: accumulation must be visible ──
        page.wait_for_timeout(8000)
        p, f = shoot_panels(page, args.out, "t1")
        shots += p
        fails += f

        # ── (a) zero console errors / uncaught exceptions, NO allowlist ──
        for e in console_errors:
            fails.append(f"console error: {e}")
        for e in page_errors:
            fails.append(f"pageerror: {e}")
        if not console_errors and not page_errors:
            print("PASS console: 0 console errors, 0 page errors")

        if args.keep_server:
            print(f"\n--keep-server: browser closed, page still served at {url}")
            print("Ctrl+C to stop.")
            browser.close()
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass
        else:
            browser.close()

    server.shutdown()

    print(f"\nscreenshots ({len(shots)}) in {args.out}")
    if fails:
        print(f"\nFAIL — {len(fails)} problem(s):")
        for f in fails:
            print(f"  FAIL {f}")
        return 1
    print("\nOK — terminal renders fixture replay end-to-end in Chromium.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
