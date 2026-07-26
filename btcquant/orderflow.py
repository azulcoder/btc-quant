"""orderflow.py — event-time order-flow bars from the recorded tick store.

This is the research-side keystone: it turns the collector's raw tick archive
(``data/ticks/YYYY-MM-DD.duckdb`` day files, and the same tables mirrored as
``hf://`` parquet partitions) into a bar ``DataFrame`` in **exactly the contract
the rest of ``btcquant`` already consumes** — a tz-aware UTC ``DatetimeIndex``
plus ``open/high/low/close/volume`` — so ``features.atr``, ``backtest.run``,
``backtest.walk_forward``, ``backtest.cpcv`` and the whole ``risk`` deflation
stack read it with **zero harness change**. The order-flow columns ride along as
extra columns; nothing downstream has to know about them.

FEATURES ONLY — this module never ranks, scores, or implies predictive power.
Scoring lives in the deflation harness (``btcquant.risk``), and STRATEGY.md §6
refuses to show anything before ``status=CLEARED``. Nothing here is a signal, a
recommendation, or evidence of one.

The recorded archive is FAR below MinBTL for any realistic trial count; this
module builds the rail while the clock runs. That comparison is **measured, not
remembered**: every frame carries ``attrs["orderflow"]["history"]`` with the days
actually resolved, the span in years, ``risk.min_backtest_length(N)`` and the
fraction of it met, so the claim cannot quietly go stale as the archive grows.
Building the plumbing now is correct; reading a Sharpe off it is not.

Honesty rails (non-negotiable — they are the moat, not decoration)
------------------------------------------------------------------
1. **Gaps stay gaps.** Nothing is interpolated, forward-filled or smoothed. Every
   bar carries ``coverage``/``gap_ms``/``is_gap``/``ret_spans_gap``/``segment``
   so downstream can drop or flag it. A bar inside a collector outage is NaN,
   never 0.0 and never the previous value. Liveness is witnessed **per leg**:
   the trade columns are gated by the *trades* stream alone and the book columns
   by the *depth* stream alone, because a venue whose trade leg dies while its
   book leg keeps printing is exactly the case a pooled witness would score as
   fully alive and then fill with fabricated zeros.
2. **Every approximation is labelled** in the docstring *and* discoverable at
   runtime through :data:`PROVENANCE` / :func:`provenance_table`, keyed by the
   emitted column name. ``FeatureNote.approximation`` is never empty.
3. **No signal claims.** See above.
4. **Insufficient history is stated, not implied.** See above.
5. **Book-derived features are single-venue.** Exactly one ``book_venue`` per
   call; venue books are never merged into one synthetic book. Cross-venue
   *trade* features are fine and are always suffixed with the venue. Two
   **symbols** are never pooled either: a venue carrying more than one symbol in
   the window is a hard error, not a silent sum (see ``symbol=``).
6. **No look-ahead in any price or flow feature.** A bar's features use only rows
   whose ``ts_ms`` falls inside that bar (or, for the VPIN as-of join and the
   day-anchored OFI/volume clocks, inside/before it). The shift-by-one discipline
   in ``backtest.run`` stays valid on top of these bars. **Stated exception, and
   it is a real one:** the *quality* family (``coverage``/``gap_ms``/``is_gap``/
   ``ret_spans_gap``/``segment``) is **ex-post by construction** — a feed hole is
   only measurable once the feed resumes, so a bar closing 4 s into a silence
   that later grows past :data:`GAP_MS` is charged for it. The leak is bounded by
   the hole length, carries no price information whatsoever, and is the same
   ex-post *data-availability* knowledge :func:`drop_gap_bars` and
   :func:`gap_flat_positions` are labelled with. It is disclosed here rather than
   defined away.

Bar contract
------------
Index: ``pd.DatetimeIndex``, tz-aware UTC, ``name="timestamp"``, a **full regular
grid** over ``[start, end)`` with the label at the bar **open** (closed-left).
Bar ``t`` sees only ``[t, t+Δ)``.

Aggregation rule, stated once: **flow** variables (delta, volume, counts, OFI,
liquidation notional) are **summed** within the bar; **state** variables (mid,
spread, microprice, book imbalance, depth, slope) are taken from the **last
snapshot inside the bar** — the same semantics as ``close``, and what a decision
made at the bar close would actually have seen.

Dtypes: every numeric column is ``float64``, **including counts**. Counts must be
able to say *unknown* (leg dead → NaN); ``Int64`` masked dtype cannot express
that without breaking some ``rolling``/``ewm`` paths. Only ``is_gap`` and
``ret_spans_gap`` are ``bool`` (always computable, never unknown).

Features, their sources, and their approximations
-------------------------------------------------
* **CVD / signed delta** — Chordia, Roll & Subrahmanyam (2002), "Order imbalance,
  liquidity, and market returns", *JFE* 65(1), 111-130. Approximation: **none on
  the sign** — the aggressor side is the real flag off the wire (normalized per
  venue by ``collector.py``), so no Lee-Ready (1991) tick rule and no bulk-volume
  classification is needed. What remains: ``cvd_*`` is *session-anchored* and is
  reset at every coverage ``segment`` boundary, because a level accumulated
  across an outage would imply flow we never observed.
* **Size-bucketed signed delta** — bucket cut-offs reuse the repo's own taxonomy
  (``dashboard/terminal-state.js`` ``CvdStore``): ``[1e4, 1e5, 1e6]`` USD, a trade
  lands in the smallest threshold ``>=`` its notional, ``whale`` above the
  largest. Approximation: bucketing is **per print, not per parent order** — one
  iceberg shows up as many retail prints.
* **OFI** — Cont, Kukanov & Stoikov (2014), "The Price Impact of Order Book
  Events", *Journal of Financial Econometrics* 12(1), 47-88. **APPROXIMATION —
  1s snapshot approximation stated.** The paper defines the order-flow imbalance
  over book *events* (every L1 update). The store holds ~1 Hz **snapshots**, so
  what is computed here is the net contribution *between consecutive snapshots*,
  not the sum of per-event contributions. **The direction of the bias is NOT
  known** — an earlier draft of this docstring claimed the snapshot value
  understates ``|OFI|``; that claim was falsified through this module and is
  withdrawn ``[SUPERSEDED]``. Both directions occur: intra-second add/cancel
  churn at an unchanged quote is invisible (understates), while a price
  *round-trip* inside the sampling interval is misread as a pure queue change of
  the full size difference and can **overstate**. Worked counterexample (asks
  frozen at 102@7, bids 100@10 -> 101@5 -> 100@1): event-level OFI is
  ``+5 - 5 = 0``, but if the middle state is missed the single sampled pair gives
  ``1{100>=100}·1 - 1{100<=100}·10 = -9``. The cause is the paper's own indicator
  convention — ``P_n == P_{n-1}`` fires *both* indicators. Pairs separated by
  more than :data:`GAP_MS` are excluded (a "book event" read across a two-hour
  hole is not an event) and counted in ``ofi_gap_pairs_*``. Pairs are anchored to
  the **UTC day**, not to the request window, so a bar's OFI does not change when
  the same bar is asked for inside a wider range; the pair straddling midnight is
  never formed.
* **Microprice** — Stoikov (2018), "The micro-price: a high-frequency estimator
  of future prices", *Quantitative Finance* 18(12), 1959-1966. **APPROXIMATION:
  weighted mid — Stoikov first-order form, not the fitted micro-price.** The full
  micro-price is a limit of conditional expectations requiring a fitted Markov
  correction over (imbalance, spread); that is a model, not an observable. What
  is emitted is the size-weighted mid ``(P_b·q_a + P_a·q_b)/(q_a+q_b)``, i.e. the
  first-order term. Queue imbalance itself: Gould & Bonart (2016), "Queue
  imbalance as a one-tick-ahead price predictor in a limit order book", *Market
  Microstructure and Liquidity* 2(2).
* **VPIN** — Easley, López de Prado & O'Hara (2012), "Flow Toxicity and Liquidity
  in a High-Frequency World", *RFS* 25(5), 1457-1493. Volume clock, exact
  boundary splitting, bucket imbalance averaged over the last
  ``vpin_window_buckets``. **contested: Andersen & Bondarenko (2014) attribute
  VPIN's content largely to volatility/intensity** (*Journal of Financial
  Markets* 17(1), 1-46; the authors' rejoinder is in the same volume). The series
  is published; toxicity is **not** claimed. Labelled deviations from the paper:
  (a) classification uses the **real aggressor flag** instead of the paper's
  bulk-volume classification — strictly better input, same statistic;
  (b) the bucket volume ``V`` is set **causally** from the median daily volume of
  *strictly prior* days (the paper's "average daily volume / 50" would peek at
  the day being measured), so the first day in a range is warm-up NaN, never a
  guess; (c) the volume clock is re-armed at **UTC midnight** and is read from
  the whole UTC days the request touches, not from the request window, so the
  same bar returns the same VPIN whether it is asked for alone or inside a wider
  range; (d) the rolling window can still **span a feed hole** — a mean over the
  last N buckets says nothing about the wall time they cover — so
  ``vpin_window_span_s_*`` and ``vpin_window_gap_s_*`` are emitted beside it.
  Reported, never silently dropped, the same way ``ofi_gap_pairs_*`` reports the
  pairs OFI excluded.
* **Liquidation intensity** — descriptive counting only, no model. Mechanism
  motivation (not evidence): Brunnermeier & Pedersen (2009), "Market Liquidity
  and Funding Liquidity", *RFS* 22(6). ``side`` is the *liquidated position*
  (``long``/``short``) as normalized by the collector. Only venues in
  :data:`LIQUIDATION_VENUES` have a liquidation leg in this store; other venues
  get **no column at all** rather than a misleading zero. Zero-vs-unknown is
  decided by **leg liveness**, never by row count, and the two backends are made
  to agree on it: a local day file holding a ``liquidations`` table with 0 rows
  and an ``hf://`` day whose ``liquidations`` partition is absent (the exporter
  skips empty tables) are the *same statement* — "the leg was alive and nothing
  printed" — and both read ``0.0``. The one residual asymmetry is stated rather
  than hidden: a day file whose schema predates the ``liquidations`` table reads
  NaN locally (structural absence is visible there) but would read ``0.0`` from
  the Hub, because the exporter erases that distinction. Every day's structural
  table presence is recorded in ``attrs["orderflow"]["manifest"]``.
* **Depth-imbalance slope** — Næs & Skjeltorp (2006), "Order book characteristics
  and the volume-volatility relation", *Journal of Financial Markets* 9(4),
  408-432; Cao, Hansch & Wang (2009), "The information content of an open
  limit-order book", *Journal of Futures Markets* 29(1), 16-41. Cumulative depth
  ``Q_j`` regressed on distance-from-mid ``x_j`` (bp) through the origin,
  ``beta = Σ x_j Q_j / Σ x_j²``. **APPROXIMATION:** a *fixed level count*, not a
  fixed bp band — measured on this store, the whole top-20 of the binancef leg
  spans ~0.37 bp, so a fixed-bp definition is degenerate there. Snapshots with
  fewer than ``depth_levels`` levels give NaN, never a zero-padded book. Stored
  depth differs per venue (binancef 20, bybit/okx 50), so **the slope is not
  comparable across venues**; ``depth_levels_*`` is emitted to keep that visible.

What this module is *not*
-------------------------
It is not a strategy, not a screener, and not a ranker. It emits observables and
their quality flags. Anything that turns them into a position belongs upstream of
``backtest.walk_forward`` and downstream of a pre-registered hypothesis.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence, Union

import numpy as np
import pandas as pd

try:  # opt-in dependency (requirements-collector.txt), same guard as collector.py
    import duckdb  # type: ignore
except Exception:  # pragma: no cover - environment without the opt-in dep
    duckdb = None  # type: ignore

from .data import DATA_DIR

__all__ = [
    "OrderFlowError",
    "FeatureNote",
    "STORE_DIR",
    "HF_REPO",
    "BAR_FREQS",
    "SIZE_BUCKETS_USD",
    "LIQUIDATION_VENUES",
    "VENUE_INSTRUMENT",
    "GAP_MS",
    "PROVENANCE",
    "HONESTY_SENTENCES",
    "SCHEMA_VERSION",
    "available_days",
    "order_flow_bars",
    "volume_buckets",
    "periods_per_year",
    "coverage_mask",
    "drop_gap_bars",
    "gap_flat_positions",
    "segments",
    "provenance_table",
]


class OrderFlowError(RuntimeError):
    """Raised when order-flow bars cannot be built from source *or* cache."""


# --------------------------------------------------------------------------- #
# Constants. Each one names the single source of truth it mirrors; a test       #
# asserts the mirror still matches, so the two can never silently drift.        #
# --------------------------------------------------------------------------- #

#: Tick store root — mirrors ``collector.DEFAULT_DB`` (``data/ticks``).
STORE_DIR: Path = DATA_DIR / "ticks"

#: Hugging Face dataset holding the archived day partitions — mirrors
#: ``scripts/upload_hf.py`` ``DEFAULT_REPO``.
HF_REPO: str = "azulcoder/btc-quant-ticks"

#: Bar clocks this module will build. Deliberately short: each extra clock is an
#: extra research trial, and the deflation maths charges for trials.
BAR_FREQS: tuple[str, ...] = ("1min", "5min", "15min", "1h")

#: Notional cut-offs for size-bucketed delta, USD. Reused verbatim from the
#: repo's own ``CvdStore`` taxonomy (``dashboard/terminal-state.js``) so the
#: research bars and the terminal panel cut flow at the same places.
SIZE_BUCKETS_USD: tuple[float, ...] = (1e4, 1e5, 1e6)

#: Venues for which the collector records a liquidation stream at all
#: (``collector.py`` wires ``allLiquidation`` on bybit only). A venue outside
#: this set gets **no** liquidation columns — absence, not a fabricated zero.
LIQUIDATION_VENUES: tuple[str, ...] = ("bybit",)

#: Instrument class per collector venue code (``collector._ACCEPTED_EXCHANGES``).
#: Used for one purpose only: to say out loud when ``price_venue`` and
#: ``book_venue`` are different instrument classes, because ``open/high/low/
#: close/volume`` cannot be venue-suffixed (that is the harness contract) and so
#: nothing else in the frame would warn that ``mid_{b} - close`` is a funding
#: basis rather than book pressure.
VENUE_INSTRUMENT: dict[str, str] = {
    "binancef": "perp",
    "bybit": "perp",
    "okx": "perp",
    "coinbase": "spot",
    "deribit": "option",
}

#: Feed-hole threshold in ms — mirrors ``scripts/check_ticks.py`` ``GAP_MS``, with
#: its justification: BTC perp prints subsecond-to-seconds around the clock, so
#: 30 s of silence on a venue leg is a feed hole, not a quiet market. One gap
#: definition for the L3 QA report and for research, never two.
GAP_MS: int = 30_000

#: Rotation grace + flush slack in minutes before yesterday's day file counts as
#: closed — mirrors ``scripts/upload_hf.py`` ``GRACE_CLOSE_MIN``.
GRACE_CLOSE_MIN: int = 6

#: Bumped whenever a formula, a column, or a dtype changes. It is part of the
#: cache spec hash, so a formula change can never read a stale cached bar.
#: v2: per-leg coverage witness (trade columns no longer gated by the depth
#: leg), segment breaks on any unobserved millisecond, day-anchored volume clock
#: and OFI pairing, ``coverage_liq_*`` / ``vpin_window_span_s_*`` /
#: ``vpin_window_gap_s_*`` added. Every one of those changes a value, so the old
#: cached bars must not be readable.
SCHEMA_VERSION: str = "2"

MS_PER_DAY: int = 86_400_000
_MS_PER_YEAR: int = 365 * MS_PER_DAY

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Repo ids get interpolated into ``read_parquet()`` paths (table functions take
#: no ``?`` placeholders), so the identifier shape is validated first — the same
#: rule ``scripts/backfill_levels.py`` applies.
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*/[A-Za-z0-9][A-Za-z0-9._\-]*$")
#: Venue codes are interpolated into column names and compared in SQL; keep them
#: to the collector's short-code shape.
_VENUE_RE = re.compile(r"^[a-z0-9_]+$")
#: Instrument ids are venue-native (``BTCUSDT``, ``BTC-USD``, ``BTC-USDT-SWAP``)
#: and are compared in SQL, so keep them to the collector's own id shape.
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9._:\-]+$")

_INSTALL_HINT = (
    "install the opt-in tick-store deps: pip install -r requirements-collector.txt"
)

#: Canonical column names + SQL types per table, mirroring ``collector._TABLE_COLUMNS``
#: and ``collector._SCHEMA_DDL``. Projecting these explicitly (never ``SELECT *``)
#: is what keeps the day-file and the parquet backends the same shape: the hive
#: parquet reader synthesises an extra ``date`` column that day files do not have.
_TABLE_SCHEMA: dict[str, tuple[tuple[str, str], ...]] = {
    "trades": (
        ("exchange", "VARCHAR"), ("symbol", "VARCHAR"), ("trade_id", "VARCHAR"),
        ("ts_ms", "BIGINT"), ("price", "DOUBLE"), ("qty", "DOUBLE"),
        ("aggressor_buy", "BOOLEAN"),
    ),
    "liquidations": (
        ("exchange", "VARCHAR"), ("symbol", "VARCHAR"), ("ts_ms", "BIGINT"),
        ("side", "VARCHAR"), ("price", "DOUBLE"), ("qty", "DOUBLE"),
        ("notional_usd", "DOUBLE"),
    ),
    "depth_snapshots": (
        ("exchange", "VARCHAR"), ("symbol", "VARCHAR"), ("ts_ms", "BIGINT"),
        ("bids", "VARCHAR"), ("asks", "VARCHAR"),
    ),
}

#: Working relation names inside the scratch connection. Deliberately prefixed so
#: they can never collide with a table reached through an ATTACHed day catalog.
_REL = {
    "trades": "of_trades",
    "depth_snapshots": "of_depth",
    "liquidations": "of_liq",
}


def _require_deps() -> None:
    """Raise an actionable error iff the opt-in tick-store deps are missing.

    Called only when a function actually touches the store, so ``import
    btcquant.orderflow`` (and therefore ``import btcquant``) stays safe on a
    machine that never installed duckdb — the ``collector.py`` guard pattern.
    """
    if duckdb is None:
        raise OrderFlowError(f"orderflow requires duckdb — {_INSTALL_HINT}")


# --------------------------------------------------------------------------- #
# Provenance — discoverable at runtime, not buried in prose.                    #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FeatureNote:
    """One emitted column's honest paperwork.

    ``approximation`` is mandatory and must never be empty: a feature whose
    approximation is genuinely nil says so explicitly ("none — ..."), which is a
    claim someone can check, unlike silence.
    """

    column: str          # template, e.g. "ofi_{venue}"
    family: str          # price | quality | trade | book | liquidation
    formula: str
    citation: str
    approximation: str
    units: str
    source_leg: str
    aggregation: str = ""   # "sum" (flow) | "last" (state) | "" (derived)
    contested: str = ""

    def __post_init__(self) -> None:
        if not self.approximation.strip():
            raise ValueError(
                f"FeatureNote({self.column!r}): approximation must be stated, "
                "even when it is 'none — ...'"
            )


def _note(**kw: Any) -> FeatureNote:
    return FeatureNote(**kw)


_CIT_CRS = ("Chordia, Roll & Subrahmanyam (2002), 'Order imbalance, liquidity, and "
            "market returns', Journal of Financial Economics 65(1), 111-130")
_CIT_CKS = ("Cont, Kukanov & Stoikov (2014), 'The Price Impact of Order Book Events', "
            "Journal of Financial Econometrics 12(1), 47-88, doi:10.1093/jjfinec/nbt003")
_CIT_STOIKOV = ("Stoikov (2018), 'The micro-price: a high-frequency estimator of future "
                "prices', Quantitative Finance 18(12), 1959-1966")
_CIT_GB = ("Gould & Bonart (2016), 'Queue imbalance as a one-tick-ahead price predictor "
           "in a limit order book', Market Microstructure and Liquidity 2(2)")
_CIT_VPIN = ("Easley, Lopez de Prado & O'Hara (2012), 'Flow Toxicity and Liquidity in a "
             "High-Frequency World', Review of Financial Studies 25(5), 1457-1493")
_CIT_AB = ("contested: Andersen & Bondarenko (2014) attribute VPIN's content largely to "
           "volatility/intensity — 'VPIN and the flash crash', Journal of Financial "
           "Markets 17(1), 1-46 (rejoinder in the same volume)")
_CIT_NS = ("Naes & Skjeltorp (2006), 'Order book characteristics and the volume-volatility "
           "relation', Journal of Financial Markets 9(4), 408-432; Cao, Hansch & Wang "
           "(2009), 'The information content of an open limit-order book', Journal of "
           "Futures Markets 29(1), 16-41")
_CIT_BP = ("mechanism motivation only, not evidence: Brunnermeier & Pedersen (2009), "
           "'Market Liquidity and Funding Liquidity', Review of Financial Studies 22(6)")
_CIT_NONE = "no external estimator — arithmetic over recorded rows"

_OFI_LABEL = "1s snapshot approximation stated"

#: The quality family is ex-post by construction and says so, every time. A feed
#: hole is only measurable once the feed RESUMES, so a bar that closes partway
#: into a silence is charged for a hole the clock could not yet have declared.
#: Bounded by the hole length, carries no price information, and is the same
#: class of ex-post *data-availability* knowledge ``drop_gap_bars`` is labelled
#: with — disclosed rather than defined away (rail 6).
_EXPOST_LABEL = (
    "EX-POST (rail 6 exception, stated): a feed hole is only measurable once the feed "
    "resumes, so this column at bar t uses data-availability knowledge from after t "
    "closes — never price knowledge"
)

#: ``open/high/low/close/volume`` cannot carry a venue suffix — that is the
#: harness contract — so when ``price_venue`` and ``book_venue`` are different
#: instrument classes nothing in the frame's own column names would say so. This
#: sentence does, on every book-price column, and the run also records it in
#: ``attrs["orderflow"]["cross_instrument"]`` and warns once.
_CROSS_INSTRUMENT_LABEL = (
    "CROSS-INSTRUMENT WARNING: this is the BOOK venue's own price. When book_venue and "
    "price_venue are different instrument classes (spot vs perp) the difference against "
    "`close` is a funding BASIS, not book pressure — on this archive a spot close against "
    "a perp mid runs ~40 USD (~6 bp) apart. Use micro_minus_mid_{b}, which is within-venue"
)

#: The five load-bearing honesty statements. They live in the module docstring
#: **and** here, so they are machine-checkable rather than prose a refactor can
#: quietly reword — the same discipline ``scripts/check_terminal.cjs`` enforces
#: on the terminal's labels. ``tests/test_orderflow.py`` asserts every one of
#: them still appears in ``__doc__``.
HONESTY_SENTENCES: tuple[str, ...] = (
    _OFI_LABEL,
    "weighted mid — Stoikov first-order form, not the fitted micro-price",
    "contested: Andersen & Bondarenko (2014) attribute VPIN's content largely to "
    "volatility/intensity",
    "FEATURES ONLY — this module never ranks, scores, or implies predictive power",
    # Deliberately carries no day count. The archive grows one day per day — that
    # is the point of the collector — so a hard-coded numeral here would be a
    # claim the test could only check for PRESENCE, never for TRUTH. The number
    # is measured per call into attrs["orderflow"]["history"] instead.
    "The recorded archive is FAR below MinBTL for any realistic trial count; this "
    "module builds the rail while the clock runs",
)

#: Column template -> :class:`FeatureNote`. ``{v}`` is a trade venue, ``{b}`` the
#: single book venue. :func:`provenance_table` resolves templates for a concrete
#: frame; :func:`_note_for` resolves one column name back to its note.
PROVENANCE: dict[str, FeatureNote] = {
    # ---- price (OHLCV, from the single price_venue) ----
    "open": _note(
        column="open", family="price",
        formula="first trade price in [t, t+bar) ordered by (ts_ms, trade_id)",
        citation=_CIT_NONE,
        approximation="ms timestamp resolution — prints inside the same millisecond are "
                      "ordered by trade_id, a deterministic but not chronological tiebreak",
        units="USD", source_leg="trades[price_venue]", aggregation="first"),
    "high": _note(
        column="high", family="price", formula="max trade price in [t, t+bar)",
        citation=_CIT_NONE,
        approximation="none — exact max of the recorded prints in the bar",
        units="USD", source_leg="trades[price_venue]", aggregation="max"),
    "low": _note(
        column="low", family="price", formula="min trade price in [t, t+bar)",
        citation=_CIT_NONE,
        approximation="none — exact min of the recorded prints in the bar",
        units="USD", source_leg="trades[price_venue]", aggregation="min"),
    "close": _note(
        column="close", family="price",
        formula="last trade price in [t, t+bar) ordered by (ts_ms, trade_id)",
        citation=_CIT_NONE,
        approximation="ms timestamp resolution — same tiebreak note as `open`. NaN when "
                      "the bar holds no print; never 0.0 and never forward-filled",
        units="USD", source_leg="trades[price_venue]", aggregation="last"),
    "volume": _note(
        column="volume", family="price", formula="sum of qty in [t, t+bar)",
        citation=_CIT_NONE,
        approximation="base-asset units (BTC) for every venue — OKX contract qty is "
                      "converted by the collector (OKX_CTVAL); spot and perp legs are "
                      "different instruments and are never summed together here",
        units="BTC", source_leg="trades[price_venue]", aggregation="sum"),
    # ---- quality ----
    "coverage": _note(
        column="coverage", family="quality",
        formula="1 - gap_ms/bar_ms for the price venue's TRADE leg",
        citation="gap threshold mirrors scripts/check_ticks.py GAP_MS = 30_000 ms",
        approximation=_EXPOST_LABEL + ". Liveness is inferred from inter-arrival silence "
                      "on trades[price_venue] ALONE — the depth leg is deliberately NOT "
                      "unioned in, because a venue whose trade leg dies while its book "
                      "keeps printing would otherwise score as fully covered and its "
                      "delta/volume/count columns would be filled with fabricated zeros. "
                      "The cost of that choice is the opposite error: a venue that is up "
                      "but genuinely silent for >30 s is scored as a hole. Erring toward "
                      "'unknown' is the intended direction",
        units="fraction", source_leg="trades[price_venue]"),
    "gap_ms": _note(
        column="gap_ms", family="quality",
        formula="milliseconds of the bar overlapping a detected feed hole",
        citation="scripts/check_ticks.py GAP_MS",
        approximation=_EXPOST_LABEL + ". Same trade-leg witness and same inference caveat "
                      "as `coverage`", units="ms",
        source_leg="trades[price_venue]", aggregation="sum"),
    "is_gap": _note(
        column="is_gap", family="quality", formula="gap_ms > 0",
        citation="scripts/check_ticks.py GAP_MS",
        approximation=_EXPOST_LABEL + ". Otherwise none — a strict comparison on gap_ms",
        units="bool", source_leg="trades[price_venue]"),
    "ret_spans_gap": _note(
        column="ret_spans_gap", family="quality",
        formula="this bar or the previous one is not fully covered, or either close is NaN",
        citation=_CIT_NONE,
        approximation=_EXPOST_LABEL + ". Otherwise none — a flag over coverage and close; "
                      "it marks the close-to-close return of THIS bar as not a valid "
                      "one-bar return. `close[t]` itself is tested, so a bar whose own "
                      "close is unknown can never be scored as a clean return",
        units="bool", source_leg="derived"),
    "segment": _note(
        column="segment", family="quality",
        formula="running index of maximal runs of FULLY covered bars; every bar with "
                "gap_ms > 0 terminates the current run and gets its own index",
        citation=_CIT_NONE,
        approximation=_EXPOST_LABEL + ". Otherwise none — a counter. A hole shorter than "
                      "one bar still breaks the segment: cumulative levels (cvd_*) are "
                      "only comparable WITHIN one segment, and a 20-minute hole inside a "
                      "1h bar is unobserved flow just as surely as a whole empty bar is",
        units="index", source_leg="derived"),
    "trade_count": _note(
        column="trade_count", family="quality", formula="number of prints in the bar",
        citation=_CIT_NONE,
        approximation="prints, not parent orders", units="count",
        source_leg="trades[price_venue]", aggregation="sum"),
    "dollar_volume": _note(
        column="dollar_volume", family="quality", formula="sum of price*qty in the bar",
        citation=_CIT_NONE,
        approximation="notional at the print price; no funding/fee adjustment",
        units="USD", source_leg="trades[price_venue]", aggregation="sum"),
    "vwap": _note(
        column="vwap", family="quality", formula="sum(price*qty) / sum(qty) in the bar",
        citation=_CIT_NONE,
        approximation="print-weighted within the bar only; NaN when the bar has no print",
        units="USD", source_leg="trades[price_venue]"),
    # ---- trade venue ----
    "delta_{v}": _note(
        column="delta_{v}", family="trade",
        formula="sum over prints of (+qty if aggressor bought else -qty)",
        citation=_CIT_CRS,
        approximation="none on the sign — the aggressor side is the REAL wire flag "
                      "normalized per venue by collector.py (Coinbase maker-side "
                      "inverted, Binance isBuyerMaker inverted, Bybit/OKX taker-side "
                      "as-is); no Lee-Ready tick rule and no bulk-volume classification. "
                      "Prints with a NULL aggressor flag are excluded from the signed "
                      "sums (they still count in volume/OHLC)",
        units="BTC", source_leg="trades[{v}]", aggregation="sum"),
    "cvd_{v}": _note(
        column="cvd_{v}", family="trade",
        formula="cumulative sum of delta_{v} within one coverage segment",
        citation=_CIT_CRS,
        approximation="session-anchored and RESET at every segment boundary — carrying "
                      "the level across an outage would imply flow that was never "
                      "observed. Only slope and divergence within a segment are "
                      "meaningful. A segment breaks on ANY unobserved millisecond, not "
                      "only on a whole empty bar, so a hole shorter than one bar resets "
                      "the level too",
        units="BTC", source_leg="trades[{v}]"),
    "buy_volume_{v}": _note(
        column="buy_volume_{v}", family="trade", formula="sum of qty for buy-aggressor prints",
        citation=_CIT_CRS, approximation="none — real aggressor flag", units="BTC",
        source_leg="trades[{v}]", aggregation="sum"),
    "sell_volume_{v}": _note(
        column="sell_volume_{v}", family="trade", formula="sum of qty for sell-aggressor prints",
        citation=_CIT_CRS, approximation="none — real aggressor flag", units="BTC",
        source_leg="trades[{v}]", aggregation="sum"),
    "volume_{v}": _note(
        column="volume_{v}", family="trade", formula="sum of qty over all prints",
        citation=_CIT_NONE,
        approximation="base-asset units; venues are separate instruments (spot vs perp) "
                      "and are never pooled",
        units="BTC", source_leg="trades[{v}]", aggregation="sum"),
    "delta_usd_{v}": _note(
        column="delta_usd_{v}", family="trade",
        formula="sum over prints of (+price*qty if aggressor bought else -price*qty)",
        citation=_CIT_CRS, approximation="none on the sign — real aggressor flag",
        units="USD", source_leg="trades[{v}]", aggregation="sum"),
    "delta_usd_le10k_{v}": _note(
        column="delta_usd_le10k_{v}", family="trade",
        formula="signed notional restricted to prints with notional <= $10k",
        citation="size taxonomy reused from the repo's own CvdStore "
                 "(dashboard/terminal-state.js): [1e4, 1e5, 1e6] USD",
        approximation="bucketed PER PRINT, not per parent order — one iceberg appears as "
                      "many retail prints. A print lands in the smallest threshold >= its "
                      "notional, so exactly $10,000.00 is retail",
        units="USD", source_leg="trades[{v}]", aggregation="sum"),
    "delta_usd_le100k_{v}": _note(
        column="delta_usd_le100k_{v}", family="trade",
        formula="signed notional for prints with $10k < notional <= $100k",
        citation="CvdStore taxonomy (dashboard/terminal-state.js)",
        approximation="per print, not per parent order", units="USD",
        source_leg="trades[{v}]", aggregation="sum"),
    "delta_usd_le1m_{v}": _note(
        column="delta_usd_le1m_{v}", family="trade",
        formula="signed notional for prints with $100k < notional <= $1M",
        citation="CvdStore taxonomy (dashboard/terminal-state.js)",
        approximation="per print, not per parent order", units="USD",
        source_leg="trades[{v}]", aggregation="sum"),
    "delta_usd_whale_{v}": _note(
        column="delta_usd_whale_{v}", family="trade",
        formula="signed notional for prints with notional > $1M",
        citation="CvdStore taxonomy (dashboard/terminal-state.js)",
        approximation="per print, not per parent order — a single $5M print and five $1M "
                      "prints are indistinguishable from the parent-order view",
        units="USD", source_leg="trades[{v}]", aggregation="sum"),
    "trade_count_{v}": _note(
        column="trade_count_{v}", family="trade", formula="number of prints in the bar",
        citation=_CIT_NONE, approximation="prints, not parent orders", units="count",
        source_leg="trades[{v}]", aggregation="sum"),
    "coverage_{v}": _note(
        column="coverage_{v}", family="quality",
        formula="1 - gap_ms/bar_ms for this venue's TRADE leg",
        citation="scripts/check_ticks.py GAP_MS",
        approximation=_EXPOST_LABEL + ". Liveness inferred from inter-arrival silence on "
                      "trades[{v}] ALONE — this is the gate that decides 0.0-vs-NaN for "
                      "every delta_/volume_/trade_count_/vpin_ column of {v}, so pooling "
                      "it with the depth leg would fabricate zeros on a dead trade leg. "
                      "The liquidation gate deliberately uses the WIDER "
                      "trades U depth witness (see coverage_liq_{v})",
        units="fraction", source_leg="trades[{v}]"),
    "vpin_{v}": _note(
        column="vpin_{v}", family="trade",
        formula="mean over the last N volume buckets of |V_buy - V_sell| / V, as-of "
                "joined backward to the bar close (only buckets closed strictly before "
                "the bar end are visible)",
        citation=_CIT_VPIN,
        approximation="(a) classification uses the REAL aggressor flag, not the paper's "
                      "bulk-volume classification — better input, same statistic; "
                      "(b) the bucket volume V is set CAUSALLY from the median daily "
                      "volume of strictly prior WHOLE UTC days spanned by the request, so "
                      "the first day of any range is warm-up NaN rather than a peek at "
                      "its own volume; (c) the volume clock is re-armed at UTC midnight "
                      "and reads the whole UTC days the request touches (never the "
                      "request window), so each day's trailing partial bucket is dropped "
                      "and the same bar returns the same value inside a wider range; "
                      "(d) at the paper's 50 buckets/day the series updates ~50x/day, so "
                      "on a 1min grid most bars carry a STALE value — see vpin_age_s_{v}; "
                      "(e) the rolling window can SPAN A FEED HOLE, and a mean over N "
                      "buckets carries no wall-clock information at all — "
                      "vpin_window_gap_s_{v} reports exactly that and is NOT netted out "
                      "of the value",
        units="fraction", source_leg="trades[{v}]", contested=_CIT_AB),
    "vpin_window_span_s_{v}": _note(
        column="vpin_window_span_s_{v}", family="trade",
        formula="(close of the newest bucket in the VPIN window - open of the oldest) "
                "/ 1000, carried from the same bucket vpin_{v} came from",
        citation=_CIT_VPIN,
        approximation="wall time is not part of the statistic — it is reported so a "
                      "window that took a week to fill cannot be mistaken for one that "
                      "took an hour", units="s", source_leg="trades[{v}]", contested=_CIT_AB),
    "vpin_window_gap_s_{v}": _note(
        column="vpin_window_gap_s_{v}", family="trade",
        formula="seconds of detected feed silence (inter-arrival > GAP_MS, counted in "
                "full the way scripts/check_ticks.py counts a hole) inside the VPIN "
                "window's span — between its buckets and within them",
        citation="scripts/check_ticks.py GAP_MS; window semantics per " + _CIT_VPIN,
        approximation="reported, not netted out and not used to NaN the value: the "
                      "module reports coverage rather than deciding for the caller "
                      "(same stance as ofi_gap_pairs_{b}). > 0 means the mean mixes "
                      "buckets from either side of an outage",
        units="s", source_leg="trades[{v}]", aggregation="sum", contested=_CIT_AB),
    "vpin_age_s_{v}": _note(
        column="vpin_age_s_{v}", family="trade",
        formula="(bar end - close time of the bucket the VPIN value came from) / 1000",
        citation=_CIT_VPIN,
        approximation="the staleness is reported, never hidden by interpolation",
        units="s", source_leg="trades[{v}]", contested=_CIT_AB),
    "vpin_buckets_{v}": _note(
        column="vpin_buckets_{v}", family="trade",
        formula="number of complete volume buckets that closed inside this bar",
        citation=_CIT_VPIN, approximation="incomplete trailing buckets are never counted",
        units="count", source_leg="trades[{v}]", aggregation="sum", contested=_CIT_AB),
    # ---- book venue (exactly one) ----
    "mid_{b}": _note(
        column="mid_{b}", family="book", formula="(best bid + best ask) / 2 of the last "
        "snapshot in the bar", citation=_CIT_NONE,
        approximation="~1 Hz snapshots, not events — the intra-second path is not stored. "
                      + _CROSS_INSTRUMENT_LABEL,
        units="USD", source_leg="depth_snapshots[{b}]", aggregation="last"),
    "spread_{b}": _note(
        column="spread_{b}", family="book", formula="best ask - best bid, last snapshot",
        citation=_CIT_NONE, approximation="~1 Hz snapshots, not events", units="USD",
        source_leg="depth_snapshots[{b}]", aggregation="last"),
    "spread_bps_{b}": _note(
        column="spread_bps_{b}", family="book", formula="1e4 * spread / mid, last snapshot",
        citation=_CIT_NONE, approximation="~1 Hz snapshots, not events", units="bp",
        source_leg="depth_snapshots[{b}]", aggregation="last"),
    "microprice_{b}": _note(
        column="microprice_{b}", family="book",
        formula="(P_bid*q_ask + P_ask*q_bid) / (q_bid + q_ask), last snapshot",
        citation=_CIT_STOIKOV,
        approximation="weighted mid — Stoikov first-order form, not the fitted "
                      "micro-price. The full estimator needs a fitted Markov correction "
                      "over (imbalance, spread) states; that is a model, not an "
                      "observable, and it is not fitted here. " + _CROSS_INSTRUMENT_LABEL,
        units="USD", source_leg="depth_snapshots[{b}]", aggregation="last"),
    "micro_minus_mid_{b}": _note(
        column="micro_minus_mid_{b}", family="book", formula="microprice - mid, last snapshot",
        citation=_CIT_STOIKOV,
        approximation="weighted mid — Stoikov first-order form, not the fitted "
                      "micro-price. WITHIN-VENUE by construction, which is why this is "
                      "the safe book-pressure read when price_venue and book_venue are "
                      "different instruments",
        units="USD", source_leg="depth_snapshots[{b}]", aggregation="last"),
    "book_imbalance_{b}": _note(
        column="book_imbalance_{b}", family="book",
        formula="q_bid / (q_bid + q_ask) at L1, last snapshot",
        citation=_CIT_GB,
        approximation="L1 only, ~1 Hz — queue POSITION is unknowable from L2 snapshots",
        units="fraction", source_leg="depth_snapshots[{b}]", aggregation="last"),
    "ofi_{b}": _note(
        column="ofi_{b}", family="book",
        formula="sum over consecutive snapshot pairs of "
                "1{Pb_n>=Pb_p}qb_n - 1{Pb_n<=Pb_p}qb_p - 1{Pa_n<=Pa_p}qa_n + 1{Pa_n>=Pa_p}qa_p",
        citation=_CIT_CKS,
        approximation=(
            _OFI_LABEL + " — the paper sums per-EVENT contributions over every L1 update; "
            "the store holds ~1 Hz SNAPSHOTS, so this is the net inter-snapshot "
            "contribution. The DIRECTION OF THE BIAS IS NOT KNOWN, in either sign or "
            "magnitude. [SUPERSEDED] an earlier version of this note claimed the sampled "
            "|OFI| understates the event-level value; that was falsified and is withdrawn. "
            "Intra-second add/cancel churn at an unchanged quote is invisible "
            "(understates), while a price ROUND-TRIP inside the sampling interval is read "
            "as a pure queue change of the full size difference and can overstate: asks "
            "frozen, bids 100@10 -> 101@5 -> 100@1 gives event-level 0 but sampled -9, "
            "because the paper's indicator convention fires BOTH indicators when "
            "P_n == P_(n-1). Pairs separated by more than GAP_MS are excluded and counted "
            "in ofi_gap_pairs_{b}. Pairs are anchored to the UTC DAY, not to the request "
            "window, so bar 0 of a mid-day request keeps the pair that straddles the "
            "window start and the value does not change inside a wider range; the pair "
            "straddling midnight is never formed"),
        units="BTC", source_leg="depth_snapshots[{b}]", aggregation="sum"),
    "ofi_n_{b}": _note(
        column="ofi_n_{b}", family="book",
        formula="number of snapshot pairs that contributed to ofi_{b}",
        citation=_CIT_CKS,
        approximation=_OFI_LABEL + " — ofi_{b} is NaN, not 0.0, when this is 0",
        units="count", source_leg="depth_snapshots[{b}]", aggregation="sum"),
    "ofi_gap_pairs_{b}": _note(
        column="ofi_gap_pairs_{b}", family="book",
        formula="snapshot pairs in the bar separated by more than GAP_MS and therefore "
                "EXCLUDED from ofi_{b}",
        citation=_CIT_CKS,
        approximation=_OFI_LABEL + " — excluding them is a choice, stated here: a book "
                      "'event' read across a multi-minute hole is not an event",
        units="count", source_leg="depth_snapshots[{b}]", aggregation="sum"),
    "depth_bid_{b}": _note(
        column="depth_bid_{b}", family="book",
        formula="sum of bid qty over the top depth_levels levels, last snapshot",
        citation=_CIT_NS,
        approximation="fixed LEVEL COUNT, not a fixed bp band; stored depth differs per "
                      "venue (binancef 20, bybit/okx 50) so the value is not comparable "
                      "across venues",
        units="BTC", source_leg="depth_snapshots[{b}]", aggregation="last"),
    "depth_ask_{b}": _note(
        column="depth_ask_{b}", family="book",
        formula="sum of ask qty over the top depth_levels levels, last snapshot",
        citation=_CIT_NS, approximation="see depth_bid_{b}", units="BTC",
        source_leg="depth_snapshots[{b}]", aggregation="last"),
    "depth_slope_bid_{b}": _note(
        column="depth_slope_bid_{b}", family="book",
        formula="OLS through the origin of cumulative depth Q_j on distance-from-mid "
                "x_j (bp): beta = sum(x_j*Q_j)/sum(x_j^2), last snapshot",
        citation=_CIT_NS,
        approximation="fixed level count K rather than a fixed bp band — measured on this "
                      "store the entire top-20 of the binancef leg spans ~0.37 bp, so a "
                      "fixed-band estimator is degenerate there. Snapshots with fewer than "
                      "K levels give NaN, never a zero-padded book. Intercept is pinned at "
                      "the origin because cumulative depth at zero distance is zero by "
                      "construction",
        units="BTC/bp", source_leg="depth_snapshots[{b}]", aggregation="last"),
    "depth_slope_ask_{b}": _note(
        column="depth_slope_ask_{b}", family="book",
        formula="same as depth_slope_bid_{b} on the ask side",
        citation=_CIT_NS, approximation="see depth_slope_bid_{b}", units="BTC/bp",
        source_leg="depth_snapshots[{b}]", aggregation="last"),
    "depth_slope_imb_{b}": _note(
        column="depth_slope_imb_{b}", family="book",
        formula="(beta_bid - beta_ask) / (beta_bid + beta_ask)",
        citation=_CIT_NS,
        approximation="inherits every depth_slope caveat; undefined (NaN) when the two "
                      "slopes sum to zero",
        units="fraction in [-1, 1]", source_leg="depth_snapshots[{b}]", aggregation="last"),
    "depth_levels_{b}": _note(
        column="depth_levels_{b}", family="book",
        formula="min(stored bid levels, stored ask levels) of the last snapshot",
        citation=_CIT_NONE,
        approximation="emitted precisely so the cross-venue incomparability of the slope "
                      "columns stays visible instead of implicit",
        units="count", source_leg="depth_snapshots[{b}]", aggregation="last"),
    "book_snapshots_{b}": _note(
        column="book_snapshots_{b}", family="book",
        formula="number of depth snapshots recorded inside the bar",
        citation=_CIT_NONE,
        approximation="the collector downsamples the book firehose to ~1 Hz, so this is a "
                      "sampling count, not an event count",
        units="count", source_leg="depth_snapshots[{b}]", aggregation="sum"),
    "coverage_book_{b}": _note(
        column="coverage_book_{b}", family="quality",
        formula="1 - gap_ms/bar_ms for the book venue's depth stream",
        citation="scripts/check_ticks.py GAP_MS",
        approximation="the depth stream is its own witness (1 Hz heartbeat), so this is a "
                      "direct liveness measure rather than an inference from trades",
        units="fraction", source_leg="depth_snapshots[{b}]"),
    # ---- liquidations ----
    "liq_count_{v}": _note(
        column="liq_count_{v}", family="liquidation",
        formula="number of liquidation prints in the bar",
        citation=_CIT_BP,
        approximation="pure counting, no cascade model and no leverage assumption. A "
                      "venue prints a liquidation only when its own engine reports one, "
                      "so 0.0 means 'the leg was alive and nothing printed'; when the leg "
                      "is dead the value is NaN, never 0.0. Liveness is the ONLY gate — "
                      "row count never decides — so a local zero-row liquidations table "
                      "and an absent hf:// partition (the exporter skips empty tables) "
                      "both read 0.0 on a live leg. The witness here is deliberately the "
                      "WIDER trades U depth_snapshots stream (see coverage_liq_{v}): "
                      "bybit prints 3-2000 liquidations a DAY, so the leg cannot witness "
                      "its own liveness. Residual asymmetry, stated: a day file whose "
                      "schema predates the table reads NaN locally and would read 0.0 "
                      "from the Hub",
        units="count", source_leg="liquidations[{v}]", aggregation="sum"),
    "coverage_liq_{v}": _note(
        column="coverage_liq_{v}", family="quality",
        formula="1 - gap_ms/bar_ms for {v}'s trades U depth_snapshots witness — the gate "
                "that decides 0.0-vs-NaN for the liq_* columns of {v}",
        citation="scripts/check_ticks.py GAP_MS",
        approximation=_EXPOST_LABEL + ". A sparse stream cannot witness its own liveness "
                      "(bybit prints 3-2000 liquidations a DAY, well past GAP_MS), so the "
                      "witness is the venue's two dense streams. That inference is why "
                      "this is a SEPARATE column from coverage_{v} instead of being "
                      "quietly reused for the trade family",
        units="fraction", source_leg="trades[{v}] U depth_snapshots[{v}]"),
    "liq_notional_usd_{v}": _note(
        column="liq_notional_usd_{v}", family="liquidation",
        formula="sum of notional_usd over liquidation prints in the bar",
        citation=_CIT_BP,
        approximation="notional as reported by the venue; no model", units="USD",
        source_leg="liquidations[{v}]", aggregation="sum"),
    "liq_long_notional_{v}": _note(
        column="liq_long_notional_{v}", family="liquidation",
        formula="sum of notional_usd where the LIQUIDATED position was long",
        citation=_CIT_BP,
        approximation="`side` is the liquidated position as normalized by collector.py "
                      "(bybit allLiquidation side 'Buy' means a SHORT was liquidated — the "
                      "printed order is the forced buy-back), not the printed order side",
        units="USD", source_leg="liquidations[{v}]", aggregation="sum"),
    "liq_short_notional_{v}": _note(
        column="liq_short_notional_{v}", family="liquidation",
        formula="sum of notional_usd where the LIQUIDATED position was short",
        citation=_CIT_BP, approximation="see liq_long_notional_{v}", units="USD",
        source_leg="liquidations[{v}]", aggregation="sum"),
}


def _resolve_template(template: str, *, v: str = "", b: str = "") -> str:
    return template.replace("{v}", v).replace("{b}", b)


def _resolved(note: FeatureNote, *, v: str = "", b: str = "") -> FeatureNote:
    return FeatureNote(**{k: _resolve_template(val, v=v, b=b) if isinstance(val, str) else val
                          for k, val in asdict(note).items()})


def _note_for(column: str, trade_venues: Sequence[str],
              book_venue: Optional[str]) -> Optional[FeatureNote]:
    """Resolve an emitted column name back to its :class:`FeatureNote`."""
    if column in PROVENANCE:
        return PROVENANCE[column]
    for tpl, note in PROVENANCE.items():
        if "{v}" in tpl:
            for v in trade_venues:
                if _resolve_template(tpl, v=v) == column:
                    return _resolved(note, v=v)
        if "{b}" in tpl and book_venue and _resolve_template(tpl, b=book_venue) == column:
            return _resolved(note, b=book_venue)
    return None


def provenance_table(bars: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """One row per feature: column, family, formula, citation, approximation, ...

    With ``bars`` given, the templates are resolved against the venues that frame
    actually carries and the result covers **exactly** its columns — so a column
    with no note shows up as a missing row and the contract test fails loudly.
    Without ``bars`` the unresolved templates are returned.
    """
    if bars is None:
        rows = [asdict(n) for n in PROVENANCE.values()]
        return pd.DataFrame(rows, columns=list(asdict(next(iter(PROVENANCE.values())))))

    meta = bars.attrs.get("orderflow", {})
    tvs: Sequence[str] = tuple(meta.get("trade_venues", ()))
    bv: Optional[str] = meta.get("book_venue")
    rows = []
    for col in bars.columns:
        note = _note_for(str(col), tvs, bv)
        if note is not None:
            rows.append(asdict(note) | {"column": str(col)})
        else:
            rows.append({"column": str(col), "family": "", "formula": "",
                         "citation": "", "approximation": "", "units": "",
                         "source_leg": "", "aggregation": "", "contested": ""})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Time / grid helpers                                                           #
# --------------------------------------------------------------------------- #
def _to_utc(value: Union[str, pd.Timestamp, datetime]) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")


def _bar_ms(bar: str) -> int:
    if bar not in BAR_FREQS:
        raise OrderFlowError(f"bar={bar!r} not in {BAR_FREQS} (each extra clock is an extra trial)")
    return int(pd.Timedelta(bar) / pd.Timedelta("1ms"))


def periods_per_year(bar: str) -> int:
    """Bars per year for one of :data:`BAR_FREQS` (crypto trades 24/7, 365 d/yr).

    ``1min`` -> 525_600, ``5min`` -> 105_120, ``15min`` -> 35_040, ``1h`` -> 8_760.
    This is what ``backtest.walk_forward(..., periods_per_year=...)`` wants; it
    complements ``scripts/compare.py`` which only knows ``1h``/``1d``.
    """
    return _MS_PER_YEAR // _bar_ms(bar)


def _grid(start_ms: int, end_ms: int, bar_ms: int) -> np.ndarray:
    """Left-closed bar-open epoch-ms grid over ``[start_ms, end_ms)``."""
    if end_ms <= start_ms:
        raise OrderFlowError("end must be strictly after start")
    n = int(np.ceil((end_ms - start_ms) / bar_ms))
    return start_ms + np.arange(n, dtype="int64") * bar_ms


def _dates_between(start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    """Every UTC date the half-open ``[start, end)`` range touches."""
    d0 = start.normalize()
    last = (end - pd.Timedelta("1ms")).normalize()
    out, d = [], d0
    while d <= last:
        out.append(d.strftime("%Y-%m-%d"))
        d = d + pd.Timedelta(days=1)
    return out


def _now_ms() -> int:
    """Wall clock, epoch ms — a function so tests can freeze it."""
    return int(time.time() * 1000)


def _day_bounds(date: str) -> tuple[int, int]:
    if not _DATE_RE.match(date):
        raise OrderFlowError(f"bad date {date!r} — expected YYYY-MM-DD")
    a = int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    return a, a + MS_PER_DAY


def _day_is_closed(date: str, now_ms: Optional[int] = None) -> bool:
    """Is this UTC day finished *and* past the writer's rotation grace window?

    Today is never closed. Yesterday is closed only once the collector's
    midnight grace window (plus flush slack) has elapsed — the same rule
    ``scripts/upload_hf.py`` uses to decide a day file is immutable.
    """
    now = _now_ms() if now_ms is None else now_ms
    return now >= _day_bounds(date)[1] + GRACE_CLOSE_MIN * 60_000


# --------------------------------------------------------------------------- #
# IO — one SQL body, two backends                                               #
# --------------------------------------------------------------------------- #
def _hf_url(repo: str, date: str, table: str) -> str:
    """``hf://`` path of one archived day/table partition (the §3c hive layout).

    Same shape as ``scripts/backfill_levels.py._hf_url``, generalized past
    ``trades``. The repo id is validated before interpolation because
    ``read_parquet`` takes no bound parameters.
    """
    if not _REPO_RE.match(repo):
        raise OrderFlowError(f"bad hf repo id {repo!r}")
    if table not in _TABLE_SCHEMA:
        raise OrderFlowError(f"unknown table {table!r}")
    if date != "*" and not _DATE_RE.match(date):
        raise OrderFlowError(f"bad date {date!r}")
    return f"hf://datasets/{repo}/data/date={date}/{table}.parquet"


def _hf_partitions(con: Any, repo: str) -> dict[str, set[str]]:
    """``{table: {dates present on the Hub}}``, one cheap glob over paths.

    The exporter skips empty tables, so an absent partition is a real, honest
    statement ("this table had no rows that day") and is recorded as such rather
    than being flattened into a zero.
    """
    pat = f"hf://datasets/{repo}/data/date=*/*.parquet"
    rows = con.execute("SELECT file FROM glob(?)", [pat]).fetchall()
    out: dict[str, set[str]] = {t: set() for t in _TABLE_SCHEMA}
    rx = re.compile(r"date=(\d{4}-\d{2}-\d{2})/([a-z_]+)\.parquet$")
    for (f,) in rows:
        m = rx.search(str(f))
        if m and m.group(2) in out:
            out[m.group(2)].add(m.group(1))
    return out


def _empty_view_sql(table: str) -> str:
    cols = _TABLE_SCHEMA[table]
    sel = ", ".join(f"NULL::{typ} AS {name}" for name, typ in cols)
    return f"SELECT {sel} WHERE false"


def _projection(table: str) -> str:
    """Explicit canonical column list — never ``SELECT *``.

    The hive parquet reader synthesises an extra ``date`` column that the day
    files do not have, so projecting explicitly is what keeps the two backends
    the same shape.
    """
    return ", ".join(name for name, _ in _TABLE_SCHEMA[table])


@dataclass
class _Source:
    """An open scratch connection plus the manifest describing where rows came from.

    ``day_rel`` maps a table to the relation holding the **whole UTC days** the
    request touches, as opposed to the window-filtered ``_REL`` relation. The two
    are the same object whenever the request is already day-aligned. Day-anchored
    relations are what make the volume clock and the OFI snapshot pairing depend
    on the UTC day rather than on where the caller happened to start the window.
    """

    con: Any
    manifest: dict[str, Any]
    day_rel: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.day_rel is None:
            self.day_rel = dict(_REL)

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:  # pragma: no cover - best effort
            pass


def _symbol_map(symbol: Optional[Union[str, dict]],
                exchanges: Sequence[str]) -> dict[str, str]:
    """Normalize ``symbol=`` to ``{venue: instrument id}`` and validate its shape.

    Accepts a plain string (the same instrument on every requested venue) or a
    per-venue mapping — venue ids are venue-native (``BTCUSDT`` on bybit/binancef,
    ``BTC-USD`` on coinbase, ``BTC-USDT-SWAP`` on okx), so a single string is not
    always expressible and the mapping form is not a nicety.
    """
    if symbol is None:
        return {}
    if isinstance(symbol, str):
        out = {str(v): symbol for v in exchanges}
    elif isinstance(symbol, dict):
        out = {str(k): str(v) for k, v in symbol.items()}
    else:
        raise OrderFlowError("symbol must be a string, a {venue: symbol} mapping, or None")
    for venue, sym in out.items():
        if not _VENUE_RE.match(venue):
            raise OrderFlowError(f"bad venue code {venue!r} in symbol=")
        if not _SYMBOL_RE.match(sym):
            raise OrderFlowError(f"bad symbol id {sym!r} — expected a venue-native id "
                                 "like 'BTCUSDT', 'BTC-USD' or 'BTC-USDT-SWAP'")
    return out


def _symbol_predicate(sym_map: dict[str, str]) -> str:
    """SQL fragment restricting each venue to its requested instrument."""
    if not sym_map:
        return ""
    parts = [f"(exchange = '{v}' AND symbol = '{s}')" for v, s in sorted(sym_map.items())]
    return " AND (" + " OR ".join(parts) + " OR exchange NOT IN (" + \
        ", ".join(f"'{v}'" for v in sorted(sym_map)) + "))"


def _assert_one_symbol_per_venue(con: Any, rel: str, table: str) -> dict[str, str]:
    """Refuse to pool two instruments; return the surviving ``{venue: symbol}``.

    Rail 5 is enforced across venues by construction (one ``book_venue``, every
    cross-venue column suffixed). It was **not** enforced across *symbols*: no
    query filtered on ``symbol``, so a venue carrying two instruments summed
    their volume, mixed their OHLC, and — worst — the book de-dup
    ``QUALIFY row_number() OVER (PARTITION BY ts_ms ...)`` kept whichever
    instrument's L1 sorted first, silently. Latent on today's archive (one symbol
    per venue) but the collector accepts five venue codes and the terminal
    already went multi-symbol, so this is a missing guard, not an unnecessary one.
    """
    rows = con.execute(
        f"SELECT exchange, symbol, count(*) FROM {rel} GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    by_venue: dict[str, list[tuple[str, int]]] = {}
    for ex, sym, n in rows:
        by_venue.setdefault(str(ex), []).append((str(sym), int(n)))
    bad = {v: s for v, s in by_venue.items() if len(s) > 1}
    if bad:
        detail = "; ".join(
            f"{v}: " + ", ".join(f"{s} ({n} rows)" for s, n in sorted(syms))
            for v, syms in sorted(bad.items()))
        raise OrderFlowError(
            f"{table} carries more than one symbol per venue in this window ({detail}). "
            "Two instruments are never pooled into one tape or one book — pass "
            "symbol='<venue-native id>' or symbol={'<venue>': '<id>'} to choose.")
    return {v: syms[0][0] for v, syms in by_venue.items()}


def _open_source(
    dates: Sequence[str],
    *,
    source: str,
    store_dir: Path,
    hf_repo: str,
    exchanges: Sequence[str],
    t0_ms: int,
    t1_ms: int,
    tables: Sequence[str],
    symbol: Optional[Union[str, dict]] = None,
    day_tables: Sequence[str] = (),
    materialize: Optional[bool] = None,
    now_ms: Optional[int] = None,
) -> _Source:
    """Open one in-memory DuckDB with ``of_trades`` / ``of_depth`` / ``of_liq``.

    The two backends are unified at the *view* level, so every feature query is
    written once:

    * **local** — ``ATTACH`` each closed day file ``READ_ONLY`` (the
      ``check_ticks.connect_dir_readonly`` idiom). A file the live writer holds a
      lock on is **skipped, never fought over**, and recorded in the manifest as
      ``skipped_locked``. Today's file is not a candidate at all.
    * **hf** — ``read_parquet`` over the day partitions that actually exist.
      Missing partitions produce a typed empty view so the SQL still runs, and
      the absence is recorded per (date, table).
    * **auto** — per day: closed local file when present, else ``hf://``.

    On that last point, precisely: the two backends were measured to agree on
    2026-07-25 (the only day both currently exist) across all emitted columns,
    and the zero-vs-unknown rule is now backend-symmetric by construction. They
    are **not** promised to be bit-identical, and an earlier version of this
    docstring said they were — ``[SUPERSEDED]``. DuckDB's parallel float
    aggregation is not order-stable: summing one real day's ``qty`` at
    ``threads=1`` vs ``threads=8`` differs by ~4e-08 absolute, and truncating a
    real request moves ``ofi`` by ~1e-11 on a mean of ~824 (≈1e-14 relative). The
    honest guarantee is agreement to a few ULP, not byte identity.

    ``symbol`` filters the instrument. Two symbols on one venue are never pooled:
    the caller either names one or gets an error naming both (rail 5).

    ``day_tables`` additionally builds ``<rel>_day`` relations spanning the whole
    UTC days the request touches — the anchor for the volume clock and the OFI
    snapshot pairing. When the request is already day-aligned these are the same
    relations, so the common whole-day call pays nothing.

    Multi-day concatenation happens here (``UNION ALL`` / a parquet file list),
    not in pandas, so the bar grid is built once over the whole range and no
    midnight seam artifact can appear.
    """
    _require_deps()
    if source not in ("auto", "local", "hf"):
        raise OrderFlowError(f"source={source!r} must be 'auto', 'local' or 'hf'")
    for ex in exchanges:
        if not _VENUE_RE.match(ex):
            raise OrderFlowError(f"bad venue code {ex!r}")
    sym_map = _symbol_map(symbol, exchanges)

    con = duckdb.connect()
    con.execute("SET enable_progress_bar=false")

    store_dir = Path(store_dir)
    per_day: dict[str, dict[str, Any]] = {}
    attached: list[tuple[str, str]] = []       # (alias, date)
    skipped_locked: list[str] = []
    hf_dates: list[str] = []
    hf_present: dict[str, set[str]] = {}

    want_local = source in ("auto", "local")
    want_hf = source in ("auto", "hf")

    for i, date in enumerate(dates):
        rec: dict[str, Any] = {"date": date, "source": None, "tables_present": {}}
        per_day[date] = rec
        path = store_dir / f"{date}.duckdb"
        used_local = False
        if want_local and path.exists():
            if not _day_is_closed(date, now_ms):
                rec["note"] = "local day file exists but the UTC day is not closed yet"
            else:
                alias = f"d{i}"
                try:
                    esc = str(path).replace("'", "''")
                    con.execute(f"ATTACH '{esc}' AS {alias} (READ_ONLY)")
                    attached.append((alias, date))
                    rec["source"] = "local"
                    rec["path"] = str(path)
                    used_local = True
                except Exception as exc:  # duckdb.Error, but keep the guard broad
                    if "lock" in str(exc).lower():
                        skipped_locked.append(str(path))
                        rec["note"] = "day file locked by the live writer — skipped, not fought over"
                    else:
                        raise OrderFlowError(f"cannot attach {path}: {exc}") from exc
        if not used_local and want_hf:
            if not hf_present:
                hf_present = _hf_partitions(con, hf_repo)
            hf_dates.append(date)
            rec["source"] = "hf"
        elif not used_local and not want_hf:
            rec["source"] = None
            rec.setdefault("note", "no readable source for this day under "
                                   f"source={source!r}")

    # Which attached catalog holds which table (a v1-migrated day may lack a v2 table).
    have: dict[str, list[str]] = {t: [] for t in tables}
    if attached:
        aliases = {a for a, _ in attached}
        for cat, name in con.execute(
            "SELECT table_catalog, table_name FROM information_schema.tables "
            "WHERE table_schema = 'main'"
        ).fetchall():
            if cat in aliases and name in have:
                have[name].append(cat)

    alias_date = dict(attached)
    for table in tables:
        parts: list[str] = []
        for alias in sorted(have[table], key=lambda a: int(a[1:])):
            parts.append(f"SELECT {_projection(table)} FROM {alias}.{table}")
            per_day[alias_date[alias]]["tables_present"][table] = True
        for alias, date in attached:
            per_day[date]["tables_present"].setdefault(table, alias in have[table])
        urls = []
        for date in hf_dates:
            present = date in hf_present.get(table, set())
            per_day[date]["tables_present"][table] = present
            if present:
                urls.append(_hf_url(hf_repo, date, table))
        if urls:
            lst = ", ".join("'" + u.replace("'", "''") + "'" for u in urls)
            parts.append(f"SELECT {_projection(table)} FROM read_parquet([{lst}])")
        body = " UNION ALL ".join(parts) if parts else _empty_view_sql(table)
        con.execute(f"CREATE VIEW _src_{table} AS {body}")

    # Zero-vs-unknown for the liquidation family must not depend on WHICH backend
    # answered. Locally, "table absent" and "table present with 0 rows" are
    # distinguishable; on the Hub they are not, because upload_hf.py skips empty
    # tables. So the day-level question is "did this source record the day at
    # all", answered symmetrically: structural table presence for a local day,
    # day-present-on-the-Hub for an hf day.
    for date, rec in per_day.items():
        if rec["source"] == "local":
            rec["liquidations_recorded"] = bool(rec["tables_present"].get("liquidations", False)) \
                if "liquidations" in tables else None
        elif rec["source"] == "hf":
            rec["liquidations_recorded"] = any(
                date in hf_present.get(t, set()) for t in _TABLE_SCHEMA) \
                if "liquidations" in tables else None
        else:
            rec["liquidations_recorded"] = False if "liquidations" in tables else None
        rec["resolved"] = bool(rec["source"]) and any(rec["tables_present"].values())

    # Filter once, to the requested window, venues and instrument.
    ex_list = ", ".join("'" + e + "'" for e in sorted(set(exchanges)))
    sym_pred = _symbol_predicate(sym_map)
    do_materialize = bool(hf_dates) if materialize is None else bool(materialize)
    day0, day1 = (_day_bounds(dates[0])[0], _day_bounds(dates[-1])[1]) if dates else (t0_ms, t1_ms)
    day_aligned = int(t0_ms) == int(day0) and int(t1_ms) == int(day1)
    day_rel = dict(_REL)

    def _build(rel: str, lo: int, hi: int, table: str) -> None:
        where = f"ts_ms >= {int(lo)} AND ts_ms < {int(hi)}"
        if ex_list:
            where += f" AND exchange IN ({ex_list})"
        where += sym_pred
        body = f"SELECT {_projection(table)} FROM _src_{table} WHERE {where}"
        if do_materialize:
            # One network pass per table instead of one per feature query. Local
            # sources stay lazy — the day files are already on this disk.
            con.execute(f"CREATE TEMP TABLE {rel} AS {body}")
        else:
            con.execute(f"CREATE VIEW {rel} AS {body}")

    for table in tables:
        _build(_REL[table], t0_ms, t1_ms, table)
        if table in day_tables and not day_aligned:
            day_rel[table] = _REL[table] + "_day"
            _build(day_rel[table], day0, day1, table)

    symbols: dict[str, dict[str, str]] = {}
    for table in tables:
        symbols[table] = _assert_one_symbol_per_venue(con, _REL[table], table)
        if day_rel[table] != _REL[table]:
            _assert_one_symbol_per_venue(con, day_rel[table], table)

    rows: list[dict[str, Any]] = []
    for table in tables:
        for date_i, ex, n in con.execute(
            f"SELECT ts_ms // {MS_PER_DAY}, exchange, count(*) FROM {_REL[table]} "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall():
            rows.append({
                "date": datetime.fromtimestamp(int(date_i) * 86400, tz=timezone.utc).strftime("%Y-%m-%d"),
                "table": table, "exchange": ex, "rows": int(n),
            })

    unresolved = [d["date"] for d in per_day.values() if not d["resolved"]]
    manifest = {
        "days": list(per_day.values()),
        "rows_by_day_table_exchange": rows,
        "skipped_locked": skipped_locked,
        "materialized": do_materialize,
        "hf_repo": hf_repo if hf_dates else None,
        "store_dir": str(store_dir),
        "source_requested": source,
        "symbol_requested": sym_map or None,
        "symbols_observed": symbols,
        "day_anchored": {t: day_rel[t] != _REL[t] for t in tables},
        # A range with an unresolved day is NOT final: the day may be open, the
        # file locked, or the Hub partition not uploaded yet. Whatever the reason,
        # the frame reports it as a gap and the result must never be cached — the
        # cache key cannot see it, so a stale all-gap frame would be served
        # forever once the day closes.
        "unresolved_days": unresolved,
        "final": not unresolved,
    }
    return _Source(con=con, manifest=manifest, day_rel=day_rel)


def available_days(
    *,
    store_dir: Union[str, Path] = STORE_DIR,
    hf_repo: str = HF_REPO,
    source: str = "auto",
    by_exchange: bool = False,
    now_ms: Optional[int] = None,
) -> pd.DataFrame:
    """What the store actually holds, per ``(date, source, table)``. Read-only.

    Columns: ``date, source, table, present, exchange, rows``. ``exchange`` is
    ``"*"`` (and ``rows`` is the whole-table count) unless ``by_exchange=True``,
    because a per-venue breakdown of the Hub means scanning ~40 M trade rows
    while the aggregate comes straight out of the parquet footers. Today's day
    file is never listed as readable: it is open, locked, and still growing.
    """
    _require_deps()
    con = duckdb.connect()
    con.execute("SET enable_progress_bar=false")
    out: list[dict[str, Any]] = []
    try:
        local_dates: list[str] = []
        if source in ("auto", "local"):
            for p in sorted(Path(store_dir).glob("*.duckdb")):
                if _DATE_RE.match(p.stem) and _day_is_closed(p.stem, now_ms):
                    local_dates.append(p.stem)
        hf_present: dict[str, set[str]] = {}
        if source in ("auto", "hf"):
            hf_present = _hf_partitions(con, hf_repo)

        hf_dates = sorted({d for s in hf_present.values() for d in s})
        for date in sorted(set(local_dates) | set(hf_dates)):
            use_local = date in local_dates and source in ("auto", "local")
            src = "local" if use_local else "hf"
            for table in _TABLE_SCHEMA:
                if use_local:
                    esc = str(Path(store_dir) / f"{date}.duckdb").replace("'", "''")
                    try:
                        con.execute(f"ATTACH '{esc}' AS probe (READ_ONLY)")
                    except Exception:
                        out.append({"date": date, "source": "local", "table": table,
                                    "present": False, "exchange": "*", "rows": 0,
                                    "note": "locked by the live writer"})
                        continue
                    try:
                        names = {r[0] for r in con.execute(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_catalog='probe' AND table_schema='main'").fetchall()}
                        if table not in names:
                            out.append({"date": date, "source": src, "table": table,
                                        "present": False, "exchange": "*", "rows": 0})
                        else:
                            rel = f"probe.{table}"
                            out.extend(_count_rows(con, rel, date, src, table, by_exchange))
                    finally:
                        con.execute("DETACH probe")
                else:
                    if date not in hf_present.get(table, set()):
                        out.append({"date": date, "source": src, "table": table,
                                    "present": False, "exchange": "*", "rows": 0})
                        continue
                    url = _hf_url(hf_repo, date, table).replace("'", "''")
                    out.extend(_count_rows(con, f"read_parquet('{url}')", date, src, table, by_exchange))
    finally:
        con.close()
    cols = ["date", "source", "table", "present", "exchange", "rows"]
    df = pd.DataFrame(out)
    if df.empty:
        return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols + [c for c in df.columns if c not in cols]].sort_values(
        ["date", "table", "exchange"]).reset_index(drop=True)


def _count_rows(con: Any, rel: str, date: str, src: str, table: str,
                by_exchange: bool) -> list[dict[str, Any]]:
    if by_exchange:
        rows = con.execute(f"SELECT exchange, count(*) FROM {rel} GROUP BY 1 ORDER BY 1").fetchall()
        if not rows:
            return [{"date": date, "source": src, "table": table, "present": True,
                     "exchange": "*", "rows": 0}]
        return [{"date": date, "source": src, "table": table, "present": True,
                 "exchange": ex, "rows": int(n)} for ex, n in rows]
    n = con.execute(f"SELECT count(*) FROM {rel}").fetchone()[0]
    return [{"date": date, "source": src, "table": table, "present": True,
             "exchange": "*", "rows": int(n)}]


# --------------------------------------------------------------------------- #
# Coverage / gap model — gaps stay gaps                                         #
# --------------------------------------------------------------------------- #
def _holes_from_ts(ts: np.ndarray, t0: int, t1: int, gap_ms: int = GAP_MS) -> list[tuple[int, int]]:
    """Feed holes over ``[t0, t1)`` implied by a sorted witness timestamp array.

    A hole is any inter-arrival silence longer than ``gap_ms``, plus the head
    ``[t0, first)`` and tail ``(last, t1)`` stretches when they exceed it. An
    empty witness means the entire range is one hole — the honest reading of "we
    have no evidence this leg was ever alive here".
    """
    if ts.size == 0:
        return [(t0, t1)]
    holes: list[tuple[int, int]] = []
    first, last = int(ts[0]), int(ts[-1])
    if first - t0 > gap_ms:
        holes.append((t0, first))
    if ts.size > 1:
        d = np.diff(ts)
        idx = np.nonzero(d > gap_ms)[0]
        for i in idx:
            holes.append((int(ts[i]), int(ts[i + 1])))
    if t1 - last > gap_ms:
        holes.append((last, t1))
    return holes


def _gap_ms_per_bar(holes: Sequence[tuple[int, int]], grid: np.ndarray, bar_ms: int) -> np.ndarray:
    """Milliseconds of each bar covered by a hole (vectorized interval overlap).

    Deliberately computed in numpy rather than SQL. DuckDB's ``least``/
    ``greatest`` are NULL-**ignoring** (``least(x, NULL) = x``, unlike standard
    SQL), so the obvious overlap expression written over a LEFT JOIN silently
    returns ``bar_ms`` for every bar that has no matching hole — i.e. it reports
    a fully-empty day. That bug was hit during prototyping; this implementation
    cannot express it, and ``tests/test_orderflow.py`` pins the DuckDB semantics
    so the reason stays on the record.
    """
    out = np.zeros(grid.size, dtype="float64")
    if grid.size == 0:
        return out
    edges_lo = grid.astype("float64")
    edges_hi = edges_lo + bar_ms
    t0 = int(grid[0])
    for h0, h1 in holes:
        # Only the bars the hole can actually touch (a day can hold hundreds of
        # holes and a month of 1min bars is tens of thousands of rows).
        i0 = max(0, (int(h0) - t0) // bar_ms)
        i1 = min(grid.size, (int(h1) - t0) // bar_ms + 1)
        if i1 <= i0:
            continue
        lo = np.maximum(edges_lo[i0:i1], float(h0))
        hi = np.minimum(edges_hi[i0:i1], float(h1))
        out[i0:i1] += np.clip(hi - lo, 0.0, None)
    return np.minimum(out, float(bar_ms))


def _segment_ids(coverage: np.ndarray) -> np.ndarray:
    """Segment id per bar: maximal runs of **fully covered** bars.

    Any bar with ``gap_ms > 0`` terminates the current run and takes an id of its
    own, so the next fully-covered bar starts a fresh one. The earlier rule
    (``coverage > 0``) only broke on a *completely* empty bar, which let
    ``cvd_*`` accumulate straight through any hole shorter than one bar — up to
    59 minutes of unobserved flow on a 1h grid — while its own FeatureNote
    promised a reset. Unobserved flow is unobserved flow whatever fraction of the
    bar it occupies.
    """
    full = coverage >= 1.0
    start = np.zeros(full.size, dtype=bool)
    if full.size:
        start[0] = True
        start[1:] = ~full[1:] | ~full[:-1]
    return np.cumsum(start) - 1


def _witness_ts(con: Any, venue: str, streams: Sequence[str]) -> np.ndarray:
    """Sorted unique witness timestamps for one venue leg.

    **The stream list is load-bearing and is chosen per family, never pooled by
    default.** A leg is witnessed by itself wherever it is dense enough to do so:
    the trade family by ``trades`` alone and the book family by
    ``depth_snapshots`` alone. Only the *liquidation* family is witnessed by
    ``trades UNION depth_snapshots``, and only because a sparse stream cannot
    witness its own liveness — bybit prints 3-2000 liquidations a *day*, so its
    normal inter-arrival time already exceeds ``GAP_MS``.

    Using that union for the trade family too was a real defect: a venue whose
    trade leg died while its book leg kept printing scored as fully covered, and
    the missing rows then became 0.0 instead of NaN. Reproduced on the archive
    (2026-07-25: ``binancef`` has 0 trades and 74,575 depth snapshots), so the
    witness is now picked per family at every call site.
    """
    parts = [f"SELECT ts_ms FROM {_REL[s]} WHERE exchange = ?" for s in streams]
    sql = " UNION ALL ".join(parts)
    rows = con.execute(
        f"SELECT DISTINCT ts_ms FROM ({sql}) ORDER BY ts_ms", [venue] * len(parts)
    ).fetchnumpy()
    ts = rows["ts_ms"]
    if isinstance(ts, np.ma.MaskedArray):  # nullable column -> drop the NULLs
        ts = ts.compressed()
    return np.sort(np.asarray(ts, dtype="int64"))


# --------------------------------------------------------------------------- #
# Feature queries                                                               #
# --------------------------------------------------------------------------- #
def _bucket_predicates() -> list[tuple[str, str]]:
    """``(suffix, SQL predicate)`` for the size buckets, from SIZE_BUCKETS_USD."""
    out: list[tuple[str, str]] = []
    lo = None
    names = ["le10k", "le100k", "le1m"]
    for i, hi in enumerate(SIZE_BUCKETS_USD):
        pred = f"price*qty <= {hi!r}" if lo is None else f"price*qty > {lo!r} AND price*qty <= {hi!r}"
        out.append((names[i] if i < len(names) else f"le{i}", pred))
        lo = hi
    out.append(("whale", f"price*qty > {SIZE_BUCKETS_USD[-1]!r}"))
    return out


def _trade_bars(con: Any, venue: str, t0: int, bar_ms: int) -> pd.DataFrame:
    """Per-bar OHLCV + signed flow for one trade venue, aggregated inside DuckDB."""
    signed = "CASE WHEN aggressor_buy THEN price*qty WHEN NOT aggressor_buy THEN -price*qty END"
    bucket_cols = ", ".join(
        f"coalesce(sum({signed}) FILTER (WHERE {pred}), 0.0) AS delta_usd_{sfx}"
        for sfx, pred in _bucket_predicates()
    )
    sql = f"""
        SELECT
          CAST((ts_ms - {t0}) // {bar_ms} AS BIGINT)               AS b,
          first(price ORDER BY ts_ms, trade_id)                     AS open,
          max(price)                                                AS high,
          min(price)                                                AS low,
          last(price ORDER BY ts_ms, trade_id)                      AS close,
          sum(qty)                                                  AS volume,
          count(*)                                                  AS trade_count,
          sum(price*qty)                                            AS dollar_volume,
          coalesce(sum(qty) FILTER (WHERE aggressor_buy), 0.0)      AS buy_volume,
          coalesce(sum(qty) FILTER (WHERE NOT aggressor_buy), 0.0)  AS sell_volume,
          coalesce(sum({signed}), 0.0)                              AS delta_usd,
          {bucket_cols}
        FROM {_REL['trades']}
        WHERE exchange = ?
        GROUP BY 1 ORDER BY 1
    """
    return con.execute(sql, [venue]).df()


def _book_snapshot_cte(venue_param: str, depth_levels: int, rel: str) -> str:
    """SQL CTE chain turning JSON book snapshots into per-snapshot scalars.

    ``bids``/``asks`` are stored as JSON ``[[price, qty], ...]`` best-first;
    ``CAST(... AS DOUBLE[][])`` parses them inside DuckDB so the levels never
    become Python objects (the archive holds ~1.5 M snapshots).
    """
    k = int(depth_levels)
    return f"""
        WITH raw AS (
          SELECT ts_ms,
                 CAST(bids AS DOUBLE[][]) AS bl,
                 CAST(asks AS DOUBLE[][]) AS al
          FROM {rel}
          WHERE exchange = {venue_param}
        ),
        m AS (
          SELECT ts_ms, bl, al,
                 bl[1][1] AS bp, bl[1][2] AS bq,
                 al[1][1] AS ap, al[1][2] AS aq,
                 len(bl) AS nb, len(al) AS na,
                 (bl[1][1] + al[1][1]) / 2.0 AS mid
          FROM raw
          WHERE len(bl) >= 1 AND len(al) >= 1
          -- Deterministic de-dup: the archive has zero duplicate (exchange, ts_ms)
          -- rows today (measured); if one ever appears it must not silently create
          -- a spurious OFI pair, and the survivor must not depend on scan order.
          QUALIFY row_number() OVER (
            PARTITION BY ts_ms ORDER BY bl[1][1], bl[1][2], al[1][1], al[1][2]) = 1
        ),
        l AS (
          SELECT *,
            CASE WHEN nb >= {k} THEN list_transform(list_slice(bl, 1, {k}), x -> abs(x[1]-mid)/mid*1e4) END AS bx,
            CASE WHEN nb >= {k} THEN list_transform(list_slice(bl, 1, {k}), x -> x[2]) END AS bqs,
            CASE WHEN na >= {k} THEN list_transform(list_slice(al, 1, {k}), x -> abs(x[1]-mid)/mid*1e4) END AS ax,
            CASE WHEN na >= {k} THEN list_transform(list_slice(al, 1, {k}), x -> x[2]) END AS aqs
          FROM m
        ),
        c AS (
          SELECT *,
            list_transform(range(1, {k}+1), j -> list_sum(list_slice(bqs, 1, j))) AS bcum,
            list_transform(range(1, {k}+1), j -> list_sum(list_slice(aqs, 1, j))) AS acum
          FROM l
        ),
        snap AS (
          SELECT ts_ms, bp, bq, ap, aq, nb, na, mid,
                 ap - bp                                   AS spread,
                 (ap - bp) / mid * 1e4                      AS spread_bps,
                 (bp*aq + ap*bq) / nullif(bq + aq, 0)       AS microprice,
                 bq / nullif(bq + aq, 0)                    AS book_imbalance,
                 list_sum(bqs)                              AS depth_bid,
                 list_sum(aqs)                              AS depth_ask,
                 list_sum(list_transform(range(1, {k}+1), j -> bx[j]*bcum[j]))
                   / nullif(list_sum(list_transform(bx, x -> x*x)), 0)  AS slope_bid,
                 list_sum(list_transform(range(1, {k}+1), j -> ax[j]*acum[j]))
                   / nullif(list_sum(list_transform(ax, x -> x*x)), 0)  AS slope_ask,
                 least(nb, na)                              AS depth_levels
          FROM c
        )
    """


def _book_bars(con: Any, venue: str, t0: int, t1: int, bar_ms: int, depth_levels: int,
               rel: str) -> pd.DataFrame:
    """Per-bar book state (last snapshot) + summed OFI for one book venue.

    ``rel`` spans the whole UTC days the request touches, while the aggregation
    is restricted to ``[t0, t1)``. That asymmetry is the point: the snapshot pair
    straddling the window start would otherwise be dropped (``lag`` over a
    window-filtered relation gives the first row a NULL predecessor), so bar 0 of
    a mid-day request carried one contribution fewer than the same bar inside a
    wider request — measured on the real binancef leg as 175.039 (n=58) vs
    191.017 (n=57) for 01:00Z on 2026-07-25. Anchoring the pairing to the UTC day
    makes the value window-independent; the pair straddling midnight is never
    formed, which is a rule, not an accident.

    The explicit ``ts_ms >= t0`` filter is load-bearing for a second reason:
    DuckDB integer division truncates toward zero, so a snapshot one second
    before ``t0`` would otherwise compute ``b = 0`` and land inside bar 0.
    """
    cte = _book_snapshot_cte("?", depth_levels, rel)
    sql = f"""
        {cte},
        pairs AS (
          SELECT *,
                 lag(ts_ms) OVER w AS pts,
                 lag(bp)    OVER w AS pbp, lag(bq) OVER w AS pbq,
                 lag(ap)    OVER w AS pap, lag(aq) OVER w AS paq
          FROM snap WINDOW w AS (ORDER BY ts_ms)
        ),
        e AS (
          SELECT *,
            CASE WHEN pts IS NULL THEN NULL ELSE ts_ms - pts END AS dt,
            CASE WHEN pts IS NULL THEN NULL ELSE
              (CASE WHEN bp >= pbp THEN bq  ELSE 0.0 END)
            - (CASE WHEN bp <= pbp THEN pbq ELSE 0.0 END)
            - (CASE WHEN ap <= pap THEN aq  ELSE 0.0 END)
            + (CASE WHEN ap >= pap THEN paq ELSE 0.0 END)
            END AS ev
          FROM pairs
        )
        SELECT
          CAST((ts_ms - {t0}) // {bar_ms} AS BIGINT)                        AS b,
          count(*)                                                          AS book_snapshots,
          coalesce(sum(ev) FILTER (WHERE dt IS NOT NULL AND dt <= {GAP_MS}), 0.0) AS ofi,
          count(*) FILTER (WHERE dt IS NOT NULL AND dt <= {GAP_MS})         AS ofi_n,
          count(*) FILTER (WHERE dt IS NOT NULL AND dt >  {GAP_MS})         AS ofi_gap_pairs,
          last(mid            ORDER BY ts_ms)                               AS mid,
          last(spread         ORDER BY ts_ms)                               AS spread,
          last(spread_bps     ORDER BY ts_ms)                               AS spread_bps,
          last(microprice     ORDER BY ts_ms)                               AS microprice,
          last(book_imbalance ORDER BY ts_ms)                               AS book_imbalance,
          last(depth_bid      ORDER BY ts_ms)                               AS depth_bid,
          last(depth_ask      ORDER BY ts_ms)                               AS depth_ask,
          last(slope_bid      ORDER BY ts_ms)                               AS depth_slope_bid,
          last(slope_ask      ORDER BY ts_ms)                               AS depth_slope_ask,
          last(depth_levels   ORDER BY ts_ms)                               AS depth_levels
        FROM e
        WHERE ts_ms >= {int(t0)} AND ts_ms < {int(t1)}
        GROUP BY 1 ORDER BY 1
    """
    return con.execute(sql, [venue]).df()


def _liq_bars(con: Any, venue: str, t0: int, bar_ms: int) -> pd.DataFrame:
    """Per-bar liquidation counting for one venue. Descriptive only, no model."""
    sql = f"""
        SELECT
          CAST((ts_ms - {t0}) // {bar_ms} AS BIGINT)                              AS b,
          count(*)                                                                AS liq_count,
          coalesce(sum(notional_usd), 0.0)                                        AS liq_notional_usd,
          coalesce(sum(notional_usd) FILTER (WHERE lower(side) = 'long'), 0.0)    AS liq_long_notional,
          coalesce(sum(notional_usd) FILTER (WHERE lower(side) = 'short'), 0.0)   AS liq_short_notional
        FROM {_REL['liquidations']}
        WHERE exchange = ?
        GROUP BY 1 ORDER BY 1
    """
    return con.execute(sql, [venue]).df()


# --------------------------------------------------------------------------- #
# Volume clock / VPIN                                                           #
# --------------------------------------------------------------------------- #
def _daily_volume(con: Any, venue: str, rel: str) -> pd.Series:
    """Traded volume per UTC epoch-day for one venue (index = epoch day number).

    ``rel`` is the **day-anchored** relation, so a request starting mid-day still
    measures whole days. Reading the window-filtered relation here fed a partial
    first day into the causal median that sets ``V`` for the next day.
    """
    df = con.execute(
        f"SELECT ts_ms // {MS_PER_DAY} AS d, sum(qty) AS v FROM {rel} "
        "WHERE exchange = ? GROUP BY 1 ORDER BY 1", [venue]
    ).df()
    if df.empty:
        return pd.Series(dtype="float64")
    return pd.Series(df["v"].to_numpy(dtype="float64"), index=df["d"].to_numpy(dtype="int64"))


def _causal_bucket_volumes(daily: pd.Series, buckets_per_day: int) -> dict[int, float]:
    """``{epoch_day: V}`` from the median volume of **strictly prior** days.

    The paper's convention (average daily volume / 50) peeks at the very day it
    measures. The causal restatement uses only days that already closed, so the
    first day of any range has no ``V`` and its VPIN is warm-up NaN rather than a
    guess. The median (not the mean) keeps a half-recorded outage day from
    dragging ``V`` down.

    "Prior days" means prior **whole UTC days inside the requested range** — the
    module never silently widens the range to warm ``V`` up, but it does read
    each day it touches in full, so a request starting mid-day no longer feeds a
    half day's volume into the next day's ``V``. Ask for a longer range if you
    want a longer warm-up; the parameter is visible either way.
    """
    out: dict[int, float] = {}
    seen: list[float] = []
    for day in sorted(daily.index):
        if seen:
            v = float(np.median(seen)) / float(buckets_per_day)
            if np.isfinite(v) and v > 0:
                out[int(day)] = v
        vol = float(daily.loc[day])
        if np.isfinite(vol) and vol > 0:
            seen.append(vol)
    return out


_BUCKET_COLUMNS = [
    "venue", "epoch_day", "bucket_index", "bucket_volume", "buy_volume",
    "sell_volume", "delta", "imbalance", "vpin", "trade_count",
    "open_ts_ms", "close_ts_ms", "duration_s", "window_span_s", "window_gap_s",
]


def _volume_buckets(
    con: Any,
    venue: str,
    *,
    rel: str,
    bucket_volume: Optional[float],
    buckets_per_day: int,
    window_buckets: int,
) -> pd.DataFrame:
    """Exact-split volume-clock buckets for one venue, with rolling VPIN.

    A print that straddles a bucket boundary is **split exactly** across the
    buckets it spans (the ``VpinStore`` convention, and the reason a naive
    ``floor(cumvol / V)`` assignment is wrong: it leaves buckets whose volume is
    only approximately ``V``). The clock is re-armed at UTC midnight, so buckets
    never straddle a day boundary and each day's trailing incomplete bucket is
    dropped rather than published half-formed.

    ``rel`` is the **day-anchored** relation. Reading the window-filtered one
    armed the clock at the first print inside the request instead of at midnight,
    which made the same bar's VPIN depend on where the caller started: measured
    on the real coinbase leg, 180 of 180 shared 1-min bars differed (max 8.7e-02)
    between ``[00:00, 06:00)`` and ``[03:00, 06:00)``.

    Two diagnostics ride along with each bucket because the statistic itself
    carries no wall-clock information: ``window_span_s`` (how long the rolling
    window took to fill) and ``window_gap_s`` (how much of that span was detected
    feed silence). A window whose 50 buckets straddle a dead day is not a fresh
    reading, and ``vpin_age_s`` — the age of the newest bucket alone — is
    smallest exactly when the contamination is largest.
    """
    daily = _daily_volume(con, venue, rel)
    empty = pd.DataFrame(columns=_BUCKET_COLUMNS)
    if daily.empty:
        return empty
    if bucket_volume is not None:
        if not (np.isfinite(bucket_volume) and bucket_volume > 0):
            raise OrderFlowError("vpin_bucket_volume must be a positive finite number")
        vmap = {int(d): float(bucket_volume) for d in daily.index}
    else:
        vmap = _causal_bucket_volumes(daily, buckets_per_day)
    if not vmap:
        return empty

    vvals = ", ".join(f"({d}, {v!r})" for d, v in sorted(vmap.items()))
    sql = f"""
        WITH vmap(d, V) AS (VALUES {vvals}),
        t AS (
          SELECT ts_ms, qty, aggressor_buy, ts_ms // {MS_PER_DAY} AS d,
                 sum(qty) OVER (PARTITION BY ts_ms // {MS_PER_DAY}
                                ORDER BY ts_ms, trade_id
                                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS c1
          FROM {rel}
          WHERE exchange = ? AND qty > 0
        ),
        u AS (SELECT t.*, t.c1 - t.qty AS c0, vmap.V FROM t JOIN vmap USING (d)),
        x AS (
          SELECT u.d, u.ts_ms, u.aggressor_buy, u.V, k.k,
                 least(u.c1, (k.k + 1) * u.V) - greatest(u.c0, k.k * u.V) AS w
          FROM u, unnest(range(CAST(floor(u.c0 / u.V) AS BIGINT),
                               CAST(ceil(u.c1 / u.V) AS BIGINT))) AS k(k)
        ),
        y AS (
          SELECT *, ts_ms - lag(ts_ms) OVER (PARTITION BY d, k ORDER BY ts_ms) AS dt
          FROM x WHERE w > 0
        )
        SELECT d AS epoch_day, k AS bucket_index, max(V) AS v_target,
               sum(w) AS bucket_volume,
               coalesce(sum(w) FILTER (WHERE aggressor_buy), 0.0) AS buy_volume,
               coalesce(sum(w) FILTER (WHERE NOT aggressor_buy), 0.0) AS sell_volume,
               count(*) AS trade_count,
               min(ts_ms) AS open_ts_ms, max(ts_ms) AS close_ts_ms,
               coalesce(sum(dt) FILTER (WHERE dt > {GAP_MS}), 0) AS inner_gap_ms
        FROM y
        GROUP BY 1, 2 ORDER BY 1, 2
    """
    df = con.execute(sql, [venue]).df()
    if df.empty:
        return empty

    v_target = df["v_target"].to_numpy(dtype="float64")
    vol = df["bucket_volume"].to_numpy(dtype="float64")
    complete = np.isclose(vol, v_target, rtol=1e-9, atol=1e-9)
    df = df.loc[complete].reset_index(drop=True)
    if df.empty:
        return empty

    v_target = df["v_target"].to_numpy(dtype="float64")
    buy = df["buy_volume"].to_numpy(dtype="float64")
    sell = df["sell_volume"].to_numpy(dtype="float64")
    imb = np.abs(buy - sell) / v_target
    open_ms = df["open_ts_ms"].to_numpy(dtype="int64")
    close_ms = df["close_ts_ms"].to_numpy(dtype="int64")
    inner_gap = df["inner_gap_ms"].to_numpy(dtype="float64")

    # Wall-clock span of the rolling window, and the detected silence inside it.
    # Silence is counted in FULL (the whole inter-arrival, not the excess over
    # GAP_MS) so the number means the same thing as `gap_ms` and as the hole
    # census in scripts/check_ticks.py.
    w = max(int(window_buckets), 1)
    between = np.zeros(open_ms.size, dtype="float64")
    if open_ms.size > 1:
        silence = (open_ms[1:] - close_ms[:-1]).astype("float64")
        between[1:] = np.where(silence > GAP_MS, silence, 0.0)
    # The window for bucket i spans buckets [i-w+1, i]: every bucket's own
    # internal silence, plus the w-1 inter-bucket silences strictly inside the
    # span (the one preceding the oldest bucket is outside it, hence subtracted).
    oldest_open = pd.Series(open_ms.astype("float64")).shift(w - 1).to_numpy()
    span = (close_ms.astype("float64") - oldest_open) / 1000.0
    gap_ms_window = (pd.Series(inner_gap).rolling(w).sum().to_numpy()
                     + pd.Series(between).rolling(w).sum().to_numpy()
                     - pd.Series(between).shift(w - 1).to_numpy())

    out = pd.DataFrame({
        "venue": venue,
        "epoch_day": df["epoch_day"].to_numpy(dtype="int64"),
        "bucket_index": df["bucket_index"].to_numpy(dtype="int64"),
        "bucket_volume": df["bucket_volume"].to_numpy(dtype="float64"),
        "buy_volume": buy,
        "sell_volume": sell,
        "delta": buy - sell,
        "imbalance": imb,
        "vpin": pd.Series(imb).rolling(w).mean().to_numpy(),
        "trade_count": df["trade_count"].to_numpy(dtype="float64"),
        "open_ts_ms": open_ms,
        "close_ts_ms": close_ms,
    })
    out["duration_s"] = (out["close_ts_ms"] - out["open_ts_ms"]) / 1000.0
    out["window_span_s"] = span
    out["window_gap_s"] = gap_ms_window / 1000.0
    return out


def _vpin_on_grid(buckets: pd.DataFrame, grid: np.ndarray, bar_ms: int) -> pd.DataFrame:
    """As-of (backward) join of the bucket VPIN series onto the time grid.

    A bar may only see buckets that **closed strictly before its own end**, so a
    bucket closing 1 ms after the bar end is invisible. Nothing is interpolated:
    the staleness of the value that *is* visible is reported in ``vpin_age_s``.
    """
    n = grid.size
    empty = pd.DataFrame({"vpin": np.full(n, np.nan),
                          "vpin_age_s": np.full(n, np.nan),
                          "vpin_buckets": np.zeros(n),
                          "vpin_window_span_s": np.full(n, np.nan),
                          "vpin_window_gap_s": np.full(n, np.nan)})
    if buckets is None or buckets.empty:
        return empty
    close = buckets["close_ts_ms"].to_numpy(dtype="int64")
    order = np.argsort(close, kind="stable")
    close = close[order]
    vpin = buckets["vpin"].to_numpy(dtype="float64")[order]
    wspan = buckets["window_span_s"].to_numpy(dtype="float64")[order]
    wgap = buckets["window_gap_s"].to_numpy(dtype="float64")[order]

    bar_end = grid + bar_ms
    idx = np.searchsorted(close, bar_end, side="left") - 1
    ok = idx >= 0
    v = np.full(n, np.nan)
    age = np.full(n, np.nan)
    span = np.full(n, np.nan)
    gap = np.full(n, np.nan)
    v[ok] = vpin[idx[ok]]
    age[ok] = (bar_end[ok] - close[idx[ok]]) / 1000.0
    span[ok] = wspan[idx[ok]]
    gap[ok] = wgap[idx[ok]]
    bad = ~np.isfinite(v)
    age[bad] = np.nan
    span[bad] = np.nan
    gap[bad] = np.nan
    lo = np.searchsorted(close, grid, side="left")
    hi = np.searchsorted(close, bar_end, side="left")
    return pd.DataFrame({"vpin": v, "vpin_age_s": age,
                         "vpin_buckets": (hi - lo).astype("float64"),
                         "vpin_window_span_s": span, "vpin_window_gap_s": gap})


# --------------------------------------------------------------------------- #
# Cache (mirrors data.py's cache contract)                                      #
# --------------------------------------------------------------------------- #
_ORDERFLOW_CACHE = DATA_DIR / "orderflow"


def _spec_hash(spec: dict[str, Any]) -> str:
    payload = json.dumps({**spec, "schema_version": SCHEMA_VERSION},
                         sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cache_paths(spec: dict[str, Any]) -> tuple[Path, Path]:
    h = _spec_hash(spec)
    stem = f"{spec['start']}_{spec['end']}".replace(":", "").replace(" ", "T")
    d = _ORDERFLOW_CACHE / h
    return d / f"{stem}.parquet", d / f"{stem}.json"


def _write_bars_cache(bars: pd.DataFrame, spec: dict[str, Any]) -> None:
    pq, js = _cache_paths(spec)
    try:
        pq.parent.mkdir(parents=True, exist_ok=True)
        bars.to_parquet(pq)
        js.write_text(json.dumps(bars.attrs.get("orderflow", {}), indent=2, default=str),
                      encoding="utf-8")
    except Exception as exc:  # pragma: no cover - disk/pyarrow issues are environmental
        warnings.warn(f"could not write order-flow cache {pq}: {exc}", stacklevel=2)


def _read_bars_cache(spec: dict[str, Any]) -> Optional[pd.DataFrame]:
    pq, js = _cache_paths(spec)
    if not pq.exists():
        return None
    try:
        df = pd.read_parquet(pq)
    except Exception as exc:  # pragma: no cover
        warnings.warn(f"could not read order-flow cache {pq}: {exc}", stacklevel=2)
        return None
    if js.exists():
        try:
            df.attrs["orderflow"] = json.loads(js.read_text(encoding="utf-8"))
        except Exception:  # pragma: no cover
            pass
    return df


# --------------------------------------------------------------------------- #
# Public API                                                                    #
# --------------------------------------------------------------------------- #
def order_flow_bars(
    start: Union[str, pd.Timestamp],
    end: Union[str, pd.Timestamp],
    *,
    price_venue: str,
    bar: str = "1h",
    trade_venues: Optional[Sequence[str]] = None,
    book_venue: Optional[str] = None,
    symbol: Optional[Union[str, dict]] = None,
    depth_levels: int = 10,
    vpin: bool = True,
    vpin_buckets_per_day: int = 50,
    vpin_window_buckets: int = 50,
    vpin_bucket_volume: Optional[float] = None,
    liquidations: bool = True,
    source: str = "auto",
    store_dir: Union[str, Path] = STORE_DIR,
    hf_repo: str = HF_REPO,
    cache: bool = True,
    materialize: Optional[bool] = None,
    now_ms: Optional[int] = None,
) -> pd.DataFrame:
    """Event-time order-flow bars over ``[start, end)``. **Features only.**

    The returned frame is a drop-in for every existing consumer: a UTC
    ``DatetimeIndex`` with ``open/high/low/close/volume`` in the canonical order,
    plus the order-flow and quality columns. ``bars["close"]`` is exactly what
    ``backtest.walk_forward`` wants; ``bars`` itself is exactly what
    ``features.atr`` wants.

    Parameters
    ----------
    start, end
        Half-open UTC window ``[start, end)``. Parsed with ``pd.Timestamp``;
        naive inputs are read as UTC.
    price_venue
        **Required, no default.** OHLCV comes from this venue alone. There is no
        honest default: the archive holds ``coinbase BTC-USD`` (spot),
        ``bybit BTCUSDT`` / ``binancef BTCUSDT`` / ``okx BTC-USDT-SWAP`` (perps),
        and no single venue covers every recorded day.
    bar
        One of :data:`BAR_FREQS`.
    trade_venues
        Trade legs to emit flow columns for; defaults to ``(price_venue,)``.
        Cross-venue *trade* features are legitimate and always venue-suffixed.
    book_venue
        Exactly one venue (rail 5) or ``None`` for no book columns. Books from
        different venues are never merged into one synthetic book. When it names
        a different instrument *class* than ``price_venue`` (spot book vs perp
        tape or the reverse) the run warns and records
        ``attrs["orderflow"]["cross_instrument"]``: ``mid_{b} - close`` is then a
        funding basis, not book pressure.
    symbol
        Instrument id — a string, or ``{venue: id}`` when the venues use
        different native ids. Optional only because the archive currently holds
        one instrument per venue: a venue carrying two symbols in the window
        raises rather than pooling them (rail 5 applies to symbols, not just to
        venues).
    depth_levels
        Level count for depth sums and the depth slope. A snapshot with fewer
        stored levels yields NaN, never a zero-padded book.
    vpin, vpin_buckets_per_day, vpin_window_buckets, vpin_bucket_volume
        Volume-clock settings. Defaults are the paper's (50/50). Passing
        ``vpin_bucket_volume`` pins ``V`` explicitly and records it in the
        manifest; leaving it ``None`` uses the causal prior-days rule.
    liquidations
        Emit liquidation columns for the requested venues that actually have a
        liquidation leg (:data:`LIQUIDATION_VENUES`).
    source
        ``"auto"`` (closed local day file when present, else ``hf://``),
        ``"local"``, or ``"hf"``.
    cache
        Read/write ``data/orderflow/<spec_hash>/<range>.parquet`` plus a JSON
        manifest sidecar. The hash covers every parameter **and**
        :data:`SCHEMA_VERSION`, so a formula change can never read a stale bar.
        Staleness in the other direction is prevented by a **finality check**,
        not by an argument: a range is written to the cache only when *every* UTC
        day in it actually resolved to a readable source. A day that is still
        open, whose file the live writer holds, or that is not on the Hub yet is
        reported as a gap, warned about, and **not cached** — because the spec
        hash cannot see which days resolved, so an all-gap frame would otherwise
        be served forever once that day closed. ``[SUPERSEDED]`` an earlier
        version of this docstring argued the opposite from "only closed days are
        ever read"; the premise is true and the conclusion did not follow, since
        a *range* can contain a day that was skipped for being open — and
        ``order_flow_bars(start, <now>)`` is the natural call that does it.
    materialize
        Copy the filtered source rows into scratch tables before running the
        feature queries. Defaults to on whenever any day comes from ``hf://``
        (one network pass instead of one per query).

    Returns
    -------
    pd.DataFrame
        Bars. ``bars.attrs["orderflow"]`` carries the run manifest: per-day
        source, table presence, row counts, the resolved parameters, a coverage
        summary, and the schema version. ``pandas`` does not always propagate
        ``attrs`` through operations, so the JSON sidecar next to the cached
        parquet is the durable copy and :func:`provenance_table` can always be
        called again.

    Notes
    -----
    No column here is a signal. Coverage-flagged bars are *reported*, not
    dropped: :func:`drop_gap_bars` and :func:`gap_flat_positions` make that an
    explicit, labelled downstream choice.
    """
    _require_deps()
    if not price_venue or not _VENUE_RE.match(price_venue):
        raise OrderFlowError("price_venue is required and must be a collector venue code "
                             "(no honest default exists across this archive)")
    bar_ms = _bar_ms(bar)
    t_start, t_end = _to_utc(start), _to_utc(end)
    t0 = int(t_start.value // 1_000_000)
    t1 = int(t_end.value // 1_000_000)
    if t1 <= t0:
        raise OrderFlowError("end must be strictly after start")
    if t0 % bar_ms or t1 % bar_ms:
        raise OrderFlowError(
            f"start/end must align to the {bar} grid so bars are never partial; "
            f"got start={t_start} end={t_end}")

    tvs: tuple[str, ...] = tuple(dict.fromkeys(trade_venues or (price_venue,)))
    if price_venue not in tvs:
        tvs = (price_venue,) + tvs
    for v in tvs:
        if not _VENUE_RE.match(v):
            raise OrderFlowError(f"bad venue code {v!r}")
    if book_venue is not None and not _VENUE_RE.match(book_venue):
        raise OrderFlowError(f"bad venue code {book_venue!r}")
    if int(depth_levels) < 1:
        raise OrderFlowError("depth_levels must be >= 1")

    liq_venues: tuple[str, ...] = tuple(v for v in tvs if v in LIQUIDATION_VENUES) if liquidations else ()

    spec = {
        "start": t_start.strftime("%Y-%m-%dT%H%M%SZ"),
        "end": t_end.strftime("%Y-%m-%dT%H%M%SZ"),
        "price_venue": price_venue, "bar": bar, "trade_venues": list(tvs),
        "book_venue": book_venue, "symbol": symbol, "depth_levels": int(depth_levels),
        "vpin": bool(vpin), "vpin_buckets_per_day": int(vpin_buckets_per_day),
        "vpin_window_buckets": int(vpin_window_buckets),
        "vpin_bucket_volume": vpin_bucket_volume,
        "liquidations": bool(liquidations), "source": source,
        "hf_repo": hf_repo, "store_dir": str(store_dir),
    }
    if cache:
        cached = _read_bars_cache(spec)
        if cached is not None:
            return cached

    grid = _grid(t0, t1, bar_ms)
    idx = pd.to_datetime(grid, unit="ms", utc=True)
    idx.name = "timestamp"

    exchanges = tuple(dict.fromkeys(tvs + ((book_venue,) if book_venue else ()) + liq_venues))
    tables = ["trades", "depth_snapshots"] + (["liquidations"] if liq_venues else [])
    dates = _dates_between(t_start, t_end)

    try:
        src = _open_source(
            dates, source=source, store_dir=Path(store_dir), hf_repo=hf_repo,
            exchanges=exchanges, t0_ms=t0, t1_ms=t1, tables=tables,
            symbol=symbol, day_tables=("trades", "depth_snapshots"),
            materialize=materialize, now_ms=now_ms,
        )
    except OrderFlowError:
        raise
    except Exception as exc:
        cached = _read_bars_cache(spec) if cache else None
        if cached is not None:
            warnings.warn(f"order-flow source unavailable ({exc}); serving the cached bars "
                          "for this exact spec", stacklevel=2)
            return cached
        raise OrderFlowError(f"cannot open the tick store: {exc}") from exc

    try:
        bars = _assemble(
            src, grid=grid, idx=idx, t0=t0, t1=t1, bar_ms=bar_ms,
            price_venue=price_venue, trade_venues=tvs, book_venue=book_venue,
            depth_levels=int(depth_levels), liq_venues=liq_venues, vpin=vpin,
            vpin_buckets_per_day=int(vpin_buckets_per_day),
            vpin_window_buckets=int(vpin_window_buckets),
            vpin_bucket_volume=vpin_bucket_volume,
        )
    finally:
        src.close()

    cross = _cross_instrument_note(price_venue, book_venue)
    bars.attrs["orderflow"] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": spec,
        "trade_venues": list(tvs),
        "book_venue": book_venue,
        "liq_venues": list(liq_venues),
        "bar_ms": bar_ms,
        "periods_per_year": periods_per_year(bar),
        "manifest": src.manifest,
        "coverage_summary": _coverage_summary(bars),
        "history": _history_summary(src.manifest, t0, t1),
        "cross_instrument": cross,
        "honesty": list(HONESTY_SENTENCES),
    }
    if cross:
        warnings.warn(cross["note"], stacklevel=2)
    if not src.manifest.get("final", True):
        warnings.warn(
            "order-flow range is NOT final: "
            f"{src.manifest['unresolved_days']} did not resolve to a readable source "
            "(day still open, file held by the live writer, or not on the Hub yet). "
            "Those bars are reported as gaps and the result is NOT cached — a spec "
            "hash cannot see which days resolved, so caching it would serve an "
            "all-gap frame forever.", stacklevel=2)
    elif cache:
        _write_bars_cache(bars, spec)
    return bars


def _cross_instrument_note(price_venue: str, book_venue: Optional[str]) -> Optional[dict]:
    """State it when the tape and the book are different instrument classes.

    ``open/high/low/close/volume`` cannot be venue-suffixed — that is the harness
    contract — so nothing in the frame's own column names says that ``close`` is
    spot while ``mid_{b}`` is a perp. On the current archive that pairing is not
    even avoidable (2026-07-25 holds coinbase trades and binancef depth and
    nothing else), and the two run ~40 USD apart on pure basis.
    """
    if not book_venue or book_venue == price_venue:
        return None
    a = VENUE_INSTRUMENT.get(price_venue, "unknown")
    b = VENUE_INSTRUMENT.get(book_venue, "unknown")
    if a == b and a != "unknown":
        return None
    return {
        "price_venue": price_venue, "price_instrument": a,
        "book_venue": book_venue, "book_instrument": b,
        "note": (f"cross-instrument request: price_venue={price_venue} ({a}) vs "
                 f"book_venue={book_venue} ({b}). `close` and `mid_{book_venue}` are "
                 "different instruments, so their difference is a funding BASIS, not "
                 f"book pressure. `micro_minus_mid_{book_venue}` is within-venue and is "
                 "the safe book-pressure read."),
    }


def _history_summary(manifest: dict[str, Any], t0: int, t1: int) -> dict[str, Any]:
    """MinBTL arithmetic **measured**, never remembered (rail 4).

    A hard-coded day count in a docstring is a claim a test can only check for
    presence, not for truth, and the archive grows one day per day. So the span
    actually resolved, the ``risk.min_backtest_length`` requirement and the
    fraction met are computed per call and ride along in ``attrs``.
    """
    from .risk import min_backtest_length  # local import: keeps `import btcquant` cheap

    days = [d for d in manifest.get("days", []) if d.get("resolved")]
    span_years = (t1 - t0) / float(_MS_PER_YEAR)
    out: dict[str, Any] = {
        "days_requested": len(manifest.get("days", [])),
        "days_resolved": len(days),
        "days_unresolved": list(manifest.get("unresolved_days", [])),
        "span_days": (t1 - t0) / float(MS_PER_DAY),
        "span_years": span_years,
        "min_backtest_length_years": {},
        "fraction_of_minbtl": {},
        "statement": HONESTY_SENTENCES[4],
    }
    for n in (5, 20, 100):
        need = float(min_backtest_length(n))
        out["min_backtest_length_years"][str(n)] = need
        out["fraction_of_minbtl"][str(n)] = span_years / need if need > 0 else float("nan")
    return out


def _coverage_summary(bars: pd.DataFrame) -> dict[str, Any]:
    cov = bars["coverage"].to_numpy(dtype="float64")
    seg = bars["segment"].to_numpy(dtype="float64")
    full = cov >= 1.0
    return {
        "bars": int(cov.size),
        "full_coverage": int(np.sum(full)),
        "partial_coverage": int(np.sum((cov > 0.0) & (cov < 1.0))),
        "empty": int(np.sum(cov <= 0.0)),
        "gap_seconds_total": float(np.nansum(bars["gap_ms"].to_numpy(dtype="float64")) / 1000.0),
        "returns_spanning_a_gap": int(bars["ret_spans_gap"].sum()),
        # "segments" is the number of maximal CLEAN runs — the stretches a
        # cumulative level is comparable within. Every non-fully-covered bar also
        # takes a segment id of its own (so cvd_* cannot accumulate across it),
        # which is why the raw id count is larger and is reported separately
        # rather than passed off as the clean-run count.
        "segments": int(np.unique(seg[full]).size),
        "segment_ids_total": int(np.unique(seg).size) if len(bars) else 0,
    }


def _assemble(
    src: _Source, *, grid: np.ndarray, idx: pd.DatetimeIndex, t0: int, t1: int,
    bar_ms: int, price_venue: str, trade_venues: Sequence[str],
    book_venue: Optional[str], depth_levels: int, liq_venues: Sequence[str],
    vpin: bool, vpin_buckets_per_day: int, vpin_window_buckets: int,
    vpin_bucket_volume: Optional[float],
) -> pd.DataFrame:
    """Build the bar frame. Every masking decision lives here, in one place."""
    con = src.con
    n = grid.size
    bidx = pd.RangeIndex(n)

    def _reindex(df: pd.DataFrame) -> pd.DataFrame:
        """Bar-index -> full grid. An empty result keeps its COLUMNS (all NaN):
        the downstream masking, not a missing column, decides 0.0-vs-unknown."""
        d = df.drop(columns=["b"]).set_axis(pd.Index(df["b"].astype("int64")), axis=0)
        d = d[(d.index >= 0) & (d.index < n)]
        return d.reindex(bidx)

    def _coverage(venue: str, streams: Sequence[str]) -> np.ndarray:
        ts = _witness_ts(con, venue, streams)
        gap = _gap_ms_per_bar(_holes_from_ts(ts, t0, t1), grid, bar_ms)
        return 1.0 - gap / float(bar_ms)

    # Per-LEG liveness (rail 1). `cov` witnesses the trade leg with trades alone
    # because it is the gate that decides 0.0-vs-NaN for every trade-derived
    # column; unioning the depth leg in scored a venue with a dead trade leg and
    # a live book as fully covered and then wrote fabricated zeros into
    # delta/volume/trade_count. `cov_liq` keeps the wider witness, which is what
    # the sparse-stream argument actually justifies.
    cov: dict[str, np.ndarray] = {v: _coverage(v, ("trades",)) for v in trade_venues}
    cov_liq: dict[str, np.ndarray] = {
        v: _coverage(v, ("trades", "depth_snapshots")) for v in liq_venues}
    book_cov = None
    if book_venue:
        book_cov = _coverage(book_venue, ("depth_snapshots",))

    out = pd.DataFrame(index=idx)
    price_alive = cov[price_venue] > 0.0

    def _flow(s: pd.Series, alive: np.ndarray) -> np.ndarray:
        a = pd.to_numeric(s, errors="coerce").to_numpy(dtype="float64")
        a = np.where(np.isnan(a), 0.0, a)
        return np.where(alive, a, np.nan)

    def _state(s: pd.Series, alive: np.ndarray) -> np.ndarray:
        a = pd.to_numeric(s, errors="coerce").to_numpy(dtype="float64")
        return np.where(alive, a, np.nan)

    # ---- trade venues -------------------------------------------------------
    trade_frames: dict[str, pd.DataFrame] = {}
    for v in trade_venues:
        trade_frames[v] = _reindex(_trade_bars(con, v, t0, bar_ms))

    tp = trade_frames[price_venue]
    for col in ("open", "high", "low", "close"):
        out[col] = _state(tp[col], price_alive)
    out["volume"] = _flow(tp["volume"], price_alive)

    gap_price = (1.0 - cov[price_venue]) * bar_ms
    out["coverage"] = cov[price_venue]
    out["gap_ms"] = gap_price
    out["is_gap"] = gap_price > 0.0
    close_now = out["close"].to_numpy()
    prev_cov = np.concatenate(([np.nan], cov[price_venue][:-1]))
    prev_close = np.concatenate(([np.nan], close_now[:-1]))
    # `close[t]` is tested as well as `close[t-1]`: a bar whose own close is
    # unknown has no valid one-bar return either, and walk_forward's px.dropna()
    # would otherwise splice the whole across-the-hole move into one bar that
    # drop_gap_bars had just certified as clean.
    out["ret_spans_gap"] = (
        (cov[price_venue] < 1.0) | ~(prev_cov >= 1.0)
        | ~np.isfinite(prev_close) | ~np.isfinite(close_now)
    )
    seg = _segment_ids(cov[price_venue])
    out["segment"] = seg.astype("float64")
    out["trade_count"] = _flow(tp["trade_count"], price_alive)
    out["dollar_volume"] = _flow(tp["dollar_volume"], price_alive)
    dv = out["dollar_volume"].to_numpy()
    vol = out["volume"].to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        out["vwap"] = np.where(vol > 0, dv / vol, np.nan)

    for v in trade_venues:
        f = trade_frames[v]
        alive = cov[v] > 0.0
        buy = _flow(f["buy_volume"], alive)
        sell = _flow(f["sell_volume"], alive)
        delta = buy - sell
        out[f"delta_{v}"] = delta
        vseg = _segment_ids(cov[v])
        cvd = pd.Series(np.where(np.isnan(delta), 0.0, delta)).groupby(vseg).cumsum().to_numpy()
        out[f"cvd_{v}"] = np.where(np.isnan(delta), np.nan, cvd)
        out[f"buy_volume_{v}"] = buy
        out[f"sell_volume_{v}"] = sell
        out[f"volume_{v}"] = _flow(f["volume"], alive)
        out[f"delta_usd_{v}"] = _flow(f["delta_usd"], alive)
        for sfx, _pred in _bucket_predicates():
            out[f"delta_usd_{sfx}_{v}"] = _flow(f[f"delta_usd_{sfx}"], alive)
        out[f"trade_count_{v}"] = _flow(f["trade_count"], alive)
        out[f"coverage_{v}"] = cov[v]
        if vpin:
            buckets = _volume_buckets(
                con, v, rel=src.day_rel["trades"], bucket_volume=vpin_bucket_volume,
                buckets_per_day=vpin_buckets_per_day,
                window_buckets=vpin_window_buckets)
            vg = _vpin_on_grid(buckets, grid, bar_ms)
            out[f"vpin_{v}"] = np.where(alive, vg["vpin"].to_numpy(), np.nan)
            out[f"vpin_age_s_{v}"] = np.where(alive, vg["vpin_age_s"].to_numpy(), np.nan)
            out[f"vpin_buckets_{v}"] = np.where(alive, vg["vpin_buckets"].to_numpy(), np.nan)
            out[f"vpin_window_span_s_{v}"] = np.where(
                alive, vg["vpin_window_span_s"].to_numpy(), np.nan)
            out[f"vpin_window_gap_s_{v}"] = np.where(
                alive, vg["vpin_window_gap_s"].to_numpy(), np.nan)

    # ---- book venue (exactly one) ------------------------------------------
    if book_venue:
        b = _reindex(_book_bars(con, book_venue, t0, t1, bar_ms, depth_levels,
                                src.day_rel["depth_snapshots"]))
        alive = book_cov > 0.0
        out[f"mid_{book_venue}"] = _state(b["mid"], alive)
        out[f"spread_{book_venue}"] = _state(b["spread"], alive)
        out[f"spread_bps_{book_venue}"] = _state(b["spread_bps"], alive)
        micro = _state(b["microprice"], alive)
        out[f"microprice_{book_venue}"] = micro
        out[f"micro_minus_mid_{book_venue}"] = micro - out[f"mid_{book_venue}"].to_numpy()
        out[f"book_imbalance_{book_venue}"] = _state(b["book_imbalance"], alive)
        ofi_n = _flow(b["ofi_n"], alive)
        ofi = _flow(b["ofi"], alive)
        out[f"ofi_{book_venue}"] = np.where(ofi_n > 0, ofi, np.nan)
        out[f"ofi_n_{book_venue}"] = ofi_n
        out[f"ofi_gap_pairs_{book_venue}"] = _flow(b["ofi_gap_pairs"], alive)
        sb = _state(b["depth_slope_bid"], alive)
        sa = _state(b["depth_slope_ask"], alive)
        out[f"depth_bid_{book_venue}"] = _state(b["depth_bid"], alive)
        out[f"depth_ask_{book_venue}"] = _state(b["depth_ask"], alive)
        out[f"depth_slope_bid_{book_venue}"] = sb
        out[f"depth_slope_ask_{book_venue}"] = sa
        with np.errstate(invalid="ignore", divide="ignore"):
            denom = sb + sa
            out[f"depth_slope_imb_{book_venue}"] = np.where(denom != 0, (sb - sa) / denom, np.nan)
        out[f"depth_levels_{book_venue}"] = _state(b["depth_levels"], alive)
        out[f"book_snapshots_{book_venue}"] = _flow(b["book_snapshots"], alive)
        out[f"coverage_book_{book_venue}"] = book_cov

    # ---- liquidations -------------------------------------------------------
    recorded_by_date = {d["date"]: bool(d.get("liquidations_recorded"))
                        for d in src.manifest["days"]}
    bar_dates = pd.to_datetime(grid, unit="ms", utc=True).strftime("%Y-%m-%d").to_numpy()
    liq_recorded = np.array([recorded_by_date.get(str(d), False) for d in bar_dates])
    for v in liq_venues:
        f = _reindex(_liq_bars(con, v, t0, bar_ms))
        # NaN unless the venue leg was demonstrably alive AND the source recorded
        # the day at all. LIVENESS decides zero-vs-unknown; ROW COUNT never does.
        # `liquidations_recorded` is computed backend-symmetrically in
        # _open_source precisely so a local zero-row table and an absent hf
        # partition (upload_hf.py skips empty tables) give the same 0.0 rather
        # than 0.0-here / NaN-there for the same day.
        #
        # The witness is the WIDER trades U depth stream: bybit prints 3-2000
        # liquidations a day, so the leg cannot witness its own liveness.
        alive = (cov_liq[v] > 0.0) & liq_recorded
        out[f"coverage_liq_{v}"] = cov_liq[v]
        for col, name in (("liq_count", f"liq_count_{v}"),
                          ("liq_notional_usd", f"liq_notional_usd_{v}"),
                          ("liq_long_notional", f"liq_long_notional_{v}"),
                          ("liq_short_notional", f"liq_short_notional_{v}")):
            out[name] = _flow(f[col], alive)

    for col in out.columns:
        if col not in ("is_gap", "ret_spans_gap"):
            out[col] = out[col].astype("float64")
        else:
            out[col] = out[col].astype("bool")
    return out


def volume_buckets(
    start: Union[str, pd.Timestamp],
    end: Union[str, pd.Timestamp],
    *,
    venue: str,
    symbol: Optional[Union[str, dict]] = None,
    bucket_volume: Optional[float] = None,
    buckets_per_day: int = 50,
    window_buckets: int = 50,
    source: str = "auto",
    store_dir: Union[str, Path] = STORE_DIR,
    hf_repo: str = HF_REPO,
    materialize: Optional[bool] = None,
    now_ms: Optional[int] = None,
) -> pd.DataFrame:
    """Volume-clock buckets for one venue, **right-labelled at the bucket close**.

    **Not a bar frame, and deliberately not named like one.** It carries no
    ``open/high/low/close/volume``, so ``features.atr`` and
    ``backtest.walk_forward`` do not apply — it is the VPIN diagnostic table that
    sits *behind* the ``vpin_*`` columns of :func:`order_flow_bars`. It was
    called ``volume_bars`` in the first cut of this module; "volume bars" is
    López de Prado's term for information-driven BARS, and sitting next to
    ``order_flow_bars`` under that name invited exactly the wrong assumption
    about its contract.

    Index is the bucket's closing timestamp (UTC), because that is the first
    moment the bucket is knowable — labelling at the open would leak. Columns:
    ``venue, epoch_day, bucket_index, bucket_volume, buy_volume, sell_volume,
    delta, imbalance, vpin, trade_count, open_ts_ms, close_ts_ms, duration_s,
    window_span_s, window_gap_s``.

    Only **complete** buckets are returned; each UTC day's trailing partial
    bucket is dropped rather than published half-formed. ``vpin`` carries the
    Andersen-Bondarenko contested note (see :data:`PROVENANCE`); the series is
    published, toxicity is not claimed. ``window_gap_s`` says how much detected
    feed silence the rolling window spans — a mean over N buckets carries no
    wall-clock information on its own.
    """
    _require_deps()
    if not _VENUE_RE.match(venue):
        raise OrderFlowError(f"bad venue code {venue!r}")
    t_start, t_end = _to_utc(start), _to_utc(end)
    t0 = int(t_start.value // 1_000_000)
    t1 = int(t_end.value // 1_000_000)
    src = _open_source(
        _dates_between(t_start, t_end), source=source, store_dir=Path(store_dir),
        hf_repo=hf_repo, exchanges=(venue,), t0_ms=t0, t1_ms=t1,
        tables=["trades"], symbol=symbol, day_tables=("trades",),
        materialize=materialize, now_ms=now_ms,
    )
    try:
        df = _volume_buckets(src.con, venue, rel=src.day_rel["trades"],
                             bucket_volume=bucket_volume,
                             buckets_per_day=buckets_per_day,
                             window_buckets=window_buckets)
    finally:
        src.close()
    if df.empty:
        out = df.copy()
        out.index = pd.DatetimeIndex([], tz="UTC", name="bucket_close")
        return out
    out = df.copy()
    out.index = pd.to_datetime(out["close_ts_ms"].to_numpy(), unit="ms", utc=True)
    out.index.name = "bucket_close"
    out.attrs["orderflow"] = {
        "schema_version": SCHEMA_VERSION,
        "venue": venue,
        "buckets_per_day": buckets_per_day,
        "window_buckets": window_buckets,
        "bucket_volume": bucket_volume,
        "manifest": src.manifest,
        "contested": _CIT_AB,
    }
    return out


# --------------------------------------------------------------------------- #
# Downstream helpers — each labels what information it uses                     #
# --------------------------------------------------------------------------- #
def coverage_mask(bars: pd.DataFrame, min_coverage: float = 1.0) -> pd.Series:
    """Boolean mask of bars whose coverage meets ``min_coverage`` and whose
    close-to-close return does not span a gap."""
    cov = pd.Series(bars["coverage"], dtype="float64")
    ok = (cov >= float(min_coverage)) & ~bars["ret_spans_gap"].astype(bool)
    return ok.rename("coverage_ok")


def drop_gap_bars(bars: pd.DataFrame, min_coverage: float = 1.0) -> pd.DataFrame:
    """Drop bars that fail :func:`coverage_mask`.

    **Labelled honestly:** this is a *sample definition* made with ex-post
    knowledge of **data availability** (never of prices). That is standard
    practice and it is also a choice — a live system does not know in advance
    that the feed is about to die. Recorded, not hidden.

    **Not sufficient on its own.** Dropping rows leaves a shorter index, and
    ``backtest.run`` computes ``pct_change()`` on whatever index it is handed —
    so a retained bar whose predecessor was dropped still chains its return back
    to the last *surviving* bar. Use :func:`segments` when that must not happen,
    or :func:`gap_flat_positions` to keep the regular grid and go flat instead.
    """
    return bars.loc[coverage_mask(bars, min_coverage)]


def gap_flat_positions(positions: pd.Series, bars: pd.DataFrame) -> pd.Series:
    """Force the target position flat so no gap-spanning return is ever traded.

    The shift matters and is easy to get wrong. ``backtest.run`` trades
    ``pos.shift(1)``, so the P&L at bar ``t`` is ``pos[t-1] * r[t]``. To avoid
    earning ``r[t]`` when ``ret_spans_gap[t]`` is true, the position that must be
    zero is the one at ``t-1`` — i.e. the flag is applied **shifted back by one
    bar**, not in place. The final bar is flattened too (nothing can be known
    about the return after the sample ends).

    Because ``ret_spans_gap[t]`` is already true whenever bar ``t-1`` is not
    fully covered, this single rule also keeps a position from being sized on a
    partially-observed bar.

    One more reason to use it: ``backtest.run`` calls ``px.pct_change()``
    directly, and pandas' default ``fill_method='pad'`` forward-fills a NaN
    close before differencing — so an untouched position would earn the entire
    across-the-hole move in one bar. Going flat is what stops that padding from
    being traded. (``backtest.walk_forward`` drops NaN prices first, which moves
    the same jump to the first surviving bar rather than removing it.)

    Same label as :func:`drop_gap_bars`: it uses ex-post knowledge of *data
    availability* (never of prices). A live system cannot know its feed is about
    to die; this helper can, and says so.
    """
    pos = pd.Series(positions, dtype="float64").reindex(bars.index)
    spans = bars["ret_spans_gap"].astype(bool).to_numpy()
    bad = np.empty(spans.size, dtype=bool)
    bad[-1:] = True
    if spans.size > 1:
        bad[:-1] = spans[1:]
    return pd.Series(np.where(bad, 0.0, pos.to_numpy()), index=bars.index, name=pos.name)


def segments(bars: pd.DataFrame, min_coverage: float = 1.0) -> list[pd.DataFrame]:
    """Split ``bars`` into maximal runs of contiguous, sufficiently-covered bars.

    The strictest downstream option: backtest each segment separately and
    concatenate the OOS returns, so no ``pct_change()`` ever crosses a hole.
    """
    ok = coverage_mask(bars, min_coverage).to_numpy()
    out: list[pd.DataFrame] = []
    i = 0
    while i < ok.size:
        if not ok[i]:
            i += 1
            continue
        j = i
        while j + 1 < ok.size and ok[j + 1]:
            j += 1
        out.append(bars.iloc[i:j + 1])
        i = j + 1
    return out
