# PREREG-microstructure-001 — kontrol positif: effective spread dari trade saja

**Status:** DIJALANKAN dan DITUTUP. Vonis **INDETERMINATE**, premis MA(1) ditolak datanya —
lihat RESULT di bawah. Deklarasi di §0–§8 ditulis dan di-commit sebelum satu angka pun ada (`9d7930e`).
**Ditulis:** 2026-08-06. **`N_trials` cap: 1.** **Look accounting: 1 diagnostic look.**
**Prasyarat:** `btcquant/hasbrouck.py`; rencana di `docs/PLAN-microstructure-001.md` §4 Langkah 1.

## 0. Mengapa ini yang pertama disentuhkan ke data nyata

Repo ini menuju sistem live, dan seluruh model biayanya asumsi — `backtest.py` hardcode
`cost_bps = 10.0 + slippage_bps = 2.0`, tanpa satu pun pengukuran. Untuk sistem live itu cacat
**blocking**, bukan catatan. Keluarga Roll mengukur effective spread dari harga transaksi saja,
yang ada sepanjang arsip; tapi sebelum angkanya boleh dipercaya, instrumennya harus mereproduksi
sesuatu yang sudah diketahui lewat rute yang sama sekali berbeda.

Rail repo: **angka pertama dari instrumen baru adalah KONTROL, bukan hasil.**

## 1. Pertanyaan, dalam satu kalimat falsifiable

> Apakah interval teridentifikasi untuk `c`, dihitung **dari aggTrades saja**, memuat separuh
> spread yang sudah diukur **langsung dari order book** pada jendela yang sama?

## 2. Target pembanding — dan dua koreksi skala yang wajib

Pengukuran buku yang jadi target: `docs/EDA-microstructure-001.md` §2a (grep
`Confirmed over 26 days, not one`) — **859.264 snapshot, p50 = 0,0156 bps**.

Dua hal yang mudah salah dan karena itu dikunci di sini, sebelum ada angka:

| jebakan | koreksi yang dideklarasikan |
|---|---|
| Buku diukur **hanya pada jam UTC 12–23**, bukan hari penuh. Membandingkannya dengan estimasi 24 jam adalah kelas D (skala tak sepadan). | Estimator trades-only dijalankan pada **jam UTC 12–23 saja**, jendela identik. |
| `0,0156 bps` adalah **spread penuh**; `c` adalah **separuh**-spread. | Target = `c_book = 0,0156 / 2 =` **`0,0078 bps`**. |

Catatan ketiga, dideklarasikan sebagai batas interpretasi dan bukan sebagai koreksi: buku
mengukur spread **terkuotasi**, Roll mengukur spread **efektif** (yang benar-benar dibayar trade).
Keduanya hanya berimpit kalau trade dieksekusi di touch. Pada buku selebar satu tick itu masuk
akal, tapi ia asumsi — `[DIASUMSIKAN]`, dan disebut lagi di vonis apa pun.

## 3. Data — persis, dan alasannya

- **Sumber:** tick store terekam (`data/date=*` di HF), **bukan** arsip `vision/`. Alasan: buku
  dan trade harus berasal dari berkas hari yang sama supaya benar-benar like-for-like.
- **Tanggal:** 26 partisi yang sama dengan EDA §2a, di dalam exploration slice yang dibekukan
  `2026-07-05..2026-08-03`. **Di luar LockBox** (`2026-08-05 01:00Z` ke atas) dan di luar
  karantina `2026-08-04`. Tidak ada bacaan berbiaya-LockBox di sini.
- **Dikecualikan sebelum melihat apa pun:**
  - `2026-07-13` — gagal baca ZSTD, sudah tercatat di EDA §2a. Dikecualikan dan **dihitung**.
  - setiap tanggal yang punya entri di `reports/recorded-damage.json`. Hari rusak punya sifat
    statistik berbeda karena kerusakan, bukan karena pasar.
- **Dedup wajib:** `DISTINCT (exchange, symbol, trade_id)` sebelum apa pun dihitung. Store terekam
  punya cacat duplikat terukur (979 baris pada satu hari; `docs/STATUS.md` §5c-bis), dan duplikat
  akan merusak `γ̂₁` secara langsung.

## 4. Estimator dan agregasi — dideklarasikan penuh

- **Estimator:** `hasbrouck.identified_interval_c` — interval `[E5, Roll]`, **bukan** estimasi
  titik. Roll sendiri adalah rata-rata geometrik `c` dan `c+λ`, jadi sebagai titik ia overstate
  half-spread dan understate spread sekaligus.
- **Basis waktu:** **event time** (urutan trade), bukan waktu kalender. Diukur di keduanya lalu
  memilih adalah dua look.
- **Urutan:** menurut `trade_id` (aggTradeId) menaik, di dalam satu tanggal.
- **Segmentasi:** deret dipecah pada **diskontinuitas `trade_id`** dan pada **batas jam**. Pasangan
  lag-1 tidak pernah menyeberangi celah id maupun batas jam — menyeberanginya adalah splice, dan
  `γ₁` adalah pernyataan tentang kebersebelahan.
- **Agregasi lintas hari:** **momen yang dikumpulkan, bukan deretnya.** `γ̂₀` dan `γ̂₁` dihitung
  per-segmen, lalu dirata-rata dengan bobot jumlah pasangan. Menyambung deret lintas hari akan
  menciptakan kebersebelahan yang tidak pernah ada.
- **Harga:** log harga transaksi. Satuan keluaran bps (`1e-4`).

**Mengapa agregasi ini perlu, dinyatakan sekarang:** pada ~444k trade per hari-jendela,
`sd(γ̂₁)` sekitar 3,4× sinyalnya (`docs/PLAN-microstructure-001.md` §2), jadi estimasi harian
sebagian besar derau dan sekitar sepertiga akan keluar positif. Yang dikumpulkan harus momennya.

## 5. Kriteria — ditulis sebelum angkanya ada

| keluaran | vonis | artinya |
|---|---|---|
| interval memuat **0,0078 bps** | **PASS** | Instrumen trades-only mereproduksi pengukuran buku lewat rute independen. Keluarga Roll boleh dipakai di arsip. |
| interval seluruhnya **di atas** target | **FAIL-HIGH** | Roll overstate. Konsisten dengan order flow berautokorelasi, yang sumbernya sendiri peringatkan. Bukan bug — batas pemakaian. |
| interval seluruhnya **di bawah** target | **FAIL-LOW** | Tidak ada mekanisme yang diketahui memprediksi ini. Kalau terjadi, instrumen atau datanya salah, dan track berhenti sampai sebabnya ketemu. |
| `γ̂₁` gabungan **≥ 0** | **INDETERMINATE** | Sinyalnya tidak terdeteksi pada n ini. Bukan bukti tidak ada spread. Semantik sama dengan vonis PBO. |

**Tidak ada penyetelan setelah melihat.** Kalau hasilnya INDETERMINATE, respons yang sah hanyalah
menambah n (lebih banyak hari) **dalam PREREG baru dengan look-nya sendiri** — bukan mengubah
estimator, jendela, atau kriteria di sini.

## 6. `N_trials` dan akuntansi look

- **`N_trials` = 1.** Satu spesifikasi, dideklarasikan lengkap di atas.
- **1 diagnostic look**, dicatat di `docs/EDA-microstructure-001.md` pada commit yang sama dengan
  hasilnya. Varian apa pun yang dicoba setelah ini — orde lain, jendela jam lain, sumber `vision`
  alih-alih terekam, basis waktu kalender — adalah look terpisah dan butuh PREREG-nya sendiri.
- Tidak ada return yang diskor, jadi tidak ada tambahan ke kolom predictive.

## 7. Kriteria kill untuk track ini

Track microstructure **berhenti** kalau salah satu terjadi:

1. **FAIL-LOW.** Tidak ada mekanisme yang menjelaskannya; berarti ada yang salah lebih dulu.
2. **Gerbang orde MA(1) menolak data secara sistematis** pada mayoritas segmen. Kalau `Δp` bukan
   MA(1), maka E2/E3/E5 dan intervalnya gugur, dan yang tersisa (E4 intersep, E9) tidak memberi
   `c` sama sekali — jadi tidak ada model biaya yang bisa dibangun dari sini.
3. **PASS tapi tidak berguna:** interval memuat target **dan juga** memuat asumsi 10+2 bps yang
   sudah dipakai. Maka pengukurannya benar tapi tidak mengubah keputusan apa pun, dan itu harus
   dikatakan apa adanya alih-alih dilaporkan sebagai kemenangan.

## 8. Apa yang TIDAK dijawab oleh ini

- Bukan estimasi slippage. `c` adalah separuh-spread pada ukuran yang dieksekusi, bukan biaya
  memindahkan klip besar. Impact adalah E6, yang belum ada.
- Bukan model fee. Fee venue adalah tarif terpublikasi, terpisah dari spread.
- Bukan pernyataan tentang periode di luar exploration slice. Slice-nya beku dan sempit; arsip
  6,587 tahun belum disentuh dan butuh PREREG-nya sendiri.

---

# RESULT — dijalankan 2026-08-07, vonis **INDETERMINATE**, premis MA(1) **DITOLAK DATANYA**

`scripts/prereg_microstructure_001.py`, dijalankan sebagaimana dideklarasikan.
Hasil mesin: `reports/prereg-microstructure-001-result.json`. **1 diagnostic look**, dicatat di
`docs/EDA-microstructure-001.md` pada commit yang sama.

## Yang terjadi

Kontrol atas kode pooling-nya sendiri lewat lebih dulu: jalur momen yang ditulis untuk
menggabungkan lintas hari cocok dengan `hasbrouck.identified_interval_c` sampai `1e-15` pada
seri simulasi. Jadi kalau hasilnya aneh, bukan kode penggabungnya.

Dari 30 tanggal di slice, 2 dikecualikan sesuai deklarasi (`2026-07-13` gagal baca ZSTD,
`2026-08-03` punya entri recorded-damage), 5 tidak punya satu pun trade di jendela jam
(`2026-07-17`, `-20`, `-23`, `-24`, `-25`), menyisakan **23 tanggal** dan **13.418.171** `Δp`
dengan **13.417.910** pasangan lag-1.

```
gamma_0 =  9.860485e-10
gamma_1 = -7.027543e-10
sigma2_w = gamma_0 + 2*gamma_1 = -4.194601e-10      <- NEGATIF
rho_1   = gamma_1 / gamma_0    = -0.7127
```

## Vonis, dan mengapa ia lebih keras dari sekadar "kurang data"

**Autokorelasi lag-1 sebuah MA(1) dibatasi di `[-0,5, +0,5]` secara matematis** — nilainya
`θ/(1+θ²)`, yang mencapai ekstremnya di `θ = ∓1`. Nilai terukur `ρ₁ = -0,7127` **berada di luar
rentang yang bisa dihasilkan MA(1) mana pun**. Ini bukan estimasi berisik dari model yang benar;
ini model yang salah.

`σ²_w` gabungan keluar **negatif**, dan varians tidak bisa negatif. Modulnya tidak meng-clip-nya
(RULE-EXTRACT-6), jadi kesalahannya terlihat alih-alih menyamar sebagai angka kecil yang wajar.

Diperiksa per hari pada satu jam penuh (UTC 14), tiga tanggal, dengan gerbang orde:

| tanggal | n | `ρ₁` | `ρ₂` | `ρ₃` | `ρ₄` | gerbang |
|---|---:|---:|---:|---:|---:|---|
| 2026-07-06 | 166.412 | −0,434 | **+0,306** | −0,190 | +0,194 | REJECT |
| 2026-07-14 | 98.621 | −0,706 | **+0,466** | −0,332 | +0,196 | REJECT |
| 2026-07-29 | 111.443 | −0,106 | **+0,519** | −0,014 | +0,479 | REJECT |

**`ρ₂` besar dan positif adalah pembunuhnya.** MA(1) mensyaratkan `ρ_k = 0` untuk `k ≥ 2`. Yang
terukur adalah ACF berosilasi yang meluruh lambat — tanda tangan struktur berorde tinggi, bukan
bid-ask bounce. Gerbang menolak ketiga hari.

Dan besarannya sendiri menutup pintu: `sd(Δp) = 0,121 bps ≈ 7,5 tick`, sementara spread satu tick
berarti `c ≈ 0,5 tick`. Bounce murni akan memberi `ρ₁ ≈ −0,0045`. Yang terukur 100× lebih besar,
jadi autokorelasi negatif ini **bukan** bid-ask bounce.

> **Vonis: INDETERMINATE** menurut §5 — tapi lewat cabang yang tidak dideklarasikan. §5 hanya
> menyebut `γ̂₁ ≥ 0` sebagai jalan ke INDETERMINATE; diskriminan negatif tidak diantisipasi.
> Itu lubang di deklarasiku, dicatat di sini alih-alih ditambal diam-diam.

## Konsekuensi: kill criterion §7.2 aktif

§7.2 menyatakan track berhenti kalau gerbang orde menolak data secara sistematis, karena
E2/E3/E5 dan intervalnya gugur dan yang tersisa tidak memberi `c`. **Kondisi itu terpenuhi.**

Yang **tidak** boleh disimpulkan dari sini: bahwa spread tidak bisa diukur dari trade, atau bahwa
angka buku 0,0156 bps salah. Yang terbukti hanya bahwa **keluarga Roll tidak berlaku pada seri
ini**, dan sebabnya belum diketahui. Dua kandidat yang tidak bisa dipisahkan tanpa penyelidikan
terdeklarasi tersendiri:

1. **Struktur pasar nyata** — order splitting dan sweep multi-level menghasilkan ACF berosilasi
   yang tidak dimuat model Roll.
2. **Artefak data** — sesuatu pada store terekam (urutan, agregasi, interleaving leg) yang membuat
   `Δp` berosilasi. `sd(Δp) = 7,5 tick` per trade layak dicurigai.

Membedakan keduanya adalah PREREG baru dengan look-nya sendiri. **Sampai itu dijawab, tidak ada
model biaya yang boleh dibangun dari keluarga Roll pada instrumen ini.**

## Cacat instrumen yang ditemukan oleh run ini

Tiga dari lima estimator yang bertumpu pada premis MA(1) **tidak memanggil gerbangnya**:
`roll`, `pricing_error_lower_bound`, dan `identified_interval_c`. Hanya `sigma2_w_ma1` dan
`sigma2_w_wold` yang bergerbang. Review adversarial menemukan satu instance dan diperbaiki;
polanya ternyata tiga, dan run ini yang menyingkapnya.

Run ini selamat hanya karena diskriminannya kebetulan negatif. Pada momen yang sedikit berbeda,
`identified_interval_c` akan mengembalikan interval yang percaya diri atas seri yang bukan MA(1).
**Vonisnya tidak berubah** — gerbang menolak ketiga hari yang diuji, jadi jalur bergerbang memberi
ABSTAIN dan jalur tak-bergerbang memberi INDETERMINATE — tapi itu keberuntungan, bukan desain.
