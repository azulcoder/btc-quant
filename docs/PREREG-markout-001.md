# PREREG-markout-001 — apakah sisi agresor memprediksi pergerakan harga berikutnya?

**Status:** ~~DIDEKLARASIKAN, belum dijalankan~~ **CLOSED 2026-08-08 — vonis GAGAL** (hasil di
bagian HASIL, akhir dokumen). Deklarasi di-commit sebelum runner-nya berjalan; riwayat git
adalah buktinya (deklarasi → 3 amandemen berstempel → hasil).
**Look accounting:** ini look **PREDIKTIF** pertama sejak `PREREG-microstructure-001`. Dicatat di
kolom **predictive** counter (pemilik: `docs/EDA-microstructure-001.md`), pada commit yang sama
dengan hasilnya. **`N_trials` cap = 32**, rincian di §3 — cap ini kuterima tanpa keberatan.

## 1. Pertanyaan, satu kalimat falsifiable

> Apakah sisi agresor sebuah trade memprediksi pergerakan harga berikutnya pada `binancef`
> BTCUSDT, dengan magnitudo yang melampaui biaya (4 bps maker RT / 10 bps taker RT), pada
> offset latensi yang bisa dicapai dari VPS Tokyo?

## 2. Data dan kerangka sampel — deterministik, dideklarasikan sebelum melihat

- **Sumber:** arsip Vision `binancef`/BTCUSDT aggTrades di HF (`vision/` prefix),
  `2019-12-31..2026-08-01`. Kontrol identitasnya baru diverifikasi ulang: satu hari penuh
  (`2026-07-30`) **identik baris-per-baris dua arah** dengan store terekam (834.335 =
  834.335, 0 hanya-arsip, 0 hanya-terekam, 0 field mismatch), di atas verifikasi-verifikasi
  set-identity sebelumnya. Lubang arsip **dihitung, tidak diisi**: lubang besar
  `2025-10-08..2026-07-29` plus hari terpencar yang belum ter-backfill saat runtime.
- **Kerangka hari (stratified, kalender, bukan pilihan):** tanggal **1, 8, 15, 22 setiap
  bulan** dalam rentang arsip. Hari kerangka yang tidak ada di HF saat runtime **dilewati dan
  dihitung**. Perkiraan ≈ 280–316 hari, melintasi seluruh rezim (bull 2020–21, bear 2022,
  pemulihan 2023, era ETF 2024, 2025–26).
- **Anchor per hari:** 20.000 trade, sampel seragam dengan seed `20260808 + indeks-hari`,
  dibatasi pada trade yang masih menyisakan lookahead `offset + horizon` di dalam berkas
  harinya; anchor yang kehabisan lookahead dilewati dan dihitung.
- **LockBox tidak tersentuh:** seluruh data ≤ `2026-08-01`; batas LockBox `2026-08-05 01:00Z`.

## 3. Grid = cap: 8 × 4 = 32 sel, TANPA conditioning

| dimensi | nilai |
|---|---|
| horizon `h` | 1 s, 5 s, 15 s, 60 s, 300 s, 900 s, 3600 s, 14400 s |
| offset latensi `L` | 0, 10, 50, 200 ms |
| venue | `binancef` saja |
| conditioning | **TIDAK ADA** — bucket ukuran, jam, rezim vol, venue lain = PREREG terpisah |

Sel tidak ditambah, tidak dihapus, tidak dipilih-pilih. Ke-32 dilaporkan.

## 4. Statistik — dan proksi harga yang DIPAKSAKAN oleh data, diakui di muka

**Markout bertanda (bps):** `sign(agresor) × (P[t+L+h] − P[t+L]) / P[t+L] × 1e4`,
`sign = +1` untuk agresor beli (`aggressor_buy`), `−1` untuk jual.

**Proksi harga: HARGA TRADE, bukan mid.** Arsip aggTrades tidak memuat buku, dan buku terekam
hanya 30 hari pada kadensi 1 Hz — tidak bisa menyelesaikan offset 10–200 ms. Maka:
`P[τ]` = harga trade **pertama dengan `ts > τ`** (strictly after, supaya offset 0 tidak memakai
anchor-nya sendiri), dengan toleransi kesegaran `max(0,2 s; 0,05·h)`, cap 5 s — referensi yang
lebih basi dari itu dilewati dan dihitung.

**Bias proksi, dinyatakan sebelum angka ada:** harga trade membawa bounce bid-ask, yang
mendorong markout **ke bawah** sekitar `−c` (separuh-spread) relatif terhadap markout berbasis
mid. Kontrol positif §6 mengukur besarnya pada parameter yang ditanam. Ambang §5 TIDAK
disesuaikan untuk ini — kalau bias itu membuat vonis GAGAL padahal mid-markout akan lolos,
itu keterbatasan instrumen yang dicatat, bukan alasan menyetel.

**Agregasi:** median per-hari per-sel → headline = **median dari median-harian** (bobot hari
sama); mean dari mean-harian dilaporkan berdampingan; **median yang mengikat** (ekor gemuk
sudah terukur di repo ini). **Error bar:** block bootstrap per hari, 400 resample, interval 95 %.
`n` trade per sel dilaporkan.

## 5. Kriteria vonis — ekshaustif, dengan cabang tangkap-semua

| vonis | kondisi |
|---|---|
| **LOLOS-TAKER** | ada sel yang **seluruh interval 95 %-nya** > 10 bps |
| **LOLOS-MAKER** | bukan LOLOS-TAKER, dan ada sel yang seluruh intervalnya > 4 bps |
| **GAGAL** | seluruh 32 sel: interval 95 % sepenuhnya < 4 bps |
| **INDETERMINATE** | ada interval yang melintasi ambang, ATAU keluaran apa pun yang tidak cocok dengan cabang di atas → **amandemen wajib** yang menyebut cabang yang hilang |

## 6. Kontrol — dijalankan SEBELUM data nyata; kegagalan mana pun = berhenti total

**Negatif (pipeline bocor?):** 30 hari pertama kerangka, sisi agresor diacak dalam-hari
(seed 424242), pipeline identik. **PASS** = ke-32 sel: |median-dari-median| < 0,1 bps **dan**
interval 95 % memuat 0. Selain itu = pipeline bocor, hasil apa pun tidak berarti, berhenti.

**Positif (instrumen bisa melihat?):** simulasi generalized-Roll dalam event-time trade:
- Tanam `c = 4 bps, λ = 0` → markout harus memulihkan **≈ −c** (drift bounce murni),
  toleransi ±20 % pada horizon ≥ 15 s.
- Tanam `c = 4 bps, λ = 2 bps` → harus memulihkan **≈ λ − c ≈ −2 bps**.
Gagal memulihkan salah satunya = instrumen tidak melihat, berhenti sebelum data nyata.

## 7. Apa yang vonis ini TIDAK buktikan — ditulis sebelum vonisnya ada

Markout positif **bukan strategi**: ia tidak memuat sizing, risiko, antrian, biaya fill nyata,
atau kapasitas. Ia diukur pada **trade orang lain**, bukan trade-mu — agresor di tape sudah
membayar spread dan fee-nya sendiri, dan meniru mereka menambahkan latensimu di atas latensi
mereka. Proksi harga-trade menggeser level semua angka relatif mid. Dan satu venue tanpa
conditioning berarti hasilnya rata-rata atas seluruh kondisi — sinyal yang hidup hanya di
subset kondisi akan tampak mati di sini, dan menemukannya adalah PREREG lain, bukan revisi
yang ini.

---

# AMANDEMEN 1 — 2026-08-08: ekspektasi kontrol positif adalah aljabar yang salah

**Data nyata BELUM tersentuh.** Runner berhenti di kontrol positif sebagaimana dideklarasikan
(§6: kegagalan mana pun = berhenti total), dan urutan commit membuktikan deklarasi → kegagalan
kontrol → amandemen ini, sebelum satu markout nyata pun dihitung.

## Yang salah, dengan turunannya

§6 mendeklarasikan bahwa pada aliran iid, `λ=0` harus memulihkan `≈ −c` dan `λ=2` memulihkan
`≈ λ−c`. **Itu salah untuk proksi yang §4 sendiri deklarasikan.** Reversal `−c` muncul ketika
referensinya adalah harga trade anchor **sendiri**; proksi kita memakai **trade pertama
SETELAH** `t+L`, yang sisinya independen dari sisi anchor pada aliran iid:

```
E[markout | iid, λ apa pun] = λ·Σ E[q_j|q_0] + c·E[s_0(q_tgt − q_ref)] = 0 + 0 = 0
```

Diverifikasi tiga jalur `[DIUKUR]`: pipeline penuh memberi −0,037 bps (λ=0); brute-force
independen satu sel memberi −0,16 ± 0,44 (λ=2, konsisten nol); dan pemisahan per-sisi
menunjukkan drift pasar (+2,3 bps kedua sisi) yang justru **dibatalkan** dengan benar oleh
tanda. Instrumen melihat nol karena **nilai sebenarnya memang nol** — kontrol lama menuntut
instrumen memulihkan angka yang salah.

Kegagalan kedua kontrol lama: sel horizon panjang (1 h, 4 h) dalam SATU hari simulasi ~11 jam
punya jendela yang tumpang tindih masif — ill-conditioned, dan ikut mencemari agregat kontrol.

## Kontrol positif v2 — dideklarasikan di sini, sebelum rerun

Kontrol dibatasi ke sel `h ∈ {15 s, 60 s, 300 s}` (terkondisi baik dalam satu hari simulasi):

1. **Pemulihan nol** (dua kasus): aliran iid dengan `λ=0` dan `λ=2 bps` → |median| < 0,3 bps.
   Instrumen tidak boleh menemukan sinyal yang tidak ada.
2. **Pemulihan sinyal tanam**: aliran agresor **berautokorelasi** AR(1) `ρ=0,6` (persistensi
   order-splitting — mekanisme nyata yang terukur di tape ini), `λ=2 bps`, `c=0,5 bps`.
   Bentuk tertutup, diturunkan tangan: markout(k trade) =
   `λ(ρ²−ρ^{k+1})/(1−ρ) + c(ρ^k − ρ)` → plateau `λρ²/(1−ρ) − cρ = 1,8 − 0,3 = +1,5 bps`
   untuk `k ≳ 150` (yaitu `h ≥ 15 s` pada ~10 trade/s). **PASS** = median terukur pada ketiga
   horizon dalam ±25 % dari +1,5 dan interval bebas dari nol.

Ambang vonis §5, grid §3, proksi §4, dan kontrol negatif §6 **tidak berubah**.

---

# AMANDEMEN 2 — 2026-08-08: kontrol v2 gagal karena KEKUATAN, bukan aljabar; data nyata tetap tak tersentuh

v2 mengukur +3,761 lawan plateau tangan +1,50 dan FAIL pada toleransi ±25 %. Diagnosis empiris
`[DIUKUR]`: rantai AR-nya persis benar (`E[q_{a+j}q_a]` cocok `ρ^j` sampai 4 desimal), mean≈median,
dan formula plateau-nya berdiri — tapi derau per-jendela dari walk-λ adalah
`≈ λ·2j/√n` sehingga sinyal/SE `= 0,45·√n/j`: pada `h=60–300 s` (j=600–3.000) hanya **0,5–1 SE**.
Nilai +3,761 itu **konsisten dengan 1,50 di dalam deraunya sendiri**. Kesalahan v2 adalah menuntut
toleransi ±25 % dari pengukuran ber-SE ~50–100 % — presisi yang disiratkan tanpa error bar, kelas
kesalahan yang repo ini sudah punya nama untuknya.

**Kontrol v3 — kekuatan diperbaiki, dunia tanam tidak berubah:**
- Sel kontrol: `h ∈ {5 s, 15 s}` saja (j = 50–150; sinyal/SE per hari ≈ 2–6).
- **30 hari simulasi independen** × 200k trade, agregasi median-dari-median-harian — pipeline
  agregasi yang sama dengan run nyata.
- PASS sinyal-tanam: median gabungan dalam **±25 %** dari plateau +1,50 (SE gabungan kini ≈ 2 %
  dari sinyal, jadi toleransinya bermakna). PASS nol-recovery: tak berubah (|median| < 0,3 bps).

Grid §3, proksi §4, ambang §5, kontrol negatif — semuanya tak berubah. Data nyata belum tersentuh;
urutan commit tetap buktinya.

---

# AMANDEMEN 3 — 2026-08-08: ambang kontrol negatif berada di bawah lantai derau statistiknya

Kontrol negatif FAIL pada klausa `|median| < 0,1 bps` — **di sel 4 jam saja**, yang mediannya
0,15–0,23 dengan CI bootstrap `[−0,14, +0,88]`: seluruh 32 interval memuat nol, dan h ≤ 900 s
semua |median| ≤ 0,056. Lebar CI terukur di 4 jam (~±0,5 bps) adalah **5× ambang** yang
kudeklarasikan — per hari hanya ~6 jendela 4-jam yang independen, jadi derau statistiknya
sendiri jauh di atas 0,1. Aturan lama menuntut presisi di bawah lantai derau — kelas kesalahan
yang sama dengan Amandemen 2, kali ini di sisi negatif.

**Aturan PASS v2 (justifikasi: lebar CI terukur, bukan keinginan lolos):**
- Seluruh 32 sel: CI 95 % memuat 0.
- Klausa `|median| < 0,1 bps` berlaku untuk `h ≤ 900 s` (derau statistiknya terukur ≪ 0,1).
- Untuk `h ∈ {3600 s, 14400 s}`: klausa CI yang memutuskan; mediannya tetap dilaporkan.

**Anti-rule-shopping:** kontrol negatif **diulang dengan seed baru `424243`** dan 30 hari
kerangka yang sama — vonis dijatuhkan pada realisasi segar, bukan pada angka yang memotivasi
amandemen ini. Data nyata (bertanda asli) tetap belum tersentuh.

---

# HASIL — 2026-08-08, dijalankan persis sebagaimana dideklarasikan

Runner: `scripts/prereg_markout_001.py`. Artefak mesin: `reports/prereg-markout-001-result.json`.
Urutan eksekusi sesuai §6: kontrol positif dulu, kontrol negatif kedua, data nyata terakhir —
data bertanda asli baru dievaluasi setelah kedua kontrol PASS.

## Kontrol (semuanya PASS sebelum data nyata disentuh)

- **Kontrol positif v3** (Amandemen 2): nol-recovery iid λ=0 → `+0.002` bps, iid λ=2 →
  `-0.015` bps (keduanya |·| < 0,3); sinyal-tanam AR(1) ρ=0,6 → terukur `+1.411` vs plateau
  teoretis `+1.50` (dalam ±25 %, SE gabungan ≈ 2 % dari sinyal). **PASS.**
- **Kontrol negatif, seed segar `424243`** (Amandemen 3): sisi agresor diacak dalam-hari, 30 hari
  kerangka, pipeline identik. Seluruh 32 CI memuat nol; semua `h ≤ 900 s` ber-|median| ≤ 0,052.
  **PASS — pipeline tidak bocor.**

## Matriks 8×4 [DIUKUR] — 277 hari, median-dari-median-harian [95 % CI bootstrap per-hari], bps

```
    h                     L=0ms                    L=10ms                    L=50ms                   L=200ms
   1s   +0.074 [ +0.059, +0.097]   +0.047 [ +0.041, +0.062]   +0.014 [ +0.010, +0.017]   +0.000 [ +0.000, +0.000]
   5s   +0.157 [ +0.138, +0.180]   +0.147 [ +0.123, +0.172]   +0.052 [ +0.044, +0.069]   +0.009 [ +0.000, +0.010]
  15s   +0.129 [ +0.111, +0.152]   +0.134 [ +0.113, +0.150]   +0.045 [ +0.038, +0.060]   +0.000 [ +0.000, +0.010]
  60s   +0.142 [ +0.118, +0.161]   +0.132 [ +0.102, +0.162]   +0.069 [ +0.049, +0.093]   +0.000 [ +0.000, +0.011]
 300s   +0.080 [ +0.043, +0.103]   +0.064 [ +0.043, +0.079]   +0.015 [ +0.000, +0.037]   -0.048 [ -0.095, -0.023]
 900s   +0.065 [ +0.011, +0.107]   +0.065 [ +0.030, +0.096]   +0.000 [ -0.038, +0.026]   -0.081 [ -0.139, -0.047]
3600s   +0.000 [ -0.042, +0.107]   +0.011 [ -0.052, +0.072]   -0.050 [ -0.117, +0.012]   -0.124 [ -0.235, -0.052]
14400s  -0.018 [ -0.230, +0.160]   -0.045 [ -0.257, +0.178]   -0.087 [ -0.324, +0.092]   -0.192 [ -0.395, +0.017]
n/sel                 3,794,721                 3,777,646                 3,759,105                 3,715,103   (baris h=1s; sel lain sebanding)
```

Tidak ada sel yang ditambah, dihapus, atau dipilih. 32 sel = 32 trial prediktif yang
dideklarasikan; pencatatannya di pemilik counter (`docs/EDA-microstructure-001.md`), commit
yang sama dengan hasil ini.

## Vonis, per tabel §5 yang dideklarasikan sebelum angka ada

**GAGAL.** Tidak satu pun sel bermedian > 4 bps — dan lebih kuat dari itu: **seluruh 32 batas
atas CI berada di bawah 4 bps** (maksimum batas atas: `+0.180` pada h=5 s, L=0 ms), jadi ini
GAGAL yang tegas, bukan INDETERMINATE. Ambang maker (4 bps) tidak tercapai di sel mana pun;
ambang taker (10 bps) apalagi.

Deskripsi jujur atas polanya (deskripsi, bukan vonis baru): sisi agresor MEMANG membawa
informasi — median positif dengan CI yang mengecualikan nol pada h ≤ 900 s untuk L ≤ 10 ms,
puncaknya `+0.157` bps pada 5 s — tetapi magnitudonya ~25× di bawah hurdle maker dan ~64× di
bawah hurdle taker, meluruh cepat terhadap offset latensi (pada L=200 ms praktis nol atau
negatif), dan berbalik tanda pada horizon panjang. Ini konsisten dengan temuan repo yang sudah
ada (`RESEARCH.md`: prediktabilitas nyata tapi kecil dan berumur pendek; sensus gerakan
`DIAG-movement-census-001`): informasi arah ada, ekonominya tidak.

## Apa yang vonis ini TIDAK buktikan (item 4, deklarasi §7)

Vonis GAGAL ini mengubur satu klaim spesifik — bahwa sisi agresor trade `binancef` BTCUSDT,
tanpa conditioning, memprediksi pergerakan mid dengan magnitudo yang melampaui biaya pada
latensi VPS Tokyo — dan TIDAK mengubur yang lain. Ia bukan bukti bahwa microstructure alpha
tidak ada: conditioning apa pun (ukuran trade, rezim volatilitas, jam, sekuens order-flow,
book state dari perekam L2 yang sedang berjalan) adalah pertanyaan berbeda yang masing-masing
butuh PREREG sendiri dengan cap sendiri. Sebaliknya pun berlaku: seandainya vonisnya LOLOS,
markout positif tetap bukan strategi — ia diukur pada trade orang lain, bukan trade-mu; ia
tidak memuat sizing, manajemen risiko, adverse selection saat kamu yang menjadi sisi pasif,
antrian maker yang terukur ~$445k di touch, ataupun fill nyata. Angka `+0.157` bps terbaik di
matriks ini adalah properti informasi tape, bukan P&L yang bisa dipanen — dan kegagalannya
melampaui 4 bps adalah pernyataan tentang ekonomi eksekusi, bukan tentang ada-tidaknya
informasi di sisi agresor.
