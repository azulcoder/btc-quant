# DIAG-funding-and-turnover-001 — funding diverifikasi, sensus turnover, tiga temuan ditutup

**Look: diagnostik provenance.** Tidak ada return, P&L, Sharpe, atau equity curve yang dihitung,
dibaca, atau ditampilkan. `backtest.run`, `walk_forward` dan `cpcv` tidak pernah dipanggil.
**Skrip:** `scripts/diag_funding_settled_001.py`, `scripts/diag_turnover_census_001.py`
**Mesin:** `reports/funding-settled-001.json`, `reports/turnover-census-001.json`
**Semua angka `[DIUKUR]`** kecuali yang ditandai lain. Dijalankan 2026-08-08.

---

# 1. Funding — mekanismenya benar dicurigai, konsekuensinya tidak

## 1a. Rate-nya PREDIKTIF, dicuplik terus-menerus. Terkutip.

Skema (`btcquant/collector.py`, grep `CREATE TABLE IF NOT EXISTS funding_mark`):

```sql
CREATE TABLE IF NOT EXISTS funding_mark (
    exchange VARCHAR, symbol VARCHAR, ts_ms BIGINT, mark DOUBLE,
    "index" DOUBLE, funding_rate DOUBLE, next_funding_ts BIGINT)
```

Yang mengisinya, ketiganya:

- **binancef** — `/fapi/v1/premiumIndex`, field `lastFundingRate`. Docstring-nya
  (grep `lastFundingRate` is the current period's rate): *"`lastFundingRate` is the **current
  period's rate** (decimal, e.g. `"0.00010000"` == 1 bp per 8 h interval)"*.
- **okx** — `/api/v5/public/funding-rate`. Docstring-nya (grep `fundingTime` is the UPCOMING):
  *"`fundingTime` is the **UPCOMING settlement** of the displayed rate"*.
- **bybit** — dari frame `tickers.<SYM>` (grep `Bybit v5 ``tickers.<SYM>`` frame`).

**Ketiganya adalah rate periode berjalan untuk settlement yang belum terjadi**, disimpan setiap
kali poll/frame tiba. Bukan rate settled. Dugaan itu **benar**.

## 1b. Nilai distinct — ia bergerak, tapi menumpuk di satu titik

| venue | sampel | distinct | nilai tersering | share |
|---|---:|---:|---:|---:|
| binancef/BTCUSDT | 243.905 | 7.053 | **1,0000 bps** | **16,2 %** |
| bybit/BTCUSDT | 997.585 | 6.537 | **1,0000 bps** | **16,9 %** |
| okx/BTC-USDT-SWAP | 25.231 | 21.567 | **1,0000 bps** | **6,5 %** |

Nilai tersering kedua di mana pun ≤ 0,8 %. Jadi bukan konstanta — ribuan nilai berbeda — tapi
ada **massa titik besar tepat di 1,0000 bps** dan tidak ada satu pun sampel di atasnya.

## 1c. Dua rute berdampingan — angkanya nyaris tidak bergerak

| venue | rute | n | p05 | p50 | p95 | maks | % neg | **bps/hari** |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| binancef | A last-before-`next_funding_ts` | 77 | 0,0308 | 0,6147 | 1,0000 | 1,0000 | 2,6 % | **1,8441** |
| binancef | B terdekat 00/08/16 UTC (≤5 mnt) | 61 | 0,1404 | 0,6226 | 1,0000 | 1,0000 | 1,6 % | **1,8678** |
| bybit | A | 71 | −0,2092 | 0,4534 | 1,0000 | 1,0000 | 12,7 % | 1,3602 |
| bybit | B | 54 | −0,2081 | 0,4844 | 1,0000 | 1,0000 | 11,1 % | 1,4534 |
| okx | A | 85 | −0,0869 | 0,4458 | 0,9965 | 1,0000 | 9,4 % | 1,3373 |
| okx | B | 66 | −0,0393 | 0,4542 | 0,8825 | 1,0000 | 7,6 % | 1,3625 |

**Angka lama tidak diganti — didampingkan.** Selisihnya **+1,3 % / +6,9 % / +1,9 %**.

Sebabnya bisa dinyatakan: rute A sudah mengambil **cuplikan terakhir sebelum tiap settlement**
(`arg_max(funding_rate, ts_ms)` di-group menurut `next_funding_ts`), yang merupakan proksi
terdekat yang tersedia untuk nilai settled. Ia bukan rata-rata sepanjang jendela. Jadi kecurigaan
"merata-ratakan prediksi" tidak berlaku pada rute A sejak awal.

## 1d. Maksimum tetap **persis 1,0000 bps** di ketiga venue, di kedua rute

Yang masih bisa menjelaskannya, dan tidak dikejar di sini:

- **1,0000 bps = 0,01 % adalah komponen suku bunga standar per interval 8 jam**, konvensi yang
  sama di ketiga venue. Funding = premium + clamp(bunga − premium). Ketika premium kecil, funding
  duduk persis di baseline itu.
- **Ketiga venue BUKAN observasi independen.** Mereka memberi harga aset yang sama; kalau basis
  datar, ketiganya duduk di baseline yang sama karena konstantanya sama, bukan karena tiga
  kebetulan. "Persis 1,0000 di ketiganya" adalah **satu** kondisi pasar dilihat tiga kali.
- Yang **tidak** menjelaskannya: clamp di collector. Ia menyimpan `float(payload[...])` mentah;
  tidak ada pembatasan di kode.

**Konsekuensi untuk neraca:** `1,8441` bertahan, dan menjadi `1,8678` di bawah rute B. Funding
tetap term terbesar kedua.

---

# 2. Sensus turnover — dan koreksi atas aritmetikaku sendiri

Posisi saja. Setiap strategi dipanggil lewat `compare._make_positions_fn`, closure
`prices -> positions` yang sama yang diberikan `compare.py` ke harness. **Tidak satu pun return
dihitung.**

Parameter diambil **statis lewat AST** dari `scripts/compare.py`, bukan dikarang: parser-nya
dibangun di dalam `main()`, jadi memanggilnya akan menjalankan seluruh leaderboard dan menghitung
return. Percobaan pertama menebak `vol_target` padahal repo menamainya `target_vol`, dan tiga
strategi **raise** alih-alih diam-diam berjalan pada angka yang salah.

## Koreksi: funding perp tidak berlaku pada backtest spot

Board di-backtest pada **spot** `BTC-USD` coinbase harian. Spot tidak membayar funding. Versi
pertama sensus ini membebankan funding perp ke seluruhnya — itu kesalahan kategori, dan kolom
perp dipertahankan hanya karena **venue eksekusi yang dituju belum diputuskan**.

4.034 bar harian = 11,05 tahun (2015-07-20 .. 2026-08-04).

| strategi | leg/thn | hold hari | hari/thn | lama | baru SPOT | **Δ SPOT** | baru PERP | Δ PERP |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| tsmom_dir | 34,56 | 11,8 | 203,7 | 414,8 | 173,1 | **+241,7** | 548,7 | −133,9 |
| tsmom_ls | 27,22 | 26,7 | 363,1 | 326,7 | 136,3 | **+190,3** | 805,9 | −479,3 |
| pairs_ou | 16,29 | 5,3 | 42,9 | 195,4 | 81,6 | **+113,9** | 160,7 | +34,8 |
| tsmom | 14,10 | 28,9 | 203,7 | 169,2 | 70,6 | **+98,6** | 446,2 | −277,0 |
| tsmom_voltarget | 14,10 | 28,9 | 203,7 | 169,2 | 70,6 | **+98,6** | 446,2 | −277,0 |
| pairs_coint | 3,62 | 2,7 | 4,9 | 43,4 | 18,1 | +25,3 | 27,1 | +16,3 |
| ma_trend_filter | 1,63 | 262,7 | 213,9 | 19,5 | 8,2 | +11,4 | 402,6 | −383,1 |
| buy_and_hold | 0,09 | 8066,0 | 364,9 | 1,1 | 0,5 | +0,6 | 673,4 | −672,3 |

```
lama      = 12,0   × leg/thn                        (tidak membebankan funding sama sekali)
baru SPOT = 5,008  × leg/thn                        (yang board-nya benar-benar di-backtest)
baru PERP = 5,008  × leg/thn + 1,8441 × hari-ditahan/thn
```

**Lima dari delapan** punya `|Δ SPOT| > 50 bps/tahun`. Semua Δ SPOT positif — konstanta lama
memang lebih mahal, dan selisihnya `6,992 × leg/thn`, jadi ia terbesar pada strategi
ber-turnover tinggi.

Kolom PERP membalik tandanya untuk enam dari delapan, dan sebabnya aritmetika: konstanta lama
membebankan **nol funding**, sementara model perp membebankan 1,8441 bps/hari ditahan.
`buy_and_hold` menahan 364,9 hari/tahun, jadi ia membayar 673 bps/tahun funding di perp melawan
1,1 bps/tahun di model lama.

**Tidak ada strategi yang dievaluasi, tidak ada peringkat kinerja, tidak ada return yang dibaca.**

---

# 3. Tiga temuan terbuka

## 3a. Selisih 2,5×–9,5× — **TERCATAT, TIDAK DIKEJAR**

Bukan "terselesaikan". Alasannya dua, keduanya terukur: ia hidup di dalam term half-spread, yang
menyumbang `2 × 0,0078 = 0,0156` bps dari `10,0156` bps biaya taker pulang-pergi — **0,16 %**;
dan penyebutnya sendiri bergerak **2,3×** di bawah pembatasan staleness
(`DIAG-book-resolution-001` §1), jadi rasionya belum stabil untuk dikejar.

## 3b. Blok B — **TIDAK TERJAWAB, TIDAK DIKEJAR**

Sebabnya terkutip, `scripts/diag_provenance_001.py` (grep `ob_dd = "ORDER BY CAST`):

```python
ob_dd = "ORDER BY CAST(trade_id AS BIGINT)" if dedup else ""
inner = f"""... {ob_dd}"""
q = f"SELECT tid, ts_ms, price FROM ({inner}) {order_sql}"
```

`ORDER BY` berada di dalam subquery, jadi varian "tanpa `ORDER BY`" sudah terurut sebelum query
luar melihatnya. Varian (i), (ii), (iii) identik sampai digit ke-16. Pertanyaan sensitivitas
urutan berstatus **tidak terjawab**, bukan terjawab negatif.

## 3c. Duplikat — **DIPERBAIKI** dengan UNIQUE constraint

`UNIQUE (exchange, symbol, trade_id)` ditambahkan ke DDL `trades`. Bukan `DISTINCT` saat baca:
aturan yang hidup di ingatan pembaca adalah pola rot yang gerbangnya sendiri larang.

**Constraint saja akan memperburuknya, dan itu diukur sebelum ditambahkan:**

| yang diuji | hasil |
|---|---|
| `INSERT OR IGNORE` pada tabel **tanpa** constraint | **RAISES** Binder Error |
| `INSERT` polos `executemany` pada tabel **ber**-constraint, batch 3 dengan 1 duplikat | **RAISES**, hanya **1** baris tersimpan — seluruh batch hilang |
| `INSERT OR IGNORE` pada tabel ber-constraint, batch sama | **2** baris tersimpan, duplikatnya dilewati |

Baris kedua menentukan: jalur flush collector melakukan `rows_dropped_error += len(buf)` dan
mengusir koneksinya, jadi satu duplikat byte-identical akan menjadi **kehilangan data nyata**.
Dan baris pertama menentukan bentuk perbaikannya: store menyimpan **hari ini DAN kemarin**, jadi
berkas hari yang dibuat sebelum perubahan ini masih ditulisi dan tidak punya constraint.

Karena itu `insert_sql_for(con, table)` menanyakan **koneksinya** skema mana yang ia lihat
(`duckdb_constraints()`), dan memilih bentuk yang bisa dijalankan. Deteksi, bukan ingatan.

**Baris yang akan ditolak**, pada enam hari yang diperiksa (semua venue):

| hari | baris | ditolak | fraksi |
|---|---:|---:|---:|
| 2026-07-05 | 2.014.278 | 1.735 | 0,0861 % |
| 2026-07-07 | 4.815.304 | 31 | 0,0006 % |
| 2026-07-10 | 2.723.288 | 2.024 | 0,0743 % |
| 2026-08-01 | 730.022 | 1.039 | 0,1423 % |
| 2026-08-02 | 1.209.459 | 6.159 | 0,5092 % |
| 2026-08-06 | 2.395.188 | 758 | 0,0316 % |
| **total** | **13.887.539** | **11.746** | **0,0846 %** |

Setiap pasangan duplikat yang pernah diukur byte-identical di `ts_ms`, `price`, `qty` dan
`aggressor_buy`, jadi baris mana yang bertahan tidak bisa berbeda.

**Yang TIDAK diperbaiki:** berkas hari yang sudah ada tetap memuat duplikatnya — `CREATE TABLE
IF NOT EXISTS` tidak menambahkan constraint ke tabel yang sudah ada. Constraint ini mencegah
duplikat **baru**; ia tidak membersihkan yang lama.
