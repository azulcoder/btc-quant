# PLAN-microstructure-001 — apa gunanya estimator ini, dan urutan yang benar

**Status:** rencana. **Nol** look — seluruh angka di sini dari simulasi dan dari pengukuran yang
sudah ada di repo, tidak ada partisi baru yang dibuka.
**Prasyarat:** `btcquant/hasbrouck.py` (E1–E5, E9–E11, interval teridentifikasi) sudah ada dan
terkontrol; `docs/VERIFY-hasbrouck-extraction.md` memegang matematikanya.

## 1. Pertanyaan yang sebenarnya dijawab

Estimator mikrostruktur bukan strategi. Ia tidak akan pernah lolos gerbang promosi karena ia tidak
menghasilkan return. Nilainya di repo ini ada di satu tempat yang sangat spesifik, dan tempat itu
sudah lama bolong:

> **Seluruh model biaya repo ini ASUMSI.** `btcquant/backtest.py` memakai
> `cost_bps = 10.0` + `slippage_bps = 2.0` sebagai default *hardcoded*, tidak pernah diukur.
> Tidak ada tabel fee venue, tidak ada model impact, tidak ada estimasi slippage di mana pun
> (dicek 2026-08-06: nol hit di `btcquant/`).

Semua yang bertumpu pada angka itu mewarisi asumsinya: setiap backtest, gerbang cost-drag
(jangkar 188 bps/tahun), dan aritmetika `p*` di `PREREG-scalp-001` yang menolak premis scalping.
Keluarga Roll mengukur **effective spread dari harga transaksi saja** — dan harga transaksi ada
sepanjang **6,587 tahun** arsip aggTrades, bukan hanya 26 hari data buku.

Itu satu-satunya alasan yang cukup kuat untuk melanjutkan. Kalau ternyata tidak bisa mengukurnya,
pekerjaan ini berhenti, dan itu hasil yang sah.

## 2. Batas yang menentukan segalanya: berapa data yang dibutuhkan [DIUKUR]

Diukur pada simulasi yang dikalibrasi ke pengukuran repo ini sendiri — tick `0,1 USDT` pada median
mid `$63.719,75`, spread median **satu tick** (`docs/EDA-execution-001.md`, grep `physical anchor`),
volatilitas per-trade `σ_u ≈ 2,65e−05` dari ~888k trade/hari:

| n trade | ≈ hari | `sd(γ̂₁) / |γ₁|` | ABSTAIN | lebar interval / c |
|---|---|---|---|---|
| 400.000 | 0,5 | **3,61** | 3/12 | 2,40 |
| 4.000.000 | 4,5 | 1,14 | 1/12 | 1,53 |
| 40.000.000 | 45 | 0,36 | 0/4 | 1,74 |

Sinyal yang diestimasi adalah `−γ₁ = c·s ≈ 3,1e−13`, sementara `γ₀ ≈ σ²_u ≈ 7,0e−10`. Karena
`sd(γ̂₁) ≈ γ₀/√n`, **derau sampel mengalahkan sinyalnya 3,6× pada setengah hari.**

```
untuk sd(γ̂₁) = 20 % dari sinyal  ->  n ≈ 130.000.000 trade ≈ 147 hari
arsip memegang ~2.400 hari                                  ≈ 16× kebutuhan itu
```

**Tiga konsekuensi, semuanya langsung:**

1. **Estimator ini tidak bisa mengatakan apa pun tentang satu hari.** Bukan karena implementasinya
   lemah — karena sinyalnya memang di bawah derau pada n sebesar itu.
2. **Arsipnya cukup, dengan margin 16×.** Jadi pertanyaannya bukan "bisa atau tidak", melainkan
   "pada agregasi berapa".
3. **Subsampling HARIAN salah unit untuk instrumen ini**, dan itu mengoreksi rekomendasi dokumen
   ekstraksi (`EXTRACT-hasbrouck-s9-s12.md` §B.2 + RULE-EXTRACT-7, yang menyebut hari sebagai
   default alami). Pada BTCUSDT perp satu blok harian adalah derau murni. Blok harus ≥ ~150 hari,
   yang berarti ~16 blok dari seluruh arsip dan `SE = sd/√16`. Deklarasikan itu sebelum mengukur.

**Diskretisasi tick BUKAN kendala pengikat.** Dugaan awalku salah: pembulatan ke grid `0,1 USDT`
menyumbang `−δ²/12 = 2,05e−13` ke `γ₁`, yaitu 0,67× sinyal sejati, tapi efeknya pada interval
hanya menaikkan overstatement Roll dari 2,56× ke 2,80×. Derau random-walk sudah menelan keduanya.
Yang mengikat adalah `n`, bukan grid.

## 3. Mengapa ini TIDAK masuk ke orderflow terminal

Terminal itu live-descriptive (§0.1). Godaannya jelas: taruh readout effective-spread di sana.
**Jangan**, dan alasannya terukur, bukan selera:

- Pada skala intraday (~888k trade/hari) `sd(γ̂₁)` adalah 3,6× sinyal. Panel live akan berkedip
  antara `ABSTAIN` dan angka yang salah 2–3×. Itu instrumen yang menggonggong salah — kelas I.
- **Terminal sudah punya bukunya.** Ia menghitung `spread_bps` langsung dari `depth_snapshots`,
  yang lebih akurat dan tidak butuh asumsi model apa pun.

Pembagian kerjanya justru bersih: **buku untuk live, Roll untuk sejarah.** Keluarga Roll berguna
persis di tempat bukunya tidak ada — arsip 6,587 tahun. Menaruhnya di terminal adalah memakainya
di satu-satunya tempat ia kalah.

## 4. Urutan yang benar

### Langkah 0 — `PREREG-microstructure-001`, sebelum satu partisi pun dibuka

RULE-EXTRACT-5 mengikat. Yang harus dideklarasikan, dan semuanya sudah diketahui:

| pilihan | catatan |
|---|---|
| orde truncation AR `K` | kriteria pemilihan (AIC/BIC/uji autokorelasi) ditulis di muka |
| `k_min` dan grid `k` untuk E4 | intersep saja, tidak pernah slope (VERIFY §3b) |
| basis waktu | event-time vs kalender — mengukur di keduanya lalu memilih adalah dua look |
| unit subsampel | **≥150 hari**, bukan harian — §2 di atas |
| jendela funding 00:00/08:00/16:00 UTC | exclude / adjust / batasi `k` — ketiganya sah, memilih setelah melihat tidak |
| hari ber-`recorded-damage` | ada 3 entri; perlakuannya dideklarasikan, bukan ad hoc |
| jendela sampel | dan `N_trials` cap eksplisit |

### Langkah 1 — angka nyata pertama adalah KONTROL POSITIF, bukan hasil

Ini yang membuat rencana ini layak dikerjakan, dan repo ini sudah memegang kontrolnya:

> Estimasi effective spread **dari trade saja** pada 26 hari yang juga punya `depth_snapshots`,
> lalu bandingkan dengan spread yang **diukur langsung dari buku**: 0,0156 bps atas 859.264
> snapshot (`EDA-microstructure-001.md` §2a), dikonfirmasi 0,0157 bps oleh dua rute lain.

Kalau estimator trades-only mereproduksi angka buku itu, sebuah instrumen biaya **6,587 tahun**
tervalidasi terhadap pengukuran buku 26 hari — dua rute yang sama sekali berbeda atas satu
kuantitas. Kalau tidak, pekerjaan ini berhenti di sini dan itu hasil yang sah.

⚠️ 26 hari ≈ 23 juta trade, di bawah ambang 147 hari. Jadi kontrolnya harus dinyatakan sebagai
**interval**, dan kriteria lulusnya adalah "interval trades-only memuat 0,0156 bps", bukan
kesamaan titik. Ambang itu dideklarasikan di Langkah 0, sebelum angkanya ada.

### Langkah 2 — §12 lalu E6

E6 (VAR harga-trade) adalah yang paling bernilai dari yang belum ada: di situlah `λ` dan `R²_w`
hidup, dan **ia hanya butuh `q_t`**, yang berasal dari `isBuyerMaker` — jadi ia ada di keluarga
6,587 tahun, bukan keluarga 1,8 % yang butuh buku. Itu poin yang mudah terlewat: E6 **tidak**
butuh depth.

Tapi §12 (VMA/VAR, IRF, Cholesky) harus lebih dulu, karena tanpa mesinnya implementasi E6 akan
menebak. RULE-EXTRACT-8 mewajibkan mengevaluasi seluruh `2ⁿ` himpunan bagian untuk batas
dekomposisi; RULE-EXTRACT-9 mewajibkan test yang mengunci `Σθ_jΩθ_j′ ≠ θ(1)Ωθ(1)′`, dan test itu
**tidak bisa ada sebelum E6 ada**.

### Langkah 3 — E7, paling akhir

Information share perp vs spot adalah pertanyaan paling bernilai dan paling belum terjawab, tapi
juga yang paling banyak pilihan penelitinya. Basis perp-spot tidak stasioner murni — funding
menggesernya — jadi vektor kointegrasi `(1, −1)` mungkin salah spesifikasi dan itu **harus diuji,
bukan diasumsikan**.

## 5. Apa yang akan membatalkan rencana ini

Ditulis sekarang, sebelum ada angka:

- **Kontrol positif Langkah 1 gagal** — interval trades-only tidak memuat 0,0156 bps. Maka keluarga
  Roll tidak mengukur spread pada instrumen ini, dan Langkah 2–3 tidak dikerjakan.
- **Gerbang orde MA(1) menolak arsip secara sistematis.** Order flow crypto berautokorelasi kuat
  (order splitting), jadi `Δp` mungkin bukan MA(1) sama sekali. Kalau begitu E2/E3/E5 dan interval
  gugur, dan yang tersisa hanya E4 (intersep) dan E9 — yang tidak memberi `c`.
- **Effective spread terukur ternyata tidak mengubah kesimpulan apa pun.** Kalau ia mendarat dekat
  asumsi 10+2 bps yang sudah dipakai, maka mengukurnya benar tapi tidak berguna, dan itu harus
  dikatakan apa adanya.

## 6. Yang TIDAK direkomendasikan

- **Jangan** taruh di terminal (§3).
- **Jangan** hitung per-hari lalu rata-ratakan (§2 — tiap blok derau murni).
- **Jangan** laporkan Roll sebagai estimasi titik. Ia rata-rata geometrik `c` dan `c+λ`, jadi
  overstate half-spread dan understate spread sekaligus. Yang benar adalah interval `[E5, Roll]`,
  dan interval itu gratis.
- **Jangan** kerjakan §15 (PIN). Asumsi hitungan trade-nya bentrok dengan agregasi aggTrades, jadi
  angkanya akan berarti sesuatu yang lain tanpa memberi tahu.
