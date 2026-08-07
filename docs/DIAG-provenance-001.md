# DIAG-provenance-001 — apakah `ρ₁ ≈ −0,43` fakta pasar atau artefak data

**Klasifikasi look: DIAGNOSTIK PROVENANCE.** Ia memeriksa integritas tape — jumlah baris,
duplikat, urutan, bentuk distribusi, dan perbandingan terhadap tape venue. Ia tidak mengekstrak
apa pun tentang return, posisi, atau P&L, dan tidak ada spesifikasi prediktif yang dievaluasi.
Dicatat di kolom **diagnostic**, bukan predictive. Aku setuju dengan klasifikasi ini; kalau
tidak, aku akan berhenti dan bertanya alih-alih menulis bagian ini.

**Tugas:** ukur, laporkan, berhenti. Tidak ada perbaikan di giliran ini.
**Latar:** `docs/PREREG-microstructure-001.md` RESULT — vonis INDETERMINATE, `ρ₁` gabungan
−0,7127, di luar rentang `[−0,5, +0,5]` yang bisa dihasilkan MA(1) mana pun.

---

# PREDIKSI — ditulis dan di-commit sebelum skripnya ada

Bagian ini di-commit terpisah, sebelum `scripts/diag_provenance_001.py` ditulis. Riwayat git
adalah buktinya. Hasil diletakkan berdampingan di bawah, tanpa satu pun prediksi diedit.

## Prediksi headline

**ARTEFAK**, dengan keyakinan sedang — **tapi aku tidak punya mekanisme yang menjelaskan bentuk
ACF-nya**, dan itu kunyatakan sekarang supaya tidak bisa kutambal belakangan.

Alasan ragu: pola terukur adalah **alternasi teredam** (`−0,434 · +0,306 · −0,190 · +0,194`,
rasio ~0,7 per lag). Itu tanda tangan `Δp` yang mengikuti AR(1) berkoefisien negatif. **Tidak
satu pun kandidat artefak yang kutahu menghasilkannya**: pengacakan penuh maupun per-blok
memberi `ρ₂ ≈ 0`, campuran dua instrumen memberi `ρ₂ ≈ 0`, duplikat memberi `ρ₁ ≈ 0`. Outlier
memberi lonjakan di lag 1 saja, bukan osilasi yang bertahan sampai lag 4.

Jadi prediksiku ARTEFAK bersandar pada Blok D (konsentrasi), sementara **bentuk** ACF-nya
mengarah ke sesuatu yang belum kupahami. Kalau D bersih dan C sepakat, jawaban jujurnya adalah
TIDAK KONKLUSIF, dan aku sudah menyatakan sekarang bahwa itu keluaran yang akan kuterima.

## Blok A — audit deret input

| | prediksi |
|---|---|
| **A1** pasangan `(exchange, symbol)` tanpa filter | **> 1**. Store merekam 16 leg lintas venue, jadi partisi harian pasti memuat beberapa. Tapi `PREREG-001` memfilter eksplisit ke `binancef`/`BTCUSDT`, jadi ini **tidak** menjelaskan hasilnya. |
| **A2** duplikat `(exchange, symbol, trade_id)` per hari | Ada, kecil: **< 0,2 %** baris. Acuan terukur: 979 dari 887.614 pada `2026-08-05` = 0,11 %. |
| **A3** `trade_id` monoton setelah `ORDER BY ts` | **TIDAK monoton.** Pelanggaran **> 20 %** baris, karena banyak aggTrade berbagi milidetik dan tie-break `ts` sewenang-wenang. |
| **A4** fraksi berbagi `ts` dengan baris sebelumnya | **Tinggi: 40–70 %**, rata-rata ukuran kelompok **2–5**. |
| **A5** `price` adalah harga aggTrade | **TERKONFIRMASI**, dan sudah terbukti sebelumnya: `test_vision_overlap` mengukur `max|Δprice| = 0,0` terhadap aggTrades venue. |

## Blok B — sensitivitas urutan

| pengurutan | prediksi `ρ₁` |
|---|---|
| (ii) `ORDER BY trade_id` | **≈ −0,43**, yaitu nilai yang sudah terukur |
| (iii) `ORDER BY ts, trade_id` | **≈ sama dengan (ii)**, selisih < 0,01 |
| (iv) `ORDER BY ts` saja | **bergerak menuju −0,5**, selisih **> 0,05** dari (ii) |
| (i) tanpa `ORDER BY` | dekat urutan berkas; kemungkinan besar ≈ (ii) |

**Ketegangan yang kunyatakan di muka:** aturan keputusan memvonis ARTEFAK bila `|ρ₁|` berubah
> 0,05 antar pengurutan. Prediksiku ia **akan** berubah pada varian (iv). Tapi `PREREG-001`
memakai pengurutan kanonik `trade_id`, jadi kalau (ii) dan (iii) sepakat di −0,43, pengurutan
**bukan** penyebab hasil itu. Aku akan melaporkan vonis menurut aturan sebagaimana ditulis, dan
menyebut ketegangan ini terpisah alih-alih menekuk aturannya.

**Kecurigaan pada skrip PREREG-ku sendiri:** ia memakai `DISTINCT ON (trade_id)` bersama
`ORDER BY tid` di mana `tid = CAST(trade_id AS BIGINT)` — dua ekspresi yang **berbeda**. Kalau
DuckDB tidak memperlakukannya sebagai kunci yang sama, baris mana yang bertahan per `trade_id`
bisa sewenang-wenang. Blok B menguji tanpa `DISTINCT ON`, jadi ia akan menyingkapnya.

## Blok C — A/B lawan tape venue

**Prediksi: keduanya SEPAKAT**, `|ρ₁(arsip) − ρ₁(terekam)| < 0,05`.

Alasannya terukur, bukan tebakan: `test_vision_overlap` sudah membandingkan store terekam
terhadap arsip venue baris-per-baris dan menemukan **`ts_mismatch 0`, `max|Δprice| 0,0`,
`side_mismatch 0`**. Kalau harganya identik baris-per-baris, ACF-nya harus identik juga kecuali
himpunan barisnya berbeda. Store terekam kehilangan baris (lubang feed), dan baris hilang
**menghapus** pasangan — ia tidak **menciptakan** alternasi.

Kalau prediksi ini benar, ia mematikan seluruh keluarga hipotesis "cacat collector", dan
pertanyaannya berpindah ke apakah tape venue itu sendiri punya properti ini.

## Blok D — bentuk distribusi dan konsentrasi

| | prediksi |
|---|---|
| **D1** `\|Δp\|` dalam tick | 0 tick **≈ 5 %** (terukur 4,9 % pada satu jam) · 1 tick 20–35 % · ≥ 10 tick **> 8 %** · ≥ 30 tick **> 1,5 %**. Ekor tebal, karena `sd(Δp) = 7,5 tick` tidak konsisten dengan buku selebar satu tick tanpa ekor besar. |
| **D2** kontribusi 1 % hasil-kali terbesar | **> 0,5** — memicu ARTEFAK |
| **D3** winsorize p99,0 | `ρ₁` bergerak **> 0,10 menuju nol** — memicu ARTEFAK |
| **D4** `ρ₁` tanpa baris `Δp = 0` | berubah **< 0,03**; hanya ~5 % baris |

**Prediksi yang paling mungkin salah:** D2 dan D3. Outlier menjelaskan `ρ₁` besar tapi
**tidak** menjelaskan `ρ₂ > 0` yang bertahan. Kalau D2 dan D3 keluar bersih, prediksi headline-ku
gugur dan aku akan mengatakannya sebagai prediksi yang salah, bukan menggeser ceritanya.

## Aturan keputusan — disalin dari tugas, mengikat

**ARTEFAK** bila salah satu benar: A1 menemukan > 1 `(exchange, symbol)` · B menunjukkan `|ρ₁|`
berubah > 0,05 antar pengurutan · C menunjukkan `|ρ₁(arsip) − ρ₁(terekam)| > 0,05` · D2 > 0,5
atau D3 menggerakkan `ρ₁` > 0,10 menuju nol.

**PASAR** bila semua bersih DAN kedua sumber sepakat pada `ρ₁ ≈ −0,43` dengan `ρ₂ > 0`.

**Selain itu: TIDAK KONKLUSIF** — dikatakan apa adanya, tanpa memilih cabang.
