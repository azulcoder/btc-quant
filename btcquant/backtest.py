"""backtest.py — vectorized, no-look-ahead backtester for btc-quant.

The honesty rails are the product (DESIGN.md non-negotiables; RESEARCH.md §3):

* **No look-ahead.** The target-position series is **shifted by one bar inside
  :func:`run`** so a signal computed at the close of bar ``t`` only trades the
  return of bar ``t+1``. This is *asserted*, not merely documented — a strategy
  that tries to trade its own bar will raise.
* **Costs + slippage are on by default.** Cost is charged on **turnover**
  (``|Δ position|``) at ``cost_bps + slippage_bps`` per unit of turnover, so a
  full 0→1 entry then 1→0 exit pays the round-trip. Net returns are returned
  alongside gross so a gross-only curve never stands alone.
* **Buy-and-hold is the baseline** and the **deflated** Sharpe is the headline,
  not a single equity curve — :func:`run` threads the search ``n_trials`` through
  to ``risk.summary`` so the reported DSR reflects how hard you searched.
* **Out-of-sample is mandatory.** :func:`walk_forward` fits on each in-sample
  block, evaluates the *next* (untouched) out-of-sample block, concatenates the
  OOS pieces, and reports OOS-vs-IS — the overfitting tell.

All functions are pure (no I/O) and operate on pandas objects with a shared,
ascending ``DatetimeIndex``.
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import features, risk

__all__ = ["run", "walk_forward", "cpcv"]


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _align(positions: pd.Series, prices: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Coerce to float Series on a shared, sorted, de-duplicated index.

    Positions and prices are inner-joined on their common timestamps so the
    bar-return at ``t`` is always paired with the position decided at ``t-1``.
    Both are returned ascending with duplicate timestamps dropped (keep first).
    """
    pos = pd.Series(positions, dtype="float64")
    px = pd.Series(prices, dtype="float64")

    pos = pos[~pos.index.duplicated(keep="first")].sort_index()
    px = px[~px.index.duplicated(keep="first")].sort_index()

    idx = pos.index.intersection(px.index)
    return pos.reindex(idx), px.reindex(idx)


def _assert_no_lookahead(raw_pos: pd.Series, traded_pos: pd.Series) -> None:
    """Assert the traded position is the *prior* bar's target (shift-by-1).

    ``traded_pos`` must equal ``raw_pos.shift(1)`` everywhere both are defined.
    The very first traded bar must be NaN (no decision existed before the sample
    began). Raising here is intentional: silently trading the current bar is the
    classic look-ahead bug and the test-suite asserts this contract.
    """
    expected = raw_pos.shift(1)
    if not bool(pd.isna(traded_pos.iloc[0])):
        raise AssertionError(
            "look-ahead guard: first traded position must be NaN "
            "(no signal existed before the sample start)."
        )
    # Compare on the overlap where the shifted target is defined; NaNs must align.
    a = traded_pos.to_numpy()
    b = expected.to_numpy()
    both_nan = np.isnan(a) & np.isnan(b)
    if not np.allclose(np.where(both_nan, 0.0, a), np.where(both_nan, 0.0, b), equal_nan=True):
        raise AssertionError(
            "look-ahead guard: traded positions are not the 1-bar-lagged targets; "
            "a signal at bar t must trade bar t+1, never its own bar."
        )


# --------------------------------------------------------------------------- #
# Core backtest                                                                #
# --------------------------------------------------------------------------- #
def run(
    positions: pd.Series,
    prices: pd.Series,
    cost_bps: float = 10.0,
    slippage_bps: float = 2.0,
    periods_per_year: int = 365,
    n_trials: int = 1,
    var_trials_sr: Optional[float] = None,
) -> dict:
    """Vectorized backtest of a target-position series against a price series.

    The ``positions`` series is the **target weight per bar** (``[-1, 1]`` for
    long/short, ``[0, 1]`` for long/flat) decided at each bar's *close*. It is
    **shifted by one bar internally** so the weight chosen at bar ``t`` earns the
    asset return of bar ``t+1`` — there is **no look-ahead**, and this is asserted.

    Transaction cost (fees + slippage) is charged on **turnover**
    ``= |position_t - position_{t-1}|`` at ``(cost_bps + slippage_bps) / 10_000``
    per unit of turnover, deducted from that bar's gross return.

    Cost-model grounding (Harris, *Trading and Exchanges*): a **conservative constant
    bps on turnover** is the honest choice for a daily/hourly OHLCV backtest. Estimating
    the realized spread from the data and feeding it back as cost would be **look-ahead**
    (you do not know the fill spread until you trade), and btc-quant stores no historical
    quote/tick series to bound it. So we charge a fixed, deliberately-pessimistic bps and
    flag the net-vs-gross gap rather than model a spread we cannot observe out-of-sample.

    Parameters
    ----------
    positions : pd.Series
        Target weights per bar (pre-shift), indexed by a ``DatetimeIndex``.
    prices : pd.Series
        Price (close) series on a compatible index.
    cost_bps : float, default 10.0
        One-way transaction fee in basis points charged per unit turnover.
    slippage_bps : float, default 2.0
        One-way slippage in basis points charged per unit turnover.
    periods_per_year : int, default 365
        Bars per year (365 for daily crypto; ``24*365`` for hourly).
    n_trials : int, default 1
        Number ``N`` of strategy configurations searched, threaded into the
        Deflated Sharpe so the headline metric reflects selection bias. ``1`` is
        the honest "no-selection" floor.
    var_trials_sr : float, optional
        Variance of the (per-period) Sharpe ratios across the ``n_trials``. If
        omitted with ``n_trials > 1`` it falls back to the sampling variance of a
        skill-less Sharpe, ``1 / n_periods`` (Bailey-LdP), which is conservative.

    Returns
    -------
    dict
        ``{equity, returns, gross_returns, turnover, trades, stats}`` where:

        * ``equity`` — net wealth curve ``∏(1 + net_return)`` (starts ~1.0).
        * ``returns`` — per-bar **net-of-cost** strategy returns.
        * ``gross_returns`` — per-bar returns **before** cost/slippage.
        * ``turnover`` — per-bar ``|Δ position|`` (the cost base).
        * ``trades`` — int count of bars where the position changed.
        * ``stats`` — :func:`risk.summary` dict, augmented with the **deflated
          Sharpe** (``n_trials`` / ``var_trials_sr``), ``n_trials``, total
          ``turnover`` and ``avg_turnover``, and the cost in bps.
    """
    pos, px = _align(positions, prices)
    if len(px) < 2:
        raise ValueError("run() needs at least 2 aligned price/position bars.")

    # Asset simple returns; bar t return is realized over (t-1 -> t].
    asset_ret = px.pct_change()

    # No look-ahead: the weight decided at the close of bar t-1 earns bar t's
    # return. Shift the target forward by one bar before it trades.
    traded_pos = pos.shift(1)
    _assert_no_lookahead(pos, traded_pos)

    gross_returns = (traded_pos * asset_ret).rename("gross_returns")

    # Turnover = |Δ traded position|. The first traded bar's turnover is the cost
    # of establishing the opening position from flat (0).
    turnover = traded_pos.fillna(0.0).diff().abs()
    turnover.iloc[0] = abs(float(traded_pos.fillna(0.0).iloc[0]))
    turnover = turnover.rename("turnover")

    cost_rate = (float(cost_bps) + float(slippage_bps)) / 10_000.0
    costs = turnover * cost_rate

    net_returns = (gross_returns.fillna(0.0) - costs).rename("returns")
    # Re-mask the warm-up bar (no traded position yet) as a true 0-return bar,
    # except for the opening-trade cost which is real.
    net_returns.iloc[0] = -float(costs.iloc[0])

    equity = (1.0 + net_returns.fillna(0.0)).cumprod().rename("equity")

    trades = int((turnover > 0).sum())

    # --- Stats, incl. the headline Deflated Sharpe (selection-bias aware). ---
    stats = risk.summary(net_returns, equity=equity, periods_per_year=periods_per_year)

    n_periods = int(stats.get("n_periods", 0))
    sr_period = stats.get("sharpe_per_period", float("nan"))
    skew = stats.get("skew", float("nan"))
    kurt = stats.get("kurtosis", float("nan"))

    if var_trials_sr is None:
        # Sampling variance of a skill-less per-period Sharpe ≈ 1/n (Bailey-LdP).
        var_sr = 1.0 / n_periods if n_periods > 0 else float("nan")
    else:
        var_sr = float(var_trials_sr)

    stats["deflated_sharpe"] = risk.deflated_sharpe_ratio(
        sr_period, n_periods, skew, kurt, int(n_trials), var_sr
    )
    stats["n_trials"] = int(n_trials)
    stats["var_trials_sr"] = float(var_sr) if var_sr == var_sr else float("nan")
    stats["trades"] = trades
    stats["total_turnover"] = float(turnover.sum())
    stats["avg_turnover"] = float(turnover.mean()) if len(turnover) else float("nan")
    stats["cost_bps"] = float(cost_bps)
    stats["slippage_bps"] = float(slippage_bps)

    return {
        "equity": equity,
        "returns": net_returns,
        "gross_returns": gross_returns,
        "turnover": turnover,
        "trades": trades,
        "stats": stats,
    }


# --------------------------------------------------------------------------- #
# Walk-forward (out-of-sample) evaluation                                      #
# --------------------------------------------------------------------------- #
def walk_forward(
    make_positions: Callable[[pd.Series], pd.Series],
    prices: pd.Series,
    n_splits: int = 5,
    cost_bps: float = 10.0,
    slippage_bps: float = 2.0,
    periods_per_year: int = 365,
    min_train: Optional[int] = None,
) -> dict:
    """Anchored walk-forward: fit in-sample, trade the *next* out-of-sample block.

    The price history is cut into ``n_splits + 1`` contiguous blocks. For each
    fold the model is *fit* on everything up to and including the current block
    (the **in-sample** set), then used to generate positions that are **only
    traded on the following block** (the **out-of-sample** set). The OOS pieces
    are concatenated into one continuous OOS track record; its stats are compared
    against the pooled in-sample stats — a large IS≫OOS gap is the overfitting
    tell (RESEARCH.md §3: walk-forward + multi-path dispersion is the headline,
    never the single best equity curve).

    ``make_positions`` is a callable ``prices -> positions`` (the same shape
    ``strategies.*`` returns: a target-weight Series on the price index). It is
    re-evaluated on the growing in-sample window each fold, and the positions it
    proposes for the OOS dates are taken from that fit — so any look-ahead in the
    strategy itself is exposed by the IS-vs-OOS divergence, while the backtester's
    own 1-bar shift still guarantees no intra-bar leakage.

    Parameters
    ----------
    make_positions : Callable[[pd.Series], pd.Series]
        Strategy factory: given a price Series, returns target positions on the
        same index.
    prices : pd.Series
        Price (close) series, ascending ``DatetimeIndex``.
    n_splits : int, default 5
        Number of out-of-sample folds (the data is split into ``n_splits + 1``
        contiguous blocks; the first block is train-only).
    cost_bps, slippage_bps : float
        Passed through to :func:`run` for both the OOS and IS backtests.
    periods_per_year : int, default 365
    min_train : int, optional
        Minimum number of bars in the first in-sample window. Defaults to one
        block. Folds whose OOS slice would be empty are skipped.

    Returns
    -------
    dict
        ``{oos, is_, folds, oos_equity, oos_returns}`` where ``oos`` and ``is_``
        are :func:`risk.summary`-style stat dicts (with ``n_trials = n_splits``
        for the OOS Deflated Sharpe — each fold is a trial), ``folds`` is a list
        of per-fold ``{train_end, oos_start, oos_end, stats}`` records, and
        ``oos_equity`` / ``oos_returns`` are the concatenated OOS series.
    """
    px = pd.Series(prices, dtype="float64")
    px = px[~px.index.duplicated(keep="first")].sort_index().dropna()
    n = len(px)
    if n_splits < 1:
        raise ValueError("n_splits must be >= 1.")
    if n < (n_splits + 1) * 2:
        raise ValueError(
            f"walk_forward needs >= {(n_splits + 1) * 2} bars for {n_splits} splits; "
            f"got {n}."
        )

    # Contiguous, (near-)equal blocks; the first is train-only.
    edges = np.linspace(0, n, n_splits + 2, dtype=int)
    if min_train is None:
        min_train = int(edges[1])

    oos_returns_parts: list[pd.Series] = []
    oos_pos_parts: list[pd.Series] = []
    is_returns_parts: list[pd.Series] = []
    folds: list[dict] = []

    for k in range(1, n_splits + 1):
        train_end = max(int(edges[k]), min_train)
        oos_start = train_end
        oos_end = int(edges[k + 1])
        if oos_end <= oos_start or oos_start >= n:
            continue

        # Fit the strategy on the in-sample window; generate the full position
        # path, then slice IS / OOS from it (positions for OOS dates are the
        # decisions the fit would have produced as data arrived).
        train_px = px.iloc[:train_end]
        full_pos = pd.Series(make_positions(px), dtype="float64").reindex(px.index)

        # In-sample block performance (the optimistic, fitted view).
        is_px = train_px
        is_pos = full_pos.reindex(is_px.index)
        if is_px.notna().sum() >= 2 and is_pos.notna().any():
            is_res = run(
                is_pos,
                is_px,
                cost_bps=cost_bps,
                slippage_bps=slippage_bps,
                periods_per_year=periods_per_year,
            )
            is_returns_parts.append(is_res["returns"])

        # Out-of-sample block (untouched by the fit's selection).
        oos_px = px.iloc[oos_start:oos_end]
        oos_pos = full_pos.reindex(oos_px.index)
        oos_res = run(
            oos_pos,
            oos_px,
            cost_bps=cost_bps,
            slippage_bps=slippage_bps,
            periods_per_year=periods_per_year,
        )
        oos_returns_parts.append(oos_res["returns"])
        oos_pos_parts.append(oos_pos)

        folds.append(
            {
                "fold": k,
                "train_end": px.index[train_end - 1],
                "oos_start": oos_px.index[0],
                "oos_end": oos_px.index[-1],
                "oos_sharpe": oos_res["stats"]["sharpe"],
                "stats": oos_res["stats"],
            }
        )

    if not oos_returns_parts:
        raise ValueError("walk_forward produced no out-of-sample folds.")

    oos_returns = pd.concat(oos_returns_parts).sort_index()
    oos_returns = oos_returns[~oos_returns.index.duplicated(keep="first")]
    oos_equity = (1.0 + oos_returns.fillna(0.0)).cumprod().rename("equity")

    # Concatenated OOS target positions (contiguous, anchored) — for the Tharp
    # expectancy / R-multiple OOS ledger (risk.expectancy_report).
    oos_positions = pd.concat(oos_pos_parts).sort_index()
    oos_positions = oos_positions[~oos_positions.index.duplicated(keep="first")].rename("oos_positions")

    is_returns = (
        pd.concat(is_returns_parts).sort_index() if is_returns_parts else pd.Series(dtype="float64")
    )
    is_returns = is_returns[~is_returns.index.duplicated(keep="first")]

    # OOS deflated Sharpe treats each fold as a trial (selection across folds).
    oos_stats = risk.summary(oos_returns, equity=oos_equity, periods_per_year=periods_per_year)
    oos_stats["deflated_sharpe"] = risk.deflated_sharpe_ratio(
        oos_stats.get("sharpe_per_period", float("nan")),
        int(oos_stats.get("n_periods", 0)),
        oos_stats.get("skew", float("nan")),
        oos_stats.get("kurtosis", float("nan")),
        int(n_splits),
        1.0 / oos_stats["n_periods"] if oos_stats.get("n_periods", 0) > 0 else float("nan"),
    )
    oos_stats["n_trials"] = int(n_splits)

    is_stats = (
        risk.summary(is_returns, periods_per_year=periods_per_year)
        if len(is_returns) >= 2
        else {}
    )

    return {
        "oos": oos_stats,
        "is_": is_stats,
        "folds": folds,
        "oos_equity": oos_equity,
        "oos_returns": oos_returns,
        "oos_positions": oos_positions,
    }


# --------------------------------------------------------------------------- #
# Combinatorial Purged CV (multi-path OOS dispersion)                          #
# --------------------------------------------------------------------------- #
def cpcv(
    make_positions: Callable[[pd.Series], pd.Series],
    prices: pd.Series,
    n_blocks: int = 6,
    k_test: int = 2,
    embargo: float = 0.01,
    cost_bps: float = 10.0,
    slippage_bps: float = 2.0,
    periods_per_year: int = 365,
) -> dict:
    """Combinatorial Purged CV — the *distribution* of OOS Sharpe across time-block
    subsets (López de Prado 2018; RESEARCH.md §3/§5: "report multi-path dispersion as
    the headline, not the single best equity curve").

    Split the bars into ``n_blocks`` contiguous groups; for each of the
    ``C(n_blocks, k_test)`` ways to pick ``k_test`` groups as the test set, evaluate
    the strategy on **only those** bars (with a leading ``embargo`` trimmed from each
    test group to purge signal leakage from the preceding bar), giving one OOS path
    Sharpe. The spread of those paths is the honest headline: a strategy whose edge
    lives in one regime shows a wide, often sign-flipping dispersion.

    These strategies are causal and parameter-free, so there is no per-fold *refit*;
    CPCV here measures **regime sensitivity** of the same position rule across
    different held-out time subsets. Returns annualized Sharpe statistics over the
    paths::

        {paths, n_paths, median_sharpe, mean_sharpe, p25, p75, iqr, min, max}

    Reference: López de Prado (2018), *Advances in Financial Machine Learning*, ch. 7
    (purged CV / embargo) & ch. 12 (CPCV).
    """
    import itertools

    nan_out = {"paths": [], "n_paths": 0, "median_sharpe": float("nan"),
               "mean_sharpe": float("nan"), "p25": float("nan"), "p75": float("nan"),
               "iqr": float("nan"), "min": float("nan"), "max": float("nan")}
    px = pd.Series(prices, dtype="float64")
    px = px[~px.index.duplicated(keep="first")].sort_index().dropna()
    n = len(px)
    if n_blocks < 2 or k_test < 1 or k_test >= n_blocks or n < (n_blocks + 1) * 2:
        return nan_out

    full_pos = pd.Series(make_positions(px), dtype="float64").reindex(px.index)
    res = run(full_pos, px, cost_bps=cost_bps, slippage_bps=slippage_bps,
              periods_per_year=periods_per_year)
    rets = res["returns"]

    edges = np.linspace(0, n, n_blocks + 1, dtype=int)
    blocks = [np.arange(edges[i], edges[i + 1]) for i in range(n_blocks)]
    blocks = [b for b in blocks if b.size > 0]
    if len(blocks) < 2:
        return nan_out

    paths: list[float] = []
    for combo in itertools.combinations(range(len(blocks)), k_test):
        parts = []
        for bi in combo:
            blk = blocks[bi]
            drop = min(blk.size - 1, max(1, int(round(embargo * blk.size)))) if blk.size > 1 else 0
            parts.append(blk[drop:])
        idx = np.concatenate(parts) if parts else np.array([], dtype=int)
        if idx.size < 2:
            continue
        path_ret = rets.iloc[idx]
        paths.append(risk.sharpe(path_ret, periods_per_year=periods_per_year))

    arr = np.array([p for p in paths if np.isfinite(p)], dtype=float)
    if arr.size == 0:
        return nan_out
    p25, p75 = (float(np.percentile(arr, 25)), float(np.percentile(arr, 75)))
    return {
        "paths": [float(p) for p in paths],
        "n_paths": int(arr.size),
        "median_sharpe": float(np.median(arr)),
        "mean_sharpe": float(np.mean(arr)),
        "p25": p25, "p75": p75, "iqr": float(p75 - p25),
        "min": float(np.min(arr)), "max": float(np.max(arr)),
    }
