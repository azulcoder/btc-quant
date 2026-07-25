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
    // violation. The adapter stamps lastAliveAt via api.markAlive() on every ticker/
    // heartbeat frame (NOT trades — a quiet market_trades window is normal). While the
    // socket is OPEN we flip to amber "stale" after STALE_MS, and force ONE reconnect
    // after DEAD_MS which routes through the EXISTING backoff (we never fight it).
    let lastAliveAt = 0, stale = false, forcedDead = false, wdTimer = null;
    const MAX_BACKOFF = 30000, STALE_MS = 12000, DEAD_MS = 40000, WATCHDOG_MS = 2000;

    function clearHeartbeat() { if (hbTimer) { clearInterval(hbTimer); hbTimer = null; } }

    // N5: OPTIONAL silent-catch telemetry. A frame the socket had to swallow is
    // otherwise invisible; a caller may pass api.onDropped(reason) to count it.
    // Guarded + try-wrapped: absent (app.js) → nothing runs, byte-unaffected; a
    // throwing onDropped can NEVER kill the socket (the whole point of the two
    // onmessage catches is that no bad frame — or bad telemetry — takes it down).
    function drop(reason) { if (api.onDropped) { try { api.onDropped(reason); } catch (_) { /* telemetry never kills the socket */ } } }

    // Adapter calls this on a healthy-feed frame (ticker tick / heartbeat). Recovery
    // is a single clean transition back to green — no flicker.
    const liveApi = Object.assign({}, api, {
      markAlive() {
        lastAliveAt = Date.now();
        if (stale) { stale = false; forcedDead = false; api.onStatus('open', 'live feed recovered'); }
      },
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
        lastAliveAt = Date.now();          // fresh liveness baseline → no instant false-stale
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
        let msg; try { msg = JSON.parse(ev.data); } catch (_) { drop('parse'); return; }
        try { adapter.onMessage(msg, liveApi); } catch (_) { drop('handler'); /* never let a bad frame kill the socket */ }
      };
      ws.onerror = () => { /* onclose fires next; handled there */ };
      ws.onclose = () => { clearHeartbeat(); scheduleReconnect(); };
    }

    // Judge ONLY an OPEN socket — a CONNECTING/closed one is the backoff's job, so the
    // watchdog never double-drives reconnection. One interval for the socket's lifetime.
    function startWatchdog() {
      if (wdTimer) return;
      wdTimer = setInterval(() => {
        if (closedByUs || !ws || ws.readyState !== WebSocket.OPEN) return;
        const gap = Date.now() - lastAliveAt;
        if (gap >= DEAD_MS) {
          if (!forcedDead) {
            forcedDead = true;
            api.onStatus('reconnecting', 'live feed stalled — reconnecting');
            try { ws.close(); } catch (_) { /* onclose → scheduleReconnect (existing backoff) */ }
          }
          return;
        }
        if (gap >= STALE_MS) { stale = true; api.onStatus('stale', `stale — no data for ${Math.round(gap / 1000)}s`); }
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
