# BOOK-001 — spread terkuotasi dari `depth_snapshots`, per venue, bisa dijalankan ulang

**Look: diagnostik provenance.** Mengukur properti tape; tidak ada spesifikasi prediktif yang
dievaluasi, tidak ada estimator dari `btcquant/hasbrouck.py` yang diimpor atau dijalankan.
**Skrip:** `scripts/measure_book_spread_001.py` · **mesin:** `reports/book-spread-001.json`
**Semua angka `[DIUKUR]`**, dijalankan 2026-08-07 atas slice beku `2026-07-05..2026-08-03`.

**Mengapa ini ada.** Setiap perbandingan "trades-only versus buku" di repo ini membagi dengan
`0,0156 bps`. `docs/DIAG-venue-filter-audit.md` §2 mencari kueri di baliknya dan **tidak
menemukannya** — tidak ada skrip yang menghitungnya, tidak ada SQL yang mengutipnya. Ia jangkar
tanpa pemeriksa. Dokumen ini pemeriksanya.

**Mengapa tanpa PREREG terpisah.** Seluruh pilihan bebasnya sudah terkunci sebelum ada angka:
tiga pembobotan dilaporkan **berdampingan** bukan dipilih salah satu, semua kuantil dilaporkan,
`√(E[c²])` wajib, dan kontrol positif dilarang menyetel definisi agar cocok. Memilih salah satu
pembobotan **setelah** melihat hasilnya akan butuh PREREG; melaporkan ketiganya tidak.

---

## Kontrol positif: **REPRODUKSI**

| jalur | nilai | selisih dari 0,0156 bps |
|---|---:|---:|
| `binancef/BTCUSDT` (i) per snapshot, `2×p50` | **0,01561 bps** | **+0,00001 (+0,1 %)** |
| `binancef/BTCUSDT` (ii) tertimbang waktu, `2×p50` | 0,01561 bps | +0,1 % |
| `binancef/BTCUSDT` (iii) tertimbang trade, `2×p50` | 0,01561 bps | +0,1 % |
| `bybit/BTCUSDT` (i) per snapshot, `2×p50` | 0,01562 bps | +0,1 % |

Jangkarnya sekarang punya kueri yang bisa dijalankan ulang. **`c_book = 0,0078 bps` berlaku
sebagai MEDIAN** — dan hanya sebagai median; lihat bagian berikutnya.

## B1/B2 — `c` (separuh-spread) dalam TICK, per venue, tak pernah di-pool

Tick **diukur** sebagai selisih positif terkecil antar level harga yang teramati, bukan
diasumsikan. Ketiganya keluar **0,1000**.

| venue | tick | mid rujukan | 1 tick |
|---|---:|---:|---:|
| `binancef/BTCUSDT` | 0,1000 | 64.069,88 | 0,01561 bps |
| `bybit/BTCUSDT` | 0,1000 | 64.023,30 | 0,01562 bps |
| `okx/BTC-USDT-SWAP` | 0,1000 | 64.022,05 | 0,01562 bps |

| venue | pembobotan | n | p25 | p50 | p75 | p95 | p99 | mean | **√(E[c²])** |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| binancef | (i) per snapshot | 1.442.013 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5066 | **0,7621** |
| binancef | (ii) tertimbang waktu | 1.442.013 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5055 | **0,6768** |
| binancef | (iii) tertimbang trade | 3.340.826 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5417 | **1,5762** |
| bybit | (i) per snapshot | 1.036.632 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5040 | 0,5665 |
| bybit | (ii) tertimbang waktu | 1.036.632 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5113 | 0,6934 |
| bybit | (iii) tertimbang trade | 2.496.336 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5114 | 0,6620 |
| okx | (i) per snapshot | 1.162.817 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5024 | 0,6253 |
| okx | (ii) tertimbang waktu | 1.162.817 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5014 | 0,5771 |
| okx | (iii) tertimbang trade | 1.524.719 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5000 | 0,5091 | 0,7377 |

**Kuantil p25 sampai p99 semuanya 0,5000 tick, di ketiga venue, di ketiga pembobotan.** Bukunya
selebar satu tick dari kuartil bawah sampai persentil 99. Distribusinya degenerate sebagai
kuantil, dan **`√(E[c²])` adalah satu-satunya statistik di tabel ini yang memisahkan apa pun.**

Dalam bps, statistik yang Roll targetkan:

| venue | `√(E[c²])` per snapshot | tertimbang waktu | **tertimbang trade** |
|---|---:|---:|---:|
| binancef | 0,01189 bps | 0,01056 bps | **0,02460 bps** |
| bybit | 0,00885 bps | 0,01083 bps | 0,01034 bps |
| okx | 0,00977 bps | 0,00901 bps | 0,01152 bps |

## B3 — rasio (iii)/(i), apa adanya

| venue | p50 | mean | `√(E[c²])` |
|---|---:|---:|---:|
| binancef | 1,000 | 1,069 | **2,068** |
| bybit | 1,000 | 1,015 | 1,169 |
| okx | 1,000 | 1,013 | 1,180 |

Jeda antar snapshot, terukur: p50 **1.034 ms** (binancef), 1.001 ms (bybit), 1.000 ms (okx);
p99 1.215 / 1.240 / 1.000 ms; **maks ~5,4×10⁷ ms** di ketiganya — yaitu lubang feed ~15 jam,
bukan kadensi.

## B4 — kualitas data

| venue | raw | sisi kosong | crossed/locked | dipakai | hari |
|---|---:|---:|---:|---:|---:|
| binancef | 1.442.013 | 0 (0,0000 %) | 0 (0,0000 %) | 1.442.013 | 30 |
| bybit | 1.036.632 | 0 | 0 | 1.036.632 | 28 |
| okx | 1.162.817 | 0 | 0 | 1.162.817 | 28 |

Nol snapshot bersilang, nol sisi kosong. `coinbase` tidak punya `depth_snapshots` sama sekali
dan karena itu absen dari seluruh dokumen ini.

## B5 — klaim "melebar episodik 63–753 tick", diuji terhadap buku

| venue | episode | snapshot di dalam | durasi median | durasi maks | trade di dalam |
|---|---:|---:|---:|---:|---|
| binancef | 5 | 5 | 0 ms | 0 ms | 864 / 3.340.886 = **0,0259 %** |
| bybit | 2 | 2 | 0 ms | 0 ms | 292 / 2.496.451 = 0,0117 % |
| okx | 2 | 2 | 0 ms | 0 ms | 125 / 1.524.719 = 0,0082 % |

Subset terdeklarasi `2026-07-30, -31, 2026-08-01, 2026-08-03`. Ambang `≥ 63 tick` adalah batas
bawah klaim yang diuji.

**Batas instrumen, terukur bukan diduga:** kadensi snapshot p50 adalah **~1.000 ms**, sementara
run alternasi yang mendasari klaim itu berlangsung **1 ms** (`DIAG-venue-filter-audit.md` §5b:
39 trade, 2 distinct `ts_ms`, rentang 1 ms). Satu snapshot per detik **tidak bisa melihat**
peristiwa satu milidetik kecuali kebetulan jatuh di dalamnya. Angka 0,0259 % di atas karena itu
adalah pernyataan tentang apa yang **tertangkap snapshot**, bukan tentang seberapa sering buku
melebar.

## B6 — error bar, block bootstrap dengan blok = satu hari, 400 resample

| venue | p50 | mean | `√(E[c²])` | blok |
|---|---|---|---|---:|
| binancef | 0,5000 ± 0,0000 | 0,5066 ± 0,0010 | **0,7579 ± 0,0539** | 30 |
| bybit | 0,5000 ± 0,0000 | 0,5040 ± 0,0004 | 0,5661 ± 0,0080 | 28 |
| okx | 0,5000 ± 0,0000 | 0,5024 ± 0,0004 | 0,6184 ± 0,0425 | 28 |

SE nol pada p50 bukan presisi tak hingga — ia konsekuensi distribusi yang degenerate: setiap
resample memberi median yang sama karena setiap kuantil sampai p99 adalah 0,5000 tick.

## B7 — invariansi jam, diverifikasi ulang

`p50` `c` per jam UTC, ketiga venue: **0,5000 tick di seluruh 24 jam**, rentang antar-jam
**0,0000 tick**. Klaim invariansi jam di `EDA-microstructure-001.md` §2a **bertahan**, dan
sekarang jelas mengapa ia degenerate: ia mengukur median dari besaran yang bernilai satu tick di
mana-mana.

---

## Apa yang dokumen ini TIDAK lakukan

Tidak menjalankan Roll di venue mana pun, tidak menyentuh estimator, tidak menghitung ulang rasio
mana pun. Penyebut untuk `√(E[c²])` sekarang ada dan tercetak di atas; memakainya untuk
menghitung ulang rasio 8×–30× adalah pekerjaan berikutnya, bukan pekerjaan ini.
