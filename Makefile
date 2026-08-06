# btc-quant — convenience targets (no long flags, no pasted-comment issues).
# Examples:
#   make backtest                 -> ma_trend_filter, 2018 -> now, deflated Sharpe
#   make backtest STRAT=tsmom     -> pick a strategy
#   make scan                     -> live signal snapshot
#   make test                     -> pytest
#   make dash                     -> serve dashboard at :8787 (Ctrl-C to stop)
#   make collector                -> O-0 tick collector daemon (Ctrl-C flushes + exits)
#   make collector-api            -> same, plus the BYOD replay API at :8788

STRAT  ?= ma_trend_filter
START  ?= 2018-01-01
TRIALS ?= 20
PORT   ?= 8787
SYMBOL   ?= BTCUSDT
API_PORT ?= 8788

.PHONY: help install backtest compare dsr-ab scan test gate fetch dash local collector collector-api verify-browser verify-census verify-focus verify-wire bench-render check-ticks check-vision churn-threshold coverage-census lockbox-integrity doc-freshness handoff orderflow-smoke econ archive archive-dry archive-list hf-sync backfill-levels vision-sync vision-list

help:
	@echo "targets: install | backtest [STRAT=.. START=..] | compare | scan | test | fetch | dash [PORT=..] | collector [SYMBOL=..] | collector-api [SYMBOL=.. API_PORT=..]"
	@echo "research: orderflow-smoke (M1 end-to-end: recorded ticks -> bars -> deflation harness; expect INSUFFICIENT HISTORY)"
	@echo "gate:    gate (fail-fast, CI order then local: pytest -> check_parity -> check_terminal -> churn-threshold -> lockbox-integrity)"
	@echo "verify:  verify-browser (L1 fixture-replay in headless Chromium) | verify-census (L1b layout census) | verify-focus (L1c hierarchy + focus mode) | verify-wire (L2 live invariants, ~45s) | check-ticks (L3 tick-store QA + MinBTL readiness meter)"
	@echo "bench:   bench-render (where the frame budget actually goes: store cost, Worker-boundary cost, Canvas2D churn vs raster — STRATEGY §ARCHITECTURE)"
	@echo "archive: archive-dry (export closed months to local parquet ONLY) | archive (export + upload to GitHub Releases + prune) | archive-list (what is offsite)"
	@echo "hf:      hf-sync (closed day files -> HF dataset, verify on Hub, then delete local; ARGS=--dry-run to stage only, ARGS=--yes for cron)"
	@echo "levels:  backfill-levels (archived HF days -> data/ticks/levels.jsonl registry; idempotent, never touches day files)"
	@echo "vision:  vision-list (what the PUBLIC archive publishes; downloads nothing) | vision-sync ARGS=\"--start .. --end ..\" | check-vision (L3 QA over the archive partition)"
	@echo "         vision is TRADES ONLY: it lengthens CVD/footprint/delta/VPIN and gives the book families nothing. Archive rows never count toward the MinBTL readiness meter."
	@echo "dsr-ab:  A/B/B Deflated-Sharpe convention aid (production A vs B1=1/n vs B2=own Lo/Mertens V); ARGS=--research for N=8, --json"
	@echo "strategies: buy_and_hold ma_trend_filter tsmom pairs_coint carry"
	@echo "collector needs opt-in deps: pip install -r requirements-collector.txt"

compare:
	python3 scripts/compare.py --start $(START)

# Non-destructive DSR convention decision aid. Reports production convention A
# (empirical cross-strategy V) alongside B1 (1/n) and B2 (per-strategy Lo/Mertens V)
# on the SAME OOS leaderboard, plus a coupling-sensitivity block. Production is UNCHANGED.
dsr-ab:
	python3 scripts/dsr_ab.py --start $(START) $(ARGS)

install:
	python3 -m pip install -r requirements.txt

backtest:
	MPLBACKEND=Agg python3 scripts/run_backtest.py --strategy $(STRAT) --start $(START) --n-trials $(TRIALS)

scan:
	python3 scripts/scan.py

test:
	python3 -m pytest -q

# Fail-fast local gate: the CI steps runnable here, in the SAME order as
# .github/workflows/ci.yml (pytest -> JS<->Python parity -> terminal fixture
# smoke), then the local-only integrity gates CI has no data for. Make stops at
# the first failing line, so a red run names its gate. CI's node --check and
# ppy() self-check steps are not repeated here. verify_terminal_browser.py is
# excluded on purpose, same as CI ("deliberately NOT run here", ci.yml): it
# needs playwright + a Chromium download — run it separately as
# `make verify-browser`.
gate:
	python3 -m pytest -q
	python3 scripts/check_parity.py
	node scripts/check_terminal.cjs
	python3 scripts/doc_freshness.py --quiet-exempt
	python3 scripts/churn_threshold.py
	python3 scripts/lockbox_integrity.py
	@python3 -c "import json,subprocess,datetime as dt,pathlib; \
	sha=subprocess.run(['git','rev-parse','--short','HEAD'],capture_output=True,text=True).stdout.strip(); \
	pathlib.Path('reports/gate-last.json').write_text(json.dumps({'result':'green','sha':sha,\
	'utc':dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),\
	'chain':'pytest -> parity -> terminal -> doc-freshness -> churn -> lockbox'},indent=2)+chr(10))"
	@$(MAKE) --no-print-directory handoff

# Documentation rot gate: no file:line pointers, single-owner look counter, fast-moving
# facts only in docs/STATUS.md. Negative controls in tests/test_doc_freshness.py.
doc-freshness:
	python3 scripts/doc_freshness.py

# Regenerate docs/HANDOFF.md — the one self-contained file a session without this machine
# needs. Every field is read from a source; an unreadable source says UNKNOWN.
handoff:
	python3 scripts/make_handoff.py

fetch:
	python3 scripts/fetch_data.py --symbol BTC-USD --granularity 1d --start $(START)

dash:
	@echo "Dashboard -> http://127.0.0.1:$(PORT)   (Ctrl-C to stop)"
	python3 -m http.server $(PORT) --bind 127.0.0.1 --directory dashboard

# The COMPLETE local terminal in one command: collector BYOD API + dashboard,
# with a per-leg health report and an explicit list of what is degraded. Starts
# only what is actually missing — a launchd-owned collector is probed, never
# restarted, because a needless restart is a real hole in the recorded tape.
# NO_API=1 for a charts-only run.
local:
	bash scripts/local_stack.sh

# Tick collector v2 (DESIGN-orderflow-terminal.md §3 + §3c). Keyless public feeds ->
# per-UTC-day files under data/ticks/ (event-time rotation; closed days are immutable
# and move to the HF dataset via `make hf-sync`). All five venues by default:
# bybit + binancef + okx + coinbase + deribit. Opt-in deps: requirements-collector.txt.
# Legacy single-file mode: ARGS="--db data/ticks.duckdb --exchanges binancef,bybit".
collector:
	python3 scripts/run_collector.py --symbol $(SYMBOL) $(ARGS)

collector-api:
	@echo "BYOD API -> http://127.0.0.1:$(API_PORT)   (Ctrl-C to stop)"
	python3 scripts/run_collector.py --symbol $(SYMBOL) --api-port $(API_PORT) $(ARGS)

# O-5 econ-calendar mirror (DESIGN-orderflow-terminal.md §4e): the faireconomy
# JSON has NO CORS header so the browser cannot fetch it — this writes the
# same-origin dashboard/econ_calendar.json (gitignored) the EconView reads.
# Re-run whenever the panel says the mirror is stale.
econ:
	python3 scripts/fetch_econ.py

# Data lifecycle (DESIGN-orderflow-terminal.md §3 'Data lifecycle'): the store grows
# ~2.4 GB/month on a disk-limited machine, so closed UTC months move to GitHub
# Releases (immutable, checksummed, provenance-stamped parquet) BEFORE any prune.
# Stop the collector first — the script refuses otherwise; the window is an honest
# maintenance gap. archive-dry stages local parquet only (nothing uploaded, nothing
# pruned); archive is the real lifecycle (prune runs only after the upload is
# byte-verified, and asks for confirmation); archive-list shows what is offsite.
# Extra flags via ARGS, e.g.:  make archive ARGS="--month 2026-07 --partial"
archive:
	python3 scripts/archive_ticks.py --db data/ticks.duckdb --out data/archive --upload --prune $(ARGS)

archive-dry:
	python3 scripts/archive_ticks.py --db data/ticks.duckdb --out data/archive $(ARGS)

archive-list:
	python3 scripts/archive_ticks.py --list

# HF data lifecycle (DESIGN §3c — the SCHEDULED path; the GH-Release archive above
# stays functional as a frozen artifact): every CLOSED UTC day file in data/ticks/
# is exported to ZSTD parquet, uploaded to the HF dataset azulcoder/btc-quant-ticks
# (hive layout data/date=YYYY-MM-DD/ + sha256 manifest), verified ON THE HUB, and
# only then deleted locally (today + yesterday always stay local for the BYOD API).
# Needs NO collector stop. Daily automation: scripts/com.btcquant.hfsync.plist.example.
# Extra flags via ARGS, e.g.:  make hf-sync ARGS="--dry-run"   |   ARGS="--yes"
hf-sync:
	python3 scripts/upload_hf.py $(ARGS)

# §4f levels registry backfill: one row per ARCHIVED HF day (o/h/l/c, POC/VA at the
# fixed $10 tick, bybit leg) into data/ticks/levels.jsonl — the same row the rotation
# hook appends when a day closes locally. Idempotent (skips recorded dates), reads
# hf:// only, never touches day files; safe while the collector runs.
backfill-levels:
	python3 scripts/backfill_levels.py $(ARGS)

# Terminal verification layers (DESIGN-orderflow-terminal.md §7).
# L1: deterministic — replays the captured fixture frames through the real terminal in
#     headless Chromium (?replay=1), asserts render + zero console errors, screenshots
#     to reports/verify/. Needs: pip install playwright && playwright install chromium.
verify-browser:
	python3 scripts/verify_terminal_browser.py

# L2: live wire — drives the PRODUCTION adapters/stores from the real public feeds for
#     ~45s and checks cross-venue invariants (never-crossed books, coherent mids,
#     ts sanity, CVD bucket sums). Exit 2 = offline (not a code bug).
verify-wire:
	node scripts/verify_wire_live.mjs --seconds 45

# L1b: layout census — page height, panel/empty counts, .hint coverage, TradingView
#      logo count and DOM text collisions at 1680x1050. A REPORTING pass, so the
#      T-4 visual targets are re-measurable numbers instead of eyeballed screenshots.
verify-census:
	python3 scripts/verify_terminal_browser.py --census

# L1c: T-4 hierarchy + focus mode — every panel carries a data-tier stamped from the
#      M3 registry, double-click a panel header maximizes it (incl. the 7 panels
#      NESTED in a term-col, the case that breaks naively), Esc restores, and no
#      store count regresses across the toggle (presentation only).
verify-focus:
	python3 scripts/verify_terminal_browser.py --focus


# Render-budget bench (STRATEGY.md §ARCHITECTURE). Answers where the terminal's frame
# time actually goes BEFORE any render-layer work is sequenced off a guess: the Node half
# measures the ingest+store cost (the ceiling on what a Worker can free) and the cost of a
# structured-clone thread boundary; the browser half measures Canvas2D state churn vs
# rasterization, LOD vs naive zoom, a WebGL2 equivalent, and what a Worker/OffscreenCanvas
# path would have to replace. Numbers are machine-specific — record the machine with them.
bench-render:
	node scripts/bench_render.cjs
	@echo ""
	@echo "browser half (needs a real GPU, so not headless-by-default):"
	@echo "  python3 -m http.server 8799 && open http://127.0.0.1:8799/scripts/bench_render.html"
	@echo "  results also land in window.__BENCH__"

# L3: tick-store QA report card over data/ticks.duckdb (gaps/dupes/cadence/coherence —
#     reported, never filled). Run while the collector is stopped, or on a copy.
check-ticks:
	python3 scripts/check_ticks.py --db data/ticks

# Where are we against the §14b churn pre-registration? Idempotent, read-only, and
# it refuses to report if its own control fails. Exit 2 = the instrument is wrong.
churn-threshold:
	python3 scripts/churn_threshold.py

# Is every LockBox defect recorded where it survives a restart? /health resets; this
# reads the append-only stamped log. Exit 1 = a drop is unrecorded, or overstated.
lockbox-integrity:
	python3 scripts/lockbox_integrity.py

# Coverage cells normalised by TIME covered, not sample count (EDA §11-N).
coverage-census:
	python3 scripts/coverage_census.py

# M7 public-archive ingest (DESIGN-orderflow-terminal.md §3d, STRATEGY.md M7).
# Binance's own published aggTrades archive -> data/vision/ ZSTD parquet, seven
# verification gates per day (checksum, zip shape, header sniff, day containment,
# ms-unit, ID continuity, parquet re-read). A SEPARATE tree from data/ticks/:
# archive rows are never written into a recorded day file, never unioned into an
# hf:// partition, and never counted by the MinBTL readiness meter.
#
# THE LIMIT, because it is the whole point: aggTrades is TRADES ONLY. This
# lengthens CVD / footprint / size-bucketed delta / VPIN. It gives OFI, weighted
# mid, depth-imbalance slope and walls NOTHING — the archive publishes no book.
#
# Reading it back is an explicit opt-in and never the default:
#   order_flow_bars(..., source=("local", "hf", "vision"))
#
#   make vision-list                                          # enumerate, download nothing
#   make vision-sync ARGS="--start 2026-07-25 --end 2026-08-01"
#   make vision-sync ARGS="--all --yes"                       # ~2,406 days, ~41 GiB, hours
vision-sync:
	python3 scripts/ingest_vision.py $(ARGS)

vision-list:
	python3 scripts/ingest_vision.py --list $(ARGS)

# L3 QA over the archive partition — the SAME sections as check-ticks, with the
# same duplicate-trade_id FAIL and the same report-never-fill gap census, plus an
# ID-continuity census the recorded store cannot have. It REFUSES to print a
# MinBTL readiness number: archive days never count toward it.
# NOTE: the bare target below is the FULL archive, which does not fit this
# machine (docs/STATUS.md "locally infeasible at full scale": dedup over
# ~2.83 B rows needs ~160 GB of aggregate state vs ~14 GB free). Grade month
# windows instead, e.g.:
#   python3 scripts/check_ticks.py --vision data/vision/binancef/BTCUSDT/aggTrades --month 2026-07
check-vision:
	python3 scripts/check_ticks.py --vision data/vision/binancef/BTCUSDT/aggTrades

# M1 end-to-end smoke (STRATEGY.md M1): recorded ticks -> order-flow bars ->
# features/walk_forward/DSR/PBO/MinBTL with ZERO harness change. The expected and
# correct verdict is INSUFFICIENT HISTORY — the deflation stack refusing to score
# a ~3-week archive is the machinery working. Reads the HF mirror by default, so
# the first run costs a few minutes; the result is cached under data/orderflow/.
orderflow-smoke:
	python3 scripts/orderflow_smoke.py $(ARGS)
