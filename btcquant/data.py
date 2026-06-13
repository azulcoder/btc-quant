"""Market-data fetching and caching for btc-quant.

Research / backtest only. **No API keys, no authenticated endpoints, no orders.**
Every function here hits a *public* market-data endpoint, normalizes the result to
a tidy, UTC-indexed, ascending, de-duplicated :class:`pandas.DataFrame`, and caches
it to ``data/`` so that a later network failure degrades gracefully (load the cache
and warn) instead of crashing a backtest.

Public OHLCV columns are always ``[open, high, low, close, volume]``.

Sources
-------
- ``coinbase``  : ``api.exchange.coinbase.com/products/{symbol}/candles`` (max 300
  candles/request -> paginated). Candles arrive as ``[time, low, high, open, close,
  volume]`` **newest-first** and are normalized here to ascending
  ``[open, high, low, close, volume]``.
- ``kraken``    : ``api.kraken.com/0/public/OHLC``.
- ``coingecko`` : ``api.coingecko.com/.../market_chart`` (daily close only; no OHLC,
  no real volume split -> open/high/low are filled from close, see caveat below).

Granularities: ``1h`` and ``1d``.

Funding (perp-only)
-------------------
:func:`get_funding` reads Bybit's public ``/v5/market/funding/history`` and returns a
single ``funding_rate`` column. Funding is a **perpetual-futures** concept only; it does
not exist for spot. Document that wherever the rate is consumed (e.g. the ``carry``
strategy).

Implied volatility (Deribit DVOL)
---------------------------------
:func:`get_dvol` reads Deribit's public ``get_volatility_index_data`` and returns a
single ``implied_vol`` column (the DVOL index, kept in **percent** — e.g. ``55.0`` ==
55% annualized — see the function docstring). It feeds the variance-risk-premium
diagnostic in :mod:`btcquant.features` (``variance_risk_premium``). No key, no orders.

Option chain (Deribit book summary)
-----------------------------------
:func:`get_option_chain` reads Deribit's public ``get_book_summary_by_currency``
(``kind=option``) in **one** call and returns a tidy per-contract frame with the
expiry, strike, option type, mark IV, open interest, volume, underlying price, and
the mid/bid/ask. **Critical unit trap (brief §1.2):** Deribit's ``mark_iv`` is in
**percent** (``80`` == 80% annualized), so the returned ``iv`` column is stored as a
**decimal** (``mark_iv / 100``). The option-surface helpers in
:mod:`btcquant.features` (``atm_iv``, ``iv_term_structure``, ``iv_skew_25d``,
``smile``) consume this frame. Same degrade-to-cache contract as :func:`get_dvol`.

On-chain (DESCRIPTIVE ONLY)
---------------------------
:func:`get_onchain` reads blockchain.info charts (e.g. ``n-transactions``). These are
flagged **descriptive only**: per RESEARCH.md §2.17 the dominant trap is look-ahead /
revision (a value pulled today for a past date is not what was published then), which is
larger than trading cost. Never wire these into a tradeable signal — risk-zone gauges
only.
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path
from typing import Any, Mapping, Optional, Union

import numpy as np
import pandas as pd
import requests

__all__ = [
    "DATA_DIR",
    "DataError",
    "http_get",
    "get_ohlcv",
    "get_funding",
    "get_dvol",
    "get_option_chain",
    "get_onchain",
]

# Directory for cached CSV/JSON. ``data/`` is gitignored.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Public REST roots (no keys, no auth).
_COINBASE_ROOT = "https://api.exchange.coinbase.com"
_KRAKEN_ROOT = "https://api.kraken.com"
_COINGECKO_ROOT = "https://api.coingecko.com/api/v3"
_BYBIT_ROOT = "https://api.bybit.com"
_DERIBIT_ROOT = "https://www.deribit.com/api/v2"
_BLOCKCHAIN_ROOT = "https://api.blockchain.info"

# A polite, identifiable UA so public endpoints don't 403 a bare client.
_USER_AGENT = "btc-quant/0.1 (research backtest terminal; no-trading)"

# Coinbase granularity (seconds) lookup; coinbase rejects anything else.
_COINBASE_GRAN_SECONDS = {"1h": 3600, "1d": 86400}
# Kraken expects the interval in *minutes*.
_KRAKEN_INTERVAL_MINUTES = {"1h": 60, "1d": 1440}
# Seconds per bar, used for pagination windows and de-dup spacing.
_GRAN_SECONDS = {"1h": 3600, "1d": 86400}

_OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


class DataError(RuntimeError):
    """Raised when a data request cannot be satisfied from network *or* cache."""


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #
def http_get(
    url: str,
    params: Optional[Mapping[str, Any]] = None,
    *,
    timeout: float = 15.0,
    retries: int = 3,
    backoff: float = 0.75,
    session: Optional[requests.Session] = None,
) -> Any:
    """GET ``url`` and return parsed JSON, with timeout + bounded retry.

    Retries on connection errors, timeouts, and 429/5xx responses with
    exponential backoff. A clear :class:`DataError` is raised once all attempts
    are exhausted so callers can fall back to a cache.

    Parameters
    ----------
    url:
        Fully-qualified public endpoint (no auth, no keys).
    params:
        Optional query parameters.
    timeout:
        Per-request timeout in seconds.
    retries:
        Number of attempts (``>= 1``).
    backoff:
        Base seconds for exponential backoff (``backoff * 2**attempt``).
    session:
        Optional :class:`requests.Session` to reuse a connection pool.

    Returns
    -------
    Any
        The decoded JSON body (``dict`` or ``list``).

    Raises
    ------
    DataError
        If every attempt fails.
    """
    if retries < 1:
        retries = 1

    sess = session or requests
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    last_exc: Optional[BaseException] = None

    for attempt in range(retries):
        try:
            resp = sess.get(url, params=params, timeout=timeout, headers=headers)
            # Retry transient throttling / server errors; raise on the rest.
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = DataError(
                    f"HTTP {resp.status_code} from {resp.url}"
                )
                raise last_exc
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, DataError, ValueError) as exc:
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(backoff * (2 ** attempt))

    raise DataError(
        f"GET failed after {retries} attempt(s): {url} "
        f"(params={dict(params) if params else None}) -> {last_exc!r}"
    )


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #
def _cache_path(source: str, symbol: str, granularity: str) -> Path:
    """Return the on-disk cache path for an OHLCV request."""
    safe_symbol = symbol.replace("/", "-")
    return DATA_DIR / f"{source}_{safe_symbol}_{granularity}.csv"


def _write_cache(df: pd.DataFrame, path: Path) -> None:
    """Persist ``df`` (UTC DatetimeIndex) to ``path`` as CSV. Best-effort."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index_label="timestamp")
    except OSError as exc:  # pragma: no cover - disk issues are environmental
        warnings.warn(f"Could not write cache {path}: {exc}", stacklevel=2)


def _read_cache(path: Path) -> pd.DataFrame:
    """Load a cached CSV back into a UTC-indexed DataFrame."""
    df = pd.read_csv(path)
    ts_col = df.columns[0]
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df = df.set_index(ts_col)
    df.index.name = "timestamp"
    return df


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce to numeric ``[open, high, low, close, volume]``, ascending, unique UTC index."""
    df = df.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df.index.name = "timestamp"
    # Keep only the canonical columns, in canonical order.
    for col in _OHLCV_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    df = df[_OHLCV_COLUMNS]
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()
    return df


def _slice_range(
    df: pd.DataFrame,
    start: Optional[pd.Timestamp],
    end: Optional[pd.Timestamp],
) -> pd.DataFrame:
    """Inclusive [start, end] slice on a sorted UTC index (either bound optional)."""
    if start is not None:
        df = df[df.index >= start]
    if end is not None:
        df = df[df.index <= end]
    return df


def _to_utc_ts(value: Union[str, pd.Timestamp, None]) -> Optional[pd.Timestamp]:
    """Parse a user-supplied bound into a tz-aware UTC Timestamp (or None)."""
    if value is None:
        return None
    ts = pd.Timestamp(value)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


# --------------------------------------------------------------------------- #
# Source fetchers (each returns a *normalized* OHLCV frame for [start, end])
# --------------------------------------------------------------------------- #
def _fetch_coinbase(
    symbol: str,
    granularity: str,
    start: Optional[pd.Timestamp],
    end: Optional[pd.Timestamp],
    session: Optional[requests.Session],
) -> pd.DataFrame:
    """Fetch Coinbase Exchange candles (paginated at 300/request).

    Coinbase returns rows ``[time, low, high, open, close, volume]`` newest-first;
    we re-order to ``[open, high, low, close, volume]`` and sort ascending.
    """
    if granularity not in _COINBASE_GRAN_SECONDS:
        raise DataError(
            f"coinbase: unsupported granularity {granularity!r} "
            f"(supported: {sorted(_COINBASE_GRAN_SECONDS)})"
        )
    gran_s = _COINBASE_GRAN_SECONDS[granularity]
    url = f"{_COINBASE_ROOT}/products/{symbol}/candles"

    # Define the [lo, hi] epoch window. Default look-back ~300 bars if no start.
    now = pd.Timestamp.utcnow().tz_localize(None).tz_localize("UTC")
    hi = end if end is not None else now
    if start is not None:
        lo = start
    else:
        lo = hi - pd.Timedelta(seconds=gran_s * 300)

    rows: list[list[float]] = []
    window = pd.Timedelta(seconds=gran_s * 300)
    cursor_end = hi
    # Walk backward 300 bars at a time until we cover [lo, hi].
    while cursor_end > lo:
        cursor_start = max(lo, cursor_end - window)
        params = {
            "granularity": gran_s,
            "start": cursor_start.isoformat(),
            "end": cursor_end.isoformat(),
        }
        payload = http_get(url, params=params, session=session)
        if not isinstance(payload, list):
            raise DataError(f"coinbase: unexpected payload type {type(payload)!r}")
        if not payload:
            break
        rows.extend(payload)
        # Oldest timestamp in this batch becomes the next window's upper bound.
        oldest = min(r[0] for r in payload)
        next_end = pd.Timestamp(oldest, unit="s", tz="UTC") - pd.Timedelta(seconds=gran_s)
        if next_end >= cursor_end:  # no progress -> avoid infinite loop
            break
        cursor_end = next_end
        time.sleep(0.2)  # be gentle with the public endpoint

    if not rows:
        raise DataError(f"coinbase: no candles for {symbol} {granularity}")

    arr = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"])
    arr["timestamp"] = pd.to_datetime(arr["time"], unit="s", utc=True)
    arr = arr.set_index("timestamp")[_OHLCV_COLUMNS]
    return _normalize_ohlcv(arr)


def _fetch_kraken(
    symbol: str,
    granularity: str,
    start: Optional[pd.Timestamp],
    end: Optional[pd.Timestamp],
    session: Optional[requests.Session],
) -> pd.DataFrame:
    """Fetch Kraken public OHLC.

    Kraken returns ``[time, open, high, low, close, vwap, volume, count]`` ascending.
    ``since`` bounds the *start*; Kraken caps the response size, so very long
    ranges are truncated (it serves the most recent ~720 bars).
    """
    if granularity not in _KRAKEN_INTERVAL_MINUTES:
        raise DataError(
            f"kraken: unsupported granularity {granularity!r} "
            f"(supported: {sorted(_KRAKEN_INTERVAL_MINUTES)})"
        )
    pair = symbol.replace("-", "").replace("/", "")
    params: dict[str, Any] = {
        "pair": pair,
        "interval": _KRAKEN_INTERVAL_MINUTES[granularity],
    }
    if start is not None:
        params["since"] = int(start.timestamp())

    payload = http_get(f"{_KRAKEN_ROOT}/0/public/OHLC", params=params, session=session)
    if not isinstance(payload, dict):
        raise DataError(f"kraken: unexpected payload type {type(payload)!r}")
    if payload.get("error"):
        raise DataError(f"kraken error: {payload['error']}")

    result = payload.get("result", {})
    series = [v for k, v in result.items() if k != "last"]
    if not series or not series[0]:
        raise DataError(f"kraken: no OHLC for {pair} {granularity}")

    candles = series[0]
    df = pd.DataFrame(
        candles,
        columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"],
    )
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("timestamp")[_OHLCV_COLUMNS]
    df = _normalize_ohlcv(df)
    return _slice_range(df, start, end)


def _fetch_coingecko(
    symbol: str,
    granularity: str,
    start: Optional[pd.Timestamp],
    end: Optional[pd.Timestamp],
    session: Optional[requests.Session],
) -> pd.DataFrame:
    """Fetch CoinGecko ``market_chart`` (daily close + volume only).

    CoinGecko's free ``market_chart`` returns *price* and *total volume*, not OHLC.
    We map price onto ``close`` and fill ``open/high/low`` from ``close`` (no
    intrabar info is available). Only daily resolution is honest here; ``1h`` would
    require the paid endpoint, so it is rejected.
    """
    if granularity != "1d":
        raise DataError(
            "coingecko: only granularity '1d' is supported on the public endpoint"
        )
    coin = _coingecko_coin_id(symbol)
    vs = _coingecko_vs_currency(symbol)

    # Choose a window. CoinGecko daily granularity needs days > 90 to be auto-daily.
    if start is not None:
        now = pd.Timestamp.utcnow().tz_localize(None).tz_localize("UTC")
        span_days = max(1, int((( end or now) - start).days) + 1)
    else:
        span_days = 365
    days = max(span_days, 91)  # force daily auto-granularity

    params = {"vs_currency": vs, "days": days, "interval": "daily"}
    payload = http_get(
        f"{_COINGECKO_ROOT}/coins/{coin}/market_chart",
        params=params,
        session=session,
    )
    if not isinstance(payload, dict) or "prices" not in payload:
        raise DataError(f"coingecko: unexpected payload for {coin}")

    prices = payload.get("prices") or []
    volumes = {int(t): v for t, v in (payload.get("total_volumes") or [])}
    if not prices:
        raise DataError(f"coingecko: no prices for {coin}")

    df = pd.DataFrame(prices, columns=["ms", "close"])
    df["timestamp"] = pd.to_datetime(df["ms"], unit="ms", utc=True).dt.normalize()
    df["volume"] = df["ms"].astype(int).map(volumes)
    df["open"] = df["close"]
    df["high"] = df["close"]
    df["low"] = df["close"]
    df = df.set_index("timestamp")[_OHLCV_COLUMNS]
    df = _normalize_ohlcv(df)
    return _slice_range(df, start, end)


def _coingecko_coin_id(symbol: str) -> str:
    """Map an exchange-style symbol (e.g. ``BTC-USD``) to a CoinGecko coin id."""
    base = symbol.replace("/", "-").split("-")[0].upper()
    ids = {
        "BTC": "bitcoin",
        "XBT": "bitcoin",
        "ETH": "ethereum",
        "SOL": "solana",
        "LTC": "litecoin",
        "DOGE": "dogecoin",
    }
    return ids.get(base, base.lower())


def _coingecko_vs_currency(symbol: str) -> str:
    """Extract the quote currency for CoinGecko (defaults to usd)."""
    parts = symbol.replace("/", "-").split("-")
    if len(parts) >= 2:
        quote = parts[-1].upper()
        return "usd" if quote in {"USD", "USDT", "USDC"} else quote.lower()
    return "usd"


_FETCHERS = {
    "coinbase": _fetch_coinbase,
    "kraken": _fetch_kraken,
    "coingecko": _fetch_coingecko,
}


# --------------------------------------------------------------------------- #
# Public OHLCV API
# --------------------------------------------------------------------------- #
def get_ohlcv(
    symbol: str = "BTC-USD",
    source: str = "coinbase",
    granularity: str = "1d",
    start: Union[str, pd.Timestamp, None] = None,
    end: Union[str, pd.Timestamp, None] = None,
    cache: bool = True,
) -> pd.DataFrame:
    """Fetch OHLCV candles from a public exchange and return a tidy frame.

    Parameters
    ----------
    symbol:
        Product symbol, exchange-style, e.g. ``"BTC-USD"``.
    source:
        One of ``"coinbase"``, ``"kraken"``, ``"coingecko"``.
    granularity:
        ``"1h"`` or ``"1d"`` (coingecko: ``"1d"`` only).
    start, end:
        Optional inclusive UTC bounds. Strings are parsed (naive -> UTC).
    cache:
        If ``True`` (default), write the freshly fetched frame to
        ``data/{source}_{symbol}_{granularity}.csv`` and, on a **network
        failure**, fall back to that cache (with a warning) instead of raising.

    Returns
    -------
    pandas.DataFrame
        Columns ``[open, high, low, close, volume]``, a tz-aware **UTC
        DatetimeIndex**, ascending and de-duplicated.

    Raises
    ------
    DataError
        If the source is unknown, or the fetch fails and no usable cache exists.

    Notes
    -----
    This never authenticates and never requires an API key. CoinGecko returns
    only daily close + volume, so its ``open/high/low`` are filled from ``close``.
    """
    if source not in _FETCHERS:
        raise DataError(
            f"unknown source {source!r}; choose from {sorted(_FETCHERS)}"
        )
    if granularity not in _GRAN_SECONDS:
        raise DataError(
            f"unsupported granularity {granularity!r}; choose from {sorted(_GRAN_SECONDS)}"
        )

    start_ts = _to_utc_ts(start)
    end_ts = _to_utc_ts(end)
    path = _cache_path(source, symbol, granularity)

    with requests.Session() as session:
        try:
            df = _FETCHERS[source](symbol, granularity, start_ts, end_ts, session)
        except DataError as exc:
            # Network/source failure -> degrade to cache if we have one.
            if cache and path.exists():
                warnings.warn(
                    f"get_ohlcv({symbol!r}, source={source!r}): live fetch failed "
                    f"({exc}); loading cached data from {path}",
                    stacklevel=2,
                )
                cached = _read_cache(path)
                return _slice_range(_normalize_ohlcv(cached), start_ts, end_ts)
            raise DataError(
                f"get_ohlcv({symbol!r}, source={source!r}, granularity={granularity!r}) "
                f"failed and no cache at {path}: {exc}"
            ) from exc

    if cache and not df.empty:
        _write_cache(df, path)
    return df


# --------------------------------------------------------------------------- #
# Funding (perp-only)
# --------------------------------------------------------------------------- #
def get_funding(
    symbol: str = "BTCUSDT",
    source: str = "bybit",
    limit: int = 200,
    cache: bool = True,
) -> pd.DataFrame:
    """Fetch perpetual funding-rate history from a public endpoint.

    Funding exists **only for perpetual futures** (it is the periodic payment that
    tethers the perp to spot); it has no spot analogue. The ``carry`` strategy is
    the canonical consumer.

    Parameters
    ----------
    symbol:
        Perp symbol, e.g. ``"BTCUSDT"`` (Bybit linear perp).
    source:
        Only ``"bybit"`` is implemented (``/v5/market/funding/history``).
    limit:
        Max rows to request (Bybit caps at 200/request).
    cache:
        If ``True`` (default), persist to ``data/{source}_{symbol}_funding.csv``
        and fall back to it on network failure (with a warning).

    Returns
    -------
    pandas.DataFrame
        A single ``funding_rate`` column on an ascending, de-duplicated UTC
        DatetimeIndex. The rate is per funding interval (commonly 8h), as a
        decimal (e.g. ``0.0001`` == 0.01%).

    Raises
    ------
    DataError
        If the source is unknown, or the fetch fails and no usable cache exists.
    """
    if source != "bybit":
        raise DataError(f"unknown funding source {source!r}; only 'bybit' is supported")

    safe_symbol = symbol.replace("/", "-")
    path = DATA_DIR / f"{source}_{safe_symbol}_funding.csv"
    params = {
        "category": "linear",
        "symbol": symbol,
        "limit": int(max(1, min(limit, 200))),
    }

    try:
        payload = http_get(f"{_BYBIT_ROOT}/v5/market/funding/history", params=params)
        if not isinstance(payload, dict):
            raise DataError(f"bybit: unexpected payload type {type(payload)!r}")
        if payload.get("retCode", 0) != 0:
            raise DataError(
                f"bybit funding error retCode={payload.get('retCode')}: "
                f"{payload.get('retMsg')}"
            )
        rows = (payload.get("result") or {}).get("list") or []
        if not rows:
            raise DataError(f"bybit: no funding history for {symbol}")
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(
            df["fundingRateTimestamp"].astype("int64"), unit="ms", utc=True
        )
        df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
        df = df.set_index("timestamp")[["funding_rate"]]
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df.index.name = "timestamp"
    except DataError as exc:
        if cache and path.exists():
            warnings.warn(
                f"get_funding({symbol!r}, source={source!r}): live fetch failed "
                f"({exc}); loading cached data from {path}",
                stacklevel=2,
            )
            cached = pd.read_csv(path)
            ts_col = cached.columns[0]
            cached[ts_col] = pd.to_datetime(cached[ts_col], utc=True)
            cached = cached.set_index(ts_col)
            cached.index.name = "timestamp"
            cached["funding_rate"] = pd.to_numeric(
                cached["funding_rate"], errors="coerce"
            )
            return cached[["funding_rate"]].sort_index()
        raise DataError(
            f"get_funding({symbol!r}, source={source!r}) failed and no cache "
            f"at {path}: {exc}"
        ) from exc

    if cache and not df.empty:
        _write_cache(df, path)
    return df


# --------------------------------------------------------------------------- #
# Single-column cache reader (DVOL / on-chain degrade-to-cache helper)         #
# --------------------------------------------------------------------------- #
def _read_single_col_cache(path: Path, column: str) -> pd.DataFrame:
    """Load a cached one-column series CSV back into a UTC-indexed DataFrame.

    Mirrors :func:`_read_cache` but coerces a single named ``column`` to numeric;
    used by the DVOL and on-chain fetchers when degrading to disk after a network
    failure (same graceful-degrade contract as :func:`get_ohlcv`/:func:`get_funding`).
    """
    cached = pd.read_csv(path)
    ts_col = cached.columns[0]
    cached[ts_col] = pd.to_datetime(cached[ts_col], utc=True)
    cached = cached.set_index(ts_col)
    cached.index.name = "timestamp"
    cached[column] = pd.to_numeric(cached[column], errors="coerce")
    cached = cached[~cached.index.duplicated(keep="last")].sort_index()
    return cached[[column]]


# --------------------------------------------------------------------------- #
# Implied volatility — Deribit DVOL (the option-implied vol index)             #
# --------------------------------------------------------------------------- #
def get_dvol(
    currency: str = "BTC",
    resolution: str = "1D",
    start: Union[str, pd.Timestamp, None] = None,
    end: Union[str, pd.Timestamp, None] = None,
    cache: bool = True,
) -> pd.DataFrame:
    """Fetch Deribit's DVOL implied-volatility index (public, no key).

    Reads ``GET /public/get_volatility_index_data`` (Deribit's option-implied
    volatility index, analogous to the equity VIX). The endpoint returns
    ``result.data`` rows ``[ts_ms, open, high, low, close]``; we keep the **close**
    as ``implied_vol``.

    **Units.** DVOL is published as an **annualized volatility in percent** (e.g. a
    value of ``55.0`` means 55% annualized implied vol). We keep it **in percent**
    (we do *not* divide by 100), so a consumer that needs a decimal vol — e.g.
    :func:`btcquant.features.variance_risk_premium`, which expects implied and
    realized vols in the *same* units — must divide by 100 first (or pass realized
    vol also in percent). This is documented at both ends so the units never drift.

    Parameters
    ----------
    currency:
        Index currency, ``"BTC"`` (default) or ``"ETH"`` — Deribit's
        ``currency`` query parameter.
    resolution:
        Candle resolution for the index. Deribit accepts seconds (e.g. ``"3600"``,
        ``"43200"``) or the daily token ``"1D"`` (default).
    start, end:
        Optional inclusive UTC bounds. Strings are parsed (naive -> UTC). When
        ``start`` is omitted a ~2-year look-back window is requested.
    cache:
        If ``True`` (default), persist to ``data/deribit_{currency}_dvol.csv`` and,
        on a **network failure**, fall back to that cache (with a warning) instead
        of raising — the same degrade-gracefully contract as the other fetchers.

    Returns
    -------
    pandas.DataFrame
        A single ``implied_vol`` column (DVOL close, in **percent**) on an
        ascending, de-duplicated UTC ``DatetimeIndex``.

    Raises
    ------
    DataError
        If the fetch fails and no usable cache exists.

    Notes
    -----
    No authentication, no API key. Confirmed public endpoint:
    ``https://www.deribit.com/api/v2/public/get_volatility_index_data``.
    """
    safe_currency = str(currency).replace("/", "-")
    path = DATA_DIR / f"deribit_{safe_currency}_dvol.csv"

    start_ts = _to_utc_ts(start)
    end_ts = _to_utc_ts(end)
    now = pd.Timestamp.utcnow().tz_localize(None).tz_localize("UTC")
    hi = end_ts if end_ts is not None else now
    lo = start_ts if start_ts is not None else hi - pd.Timedelta(days=730)  # ~2y

    params = {
        "currency": currency,
        "start_timestamp": int(lo.timestamp() * 1000),
        "end_timestamp": int(hi.timestamp() * 1000),
        "resolution": resolution,
    }

    try:
        payload = http_get(
            f"{_DERIBIT_ROOT}/public/get_volatility_index_data", params=params
        )
        if not isinstance(payload, dict):
            raise DataError(f"deribit: unexpected payload type {type(payload)!r}")
        if "error" in payload and payload.get("error"):
            raise DataError(f"deribit DVOL error: {payload['error']}")
        rows = (payload.get("result") or {}).get("data") or []
        if not rows:
            raise DataError(f"deribit: no DVOL data for {currency} {resolution}")
        # Each row is [timestamp_ms, open, high, low, close]; implied_vol = close.
        df = pd.DataFrame(rows, columns=["ms", "open", "high", "low", "close"])
        df["timestamp"] = pd.to_datetime(df["ms"].astype("int64"), unit="ms", utc=True)
        df["implied_vol"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.set_index("timestamp")[["implied_vol"]]
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df.index.name = "timestamp"
    except DataError as exc:
        if cache and path.exists():
            warnings.warn(
                f"get_dvol({currency!r}, resolution={resolution!r}): live fetch "
                f"failed ({exc}); loading cached data from {path}",
                stacklevel=2,
            )
            cached = _read_single_col_cache(path, "implied_vol")
            return _slice_range(cached, start_ts, end_ts)
        raise DataError(
            f"get_dvol({currency!r}, resolution={resolution!r}) failed and no cache "
            f"at {path}: {exc}"
        ) from exc

    if cache and not df.empty:
        _write_cache(df, path)
    return _slice_range(df, start_ts, end_ts)


# --------------------------------------------------------------------------- #
# Option chain — Deribit get_book_summary_by_currency (one public call)        #
# --------------------------------------------------------------------------- #
# Deribit instrument months are the English 3-letter uppercase abbreviations.
_DERIBIT_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Canonical column order for the parsed option chain.
_OPTION_COLUMNS = [
    "instrument_name",
    "expiry",
    "strike",
    "opt_type",
    "iv",
    "mark_iv",
    "open_interest",
    "volume",
    "underlying_price",
    "underlying_index",
    "mid_price",
    "bid_price",
    "ask_price",
    "mark_price",
]


def _parse_option_instrument(name: str) -> Optional[tuple[pd.Timestamp, float, str]]:
    """Parse a Deribit option ``instrument_name`` into ``(expiry, strike, opt_type)``.

    The format is ``BTC-DDMMMYY-STRIKE-C`` / ``...-P`` — e.g.
    ``BTC-27JUN25-100000-C``. Deribit options are **European, cash-settled and
    expire at 08:00 UTC** (brief §1.5), so the returned ``expiry`` is pinned to
    ``08:00:00 UTC`` on the contract date. ``opt_type`` is ``"C"`` or ``"P"``.
    Returns ``None`` for anything that is not a well-formed option name (e.g. a
    perpetual or a future leaking into the payload), so the caller can drop it.
    """
    parts = str(name).split("-")
    if len(parts) != 4:
        return None
    _currency, date_token, strike_token, cp = parts
    cp = cp.upper()
    if cp not in ("C", "P"):
        return None
    # date_token = DDMMMYY, e.g. 27JUN25
    if len(date_token) < 6:
        return None
    mon = date_token[-5:-2].upper()
    month = _DERIBIT_MONTHS.get(mon)
    if month is None:
        return None
    try:
        day = int(date_token[: len(date_token) - 5])
        year = 2000 + int(date_token[-2:])
        strike = float(strike_token)
    except (ValueError, TypeError):
        return None
    try:
        expiry = pd.Timestamp(
            year=year, month=month, day=day, hour=8, minute=0, second=0, tz="UTC"
        )
    except (ValueError, TypeError):
        return None
    return expiry, strike, cp


def get_option_chain(currency: str = "BTC", cache: bool = True) -> pd.DataFrame:
    """Fetch the full Deribit option chain in **one** public call (no key).

    Reads ``GET /public/get_book_summary_by_currency?currency={currency}&kind=option``
    — a single, rate-limit-friendly request that enumerates the entire option
    universe (brief §1.1). The raw ``result`` is a list of per-contract book
    summaries; we parse each ``instrument_name`` (``BTC-DDMMMYY-STRIKE-C/P``) and
    return a tidy frame.

    **Unit trap (brief §1.2).** Deribit's ``mark_iv`` is in **percent**
    (``80`` == 80% annualized). The returned ``iv`` column is the **decimal** form
    (``mark_iv / 100``) so downstream vol formulas use it directly; the original
    percent value is also kept as ``mark_iv`` for reference. Forgetting the /100 is
    the single most common silent 100x bug across the whole surface.

    **Mark-only caveat (brief §1.1, §1.7).** ``get_book_summary_by_currency``
    returns ``mark_iv`` *only* — no per-contract greeks, no ``bid_iv`` / ``ask_iv``.
    Deribit interpolates ``mark_iv`` even for contracts with no live quotes, so a
    smile built purely from this is a **MARK smile, not a tradable bid/ask smile**.
    The greeks / bid-ask-IV detail layer (``ticker``) is intentionally *not* called
    here (it is one request per instrument — rate-limit hostile for the whole
    universe); the feature helpers gate on what this frame provides.

    Parameters
    ----------
    currency:
        Index currency, ``"BTC"`` (default) or ``"ETH"`` — Deribit's ``currency``
        query parameter.
    cache:
        If ``True`` (default), persist the parsed frame to
        ``data/deribit_{currency}_option_chain.csv`` and, on a **network
        failure**, fall back to that cache (with a warning) instead of raising —
        the same degrade-gracefully contract as :func:`get_dvol`. The chain is a
        live snapshot; the cache is a *stale* fallback, never fabricated data.

    Returns
    -------
    pandas.DataFrame
        One row per option contract with columns
        ``[instrument_name, expiry, strike, opt_type, iv, mark_iv, open_interest,
        volume, underlying_price, underlying_index, mid_price, bid_price,
        ask_price, mark_price]``. ``expiry`` is a tz-aware UTC ``Timestamp`` pinned
        to 08:00 UTC; ``iv`` is a **decimal** annualized vol; ``opt_type`` is
        ``"C"``/``"P"``. The frame uses a default integer index (it is a
        cross-sectional snapshot, not a time series).

    Raises
    ------
    DataError
        If the fetch fails and no usable cache exists.

    Notes
    -----
    No authentication, no API key. Confirmed public endpoint:
    ``https://www.deribit.com/api/v2/public/get_book_summary_by_currency``.
    """
    safe_currency = str(currency).replace("/", "-")
    path = DATA_DIR / f"deribit_{safe_currency}_option_chain.csv"
    params = {"currency": currency, "kind": "option"}

    try:
        payload = http_get(
            f"{_DERIBIT_ROOT}/public/get_book_summary_by_currency", params=params
        )
        if not isinstance(payload, dict):
            raise DataError(f"deribit: unexpected payload type {type(payload)!r}")
        if payload.get("error"):
            raise DataError(f"deribit option-chain error: {payload['error']}")
        rows = payload.get("result") or []
        if not rows:
            raise DataError(f"deribit: no option book summary for {currency}")
        df = _parse_option_chain_rows(rows)
        if df.empty:
            raise DataError(
                f"deribit: option book summary for {currency} parsed to 0 contracts"
            )
    except DataError as exc:
        if cache and path.exists():
            warnings.warn(
                f"get_option_chain({currency!r}): live fetch failed ({exc}); "
                f"loading cached chain from {path} (STALE snapshot)",
                stacklevel=2,
            )
            return _read_option_chain_cache(path)
        raise DataError(
            f"get_option_chain({currency!r}) failed and no cache at {path}: {exc}"
        ) from exc

    if cache and not df.empty:
        _write_cache(df.reset_index(drop=True), path)
    return df


def _parse_option_chain_rows(rows: list) -> pd.DataFrame:
    """Turn raw ``get_book_summary_by_currency`` rows into the tidy option frame.

    Drops any instrument whose name is not a well-formed option (``None`` from
    :func:`_parse_option_instrument`). Applies the **/100 IV unit fix** (brief
    §1.2) and coerces all numeric columns. Pure (no network); shared by the live
    path and the cache path's re-validation.
    """
    records: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, Mapping):
            continue
        name = r.get("instrument_name")
        parsed = _parse_option_instrument(name) if name is not None else None
        if parsed is None:
            continue
        expiry, strike, cp = parsed
        mark_iv = r.get("mark_iv")
        mark_iv = float(mark_iv) if mark_iv is not None else np.nan
        # CRITICAL (brief §1.2): mark_iv is in PERCENT -> store iv as a decimal.
        iv = mark_iv / 100.0 if not np.isnan(mark_iv) else np.nan
        records.append(
            {
                "instrument_name": name,
                "expiry": expiry,
                "strike": strike,
                "opt_type": cp,
                "iv": iv,
                "mark_iv": mark_iv,
                "open_interest": r.get("open_interest"),
                "volume": r.get("volume"),
                "underlying_price": r.get("underlying_price"),
                "underlying_index": r.get("underlying_index"),
                "mid_price": r.get("mid_price"),
                "bid_price": r.get("bid_price"),
                "ask_price": r.get("ask_price"),
                "mark_price": r.get("mark_price"),
            }
        )

    if not records:
        return pd.DataFrame(columns=_OPTION_COLUMNS)

    df = pd.DataFrame.from_records(records)
    # Coerce numerics (bid/ask can legitimately be null -> NaN, used by the gate).
    numeric_cols = [
        "strike", "iv", "mark_iv", "open_interest", "volume",
        "underlying_price", "mid_price", "bid_price", "ask_price", "mark_price",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["expiry"] = pd.to_datetime(df["expiry"], utc=True)
    df = df[_OPTION_COLUMNS]
    df = df.sort_values(["expiry", "strike", "opt_type"]).reset_index(drop=True)
    return df


def _read_option_chain_cache(path: Path) -> pd.DataFrame:
    """Load a cached option-chain CSV back into the tidy snapshot frame.

    Mirrors the live path's typing: parses ``expiry`` as UTC and coerces the
    numeric columns. Used only when degrading to disk after a network failure
    (a STALE snapshot, clearly warned by the caller — never fabricated).
    """
    cached = pd.read_csv(path)
    # Drop a stray index column if one was written.
    if cached.columns[0] in ("timestamp", "Unnamed: 0"):
        cached = cached.drop(columns=[cached.columns[0]])
    for col in _OPTION_COLUMNS:
        if col not in cached.columns:
            cached[col] = np.nan
    numeric_cols = [
        "strike", "iv", "mark_iv", "open_interest", "volume",
        "underlying_price", "mid_price", "bid_price", "ask_price", "mark_price",
    ]
    for col in numeric_cols:
        cached[col] = pd.to_numeric(cached[col], errors="coerce")
    cached["expiry"] = pd.to_datetime(cached["expiry"], utc=True)
    cached["opt_type"] = cached["opt_type"].astype(str).str.upper()
    cached = cached[_OPTION_COLUMNS]
    return cached.sort_values(["expiry", "strike", "opt_type"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# On-chain charts — blockchain.info (DESCRIPTIVE ONLY, look-ahead trap)        #
# --------------------------------------------------------------------------- #
def get_onchain(
    metric: str = "n-transactions",
    timespan: str = "2years",
    cache: bool = True,
) -> pd.DataFrame:
    """Fetch a blockchain.info chart series (public, no key) — **DESCRIPTIVE ONLY**.

    Reads ``GET /charts/{metric}?timespan=...&format=json&cors=true`` and returns a
    single column named after ``metric`` (non-alphanumerics mapped to ``_``). Each
    chart point is ``{"x": unix_seconds, "y": value}`` — note the timestamps are in
    **seconds**, unlike the millisecond endpoints elsewhere in this module.

    **Descriptive only — do NOT build a tradeable signal on this** (RESEARCH.md
    §2.17). On-chain series carry a **look-ahead / revision trap that is larger than
    trading cost**: entity-clustering and realized-value methodologies are
    retroactively revised, so a value pulled today for a past date is *not* what was
    published then. A backtest on revised on-chain data looks profitable and
    degrades to noise on true point-in-time (PIT) data. Use these series as
    *descriptive risk-zone gauges* on the dashboard, never as out-of-sample signals.

    Parameters
    ----------
    metric:
        A blockchain.info chart slug, e.g. ``"n-transactions"`` (default),
        ``"market-price"``, ``"hash-rate"``, ``"miners-revenue"``.
    timespan:
        Chart timespan token, e.g. ``"30days"``, ``"1year"``, ``"2years"``
        (default), ``"all"``.
    cache:
        If ``True`` (default), persist to ``data/blockchain_{metric}.csv`` and fall
        back to it on network failure (with a warning) — same degrade contract as
        the other fetchers.

    Returns
    -------
    pandas.DataFrame
        A single column (named from ``metric``) on an ascending, de-duplicated UTC
        ``DatetimeIndex``.

    Raises
    ------
    DataError
        If the fetch fails and no usable cache exists.

    Notes
    -----
    No authentication, no API key. The ``cors=true`` flag is sent so the same
    endpoint also works from the static dashboard client-side.
    """
    column = "".join(ch if ch.isalnum() else "_" for ch in str(metric))
    safe_metric = str(metric).replace("/", "-")
    path = DATA_DIR / f"blockchain_{safe_metric}.csv"
    params = {"timespan": timespan, "format": "json", "cors": "true"}

    try:
        payload = http_get(
            f"{_BLOCKCHAIN_ROOT}/charts/{metric}", params=params
        )
        if not isinstance(payload, dict):
            raise DataError(f"blockchain.info: unexpected payload type {type(payload)!r}")
        values = payload.get("values") or []
        if not values:
            raise DataError(f"blockchain.info: no values for chart {metric!r}")
        # Points are {"x": unix_SECONDS, "y": value}.
        df = pd.DataFrame(values)
        if "x" not in df.columns or "y" not in df.columns:
            raise DataError(f"blockchain.info: malformed chart payload for {metric!r}")
        df["timestamp"] = pd.to_datetime(df["x"].astype("int64"), unit="s", utc=True)
        df[column] = pd.to_numeric(df["y"], errors="coerce")
        df = df.set_index("timestamp")[[column]]
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df.index.name = "timestamp"
    except DataError as exc:
        if cache and path.exists():
            warnings.warn(
                f"get_onchain({metric!r}, timespan={timespan!r}): live fetch failed "
                f"({exc}); loading cached data from {path}",
                stacklevel=2,
            )
            return _read_single_col_cache(path, column)
        raise DataError(
            f"get_onchain({metric!r}, timespan={timespan!r}) failed and no cache "
            f"at {path}: {exc}"
        ) from exc

    if cache and not df.empty:
        _write_cache(df, path)
    return df
