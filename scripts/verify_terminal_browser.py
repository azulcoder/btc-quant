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


# N1 dead-surface model — mirrors terminal.js markPanelDead(). A faulted key's
# honest DOM footprint is NOT always one .panel:
#   • 'fp' paints TWO panels (the footprint AND the CVD subchart it draws into
#     view-cvd) → BOTH go .panel--dead with a 'stalled' chip; both canvases die
#     before a first good frame, so both are legitimately blank.
#   • 'tape'/'tapeint' SHARE one .panel → the faulted sub-container goes
#     .unit--dead (NEVER .panel--dead — that would flag the live sibling), the
#     chip in the shared <h2> names the sub-unit, and the SIBLING sub-unit must
#     stay live (undimmed). This is the finding-3 crux the harness now proves.
#   • any other key → its own .panel goes .panel--dead + 'stalled'.
#   panels = anchor ids that must sit inside a .panel--dead (each with a chip)
#   units  = element ids that must carry .unit--dead (chip in the shared <h2>)
#   live   = element ids that MUST stay undimmed (the still-painting sibling)
#   chip   = expected text on the faulted key's chip
# Witness keys must be FAST-cadence (dirty set most frames) so the breaker reaches
# N consecutive throws inside the sample window — fp/tape/tapeint/heat qualify. A
# sparse-cadence panel like 'det' (dirty only on a new detection event) may never
# reach 3 in a short replay; its surface mapping would be identical to 'heat''s
# (one own .panel--dead), so it adds no coverage and is left out on purpose.
DEAD_SURFACE = {
    "fp":      {"panels": ["view-footprint", "view-cvd"], "units": [],               "live": [],               "chip": "stalled"},
    "tape":    {"panels": [],                             "units": ["view-tape"],    "live": ["view-tapeint"], "chip": "tape stalled"},
    "tapeint": {"panels": [],                             "units": ["view-tapeint"], "live": ["view-tape"],     "chip": "tape speed stalled"},
    "heat":    {"panels": ["view-bookheat"],             "units": [],               "live": [],               "chip": "stalled"},
}


def dead_surface(key):
    return DEAD_SURFACE.get(key, {"panels": ["view-" + key], "units": [], "live": [], "chip": "stalled"})


def fault_blanks(key):
    """Canvas host-ids the faulted key legitimately blanks (its own units) — used
    to scope the sibling non-blank check; every OTHER canvas must keep painting."""
    s = dead_surface(key)
    return set(s["panels"]) | set(s["units"])


def run_fault(sync_playwright, args) -> int:
    """N1 paint-loop quarantine proof (L1, separate from the standard green run).

    Opens ?replay=1&fault=<key> so terminal.js forces that ONE panel to throw on
    every paint (a slice-build-style throw, BEFORE render, via safePanel), and
    proves the quarantine end-to-end in a real browser:
      (a) the faulted key's dead-surface matches DEAD_SURFACE exactly — the right
          panel(s)/sub-unit(s) carry the '.st-dead' chip + dimming, NO chip
          mislabels another key, and any shared-panel SIBLING stays live
          (findings 3/4: fp chips both panels; a tape fault never dims tapeint);
      (b) the throw was CAUGHT — ZERO uncaught pageerror (the whole point of N1:
          one bad panel can no longer blackout the loop);
      (c) telemetry is rate-limited — EXACTLY ONE console.error, naming the key
          and 'quarantined' (once on death, never per frame);
      (d) SIBLINGS keep painting — every judged canvas except the faulted key's
          own units is non-blank;
      (e) the rAF loop is STILL running after quarantine — the frames() heartbeat
          advances across a ~1.2 s sample.
    """
    key = args.fault
    blanks = fault_blanks(key)

    server, port = start_server()
    url = f"http://127.0.0.1:{port}/dashboard/terminal.html?replay=1&fault={key}"
    print(f"serving {ROOT} at http://127.0.0.1:{port}/ (localhost only)")
    print(f"N1 FAULT-INJECTION PROOF — opening {url}")

    fails: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    shots: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1680, "height": 1400})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        page.goto(url, wait_until="domcontentloaded")

        # Ready gate: ingestion is independent of the faulted panel's render, so
        # tapeRows>0 && heatSamples>=3 must still be reached (proof the pipeline
        # kept flowing while one panel threw every frame).
        try:
            page.wait_for_function(READY_JS, timeout=READY_TIMEOUT_MS)
            print(f"PASS ready: pipeline flowed despite fault (tapeRows>0, heatSamples>=3)")
        except Exception:
            dbg = page.evaluate("() => window.__BTCQ_TERMINAL_DEBUG ? __BTCQ_TERMINAL_DEBUG.guards() : null")
            fails.append(f"ready: page never reached tapeRows>0 && heatSamples>=3 (guards: {dbg})")

        # Quarantine is CADENCE-gated: a panel only throws when it is due(), so a
        # slower-cadence key (e.g. 'det') needs several due-fires to reach N
        # consecutive. Wait for the breaker to actually latch before asserting its
        # surface — makes the proof independent of any panel's MIN_MS.
        try:
            page.wait_for_function(
                "(k) => { const d = window.__BTCQ_TERMINAL_DEBUG;"
                " return d && d.guards()[k] && d.guards()[k].dead; }",
                arg=key, timeout=15_000)
        except Exception:
            g = page.evaluate("(k) => window.__BTCQ_TERMINAL_DEBUG ? __BTCQ_TERMINAL_DEBUG.guards()[k] : null", key)
            fails.append(f"quarantine: guard[{key!r}] never latched dead within 15s (cadence too slow?): {g}")

        # ── (a) the faulted key's dead-surface matches DEAD_SURFACE exactly ──
        exp = dead_surface(key)
        n0 = len(fails)
        snap = page.evaluate(
            "() => {"
            " const chip = (c) => ({ key: c.getAttribute('data-key'), text: c.textContent, title: c.title });"
            " return {"
            "  chips: [...document.querySelectorAll('.st-dead')].map(chip),"
            "  panelsDead: [...document.querySelectorAll('.panel--dead')].map("
            "    (p) => [...p.querySelectorAll('[id]')].map((e) => e.id).filter((s) => s.startsWith('view-'))),"
            "  unitsDead: [...document.querySelectorAll('.unit--dead')].map((u) => u.id) }; }")
        panels_flat = sorted({a for grp in snap["panelsDead"] for a in grp})
        units_flat = sorted(snap["unitsDead"])
        if panels_flat != sorted(exp["panels"]):
            fails.append(f"dead panels: expected {sorted(exp['panels'])}, got {panels_flat}")
        if units_flat != sorted(exp["units"]):
            fails.append(f"dead units: expected {sorted(exp['units'])}, got {units_flat}")
        # No chip may belong to a different key — that is the finding-3 mislabel
        # (a live sibling wearing 'stalled').
        foreign = [(c["key"], c["text"]) for c in snap["chips"] if c["key"] != key]
        if foreign:
            fails.append(f"dead chip mislabels other key(s): {foreign}")
        own = [c for c in snap["chips"] if c["key"] == key]
        if not own:
            fails.append(f"dead chip: no .st-dead chip for faulted key {key!r}")
        else:
            if any(c["text"] != exp["chip"] for c in own):
                fails.append(f"dead chip text: expected {exp['chip']!r}, got {[c['text'] for c in own]}")
            if any((not c["title"]) or "quarantined" not in c["title"] or "LAST GOOD frame" not in c["title"] for c in own):
                fails.append(f"dead chip title missing honesty text: {[c['title'] for c in own]}")
        # Finding-3 crux: a shared-panel sibling must stay LIVE (undimmed).
        for live_id in exp["live"]:
            st = page.evaluate(
                "(id) => { const e = document.getElementById(id); if (!e) return { missing: true };"
                " const p = e.closest('.panel');"
                " return { unitDead: e.classList.contains('unit--dead'),"
                "   panelDead: !!(p && p.classList.contains('panel--dead')) }; }", live_id)
            if st.get("missing"):
                fails.append(f"live sibling {live_id}: element missing")
            elif st["unitDead"] or st["panelDead"]:
                fails.append(f"live sibling {live_id} falsely quarantined "
                             f"(unitDead={st['unitDead']}, panelDead={st['panelDead']}) — "
                             f"a still-painting panel must never read stale (§0)")
        if len(fails) == n0:
            title0 = own[0]["title"] if own else ""
            print(f"PASS dead surface: panels={panels_flat} units={units_flat}, "
                  f"chip {exp['chip']!r}, siblings live — {title0[:60]}…")

        # ── guard state: faulted key dead (failures>=threshold), siblings alive ──
        guards = page.evaluate("() => window.__BTCQ_TERMINAL_DEBUG ? __BTCQ_TERMINAL_DEBUG.guards() : null")
        if not guards:
            fails.append("guards: __BTCQ_TERMINAL_DEBUG.guards() missing")
        elif key not in guards:
            fails.append(f"guards: faulted key {key!r} not among {list(guards)}")
        else:
            g = guards[key]
            others_dead = [k for k, v in guards.items() if k != key and v.get("dead")]
            if not g.get("dead"):
                fails.append(f"guard[{key}]: expected dead=True, got {g}")
            elif g.get("failures", 0) < g.get("threshold", 3):
                fails.append(f"guard[{key}]: failures {g.get('failures')} < threshold "
                             f"{g.get('threshold')} — loop died before reaching quarantine")
            elif others_dead:
                fails.append(f"quarantine leaked: sibling guards also dead: {others_dead}")
            else:
                print(f"PASS guard[{key}]: dead after {g['failures']} throws (threshold "
                      f"{g['threshold']}); all {len(guards) - 1} siblings alive")

        # t0 screenshots — the visual witness (faulted panel + live siblings).
        p, f = shoot_panels(page, args.out, f"fault-{key}-t0")
        shots += p
        fails += f

        # ── (d) SIBLINGS keep painting: every judged canvas but the faulted
        # panel's own must be non-blank ──
        sib_ok = 0
        for c in page.evaluate(CANVAS_JS):
            if c["id"] in blanks or c.get("skipped") or c["nonBgPct"] is None:
                continue
            if c["nonBgPct"] <= 2.0:
                fails.append(f"sibling canvas [{c['id']}] {c['w']}x{c['h']}: blank "
                             f"({c['nonBgPct']:.2f}%) — quarantine should not have touched it")
            else:
                sib_ok += 1
        if sib_ok >= 2:
            print(f"PASS siblings: {sib_ok} non-faulted canvas stacks still painting")
        else:
            fails.append(f"siblings: only {sib_ok} live canvas stacks (expected >=2)")

        # ── (e) rAF loop STILL running after quarantine: heartbeat advances ──
        f0 = page.evaluate("() => __BTCQ_TERMINAL_DEBUG.frames()")
        page.wait_for_timeout(1200)
        f1 = page.evaluate("() => __BTCQ_TERMINAL_DEBUG.frames()")
        if f1 - f0 >= 5:
            print(f"PASS loop alive: frames() advanced {f0} -> {f1} (+{f1 - f0}) after quarantine")
        else:
            fails.append(f"loop: frames() barely advanced {f0} -> {f1} — rAF loop may be dead")

        # A second store count must also have moved (a live panel's data grows).
        c0 = page.evaluate("() => __BTCQ_TERMINAL_DEBUG.counts().heatSamples")
        p, f = shoot_panels(page, args.out, f"fault-{key}-t1")
        shots += p
        fails += f
        c1 = page.evaluate("() => __BTCQ_TERMINAL_DEBUG.counts().heatSamples")
        print(f"note: heatSamples {c0} -> {c1} (sampler kept running through the fault)")

        # ── (b) throw CAUGHT: zero uncaught pageerror ──
        if page_errors:
            for e in page_errors:
                fails.append(f"pageerror (throw ESCAPED the boundary — N1 broken): {e}")
        else:
            print("PASS caught: 0 uncaught pageerror — the throw stayed inside safePanel")

        # ── (c) telemetry rate-limited: EXACTLY ONE quarantine console.error ──
        quar = [e for e in console_errors if "quarantined" in e and f'"{key}"' in e]
        other = [e for e in console_errors if e not in quar]
        if len(quar) != 1:
            fails.append(f"telemetry: expected exactly 1 quarantine log for {key!r}, "
                         f"got {len(quar)}: {quar}")
        elif other:
            fails.append(f"telemetry: unexpected extra console errors: {other}")
        else:
            print(f"PASS telemetry: exactly 1 quarantine log — {quar[0]}")

        browser.close()

    server.shutdown()
    print(f"\nscreenshots ({len(shots)}) in {args.out}")
    if fails:
        print(f"\nFAIL — {len(fails)} problem(s):")
        for f in fails:
            print(f"  FAIL {f}")
        return 1
    print(f"\nOK — N1 quarantine proven: panel '{key}' quarantined, siblings live, loop survived.")
    return 0


# N3 (Gap 7) a11y proof — footprint up/buy fill sampler. The buy side is drawn
# in --up (rgba(p.up, aB) fills + full-opacity imbalance strokes / delta text);
# "green-family" = green is the max channel and clearly above red, whether the
# palette is the teal default (#26A69A) or the Okabe-Ito bluish-green (#009E73).
# Averaging those pixels gives a palette signature the toggle MOVES (red channel
# drops teal→Okabe) but replay data volume does NOT (new bars paint in the
# CURRENT palette too, so more data never reintroduces teal). Vermillion/red
# down pixels have G-R < 0 and are excluded, so this isolates the up semantic.
FOOTPRINT_GREEN_JS = """
(sel) => {
  const host = document.querySelector(sel);
  if (!host) return { err: 'no footprint host' };
  let best = null;
  host.querySelectorAll('canvas').forEach((cv) => {
    if (cv.width < 2 || cv.height < 2) return;
    if (!best || cv.width * cv.height > best.width * best.height) best = cv;
  });
  if (!best) return { err: 'no footprint canvas' };
  let ctx;
  try { ctx = best.getContext('2d'); } catch (e) { return { err: 'no 2d ctx' }; }
  const d = ctx.getImageData(0, 0, best.width, best.height).data;
  let n = 0, r = 0, g = 0, b = 0;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i + 3] < 40) continue;                       // near-transparent → bg
    const R = d[i], G = d[i + 1], B = d[i + 2];
    if (G > 60 && G >= B && G - R > 24) { n++; r += R; g += G; b += B; }
  }
  return n ? { n, r: r / n, g: g / n, b: b / n } : { n: 0 };
}
"""


def run_a11y(sync_playwright, args) -> int:
    """N3 (Gap 7) a11y proof — the STANDING guard for the CVD-safe (Okabe-Ito)
    + density port to the terminal. A SEPARATE flag-gated run (like --fault) so
    the standard green path stays pristine: this one deliberately mutates view
    state, reloads, and asserts persistence — things the plain render check must
    not do.

    Proves the N3 accept clause end-to-end ("colour-blind mode visibly changes
    footprint/heatmap on the terminal and persists; browser-harness screenshot
    verifies"), and standing-guards the load-bearing choices a future edit could
    silently break (class on documentElement, the :root.cvd-strict override in
    terminal.css, the shared LS keys):
      (a) toggling CVD-safe flips the LIVE palette the canvas reader consumes —
          getComputedStyle(documentElement) --up/--down 26A69A/EF5350 → the
          EXACT Okabe-Ito 009E73/D55E00 — and the class lands on documentElement
          (NOT <body>), which is what terminal-views.js pal() reads at draw time;
      (b) it reaches PIXELS, not just the CSS var: the footprint's green up-fill
          recolours (avg green-family pixel shifts off teal toward Okabe-Ito —
          the red channel drops);
      (c) presentation ONLY — store counts are byte-identical across the toggle
          (read → click → read inside ONE synchronous evaluate so no replay tick
          intervenes): the palette swap touches no datum (§0 honesty rail);
      (d) density-compact retightens the token scale (--sp-2 8→6px, --fs-base
          13→12px);
      (e) both preferences PERSIST — after a reload the boot-apply restores the
          classes/vars/checkboxes from the shared localStorage keys (btcq-cvd /
          btcq-density, the same keys the analytics page uses).
    Screenshots a11y-off / a11y-on / a11y-reload are the visual witness.
    """
    def _norm(v):
        return (v or "").strip().lower()

    def shot(page, tag):
        paths = []
        full = os.path.join(args.out, f"a11y-{tag}-full.png")
        page.screenshot(path=full, full_page=True)
        paths.append(full)
        fp = os.path.join(args.out, f"a11y-{tag}-footprint.png")
        try:
            page.locator("#view-footprint").screenshot(path=fp, timeout=10_000)
            paths.append(fp)
        except Exception as e:  # zero-size / detached = a real page bug
            fails.append(f"screenshot footprint at {tag}: {type(e).__name__}: {e}")
        return paths

    server, port = start_server()
    url = f"http://127.0.0.1:{port}/dashboard/terminal.html?replay=1"
    print(f"serving {ROOT} at http://127.0.0.1:{port}/ (localhost only)")
    print(f"N3 A11Y PROOF — opening {url}")

    fails: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    shots: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1680, "height": 1400})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_function(READY_JS, timeout=READY_TIMEOUT_MS)
            print("PASS ready: tapeRows > 0 and heatSamples >= 3")
        except Exception:
            dbg = page.evaluate("() => window.__BTCQ_TERMINAL_DEBUG ? __BTCQ_TERMINAL_DEBUG.counts() : null")
            fails.append(f"ready: page never reached tapeRows>0 && heatSamples>=3 (counts: {dbg})")

        # Reader for the live palette + tokens off documentElement — EXACTLY the
        # node terminal-views.js pal() reads its custom props from.
        VARS_JS = (
            "() => { const s = getComputedStyle(document.documentElement); const g = (k) =>"
            " s.getPropertyValue(k).trim(); return { up: g('--up'), down: g('--down'),"
            " sp2: g('--sp-2'), fsBase: g('--fs-base'),"
            " cvd: document.documentElement.classList.contains('cvd-strict'),"
            " dense: document.documentElement.classList.contains('density-compact'),"
            " cvdBox: !!(document.getElementById('set-cvd-strict')||{}).checked,"
            " denBox: !!(document.getElementById('set-density-compact')||{}).checked,"
            " lsCvd: localStorage.getItem('btcq-cvd'), lsDen: localStorage.getItem('btcq-density') }; }"
        )

        # ── baseline (OFF): default teal palette, no a11y classes ──
        page.wait_for_timeout(3000)  # let the footprint accumulate up-fill
        v0 = page.evaluate(VARS_JS)
        if _norm(v0["up"]) != "#26a69a" or _norm(v0["down"]) != "#ef5350":
            fails.append(f"baseline palette: expected default 26A69A/EF5350, got {v0['up']!r}/{v0['down']!r}")
        if v0["cvd"] or v0["dense"]:
            fails.append(f"baseline classes: expected none, got cvd={v0['cvd']} dense={v0['dense']}")
        fp0 = page.evaluate(FOOTPRINT_GREEN_JS, "#view-footprint")
        if not fp0.get("n"):
            fails.append(f"baseline footprint: no green up-fill pixels to compare ({fp0}) — "
                         "replay too short or footprint empty")
        shots += shot(page, "off")

        # ── (c) presentation-only + toggle CVD ON, in ONE synchronous evaluate:
        # read counts → click the checkbox (fires change → applyCvd synchronously
        # → classList + LS + dirtyAll, NO recompute) → read counts. No replay
        # macrotask can preempt a synchronous evaluate, so any delta would be the
        # toggle mutating a datum — there must be none. ──
        pres = page.evaluate(
            "() => { const d = window.__BTCQ_TERMINAL_DEBUG; const before = d.counts();"
            " document.getElementById('set-cvd-strict').click(); const after = d.counts();"
            " return { before, after }; }")
        diffs = {k: [pres["before"][k], pres["after"].get(k)]
                 for k in pres["before"] if pres["before"][k] != pres["after"].get(k)}
        if diffs:
            fails.append(f"presentation-only VIOLATED: store counts changed across the CVD toggle "
                         f"(§0) — {diffs}")
        else:
            print(f"PASS presentation-only: {len(pres['before'])} store counts identical across the toggle")

        # ── (a) the toggle flipped the LIVE palette on documentElement ──
        page.wait_for_timeout(600)  # one rAF + a repaint so the canvas re-reads
        v1 = page.evaluate(VARS_JS)
        if _norm(v1["up"]) != "#009e73" or _norm(v1["down"]) != "#d55e00":
            fails.append(f"CVD-on palette: expected Okabe-Ito 009E73/D55E00 on documentElement, "
                         f"got {v1['up']!r}/{v1['down']!r}")
        elif not v1["cvd"]:
            fails.append("CVD-on: --up/--down flipped but .cvd-strict class not on documentElement")
        elif v1["lsCvd"] != "1" or not v1["cvdBox"]:
            fails.append(f"CVD-on: class set but LS/checkbox not synced (lsCvd={v1['lsCvd']!r}, box={v1['cvdBox']})")
        else:
            print("PASS CVD-on: documentElement --up/--down → Okabe-Ito 009E73/D55E00, "
                  "class + LS(btcq-cvd=1) + checkbox all synced")

        # ── (b) it reached PIXELS: the footprint up-fill recoloured off teal ──
        fp1 = page.evaluate(FOOTPRINT_GREEN_JS, "#view-footprint")
        if fp0.get("n") and fp1.get("n"):
            dist = ((fp0["r"] - fp1["r"]) ** 2 + (fp0["g"] - fp1["g"]) ** 2 + (fp0["b"] - fp1["b"]) ** 2) ** 0.5
            # Okabe-Ito green has LESS red than teal — the up-fill's red channel
            # must drop; and the average must move a visible distance.
            if fp1["r"] < fp0["r"] - 3 and dist > 8:
                print(f"PASS pixels: footprint up-fill recoloured — avg green-family "
                      f"({fp0['r']:.0f},{fp0['g']:.0f},{fp0['b']:.0f}) → "
                      f"({fp1['r']:.0f},{fp1['g']:.0f},{fp1['b']:.0f}), Δ={dist:.1f}, red dropped")
            else:
                fails.append(f"pixels: footprint up-fill did NOT recolour toward Okabe-Ito "
                             f"(off r={fp0['r']:.0f} → on r={fp1['r']:.0f}, Δ={dist:.1f}) — "
                             "CSS var flipped but the canvas did not follow")
        elif not fp1.get("n"):
            fails.append(f"pixels: no green up-fill in the CVD-on footprint ({fp1})")
        shots += shot(page, "on")

        # ── (d) density → compact retightens the token scale ──
        page.locator("#set-density-compact").check()
        page.wait_for_timeout(300)
        v2 = page.evaluate(VARS_JS)
        if v2["sp2"] != "6px" or v2["fsBase"] != "12px":
            fails.append(f"density-compact tokens: expected --sp-2 6px / --fs-base 12px, "
                         f"got {v2['sp2']!r}/{v2['fsBase']!r}")
        elif not v2["dense"] or v2["lsDen"] != "compact" or not v2["denBox"]:
            fails.append(f"density-compact: tokens set but class/LS/checkbox not synced "
                         f"(dense={v2['dense']}, lsDen={v2['lsDen']!r}, box={v2['denBox']})")
        else:
            print("PASS density: --sp-2 8→6px, --fs-base 13→12px, class + LS(btcq-density=compact) + checkbox synced")

        # ── (e) PERSISTENCE: reload → boot-apply restores both from the shared
        # LS keys (the whole point of reusing btcq-cvd / btcq-density) ──
        page.reload(wait_until="domcontentloaded")
        try:
            page.wait_for_function(READY_JS, timeout=READY_TIMEOUT_MS)
        except Exception:
            fails.append("reload: page never reached ready again")
        v3 = page.evaluate(VARS_JS)
        ok = (_norm(v3["up"]) == "#009e73" and v3["cvd"] and v3["cvdBox"]
              and v3["sp2"] == "6px" and v3["dense"] and v3["denBox"])
        if ok:
            print("PASS persistence: after reload both prefs restored from LS "
                  "(cvd-strict + density-compact on documentElement, vars + checkboxes)")
        else:
            fails.append(f"persistence: reload did NOT restore both prefs from LS — {v3}")
        shots += shot(page, "reload")

        # ── zero console errors / uncaught exceptions, NO allowlist ──
        for e in console_errors:
            fails.append(f"console error: {e}")
        for e in page_errors:
            fails.append(f"pageerror: {e}")
        if not console_errors and not page_errors:
            print("PASS console: 0 console errors, 0 page errors")

        browser.close()

    server.shutdown()
    print(f"\nscreenshots ({len(shots)}) in {args.out}")
    if fails:
        print(f"\nFAIL — {len(fails)} problem(s):")
        for f in fails:
            print(f"  FAIL {f}")
        return 1
    print("\nOK — N3 a11y proven: CVD-safe recolours the footprint on the terminal, "
          "presentation-only, and both toggles persist across a reload.")
    return 0


# N5 (Gap 8) silent-catch proof — surface the swallowed events that today have no
# on-page witness. Three REPLAY-gated seams, all inert in production:
#   INJECT  (?replay=1&drop&flap=dom): a dropped-frame count (2 parse + 1 handler,
#           the same health.bump path production's onDropped uses) AND a
#           NON-LATCHING render fault (dom throws on every odd evaluation, so the
#           guard's failures climb while dead stays false). Asserts the HEADER
#           health chip appears reading "3 dropped · N render faults", the counts
#           match __BTCQ_TERMINAL_DEBUG.health(), the flapped guard never latched,
#           and NO throw escaped / logged (a flap is caught below threshold).
#   CLEAN   (?replay=1): the same page with no seam — 0 console/page errors AND
#           the health chip is ABSENT (silent when healthy — the whole honesty
#           rail: count 0 = no chip).
#   CASCADE (?replay=1&fault=ingest): the N1 ingest latch fires the N5 cascade —
#           the six PROLOGUE-FED views (heat/micro/liqmap/det/walls + conf, the
#           maybeIntel-written confluence composite) wear a 'frozen' st-stale chip,
#           while agg/dom carry NONE (sink feeds them directly, so they stay live —
#           a stale chip there would cry wolf, §0; vpin/alerts likewise excluded).
FLAP_KEY_N5 = "dom"       # fast-cadence (120 ms), safePanel-wrapped, always laid out
CASCADE_STALE = {"heat": "view-bookheat", "micro": "view-micro", "liqmap": "view-liqheat",
                 "det": "view-detect", "walls": "view-walls", "conf": "view-conf"}
CASCADE_LIVE = {"agg": "view-aggbook", "dom": "view-dom"}   # must NEVER be cascaded (grounded §A.3)


def run_n5(sync_playwright, args) -> int:
    server, port = start_server()
    base = f"http://127.0.0.1:{port}/dashboard/terminal.html"
    print(f"serving {ROOT} at http://127.0.0.1:{port}/ (localhost only)")
    print("N5 SILENT-CATCH PROOF — inject / clean / cascade")
    fails: list[str] = []
    shots: list[str] = []

    def health_chip(page):
        """The header .st-health chip's {present, visible, text, title} — present
        is the element existing, visible is it not [hidden] (silent-when-healthy)."""
        return page.evaluate(
            "() => { const c = document.querySelector('.st-health');"
            " if (!c) return { present: false };"
            " return { present: true, visible: !c.hidden, text: c.textContent, title: c.title }; }")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        # ── INJECT: dropped-frame count + non-latching render fault → chip ──
        url = f"{base}?replay=1&drop&flap={FLAP_KEY_N5}"
        print(f"\n[inject] opening {url}")
        page = browser.new_page(viewport={"width": 1680, "height": 1400})
        cerr: list[str] = []
        perr: list[str] = []
        page.on("console", lambda m: cerr.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: perr.append(str(e)))
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_function(READY_JS, timeout=READY_TIMEOUT_MS)
        except Exception:
            fails.append("[inject] ready: pipeline never flowed (tapeRows>0, heatSamples>=3)")
        # the flap must actually accrue faults (dom is due ~8×/s → a couple of
        # seconds is plenty); wait for the DEBUG surface rather than a fixed sleep.
        try:
            page.wait_for_function(
                "() => { const d = window.__BTCQ_TERMINAL_DEBUG;"
                " return d && d.health().dropped === 3 && d.health().faults > 0; }",
                timeout=15_000)
        except Exception:
            h = page.evaluate("() => window.__BTCQ_TERMINAL_DEBUG ? __BTCQ_TERMINAL_DEBUG.health() : null")
            fails.append(f"[inject] counts never reached dropped==3 && faults>0: {h}")

        dbg = page.evaluate("() => __BTCQ_TERMINAL_DEBUG.health()")
        if dbg.get("dropped") != 3:
            fails.append(f"[inject] dropped: expected 3, got {dbg.get('dropped')} ({dbg.get('drops')})")
        if dbg.get("drops", {}).get("parse") != 2 or dbg.get("drops", {}).get("handler") != 1:
            fails.append(f"[inject] drop reasons: expected 2 parse / 1 handler, got {dbg.get('drops')}")
        if not dbg.get("faults", 0) > 0:
            fails.append(f"[inject] faults: expected >0 from the flap, got {dbg.get('faults')}")
        # the crux: the flapped guard accrued failures but NEVER latched dead.
        gd = page.evaluate("(k) => __BTCQ_TERMINAL_DEBUG.guards()[k]", FLAP_KEY_N5)
        if gd.get("dead") is not False:
            fails.append(f"[inject] flap guard[{FLAP_KEY_N5}] latched dead — the fault was supposed to be NON-latching: {gd}")
        elif not gd.get("failures", 0) > 0:
            fails.append(f"[inject] flap guard[{FLAP_KEY_N5}]: failures never climbed: {gd}")
        else:
            print(f"[inject] PASS non-latching: guard[{FLAP_KEY_N5}] failures={gd['failures']}, "
                  f"consecutive={gd['consecutive']}, dead=False")

        # the HEADER health chip is visible and reads the counts.
        hc = health_chip(page)
        if not hc.get("present"):
            fails.append("[inject] health chip: .st-health element missing from the header")
        elif not hc.get("visible"):
            fails.append("[inject] health chip: present but [hidden] despite dropped==3 (should be visible)")
        else:
            txt = hc.get("text", "")
            if "3 dropped" not in txt:
                fails.append(f"[inject] health chip text missing '3 dropped': {txt!r}")
            if "render fault" not in txt:
                fails.append(f"[inject] health chip text missing 'render fault': {txt!r}")
            if "Observability only" not in hc.get("title", ""):
                fails.append(f"[inject] health chip title missing the observability-only honesty line: {hc.get('title')!r}")
            if not fails or "3 dropped" in txt:
                print(f"[inject] PASS health chip visible: {txt!r}")
        # the flap throws are CAUGHT below threshold — no escape, no quarantine log.
        if perr:
            fails.append(f"[inject] pageerror (a flap throw ESCAPED the boundary): {perr}")
        if cerr:
            fails.append(f"[inject] console error (flap should log NOTHING — it never latches): {cerr}")
        if not perr and not cerr:
            print("[inject] PASS caught: 0 pageerror, 0 console error (flap stayed below threshold)")
        p, f = shoot_panels(page, args.out, "n5-inject")
        shots += p
        fails += f
        page.close()

        # ── CLEAN: silent when healthy — no seam, no chip, no errors ──
        url = f"{base}?replay=1"
        print(f"\n[clean] opening {url}")
        page = browser.new_page(viewport={"width": 1680, "height": 1400})
        cerr = []
        perr = []
        page.on("console", lambda m: cerr.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: perr.append(str(e)))
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_function(READY_JS, timeout=READY_TIMEOUT_MS)
        except Exception:
            fails.append("[clean] ready: pipeline never flowed")
        page.wait_for_timeout(1500)   # let a few header cadences pass — a spurious chip would have shown
        dbg = page.evaluate("() => __BTCQ_TERMINAL_DEBUG.health()")
        if dbg.get("dropped") or dbg.get("faults"):
            fails.append(f"[clean] counts non-zero on a healthy run (fabricated problem?): {dbg}")
        hc = health_chip(page)
        if hc.get("present") and hc.get("visible"):
            fails.append(f"[clean] health chip VISIBLE on a healthy run — silent-when-healthy violated: {hc.get('text')!r}")
        else:
            print("[clean] PASS silent: health chip absent/hidden on a healthy run")
        if cerr or perr:
            fails.append(f"[clean] not clean: {len(cerr)} console + {len(perr)} page errors: {(cerr + perr)[:3]}")
        else:
            print("[clean] PASS clean: 0 console errors, 0 page errors")
        p, f = shoot_panels(page, args.out, "n5-clean")
        shots += p
        fails += f
        page.close()

        # ── CASCADE: ingest latch → 'frozen' chip on the 5 flush-derived views ──
        url = f"{base}?replay=1&fault=ingest"
        print(f"\n[cascade] opening {url}")
        page = browser.new_page(viewport={"width": 1680, "height": 1400})
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_function(
                "() => { const d = window.__BTCQ_TERMINAL_DEBUG;"
                " return d && d.guards().ingest && d.guards().ingest.dead; }",
                timeout=15_000)
            print("[cascade] PASS ingest guard latched dead (drives the cascade)")
        except Exception:
            g = page.evaluate("() => window.__BTCQ_TERMINAL_DEBUG ? __BTCQ_TERMINAL_DEBUG.guards().ingest : null")
            fails.append(f"[cascade] ingest guard never latched dead: {g}")
        snap = page.evaluate(
            "() => ({"
            " stale: [...document.querySelectorAll('.st-stale')].map("
            "   (c) => ({ key: c.getAttribute('data-key'), text: c.textContent,"
            "     panel: (c.closest('.panel') && [...c.closest('.panel').querySelectorAll('[id]')]"
            "       .map((e) => e.id).filter((s) => s.startsWith('view-'))) || [] })),"
            " stalePanels: [...document.querySelectorAll('.panel--stale')].map("
            "   (p) => [...p.querySelectorAll('[id]')].map((e) => e.id).filter((s) => s.startsWith('view-'))) })")
        got_keys = sorted({c["key"] for c in snap["stale"]})
        if got_keys != sorted(CASCADE_STALE):
            fails.append(f"[cascade] stale chip keys: expected {sorted(CASCADE_STALE)}, got {got_keys}")
        for c in snap["stale"]:
            if c["text"] != "frozen":
                fails.append(f"[cascade] stale chip for {c['key']!r} text {c['text']!r} != 'frozen'")
            anchor = CASCADE_STALE.get(c["key"])
            if anchor and anchor not in c["panel"]:
                fails.append(f"[cascade] stale chip {c['key']!r} on wrong panel {c['panel']} (want {anchor})")
        # the honesty crux: agg/dom stay LIVE — no stale chip, no frozen panel.
        stale_anchors = {a for grp in snap["stalePanels"] for a in grp}
        for key, anchor in CASCADE_LIVE.items():
            if anchor in stale_anchors:
                fails.append(f"[cascade] {anchor} ({key}) wrongly frozen — sink feeds it directly, it is LIVE (§0 cry-wolf)")
        if got_keys == sorted(CASCADE_STALE) and not (stale_anchors & set(CASCADE_LIVE.values())):
            print(f"[cascade] PASS {len(CASCADE_STALE)} frozen chips on {got_keys}; agg/dom stayed live")
        p, f = shoot_panels(page, args.out, "n5-cascade")
        shots += p
        fails += f
        page.close()

        browser.close()

    server.shutdown()
    print(f"\nscreenshots ({len(shots)}) in {args.out}")
    if fails:
        print(f"\nFAIL — {len(fails)} problem(s):")
        for f in fails:
            print(f"  FAIL {f}")
        return 1
    print("\nOK — N5 silent-catch proven: dropped/fault counts surface on the header chip, "
          "silent when healthy, and the ingest latch freezes only the flush-derived views.")
    return 0


# ─── T-4: layout census ──────────────────────────────────────────────────────
#
# WHY THIS EXISTS: the T-4 review measured a 7,201px page, 35 panels, 13 empty,
# and three text collisions — all by eye, off screenshots. Those are exactly the
# numbers the visual work is judged on, and a target nobody can re-measure rots
# within a release. This pass turns each into a printed number so a regression
# is a diff, not an argument. It ASSERTS nothing by default (the honest baseline
# is whatever the page is today); it reports, and --census-max-height turns the
# page-height target into a gate once a budget is agreed.
#
# Overlap detection is deliberately narrow: it compares the bounding boxes of
# DOM text nodes that are siblings within one panel. Canvas-drawn collisions
# (TPO letters vs VAH/POC/VAL labels are painted, not DOM) CANNOT be caught this
# way — those stay screenshot-judged, and this pass says so rather than implying
# coverage it does not have.
CENSUS_JS = r"""
() => {
  const doc = document;
  // ALL panels, not just direct grid children: .term-col.area-mid / .area-right
  // are column containers holding nested <section class="panel"> (28 of the 35
  // panels are grid children, 7 are nested). Counting only the children
  // undercounts by 7 and would make an empty-panel percentage wrong.
  const panels = Array.from(doc.querySelectorAll('main.term-main section.panel'));
  const gridChildren = Array.from(doc.querySelectorAll('main.term-main > section.panel')).length;
  const empty = panels.filter((p) => p.classList.contains('panel--empty'));
  // Candidate text boxes: small inline labels/notes inside a panel body. Two
  // that overlap by more than a couple of px are colliding text.
  const overlaps = [];
  for (const p of panels) {
    const els = Array.from(p.querySelectorAll('.panel-src, .hint, .farb-note, .chart-na, .local-only .lo-why, .local-only .lo-ok, h2 > span'))
      .filter((e) => e.offsetParent !== null && e.getClientRects().length);
    for (let i = 0; i < els.length; i++) {
      for (let j = i + 1; j < els.length; j++) {
        const a = els[i].getBoundingClientRect(), b = els[j].getBoundingClientRect();
        const ox = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const oy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (ox > 2 && oy > 2) {
          overlaps.push({
            panel: (p.className.match(/area-[a-z0-9-]+/) || ['?'])[0],
            a: els[i].className || els[i].tagName, b: els[j].className || els[j].tagName,
            ox: Math.round(ox), oy: Math.round(oy),
          });
        }
      }
    }
  }
  return {
    scrollHeight: doc.scrollingElement.scrollHeight,
    viewportHeight: window.innerHeight,
    docWidth: doc.scrollingElement.scrollWidth,
    windowWidth: window.innerWidth,
    panels: panels.length,
    gridChildren,
    empty: empty.length,
    emptyKeys: empty.map((p) => (p.className.match(/area-[a-z0-9-]+/) || ['?'])[0]),
    hints: doc.querySelectorAll('main.term-main section.panel .hint').length,
    // The v4.2 attribution logo is an <a> to tradingview.com injected inside the
    // chart container. Scoped to main so the deliberate ONE credit link in the
    // page footer is not counted as a watermark — the point of T-4's change is
    // that the mark moves out of the data, not that attribution disappears.
    tvLogos: doc.querySelectorAll('main.term-main a[href*="tradingview.com"], main.term-main #tv-attr-logo, main.term-main a[id*="tv-attr"]').length,
    footerAttrib: doc.querySelectorAll('footer a[href*="tradingview.com"]').length,
    lwCharts: doc.querySelectorAll('.tv-lightweight-charts').length,
    dustRows: doc.querySelectorAll('.tape-row.dust').length,
    localOnlyVisible: !!doc.querySelector('.local-only.local-on'),
    overlaps,
  };
}
"""


def run_focus(sync_playwright, args) -> int:
    """T-4: panel hierarchy (data-tier from the M3 registry) + focus/maximize mode.

    A SEPARATE run because it drives interaction and mutates view state (the
    standard gate may not), exactly like --a11y. Asserts the tier stamp reaches
    every panel, that double-clicking a header maximizes it, that Esc returns,
    and — the honesty rail — that focusing MUTATES NO DATUM: the stores keep
    ingesting while siblings are hidden, so every store count must be unchanged.
    """
    server, port = start_server()
    base = f"http://127.0.0.1:{port}/dashboard/terminal.html"
    fails: list[str] = []
    shots: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1680, "height": 1050})
            cerr: list[str] = []
            perr: list[str] = []
            page.on("console", lambda m: cerr.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: perr.append(str(e)))
            url = f"{base}?replay=1"
            print(f"[focus] opening {url}")
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_function(READY_JS, timeout=READY_TIMEOUT_MS)
            except Exception:
                fails.append("[focus] ready: pipeline never flowed")
            page.wait_for_timeout(1500)

            # ── tiers: every panel stamped, and the primary set is the instrument ──
            tiers = page.evaluate("""() => {
              const out = {primary: [], secondary: [], tertiary: [], missing: []};
              for (const p of document.querySelectorAll('main.term-main section.panel')) {
                const t = p.getAttribute('data-tier');
                const key = (p.className.match(/area-[a-z0-9-]+/) || ['?'])[0];
                if (!t) out.missing.push(key); else out[t].push(key);
              }
              return out;
            }""")
            if tiers["missing"]:
                fails.append(f"[focus] panels with NO data-tier: {tiers['missing']}")
            else:
                print(f"[focus] PASS tiers stamped: {len(tiers['primary'])} primary, "
                      f"{len(tiers['secondary'])} secondary, {len(tiers['tertiary'])} tertiary")
            # The instrument must be the primary tier — if the footprint/tape/ladder
            # are not primary, the hierarchy is decorative rather than meaningful.
            for want in ("area-fp", "area-stats"):
                if want not in tiers["primary"]:
                    fails.append(f"[focus] {want} must be data-tier=primary (it is the instrument)")

            # ── store counts BEFORE focusing (the honesty baseline) ──
            counts_before = page.evaluate("() => __BTCQ_TERMINAL_DEBUG.counts()")

            # ── double-click the footprint header → maximized ──
            page.dblclick("section.area-fp > h2")
            page.wait_for_timeout(700)
            st = page.evaluate("""() => {
              const main = document.querySelector('main.term-main');
              const foc = document.querySelectorAll('.panel--focus');
              const fp = document.querySelector('section.area-fp');
              // Count VISIBLE panels rather than probing a specific class: 7 of the
              // 35 panels are bare <section class="panel"> nested in a .term-col
              // and carry no area-* class, so a class probe silently matches
              // nothing and the assertion passes for the wrong reason.
              const all = [...document.querySelectorAll('main.term-main section.panel')];
              return {
                mainFocused: main.classList.contains('term-main--focused'),
                nFocused: foc.length,
                fpIsFocused: !!fp && fp.classList.contains('panel--focus'),
                fpH: fp ? Math.round(fp.getBoundingClientRect().height) : 0,
                nVisible: all.filter((p) => p.offsetParent !== null).length,
                vh: window.innerHeight,
              };
            }""")
            if not (st["mainFocused"] and st["fpIsFocused"] and st["nFocused"] == 1):
                fails.append(f"[focus] dblclick did not maximize exactly one panel: {st}")
            elif st["nVisible"] != 1:
                fails.append(f"[focus] {st['nVisible']} panels visible while focused — expected exactly 1")
            elif st["fpH"] < 0.7 * st["vh"]:
                fails.append(f"[focus] focused panel only {st['fpH']}px of {st['vh']}px viewport")
            else:
                print(f"[focus] PASS maximized: fp {st['fpH']}px of {st['vh']}px viewport, siblings hidden")

            # ── Esc restores ──
            page.keyboard.press("Escape")
            page.wait_for_timeout(700)
            back = page.evaluate("""() => {
              const main = document.querySelector('main.term-main');
              const all = [...document.querySelectorAll('main.term-main section.panel')];
              return {
                mainFocused: main.classList.contains('term-main--focused'),
                nFocused: document.querySelectorAll('.panel--focus').length,
                nVisible: all.filter((p) => p.offsetParent !== null).length,
              };
            }""")
            if back["mainFocused"] or back["nFocused"] or back["nVisible"] < 2:
                fails.append(f"[focus] Esc did not restore the grid: {back}")
            else:
                print(f"[focus] PASS Esc restored the grid ({back['nVisible']} panels visible again)")

            # ── the NESTED case, which is the one that actually broke ──
            # 7 panels live inside .term-col.area-mid/.area-right rather than being
            # grid children. Hiding every non-focused direct child of <main> hides
            # the COLUMN holding a focused nested panel — i.e. the panel disappears
            # when you maximize it. Exercised explicitly because a grid-child test
            # passes straight through that bug.
            nested = page.evaluate("""() => {
              const p = document.querySelector('main.term-main .term-col > section.panel');
              if (!p) return null;
              const h2 = p.querySelector(':scope > h2');
              return h2 ? (h2.textContent || '').slice(0, 28) : null;
            }""")
            if not nested:
                fails.append("[focus] could not find a nested panel to test (selector drift?)")
            else:
                page.evaluate("""() => {
                  const p = document.querySelector('main.term-main .term-col > section.panel');
                  const h2 = p.querySelector(':scope > h2');
                  h2.dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));
                }""")
                page.wait_for_timeout(700)
                nst = page.evaluate("""() => {
                  const foc = document.querySelector('.panel--focus');
                  const all = [...document.querySelectorAll('main.term-main section.panel')];
                  return {
                    focVisible: !!foc && foc.offsetParent !== null,
                    focH: foc ? Math.round(foc.getBoundingClientRect().height) : 0,
                    nVisible: all.filter((p) => p.offsetParent !== null).length,
                    vh: window.innerHeight,
                  };
                }""")
                if not nst["focVisible"]:
                    fails.append(f"[focus] NESTED panel ({nested!r}) vanished when focused — "
                                 "its ancestor .term-col was hidden with the siblings")
                elif nst["nVisible"] != 1:
                    fails.append(f"[focus] nested focus left {nst['nVisible']} panels visible — expected 1")
                elif nst["focH"] < 0.7 * nst["vh"]:
                    fails.append(f"[focus] nested panel only {nst['focH']}px of {nst['vh']}px")
                else:
                    print(f"[focus] PASS nested panel ({nested!r}) maximizes to {nst['focH']}px "
                          "— its ancestor column survives")
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)

            # ── honesty: presentation only, no datum moved ──
            counts_after = page.evaluate("() => __BTCQ_TERMINAL_DEBUG.counts()")
            regressed = [k for k in counts_before
                         if isinstance(counts_before.get(k), (int, float))
                         and isinstance(counts_after.get(k), (int, float))
                         and counts_after[k] < counts_before[k]]
            if regressed:
                fails.append(f"[focus] store counts went BACKWARD across focus (data lost): {regressed}")
            else:
                print(f"[focus] PASS presentation-only: {len(counts_before)} store counts never regressed "
                      "(ingest continued while siblings were hidden)")

            if cerr or perr:
                fails.append(f"[focus] not clean: {len(cerr)} console + {len(perr)} page errors: {(cerr + perr)[:3]}")
            else:
                print("[focus] PASS clean: 0 console errors, 0 page errors")

            full = os.path.join(args.out, "full-focus.png")
            page.dblclick("section.area-fp > h2")
            page.wait_for_timeout(600)
            page.screenshot(path=full, full_page=True)
            shots.append(full)
            page.close()
            browser.close()
    finally:
        server.shutdown()

    print(f"\nscreenshots ({len(shots)}) in {args.out}")
    if fails:
        print("\n== focus/hierarchy FAILURES ==")
        for x in fails:
            print("  FAIL " + x)
        return 1
    print("\nOK — T-4 hierarchy + focus mode proven: tiers stamped from the registry, "
          "dblclick maximizes, Esc restores, and no datum moved.")
    return 0


def run_census(sync_playwright, args) -> int:
    """T-4 layout census: page height, panel/empty counts, DOM text collisions."""
    server, port = start_server()
    base = f"http://127.0.0.1:{port}/dashboard/terminal.html"
    url = f"{base}?replay=1"
    fails: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            # 1680x1050 is the review's own measurement viewport, so the numbers
            # printed here are comparable to the figures T-4 was scoped against.
            page = browser.new_page(viewport={"width": 1680, "height": 1050})
            print(f"[census] opening {url} at 1680x1050")
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_function(READY_JS, timeout=READY_TIMEOUT_MS)
            except Exception:
                fails.append("[census] ready: pipeline never flowed")
            page.wait_for_timeout(2500)   # let the replay accrue + panels settle
            c = page.evaluate(CENSUS_JS)

            print("\n== layout census (1680x1050, ?replay=1) ==")
            print(f"  page scrollHeight     {c['scrollHeight']} px"
                  f"  ({c['scrollHeight'] / max(1, c['viewportHeight']):.1f} viewports)")
            print(f"  panels                {c['panels']}"
                  f"  ({c['gridChildren']} grid children + {c['panels'] - c['gridChildren']} nested in term-col)")
            print(f"  empty (.panel--empty) {c['empty']}"
                  f"  ({100 * c['empty'] / max(1, c['panels']):.0f}%)")
            print(f"  .hint coverage        {c['hints']}/{c['panels']}")
            print(f"  TV watermarks in main {c['tvLogos']}  (across {c['lwCharts']} mounted lw-charts)"
                  f"; footer credit: {c['footerAttrib']}")
            if c["lwCharts"] and c["tvLogos"]:
                fails.append(f"[census] {c['tvLogos']} TradingView watermark(s) still inside the data area "
                             f"— layout.attributionLogo should be false at every createChart site")
            if not c["footerAttrib"]:
                fails.append("[census] no TradingView credit in the footer — the watermark is DISABLED, so "
                             "the Apache-2.0 attribution must be carried there instead")
            print(f"  tape dust rows        {c['dustRows']}")
            print(f"  local-only strip      {'visible' if c['localOnlyVisible'] else 'hidden'}")
            print(f"  DOM text collisions   {len(c['overlaps'])}")
            for o in c["overlaps"][:10]:
                print(f"    {o['panel']}: {o['a']} x {o['b']} overlap {o['ox']}x{o['oy']}px")
            if c["emptyKeys"]:
                print(f"  empty panels: {', '.join(c['emptyKeys'])}")

            # Horizontal overflow is never acceptable — the page must not scroll
            # sideways at a standard desktop width.
            if c["docWidth"] > c["windowWidth"] + 1:
                fails.append(f"[census] page scrolls HORIZONTALLY: {c['docWidth']}px > {c['windowWidth']}px viewport")

            if args.census_max_height and c["scrollHeight"] > args.census_max_height:
                fails.append(f"[census] page height {c['scrollHeight']}px exceeds "
                             f"--census-max-height {args.census_max_height}px")

            print("\n  NOTE canvas-painted collisions (TPO letters vs VAH/POC/VAL, axis labels)")
            print("       are NOT covered here — they are drawn, not DOM. Screenshots remain")
            print("       their only witness; this census does not imply otherwise.")

            p, f = shoot_panels(page, args.out, "census")
            page.close()
            browser.close()
    finally:
        server.shutdown()

    if fails:
        print("\n== census FAILURES ==")
        for x in fails:
            print("  FAIL " + x)
        return 1
    print("\nOK — census recorded (reporting pass; add --census-max-height to gate).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "reports", "verify"),
                    help="screenshot directory (default: reports/verify/)")
    ap.add_argument("--keep-server", action="store_true",
                    help="keep serving after the checks for interactive debugging (Ctrl+C to stop)")
    ap.add_argument("--fault", metavar="PANEL_KEY", default=None,
                    help="N1 paint-loop quarantine proof: open ?replay=1&fault=<key> "
                         "(e.g. --fault fp) and assert the faulted panel is quarantined "
                         "(dead chip) while every SIBLING keeps painting and the rAF loop "
                         "survives. A SEPARATE run from the standard green path — this one "
                         "expects exactly one quarantine console.error, never zero.")
    ap.add_argument("--a11y", action="store_true",
                    help="N3 (Gap 7) a11y proof: toggle CVD-safe (Okabe-Ito) + density on the "
                         "terminal and assert the palette flips on documentElement, reaches the "
                         "footprint pixels, mutates NO datum, and persists across a reload from "
                         "the shared LS keys. A SEPARATE run — it deliberately mutates view state.")
    ap.add_argument("--n5", action="store_true",
                    help="N5 (Gap 8) silent-catch proof: three REPLAY-gated seams — inject "
                         "(?drop&flap=dom) surfaces a dropped-frame count + a NON-latching render "
                         "fault on the header health chip; clean (?replay=1) proves it is silent "
                         "when healthy; cascade (?fault=ingest) freezes only the flush-derived "
                         "views (agg/dom stay live). A SEPARATE run — it drives injected faults.")
    ap.add_argument("--focus", action="store_true",
                    help="T-4 hierarchy + focus-mode proof: every panel carries a data-tier from "
                         "the M3 registry, double-clicking a panel header maximizes it, Esc "
                         "restores the grid, and no store count regresses across the toggle "
                         "(presentation only). A SEPARATE run — it drives interaction.")
    ap.add_argument("--census", action="store_true",
                    help="T-4 layout census: print page scrollHeight, panel + empty-panel counts, "
                         ".hint coverage, TradingView logo count, tape dust rows and DOM text "
                         "collisions at 1680x1050. A REPORTING pass (asserts only horizontal "
                         "overflow) so the visual targets are re-measurable instead of eyeballed.")
    ap.add_argument("--census-max-height", type=int, default=0, metavar="PX",
                    help="with --census, FAIL if the page is taller than PX (turns the "
                         "page-height target into a gate once a budget is agreed).")
    args = ap.parse_args()

    sync_playwright = _require_playwright()
    os.makedirs(args.out, exist_ok=True)

    # N1: the fault-injection proof is its own self-contained run so it never
    # perturbs the standard zero-error L1 gate (§N1 telemetry vs the zero-console
    # gate: a quarantine logs exactly once, by design).
    if args.fault:
        return run_fault(sync_playwright, args)

    # N3: the a11y toggle proof is likewise self-contained — it mutates view
    # state, reloads, and asserts persistence, none of which the standard render
    # gate may do.
    if args.a11y:
        return run_a11y(sync_playwright, args)

    # N5: the silent-catch proof is self-contained — it injects a dropped-frame
    # count and a non-latching render fault, which the standard zero-count green
    # run must never see; run it separately, like --fault/--a11y.
    if args.n5:
        return run_n5(sync_playwright, args)

    # T-4: the layout census is its own pass — it measures at the review's
    # 1680x1050 viewport (the standard gate uses a tall 1400px one so every panel
    # is laid out for element screenshots), so it must not share that run.
    if args.focus:
        return run_focus(sync_playwright, args)

    if args.census:
        return run_census(sync_playwright, args)

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
            #
            # T-3 (§4i): the aggregated tape + ladder must be alive —
            #   tapeAggRows >= 1  (merged blocks exist in the aggregator);
            #   imprintLevels >= 1 (the bybit trade imprint bucketed prints —
            #                       the ladder's volume-at-price surface).
            # bigPrints is NOT asserted: a short replay honestly rarely holds a
            # ≥$1M huge/whale BLOCK, so the rail is legitimately empty (its
            # structure is checked below; the live check reports population).
            mins = {"tapeRows": 1, "ladderRows": 1, "cvdPoints": 1,
                    "heatSamples": 3, "aggLevels": 1, "footprintBars": 1,
                    "basisPoints": 1, "enabledLegs": 7, "aggLegs": 3,
                    "spotPerpLive": 1, "tapeAggRows": 1, "imprintLevels": 1}
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

        # ── (d3) T-3 (§4i) tape + ladder surfaces render their DOM ──
        # The big-print rail strip is present above the tape (populated or its
        # honest empty note); the tape honesty line names the feeding legs +
        # states the tiers are conventions; the ladder spread row carries the
        # enriched mid + spread readout (the reworked DomLadderView).
        rail = page.locator("#view-tape .bigprint-rail").count()
        if rail == 1:
            print("PASS tape: big-print rail strip present")
        else:
            fails.append(f"tape: expected 1 .bigprint-rail strip, got {rail}")
        hon = page.text_content("#view-tape .tape-hon") or ""
        if "merges" in hon and "conventions" in hon:
            print("PASS tape: honesty line names feeding legs + tier conventions")
        else:
            fails.append(f"tape: honesty line missing/wrong (text={hon[:90]!r})")
        # Tiered blocks: at least one aggregated row rendered with a tier class.
        tiered = page.locator("#view-tape .tape-row[class*='tier-']").count()
        if tiered >= 1:
            print(f"PASS tape: {tiered} tier-classed block rows rendered")
        else:
            fails.append("tape: no tier-classed block rows rendered")
        spread = page.text_content("#view-dom tr.spread") or ""
        if "mid" in spread and "spread" in spread:
            print("PASS ladder: enriched spread row (mid + spread ticks/bps)")
        else:
            fails.append(f"ladder: spread row missing mid/spread readout (text={spread[:90]!r})")

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
