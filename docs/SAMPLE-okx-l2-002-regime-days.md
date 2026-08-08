# SAMPLE-okx-l2-002 — aturan pemilihan hari untuk uji kontrol v2 lintas rezim

**Status: DIDEKLARASIKAN, belum dijalankan.** Di-commit sebelum satu berkas L2 diperiksa dan
sebelum satu hari pun dipilih; riwayat git adalah buktinya. Tidak ada angka prediktif di
sini maupun di hasilnya.

## 1. Pertanyaan yang ingin dijawab

Agreement 97,9 % dari kontrol v2 berasal dari **satu hari** (`2024-01-07`) dan **prefiks
24 MB**. `docs/DIAG-control-a-v2-001.md` menyimpulkan H2 — ketidakcocokan lahir dari instan
pembandingan yang jatuh di tengah event diff atomik. Kalau H2 benar, ia membuat **prediksi
yang bisa dipatahkan**:

> Book yang bergerak lebih cepat akan memberi agreement lebih RENDAH, karena semakin banyak
> yang berubah di dalam satu event.

Kalau agreement **tidak** berkorelasi dengan volatilitas, H2 lemah dan penyebabnya harus
dicari lagi. Kalau **berkorelasi**, itu bukan sekadar konfirmasi — itu **batas yang harus
dibawa ke setiap PREREG berikutnya**, karena derau yang berkorelasi dengan volatilitas
tidak berperilaku seperti derau biasa (§5).

## 2. Aturan pemilihan — deterministik, sumber volatilitas INDEPENDEN

Kerangka: 31 hari tranche A yang sudah dideklarasikan di `docs/SAMPLE-okx-l2-001.md`
(tanggal 7 tiap bulan, 2024-01..2026-07).

**Ukuran volatilitas:** `realized_range = (high − low) / open` dari **kline harian 1d
BTCUSDT Binance** — sumber yang sepenuhnya di luar arsip L2 OKX yang sedang diuji. Dipilih
justru supaya pemilihan hari tidak menyentuh data yang akan dinilai.

**Seleksi, per tahun kalender (2024, 2025, 2026):**

| slot | aturan |
|---|---|
| MAX | hari tranche A tahun itu dengan `realized_range` **tertinggi** |
| MIN | hari tranche A tahun itu dengan `realized_range` **terendah** |
| MED | hari tranche A tahun itu dengan `realized_range` **median** (indeks `n//2` setelah diurut menaik) |

**N = 3 slot × 3 tahun = 9 hari.** Melebihi minimum 6 yang diminta, dan menjangkau rentang
volatilitas terluas yang tersedia **by construction**, bukan by inspection.

**Urutan akuisisi, dideklarasikan sekarang:** seluruh MAX (2024, 2025, 2026), lalu seluruh
MIN, lalu seluruh MED. Kalau proses terputus di tengah, himpunan yang tersisa tetap
seimbang antar-tahun dan tetap mencakup kedua ekstrem — titik berhentinya sudah tertulis
sebelum hasilnya terlihat.

**Hari yang hilang di arsip dihitung, tidak diganti** — sama seperti aturan induknya.

## 3. Cakupan pemrosesan — dinyatakan di muka

- **Hari penuh (bukan prefiks) untuk minimal 2 hari**, sebagaimana diminta. Kontrolnya
  ditulis ulang menjadi **streaming** (satu baris pada satu waktu, book dipelihara
  inkremental, tidak ada daftar baris yang ditahan di memori), jadi hari penuh ~2,9 GB
  terdekompresi diproses dalam memori konstan.
- Kalau sebuah hari **tidak** bisa diproses penuh, itu dilaporkan **per hari dengan
  alasannya** — tidak boleh ada hari yang diam-diam turun ke prefiks.

## 4. Yang dilaporkan

Per hari: agreement **per level** di 4 kedalaman (1, 5, 20, 100), jumlah pasangan snapshot,
**dan `realized_range` hari itu di kolom yang sama**. Lalu korelasi antara agreement dan
`realized_range` — Spearman (rank, tahan outlier) dan Pearson, keduanya dengan `n`.

Kontrol negatif (update teracak) dijalankan pada **satu hari** dari himpunan ini sebagai
jangkar daya pisah; menjalankannya di sembilan hari hanya mengulang hal yang sama dengan
biaya sembilan kali lipat.

## 5. Kalau korelasinya ADA — kalimat yang wajib masuk ke PREREG berikutnya

Dideklarasikan **sekarang**, sebelum angkanya ada, supaya tidak bisa dilunakkan setelah
melihat hasil:

> Derau rekonstruksi book **berkorelasi dengan volatilitas**. Ini **BUKAN** attenuation bias
> yang konservatif. Derau yang tidak berkorelasi dengan sinyal hanya melemahkan estimasi ke
> arah nol — aman, karena temuan positif tetap temuan. Derau yang **berkorelasi dengan
> volatilitas** bisa **menciptakan** hubungan yang tidak ada pada setiap besaran yang juga
> berhubungan dengan volatilitas — dan hampir semua besaran mikrostruktur begitu (spread,
> imbalance, kedalaman, intensitas trade). Setiap PREREG yang memakai ukuran book hasil
> rekonstruksi wajib mengutip batas ini dan menyatakan bagaimana ia dikendalikan.

## 6. Batas yang sudah diketahui sebelum dijalankan

- **Tape Binance hanya punya SATU hari** (`2026-08-08`; perekamnya baru mulai). Perbandingan
  venue karena itu **tidak bisa** berdiri di dua hari berbeda untuk Binance. Penggantinya,
  dinyatakan sebagai pengganti: **segmentasi per jam di dalam satu hari itu**, dengan
  `realized_range` per jam sebagai sumbu volatilitasnya. Ini menguji prediksi H2 yang sama
  pada Binance, tetapi **dalam-hari**, dan tidak boleh dibaca sebagai lintas-hari.
- Semua hari OKX yang tersedia saat aturan ini ditulis ada di tahun 2024 saja; slot 2025 dan
  2026 butuh akuisisi, yang dijalankan **setelah** aturan ini di-commit.
