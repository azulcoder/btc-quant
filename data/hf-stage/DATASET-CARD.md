---
pretty_name: btc-quant tick store (BTC perp microstructure, keyless public feeds)
tags:
- bitcoin
- market-data
- order-flow
- time-series
configs:
- config_name: trades
  data_files: data/date=*/trades.parquet
- config_name: liquidations
  data_files: data/date=*/liquidations.parquet
- config_name: depth_snapshots
  data_files: data/date=*/depth_snapshots.parquet
- config_name: funding_mark
  data_files: data/date=*/funding_mark.parquet
- config_name: open_interest
  data_files: data/date=*/open_interest.parquet
- config_name: crowding
  data_files: data/date=*/crowding.parquet
- config_name: dvol
  data_files: data/date=*/dvol.parquet
- config_name: options_chain
  data_files: data/date=*/options_chain.parquet
---

# btc-quant ticks — keyless BTC perp microstructure, one partition per UTC day

Raw microstructure recorded by
[btc-quant](https://github.com/azulcoder/btc-quant)'s `btcquant/collector.py`
(DESIGN-orderflow-terminal.md §3c). **Keyless public WS/REST feeds only** —
no accounts, no signed endpoints, no orders. Partitions are **event-time UTC
days** (every row lands in the day of its own `ts_ms`, not its arrival time)
and are **immutable once uploaded**; each ships with
`manifests/MANIFEST-<date>.json` (per-table rows, ts extent, bytes, sha256).

## Layout

```
data/date=YYYY-MM-DD/<table>.parquet   # hive-style, ZSTD
manifests/MANIFEST-YYYY-MM-DD.json     # provenance + checksums per day
```

## Query it in place

```sql
-- duckdb (httpfs): one day
SELECT count(*) FROM read_parquet('hf://datasets/azulcoder/btc-quant-ticks/data/date=2026-07-04/trades.parquet');
-- every day, hive partitioning
SELECT * FROM read_parquet('hf://datasets/azulcoder/btc-quant-ticks/data/date=*/trades.parquet', hive_partitioning=1);
```

```python
from datasets import load_dataset
ds = load_dataset("azulcoder/btc-quant-ticks", "trades", streaming=True)  # one config per table
```

## Schema (all timestamps epoch **ms**, UTC; exchange codes binancef|bybit|coinbase|okx)

| table | columns | notes |
|---|---|---|
| trades | exchange, symbol, trade_id, ts_ms, price, qty, aggressor_buy | aggressor conventions normalized per venue (Coinbase `side` is the MAKER — inverted; Binance `m` true = SELL aggressor; Bybit/OKX taker side as-is); OKX qty = sz x ctVal (coin) |
| liquidations | exchange, symbol, ts_ms, side, price, qty, notional_usd | side = the LIQUIDATED position (`long`/`short`), not the printed order |
| depth_snapshots | exchange, symbol, ts_ms, bids, asks | JSON `[[price,qty]...]` best-first; top-50 (bybit/okx), top-20 (binancef — that is the whole wire); 1/s downsample |
| funding_mark | exchange, symbol, ts_ms, mark, index, funding_rate, next_funding_ts | funding_rate is the raw per-interval decimal, never annualized in storage |
| open_interest | exchange, symbol, ts_ms, oi | contracts/coin as delivered — no silent USD conversion |
| crowding | exchange, symbol, ts_ms, metric, value | binancef futures/data @5m, long format (taker_buy_sell_ratio, top_position_ls_ratio, global_account_ls_ratio, oi_sum_coin, oi_sum_usd) |
| dvol | ts_ms, index_price | Deribit DVOL, 60 s |
| options_chain | ts_ms, name, expiry_ts, strike, cp, iv, oi, volume, mark_price, underlying | Deribit book summary, hourly; iv stored as a DECIMAL (already /100) |

Days migrated from the pre-rotation store may carry only the first five tables.

## Honesty notes (binding rails, DESIGN §0)

- **Gaps stay gaps.** Collector downtime is a real hole in `ts_ms` — never
  interpolated, backfilled, or blended across sources.
- **Event-time partitions.** A 5-minute grace window at UTC midnight lets
  late/out-of-order rows land in the correct day before it closes.
- **Binance futures trades arrive via the REST `aggTrades` poll** (5 s cursor,
  gapless by aggTradeId) — the WS trade topic is filtered on the recording
  network; we record what the wire actually delivers.
- This is **descriptive research data**, not a signal, a backtest input, or
  investment advice.

## Update cadence

Daily, ~00:20 UTC (`make hf-sync` via launchd): every closed local day is
exported, verified, uploaded, verified again on the Hub, and only then removed
from the recording machine.

## License / warranty

Public market data recorded from keyless public exchange endpoints; redistributed
as-is, **no warranty of any kind**, use at your own risk. Exchange terms may
apply to commercial redistribution — check them before building on this.
