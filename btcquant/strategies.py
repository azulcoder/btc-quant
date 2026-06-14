"""strategies.py — the btc-quant strategy library (RESEARCH.md §5 first-cut set).

Every strategy is a **pure** function that maps a price/funding ``DataFrame`` to a
**target-position** ``pd.Series`` in ``[-1, 1]`` (or ``[0, 1]`` for long/flat). The
position at bar ``t`` is the *desired* exposure decided at the **close** of bar ``t``;
the backtester (:func:`btcquant.backtest.run`) shifts it forward by one bar so it
trades bar ``t+1`` — therefore these functions may legitimately use bar ``t``'s own
close, and **must not** peek at any future bar. None of them shift internally.

Honesty rails (see ``DESIGN.md`` / ``RESEARCH.md``):

- **Buy-and-hold is the baseline** every strategy is scored against, net of cost.
- The headline metric is the **net-of-cost, out-of-sample, Deflated Sharpe** — never
  a single equity curve. A high in-sample Sharpe on a negatively-skewed payoff
  (``carry``, ``short_vol``, ``pairs_coint``) is a *trap*, not an edge.
- No look-ahead: signals use only past/present data and trade the next bar.

Each docstring states: the **edge**, the evidence **tag**
(``[Established]``/``[Practitioner]``/``[Mixed]``/``[Weak]``), the **primary citation**,
and the honest **caveat** (when it works, when it inverts, how it decays).

This module consumes the real, no-look-ahead helpers in :mod:`btcquant.features`
(``sma``, ``ema``, ``momentum``, ``realized_vol``, ``simple_returns``, ``zscore``,
``ou_half_life``).
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from . import features

__all__ = [
    "buy_and_hold",
    "ma_trend_filter",
    "vol_target",
    "percent_risk_size",
    "random_entry",
    "tsmom",
    "carry",
    "pairs_coint",
    "pairs_ou",
    "short_vol",
]


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _close(df: pd.DataFrame) -> pd.Series:
    """Extract the ``close`` column as a float Series (raise a clear error if absent)."""
    if "close" not in df.columns:
        raise KeyError("strategy expects a 'close' column in the price DataFrame")
    return pd.Series(df["close"], dtype="float64")


# --------------------------------------------------------------------------- #
# 0. Baseline                                                                  #
# --------------------------------------------------------------------------- #
def buy_and_hold(df: pd.DataFrame) -> pd.Series:
    """Always-long BTC (target position ``= 1``) — the BASELINE (RESEARCH.md §5.0).

    Edge
        None claimed. This is the benchmark, *not* a strategy. BTC's secular
        uptrend means most "outperforming" backtests are merely conditioning on
        that uptrend, so every strategy must be scored *against* this line —
        net of cost, on the same Deflated-Sharpe basis.

    Evidence
        ``[Baseline]`` — the hard reference. In crypto it is a genuinely hard
        benchmark to beat after costs.

    Primary citation
        RESEARCH.md §1 / §5; the buy-and-hold benchmark convention throughout
        the brief.

    Caveat
        Full exposure to BTC's full drawdowns (multiple > 80% peak-to-trough
        crashes historically). It "works" only because of the realized uptrend;
        it carries the entire tail. Reported only as the reference curve, never
        as a recommendation.

    Returns
        ``pd.Series`` of ``1.0`` aligned to ``df.index`` (long/flat in ``[0, 1]``).
    """
    return pd.Series(1.0, index=df.index, name="buy_and_hold")


# --------------------------------------------------------------------------- #
# 1. Trend filter                                                             #
# --------------------------------------------------------------------------- #
def ma_trend_filter(df: pd.DataFrame, n: int = 200, fast: int | None = None) -> pd.Series:
    """Hold BTC only above a long moving average, else go to cash (RESEARCH.md §2.2 / §5.1).

    Two modes (long/flat, target in ``{0, 1}``):

    * **Single MA** (default, ``fast is None``): ``position_t = 1 if Close_t >
      SMA(Close, n) else 0``.
    * **Dual cross** (pass ``fast``, e.g. ``fast=50, n=200`` → the "golden cross"):
      ``position_t = 1 if SMA(Close, fast) > SMA(Close, n) else 0``.

    Edge
        A volatility / drawdown manager, **not** a pure alpha source: stay long
        BTC while it trends above its long MA, step aside below it. Trims crashes
        at the cost of bull-market lag.

    Evidence
        ``[Practitioner]``.

    Primary citation
        Grayscale Research, "The Trend Is Your Friend"; Glucksmann (2019), MSc
        thesis, ETH Zurich. (RESEARCH.md §2.2.)

    Caveat
        Reduces realized vol and max drawdown vs buy-and-hold, but: (1) whipsaws
        in sideways markets; (2) lags reversals — gives back gains at tops,
        re-enters late; (3) very few independent signals on 200d (~8 golden-cross
        trades 2018–2023) → low statistical power, high overfit risk; (4)
        parameter/period sensitive. Treat as **risk management with modest
        switching cost**, not standalone alpha — do not over-tune ``n``.

    Parameters
        df : DataFrame with a ``close`` column.
        n : long MA window in bars (default 200; "10 months" on daily).
        fast : if given, switch to the dual-cross variant (``fast`` < ``n``).

    Returns
        ``pd.Series`` of target positions in ``{0.0, 1.0}`` (NaN during MA warm-up).
    """
    close = _close(df)
    slow_ma = features.sma(close, n)

    if fast is None:
        reference = close
    else:
        if fast >= n:
            raise ValueError(f"dual-cross requires fast ({fast}) < slow n ({n})")
        reference = features.sma(close, fast)

    # 1 when above, 0 when at/below; NaN propagates through the warm-up region so
    # the backtester never trades an unwarmed window.
    above = reference > slow_ma
    pos = above.astype("float64")
    pos[slow_ma.isna() | reference.isna()] = np.nan
    return pos.rename("ma_trend_filter")


# --------------------------------------------------------------------------- #
# 2. Volatility targeting (a SIZING layer, not a standalone signal)            #
# --------------------------------------------------------------------------- #
def vol_target(
    positions: pd.Series,
    df: pd.DataFrame,
    target_vol: float = 0.15,
    window: int = 20,
    periods_per_year: int = 365,
    max_leverage: float = 3.0,
) -> pd.Series:
    """Scale any position series inversely to realized vol (RESEARCH.md §2.12 / §5.2).

    A **sizing wrapper**, not a strategy: it takes the *sign/shape* of any
    ``positions`` series and rescales its magnitude so the position runs near a
    constant ``target_vol`` annualized::

        w_t = positions_t * (target_vol / sigma_t)

    where ``sigma_t`` is the ex-ante realized vol of BTC returns over a trailing
    ``window`` (``features.realized_vol``, annualized). Leverage is capped at
    ``max_leverage`` and the result is clipped to ``[-1, 1]`` so it remains a
    valid backtester target weight. ``sigma_t`` uses only past/present returns →
    no look-ahead (and it is the *current* bar's estimate sizing the position
    that trades next bar).

    Edge
        Stabilizes risk: scaling down in high-vol regimes and up in calm ones
        reliably cuts tail risk, max drawdown, and vol-of-vol.

    Evidence
        ``[Mixed]`` — but **robust on the part that matters** (tail control).

    Primary citation
        Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert (2018), *JPM*
        — drawdown/vol-of-vol reduction across 60+ assets. (RESEARCH.md §2.12.)

    Caveat
        **Robust:** almost always reduces tail risk / max drawdown / vol-of-vol,
        independent of the leverage effect. **Fragile:** the *Sharpe* lift only
        appears for assets with negative return-vol correlation; **BTC's is
        unstable and often inverted**, so an equity-style Sharpe gain is not
        guaranteed and can hurt. Practitioner crypto lifts usually pair scaling
        *with* a trend signal — credit the signal, not the scaling. Forces
        buying calm / selling stress → turnover when liquidity thins. Be explicit
        in the terminal that the durable benefit here is **tail control, not
        Sharpe**.

    Parameters
        positions : the raw target-position Series to be scaled (e.g. from
            ``ma_trend_filter`` or ``tsmom``).
        df : DataFrame with a ``close`` column (the asset being sized).
        target_vol : annualized target volatility (default 0.15 = 15%).
        window : realized-vol lookback in bars (default 20; brief: 20–60d).
        periods_per_year : annualization factor (365 for daily crypto).
        max_leverage : hard cap on the gross scale before clipping (default 3×).

    Returns
        ``pd.Series`` of scaled target positions, clipped to ``[-1, 1]``.
    """
    positions = pd.Series(positions, dtype="float64")
    close = _close(df)
    rets = features.simple_returns(close)
    sigma = features.realized_vol(rets, window=window, periods_per_year=periods_per_year)

    # Avoid div-by-zero in flat windows; the scale is the *current* sigma sizing
    # the position that will trade next bar.
    scale = target_vol / sigma.replace(0.0, np.nan)
    scale = scale.clip(upper=max_leverage)

    scaled = (positions.reindex(scale.index) * scale).clip(-1.0, 1.0)
    return scaled.rename("vol_target")


def percent_risk_size(
    positions: pd.Series,
    df: pd.DataFrame,
    risk_pct: float = 0.02,
    atr_window: int = 20,
    k_stop: float = 2.0,
    max_leverage: float = 3.0,
) -> pd.Series:
    """Van Tharp **Percent-Risk position sizing** as a sizing wrapper (RESEARCH-tharp-runlog.md).

    Sizes any signal so that a ``k_stop * ATR`` adverse move ≈ ``risk_pct`` of equity::

        stop_frac = k_stop * ATR(atr_window) / close      # fractional notional stop distance
        scale     = clip(risk_pct / stop_frac, max=max_leverage)
        weight    = clip(positions * scale, -1, 1)

    This is the **ATR / range** counterpart of :func:`vol_target` (which scales by the
    close-to-close return σ). **Percent-Volatility sizing is `vol_target`** — not
    re-implemented here. Because ``ATR/close`` and return-σ are both volatility proxies,
    this is **likely a near-duplicate of `vol_target`** (cf. the Part-B B1 finding); its
    honest value is reshaping the **equity path / max-drawdown**, not the per-bet edge —
    let the OOS harness decide, and report max-DD prominently. Single-asset, so the
    cross-market 1R-equalization rationale does not apply.

    Reference: Tharp, *Trade Your Way to Financial Freedom*, Ch. 12 (Models 3 & 4).
    """
    positions = pd.Series(positions, dtype="float64")
    close = _close(df)
    stop_frac = (float(k_stop) * features.atr(df, window=atr_window) / close).replace(0.0, np.nan)
    scale = (float(risk_pct) / stop_frac).clip(upper=max_leverage)
    scaled = (positions.reindex(scale.index) * scale).clip(-1.0, 1.0)
    return scaled.rename("percent_risk")


def random_entry(df: pd.DataFrame, seed: int = 0, k_stop: float = 3.0, atr_window: int = 10) -> pd.Series:
    """Tharp's **random-entry control** (RESEARCH-tharp-runlog.md): coin-flip the direction,
    hold with a ``k_stop·ATR`` trailing stop, re-flip on a stop (always in the market). Tharp's
    famous result is that entry is barely better than random once risk-management (trailing
    stop + position sizing) is in place — so this is a **baseline/teaching control**, NOT a
    strategy to believe; it should NOT clear the OOS deflated-Sharpe / PBO gate.

    Seeded RNG → deterministic and reproducible. Causal: the position at bar ``t`` uses only
    bar ``t``'s close + ATR (the backtester's shift-by-one then trades it at ``t+1``). Returns
    ``±1`` (NaN/0 during the ATR warm-up). Reference: Tharp, Ch. 8 (the Basso/Tharp experiment).
    """
    close = _close(df)
    atr = features.atr(df, window=atr_window)
    c, a = close.to_numpy(dtype="float64"), atr.to_numpy(dtype="float64")
    rng = np.random.default_rng(seed)
    n = len(c)
    pos = np.zeros(n, dtype="float64")
    cur, stop = 0, float("nan")
    for i in range(n):
        if not np.isfinite(a[i]) or not np.isfinite(c[i]):
            cur, stop = 0, float("nan")
            continue
        if cur == 0 or (cur > 0 and c[i] <= stop) or (cur < 0 and c[i] >= stop):
            cur = 1 if rng.random() < 0.5 else -1          # (re-)enter on a coin flip
            stop = c[i] - cur * k_stop * a[i]
        else:                                              # trail the stop in the trade's favour
            ns = c[i] - cur * k_stop * a[i]
            stop = max(stop, ns) if cur > 0 else min(stop, ns)
        pos[i] = cur
    return pd.Series(pos, index=close.index, name="random_entry")


# --------------------------------------------------------------------------- #
# 3. Time-series (absolute) momentum                                          #
# --------------------------------------------------------------------------- #
def tsmom(
    df: pd.DataFrame,
    lookback: int = 20,
    vol_scaled: bool = True,
    long_short: bool = False,
    target_vol: float = 0.15,
    vol_window: int = 20,
    periods_per_year: int = 365,
    max_leverage: float = 3.0,
) -> pd.Series:
    """Short-lookback time-series (absolute) momentum on BTC (RESEARCH.md §2.1 / §5.3).

    ``position_t = sign( cum_return(t-lookback, t) )``::

        raw = +1 if trailing return > 0
              -1 if < 0 and long_short else 0   (long/flat by default)

    Vol-scaled variant (default ``vol_scaled=True``): the long/short sign is sized
    via :func:`vol_target` to run near ``target_vol`` annualized — i.e.
    ``w_t = (target_vol / sigma_t) * sign(cum_return)``, clipped to ``[-1, 1]``.
    The trailing return uses ``features.momentum`` (``P_t / P_{t-lookback} - 1``)
    → no look-ahead.

    Edge
        An asset's own recent return predicts its near-term return. In crypto the
        effect lives at **short** horizons (days to ~4 weeks). Strong in
        downturns.

    Evidence
        ``[Mixed]`` — the best-documented single-asset directional effect, but
        cost-fragile.

    Primary citation
        Shen, Urquhart & Wang (2022), *Financial Review* 57(2):319–344,
        doi:10.1111/fire.12290 (intraday BTC TSMOM ~16–17%/yr, strong in
        downturns); Moskowitz, Ooi & Pedersen (2012), *JFE* 104(2):228–250
        (the futures foundation). (RESEARCH.md §2.1.)

    Caveat
        Break-even transaction costs are only ~**3–10 bps** — profits do **not**
        survive realistic 10–50 bps round-trip spot fees without leverage/maker
        rebates, so always run it net of cost so the terminal *shows* when it
        dies. Whipsaws in ranging markets are the dominant loss source. Heavy
        parameter sensitivity = data-mining risk. **Inverts beyond ~1 month**
        (becomes reversal) — do **not** use a 12-month lookback (insignificant in
        crypto).

    Parameters
        df : DataFrame with a ``close`` column.
        lookback : trailing-return window in bars (default 20; crypto sweet spot
            1 day – 4 weeks).
        vol_scaled : if True (default), size the sign via :func:`vol_target`.
        long_short : if True, short on negative momentum (target in ``[-1, 1]``);
            if False (default), long/flat (target in ``[0, 1]``).
        target_vol, vol_window, periods_per_year, max_leverage : passed through to
            the vol-targeting layer when ``vol_scaled``.

    Returns
        ``pd.Series`` of target positions (NaN during the lookback/vol warm-up).
    """
    close = _close(df)
    mom = features.momentum(close, lookback=lookback)

    sign = np.sign(mom)  # -1 / 0 / +1, NaN preserved
    if not long_short:
        sign = sign.clip(lower=0.0)  # long/flat: negative momentum → 0
    raw = sign.rename("tsmom")

    if not vol_scaled:
        return raw

    return vol_target(
        raw,
        df,
        target_vol=target_vol,
        window=vol_window,
        periods_per_year=periods_per_year,
        max_leverage=max_leverage,
    ).rename("tsmom")


# --------------------------------------------------------------------------- #
# 4. Cash-and-carry / funding harvest                                         #
# --------------------------------------------------------------------------- #
def carry(
    funding_df: pd.DataFrame,
    enter_apr: float = 0.10,
    exit_apr: float = 0.055,
    smooth: int = 3,
    intervals_per_year: int = 1095,
    allow_inversion: bool = True,
) -> pd.Series:
    """Long-spot / short-perp funding harvest, delta-neutral (RESEARCH.md §2.6 / §5.4).

    The position is expressed on the **perp short leg** as a target weight: a value
    of ``-1`` means "fully on the carry trade" (long 1 spot, short 1 perp → delta
    ≈ 0); ``0`` means flat. While positive funding persists, longs pay shorts, so a
    short-perp leg *receives* funding.

    Signal (per funding interval, on the smoothed funding rate)::

        f_smooth = EMA(funding_rate, smooth)            # decimal per interval
        apr      = f_smooth * intervals_per_year        # crude annualization
        engage when apr > enter_apr  (re-engage threshold ~10% APR)
        exit     when apr < exit_apr (hysteresis band, ~T-bill + execution buffer)

    The smoothed rate and the prior state use only past/present funding rows → no
    look-ahead. Hysteresis (``enter_apr`` > ``exit_apr``) avoids flip-flopping
    around the threshold.

    **Decay (show it):** this premium has structurally decayed — He et al. report
    BTC carry Sharpe 2.39 (2021) → 0.70 (2022) → 1.32 (2023); the 2024 spot-ETF
    compressed the basis (~3pp DiD); Amberdata: only ~8% of 2025 days offered
    >10% APR. As ``apr`` falls below ``enter_apr`` more often, this strategy is
    flat more of the time — the terminal will *see* the decay as shrinking time
    in-trade.

    **Negative-funding inversion (show it):** when funding goes **negative** (e.g.
    FTX, Nov 2022), the short-perp leg *pays* instead of receives — the trade
    inverts. With ``allow_inversion=True`` (default) the strategy flips to the
    mirror trade (short spot / long perp → target ``+1`` on the perp leg) when
    funding is sufficiently negative (``apr < -enter_apr``); with
    ``allow_inversion=False`` it simply goes flat, never paying funding.

    Edge
        Harvest the funding longs pay shorts when perp funding is persistently
        positive; delta-neutral, single-asset, no microcap shorting.

    Evidence
        ``[Established]`` — but **decaying**.

    Primary citation
        Schmeling, Schrimpf & Todorov (2023, rev. 2025), BIS WP No. 1087 /
        SSRN 4268371; He, Manela, Ross & von Wachter (2024), arXiv:2212.06888
        (BTC SR ~1.8 retail-cost vs ~3.5 zero-fee). (RESEARCH.md §2.6.)

    Caveat
        It is a **risk premium for blow-up risk**, not a free lunch. **Inverts
        when funding goes negative** (short leg then pays). Decays as the asset
        class institutionalizes. Liquidation risk on the short leg without
        cross-margin (keep leverage ≤ 2–3×; 10× ⇒ likely liquidation per He et
        al.). Net excess over T-bills is small once financing is netted. **Ignore
        the He et al. "2024 SR 11.52" — it is an N=1,682 partial-year artifact.**
        Negative skew: many small wins, rare large losses on regime breaks — so
        the Deflated Sharpe (skew/kurtosis-aware) is the metric that matters here,
        not the raw Sharpe.

    Parameters
        funding_df : DataFrame with a ``funding_rate`` column (decimal per
            interval, e.g. ``0.0001`` == 0.01%), as returned by
            ``btcquant.data.get_funding``.
        enter_apr : annualized funding threshold to engage (default 0.10 = 10%).
        exit_apr : annualized threshold to disengage (hysteresis; default 0.055).
        smooth : EMA span over funding intervals to de-noise the rate (default 3).
        intervals_per_year : funding intervals per year for the crude APR
            annualization (default 1095 = 3×/day × 365, the common 8h cadence).
        allow_inversion : if True (default), flip to the mirror trade when funding
            is strongly negative; if False, go flat instead.

    Returns
        ``pd.Series`` of target positions on the **perp leg** in ``{-1, 0, +1}``
        (``-1`` = standard long-spot/short-perp carry; ``+1`` = inverted mirror;
        ``0`` = flat). NaN during the EMA warm-up.
    """
    if "funding_rate" not in funding_df.columns:
        raise KeyError("carry expects a 'funding_rate' column (see data.get_funding)")
    if exit_apr > enter_apr:
        raise ValueError(f"exit_apr ({exit_apr}) must be <= enter_apr ({enter_apr})")

    rate = pd.Series(funding_df["funding_rate"], dtype="float64")
    f_smooth = rate.ewm(span=max(1, smooth), adjust=False, min_periods=max(1, smooth)).mean()
    apr = f_smooth * float(intervals_per_year)

    # Stateful hysteresis with sign-aware inversion. Iterate in time order using
    # only past/present funding (carry-forward the prior state) → no look-ahead.
    pos = np.full(len(apr), np.nan, dtype="float64")
    state = 0.0  # -1 standard carry, +1 inverted mirror, 0 flat
    values = apr.to_numpy()
    for i, a in enumerate(values):
        if np.isnan(a):
            pos[i] = np.nan
            continue
        if state == 0.0:
            if a > enter_apr:
                state = -1.0  # positive funding: long spot / short perp
            elif allow_inversion and a < -enter_apr:
                state = +1.0  # negative funding: inverted mirror trade
        elif state == -1.0:
            if a < exit_apr:
                state = 0.0
        elif state == +1.0:
            if a > -exit_apr:
                state = 0.0
        pos[i] = state

    return pd.Series(pos, index=apr.index, name="carry")


# --------------------------------------------------------------------------- #
# 5. Pairs / cointegration (BTC vs ETH z-score spread reversion)              #
# --------------------------------------------------------------------------- #
def pairs_coint(
    btc: pd.Series,
    eth: pd.Series,
    window: int = 60,
    entry: float = 2.0,
    exit: float = 0.5,
    stop: float = 4.0,
    max_half_life: float = 60.0,
) -> pd.Series:
    """BTC–ETH z-score spread reversion with a cointegration-breakdown guard
    (RESEARCH.md §2.9 / §5.5).

    The target position is expressed on the **BTC leg** (the ETH leg is the
    ``beta``-scaled hedge against it, traded delta-neutral by convention):

    1. Estimate a rolling hedge ratio ``beta`` by regressing ``log(BTC)`` on
       ``log(ETH)`` over the trailing ``window`` (intercept + slope; trailing
       only → no look-ahead).
    2. ``spread_t = log(BTC_t) - beta_t * log(ETH_t)``.
    3. ``z_t = (spread_t - rolling_mean) / rolling_std`` over ``window``
       (``features.zscore``).
    4. Trade the spread *toward* its mean: enter **short the spread**
       (target ``-1`` on BTC) when ``z > +entry``; enter **long the spread**
       (target ``+1`` on BTC) when ``z < -entry``; flatten when ``|z| < exit``;
       hard **stop** to flat when ``|z| > stop`` (de-cointegration / divergence).

    Cointegration-breakdown guard
        At each bar the spread's OU half-life is estimated over the trailing
        ``window`` (``features.ou_half_life``). If it is non-finite or longer than
        ``max_half_life`` bars, the spread is **not** reliably mean-reverting in
        that window, so the strategy forces **flat** — this is the de-cointegration
        guard the terminal must demonstrate. Combined with the ``stop`` band, it
        keeps the rare large losses (the negative-skew tail) from compounding.

    Edge
        A cointegrated pair has a (locally) stationary spread; fade z-score
        deviations, exit on reversion. BTC–ETH is the canonical robust pair,
        which sidesteps the microcap-shorting and pair-mining traps that sink the
        general case.

    Evidence
        ``[Mixed]``.

    Primary citation
        Tadi & Witzany / copula-cointegration (2024), *Financial Innovation*;
        Leung & Li (2015), *Optimal Mean Reversion Trading*; Krauss (2017),
        *J. Economic Surveys* (documents OOS decay). (RESEARCH.md §2.9.)

    Caveat
        Published Sharpes (~2.45, "12%/month", "100% win rate") are **in-sample
        and fragile**; cointegration breaks in regime shifts. Severe
        pair-selection multiple-testing in the general case (mitigated here by
        fixing BTC–ETH). **Negative skew**: many small wins, rare large losses on
        de-cointegration / depeg / delisting — so the Deflated Sharpe is the
        honest metric. ~7 bps fees + ~20 bps slippage × 2 legs round-trip erodes
        most of it; altcoin shorting is the binding real-world constraint.
        **Inverts**: a spread mean-reverting in-sample can become a trend OOS,
        turning "fade the deviation" into "add to a loser" — hence the half-life
        guard + hard stop.

    Parameters
        btc, eth : aligned price Series (same venue/quote currency, same clock).
        window : rolling window for beta / mean / std / half-life (default 60).
        entry : ``|z|`` threshold to open (default 2.0; brief: 1.5–2.5).
        exit : ``|z|`` threshold to flatten toward the mean (default 0.5).
        stop : ``|z|`` hard-stop threshold to flatten on divergence (default 4.0).
        max_half_life : reject (force flat) if the trailing half-life exceeds this
            many bars or is non-finite (default 60).

    Returns
        ``pd.Series`` of target positions on the **BTC leg** in ``{-1, 0, +1}``
        (NaN during the rolling warm-up).
    """
    btc = pd.Series(btc, dtype="float64")
    eth = pd.Series(eth, dtype="float64")
    common = btc.index.intersection(eth.index)
    log_btc = np.log(btc.reindex(common))
    log_eth = np.log(eth.reindex(common))

    # Rolling OLS hedge ratio beta_t = Cov(log_btc, log_eth) / Var(log_eth) over a
    # trailing window (intercept handled by the covariance form) — trailing only.
    var_eth = log_eth.rolling(window).var(ddof=1)
    cov = log_btc.rolling(window).cov(log_eth)
    beta = cov / var_eth

    spread = log_btc - beta * log_eth
    z = features.zscore(spread, window=window)

    # Trailing half-life guard, evaluated bar-by-bar on the trailing spread window.
    spread_arr = spread.to_numpy()
    hl_ok = np.zeros(len(spread), dtype=bool)
    for i in range(len(spread)):
        if i + 1 < window:
            continue
        win = spread_arr[i - window + 1 : i + 1]
        if np.isnan(win).any():
            continue
        hl = features.ou_half_life(pd.Series(win))
        hl_ok[i] = np.isfinite(hl) and (hl <= max_half_life)
    hl_ok = pd.Series(hl_ok, index=spread.index)

    # Stateful threshold logic with hysteresis (only past/present z) → no look-ahead.
    zv = z.to_numpy()
    pos = np.full(len(z), np.nan, dtype="float64")
    state = 0.0
    for i in range(len(z)):
        zi = zv[i]
        if np.isnan(zi):
            pos[i] = np.nan
            continue
        if not hl_ok.iloc[i]:
            state = 0.0  # cointegration-breakdown guard: stand aside
        else:
            if state == 0.0:
                if zi > entry:
                    state = -1.0  # spread rich → short the spread (short BTC leg)
                elif zi < -entry:
                    state = +1.0  # spread cheap → long the spread (long BTC leg)
            else:
                if abs(zi) < exit or abs(zi) > stop:
                    state = 0.0  # reverted to mean, or hard-stopped on divergence
        pos[i] = state

    return pd.Series(pos, index=z.index, name="pairs_coint")


def pairs_ou(
    btc: pd.Series,
    eth: pd.Series,
    window: int = 60,
    entry: float = 2.0,
    exit: float = 0.5,
    stop: float = 4.0,
    max_half_life: float = 60.0,
) -> pd.Series:
    """RESEARCH variant of :func:`pairs_coint` — OU-model thresholds instead of z.

    Identical to ``pairs_coint`` in **every** respect (hedge ratio ``beta``, the
    half-life stationarity gate, the ``entry``/``exit``/``stop`` multiples, the
    stateful hysteresis) except for **one isolated variable**: the spread
    deviation is normalized by the **OU-fit stationary standard deviation**
    (``features.ou_sigma_eq``, from the same trailing AR(1) fit as the half-life)
    rather than the empirical rolling standard deviation (the z-score). So the
    decision variable is ``u_t = (spread_t - rolling_mean) / sigma_eq_t`` — the
    OU-model counterpart of ``z_t``.

    Pre-registered as a teaching case (RESEARCH-partB-runlog.md, B2): the
    hypothesis is that this parametric normalizer does **not** beat the simple
    empirical z-score out-of-sample, because the fitted OU parameters are
    non-stationary in crypto. Isolating the normalizer (everything else held
    fixed against ``pairs_coint``) makes the comparison clean — if OU loses, the
    model added nothing; it is not an implementation artifact.

    Returns BTC-leg target positions in ``{-1, 0, +1}`` (NaN during warm-up).
    Reference: Leung & Li (2015), *Optimal Mean Reversion Trading*; Krauss (2017).
    """
    btc = pd.Series(btc, dtype="float64")
    eth = pd.Series(eth, dtype="float64")
    common = btc.index.intersection(eth.index)
    log_btc = np.log(btc.reindex(common))
    log_eth = np.log(eth.reindex(common))

    var_eth = log_eth.rolling(window).var(ddof=1)
    cov = log_btc.rolling(window).cov(log_eth)
    beta = cov / var_eth

    spread = log_btc - beta * log_eth
    roll_mean = spread.rolling(window).mean()
    spread_arr = spread.to_numpy()
    mean_arr = roll_mean.to_numpy()

    # Per-bar OU fit on the trailing spread window → (half-life gate, sigma_eq
    # normalizer). Both come from the same AR(1) fit convention as ou_half_life;
    # trailing-only → no look-ahead. u = (spread - mean) / sigma_eq (model std).
    n = len(spread)
    u = np.full(n, np.nan, dtype="float64")
    hl_ok = np.zeros(n, dtype=bool)
    for i in range(n):
        if i + 1 < window:
            continue
        win = spread_arr[i - window + 1 : i + 1]
        if np.isnan(win).any():
            continue
        hl = features.ou_half_life(pd.Series(win))
        sig_eq = features.ou_sigma_eq(pd.Series(win))
        hl_ok[i] = np.isfinite(hl) and (hl <= max_half_life) and np.isfinite(sig_eq) and sig_eq > 0
        if hl_ok[i]:
            u[i] = (spread_arr[i] - mean_arr[i]) / sig_eq

    pos = np.full(n, np.nan, dtype="float64")
    state = 0.0
    for i in range(n):
        ui = u[i]
        if i + 1 < window or np.isnan(spread_arr[i]):
            pos[i] = np.nan
            continue
        if not hl_ok[i] or np.isnan(ui):
            state = 0.0  # de-cointegration / non-stationary guard: stand aside
        elif state == 0.0:
            if ui > entry:
                state = -1.0
            elif ui < -entry:
                state = +1.0
        elif abs(ui) < exit or abs(ui) > stop:
            state = 0.0
        pos[i] = state

    return pd.Series(pos, index=spread.index, name="pairs_ou")


# --------------------------------------------------------------------------- #
# 6. Short volatility — DOCUMENTED STUB (needs Deribit option data)            #
# --------------------------------------------------------------------------- #
def short_vol(*args, **kwargs) -> pd.Series:  # noqa: D401, ANN002, ANN003
    """Variance-risk-premium harvest (delta-hedged short ATM straddle) — **STUB**.

    This function is **deliberately not implemented**. The variance risk premium
    requires data this keyless, OHLCV-only terminal does not have, and faking
    option data would violate the honesty rails ("Never fabricate data").

    Edge (for when real data arrives)
        BTC option-implied variance systematically exceeds subsequent realized
        variance, so a delta-hedged short ATM straddle / short variance swap earns
        a premium most of the time: ``VRP = E[RV] - IV^2`` over the option
        horizon; the seller is paid.

    Evidence
        ``[Established]`` premium — but **tail-lethal**.

    Primary citation
        Alexander & Imeraj (2021), *J. Alternative Investments* 23(4),
        SSRN 3383734; Almeida, Grith, Miftachov & Wang (2024/25),
        arXiv:2410.15195. (RESEARCH.md §2.8.)

    Caveat
        You are **short a fat left tail** — VRP spikes (and short vol loses badly)
        around large moves in *either* direction; daily selling has hit ~45%
        drawdowns. Short backtests systematically understate left-tail losses; the
        payoff is sharply negatively skewed, so a high in-sample Sharpe is exactly
        the trap the Deflated Sharpe is designed to catch. **Size small, never
        naked.**

    Required data to implement (none of which this terminal sources)
        * Deribit option chains + IV surface (option mid + bid/ask).
        * Perp/spot series for delta-hedging the straddle.
        * 5-minute returns to compute realized variance for the VRP.
        Suggested route: Deribit's free historical options API or Tardis.dev
        (free historical via the Deribit partnership) — see RESEARCH.md §4.

    Raises
        NotImplementedError : always, with the guidance above.
    """
    warnings.warn(
        "short_vol is a documented stub: it needs Deribit option data "
        "(IV surface + delta-hedge series + 5-min RV) which this OHLCV-only "
        "terminal does not source. Faking option data would violate the honesty "
        "rails. See RESEARCH.md §2.8 / §4.",
        stacklevel=2,
    )
    raise NotImplementedError(
        "short_vol requires Deribit option data (IV surface, option mid/bid-ask, "
        "spot/perp for delta-hedging, and 5-min returns for realized variance). "
        "This terminal sources only public OHLCV + funding, and fabricating "
        "option data is forbidden by the honesty rails. To implement: ingest "
        "Deribit option chains (free historical API) or Tardis.dev, build the "
        "delta-hedged short ATM straddle, and MANDATE left-tail stress tests with "
        "small sizing (RESEARCH.md §2.8, §5.6)."
    )
