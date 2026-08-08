# PREREG-markout-001 — apakah sisi agresor memprediksi pergerakan harga berikutnya?

**Status:** DIDEKLARASIKAN, belum dijalankan. Di-commit sebelum runner-nya berjalan; riwayat git
adalah buktinya.
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
