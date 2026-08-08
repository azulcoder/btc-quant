# DIAG-orderflow-profile-001 — profil, memori, format, dan jaringan: diukur

**Tanggal: 2026-08-08.** Branch `orderflow-terminal`. Nol optimasi dilakukan; hanya
pengukuran. Nol angka prediktif.

**Kesimpulan satu kalimat: I/O jarak-jauh mendominasi secara absolut — 253 detik lalu GAGAL
lewat Hugging Face versus 1,64 detik atas hari lokal yang sama-sama satu hari — jadi optimasi
CPU pada layer riset tidak relevan, sementara format penyimpanan memberi 69× terukur.**

## 1. Profil `order_flow_bars()` (item 2a, 2b)

### 1a. Panggilan realistis lewat HF (7 hari, `source="auto"`)

```
WALL 252,97 s  ->  GAGAL
OrderFlowError: cannot open the tick store: Invalid Input Error:
  Failed to read file "hf://…/data/date=2026-07-23/depth_snapshots.parquet":
  ZSTD Decompression failure
max RSS 948.486.144 B (948 MB)
```

Kegagalan ZSTD itu dicatat repo sebagai **transien**; yang penting di sini bukan kegagalannya
melainkan **253 detik yang dihabiskan sebelum gagal**, seluruhnya di pembukaan sumber remote.

### 1b. Panggilan identik atas hari LOKAL (1 hari, `source="local"`)

```
WALL 1,64 s · bars (1440, 48) · 48 kolom
peak tracemalloc 6,8 MB · max RSS 754.728.960 B (755 MB)
cProfile: 156.085 pemanggilan fungsi dalam 0,196 s
```

**0,196 s dari 1,64 s wall terlihat oleh cProfile.** Sisanya ~1,44 s (**88 %**) ada di dalam
DuckDB (C++), yang tidak diinstrumentasi cProfile. Artinya: **agregasi sudah didorong ke
DuckDB**, bukan loop Python.

15 fungsi teratas menurut `cumtime` — dan yang teratas bukan komputasi:

| cumtime | fungsi |
|---|---|
| 0,084 s | `orderflow._write_bars_cache` |
| 0,083 s | `pandas.DataFrame.to_parquet` |
| 0,052 s | `pandas.io.parquet.write` |
| 0,046 s | `pyarrow.pandas_compat.dataframe_to_arrays` |
| 0,042 s | `DataFrame.__getitem__` (146×) |
| 0,040 s | `_get_item_cache` (144×) · `_get_columns_to_convert` |
| 0,038 s | `_box_col_values` (137×) |
| 0,037 s | `generic.__finalize__` (199×) |
| 0,036 s | `copy.deepcopy` (13.330 pemanggilan) |
| 0,033 s | import machinery |

**Biaya Python terbesarnya adalah MENULIS CACHE, bukan menghitung apa pun.** Dan 13.330
`deepcopy` untuk 1.440 bar × 48 kolom adalah pekerjaan yang tidak menghasilkan angka.

### 1c. Memori (item 2b)

`peak tracemalloc 6,8 MB` versus `max RSS 755 MB`. Selisih dua orde itu bukan kebocoran
Python — ia DuckDB dan pyarrow yang mengalokasi di luar pelacak Python. Yang bisa dinyatakan:
**alokasi Python-level tidak signifikan**; jejak memori dimiliki mesin kolumnar.

## 2. Biaya BACA per format, pada data yang identik (item 2c)

Tape depth-diff `binancef` 2026-08-08, **68.081.106 B** JSONL.GZ, dikonversi SATU hari sebagai
eksperimen terukur [DIUKUR 2026-08-08]:

| | waktu | keluaran |
|---|---|---|
| **A. JSONL.gz** baca + parse + agregasi (loop Python) | **2,34 s** | 457.133 frame → 486 menit |
| konversi → Parquet + ZSTD, dictionary pada `price`/`side` | 6,81 s (sekali) | **60.877.024 B** = **0,89×** ukuran sumber, 15.774.681 baris level |
| **B. Parquet** baca + agregasi (DuckDB `GROUP BY`) | **0,03 s** | 486 menit |

**Percepatan A → B = 69,0×**, dan berkasnya **lebih KECIL** meski barisnya meledak dari
457 ribu frame menjadi 15,8 juta baris level — dictionary encoding pada kolom harga yang
mengerjakannya.

Catatan kejujuran: A dan B tidak sepenuhnya setara — A memparsing JSON menjadi objek Python,
B mendorong agregasi ke DuckDB. Tapi itu **justru** perbandingan yang relevan: yang diukur
adalah apa yang tiap format **memungkinkan**, bukan mikrobenchmark parser.

## 3. Dashboard (item 2d)

| aset terminal | byte |
|---|---|
| `terminal-views.js` | 311.917 |
| `terminal.js` | 276.729 |
| `terminal-state.js` | 228.131 |
| `terminal.css` | 86.432 |
| `terminal.html` | 80.727 |
| `terminal-adapters.js` | 53.469 |
| `terminal-hist.js` | 46.054 |
| `terminal-replay.js` | 32.671 |
| `terminal-books.js` | 24.978 |
| `terminal-hfdata.js` | 12.643 |
| **subtotal halaman terminal** | **≈1,15 MB** tak-terkompresi |
| seluruh `dashboard/` | **1,9 MB** |

**Di mana agregasinya terjadi: DI KLIEN.** Rantai A (live) tidak punya server di jalurnya —
WebSocket venue → store JS → loop rAF. Rantai C mengunduh parquet **ke dalam tab** dan
mengagregasi di `aggregateTradeRows()`, sebuah loop `for…of` JS. Hanya rantai B (`/v1/profile`
dari `collector.py`) yang mengagregasi di server dengan `GROUP BY` DuckDB.

Per instruksi: **agregasi klien atas array besar adalah temuan**, dan ia ada di dua dari tiga
rantai yang memberi makan halaman. Waktu render belum diukur — `verify_terminal_browser.py`
ada tapi sengaja di luar CI (butuh Playwright), dan menjalankannya berarti membuka browser
yang tidak termasuk lingkup giliran ini.

## 4. Realitas jaringan (item 3)

### 4a. Uplink Mac, link sepi, tiga sampel per tujuan [DIUKUR 2026-08-08]

| tujuan | sampel | rata-rata |
|---|---|---|
| **GCS** | 1,98 · 1,63 · 1,99 MB/s | **1,87 MB/s** |
| **OKX CDN** | 2,66 · 3,15 · **0,05** MB/s | 1,95 MB/s (sangat tak stabil) |
| **Hugging Face** | 0,11 · 0,12 · 0,29 MB/s | **0,17 MB/s** |

> **[KOREKSI 2026-08-08]** Sebelumnya di `docs/DIAG-control-a-regime-001.md` kutulis penyebab
> lambatnya adalah *"uplink mesin lokal sedang dipakai proses lain"*. **Itu salah.** GCS
> berjalan 1,87 MB/s pada saat yang sama. Yang benar, terukur: lambatnya **per-tujuan** —
> Hugging Face seragam lambat (~0,17 MB/s, **11× lebih lambat dari GCS**), dan OKX **melambat
> di dalam sesi** (2,66 → 3,15 → 0,05 MB/s dalam tiga sampel berurutan). Kalimat lamanya tidak
> dihapus; koreksi ini yang berlaku.

Satu temuan sampingan yang mahal kalau terlewat: probe pertama ke OKX mengembalikan **HTTP 404
`NoSuchKey`** karena memakai jalur `pro/`; berkas 2024 hidup di jalur **legacy**. Mengukur
throughput terhadap URL yang 404 akan melaporkan "gagal" sebagai "lambat" — resolver
`okx_l2_acquire.resolve()` yang mencoba kedua jalur adalah yang benar.

### 4b. VM Tokyo → GCS, diturunkan tanpa menyentuh perekam [DIUKUR 2026-08-08]

Perekam dan collector **tidak disentuh**: angkanya diturunkan dari jejak `btcquant-tape-sync`
yang sudah ada di serial console (waktu mulai/selesai unit + `bytes at rest` yang dicetaknya),
26 pengamatan berturut:

```
median 4,78 MB/s · min 2,07 · maks 10,04
(mencakup startup proses + auth + probe baca-balik, jadi ini BATAS BAWAH laju unggah)
```

### 4c. Rasio (item 3c)

**VM → GCS ≈ 4,78 MB/s** versus **Mac → GCS ≈ 1,87 MB/s** = **2,6×**. Itu **di bawah** ambang
10× yang kau sebut sebagai argumen untuk memindahkan akuisisi.

Dan yang lebih penting: **angka yang menentukan belum terukur.** Yang relevan untuk memindahkan
akuisisi bukan VM→GCS (satu region, praktis intra-datacenter) melainkan **VM → OKX CDN**, dan
itu **tidak bisa diukur tanpa mengubah startup script lalu me-reset VM** — yang akan
menghentikan perekam ~50 detik. Instruksimu melarang menyentuh perekam, jadi **tidak kuukur**,
dan tidak kutebak. **Kesimpulan item 3c: argumen memindahkan akuisisi ke VM BELUM
terbukti secara terukur.**

### 4d. Byte yang menyeberangi uplink rumah (item 3d)

| skenario | turun ke Mac | naik dari Mac | total/hari |
|---|---|---|---|
| **sekarang** (Mac menarik tape dari GCS, mencerminkan ke HF) | ~290 MB | ~290 MB | **~580 MB** |
| + akuisisi OKX aktif | +430 MB/hari-berkas | +430 MB | +860 MB |
| **kalau pemrosesan pindah ke VM, hanya agregat turun** | footprint 1 menit × ~50 level × 1.440 bar × (vol bid, vol ask) ≈ 144 ribu baris ≈ **~3 MB** | ~3 MB | **~6 MB** |

**≈97× lebih sedikit** pada volume tape hari ini. Angka 3 MB itu perhitungan dari dimensi
(bar × level × 2 kolom × ~20 B), **bukan pengukuran** — ia harus diukur sebelum dipakai
sebagai dasar keputusan.

**Tidak ada apa pun yang dipindahkan di giliran ini** (item 3e).

## 5. Bottleneck, berurutan menurut kontribusi ke wall-clock (item 2e)

1. **I/O jarak-jauh — mendominasi absolut.** 253 s (dan gagal) lewat HF versus 1,64 s lokal.
   Hugging Face terukur **11× lebih lambat dari GCS** dari mesin yang sama, detik yang sama.
2. **Format penyimpanan.** 69× terukur pada operasi baca+agregasi yang sama, dengan berkas
   0,89× lebih kecil.
3. **Cache write + churn pandas.** `_write_bars_cache` → `to_parquet` adalah biaya Python
   terbesar (0,084 s dari 0,196 s), plus 13.330 `deepcopy`.
4. **Agregasi CPU: BUKAN bottleneck.** cProfile hanya melihat 12 % wall-clock; agregasinya
   sudah di DuckDB.

**Konsekuensi yang harus dipatuhi: mengoptimasi CPU pada layer riset tidak relevan.** Yang
berbayar adalah di mana byte-nya berada dan dalam format apa.

## 6. Apa yang dokumen ini TIDAK buktikan

Angka wall-clock berasal dari **satu** panggilan per skenario, bukan distribusi — tidak ada
pengulangan, tidak ada error bar, jadi 1,64 s dan 253 s adalah pengamatan tunggal dan bisa
bergeser. Perbandingan format (69×) mengukur apa yang tiap format memungkinkan, bukan parser
head-to-head, dan datanya satu hari satu venue. Throughput jaringan adalah tiga sampel per
tujuan pada satu sore; OKX sendiri bervariasi 60× di dalam tiga sampel itu, yang berarti
rata-ratanya hampir tidak bermakna dan seharusnya dibaca sebagai "tidak stabil". Angka
VM→GCS adalah batas bawah dan bukan angka yang relevan untuk keputusan pemindahan. Dan waktu
render browser sama sekali belum diukur, jadi klaim "UI bukan bottleneck" **tidak** dibuat di
sini — yang dibuat hanya bahwa agregasi klien ada dan itu temuan.
