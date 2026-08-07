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

---

# HASIL — dijalankan 2026-08-07

`scripts/diag_provenance_001.py`; mesin: `reports/diag-provenance-001.json`. Setelah dijalankan,
pembacaanku diuji oleh tiga agen yang **diminta membantah**. Dua dari enam klaimku gugur, dan
mereka menemukan **tiga bug tambahan** — dua di diagnostikku, satu di deklarasi PREREG-001.
Bagian ini menulis yang bertahan, bukan yang kuinginkan.

## Prediksi vs hasil

| | prediksi | hasil | |
|---|---|---|---|
| A1 pasangan `(exchange,symbol)` | > 1 | **4** | ✅ |
| A2 duplikat | < 0,2 % | **0,041 %** (puncak 1,00 % di `2026-08-02`) | ✅ |
| A3 pelanggaran urutan id | > 20 % | **9,1–16,3 %** | ❌ |
| A4 berbagi ts | 40–70 %, grup 2–5 | **46–57 %, grup 1,86–2,32** | ✅ |
| B urutan berpengaruh | (iv) bergeser > 0,05 | **0,0004** | ❌ **dan uji-nya sendiri cacat** |
| C arsip vs terekam | sepakat < 0,05 | **identik 17 digit, selisih baris 0** | ✅ |
| D1 nol tick | ≈ 5 % | **14,6 %** | ❌ |
| D2 top 1 % | > 0,5 | **1,081** | ✅ angka, ❌ tafsir |
| D3 winsorize | > 0,10 menuju nol | **+0,977, tapi MELEWATI nol** | ✅ angka, ❌ arah |
| D4 buang `Δp=0` | < 0,03 | **−0,0011** | ✅ |

## Vonis menurut aturan sebagaimana kau tulis: **ARTEFAK**

A1, D2 dan D3 menyala. Kulaporkan apa adanya. **Lalu kulaporkan bahwa ketiganya cacat**, karena
menekuk aturan setelah melihat hasil lebih buruk daripada mengatakan aturannya tidak layak.

| trigger | mengapa ia tidak membawa informasi |
|---|---|
| **A1** | Kuerinya sengaja tanpa filter atas store yang **sudah diketahui** multi-venue, dan setiap blok hilir memfilter. `P(nyala\|artefak) = P(nyala\|bersih) = 1` — likelihood ratio persis 1, **nol bit**. Aturan yang tidak bisa gagal. |
| **D2** | `share = prod[top_k].sum() / prod.sum()` adalah rasio terhadap total **bertanda** yang bisa mendekati nol, jadi tidak terbatas di [0,1]. Kalibrasi null pada 200 random walk murni (`γ₁ = 0` by construction): median 13,2 %, **p05 −168 %, p95 +154 %, maks +2582 %**. Ia menyala pada seri bersih. |
| **D3** | Dideklarasikan "menggerakkan `ρ₁` > 0,10 **menuju nol**"; diimplementasikan sebagai `p99.0 − base > 0.10`. `ρ₁` bergerak −0,466 → **+0,511** — ia **melewati** nol dan berakhir **lebih jauh** darinya. Kode tidak menguji yang dideklarasikan. |

Jadi vonis mekanisnya ARTEFAK, dan vonis mekanis itu **tidak bisa dipercaya**. Yang berikut
berdiri di atas pengukuran, bukan di atas aturan.

## Yang bertahan, dan lebih kuat dari klaimku

**Tape-nya faithful — jauh lebih kuat dari yang kutulis.** Bukan "identik sampai 3 desimal":
`γ₀` identik **17 digit** (`8.211007824518629e-11` di kedua sumber), ACF identik di kedelapan lag
sampai digit terakhir, dan **himpunan `trade_id` identik dua arah (0 dan 0), hari penuh, tanpa
dedup, tanpa filter jam**, dengan `ts_mismatch 0`, `max|Δprice| 0,0`, `max|Δqty| 0,0`,
`side_mismatch 0`. Arsipnya independen sungguhan (zip `data.binance.vision`, sha256 terverifikasi
di manifest, `id_holes = 0`). **Tidak ada cacat collector pada hari-hari ini.**

**Batas scope yang harus ikut, dan tidak kusebut sebelumnya:** arsip vision hanya punya **3 dari
33** hari terekam = **9 %**, dan **tidak satu pun** dari tiga hari ber-`recorded-damage` ada di
dalam overlap. Blok C secara struktural **buta** terhadap setiap cacat collector yang sudah
diketahui ada.

**Distribusinya rapat, tapi "normal" adalah kata yang salah.** Angka terkoreksi konfirm persis
lewat dua rute integer independen: 14,6 % nol · 63,9 % satu tick · 12,6 % dua tick · median 1 ·
p99 6 · maks 887 tick. Tapi `p99/sd = 1,05` (normal memberi 2,33), excess kurtosis **3.135**, dan
bucket **30+ tick (0,252 %) LEBIH BESAR** dari bucket 10–19 (0,211 %). Ekornya tidak meluruh — ia
**populasi kedua**. 0,25 % print teratas memikul **90,5 %** dari `Σ(Δp)²`.

Koreksi atas koreksiku: "seluruh histogram bergeser satu bucket" tidak akurat. Kebocorannya
**parsial dan bergantung ukuran** — 100 % bucket 0 bertahan, 81,2 % bucket 1 bocor, 60,2 % bucket
2, 32,6 % bucket 3, 16,5 % bucket 4, **0 % untuk ≥ 5 tick**. Seluruh cerita konsentrasi tidak
terpengaruh.

## Dua klaimku yang gugur

**(B) "Urutan tidak berpengaruh" — ujinya sendiri cacat.** Blok B tidak menguji empat pengurutan;
ia menguji **satu, dilaporkan tiga kali**. `load()` menaruh `ORDER BY CAST(trade_id AS BIGINT)`
di dalam subquery dedup, jadi varian (i) "tanpa `ORDER BY`" sudah terurut `trade_id`. Buktinya di
JSON-ku sendiri: (i), (ii), (iii) identik sampai digit ke-16 di kedelapan lag. Dan karena `ts_ms`
terbukti **non-decreasing terhadap `trade_id`** pada tape ini, `ORDER BY (ts_ms, tid)` **dipaksa
secara matematis** sama dengan `ORDER BY tid` — varian (iii) tidak pernah bisa jadi uji
independen. Hanya (iv) berbeda, dan hanya pada 76 dari 434.107 posisi.

**(D2/D4) Tanda pada kesimpulanku terbalik, dan itu justru temuan intinya.** Total `Σ(dp_t·dp_{t+1})`
**negatif**; kontribusi top 1 % sebesar 108 % berarti sisa 99 % adalah **POSITIF**, bukan negatif
seperti yang kutulis. Konsekuensinya besar: **tape regime tenang punya `γ₁ > 0`** — momentum /
order splitting — dan **seluruh tanda negatif datang dari ekor.** Roll mati dari dua arah
sekaligus.

Dan konsentrasinya **tidak stabil** lintas hari: 85,3 % · 71,2 % · 96,7 % · 97,4 % · 91,4 % ·
73,6 % pada enam hari. `2026-07-30` yang kupilih sebagai fokus adalah ujung ekstremnya.

## Mekanismenya ketemu — dan lubang yang kudeklarasikan di muka tertutup

Prediksiku menyatakan "tidak satu pun kandidat artefak yang kutahu menghasilkan alternasi teredam
ini". Sekarang ada jawabannya, dan ia **bukan artefak**:

> **Seluruh osilasi hidup DI DALAM satu milidetik.** Pasangan yang kedua leg-nya intra-ms memikul
> **88,9 % / 89,6 % / 76,3 %** dari `γ₁` pada tiga hari. Mengacak baris **di dalam** kelompok `ts`
> yang sama meruntuhkan `ρ₂..ρ₄` dari `(+0,337 · −0,197 · +0,165)` menjadi
> `(+0,004 · +0,000 · +0,005)`. Menipiskan ke satu print per milidetik **membalik** `ρ₁`.

Isinya adalah **bid-ask bounce harfiah** — mekanisme Roll sendiri — melintasi buku yang sesaat
melebar ke **63–753 tick (median 99)**, sementara p50 buku adalah **1,01 tick**. Sisi agresor
terkunci pada band harga (BUY di band atas, SELL di band bawah, cocok 95–98 % pada leg ekstrem),
id berturut-turut tanpa celah, dan harganya kembali. Run-nya panjang: rata-rata 4,6–5,1 leg,
maksimum 38 alternasi berturut-turut — itulah yang memproduksi `ρ₂ > 0` yang bertahan.

Kontrol positif simulasi menutupnya: `p_t = m_t + c_t·q_t` dengan `c_t` 0,5 tick tenang / 300 tick
saat burst **dan `q_t` beralternasi di dalam burst** memberi `ρ = −0,968 +0,905 −0,843 +0,781`.
Simulasi sama **tanpa** alternasi `q` memberi MA(1) buku teks `−0,487 −0,036 +0,045 −0,025`.
Terukur: `−0,752 +0,502 −0,363 +0,280`.

## Koreksi skala KETIGA, yang PREREG §2 tidak deklarasikan

`γ₁ = −c²` **kuadratik** dalam `c`. Jadi Roll memulihkan `√(E[c²])` — akar momen kedua dari spread
yang **berubah keras terhadap waktu** — sementara targetku adalah **median** buku. Itu kelas D
lagi, dan aku sudah mendeklarasikan dua koreksi skala di §2 sambil melewatkan yang ketiga dan
paling menentukan.

Terukur: `c_Roll` = **0,0619 / 0,2330 / 0,2058 bps** pada tiga hari, versus `c_book` 0,0078 bps —
**8× sampai 30×**. Selisih itu bukan bias estimator; itu jarak antara median dan akar momen kedua
dari besaran yang ekornya adalah populasi tersendiri.

## Pertanyaan kritis, dijawab: **TIDAK**

Memangkas ekor **tidak** memulihkan half-spread buku. Estimator ter-trim adalah fungsi monoton
dari level trim yang **melintasi nol**: `2026-07-30` trim 0,1 % → `c_hat` 0,0099 bps (tampak kena
target), tapi trim 0,2 % → `γ₁` sudah **positif**. `2026-07-31` trim 0,1 % → 0,0684 bps (8,8×
target). Tidak ada level trim yang bisa dibela, dan memilihnya setelah melihat target adalah look
yang tak tercatat. **Cabang "estimator tahan-ekor menyelamatkan Roll" mati.**

## Bug ketiga: PREREG-001 §4 tidak men-segment pada milidetik

§4 memecah deret pada diskontinuitas `trade_id` dan batas jam, dengan alasan yang ditulis sendiri:
*"menyeberanginya adalah splice, dan `γ₁` adalah pernyataan tentang kebersebelahan."* Tapi 76–90 %
dari `γ₁` datang dari pasangan yang "bersebelahan" **hanya dalam arti dua aggTrade berbagi
timestamp**. Argumen yang sama berlaku untuk milidetik, dan aku tidak menerapkannya.

## Vonis substantif

**Bukan cacat data.** Tape-nya faithful terhadap arsip venue pada setiap hari yang bisa diuji.
**Bukan pula "estimator artefak"** seperti yang sempat kutulis. Yang terukur adalah **fakta pasar
dengan nama**: bid-ask bounce intra-milidetik melintasi buku yang episodik melebar dua orde
besaran, ditumpuk di atas regime tenang yang `Δp`-nya justru berautokorelasi **positif**.

Roll gagal di sini bukan karena datanya kotor, melainkan karena **asumsi `c` konstannya salah
secara ekstrem** pada instrumen ini, dan karena estimatornya kuadratik dalam `c` sehingga dikuasai
ekor. Itu batas pemakaian yang bisa dinyatakan, bukan misteri.

---

# ANOTASI 2026-08-07 — pencabutan dan penyempitan, atas dasar `docs/DIAG-venue-filter-audit.md`

Ditulis sebagai anotasi bertanggal. **Tidak satu pun kalimat di atas diubah**; yang di bawah ini
mencabut atau mempersempitnya, dan teks aslinya tetap berdiri supaya jejaknya utuh.

## A1. Hipotesis kontaminasi venue: **DICABUT**

Sepanjang dokumen ini, "campuran dua instrumen" diperlakukan sebagai kandidat artefak yang hidup,
dan trigger A1 pada aturan keputusan menyala atas dasar 4 pasangan `(exchange, symbol)` yang
ditemukan tanpa filter. Hipotesis itu **mati**, dan bukan karena penalaran:

1. `scripts/prereg_microstructure_001.py` memuat **tepat satu** klausa `WHERE` di seluruh
   berkas (grep `WHERE exchange = 'binancef'`), dan itulah filternya.
2. **434 dari 434** pasangan hasil-kali teratas punya `binancef`/`BTCUSDT` di **kedua** barisnya
   `[DIUKUR]`.
3. Kontrafaktual tanpa filter **tidak bisa dijalankan**: `bybit` memakai UUID sebagai `trade_id`,
   jadi `CAST(trade_id AS BIGINT)` melempar `ConversionException`. Pencampuran tidak bisa terjadi
   diam-diam pada jalur ini — ia berhenti dengan error.
4. Baris **`ALL MIXED`** di `DIAG-venue-filter-audit.md` §5c adalah **kontrol negatif terukurnya**:
   mencampur seluruh venue memberi `ρ₂` = **−0,042 / −0,030 / −0,012** — yaitu ≈ 0, kebalikan dari
   tanda tangan `ρ₂ > 0` yang dicari. Pencampuran **menghancurkan** pola itu, tidak menciptakannya.

## A2. "Keluarga Roll mati" **DIPERSEMPIT ke `binancef`**

Vonis di bagian HASIL ditulis tanpa kualifikasi venue. Itu terlalu luas. Per-venue, tanpa pooling
`[DIUKUR]`:

| date | exchange | `ρ₁` | `ρ₂` |
|---|---|---:|---:|
| 2026-07-30 | **bybit** | −0,482 | **−0,003** |
| 2026-07-31 | **bybit** | −0,464 | **−0,003** |
| 2026-07-30 | binancef | −0,466 | +0,337 |
| 2026-07-31 | binancef | −0,752 | +0,502 |

`bybit`/`BTCUSDT` menunjukkan `ρ₁ ≈ −0,47` dengan `ρ₂ ≈ −0,003` — **MA(1) sebagaimana Roll
mengasumsikannya**. Pernyataan yang bertahan karena itu adalah: keluarga Roll gagal **pada
`binancef`/`BTCUSDT` pada hari-hari ini**, bukan pada instrumen BTC secara umum. Apakah ia
berlaku pada `bybit` belum diuji dan **tidak diuji di sini**.

## A3. Setiap klaim yang bergantung pada `0,0078 bps`: **[UNVERIFIED]**

`DIAG-venue-filter-audit.md` §2 menemukan bahwa klausa `WHERE` di balik angka buku
`0,0156 bps` **tidak ada di repo** — tidak ada skrip yang menghitungnya, tidak ada SQL yang
mengutipnya. Ia dipakai sebagai jangkar tanpa kueri yang bisa dijalankan ulang.

Sampai `BOOK-001` selesai, klaim berikut berstatus **`[UNVERIFIED]`**, bukan `[DIUKUR]`:

- **rasio `c_Roll` terhadap `c_book` sebesar 8×–30×** di bagian HASIL dokumen ini
  (`0,0619 / 0,2330 / 0,2058 bps` versus `0,0078`). Pembilangnya terukur; **penyebutnya tidak**.
- **"koreksi skala ketiga"** di bagian yang sama menyimpulkan bahwa selisihnya adalah jarak antara
  median dan akar momen kedua. Aritmetikanya berdiri sendiri, tapi **besaran** selisihnya mewarisi
  penyebut yang tak terverifikasi.
