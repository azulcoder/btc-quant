# ⚠️ EXPIRED — Baseline before `disablesleep`, 2026-08-04

> **STATUS: EXPIRED 2026-08-05. Superseded, NOT deleted.**
>
> The `disablesleep` experiment was **officially deferred** before it ever started —
> `SleepDisabled` was never set and 62 host sleep events were logged on 08-04. These numbers
> were taken at `2026-08-04 12:41 UTC` and are now more than 24 h old. **Any future run of this
> experiment needs a baseline taken adjacent to it**, because host behaviour, market regime and
> collector state all drift; comparing a T+6 window against a day-old baseline would measure
> drift as if it were treatment effect.
>
> **The real reason for the deferral** is not scheduling. §5 of `docs/EDA-microstructure-001.md`
> established that the primary research path — **daily swing — already clears MinBTL(8) at
> 302 %** on 8.6 years of daily OHLCV, and needs **no order-book data at all**. Book coverage
> therefore serves only the market-making track, which has not been started and is not
> pre-registered. Fixing coverage was urgent when the book was believed to be on the critical
> path. It is not on the critical path.
>
> **`prediction.md` in this directory is NOT expired.** Its thresholds (P1–P5, R1, R2, and the
> named ambiguous band) remain binding for whenever the experiment is run. Nothing in it was
> revised, and it must not be — it was written before any result existed, which is the only
> thing that makes it worth anything.
>
> Kept verbatim below as the record of what the store looked like on 2026-08-04.

---

# Baseline — before `disablesleep`, 2026-08-04

Taken at **2026-08-04 12:41:06 UTC**, immediately before any change. This is the "before"
half of a one-shot experiment: once host sleep is disabled it cannot be retaken.

Coverage comes from **stored rows**, never from `gaps.jsonl` and never from the collector
process. `gaps.jsonl` measures time-since-last-row, not time-since-socket-drop, and a process
cannot measure its own absence. Rows on disk are the only witness that survives the host being
asleep.

**Window:** `2026-08-03 12:41` → `2026-08-04 12:41 UTC` (86,400 s).
**Sources:** `data/ticks/2026-08-03.duckdb` (read-only) + a byte copy of
`data/ticks/2026-08-04.duckdb` (the live file is write-locked by PID 14595; the original was
never opened, only copied). Copy carries `.wal`, so it may trail the live file by seconds —
that direction of error makes 08-04 coverage a slight UNDER-count, never an over-count.

---

## Coverage — unique seconds with ≥1 row, per venue [DIUKUR]

### `depth_snapshots` (1/s storage cadence, `collector.py:246 DOWNSAMPLE_MS = 1000`)

| venue | seconds | of 86,400 |
|---|---:|---:|
| okx | 50,091 | **57.98 %** |
| binancef | 47,593 | **55.08 %** |
| bybit | 42,098 | **48.72 %** |
| coinbase | 0 | — (trades-only leg by design, `collector.py:3732`) |

### `trades`

| venue | seconds | of 86,400 |
|---|---:|---:|
| binancef | 79,696 | **92.24 %** |
| coinbase | 44,908 | 51.98 % |
| okx | 42,378 | 49.05 % |
| bybit | 31,508 | 36.47 % |

**The 92 % vs 55 % split inside one venue is the most informative number here**
[DISIMPULKAN]: binancef trades arrive by REST `aggTrades` chained on `fromId`, so a poll that
misses its slot fetches the backlog on the next one — the venue serves trade *history*. Depth
has no historical endpoint, so a second the host slept is a second of book that no longer
exists anywhere. **Host downtime costs book data specifically**, which is precisely the scarce
resource (book-derived families sit at 1.8 % of MinBTL(5), `STRATEGY.md:115-127`).

---

## The ceiling — what "healthy" actually looks like [DIUKUR]

Measured per clock-hour over the 37 readable hours, so the target is not assumed:

| venue | best hour | median hour | hours ≥90 % | hours <10 % |
|---|---:|---:|---:|---:|
| okx | **3,600 / 3,600 = 100.0 %** | 23.9 % | 14 | 16 |
| binancef | 3,514 / 3,600 = 97.6 % | 9.8 % | 14 | 19 |
| bybit | 3,275 / 3,600 = 91.0 % | 23.5 % | 2 | 15 |

Two things follow. **The ceiling is ~100 %**, so the ~55 % daily figure is real loss and not a
storage artefact. And the hour distribution is **bimodal** — hours are either above 90 % or
below 10 %, with little in between. That is the shape of *on or off*, not of gradual
degradation.

---

## Churn [DIUKUR] — source `data/ticks/gaps.jsonl`

- **366 gap events → 46 bursts** (events within 30 s of each other collapsed to one burst)
- **37 of 46 bursts hit ≥6 legs simultaneously**
- inter-burst spacing: **median 16.1 min** (min 0.7, max 291.6)
- bursts per UTC hour:

```
08-03: 15h=1  20h=1
08-04: 00h=3  01h=4  02h=5  03h=4  04h=4  05h=3  06h=5  07h=4  08h=4  09h=4  10h=4
```

Dense and near-constant from `08-04 00h` onward (≈3.6 bursts/hour over 11 hours); almost
nothing in the preceding evening. **Why the regime changed at that boundary is NOT measured**
— host idleness is the obvious candidate but I did not establish it.

## Host power [DIUKUR] — source `pmset -g log`

- **114 `Entering Sleep state` events** logged across 08-03/08-04
- last 24 h: **62 Sleep, 124 Wake/DarkWake**
- reason string on every one: `'Maintenance Sleep':TCPKeepAlive`
- **44 of 46 gap bursts (96 %) fall within 3 minutes of a Wake**; sampled pairs align to
  **1–8 seconds**

---

## What is NOT measured here

- **Downtime per disconnect.** `/tmp/btcquant-collector.log` has **0 of 2,123 lines** carrying
  a date prefix, so socket-drop → reconnect duration cannot be recovered. `gaps.jsonl`
  `from_ms`/`to_ms` measures since-last-row instead; the difference between the two is unknown
  and is not estimated.
- **The 2 bursts of 46 with no Wake within ±3 min.** Not investigated.
- **Whether host sleep and home-network failure can be told apart.** They are not separable in
  this data — both cut every leg at once. That is what the experiment is for.
- **Any 2026-08-04 row written after the copy was taken.**
