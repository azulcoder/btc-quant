// terminal-hfdata.js — archived-day reader over the public HF dataset (I-1
// Wave 2, DESIGN-orderflow-terminal.md §4f).
//
// §4f contract: the collector's HF lifecycle (§3c) archives every closed UTC
// day to hf://datasets/azulcoder/btc-quant-ticks as hive-partitioned ZSTD
// parquet (`data/date=YYYY-MM-DD/<table>.parquet`, scripts/upload_hf.py). The
// resolve endpoint 302-redirects to a signed CDN URL and the CORS headers echo
// the requesting origin (§4f empirical basis, probed 2026-07-10 — the redirect
// response carries access-control-allow-origin), so the BROWSER can read any
// ARCHIVED day directly — no server, no auth, no bundler. Parsing is done by
// dashboard/vendor/hyparquet.js (hyparquet 1.26.2 + fzstd ZSTD codec, MIT —
// provenance + build documented in that file's header).
//
// Empirical data map (probed 2026-07-10 against the REAL dataset, not docs):
//   - Resolve URL wants the hive `=` percent-encoded: …/data/date%3DDATE/….
//   - hyparquet returns parquet BIGINT columns (ts_ms, next_funding_ts,
//     expiry_ts) as JS **BigInt** — BYOD rows speak Number, so the normalizer
//     coerces every bigint via Number() (exact below 2^53 ≈ year 287396 in ms;
//     stated, not assumed). DOUBLE→number, BOOLEAN→boolean, VARCHAR→string
//     arrive correct as-is (verified on dvol + trades 2026-07-05).
//   - The tree API (`/api/datasets/<repo>/tree/main/<path>`, keyless for
//     public datasets) lists date partitions and per-file byte sizes — the
//     HONEST size warning the UI must show before loading (trades for
//     2026-07-05 is 28,760,032 bytes ≈ 27.4 MiB; a click that big must say so
//     first — same gate idiom as WhaleView's 33 MB leaderboard fetch, §4d).
//
// WHY whole-file fetch + streamed progress (not hyparquet's ranged
// asyncBufferFromUrl): one signed redirect instead of a re-302 per row-group
// range request, and a byte-true onProgress for the size-warning UX. The
// honest consequence, stated: `columns` projection then cuts PARSE work only
// (hyparquet skips decoding unrequested column chunks) — the bytes are already
// down the wire. Row counts are NOT affected by projection: parquet stores
// num_rows per row group, so a projected read returns the same row count.
//
// Row vocabulary: rows are normalized to the BYOD row shapes — the collector's
// §3 schema columns, snake_case, ts in ts_ms (trades: {exchange, symbol,
// trade_id, ts_ms, price, qty, aggressor_buy}) — so terminal-replay.js's
// byodRowToEvent() consumes them UNCHANGED. All §0.6 aggressor inversions
// happened at record time; nothing here re-interprets sides.
//
// Honesty rails (DESIGN §0): archived rows are RECORDED HISTORY, not live —
// every consumer labels the render 'archived day · hf dataset' (§4f) and
// nothing fetched here ever feeds a backtest or the OOS harness (§0.1). ts_ms
// values pass through UNCHANGED (§0.7 — no rebasing, no fabrication); a failed
// fetch throws to the caller, which renders the failure — never a substitute
// series. Keyless public endpoints only (§0.2).
//
// No DOM access, no globals beyond the ONE export — testable in Node (≥18 has
// global fetch; the §4f proof run uses it, no polyfill) via the quant.js
// dual-export pattern.
'use strict';

(function (global) {
  var HF_ORIGIN = 'https://huggingface.co';
  var DEFAULT_REPO = 'azulcoder/btc-quant-ticks';   // §3c dataset repo
  var FETCH_TIMEOUT_MS = 120000;   // 27 MiB on a slow link beats the §4c 30 s idiom — generous, abortable
  var TREE_TIMEOUT_MS = 10000;     // tree API answers are tiny JSON
  var TREE_PAGE_CAP = 20;          // Link-header pagination bound (1000 entries/page — 20k days ≈ 54y)

  // Path-segment validators — these strings are interpolated into URLs, so they
  // are allowlisted BY CONSTRUCTION (mirrors upload_hf.py's _TABLE_NAME_RE and
  // the hive date layout; anything else is a caller bug, thrown loudly).
  var DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  var TABLE_RE = /^[a-z_][a-z0-9_]*$/;
  var REPO_RE = /^[A-Za-z0-9][\w.-]*\/[\w.-]+$/;

  /** Vendored hyparquet handle: browser = the classic-script global (vendor tag
   * must precede this file), Node = require() of the SAME bytes. Resolved
   * lazily so `node --check` and normalizer-only Node use never demand it. */
  function hyp() {
    var h = global.hyparquet;
    if (!h && typeof require === 'function') {
      try { h = require('./vendor/hyparquet.js'); } catch (_) { /* fall through to the throw */ }
    }
    if (!h || typeof h.parquetReadObjects !== 'function') {
      throw new Error('hyparquet vendor missing — load dashboard/vendor/hyparquet.js before terminal-hfdata.js');
    }
    return h;
  }

  function checkSeg(re, s, what) {
    if (typeof s !== 'string' || !re.test(s)) throw new Error('bad ' + what + ': ' + s);
    return s;
  }

  /** Signed-CDN resolve URL for one archived table. The hive `=` is sent as
   * %3D (probed: the resolve router wants the encoded form; the tree API
   * accepts either). Exported — the UI's size-warning dialog links it. */
  function archivedParquetUrl(repo, date, table) {
    checkSeg(REPO_RE, repo, 'repo'); checkSeg(DATE_RE, date, 'date'); checkSeg(TABLE_RE, table, 'table');
    return HF_ORIGIN + '/datasets/' + repo + '/resolve/main/data/date%3D' + date + '/' + table + '.parquet';
  }

  // ─── Pure normalization (the testable core) ────────────────────────────────

  /**
   * One hyparquet row object → one BYOD-shaped row. PURE + exported. The ONLY
   * type mismatch between parquet decode and the BYOD JSON rows is parquet
   * BIGINT → JS BigInt (empirical map above), so this is exactly that
   * coercion, key-agnostic: projection-safe (absent columns stay absent) and
   * schema-drift-safe (a v3 table with new int64 columns still normalizes).
   * Everything else — including depth bids/asks, which the collector stores as
   * JSON STRINGS (§3) — passes through untouched; byodRowToEvent() owns the
   * event mapping and the malformed-row drops, exactly as it does for the live
   * BYOD API (one vocabulary, one owner — no duplicated validation here).
   */
  function normalizeArchivedRow(raw) {
    if (!raw || typeof raw !== 'object') return null;
    var row = {};
    for (var k in raw) {
      var v = raw[k];
      row[k] = typeof v === 'bigint' ? Number(v) : v;
    }
    return row;
  }

  // ─── Fetchers (thin, abortable, throw-on-failure) ──────────────────────────

  /** fetch with an AbortController timeout — the terminal-hist.js idiom, but
   * throwing instead of silent-null: an archived-day load is user-initiated
   * and its failure must be SHOWN, not blanked (§0.7 no substitute series). */
  async function timedFetch(url, ms) {
    var ctl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = ctl ? setTimeout(function () { ctl.abort(); }, ms) : null;
    try {
      // mode:'cors' is explicit: the WHOLE feature rests on the §4f CORS proof.
      var res = await fetch(url, { mode: 'cors', signal: ctl ? ctl.signal : undefined });
      if (!res.ok) throw new Error('HTTP ' + res.status + ' for ' + url);
      return res;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  /**
   * Read one archived table for one UTC day straight from the HF dataset.
   * Returns an array of BYOD-shaped row objects (see header).
   *
   * opts: { columns:[names] — parse-side projection (parse-only saving, see
   * header WHY); onProgress(p) — called with {phase:'download', received,
   * total|null} per chunk then {phase:'parse', rows, ms} once; timeoutMs }.
   * The 27 MiB-class warning is the CALLER's job BEFORE calling this
   * (listArchivedTables serves the byte sizes); this function just reports
   * progress honestly.
   */
  async function fetchArchivedTable(repo, date, table, opts) {
    var o = opts || {};
    var h = hyp();
    var res = await timedFetch(archivedParquetUrl(repo || DEFAULT_REPO, date, table),
      o.timeoutMs || FETCH_TIMEOUT_MS);

    // Stream the body so onProgress is byte-true. `total` is the FINAL
    // response's content-length (the CDN's, post-redirect) — null when the
    // wire doesn't say (chunked), reported as such rather than guessed.
    var buf;
    if (res.body && typeof res.body.getReader === 'function') {
      var total = Number(res.headers.get('content-length')) || null;
      var reader = res.body.getReader();
      var chunks = [], received = 0;
      for (;;) {
        var step = await reader.read();
        if (step.done) break;
        chunks.push(step.value);
        received += step.value.byteLength;
        if (o.onProgress) o.onProgress({ phase: 'download', received: received, total: total });
      }
      var flat = new Uint8Array(received), off = 0;
      for (var i = 0; i < chunks.length; i++) { flat.set(chunks[i], off); off += chunks[i].byteLength; }
      buf = flat.buffer;
    } else {
      // No ReadableStream (some Node fetch shims) — arrayBuffer() fallback,
      // progress reduces to one final report. Honest, just coarser.
      buf = await res.arrayBuffer();
      if (o.onProgress) o.onProgress({ phase: 'download', received: buf.byteLength, total: buf.byteLength });
    }

    var t0 = Date.now();   // wall-ms for the parse report only — never a data timestamp (§0.7 untouched ts_ms)
    var raw = await h.parquetReadObjects({
      file: buf,
      columns: Array.isArray(o.columns) ? o.columns : undefined,
      compressors: h.compressors,   // ZSTD — upload_hf.py's codec (§3c)
    });
    var rows = [];
    for (var r = 0; r < raw.length; r++) {
      var row = normalizeArchivedRow(raw[r]);
      if (row) rows.push(row);
    }
    if (o.onProgress) o.onProgress({ phase: 'parse', rows: rows.length, ms: Date.now() - t0 });
    return rows;
  }

  /** GET one tree-API page list, following Link rel="next" pagination up to
   * TREE_PAGE_CAP pages (1000 entries/page — the cap is a runaway guard, not
   * an expected limit). */
  async function treeList(repo, subpath) {
    checkSeg(REPO_RE, repo, 'repo');
    var url = HF_ORIGIN + '/api/datasets/' + repo + '/tree/main/' + subpath;
    var out = [];
    for (var page = 0; url && page < TREE_PAGE_CAP; page++) {
      var res = await timedFetch(url, TREE_TIMEOUT_MS);
      var body = await res.json();
      if (!Array.isArray(body)) throw new Error('unexpected tree response for ' + url);
      out = out.concat(body);
      var link = res.headers.get('link');
      var m = link && /<([^>]+)>;\s*rel="next"/.exec(link);
      url = m ? m[1] : null;
    }
    return out;
  }

  /**
   * All archived UTC days in the dataset, ascending — feeds the
   * AuctionProfileView day selector next to the local /v1/levels days.
   * Directories that are not date partitions are ignored BY PATTERN, never
   * guessed at.
   */
  async function listArchivedDates(repo) {
    var entries = await treeList(repo || DEFAULT_REPO, 'data');
    var dates = [];
    for (var i = 0; i < entries.length; i++) {
      var e = entries[i];
      var m = e && e.type === 'directory' && typeof e.path === 'string'
        && /^data\/date=(\d{4}-\d{2}-\d{2})$/.exec(e.path);
      if (m) dates.push(m[1]);
    }
    return dates.sort();
  }

  /**
   * Tables + BYTE SIZES for one archived day → [{table, bytes}] — the honest
   * size warning's data source (§4f: the UI states the download before a
   * 27 MiB-class fetch, same gate as the §4d leaderboard click).
   */
  async function listArchivedTables(repo, date) {
    checkSeg(DATE_RE, date, 'date');
    var entries = await treeList(repo || DEFAULT_REPO, 'data/date%3D' + date);
    var tables = [];
    for (var i = 0; i < entries.length; i++) {
      var e = entries[i];
      var m = e && e.type === 'file' && typeof e.path === 'string'
        && /\/([a-z_][a-z0-9_]*)\.parquet$/.exec(e.path);
      if (m) tables.push({ table: m[1], bytes: Number(e.size) || 0 });
    }
    return tables;
  }

  // ─── Export (ONE global + Node dual-export, quant.js pattern) ───────────────
  const HFDATA = {
    DEFAULT_REPO,
    archivedParquetUrl,
    normalizeArchivedRow,
    fetchArchivedTable,
    listArchivedDates,
    listArchivedTables,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = HFDATA;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_HFDATA = HFDATA;
})(typeof globalThis !== 'undefined' ? globalThis : this);
