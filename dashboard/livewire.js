// livewire.js — shared reconnect/backoff/watchdog WebSocket plumbing.
//
// ONE implementation of `makeSocket(adapter, api)` used by BOTH dashboard
// pages: app.js (analytics page, index.html) and terminal.js (orderflow
// terminal, terminal.html) — see DESIGN-orderflow-terminal.md §4. Extracted
// VERBATIM from app.js (where it grew alongside the live tape, brief §3.3)
// so reconnect semantics, backoff caps and the liveness watchdog can never
// drift apart between the two pages. Behavior change vs the inline app.js
// version: none.
//
// PUBLIC channels only — no keys, no signing. This module never fabricates
// data; it only moves frames and reports honest connection status
// (open / stale / reconnecting / error) through api.onStatus.
//
// OPTIONAL telemetry (N5): a caller MAY pass api.onDropped(reason) to observe
// the frames the socket had to swallow — reason 'parse' (JSON.parse failed) or
// 'handler' (adapter.onMessage threw). Silent when absent: app.js passes no
// onDropped, so its path is byte-unaffected; the terminal passes one to feed
// the header health chip. Observability only — it never changes what is moved.
//
// OPTIONAL telemetry (T-4 R1): a caller MAY pass api.onClosed({code, reason,
// clean, by}) to observe WHY a socket ended. ws.onclose used to discard the
// CloseEvent entirely, so the venue's own close code/reason was never
// observable and "the venue dropped us" could not be told apart from "we
// closed it ourselves" or "the feed silently stalled and the watchdog forced a
// reconnect". `by` names the closer — 'us' (handle.close(), e.g. a symbol
// switch), 'watchdog' (our own DEAD_MS force-reconnect) or 'venue' — because a
// close WE caused must never be counted as a venue drop. Same guarded +
// try-wrapped shape as onDropped: absent (app.js) → nothing runs.
//
// OPTIONAL adapter hook (T-4 R2): adapter.isControlFrame(rawText) → boolean,
// consulted ONLY inside the JSON.parse catch (see ws.onmessage). Absent →
// today's behavior verbatim.
//
// LIVENESS API given to adapters (T-4 R2): api.markAlive() for a DATA frame and
// api.markControlAlive() for a venue's own keepalive REPLY. They are different
// facts and drive different clocks — see the two-stamp note below. An adapter
// that only ever calls markAlive() (every adapter before T-4, and app.js's)
// behaves exactly as it always did.
'use strict';

(function (global) {
  // Shared reconnect skeleton: capped exponential backoff + jitter, re-subscribe
  // on every reopen, ping/heartbeat timer gated to the socket lifecycle (§3.3).
  // An adapter supplies { url, subscribe(ws), onMessage(msg, api), ping }.
  function makeSocket(adapter, api) {
    let ws = null, attempt = 0, hbTimer = null, closedByUs = false;
    // Module D — liveness watchdog. A socket can stay OPEN while the feed silently
    // stalls (proxy, dozing tab, dropped subscription); onclose never fires, so the
    // status would otherwise stay green "live" over a frozen price — an honesty-rail
    // violation. The adapter stamps lastDataAt via api.markAlive() on every ticker/
    // heartbeat frame (NOT trades — a quiet market_trades window is normal). While the
    // socket is OPEN we flip to amber "stale" after STALE_MS, and force ONE reconnect
    // after DEAD_MS which routes through the EXISTING backoff (we never fight it).
    //
    // T-4 R2 — TWO liveness stamps, not one, and only ONE of them is a verdict.
    //   lastDataAt  = "is the SUBSCRIPTION still delivering?" — DATA frames only.
    //                 It drives BOTH verdicts: amber 'stale' and the DEAD_MS
    //                 force-reconnect.
    //   lastAliveAt = "is this socket still answering at all?" — data frames AND
    //                 a venue's keepalive REPLY. DIAGNOSTIC ONLY: it enriches
    //                 the stale message and never retracts it.
    // They are split because a control frame proves the socket, NOT the
    // subscription. OKX pongs every 25s and Bybit v5 answers our op:'ping' every
    // 15s, so on a single-stamp watchdog a leg whose books/publicTrade
    // subscription had died would keep the gap under STALE_MS=12000 and
    // DEAD_MS=40000 forever: neither the amber chip nor the force-reconnect
    // could EVER fire — the exact silent-stale hole Module D exists to close.
    // Letting a keepalive clear 'stale' had the same shape one level up: the
    // chip would have gone green with "live feed recovered" on a leg that had
    // delivered nothing, a positive claim about DATA that a pong cannot support.
    // Adapters with no control frames stamp both clocks together through
    // markAlive(), so their behavior is byte-identical to before.
    let lastAliveAt = 0, lastDataAt = 0, stale = false, forcedDead = false, wdTimer = null;
    const MAX_BACKOFF = 30000, STALE_MS = 12000, DEAD_MS = 40000, WATCHDOG_MS = 2000;

    function clearHeartbeat() { if (hbTimer) { clearInterval(hbTimer); hbTimer = null; } }

    // N5: OPTIONAL silent-catch telemetry. A frame the socket had to swallow is
    // otherwise invisible; a caller may pass api.onDropped(reason) to count it.
    // Guarded + try-wrapped: absent (app.js) → nothing runs, byte-unaffected; a
    // throwing onDropped can NEVER kill the socket (the whole point of the two
    // onmessage catches is that no bad frame — or bad telemetry — takes it down).
    function drop(reason) { if (api.onDropped) { try { api.onDropped(reason); } catch (_) { /* telemetry never kills the socket */ } } }

    // T-4 R1: OPTIONAL close telemetry, the exact guarded/try-wrapped shape as
    // drop(). `by` is decided HERE because only this closure knows who pulled
    // the plug — closedByUs (handle.close(): a symbol switch or page teardown)
    // and forcedDead (our own DEAD_MS reconnect) both route through the SAME
    // ws.onclose, and counting either as a venue drop would make the telemetry
    // lie about the venue. Nothing browser-specific is read here: livewire must
    // stay constructible in Node (the check groups drive it with a WS stub), so
    // page-state context like document.visibilityState is the CALLER's to add.
    function reportClose(ev) {
      if (!api.onClosed) return;
      try {
        api.onClosed({
          code: (ev && Number.isFinite(ev.code)) ? ev.code : null,
          reason: (ev && typeof ev.reason === 'string') ? ev.reason : '',
          clean: !!(ev && ev.wasClean),
          by: closedByUs ? 'us' : forcedDead ? 'watchdog' : 'venue',
        });
      } catch (_) { /* telemetry never kills the socket */ }
    }

    // The two liveness entry points the adapters see.
    //
    // markAlive(evidence: a DATA frame — ticker tick, book update, heartbeat
    // channel) stamps BOTH clocks: data proves the subscription, which implies
    // the socket. It is also the ONLY thing that may retract 'stale', because
    // recovery is a claim about the FEED and only a data frame is evidence
    // about the feed. Single clean transition back to green — no flicker.
    //
    // markControlAlive(evidence: the venue's own keepalive REPLY — OKX's
    // plain-text 'pong' via isControlFrame, Bybit v5's JSON {ret_msg:'pong'}
    // via its adapter) stamps the ANSWERING clock and nothing else: no status,
    // no 'stale' retraction, no forcedDead reset. A pong is not data.
    const liveApi = Object.assign({}, api, {
      markAlive() {
        lastDataAt = lastAliveAt = Date.now();
        if (stale) { stale = false; forcedDead = false; api.onStatus('open', 'live feed recovered'); }
      },
      markControlAlive() { lastAliveAt = Date.now(); },
    });

    function scheduleReconnect() {
      clearHeartbeat();
      if (closedByUs) return;
      // capped exponential backoff (1s,2s,4s,…,30s) + up to 1s jitter.
      const base = Math.min(MAX_BACKOFF, 1000 * Math.pow(2, attempt));
      const delay = base + Math.random() * 1000;
      attempt++;
      api.onStatus('reconnecting', `live feed dropped — retrying in ${(delay / 1000).toFixed(0)}s`);
      setTimeout(connect, delay);
    }

    function connect() {
      if (closedByUs) return;
      try { ws = new WebSocket(adapter.url); }
      catch (e) { api.onStatus('error', 'live feed unavailable (' + e.message + ')'); scheduleReconnect(); return; }

      ws.onopen = () => {
        attempt = 0;                       // reset backoff on a clean open
        lastAliveAt = lastDataAt = Date.now();   // fresh liveness baseline (both clocks) → no instant false-stale
        stale = false; forcedDead = false;
        api.onStatus('open', 'live feed connected');
        try { adapter.subscribe(ws); }     // (re-)subscribe on EVERY (re)open
        catch (_) { /* subscribe error -> socket will close, backoff handles it */ }
        // Lifecycle-gated heartbeat: only ticks while THIS socket is open.
        if (adapter.ping) {
          clearHeartbeat();
          hbTimer = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) { try { adapter.ping(ws); } catch (_) { /* ignore */ } }
          }, adapter.pingMs || 20000);
        }
      };
      ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); }
        catch (_) {
          // T-4 R2 (regression from N5, afe817f): a venue whose keepalive REPLY is
          // not JSON — OKX answers the plain text 'pong' — lands in this catch BY
          // CONSTRUCTION. It is a CONTROL frame, not a dropped frame; counting it
          // made a healthy terminal wear a permanent amber "degraded: N dropped"
          // chip (N5's own normSkip deferral — "counting routine hygiene would
          // break silent-when-healthy" — is the precedent that should have
          // covered it). The predicate is consulted INSIDE the catch on purpose:
          // running it BEFORE the parse would fire on every frame across 7 legs
          // of 100ms book updates, while here the happy path is byte-identical.
          // A predicate that THROWS falls through to drop('parse') — we could not
          // classify the frame, so we say so rather than guess it was benign.
          if (adapter.isControlFrame) {
            let ctrl = false;
            try { ctrl = !!adapter.isControlFrame(ev.data); } catch (_) { ctrl = false; }
            if (ctrl) { liveApi.markControlAlive(); return; }
          }
          drop('parse'); return;
        }
        try { adapter.onMessage(msg, liveApi); } catch (_) { drop('handler'); /* never let a bad frame kill the socket */ }
      };
      ws.onerror = () => { /* onclose fires next; handled there */ };
      // reportClose BEFORE scheduleReconnect: the telemetry describes the socket
      // that just died, and scheduleReconnect can synchronously report a status.
      ws.onclose = (ev) => { clearHeartbeat(); reportClose(ev); scheduleReconnect(); };
    }

    // Judge ONLY an OPEN socket — a CONNECTING/closed one is the backoff's job, so the
    // watchdog never double-drives reconnection. One interval for the socket's lifetime.
    function startWatchdog() {
      if (wdTimer) return;
      wdTimer = setInterval(() => {
        if (closedByUs || !ws || ws.readyState !== WebSocket.OPEN) return;
        const now = Date.now();
        // BOTH verdicts read the DATA clock (T-4 R2). Amber means "no data",
        // dead means "no data for so long the subscription must be re-made" —
        // and a socket that only pongs is not a delivering subscription, so it
        // must be able to reach both. That is the whole point of the split:
        // otherwise the two venues that answer our ping (OKX, Bybit v5) are the
        // ONLY ones the watchdog cannot judge, and they are the primary venue
        // and the measured-flakiest legs.
        const gap = now - lastDataAt;
        if (gap >= DEAD_MS) {
          if (!forcedDead) {
            forcedDead = true;
            api.onStatus('reconnecting', 'live feed stalled — reconnecting');
            try { ws.close(); } catch (_) { /* onclose → scheduleReconnect (existing backoff) */ }
          }
          return;
        }
        if (gap >= STALE_MS) {
          stale = true;
          // lastAliveAt is DIAGNOSTIC here and nowhere else: it separates "the
          // whole socket went quiet" from "the socket still answers keepalives
          // but its subscription stopped delivering". Two different failures
          // with two different fixes, and the chip is where a reader learns
          // which — the message still names the DATA gap, never the pong.
          const answering = (now - lastAliveAt) < STALE_MS;
          api.onStatus('stale', `stale — no data for ${Math.round(gap / 1000)}s`
            + (answering ? ' (socket still answering)' : ''));
        }
      }, WATCHDOG_MS);
    }

    connect();
    startWatchdog();
    return {
      close() {
        closedByUs = true; clearHeartbeat();
        if (wdTimer) { clearInterval(wdTimer); wdTimer = null; }
        if (ws) try { ws.close(); } catch (_) { /* ignore */ }
      },
    };
  }

  const BTCQ_LIVEWIRE = { makeSocket };

  // Dual export (quant.js pattern): window global for the browser pages,
  // module.exports so the node fixture smoke (scripts/check_terminal.cjs)
  // can load it in a vm sandbox without a bundler.
  if (typeof module !== 'undefined' && module.exports) module.exports = BTCQ_LIVEWIRE;
  if (typeof global !== 'undefined') global.BTCQ_LIVEWIRE = BTCQ_LIVEWIRE;
})(typeof globalThis !== 'undefined' ? globalThis : this);
