# Strategy & Roadmap — btc-quant order-flow terminal

Living document. Tracks where the terminal genuinely stands, the moat to protect, the
honest gaps, and a staged, checkable roadmap toward the long-term vision. Every claim
is grounded to a file or measured behaviour; every roadmap item carries acceptance
criteria and, where technical, the reference to ground the implementation before code
is written. Binding design + honesty rails live in `DESIGN-orderflow-terminal.md`;
this file is the plan, not the spec.

Method for every item below: research first (trusted, cited references), implement
all-out and safe, then verify in detail (does it match, what is missing, what is the
follow-up, where is the room for improvement). Converge fast, solve precisely.

---

## 1. Where this stands (honest verdict)

Strong foundation, not amateur. Already best-in-class on two specific axes —
honesty/provenance-labeling and validation-harness coupling — but not best-in-class in
general, and the research flywheel that is the whole reason the project exists does not
yet close.

- **Language / runtime — the right choice, on track.** Vanilla JS + 2D canvas for the
  UI, Python + asyncio + batched DuckDB for the collector/research. The full-book
  engines meet an O(1)-per-event contract with sorting deferred to a 10 Hz flush
  (`dashboard/terminal-books.js`, `dashboard/terminal.js:1433` `flushBookLegs`); one rAF
  loop with per-view dirty flags + `IntersectionObserver` offscreen gating that gates
  paint but never ingest (`terminal.js:3660/3825`). Two efficiency traps remain, both
  inside our control: `setData(map(...))` full-rebuild instead of `series.update()`
  incremental (throttled to ~600 ms, `terminal-views.js:279/468`), and a single main
  thread with no Worker/OffscreenCanvas.
- **Design — solid, genuinely well-layered.** The store/view/adapter separation holds
  under inspection: stores are pure (zero DOM, `terminal-state.js:3650`), views reach
  zero globals, adapters quarantine each venue quirk. Visual craft (token-only colour,
  numerically-validated diverging delta ramp, badge taxonomy, colour-blind cues) is at
  or above Exocharts/aggr; provenance-labeling is best-in-class. The debt is
  concentration, not sloppiness.
- **Overall vs the vision — split by axis.** Match/surpass aggr/Cryexc (we maintain a
  full local book; aggr keeps no book). Cannot surpass Bookmap/Sierra/Exocharts — they
  are native-execution, L3/MBO (queue position, real iceberg/stop execution),
  colocated. That is a permanent category boundary, not a gap. As a research workbench,
  near peerless: no named competitor couples a footprint terminal to a
  walk-forward/DSR/PBO/MinBTL/CPCV harness plus a recorded tick archive.

Positioning claim that is defensible and improvable: **the most honest,
validation-coupled crypto order-flow research workbench that informs execution in a
separate native engine** — not "the most advanced order-flow terminal" in the absolute,
and not an execution engine.

## 2. The moat (protect this)

1. **Honesty rails enforced in CI, not in prose.** `heuristic`/`estimated` labels and
   the IC-honesty sentences are asserted VERBATIM via `assert.strictEqual` in the build
   gate (`scripts/check_terminal.cjs`, 77 assertion groups). Dropping or rewording a
   label fails the build. The confluence board states its own forward-IC ≈ 0 as a
   permanent label (`terminal-state.js:1595`, `RESEARCH-ic-runlog.md`). A competitor
   that smooths or fabricates fails this gate by construction.
2. **Counted-honest-resync.** A book with a known hole is fabricated data if it stays on
   screen: seq gap / broken `pu`-chain / checksum miss → book cleared and resync counted
   to the UI, never silent-patched (`terminal-books.js:234/361`).
3. **Spec checked against real data, then superseded.** The OKX `books` channel carries
   `checksum: 0` on every keyless frame (measured 300+); the CRC32 plan was degenerate,
   so the rail moved to `seqId/prevSeqId` with a `[SUPERSEDED 2026-07-24]` audit trail.
4. **The deflation/validation engine (`btcquant/risk.py`).** PSR, DSR, MinBTL,
   expected-max-Sharpe, False-Strategy-Theorem threshold (fixed-point to machine
   precision), PBO, hierarchical-Bayes Sharpe shrinkage — cited to Bailey-López de Prado,
   single-source-of-truth (`expected_max_sharpe_ratio` shared byte-identical by DSR and
   MinBTL). No incumbent charting terminal has anything equivalent.
   Caveat (state the tense): today this validates OHLCV/options strategies, not
   order-flow. It is a moat **in potential** for this product; the coupling is the
   unbuilt shaft (Gap 1).
5. **Collector → HF lifecycle, production-grade and honestly-gapped.** Event-time daily
   rotation + 5-min grace, immutable closed-day files, re-read + sha256-verify + confirm
   on the Hub before local delete, L3 QA that FAILS on duplicate `trade_id` and reports
   but never fills gaps (`scripts/check_ticks.py`).

The moat is the combination — honesty-enforced-in-code + a validation harness + an
accumulating tick archive. No peer has all three; it is an architecture of value, not a
feature list.

## 3. Honest gaps (ranked by impact on the stated goals)

- **Gap 1 — [critical, vision] the flywheel does not close.** No research/backtest/feature
  code reads the tick store. `btcquant/features.py` (36 funcs) and `strategies.py`
  (18 funcs) are 100% OHLCV/options; `btcquant/orderflow.py` does not exist. Precise:
  the live-descriptive *serving* path already reads ticks
  (`scripts/backfill_levels.py`, `/v1/profile` via `collector.py:1958`), but the
  research → deflation-harness path is missing. The collector accrues history that
  nothing downstream turns into a validated signal.
- **Gap 2 — [blocking deploy] paint loop has zero error isolation.** `frame()`
  (`terminal.js:3825-4099`) has no try/catch around its 32 `view.render()` calls, and
  `scheduleFrame()` is the last statement. One render throw (a NaN into a canvas path,
  an undefined field) freezes the entire terminal, all panels, with no recovery. Ingest
  is hardened (`livewire.js:76`); paint is not.
- **Gap 3 — [uptime, but re-justified] the moat rests on a fragile single-laptop
  collector.** Correct justification: continuity gates (a) feature integrity — an
  event-time/volume-clock bar (VPIN/CVD) cannot be honestly computed across a multi-hour
  hole — and (b) the irreplaceability of un-refetchable public tick data. It does NOT
  gate the MinBTL clock: `check_ticks.sec_readiness` counts `span_days` as calendar-
  inclusive and states "span is calendar time, not uptime" (`check_ticks.py:658-730`).
- **Gap 4 — [maintainability, scales linearly] panel-addition is shotgun surgery.**
  Adding one panel touches ~9-11 parallel string-keyed tables kept in sync by hand
  (`dirty{}`, `MIN_MS{}`, `lastAt{}`, `SEC_OF{}`, `VIEW_ANCHOR{}`, a `V.XxxView()`, a
  `.mount()`, a `frame()` `if(due())` block, plus HTML + CSS) — the exact opposite of
  the single-source `LegRegistry` (`terminal-state.js:3086`). The larger god-file is
  `terminal-views.js` (5465 lines) > `terminal.js` (4138).
- **Gap 5 — [UX category gap] no dockable/resizable/pop-out workspace.** Fixed CSS grid,
  single-scroll ~4982 px. Bookmap/Sierra/Exocharts are workspace applications (drag,
  resize, multi-monitor, pop-out). This is the "monitoring page vs workspace
  application" difference.
- **Gap 6 — [efficiency, in our control]** `setData` full-rebuild + single-thread
  ceiling (see §1).
- **Gap 7 — [a11y regression, cheap, pre-deploy]** the CVD-safe (Okabe-Ito) and density
  toggles exist on the analytics page (`app.js:1897-1907`) but are absent on the
  terminal; a colour-blind user lands on red/green footprint/heatmap with no escape. The
  draw-time engine already supports it (`terminal-views.js:18/130`); only the control is
  missing.
- **Gap 8 — [minor]** no orchestrator unit test (`terminal.js` glue is only covered
  e2e); silent catches with no telemetry (`livewire.js:76`); pure-util duplication
  (`finiteOr/posOr/makeRing` byte-identical in two modules); `--g-400 ~2.9:1` below WCAG
  AA used for readable text; deploy hygiene (`mlflow.db`, `mlruns/`, a 133 MB
  `.duckdb.wal.checkpoint` in the tree).

## 4. The execution boundary (HFT / MM reality)

The substrate — a GC'd single-threaded JS event loop, paint at rAF cadence, fed by
keyless public WS (`depth@100ms` is 100 ms-batched-stale by definition), `Date.now()`
timing, zero order-entry (verified: no signing/order/hmac in `dashboard/terminal*.js`) —
is eyes, not hands. Live-display latency floor ~100 ms (the archived mark is downsampled
to 1 s — a storage resolution, not live lag).

- Order-flow display + research: the ceiling is invisible, far below human read-latency.
  Can be best-in-class.
- Discretionary support: fits (human reaction dwarfs terminal latency), with the honest
  caveat of zero order entry by design.
- Market-making: can be a monitor/analytics surface (VPIN/OFI/microprice/walls are real
  MM inputs) but cannot be a quoting engine — a quote/cancel loop needs low-single-digit-
  ms deterministic reaction that rAF-throttled canvas cannot provide. And the exportable
  features are the L2-observable subset only; queue position, true iceberg/stop
  execution, and L3/MBO — the signals that carry real MM edge — are exactly what a
  keyless 100 ms-batched L2 feed cannot reconstruct.
- HFT: does not fit and cannot be made to fit. A serious path needs sub-µs tick-to-trade,
  exchange colocation, kernel-bypass NIC (DPDK / ef_vi), often FPGA, C++/Rust lock-free
  structures, a direct authenticated feed — a separate native process, not an increment
  of `terminal*.js`.

Reframe (ambitious but real): serve the humans and the research pipeline that inform
HFT/MM execution in a separate native engine. The contract between the terminal and any
future execution engine is two things: a normalized-event schema and a CLEARED-signal
registry.

## 5. Roadmap (checkable)

Ordering principle: build every rail now while the MinBTL clock runs; wire the payoff
last. Payoff is calendar-gated (recorded history vs `MinBTL(N)` ≈ months-to-years), so
sequence to the calendar, not to enthusiasm.

### NEAR — deploy-hardening + honesty rescope (weeks, all high-impact / low-effort)

- [x] **N1. Isolate the paint loop, with quarantine.** (done 2026-07-25 — pure
      `makePanelGuard` circuit breaker + `safePanel` per-panel boundary + top-level
      `finally` so `scheduleFrame()` always re-arms + honest dead-panel chip + guarded
      fault-injection hook; check gate 73→74.) Wrap each `view.render()` so a
      throw is caught and rate-limit-logged per panel, and `scheduleFrame()` always runs
      in a `finally`. Disable a panel after N consecutive throws and surface a dead-panel
      chip (not catch-and-retry, which spin-loops and hides a broken panel). *Accept:* a
      deliberately-throwing panel goes stale + flagged while every other panel keeps
      painting; harness group added. Blocking before deploy.
- [x] **N2. Rescope `DESIGN` §0 — name the execution wall.** (done 2026-07-25 — added
      rail `§0.8` "The terminal is an OBSERVATION surface, not an execution venue", peer to
      the §0.1 backtest wall; README positioning reframed to the research-workbench claim.)
      Add an explicit rail, peer
      to the existing backtest wall (§0.1): observation/research surface, not an execution
      venue; informed strategies execute elsewhere (a separate native process). Separate
      "surpass Exocharts/aggr for display" from "not an HFT engine" — both stated, never
      conflated. *Accept:* §0 states the boundary; README positioning line updated.
- [x] **N3. Port CVD-safe + density toggles to the terminal.** (done 2026-07-25 —
      CVD-safe (Okabe-Ito) + density toggles in the topbar + Cmd-K + shortcuts, sharing
      the analytics `btcq-cvd`/`btcq-density` LS keys so the preference is cross-page.
      Research beat caught the real bug empirically: the class must key on
      `documentElement` (not `<body>`) or the canvas palette reader — which reads vars
      off `:root` at draw time — never sees it; a body-only toggle is a silent no-op.
      Presentation-only, proven by 17 store counts byte-identical across the toggle; the
      standing browser harness gained an `--a11y` pass that also guards the Okabe-Ito
      literal against drift between styles.css and terminal.css.) Mirror `applyCvd`/
      `applyDensity` from `app.js`, read `btcq-cvd`/`btcq-density` at boot, expose in the
      settings row + Cmd-K + shortcuts. *Accept:* colour-blind mode visibly changes
      footprint/heatmap on the terminal and persists; browser-harness screenshot verifies.
- [ ] **N4. Collector always-on, supervised.** launchd/systemd agent + prevent AC sleep,
      or the GCP VM (kit already in `deploy/gcp/`), with `check_ticks` as a standing weekly
      gate. Justified on feature-integrity + data-irreplaceability. *Accept:* one week of
      continuous capture with the gap census clean. Priority #1 functional item for
      "before server deploy."
- [x] **N5. Surface the silent-catch counter** (done 2026-07-26 — `makeHealthCounter`
      accumulator + an OPTIONAL `api.onDropped(reason)` hook on the SHARED livewire socket
      (app.js passes none → byte-unaffected), wired once in `startLeg`; livewire's two
      onmessage catches now report `parse`/`handler` drops instead of swallowing them
      silently. Header `.st-health` chip is silent at count 0 and shows amber
      `degraded: N dropped · M render faults` only when real; rate-limited to the header
      cadence (`due()`/`MIN_MS`), observability-only. Folds in both N1 residuals: (a) the
      ingest latch now cascades a `frozen` st-stale chip to the six prologue-fed views
      (`heat/micro/liqmap/det/walls/conf`) — agg/dom/vpin/alerts excluded, grounded: each
      stays sink-fed-live or is a log, so a chip there would cry wolf; (b) the amber render-
      fault count reads the guards' own `stats().failures` for LIVE (non-latched) guards, so
      a non-latching flap is surfaced once, a dead panel only via its red chip. REPLAY-gated
      proof seams `?drop` / `?flap` / `?fault=ingest`, all inert in production; deferred the
      adapter `normSkip` kind (per-level non-finite skip is routine hygiene, not a fault —
      counting it would break silent-when-healthy; one-line add if data ever shows it firing).
      Check gate 74→77.) Folds in the N1 residuals (N1 set this up — `guard.stats().failures`
      already accrues, ready to read): (a) a persistent ingest-prologue fault still
      freezes the flush-derived views (DOM/heat/agg/liq/micro) — cascade a stale chip to
      them, not only the header; (b) a non-latching (alternating throw/success) render
      fault never trips the consecutive breaker and today emits no telemetry — surface it
      via this counter so a recurring intermittent fault is visible, not silent.

### MID — close the flywheel + collapse the maintainability tax (1-3 months)

- [ ] **M1. Build the keystone `btcquant/orderflow.py`.** Read day-file/HF parquet, emit
      event-time order-flow bars in the SAME DataFrame contract `features.py` uses, so
      `backtest.walk_forward`/`cpcv`/`DSR`/`PBO` consume it with zero harness change.
      Features and their references (research-first, verify each numerically vs an
      independent computation before it enters the harness — the `risk.py` fixed-point +
      parity discipline):
      - CVD (cumulative signed volume) — from real aggressor flags.
      - OFI — Cont, Kukanov, Stoikov, "The Price Impact of Order Book Events" (2014).
      - VPIN — Easley, López de Prado, O'Hara, "Flow Toxicity and Liquidity in a
        High-Frequency World" (RFS 2012); volume clock. Carry the contested-interpretation
        note (Andersen-Bondarenko).
      - Microprice — Stoikov, "The Micro-Price" (2018).
      - Size-bucketed signed delta; liquidation intensity; depth-imbalance slope.
      *Accept:* bars materialize; a smoke experiment runs end-to-end through the existing
      deflation harness; each feature has a numeric cross-check test. Highest mid priority.
- [ ] **M2. Promote the gate from prose to a machine-checkable registry.** Per-candidate
      artifact `{hypothesis_id, feature, N_trials, MinBTL_target, kill DSR/PBO thresholds,
      LockBox slice}`. A signal is `status=CLEARED` iff OOS `DSR>0.95` net-of-cost AND
      `PBO<threshold` AND recorded history ≥ `MinBTL(N)`. Wire the `LockBox`
      (`backtest.py:772-831`, currently opt-in and unused). *Accept:* the registry object
      exists, is read by a (future) terminal panel, and refusing an un-cleared signal is a
      mechanical property, not a discipline.
- [ ] **M3. Collapse the ~9 panel tables into one descriptor registry**
      `{key, section, anchor, minMs, factory, render(ctx)}`, mirroring `LegRegistry`.
      *Accept:* adding a panel touches one descriptor; render loop is data-driven.
- [ ] **M4. `setData` → `series.update()` incremental** for append-only series
      (CVD/OFI/basis/spot-perp); `setData` only on decimation/reset/symbol-switch;
      `fitContent()` occasional. *Accept:* per-paint cost drops; no visual regression.
- [ ] **M5. Extract a shared pure-util module** (`finiteOr/posOr/makeRing/roundPx/
      snapTick`) imported by `terminal-state.js` and `terminal-books.js`; remove the
      verbatim copies.
- [ ] **M6. Materialize a versioned feature store** (DVC stage → parquet order-flow bars)
      so the harness reads bars, not raw ticks; avoid a monthly re-scan per experiment;
      every figure reproducible.

### LONG — decompose, offload, expand, and place the execution interface (6-12 months)

- [ ] **L1. Web Worker + OffscreenCanvas split — partial by construction.** Move ingest +
      normalize + book engines + stores + the custom 2D-canvas panels (footprint/heatmap/
      DOM ladder) to a Worker; render via OffscreenCanvas. The ~5 lightweight-charts panels
      are DOM-attached (`LC.createChart(...)`, `terminal-views.js:352` et al.) and cannot
      move — so this does NOT subsume the `setData` cost (M4 stays complementary). The
      DOM-free stores make the movable half cheap (wiring, not rewrite). *Accept:* main
      thread frees measurably; the chart panels remain on the main thread by design, noted.
- [ ] **L2. Split `terminal.js`** into concern-modules (leg-manager, sink-router, render-
      scheduler, settings), each dual-exported + unit-testable like the stores. Only safe
      AFTER M3 (the panel registry). Last in sequence.
- [ ] **L3. Expand the collector beyond BTCUSDT** to top-liquid perps → the auction suite
      (naked-POC, delta profile, OFI/microprice) becomes multi-symbol. The strongest
      current differentiator is locked to one symbol.
- [ ] **L4. Cheap keyless venues** (Kraken, Bitget, Bitfinex, Hyperliquid) + a
      **long-history heatmap backed by the recorded book store** — the one heatmap axis we
      can win, because we own recorded book history that Bookmap (paid data) cannot give
      away.
- [ ] **L5. Dockable/resizable/pop-out workspace layer** (draggable header, resize handle,
      `window.open` + BroadcastChannel shared store, saved layouts; the current grid
      becomes the "all" preset). Interim cheap step first: a focus/maximize mode
      (double-click a panel → full viewport, esc to return).
- [ ] **L6. Pre-register the FIRST order-flow candidate** with a kill criterion; score it
      continuously as history accrues. Wire the gated terminal signal panel LAST — until
      `CLEARED` it shows a countdown, never a prediction.

## 6. What to refuse (this is the product, not a limitation)

- Refuse native execution / order entry / L3 emulation in `terminal*.js`. Wrong substrate
  category; pursuing it burns years in an arena lost by construction.
- Refuse showing any signal before `status=CLEARED` via the registry (M2). Until OOS
  `DSR>0.95` net-of-cost AND `PBO<threshold` AND history ≥ `MinBTL(N)` — show a countdown,
  not a prediction. This refusal is the product.
- Refuse mixed-history backfill / book-gap smoothing / tick interpolation. Gaps stay gaps.
- Refuse loosening the CI verbatim-assert gate for speed. That gate is what makes the moat
  mechanical rather than cosmetic.
- Refuse fighting Bookmap/Sierra head-to-head as "the most advanced order-flow terminal."
  The winnable category claim is "the most honest, research-validated crypto order-flow
  workbench."

## 7. Sustainable process (standing gates)

- **Verification harness L0-L3 as a non-negotiable CI gate.** L0 (208 pytest + parity to
  machine-eps + the 77-group verbatim-assert gate over real wire frames), L1 (deterministic
  browser replay: REPLAY MODE + zero console error + non-blank canvas), L2 (live-wire
  invariants: book never crossed, mid coherent), L3 (tick-store gap census — report, never
  fill). Every new panel carries one group; accept the linear carrying cost as the price of
  the moat.
- **Pre-registration as a machine artifact** (once M2 exists): every candidate passes
  `{hypothesis_id, N_trials, MinBTL_target, kill criterion}` + a LockBox slice before data
  is touched. Prose run-logs evolve into an enforceable schema.
- **Dogfood-and-evaluate cadence.** After 2-3 build commits, default to dogfood + evaluate
  rather than chaining the next phase — authoring throughput is cheap; evaluation time is
  the real bottleneck.
- **Docs + memory as part of the work.** Every phase commit updates DESIGN/CHANGELOG/README
  and this file in the same commit. Superseded claims marked `[SUPERSEDED]` with an audit
  trail (the OKX `checksum:0` pattern is the template).
- **Numerical verification before build.** Closed-form vs an independent optimiser to
  machine precision before building on it (the `risk.py` fixed-point + parity
  `|Δ|=1.63e-07` pattern). Apply the same to `orderflow.py`: every feature verified against
  an independent reference before it enters the harness.
- **Add what is missing:** a Node unit test that drives `sink()` with fixture events and
  asserts store-routing + dirty-flag outcomes (the 4138-line glue is the fragile part with
  no fast test); and `tsc --checkJs` + minimal JSDoc types on the pure stores to catch
  numeric regressions a fixture smoke can miss.
- **Denser replay fixture for positive-data assertions.** The current short fixture
  leaves walls/vpin/tapeint/bigprints honest-empty, so the L1 browser gate only witnesses
  their empty note by screenshot, never asserts they populate. A longer/denser fixture
  that drives those panels to non-empty upgrades them from "empty-note witness" to a real
  canvas/data assertion (surfaced by the N1 verification).
