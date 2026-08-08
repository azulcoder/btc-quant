"""ui_runtime_probe.py — what actually crosses the wire into the browser, and whether the
tab's memory grows without bound.

Read-only: it opens the terminal page and watches. It changes no dashboard file, touches no
recorder and no collector.

Two numbers `PLAN-orderflow-terminal-002.md` left empty:

* **bytes into the tab per 60 s of normal operation**, split into what arrives already
  aggregated versus what arrives raw and is aggregated client-side. The split matters because
  the census established that two of the three chains feeding this page aggregate in JS.
* **peak tab memory after five minutes.** Monotonic growth is a leak, and a leak in a page
  meant to sit open all day is a defect rather than a preference.

Live mode on purpose (no `?replay=1`): "normal operation" is the live WebSocket fan-in, and a
fixture replay would measure the fixture instead. Nothing is written back to any venue — the
page only subscribes.
"""

from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import statistics
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(root: Path):
    handler = lambda *a, **k: _Quiet(*a, directory=str(root), **k)  # noqa: E731
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--net-seconds", type=int, default=60)
    ap.add_argument("--mem-seconds", type=int, default=300)
    ap.add_argument("--json", default=str(REPO / "reports" / "ui-runtime.json"))
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    httpd, port = serve(REPO / "dashboard")
    ws_bytes = {"in": 0, "frames": 0}
    http_bytes = {"total": 0, "by_type": {}}
    try:
        with sync_playwright() as p:
            br = p.chromium.launch()
            page = br.new_page(viewport={"width": 1680, "height": 1050})
            cdp = page.context.new_cdp_session(page)
            cdp.send("Network.enable")

            def on_ws(ev):
                ws_bytes["in"] += len(ev.get("response", {}).get("payloadData", "") or "")
                ws_bytes["frames"] += 1

            def on_resp(ev):
                pass

            def on_finished(ev):
                http_bytes["total"] += ev.get("encodedDataLength", 0) or 0

            cdp.on("Network.webSocketFrameReceived", on_ws)
            cdp.on("Network.loadingFinished", on_finished)

            t0 = time.time()
            page.goto(f"http://127.0.0.1:{port}/terminal.html", wait_until="load")
            load_bytes = http_bytes["total"]
            page.wait_for_timeout(2000)
            base_ws = ws_bytes["in"]
            base_http = http_bytes["total"]
            print(f"page load: {load_bytes:,} B of assets, {time.time() - t0:.2f}s")

            print(f"\nITEM 3b — watching {a.net_seconds}s of normal operation…")
            page.wait_for_timeout(a.net_seconds * 1000)
            d_ws = ws_bytes["in"] - base_ws
            d_http = http_bytes["total"] - base_http
            tot = d_ws + d_http
            print(f"  WebSocket payload in : {d_ws:,} B  ({ws_bytes['frames']:,} frames)")
            print(f"  HTTP/fetch in        : {d_http:,} B")
            print(f"  TOTAL in {a.net_seconds}s      : {tot:,} B "
                  f"= {tot / a.net_seconds / 1024:.1f} KB/s "
                  f"-> {tot / a.net_seconds * 86400 / 1e6:.0f} MB/day at this rate")
            if tot:
                print(f"  aggregated CLIENT-SIDE (raw WS ticks) : {d_ws / tot:.1%}")
                print(f"  arrives pre-aggregated (HTTP/parquet) : {d_http / tot:.1%}")

            print(f"\nITEM 3c — tab memory over {a.mem_seconds}s…")
            samples = []
            step = max(10, a.mem_seconds // 20)
            for i in range(a.mem_seconds // step):
                m = page.evaluate(
                    "() => (performance.memory ? performance.memory.usedJSHeapSize : null)")
                if m:
                    samples.append((round(time.time() - t0), m))
                page.wait_for_timeout(step * 1000)
            if samples:
                first, last = samples[0][1], samples[-1][1]
                peak = max(s[1] for s in samples)
                halves = len(samples) // 2
                early = statistics.mean(s[1] for s in samples[:halves])
                late = statistics.mean(s[1] for s in samples[halves:])
                rising = sum(1 for x, y in zip(samples, samples[1:]) if y[1] > x[1])
                print(f"  samples {len(samples)} · first {first/1e6:.1f} MB · "
                      f"last {last/1e6:.1f} MB · PEAK {peak/1e6:.1f} MB")
                print(f"  mean first half {early/1e6:.1f} MB vs second half {late/1e6:.1f} MB "
                      f"({(late-early)/early:+.1%})")
                print(f"  monotonic-rising steps: {rising}/{len(samples)-1} "
                      f"-> {'MONOTONIC (leak signature)' if rising == len(samples)-1 else 'not monotonic'}")
            Path(a.json).parent.mkdir(parents=True, exist_ok=True)
            Path(a.json).write_text(json.dumps(
                {"load_bytes": load_bytes, "window_s": a.net_seconds,
                 "ws_bytes": d_ws, "ws_frames": ws_bytes["frames"], "http_bytes": d_http,
                 "mem_samples": samples}, indent=1))
            print(f"\n-> {a.json}")
            br.close()
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
