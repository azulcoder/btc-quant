# DIAG-venue-filter-audit — apakah deret yang masuk estimator terfilter ke satu venue

**Look: diagnostik provenance**, sama klasifikasinya dengan `DIAG-provenance-001` — integritas
tape, tidak ada return atau P&L yang disentuh. Kolom diagnostic.
**Cakupan:** audit kutipan kode + pengukuran item 5. Tidak ada perbaikan, tidak ada kesimpulan
baru, tidak ada commit selain berkas ini dan kenaikan look counter.
**Semua angka `[DIUKUR]`.**

---

## 1. `scripts/prereg_microstructure_001.py`

**Ada.** Tepat satu klausa `WHERE` di seluruh berkas, di `day_moments()`:

```sql
WHERE exchange = 'binancef' AND symbol = 'BTCUSDT'
```

Itu satu-satunya baris yang memuat kata `exchange` atau `symbol` di berkas itu.

## 2. Kueri buku (0,0078 bps) dan kueri "63–753 tick"

**`depth_snapshots` punya kolom `exchange`** — skema terukur:
`exchange VARCHAR · symbol VARCHAR · ts_ms BIGINT · bids VARCHAR · asks VARCHAR · date DATE`.

**Kueri yang menghasilkan 0,0156/0,0157 bps: klausa `WHERE`-nya TIDAK ADA di repo.** Ia kueri
ad-hoc yang hasilnya dicatat di `docs/EDA-microstructure-001.md` §2a; tidak ada satu pun skrip di
`scripts/` atau `btcquant/` yang menghitungnya, dan tidak ada blok SQL yang mengutipnya. Yang bisa
kukutip hanyalah caption dan tabelnya:

> *Source: `data/ticks/2026-08-03.duckdb` (read-only), `depth_snapshots`, best bid/ask parsed with
> `json_extract(bids,'$[0][0]')`; 151,715 snapshots across three venues.*

| venue | n | p10 | p50 | p90 | p99 |
|---|---:|---:|---:|---:|---:|
| bybit | 46,853 | 0.0157 | **0.0157** | 0.0160 | 0.0160 |
| binancef | 50,573 | 0.0157 | **0.0157** | 0.0160 | 0.0160 |
| okx | 54,289 | 0.0157 | **0.0157** | 0.0160 | 0.0160 |

Tabelnya **dipecah per venue**, jadi `binancef` terisolasi pada tingkat pelaporan. Tapi klausa
`WHERE`-nya sendiri tidak bisa kukutip karena tidak tersimpan di mana pun — hanya niat penulisnya
yang tercatat, dan kau melarangku menyimpulkan dari itu. **Statusnya: tidak dapat diverifikasi
dari repo.**

**Kueri "buku melebar 63–753 tick melawan median 1,01 tick": tidak ada di repo.** Ia dihasilkan
agen refutasi di `/private/tmp`, dan berkas skripnya sudah tidak ada. Yang tersisa hanyalah
transkrip agennya. **Statusnya: tidak dapat dikutip.**

## 3. `scripts/diag_provenance_001.py` — per blok

**Blok A memakai dua kueri berbeda, dan A1 sengaja TIDAK memfilter:**

```sql
-- A1, baris 56-59
SELECT exchange, symbol, count(*) n FROM read_parquet('{g}', hive_partitioning=1)
WHERE date BETWEEN DATE '{SLICE_LO}' AND DATE '{SLICE_HI}'
GROUP BY 1,2 ORDER BY n DESC
```

```sql
-- A2, baris 67-71
WHERE date BETWEEN DATE '{SLICE_LO}' AND DATE '{SLICE_HI}'
  AND exchange='binancef' AND symbol='BTCUSDT'
```

```sql
-- A3/A4, baris 92
WHERE exchange='binancef' AND symbol='BTCUSDT'
  AND (ts_ms // 3600000) % 24 BETWEEN 12 AND 23
```

**Blok B, C dan D memakai SATU loader bersama**, `load()` baris 120–131 — tidak ada loader
kedua. Pemanggilnya: B baris 149, C baris 174, D baris 203.

```python
def load(con, url, order_sql, dedup=True, hours=HOURS):
    dd = "DISTINCT ON (CAST(trade_id AS BIGINT))" if dedup else ""
    ob_dd = "ORDER BY CAST(trade_id AS BIGINT)" if dedup else ""
    inner = f"""
        SELECT {dd} CAST(trade_id AS BIGINT) tid, ts_ms, price
        FROM read_parquet('{url}')
        WHERE exchange='binancef' AND symbol='BTCUSDT'
          AND (ts_ms // 3600000) % 24 BETWEEN {hours[0]} AND {hours[1]}
        {ob_dd}
    """
```

Jadi B, C, D **terfilter**, dan `ob_dd` di dalam `inner` adalah bug loader yang sudah tercatat
(varian "no ORDER BY" tetap ter-sort).

## 4. Skrip agen refutasi

**Berkasnya tidak ada lagi** — `find` atas seluruh `/private/tmp/claude-501/-Users-azul/` untuk
`*.py` yang lebih baru dari 2026-08-07 mengembalikan nol berkas yang membaca `trades.parquet`.
Yang bisa kukutip hanyalah transkripnya. Hitungan klausa di ketiga transkrip agen:

| klausa, apa adanya dari transkrip | n |
|---|---|
| `WHERE exchange='binancef' AND symbol='BTCUSDT'` | 19 |
| `WHERE exchange='binancef' AND symbol='BTCUSDT'),` | 2 |
| `WHERE exchange='binancef' AND symbol='BTCUSDT') TO '{dst}' (FORMAT PARQUET)` | 1 |
| `WHERE exchange='binancef' AND symbol='BTCUSDT') TO 'rec_{d}.parquet' (FORMAT PARQUET)` | 1 |
| `WHERE exchange = 'binancef' AND symbol = 'BTCUSDT'` | 1 |
| `WHERE exchange='binancef' LIMIT 5` | 1 |
| `WHERE exchange=` (terpotong di transkrip) | 1 |

**Terfilter**, pada setiap klausa yang bisa dibaca utuh. Satu klausa terpotong dan tidak bisa
dipastikan. Ini bukti transkrip, bukan bukti berkas.

---

## 5. PENGUKURAN

### 5a. Venue kedua baris pada 434 pasangan teratas, `2026-07-30`

| baris `j` | baris `j+2` | n |
|---|---|---|
| `binancef`/`BTCUSDT` | `binancef`/`BTCUSDT` | **434** |

434 dari 434. Sumbangan mereka ke `Σ(dp_t·dp_{t+1})`: **97,4 %**.

**Kontrafaktual tanpa filter: TIDAK BISA DIJALANKAN, dan gagalnya informatif.**
`CAST(trade_id AS BIGINT)` **melempar error** pada baca tanpa filter:

```
_duckdb.ConversionException: Conversion Error: Could not convert string
'9be9dc73-ff69-57ab-8ab1-75270b8ad415' to INT64 when casting from source column trade_id
```

`bybit` memakai UUID sebagai `trade_id`. Jadi setiap kueri yang meng-cast `trade_id` ke `BIGINT`
**wajib** terfilter ke venue ber-id numerik, atau ia berhenti dengan error. Ia tidak diam-diam
mencampur.

### 5b. Satu run 38-alternasi, baris per baris

Run terpanjang yang disentuh 434 produk teratas: **38 leg**, baris harga `13182..13220`
(39 trade).

| trade_id | ts_ms | exchange | symbol | price | side | Δtick |
|---|---|---|---|---|---|---|
| 3397850411 | 1785413659551 | binancef | BTCUSDT | 64990.8 | SELL | |
| 3397850412 | 1785413659551 | binancef | BTCUSDT | 64999.9 | BUY | +91 |
| 3397850413 | 1785413659552 | binancef | BTCUSDT | 64990.8 | SELL | −91 |
| 3397850414 | 1785413659552 | binancef | BTCUSDT | 64999.9 | BUY | +91 |
| 3397850415 | 1785413659552 | binancef | BTCUSDT | 64990.8 | SELL | −91 |
| … | … | … | … | … | … | … |
| 3397850448 | 1785413659552 | binancef | BTCUSDT | 64999.9 | BUY | +91 |
| 3397850449 | 1785413659552 | binancef | BTCUSDT | 64990.8 | SELL | −91 |

Pola `64990.8 SELL` ↔ `64999.9 BUY` berulang persis, 38 kali, tanpa satu pun harga lain.

- **distinct `ts_ms` dalam run ini: 2** (`…551` dan `…552`), rentang **1 ms**
- **distinct `(exchange, symbol)`: `{('binancef','BTCUSDT')}`** — satu
- **`trade_id` kontigu: True** (3397850411 … 3397850449, tanpa celah)

### 5c. `γ₀` dan `ρ₁..ρ₄` PER `(exchange, symbol)`, tanpa pooling

Diurutkan `ts_ms` per venue (bukan `trade_id`, karena bybit ber-UUID). Baris `ALL MIXED` adalah
kontrafaktual: seluruh venue digabung dan diurutkan `ts_ms` saja.

| date | exchange | symbol | n | `γ₀` | `ρ₁` | `ρ₂` | `ρ₃` | `ρ₄` |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 2026-07-30 | binancef | BTCUSDT | 434,106 | 8.2110e−11 | −0.466 | **+0.337** | −0.197 | +0.165 |
| 2026-07-30 | bybit | BTCUSDT | 726,559 | 1.1662e−09 | −0.482 | **−0.003** | +0.002 | −0.001 |
| 2026-07-30 | coinbase | BTC-USD | 235,504 | 2.2733e−10 | −0.027 | +0.033 | +0.029 | +0.036 |
| 2026-07-30 | okx | BTC-USDT-SWAP | 424,754 | 5.1170e−11 | −0.252 | +0.107 | +0.054 | +0.081 |
| 2026-07-30 | **ALL MIXED** | (counterfactual) | 1,820,926 | 3.7013e−08 | −0.373 | **−0.042** | −0.020 | −0.011 |
| 2026-07-31 | binancef | BTCUSDT | 634,741 | 7.2181e−10 | −0.752 | **+0.502** | −0.363 | +0.280 |
| 2026-07-31 | bybit | BTCUSDT | 373,966 | 1.1099e−09 | −0.464 | **−0.003** | −0.009 | +0.008 |
| 2026-07-31 | coinbase | BTC-USD | 207,949 | 1.4153e−09 | +0.003 | +0.002 | +0.005 | −0.001 |
| 2026-07-31 | okx | BTC-USDT-SWAP | 231,239 | 1.1304e−09 | +0.004 | +0.004 | +0.004 | +0.004 |
| 2026-07-31 | **ALL MIXED** | (counterfactual) | 1,447,898 | 4.9746e−08 | −0.400 | **−0.030** | −0.020 | −0.006 |
| 2026-08-01 | binancef | BTCUSDT | 260,645 | 6.0117e−10 | −0.704 | **+0.422** | −0.276 | +0.198 |
| 2026-08-01 | coinbase | BTC-USD | 199,815 | 1.2351e−10 | +0.031 | +0.021 | +0.039 | +0.041 |
| 2026-08-01 | **ALL MIXED** | (counterfactual) | 460,461 | 6.4648e−08 | −0.459 | **−0.012** | −0.001 | −0.007 |

`okx` dan `bybit` tidak muncul pada `2026-08-01` karena `n < 5.000` di jendela jam itu.
`γ₀` pada baris `ALL MIXED` adalah **~450×** nilai `binancef`-nya.
