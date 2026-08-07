# DIAG-cost-ledger-001 — neraca besaran biaya, semua term dalam bps

**Look: diagnostik provenance.** Tidak ada estimator yang disentuh, tidak ada yang dibangun,
tidak ada rekomendasi arah di dokumen ini.
**Skrip:** `scripts/diag_cost_ledger_001.py` · **mesin:** `reports/cost-ledger-001.json`
**Semua angka `[DIUKUR]`** kecuali yang ditandai lain, dijalankan 2026-08-07, slice beku
`2026-07-05..2026-08-03`. Subset berat terdeklarasi: `2026-07-30, -31, 2026-08-01, 2026-08-03`.

---

## 1a. `cost_bps` dan `slippage_bps` — **PER SISI**

Deklarasinya (`btcquant/backtest.py`, grep `cost_bps: float = 10.0`):

```python
cost_bps: float = 10.0,
slippage_bps: float = 2.0,
```

Pemakaiannya (grep `cost_rate = (float(cost_bps)`):

```python
cost_rate = (float(cost_bps) + float(slippage_bps)) / 10_000.0
# Cost base = single-leg |Δ position| turnover, PLUS any optional extra-leg
```

Yang menjawab pertanyaannya bukan nama variabelnya melainkan komentar itu: **basis biayanya
`single-leg` turnover**. Jadi `10,0 + 2,0 = 12 bps` dibebankan **per leg**. Docstring modulnya
(grep `full 0→1 entry then 1→0 exit`) menjelaskan mekanismenya: *"charged on turnover
(`|Δ position|`) … so a full 0→1 entry then 1→0 exit pays the round-trip"* — yaitu pulang-pergi
kena **dua kali**, total **24 bps**, bukan bahwa 12 bps sudah pulang-pergi.

## 1b. Funding — realized per interval 8 jam

| venue | interval | p05 | p50 | p95 | maks | % negatif | bps/hari ditahan penuh |
|---|---:|---:|---:|---:|---:|---:|---:|
| binancef/BTCUSDT | 77 | 0,0308 | **0,6147** | 1,0000 | 1,0000 | 2,6 % | **1,8441** |
| bybit/BTCUSDT | 71 | −0,2092 | **0,4534** | 1,0000 | 1,0000 | 12,7 % | **1,3602** |
| okx/BTC-USDT-SWAP | 85 | −0,0869 | **0,4458** | 0,9965 | 1,0000 | 9,4 % | **1,3373** |

Semua dalam bps per interval kecuali kolom terakhir. Satu baris = satu settlement, di-dedup
menurut `next_funding_ts` dengan kuotasi terakhir sebelumnya. Tandanya berarti: positif = long
membayar short. `bps/hari` = `p50 × 3`.

**Catatan pada maks:** ketiga venue mencapai persis **1,0000 bps** dan tidak melewatinya. Itu
nilai baseline 0,01 % per interval, bukan sebuah cap yang terukur di sini — yang terukur hanya
bahwa slice beku ini tidak pernah melampauinya.

## 1c. Menyeberang buku — bps **di luar** half-spread

| venue | notional | n snapshot | thin | p50 | p95 |
|---|---:|---:|---:|---:|---:|
| binancef | $10.000 | 393.957 | 165 | **0,0000** | 0,0000 |
| binancef | $100.000 | 379.387 | 14.735 | **0,0000** | 0,0000 |
| binancef | $1.000.000 | 125.084 | **269.038** | **0,0000** | 0,1013 |
| bybit | $10.000 | 263.610 | 0 | 0,0000 | 0,0000 |
| bybit | $100.000 | 263.336 | 274 | 0,0000 | 0,4468 |
| bybit | $1.000.000 | 138.722 | **124.888** | **0,3286** | 0,7906 |
| okx | $10.000 | 293.467 | 3 | 0,0000 | 0,0000 |
| okx | $100.000 | 292.619 | 851 | 0,0000 | 0,3277 |
| okx | $1.000.000 | 201.721 | **91.749** | **0,1394** | 0,5843 |

`thin` = snapshot yang buku terlihatnya **tidak cukup** untuk mengisi notional itu; ia dihitung
dan **tidak diekstrapolasi**. Pada $1 juta, `thin` mencapai **68 % snapshot binancef**
(269.038 dari 394.122), jadi p50 dan p95 di baris itu dihitung atas 32 % sisanya — yaitu atas
snapshot yang bukunya kebetulan cukup tebal.

Buku yang tersimpan adalah **top-20 (binancef) / top-50 (okx)**, bukan buku penuh, jadi `thin`
mengukur batas apa yang direkam, bukan batas likuiditas venue.

## 1d. Neraca, urut menurun

| term | bps | basis | dari |
|---|---:|---|---|
| **fee taker, pulang-pergi** | **10,0000** | 2 sisi × 5 bps | `[DIASUMSIKAN]` tarif terpublikasi 0,05 %, `EDA-microstructure-001.md` §2 |
| **fee taker, per sisi** | **5,0000** | 1 sisi | idem |
| **fee maker, pulang-pergi** | **4,0000** | 2 sisi × 2 bps | `[DIASUMSIKAN]` tarif terpublikasi 0,02 %, `EDA-execution-001.md` |
| **fee maker, per sisi** | **2,0000** | 1 sisi | idem |
| **funding, per hari ditahan penuh** | **1,8441** | binancef, p50 × 3 | `[DIUKUR]` §1b |
| funding, per hari | 1,3602 | bybit | `[DIUKUR]` §1b |
| funding, per hari | 1,3373 | okx | `[DIUKUR]` §1b |
| **impact $1 jt, p95 di luar half-spread** | **0,7906** | bybit | `[DIUKUR]` §1c |
| impact $1 jt, p95 | 0,5843 | okx | `[DIUKUR]` §1c |
| impact $1 jt, p50 | 0,3286 | bybit | `[DIUKUR]` §1c |
| impact $100 rb, p95 | 0,4468 | bybit | `[DIUKUR]` §1c |
| impact $100 rb, p95 | 0,3277 | okx | `[DIUKUR]` §1c |
| impact $1 jt, p50 | 0,1394 | okx | `[DIUKUR]` §1c |
| impact $1 jt, p95 | 0,1013 | binancef | `[DIUKUR]` §1c |
| **half-spread (`c_book`)** | **0,0078** | 1 sisi | `[DIUKUR]` `BOOK-001` |
| impact $10 rb, p50 dan p95 | 0,0000 | ketiga venue | `[DIUKUR]` §1c |
| impact $100 rb, p50 | 0,0000 | ketiga venue | `[DIUKUR]` §1c |
| impact $1 jt, p50 | 0,0000 | binancef | `[DIUKUR]` §1c |

Fee adalah tarif terpublikasi dan **tidak dapat diukur dari data sama sekali** — ia dikutip,
bukan diturunkan, dan ditandai `[DIASUMSIKAN]`.

## 2a. Apakah collector merekam depth DIFF?

**Tidak, di mana pun.** `btcquant/collector.py`, grep `the raw delta stream is a UI`:

```
The collector stores the *merged* book, downsampled to at most one
``depth_snapshots`` row per second (DESIGN §3) — the raw delta stream is a UI
concern, not a storage one.
```

Delapan tabel didefinisikan (`trades`, `liquidations`, `depth_snapshots`, `funding_mark`,
`open_interest`, `crowding`, `dvol`, `options_chain`) dan **tidak satu pun menyimpan delta**.

Untuk binancef, grep `every ``depth20`` frame is a FULL 20-level snapshot`: wire-nya memang
mengirim snapshot penuh setiap 100 ms, bukan diff — tapi ia tetap di-downsample ke ≤1 baris per
detik sebelum disimpan.

## 2b. Konsekuensinya untuk posisi antrian

**Posisi antrian tidak dapat direkonstruksi dari data yang ada, pada kadensi berapa pun.** Itu
bukan soal 1 Hz terlalu lambat — antrian adalah fungsi dari **urutan peristiwa** (order masuk,
batal, terisi sebagian) pada satu level harga, dan urutan itu hanya ada di diff stream. Snapshot
merekam **keadaan**, bukan **peristiwa**; dua snapshot dengan kedalaman sama bisa berasal dari
nol aktivitas atau dari ratusan order masuk-dan-batal.

**Dan diff stream tidak dapat di-backfill.** Arsip publik Binance Vision menerbitkan aggTrades
saja (`README.md`, grep `aggTrades is TRADES ONLY`), dan tidak ada venue di daftar ini yang
menerbitkan riwayat depth diff. Untuk setiap detik yang sudah lewat, data itu hilang permanen.

## 2c. Apakah 1 Hz cukup untuk IMPACT (bukan antrian)?

`|perubahan|` kedalaman 5 level teratas antar snapshot berturut-turut:

| venue | n pasangan | p50 | p95 |
|---|---:|---:|---:|
| binancef/BTCUSDT | 196.894 | **4,39 %** | **72,95 %** |
| bybit/BTCUSDT | 131.472 | **8,91 %** | **83,23 %** |
| okx/BTC-USDT-SWAP | 146.667 | **6,80 %** | **69,70 %** |

Pasangan berjarak > 2.000 ms dikecualikan — ia menyeberangi lubang feed, bukan langkah kadensi,
dan akan mengukur lubangnya alih-alih bukunya.

## 3. Verifikasi silang skala

| jangkar | `2 × c × 64.000` | vs 1 tick $0,10 | selisih |
|---|---:|---|---:|
| 0,0078 bps (dikutip di `PREREG-microstructure-001`) | $0,099840 | **MATCH** | −0,160 % |
| 0,007805 bps (separuh dari 0,01561 terukur `BOOK-001`) | $0,099904 | **MATCH** | −0,096 % |

Skalanya konsisten. Selisih −0,16 % pada baris pertama adalah pembulatan `0,0078` terhadap
`0,007805`, bukan kesalahan skala. **Neraca §1d tidak salah skala.**

## 4. Status tiga temuan terbuka — tidak dikerjakan

- **Selisih 2,5×–9,5×** (`c_Roll` terhadap `√(E[c²])` tertimbang trade): belum punya mekanisme,
  dan penyebutnya sendiri bergerak 2,3× di bawah pembatasan staleness (`DIAG-book-resolution-001`
  §1), jadi angka rasionya belum stabil.
- **Blok B tidak terjawab**: `diag_provenance_001.py` menguji satu pengurutan tiga kali karena
  `ORDER BY` berada di dalam subquery dedup, jadi pertanyaan sensitivitas urutan berstatus
  **tidak terjawab**, bukan terjawab negatif.
- **Duplikat 0,11 %**: berulang pada hari kedua (758 dari 688.524 pada `2026-08-06`), sistemik,
  semua pasangan byte-identical, dan bentuk perbaikannya belum diputuskan.
