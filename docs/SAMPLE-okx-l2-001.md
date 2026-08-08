# SAMPLE-okx-l2-001 — aturan sampel arsip L2 OKX, dideklarasikan SEBELUM unduhan

**Status: DIDEKLARASIKAN. Di-commit sebelum satu byte pun diunduh; riwayat git adalah
buktinya.** Dokumen ini tidak mengandung satu pun angka riset dan tidak boleh dipakai untuk
menghitung apa pun yang prediktif — akuisisi dan verifikasi saja.

## 1. Sumber, dan kenapa ia ada

`docs/DIAG-data-ceiling-001.md` mengukur bahwa OKX menerbitkan arsip **L2 400-level harian
secara keyless** — snapshot penuh **plus update inkremental**, kelas data yang sama dengan
yang direkam perekam Tokyo untuk Binance, tapi **historis** dan karenanya bisa di-backfill.
Rentang terbit: 2023-12-04 sampai T+2.

Diprobe ulang hari ini sebelum aturan ini ditulis [DIUKUR 2026-08-08]: tiga HEAD keyless,
tiga-tiganya `HTTP/2 200` — `2024-03-07` 530.110.527 B · `2025-06-21` 372.061.760 B ·
`2026-07-07` 450.343.514 B. Jalurnya hidup.

## 2. Kerangka sampel — deterministik, tanpa kebebasan memilih

| dimensi | aturan |
|---|---|
| instrumen | `BTC-USDT-SWAP` saja |
| bulan | **2024-01 sampai 2026-07 inklusif** = 31 bulan |
| hari dalam bulan | **tanggal 7 dan tanggal 21** — dua per bulan |
| **N** | **62 hari** |
| urutan akuisisi | kronologis, dalam dua tranche yang dideklarasikan di §2a |
| hari yang hilang | **dihitung, TIDAK diganti** |

Tidak ada satu pun tanggal yang dipilih setelah melihat apa pun. Tanggal 7 dan 21 dipilih
karena membelah bulan mendekati merata dan tidak pernah jatuh pada batas bulan (di mana
sebuah rezim baru dimulai) — dipilih dari kalender, bukan dari data.

**Hari yang hilang tidak diganti.** Mengganti hari 404 dengan tetangganya adalah seleksi:
hari yang hilang di arsip kemungkinan besar berkorelasi dengan hari yang bermasalah di
venue, dan menggantinya diam-diam akan membuat sampel lebih bersih dari kenyataan. Lubang
dicatat sebagai lubang (rail repo: *gaps stay gaps*).

### 2a. Dua tranche, keduanya dideklarasikan sekarang

Biayanya terukur dan besar: rata-rata ~451 MB/hari × 62 = **~28 GB**, yang hampir melipatgandakan
dataset HF (14,75 GB per 2026-08-08). Karena itu akuisisi dipecah — **bukan supaya bisa berhenti
di titik yang menguntungkan setelah melihat data**, melainkan supaya titik berhentinya sudah
tertulis sebelum data terlihat:

* **Tranche A** — tanggal **7** setiap bulan, 31 hari, ~14 GB. Dijalankan lebih dulu.
* **Tranche B** — tanggal **21** setiap bulan, 31 hari, ~14 GB. Menyusul.

Kalau tranche B tidak jadi dijalankan (keputusan biaya/waktu azul), sampelnya tetap
**tanggal 7 setiap bulan, 31 hari, lintas 31 bulan** — kerangka yang tetap deterministik dan
tetap merata, bukan potongan sisa. Setiap analisis di atasnya wajib menyebut tranche mana
yang ada.

## 3. Rezim yang tercakup

31 bulan berturut-turut melintasi: pemulihan/era-ETF 2024, siklus 2025, dan 2026 sampai Juli.
Tidak ada bulan yang dilewati, jadi tidak ada rezim yang bisa hilang lewat pemilihan. Arsip
mulai 2023-12-04, jadi 2023 sengaja **tidak** disertakan — bulan Desember 2023 tidak lengkap
(dua hole terukur: 2023-12-01 dan 2023-12-10) dan memasukkan satu bulan parsial akan membuat
strata bulanan tidak seragam.

## 4. Integritas — apa yang harus lolos sebelum sebuah hari dianggap ada

Per berkas, berurutan, dan kegagalan mana pun menghentikan hari itu:

1. `content-length` dari HEAD **sama dengan** jumlah byte yang benar-benar terunduh.
2. `sha256` dicatat di ledger append-only `reports/okx-l2-ledger.jsonl`.
3. Arsipnya **membuka** — `tar` bisa dibaca dan berisi berkas data yang bisa didekompresi.
4. Diunggah ke HF di bawah prefix **`okx_l2/`** — terpisah dari `vision/`, `data/`, dan
   `depth_tape/`.
5. **Roundtrip**: diunduh kembali dari HF dan `sha256`-nya dicocokkan. Unggahan yang tidak
   melempar exception bukan bukti.
6. Salinan lokal dihapus setelah roundtrip lolos. Disk Mac hanya punya 17 GiB bebas
   [DIUKUR 2026-08-08] dan repo ini sudah dua kali kena ENOSPC — puncak pemakaian lokal
   dijaga **satu berkas** (~0,5 GB), bukan satu sampel.

## 5. Kontrol yang WAJIB dijalankan sebelum satu angka riset pun dihitung

Dideklarasikan di sini, sebelum berkasnya ada, supaya tidak bisa disetel setelah melihat
hasil.

### 5a. Kontrol internal — rekonstruksi vs snapshot berikutnya (deterministik)

Bangun book dari satu baris `action:"snapshot"`, terapkan setiap `action:"update"` sesudahnya,
lalu **bandingkan dengan baris `snapshot` berikutnya** pada saat snapshot itu terjadi.

**PASS = cocok PERSIS** (harga dan ukuran identik pada 20 level teratas kedua sisi) untuk
**setiap** pasangan snapshot dalam hari yang diuji. Ini deterministik, bukan statistik, jadi
ambang 100 % sah dan tidak butuh error bar. Kalau tidak 100 %: itu **temuan yang dilaporkan
apa adanya**, bukan ambang yang diturunkan.

Batasnya jujur: snapshot dan update berasal dari **berkas yang sama dan produsen yang sama**,
jadi kontrol ini membuktikan **semantik update-ku benar**, bukan bahwa arsip OKX
merepresentasikan book OKX yang sebenarnya.

### 5b. Kontrol semi-independen — trade harus berada di dalam spread

Arsip **trades** OKX adalah pipeline dan berkas yang berbeda. Setiap trade pada waktu `t`
dicocokkan dengan book hasil rekonstruksi pada update terakhir `≤ t`; harga trade seharusnya
berada di dalam atau tepat pada `[best_bid, best_ask]`.

Ini statistik, bukan deterministik, jadi **tidak ada ambang yang kudeklarasikan sendiri** —
kelas kesalahan itu sudah tiga kali kena di `PREREG-markout-001`. Sebagai gantinya, daya
pisahnya diukur lewat **kontrol negatif**: pipeline yang sama dijalankan ulang dengan urutan
update **diacak**. Vonis dibaca dari **jarak antara keduanya**, dan kedua angkanya dilaporkan
berdampingan:

* rekonstruksi utuh → distribusi pelanggaran (dalam tick)
* rekonstruksi rusak (update teracak) → distribusi pelanggaran

Kalau keduanya tidak terpisah jelas, kontrol ini **tidak punya daya pisah** dan harus
dinyatakan begitu — bukan dipakai sebagai bukti.

### 5c. Batas yang harus dinyatakan sebelum riset apa pun

**Tidak ada tape BBO historis yang benar-benar independen untuk OKX.** Kanal `bbo-tbt` adalah
stream live tanpa replay; candle dan trades tidak memuat quote. Jadi kebenaran rekonstruksi
book OKX hanya bisa diuji sejauh §5a (internal) dan §5b (semi-independen lewat trades).
**Ini batas permanen dari data yang tersedia, bukan kemalasan instrumen**, dan setiap klaim
riset yang berdiri di atas book OKX hasil rekonstruksi mewarisi batas ini.

## 6. Yang TIDAK boleh dihitung di giliran akuisisi ini

Tanpa pengecualian: tidak ada OBI, tidak ada markout, tidak ada imbalance, tidak ada apa pun
yang memetakan keadaan book ke return berikutnya. Look counter naik di kolom **DIAGNOSTIC**
saja. Setiap pemakaian prediktif atas sampel ini butuh PREREG-nya sendiri dengan cap
`N_trials`-nya sendiri, dan §5c wajib dikutip di dalamnya.
