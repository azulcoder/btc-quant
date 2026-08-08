# DIAG-data-ceiling-001 — apa yang setiap venue SEBENARNYA terbitkan, terukur

**Tanggal sensus: 2026-08-08.** Read-only. Tidak ada byte yang di-ingest, tidak ada stream
baru yang ditambahkan, tidak ada backfill dijalankan — sensus saja, sesuai instruksi.

**Metode.** Delapan agen probe paralel, semuanya keyless, masing-masing wajib mengutip
perintah dan keluaran mentahnya. Label dipakai konsisten: **[DIUKUR]** = diprobe dalam run
ini dengan perintah yang dikutip; **[UNVERIFIED]** = dibaca di dokumentasi venue tapi tidak
diprobe. Klaim dokumentasi tidak pernah dipromosikan jadi pengukuran. Di mana sebuah probe
gagal (404, 403, auth gate), **kegagalan itu sendiri adalah hasil pengukuran**, bukan error
yang disembunyikan.

**Kesimpulan satu paragraf.** Plafon data untuk repo ini jauh lebih tinggi dari yang
diasumsikan, tapi tidak di tempat yang diharapkan. Binance: arsip publiknya **sembilan**
famili dataset, bukan aggTrades saja — tapi tidak satu pun berisi depth diff, jadi alasan
keberadaan perekam Tokyo tetap utuh. OKX ternyata **menerbitkan arsip L2 400-level harian
secara keyless sejak 2023-12-04** — ini temuan terbesar sensus dan membalik asumsi bahwa
book history harus direkam sendiri di mana pun. Coinbase justru bergerak ke arah sebaliknya:
kanal L3 `full` yang dulu publik **kini menolak subscribe tanpa auth**. Bybit tetap
trades-only. Perp-DEX terbelah: dYdX paling terbuka, Hyperliquid mengunci arsipnya di balik
requester-pays, Lighter menutup trade history di balik auth.

---

## 1. Binance Vision — sensus lengkap (item 2)

### 1a. Listing direktori, apa adanya

Enumerasi lengkap tipe dataset, dari listing S3 dengan `delimiter=/` [DIUKUR 2026-08-08]:

```
futures/um/daily/   : aggTrades, bookDepth, bookTicker, indexPriceKlines, klines,
                      markPriceKlines, metrics, premiumIndexKlines, trades
futures/um/monthly/ : aggTrades, bookTicker, fundingRate, indexPriceKlines, klines,
                      markPriceKlines, premiumIndexKlines, trades
spot/daily/         : aggTrades, klines, trades
```

**`liquidationSnapshot` TIDAK ADA untuk USDⓈ-M** — prefix-nya kosong. (COIN-M pernah
punya, 2023-06-25..2024-10-14, juga sudah dihentikan.) Setiap `.zip` berpasangan dengan
`.CHECKSUM` SHA-256 yang terverifikasi lolos `shasum -a 256 -c` [DIUKUR 2026-08-08].
Lag publikasi ≈ T-2: pada 2026-08-08 berkas harian terbaru untuk semua tipe hidup adalah
2026-08-06 [DIUKUR 2026-08-08].

### 1b. Per tipe: paling awal, granularitas, ukuran (BTCUSDT)

| tipe | paling awal [DIUKUR 2026-08-08] | granularitas | ukuran/hari zipped | backfillable |
|---|---|---|---|---|
| `trades` (raw) | **2019-09-08** (hari peluncuran kontrak) | per-trade | 14,486,953 B | penuh |
| `aggTrades` | 2019-12-31 | per-agg-trade | 8,605,435 B | penuh |
| `klines` (16 interval) | 2019-12-31 | 1m..1mo | 59,252 B (1m) | penuh |
| `indexPriceKlines` | 2019-12-23 | 1m.. | 32,973 B (1m) | penuh |
| `markPriceKlines` | 2019-12-23 | 1m.. | 32,247 B (1m) | penuh |
| `premiumIndexKlines` | 2019-12-24 | 1m.. | 27,601 B (1m) | penuh |
| `fundingRate` (monthly saja) | 2020-01 | per settlement 8 jam | 914 B/bulan | penuh |
| `metrics` (daily saja) | **2020-09-01** | **5 menit**, 288 baris/hari | 11,044 B | penuh sejak 2020-09 |
| `bookDepth` (daily saja) | 2023-01-01 | ~30 s × 12 pita persen | 553,659 B | penuh sejak 2023-01 |
| `bookTicker` | 2023-05-16 | event-level BBO | 87,758,829 B | **MATI setelah 2024-03-30** |

Catatan yang mahal kalau terlewat: **`trades` mentah 3,5 bulan lebih tua dari `aggTrades`**
(2019-09-08 vs 2019-12-31). Semua riset repo ini berdiri di atas `aggTrades`, jadi jendela
2019-09-08..2019-12-30 ada tapi belum pernah tersentuh.

### 1c. `bookTicker` — diunduh dan diperiksa (item 2c)

Satu hari penuh diunduh dan dibedah (2024-03-30) [DIUKUR 2026-08-08]:

```
skema : update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,transaction_time,event_time
baris : 7,398,592  (85,6 update/detik rata-rata)
jeda antar-update : median 1 ms · p99 149 ms · mean 11,7 ms · maks 2.438 ms
ukuran: 87,758,829 B zipped / 697,855,247 B CSV
```

**Batas atasnya keras dan bertanggal:** berkas harian terakhir `2024-03-30`, monthly
terakhir `2024-04` (terpotong, 37,7 MB vs 88 MB/hari di akhir Maret). Probe 2026-07-01 dan
2026-08-01 dua-duanya **HTTP 404** [DIUKUR 2026-08-08]. Jadi BBO history hanya bisa
di-backfill untuk jendela beku ~319 hari `2023-05-16..2024-03-30`; sejak 2024-05 seterusnya
BBO adalah **forward-only**.

### 1d. `metrics` — diperiksa (item 2d)

```
skema : create_time,symbol,sum_open_interest,sum_open_interest_value,
        count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,
        count_long_short_ratio,sum_taker_long_short_vol_ratio
kadensi: 5 menit (288 baris/hari), sejak 2020-09-01, masih terbit
ukuran : 11.044 B/hari zipped — ~4 MB/tahun
```

Ini memetakan langsung ke empat endpoint crowding yang di-poll collector Mac (yang REST-nya
hanya menyimpan jendela pendek). Konsekuensinya dinyatakan apa adanya dan **tidak lebih**:
untuk OI dan rasio long/short, jendela historis yang tersedia bukan lagi N=30 hari melainkan
~5,9 tahun pada kadensi 5 menit. Apakah itu mengubah status `CANNOT DECIDE` pada item mana
pun **bukan urusan sensus ini** — mengubahnya butuh PREREG dengan cap sendiri. Yang dicatat
di sini hanya: bahan bakunya ada, terukur, dan gratis.

### 1e. `bookDepth` — bukan L2

`timestamp,percentage,depth,notional`; satu snapshot tiap ~30 detik; **12 baris per
snapshot** = 12 pita persen dari mid (±0,2 / ±1 / ±2 / ±3 / ±4 / ±5 %). Tidak ada harga
per-level, tidak ada order, tidak ada diff. 34.560 baris/hari [DIUKUR 2026-08-08].

### 1f. KOREKSI BERTANGGAL — kalimat lama tidak dihapus

> **[KOREKSI 2026-08-08]** Docstring `scripts/record_depth_diffs.py` (grep
> `Binance Vision publishes aggTrades only`) menyatakan: *"the diff stream cannot be
> backfilled — Binance Vision publishes aggTrades only, and no venue here publishes
> historical depth diffs."*
>
> **Klausa pertama SALAH** [DIUKUR 2026-08-08]: listing lengkap memuat sembilan famili
> dataset harian, termasuk `bookTicker` (L1, jendela mati) dan `bookDepth` (agregat pita).
> **Klausa kedua — yang menanggung beban kesimpulan — TETAP BENAR** [DIUKUR 2026-08-08]:
> tidak ada dataset depth-diff di mana pun dalam enumerasi lengkap (um daily, um monthly,
> spot daily). `bookTicker` adalah quote L1 dan `bookDepth` adalah agregat pita 30 detik;
> keduanya bukan diff stream, dan rekonstruksi antrean tetap mustahil dari keduanya.
> Alasan keberadaan perekam Tokyo **bertahan**; hanya premis "aggTrades only" yang perlu
> ditulis ulang. Kalimat lamanya tetap di tempatnya, ditandai, bukan dihapus.

> **[KOREKSI 2026-08-08]** Asumsi implisit bahwa BBO bisa di-backfill dari Vision "nanti"
> sudah **mati sejak 2024-04-30**. Kalau BBO history dibutuhkan setelah tanggal itu, satu-
> satunya sumbernya adalah rekaman sendiri.

---

## 2. Gap analysis stream USDⓈ-M — yang ADA vs yang DIREKAM (item 3)

Sumber daftar stream: dokumentasi resmi Binance, halaman `ws-streams/public` dan
`ws-streams/market` di `developers.binance.com` (base `wss://fstream.binance.com`).

| stream | Mac | Tokyo | kalau tidak direkam |
|---|---|---|---|
| `@depth@100ms` (diff L2) | — | **YA** | **HILANG PERMANEN** — tidak ada arsip di venue mana pun |
| `@depth20@100ms` (partial book) | YA (disimpan 1/detik) | — | HILANG PERMANEN untuk level-granularity |
| `@bookTicker` (BBO event-level) | — | — | **HILANG PERMANEN sejak 2024-05** (arsip mati) |
| `!bookTicker` (5 s, all-market) | — | — | HILANG PERMANEN (tak pernah diarsipkan) |
| `@rpiDepth@500ms` (stream baru) | — | — | HILANG PERMANEN |
| `@forceOrder` / `!forceOrder@arr` (likuidasi) | — | — | **HILANG PERMANEN** — lihat §2b |
| `@aggTrade` | YA (via REST poll 5 s) | — | backfillable penuh (Vision + REST `fromId`) |
| `@markPrice@1s/@3s` | setara via REST premiumIndex | — | backfillable ke granularitas kline saja |
| `@kline_*` (16 interval) | — | — | backfillable penuh (closed bars) |
| `@miniTicker` / `@ticker` (+ `@arr`) | — | — | derivable penuh dari klines — kehilangan efektif nol |
| `!contractInfo`, `!assetIndex@arr`, `tradingSession` | — | — | HILANG PERMANEN, nilai riset rendah |

### 2a. `bookTicker` sebagai kontrol positif — dan koreksi rail §0.2

Ini yang diminta secara khusus, dan hasilnya membalik satu klaim repo.

**Terukur mengalir keyless di network Mac** [DIUKUR 2026-08-08]: 815 pesan/30 s pada satu
jendela dan 5.070 pesan/30 s pada jendela lain — sementara **pada run yang sama**,
`aggTrade` dan `markPrice@1s` menghasilkan **nol** pesan. Nol-pada-run-yang-sama itulah
kontrolnya: filter topic-nya nyata, tapi tidak sepenuhnya seperti yang ditulis.

> **[KOREKSI 2026-08-08]** Komentar di `btcquant/collector.py` (grep
> `is the ONLY Binance futures WS topic that flows`) menyatakan `depth20@100ms` adalah
> satu-satunya topic Binance futures yang mengalir di network ini. **Terlalu sempit**:
> `btcusdt@bookTicker` juga mengalir, terukur dua kali. Polanya [DISIMPULKAN, bukan
> terukur]: stream dari seksi *Public* dokumentasi (keluarga book/depth) lolos, stream dari
> seksi *Market* (aggTrade/markPrice/forceOrder) diblokir. Kalimat lama dibiarkan; koreksi
> ini yang berlaku.

**Biaya volumenya** [DIUKUR 2026-08-08]: 0,39–2,4 GB/hari uncompressed dari dua jendela
30 detik (rasio gzip terukur 11,1× → ~35–215 MB/hari terkompresi). Rentang selebar itu
adalah properti dua sampel pendek, bukan rata-rata harian — dan disebut begitu, bukan
dirata-ratakan diam-diam. Referensi independen: arsip Vision 2024-03-30 = 87,8 MB zipped
untuk satu hari penuh.

**Kenapa ini kontrol positif yang perekam L2 sekarang tidak punya:** perekam depth-diff
merekonstruksi book dari snapshot + diff, dan tidak ada satu pun sumber independen di tape
untuk menguji apakah rekonstruksi itu benar. `bookTicker` adalah BBO yang dihitung venue
sendiri; best bid/ask hasil rekonstruksi harus cocok dengannya, tick per tick. Tanpa itu,
kebenaran rekonstruksi adalah **[UNVERIFIED]** dan akan tetap begitu. *Sensus mencatat ini;
menambah stream bukan bagian dari giliran ini.*

### 2b. Likuidasi Binance — tidak ada jalur historis keyless, terkonfirmasi

Tiga arah dicoba, ketiganya tertutup [DIUKUR 2026-08-08]: Vision um tidak punya
`liquidationSnapshot`; REST `allForceOrders` menjawab **404** (endpoint dihapus);
REST `forceOrders` menjawab **401** (`API-key format invalid` — gerbang USER_DATA, dan
itu pun hanya akun sendiri). `allLiquidation` Bybit yang direkam collector adalah **venue
lain** dan per §0.7 bukan substitusi.

---

## 3. Venue lain — plafon masing-masing

### 3a. OKX — temuan terbesar sensus ini

**Arsip L2 400-level harian, keyless, sejak 2023-12-04** [DIUKUR 2026-08-08]. Format:
JSON-lines `{"instId","action":"snapshot"|"update","ts","asks":[[px,sz,count],…],"bids":[…]}`
— snapshot penuh **plus update inkremental**, yaitu kelas data yang persis sama dengan yang
direkam perekam Tokyo untuk Binance. Ukuran 290–377 MB/hari untuk BTC-USDT-SWAP. Halaman
unduhnya memanggil `isVip` yang menjawab 403 tanpa login, **tetapi API download-link dan URL
CDN-nya sendiri tidak butuh auth sama sekali** — HEAD 200 pada berkas 376 MB dibuktikan.
Varian 5000-level ada sejak 2026-06-09. Lubang terukur: 2023-12-01 dan 2023-12-10 404.

> **[KOREKSI 2026-08-08]** Asumsi bahwa book history harus direkam sendiri di semua venue
> **SALAH untuk OKX**: L2 snapshot+update harian bisa di-backfill keyless sampai
> 2023-12-04. Ini tidak mengubah apa pun soal Binance (tetap tidak ada), dan tidak
> menyiratkan langkah apa pun — hanya mencatat bahwa plafonnya berbeda dari yang dikira.

Sisanya: trades tick sejak 2021-10-01 (arsip CDN), candles 1m via REST sampai **2019-12-16**
(hari listing), funding sejak 2021-01-01. REST caps terkonfirmasi ulang independen: trades
~90 hari (data di now−89d, kosong di now−90d), funding-rate-history ~95 hari, OI-history
mentok tepat di 2024-01-01 untuk period=1D. Yang benar-benar tergerbang: kanal WS
tick-by-tick `books-l2-tbt` / `books50-l2-tbt` butuh **VIP4+** (error 64003, dikutip
verbatim dari dokumen) — gerbang itu sendiri adalah temuan untuk repo keyless.

### 3b. Bybit

Arsip publik hanya **lima direktori**, dan tidak satu pun berbentuk book: `trading/`
(trades linear, sejak **2020-03-25** = hari peluncuran, ~46 MB/hari gz), `spot/` (sejak
2022-11), `premium_index/` dan `spot_index/` (dua-duanya **mati** sejak Maret 2020, inverse
saja), `kline_for_metatrader4/` (basi, berhenti 2024-11). REST: kline dan funding sampai
2020-03-25; **open-interest dan account-ratio dua-duanya mulai tepat 2020-08-05** — jendela
2020-03-25..2020-08-04 adalah lubang permanen keyless; historical-volatility opsi sampai
2019-06-09 dalam halaman maks 30 hari. Halaman history-data web-nya menjawab **403
(AkamaiGHost)** tanpa login — gerbang, dicatat sebagai gerbang.

Dua koreksi terhadap asumsi yang beredar di repo/prompt [DIUKUR 2026-08-08]: **tidak ada
topic `orderbook.500`** (kedalaman linear adalah 1/50/200/1000), dan dokumen v5 hari ini
menyatakan verbatim *"When you receive a Buy update, this means that a long position has
been liquidated"* — **berlawanan dengan pemetaan di `btcquant/collector.py`** (grep
`normalize_bybit_liq`) yang menyimpan `Buy` sebagai sisi `short`. Salah satu dari keduanya
keliru. **Tidak kuubah di giliran ini**: membalik label tanpa bukti tape hanya memindahkan
kesalahan. Yang benar adalah menguji pada jendela crash (likuidasi harusnya didominasi long)
— itu pekerjaan lain dengan kontrolnya sendiri.

Satu fakta bertanggal yang perlu diketahui, bukan disarankan: stream **`orderbook.full`**
(full-depth, 200 ms, delta-only) mulai live untuk linear mainnet **2026-08-11**, tiga hari
setelah sensus ini.

### 3c. Coinbase — bergerak ke arah sebaliknya

> **[KOREKSI 2026-08-08]** Catatan repo bahwa kanal `full` Coinbase adalah **L3 sungguhan
> yang keyless** sudah **usang**. Server menolak subscribe tanpa auth, verbatim:
> *"level2, level3, and full channels now require authentication."* [DIUKUR 2026-08-08]
> L3 order-by-order Coinbase kini bergerbang API key — bukan tier berbayar, tapi tetap
> gerbang, dan repo ini keyless. Kalimat lama di memori/dokumen tidak dihapus; ini yang
> berlaku sejak tanggal di atas.

Yang **tetap keyless**: `level2_batch` (delta L2 full-depth, batch ~50 ms; terukur ~15,3
pesan/detik → ~0,43 GB/hari), `matches`, `ticker`. Dan satu kejutan ke arah baik: REST
`/products/BTC-USD/trades` **paginasinya tak terbatas** — `trade_id=1` tercapai dalam satu
request, timestamp **2014-12-01T05:33:56Z**. Seluruh ~1,068 miliar trade bisa di-backfill
keyless (~128 GB JSON mentah, ~1,07 juta request). Candles baru mulai 2015-07-20, jadi
7,5 bulan pertama hanya ada sebagai tick. Snapshot REST `book?level=3` keyless (110.663
order, 6,8 MB) — tapi tanpa `full`, tidak ada delta untuk menganimasikannya. Perp
BTC-PERP-INTX: trades ~2023-08-31, candles 2023-08-30, funding per jam ≥2023-07-10, semua
keyless; depth perp tidak.

### 3d. Perp-DEX

| venue | arsip/history | plafon resolusi | catatan |
|---|---|---|---|
| **Hyperliquid** | S3 `hyperliquid-archive` + `hl-mainnet-node-data` = **Requester Pays**, anonim ditolak | L2 snapshot (arsip berbayar); keyless: funding penuh sejak 2023-05-12, candle 1m cuma ~3,5 hari | dokumennya sendiri: *"no guarantee of timely updates and data may be missing"* |
| **dYdX v4** | indexer keyless: trades 1000/halaman mundur ke **2023-11-14**, candles 1MIN + funding ke genesis | L3 hanya via **full node sendiri** (gRPC streaming) — harganya infra, bukan API key | BTC-USD hanya ~1.641 trade/hari ≈ 0,27 MB/hari |
| **Lighter** | funding penuh sejak 2025-01-19; `/trades` historis **minta auth**; `candlesticks` **403** dari network ini | book/trades = forward-only via WS (snapshot + delta 50 ms) | 403 mungkin geo-spesifik [UNVERIFIED] |
| **Drift** | **S3 publik, listable anonim**, CSV per hari per market | tradeRecords (1,48 MB/hari untuk SOL-PERP terukur) | satu-satunya perp-DEX dengan arsip bulk gratis |
| Paradex / Aster | REST keyless jalan; klines Paradex ke 2023-11, Aster ke 2021-09 | tidak ditemukan arsip bulk first-party [UNVERIFIED — scan cepat] | |

---

## 4. Peta penyimpanan — di mana setiap byte berada

Terukur 2026-08-08. Angka cepat-berubah tinggal di `docs/STATUS.md`; tabel ini adalah
**pengukuran bertanggal**, bukan klaim keadaan-sekarang.

| store | isi | ukuran [DIUKUR 2026-08-08] | durabilitas | siapa yang menulis |
|---|---|---|---|---|
| HF `azulcoder/btc-quant-ticks` prefix `vision/` | arsip aggTrades | 12,88 GB (4.222 berkas) | offsite, git-versioned, publik | pemegang token HF |
| HF prefix `data/` | tick terekam (8 tabel/hari) | 1,88 GB (267 berkas) | offsite, git-versioned | pemegang token HF |
| Mac `data/ticks/` | store DuckDB rolling 2 hari | 390 MB | **single-copy** sampai sync 07:20 WIB | collector launchd |
| Mac `data/archive/` | repack bulanan Juli 2026 | 28 MB | **SINGLE-COPY — tidak ada di HF** | skrip archive |
| Mac `data/vision/` | manifest saja (0 parquet sehat) | 12 MB | manifest ganda di HF; ledger single-copy | skrip migrasi |
| VM Tokyo tape | depth diff mentah | lihat §5 | **kini ganda**: VM + GCS append-only | recorder (user `btcq`) |
| GCS `btcq-depth-tape-1` | salinan tape + heartbeat + QC | tumbuh | offsite, versioned, **write-only bagi VM** | SA `btcq-tape-writer` |
| GitHub `azulcoder/btc-quant` | kode + dokumen (232 berkas) | .git lokal 21 MB | offsite, versioned, publik | azulcoder |

Dua koreksi dari pemetaan ini [DIUKUR 2026-08-08]:

> **[KOREKSI 2026-08-08]** Remote git untuk **kode** adalah GitHub, bukan HF. HF hanya
> memegang dataset; nol berkas kode dalam listing penuhnya. Kode dan data hidup di dua
> hoster berbeda — itu fitur, tapi berarti "cek HF" bukan cek kode.

> **[KOREKSI 2026-08-08]** **DVC tidak menyumbang durabilitas apa pun**: `.dvc/config`
> berukuran 0 byte, tidak ada remote terkonfigurasi. Asumsi apa pun bahwa DVC mem-backup
> data ke suatu tempat adalah salah.

Risiko single-copy yang tersisa setelah giliran ini, diurutkan berdasarkan apakah bisa
dibeli mundur: **`data/archive/` 28 MB** (bisa direkonstruksi dari HF `data/` — overlap
tanggalnya belum di-diff byte-level, jadi [DISIMPULKAN]) dan **dua hari `data/ticks/`
sebelum sync harian** (hilangnya = hilang permanen, karena mayoritas leg-nya forward-only).

---

## 5. Apa yang sensus ini TIDAK buktikan

Sensus ini memetakan **apa yang tersedia**, bukan apa yang berguna. Ia tidak mengukur satu
pun return, tidak menguji satu pun hipotesis, dan tidak memberi bukti bahwa data yang baru
ditemukan mengandung informasi yang bisa dipanen — arsip L2 OKX yang keyless itu, misalnya,
adalah 377 MB/hari yang sama sekali belum pernah dibuka di repo ini, dan ketersediaan bukan
relevansi. Absennya sesuatu juga dibatasi oleh apa yang diprobe: "tidak ditemukan" pada
arsip Paradex/Aster berarti scan cepat tidak menemukannya, bukan terbukti tidak ada, dan
gerbang auth yang terukur hari ini (Coinbase L3, OKX VIP4, Lighter, Bybit history page,
Hyperliquid requester-pays) bisa bergeser dua arah kapan saja tanpa pengumuman — semuanya
pengukuran bertanggal, bukan sifat permanen venue. Angka volume stream berasal dari jendela
30 detik dan mewarisi ketidakpastian itu; jendela sepi dan jendela ramai berbeda hampir
enam kali lipat pada `bookTicker`. Terakhir, sensus ini sengaja tidak menyarankan arah:
tidak ada stream yang ditambahkan, tidak ada byte yang di-backfill, dan setiap konsekuensi
riset dari apa yang ditemukan di sini butuh pre-registration-nya sendiri dengan cap
trial-nya sendiri.
