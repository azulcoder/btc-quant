# EXTRACT-hasbrouck-001 — estimator mikrostruktur, diekstraksi terverifikasi

**Status:** DRAFT — belum ada satu pun return diskor. Dokumen ini menambah **nol** ke look counter.
**Sumber:** Hasbrouck, J. *Empirical Market Microstructure — Economic and Statistical Perspectives
on the Dynamics of Trade in Securities Markets.* Teaching notes B40.3392, Fall 2003, Draft 1.1
(8 Jan 2004). © 2004 Joel Hasbrouck, all rights reserved.

> **Diverifikasi ulang secara independen** oleh sesi Claude Code 2026-08-06 —
> `docs/VERIFY-hasbrouck-extraction.md`. Klaim yang dikoreksi di sana ditandai `[SUPERSEDED]`
> di bawah. Dokumen ini sengaja **tidak** ditulis ulang: yang datang dipertahankan agar
> jejak auditnya utuh.

## Aturan hak cipta — mengikat

Sumbernya berhak cipta dan repo ini **publik**. Karena itu:

- PDF sumber **tidak boleh** masuk repo. Kalau disimpan lokal untuk rujukan → `.gitignore`.
- Dokumen ini tidak memuat prosa sumber. Semua kalimat di sini tulisan sendiri; yang direproduksi
  hanya *persamaan matematis* (fakta, bukan ekspresi berhak cipta), dinyatakan ulang dalam notasi
  repo ini, dengan sitasi nomor seksi.
- Sitasi memakai **nomor seksi buku** (`§7.c`), bukan nomor halaman PDF dan bukan `FILE:NNN`.
  Nomor seksi stabil terhadap edisi; nomor halaman tidak. Sesuai A1 `doc_freshness`.

---

## 1. Metode ekstraksi — dan mengapa metodenya wajib begini

### 1.1 Layer teks PDF merusak persamaan secara SENYAP

Sumbernya disusun di Mathematica. Font matematikanya (`Mathematica1`, `Mathematica2`,
`SymbolMT`, encoding Identity-H) tidak ter-map ke Unicode saat ekstraksi. Hasilnya bukan garbled
yang mencolok — hasilnya **terbaca, masuk akal, dan salah**.

Peta yang terverifikasi dengan membandingkan `pdftotext` terhadap raster halaman yang sama:

| keluaran teks | arti sebenarnya | dikonfirmasi di |
|---|---|---|
| `q` | θ | §4.b (MA(1)) |
| `D` | Δ | §4.a |
| `g` | γ | §3.b |
| `s` | σ | §3.b |
| `e` | ε | §4.b |
| `h` | η | §8.d |
| `f` | φ | §13.b |
| `l` | λ | §13.d |
| `W` | Ω | §17.b |
| `H` … `L` | `(` … `)` | seluruh dokumen |
| `8` … `<` | `{` … `}` | §4.b |
| `£` | ′ (transpose) | §13.b |
| `¥` | ≥ | §4.a |
| `∫` | ≠ | §4.b |

**Tabrakan yang mematikan.** `q` di layer teks bisa berarti θ (koefisien MA) **atau** q (indikator
arah trade) — dan keduanya muncul dalam satu persamaan di Part II. `L` bisa berarti `)` **atau**
operator lag L; `θ_η(L)η_t` keluar sebagai `qh HLL ht`. Tidak ada aturan lokal yang membedakannya.

**Selain itu urutan baris teracak** pada paragraf berpersamaan. Di §4.a dua kalimat pertama keluar
terbalik. Displayed equation memutus aliran dan `pdftotext` menyusunnya ulang berdasarkan koordinat.

### 1.2 Aturan yang mengikat agen mana pun

> **RULE-EXTRACT-1.** Persamaan apa pun dari sumber ini WAJIB berasal dari halaman yang
> dirasterisasi dan dibaca visual. `pdftotext` boleh dipakai untuk prosa, navigasi, dan pencarian
> — **tidak pernah** untuk menurunkan formula, tanda, subskrip, atau eksponen.
>
> Perintah raster: `pdftoppm -jpeg -r 150 -f N -l N <pdf> <prefix>`

Alasannya bukan kehati-hatian melainkan struktur: implementasi dari layer teks tidak akan error.
Ia akan jalan, menghasilkan angka, dan angkanya bohong. Itu kelas kegagalan terburuk yang ada
di repo ini.

### 1.3 Erratum sumber yang ditemukan

- **§13.a** — teks menyatakan sell sebagai `q_t = 1`. Seharusnya `q_t = −1`; §3.a mendefinisikannya
  benar. Implementasi literal dari §13.a akan membalik tanda seluruh λ.

---

## 2. Notasi (dipakai konsisten di seluruh dokumen ini)

| simbol | arti |
|---|---|
| `m_t` | harga efisien (random walk, tak teramati) |
| `p_t` | harga transaksi teramati (log) |
| `q_t` | indikator arah trade: +1 taker beli, −1 taker jual |
| `c` | separuh-spread / biaya per-trade tetap |
| `λ` | koefisien dampak trade (adverse selection) |
| `u_t` | inovasi informasi publik |
| `w_t` | inovasi harga efisien total (`Δm_t`) |
| `ε_t` | inovasi Wold dari `Δp_t` |
| `s_t` | pricing error, `p_t − m_t` |
| `γ_k` | autokovarians `Δp_t` pada lag k |
| `σ²_w` | varians komponen random walk (per satuan waktu) |
| `σ²_s` | varians pricing error |
| `Ω` | matriks kovarians inovasi VAR |

---

## 3. Estimator — satu per satu

Setiap entri: model · momen · estimator · **apa yang teridentifikasi dan apa yang tidak** ·
data yang dibutuhkan · kontrol positif · kontrol negatif · vonis transfer ke perp crypto.

---

### E1 — Estimator spread Roll (§3.a, §3.b)

**Model**

```
m_t = m_{t−1} + u_t
p_t = m_t + c·q_t
```

Asumsi: `q_t = ±1`, beli dan jual sama mungkin, `q_t` serially independent, `q_t ⊥ u_t`,
`u_t` homoskedastik.

**Momen** (§3.b)

```
Δp_t = −c·q_{t−1} + c·q_t + u_t
γ₀   = 2c² + σ²_u
γ₁   = −c²
γ_k  = 0        untuk k ≥ 2
```

**Estimator**

```
ĉ      = √(−γ̂₁)
spread = 2ĉ
σ̂²_u   = γ̂₀ + 2γ̂₁
```

**Yang teridentifikasi:** `c` dan `σ²_u` — dua parameter dari dua momen. Pas-identifikasi.

**Jebakan yang wajib ditangani eksplisit.** Kalau `γ̂₁ > 0`, `√(−γ̂₁)` tak punya solusi riil. Di
sampel tanpa spread ini terjadi kira-kira separuh waktu. Praktik buruk yang lazim: set `c = 0`,
atau ambil `√|γ̂₁|`. **Keduanya kebohongan senyap.**

> **RULE-EXTRACT-2.** `γ̂₁ > 0` WAJIB mengembalikan `ABSTAIN`/`NaN` beserta alasannya —
> tidak pernah nol, tidak pernah nilai absolut. Semantik ini sudah ada di repo (vonis PBO
> `INDETERMINATE`); pakai yang sama.

**Data:** hanya `p_t`. Tidak butuh arah trade, tidak butuh quote.

**Kontrol positif.** Simulasikan dengan `c` ditanam (mis. 0,5 bps), `σ_u` diketahui, N = 10⁶.
Estimator harus memulihkan `c` dalam error Monte Carlo. Verifikasi juga `γ̂_k ≈ 0` untuk `k ≥ 2`
— itu tanda tangan model yang membedakannya dari dinamika lain.

**Kontrol negatif.** (a) Random walk murni (`c = 0`): fraksi `γ̂₁ > 0` harus ≈ 50%, dan setiap
kasus itu harus `ABSTAIN`, bukan angka. (b) Seri dengan autokorelasi **positif** (momentum):
estimator harus `ABSTAIN`, tidak melaporkan spread.

**Transfer ke perp crypto:** ⚠️ **asumsinya gagal, dan sumbernya sendiri sudah memperingatkan.**
§3.b mencatat bahwa pada data nyata `q_t` berautokorelasi positif dan tidak independen dari `u_t`,
sehingga estimasi Roll bias ke bawah. Di crypto autokorelasi arah taker kuat (order splitting).
**Jangan pakai E1 sebagai estimator spread.** Nilainya di repo ini murni sebagai kontrol positif
untuk pipeline autokovarians — dan sebagai batas bawah yang diketahui bias.

---

### E2 — Generalized Roll: identifikasi σ²_w (§7.b, §7.c)

**Model**

```
m_t = m_{t−1} + w_t
w_t = λ·q_t + u_t          ← trade membawa informasi
p_t = m_t + c·q_t
Δp_t = −c·q_{t−1} + (c+λ)·q_t + u_t
```

Spread = `2(c + λ)`: `c` biaya tetap, `λ` adverse selection.

**Momen** (§7.b)

```
γ₀  = c² + (c+λ)² + σ²_u
γ₁  = −c(c+λ)
γ_k = 0        untuk k ≥ 2
```

**Yang TIDAK teridentifikasi.** Tiga parameter `{λ, c, σ²_u}` dari dua momen `{γ₀, γ₁}`.
Tak teridentifikasi. Titik. Hanya kasus khusus yang teridentifikasi, dan keduanya tak menarik:
informasi publik eksklusif (`λ = 0`) atau privat eksklusif (`σ²_u = 0`).

**Yang TERIDENTIFIKASI tanpa restriksi tambahan** (§7.c) — dan inilah hasil sentralnya:

```
σ²_w = λ² + σ²_u = γ₀ + 2γ₁
```

Varians komponen random walk teridentifikasi meski komponen penyusunnya tidak. Sumber menyatakan
ini berlaku umum — meluas ke lag banyak, multivariat, dan harga jamak.

**Konsistensi silang:** pada `λ = 0` (model Roll murni) rumus yang sama memberi `γ₀ + 2γ₁ = σ²_u`.
Cocok dengan §3.b. Ini assert yang harus ada di test.

**Data:** hanya `p_t`.

**Kontrol positif.** Simulasikan dengan `{λ, c, σ_u}` ditanam → `γ̂₀ + 2γ̂₁` harus memulihkan
`λ² + σ²_u`.

**Kontrol negatif — dan ini yang paling penting.** Konstruksi dua set parameter berbeda
`{λ, c, σ_u} ≠ {λ′, c′, σ′_u}` yang menghasilkan `(γ₀, γ₁)` identik. Estimator apa pun yang
mengklaim memulihkan `λ` atau `c` secara terpisah **harus** memberi jawaban sama untuk keduanya —
membuktikan secara empiris bahwa ia tidak bisa membedakannya. Kalau ia memberi jawaban berbeda,
ia sedang membaca noise. Ini kontrol non-identifikasi, dan repo ini belum punya polanya.

**Transfer ke perp crypto:** ✅ **bertahan.** Ia tidak butuh `q_t`, tidak butuh quote, tidak butuh
asumsi soal independensi arah trade. Yang dibutuhkan hanya `Δp_t` covariance stationary dan
autokovarians lenyap di atas lag 1. Syarat kedua itu **harus diuji, bukan diasumsikan** — lihat E3.

---

### E3 — σ²_w dari representasi Wold: `σ²_w = θ(1)²σ²_ε` (§8.c)

Generalisasi E2 tanpa asumsi struktural sama sekali.

**Setup.** `p_t = m_t + s_t` dengan `m_t` random walk dan `s_t` stasioner zero-mean. Kalau `Δp_t`
covariance stationary, teorema Wold menjamin representasi MA ada:
`Δp_t = θ(L)ε_t` dengan `θ(L) = 1 + θ₁L + θ₂L² + …`

**Hasil** (§8.c, persamaan 8.c.13):

```
σ²_w = θ(1)² · σ²_ε        dengan  θ(1) = 1 + θ₁ + θ₂ + …
```

Jumlah koefisien MA, dikuadratkan, dikali varians inovasi. Tidak butuh model ekonomi apa pun.
Cek konsistensi: MA(1) memberi `σ²_w = (1+θ)²σ²_ε`, dan pada `θ = 0` (random walk murni)
`σ²_w = σ²_ε`. Keduanya harus jadi assert.

**Peringatan invertibilitas (§7.d).** Dari `(γ₀, γ₁)` ada **dua** solusi MA(1), dan keduanya
terkait `θ_invertible = 1/θ_noninvertible`. Hanya akar invertibel (`|θ| < 1`) yang boleh dipakai —
yang non-invertibel membuat rekursi `ε_t` divergen. Estimator wajib memilih akar invertibel
**secara eksplisit** dan gagal keras kalau tidak ada.

**Data:** hanya `p_t`.

**Kontrol positif.** MA(1) tersimulasi dengan `θ`, `σ_ε` diketahui → pulihkan `(1+θ)²σ²_ε`.
Verifikasi juga bahwa dua akar itu memang muncul dan pemilih akarnya mengambil yang benar.

**Kontrol negatif.** Random walk murni → `θ̂ ≈ 0`, `σ̂²_w ≈ σ̂²_ε`, rasio → 1. Estimator tidak boleh
"menemukan" komponen mikrostruktur di tempat yang tidak ada.

**Transfer ke perp crypto:** ✅ **bertahan** — asalkan orde MA dipilih cukup tinggi. Truncation
adalah pilihan peneliti, dan **setiap pilihan orde adalah satu look**. Deklarasikan orde sebelum
memandang hasil.

---

### E4 — Rasio varians horizon panjang (§7.c)

Estimator paling robust di sini, dan yang paling cocok untuk data kamu.

**Hasil.** Karena `Var(m_t − m_{t−k}) = k·σ²_w` dan efek mikrostruktur tidak tumbuh dengan `k`:

```
σ²_w ≈ Var(p_t − p_{t−k}) / k        untuk k besar
```

**Prediksi eksak yang bisa diuji** — turunkan sendiri di bawah model Roll:

```
Var(p_t − p_{t−k}) = k·σ²_u + 2c²        (k ≥ 1)
⇒  Var(p_t − p_{t−k})/k = σ²_u + 2c²/k
```

Estimator konvergen ke `σ²_u` **dari atas**, dengan bias meluruh persis sebagai `2c²/k`. Itu
bukan sekadar arah konvergensi — itu laju, dan laju bisa diuji.

**Data:** hanya `p_t`. Tidak butuh arah trade, tidak butuh model, tidak butuh fitting.

**Kontrol positif.** Simulasikan model Roll, plot `Var(Δ_k p)/k` terhadap `1/k`. Harus **linear**
dengan intersep `σ²_u` dan slope `2c²`. Regresi linear atas `1/k` memulihkan **keduanya** —
estimator gratis untuk `c` yang tidak bergantung pada tanda `γ̂₁`, jadi tidak punya masalah
`ABSTAIN` seperti E1. Kalau plotnya tidak linear, model Roll ditolak oleh datanya sendiri.

> `[SUPERSEDED]` Kalimat "estimator gratis untuk `c`" hanya berlaku di bawah model Roll murni
> (`λ = 0`). Di model generalized slope-nya `−2γ₁ = 2c(c+λ)`, bukan `2c²`, sehingga regresi
> tidak memberi identifikasi tambahan. Lihat `docs/VERIFY-hasbrouck-extraction.md`.

**Kontrol negatif.** Random walk murni → slope harus ≈ 0, rasio datar di semua `k`. Slope
signifikan pada data tanpa spread berarti estimatornya memungut sesuatu yang lain.

**Transfer ke perp crypto:** ✅✅ **paling kuat dari semuanya.** Tidak butuh sign, tidak butuh
quote, tidak butuh MA fitting, tidak sensitif terhadap agregasi aggTrades. Sumber sendiri
mengajukan pertanyaan terbuka: seberapa besar `k` harus? Satu hari? Seminggu? Sebulan?
**Itu pertanyaan yang harus dijawab pre-registration, bukan dengan memandang hasil.**

⚠️ **Jebakan crypto khusus:** pada horizon yang melintasi waktu funding (00:00 / 08:00 / 16:00
UTC) harga perp punya komponen deterministik yang bukan random walk. Rasio varians pada `k` yang
melintasi funding akan tercemar. Deklarasikan penanganannya sebelum mengukur.

---

### E5 — Batas bawah varians pricing error (§7.e)

**Definisi.** `s_t = p_t − m_t`, `σ²_s = Var(s_t)` — seberapa jauh harga transaksi menyimpang
dari harga efisien. Ukuran kualitas pasar yang tidak butuh konsep "dealer" vs "customer".

**Hasil.** `σ²_s` **tidak** teridentifikasi (karena `c` tidak). Tapi ia punya batas bawah:

```
σ²_s ≥ θ²σ²_ε = ½(γ₀ − √(γ₀² − 4γ₁²))
```

Batas ini **tepat** pada kasus informasi privat eksklusif, dan **understates** pada kasus
informasi publik eksklusif. Tidak ada batas atas secara umum: model dengan harga efisien "stale"
menghasilkan `σ²_s` lebih besar yang secara observasional ekuivalen.

**Data:** hanya `p_t`.

**Kontrol positif.** Simulasikan kedua kasus khusus. Pada `u_t = 0` batas harus **tepat sama**
dengan `c²`. Pada `λ = 0` batas harus **di bawah** `c²` secara ketat. Kontrol yang gagal
membedakan keduanya berarti implementasinya salah.

**Kontrol negatif.** Random walk murni → batas harus ≈ 0.

**Transfer:** ✅ bertahan, dengan label wajib **"batas bawah"**. Melaporkannya sebagai `σ²_s`
tanpa kualifikasi adalah overstatement — dan repo ini punya aturan tentang itu.

---

### E6 — VAR harga-trade: λ dan dekomposisi varians (§13.b, §13.c, §13.d)

Di sini `q_t` teramati, jadi identifikasi jauh lebih kuat.

**Model 1 — regresi langsung** (§13.b). Kalau `p_t` dan `q_t` sama-sama teramati, model
generalized Roll bisa diestimasi OLS langsung dari `Δp_t = −c·q_{t−1} + (c+λ)·q_t + u_t`.
Ketiga parameter `{c, λ, σ²_u}` teridentifikasi — tapi hanya di bawah asumsi ketat Model 1
(`q_t` iid). Autokorelasi `q_t` merusaknya; itu sebabnya Model 2–4 dan VAR umum ada.

**VAR umum** (§13.b, 13.b.32–36). Ambil `y_t = (Δp_t, q_t)′`:

```
y_t = φ₁y_{t−1} + φ₂y_{t−2} + … + φ_K y_{t−K} + ε_t ,    Ω = Var(ε_t)
```

Efek kontemporer masuk lewat elemen off-diagonal `Ω`. Lalu:

```
θ(1)Ω θ(1)′ = (I − φ(1))⁻¹ Ω (I − φ(1))⁻¹′
σ²_w = a Ω a′      dengan a = baris pertama (I − φ(1))⁻¹
```

**λ — cara yang benar** (§13.d). Sumber **eksplisit melarang** mengambil satu koefisien VAR pada
satu lag sebagai λ: variabel trade bertanda saling berkorelasi kontemporer dan serial, menimbulkan
multikolinearitas dan indeterminasi per-koefisien. Yang benar: **dampak harga kumulatif dari
impulse response** terhadap inovasi trade representatif.

> **RULE-EXTRACT-3.** λ = respons harga kumulatif dari IRF, bukan `φ[0,1]`. Implementasi yang
> membaca satu koefisien VAR sebagai λ salah menurut sumbernya sendiri.

**Dekomposisi varians** (§13.d):

```
σ²_{w,x} = a_Q Ω_Q a′_Q          komponen terkait-trade (absolut)
R²_w     = σ²_{w,x} / σ²_w       ukuran relatif
```

`R²_w` = proporsi varians harga efisien yang dapat diatribusikan ke aliran order. Inilah ukuran
asimetri informasi yang paling bisa dibandingkan lintas aset dan waktu.

**Peringatan identifikasi.** `Ω` umumnya tidak diagonal, jadi dekomposisi `σ²_w` menjadi
komponen trade dan non-trade **tidak teridentifikasi**. Faktorisasi Cholesky memberi batas atas
dan bawah, bukan titik. **Setiap urutan Cholesky adalah pilihan peneliti = satu look.**

**Perluasan variabel trade** (§13.c). Sumber menyarankan volume bertanda dan transformasi
nonliniernya: `q_t·V_t`, `q_t·V²_t`, `q_t·√V_t`, `q_t·log(V_t)` — karena aliran order lebih besar
membawa lebih banyak informasi (Kyle, Easley-O'Hara). **Setiap transformasi yang dicoba adalah
satu look.** Deklarasikan himpunannya di muka dengan cap `N_trials`.

**Data:** `p_t` **dan** `q_t`, plus volume kalau dipakai.

**Kontrol positif.** Simulasikan generalized Roll dengan `λ` ditanam → IRF kumulatif harus
memulihkannya. Uji daya: tanam `R²_w` yang diketahui, verifikasi estimator memulihkannya.

**Kontrol negatif.** Acak ulang `q_t` (rusak hubungan trade-harga, pertahankan distribusi
marginalnya) → `R̂²_w` harus jatuh ke ≈ 0 dan λ̂ tidak signifikan. Ini kontrol yang paling penting
di seluruh dokumen: VAR pada dua seri berautokorelasi **akan** menghasilkan angka cantik tanpa
hubungan sebab apa pun.

**Transfer ke perp crypto:** ✅ bertahan, dengan tiga catatan di §4.

---

### E7 — Kointegrasi dan information share (§17.b, §17.c)

Bab yang paling langsung menyambung ke `pairs_coint` dan `pairs_ou` yang sudah ada di board kamu.

**Setup — satu sekuritas, dua pasar** (§17.b):

```
m_t = m_{t−1} + u_t                      harga efisien BERSAMA
p_{1,t} = m_t + c₁·q_{1,t}
p_{2,t} = m_t + c₂·q_{2,t}
Var(q₁, q₂) punya korelasi kontemporer ρ_q
```

VMA orde 1: `Δp_t = ε_t + Θ ε_{t−1}` dengan `Θ` matriks 2×2.

**Restriksi identifikasi kunci** (§17.b, 17.b.6). Karena harga efisiennya sama, forecast jangka
panjang kedua pasar harus identik. Ini memaksa baris-baris `I + Θ` sama:

```
(1 + θ₁₁ , θ₁₂) = (θ₂₁ , 1 + θ₂₂)  ≡  β
```

Ini restriksi yang **bisa diuji** — kalau data menolaknya, premis harga efisien bersama salah.

**Varians random walk bersama:**

```
σ²_w = β Ω β′
```

**Information share** (§17.b, 17.b.8) — untuk `Ω` diagonal:

```
IS_i = β²_i · Var(ε_{i,t}) / σ²_w
```

Bagian varians harga efisien yang bersumber dari pasar `i`. Prediksi struktural: pasar dengan
biaya lebih rendah (`c₁ < c₂`) memberi sinyal presisi lebih tinggi → IS lebih tinggi.

**Ketika `Ω` tidak diagonal** (kasus nyata): hanya **batas atas dan bawah** yang bisa dihitung,
lewat faktorisasi Cholesky alternatif. Bukan angka tunggal.

> **RULE-EXTRACT-4.** Information share pada `Ω` non-diagonal WAJIB dilaporkan sebagai interval
> [bawah, atas] atas semua permutasi urutan. Melaporkan satu angka dari satu urutan yang dipilih
> setelah melihat hasilnya adalah look yang tak tercatat — dan bentuk p-hacking yang paling
> terkenal di literatur ini.

**VECM, bukan VAR** (§17.b, 17.b.9). Dengan kointegrasi, VMA **non-invertibel** — representasi VAR
murni tidak ada. Harus error-correction:

```
Δp_t = φ(L)Δp_t + (γ₁, γ₂)′·(p_{1,t−1} − p_{2,t−1}) + ε_t
```

`γ₁ < 0` berarti pasar 1 menyesuaikan ke pasar 2 (pasar 1 mengikuti). Implementasi yang memakai
VAR biasa di sini salah secara struktural, bukan sekadar kurang efisien.

**Data:** dua atau lebih seri harga dari sekuritas yang sama/terhubung arbitrase.

**Kontrol positif.** Simulasikan dua pasar dengan `c₁ < c₂` ditanam → IS pasar 1 harus lebih
tinggi. Verifikasi restriksi `(1+θ₁₁, θ₁₂) = (θ₂₁, 1+θ₂₂)` terpenuhi pada data simulasi.

**Kontrol negatif.** (a) Dua pasar identik (`c₁ = c₂`, `ρ_q = 0`) → IS harus ≈ 0,5/0,5.
(b) Dua random walk **independen** (tidak kointegrasi) → uji kointegrasi harus menolak, dan
pipeline harus berhenti di situ, **tidak** melaporkan information share atas pasangan yang tidak
kointegrasi.

**Transfer ke perp crypto:** ✅✅ **paling relevan.** Perp dan spot dihubungkan arbitrase, jadi
kointegrasi punya dasar ekonomi, bukan statistik kebetulan — persis kasus yang §17.d sebut sebagai
kointegrasi yang bersumber dari kondisi arbitrase. Pertanyaan "berapa information share perp vs
spot BTC" well-posed dan belum banyak dijawab dengan disiplin ini.

⚠️ **Tapi:** basis perp-spot **tidak** stasioner murni — funding rate menggesernya secara
sistematis. Vektor kointegrasi `(1, −1)` mungkin salah spesifikasi. Ini harus diuji, bukan
diasumsikan, dan penanganannya dideklarasikan di muka.

---

## 4. Transfer ke perp crypto — apa yang berubah

Sumbernya tentang ekuitas AS 2003: NBBO, specialist, jam bursa, auction buka/tutup, era
desimalisasi. Perbedaan yang **secara material** mengubah estimator:

### 4.1 `q_t` diketahui, bukan ditebak — keunggulan besar

Seluruh §13.a membahas kesulitan menyimpulkan arah trade dari quote: trade di midpoint, sequencing
yang salah, keterbatasan Lee-Ready. **Semua itu lenyap.** Binance aggTrades punya `isBuyerMaker`,
yang memberi sisi taker secara eksak.

```
isBuyerMaker == true   →  pembeli adalah MAKER  →  taker = PENJUAL  →  q_t = −1
isBuyerMaker == false  →  pembeli adalah TAKER  →  q_t = +1
```

⚠️ Membalik ini akan membalik tanda λ dan seluruh dekomposisi varians. **Wajib satu unit test
yang mengunci pemetaan ini** terhadap contoh yang diverifikasi tangan.

Konsekuensi: satu sumber error terbesar di literatur ini absen dari data kamu. Estimator yang di
sumbernya harus dibaca dengan kehati-hatian bisa diambil apa adanya.

### 4.2 aggTrades sudah teragregasi — pisau bermata dua

Binance menggabungkan fill berurutan pada harga sama dari satu order taker menjadi satu record.

- **Menguntungkan:** satu record ≈ satu peristiwa permintaan likuiditas, yang justru lebih dekat
  ke `q_t` teoretis daripada fill mentah.
- **Merugikan:** *hitungan* trade bukan hitungan trade sebenarnya. Estimator berbasis hitungan
  (PIN di §15 terutama) berubah artinya. Autokorelasi seri `q_t` juga terpengaruh — agregasi
  menghapus sebagian order splitting yang justru menjadi sumber autokorelasi itu.
- **Netral:** `Σ q_t·V_t` (CVD) tidak terpengaruh — agregasi mempertahankan volume bertanda.
  Ini menyambung langsung ke `PRECHECK-cvd-turnover` yang sudah ada.

### 4.3 Waktu peristiwa vs waktu kalender

`t` di sumber adalah **waktu trade** (event time), bukan waktu kalender. Pekerjaan bar-mu di waktu
kalender. `σ²_w` per-trade dan `σ²_w` per-detik adalah kuantitas berbeda dan tidak bisa
dibandingkan langsung.

> Deklarasikan basis waktu **sebelum** mengukur. Mengukur di keduanya lalu memilih yang lebih
> bagus adalah dua look, bukan satu.

### 4.4 24/7, tapi bukan tanpa struktur

Tidak ada gap semalam dan tidak ada auction, jadi penanganan khusus di sumber tidak berlaku.
Tapi ada analog crypto-nya:

- sesi Asia / Eropa / AS punya pola volume dan volatilitas
- **waktu funding (00:00 / 08:00 / 16:00 UTC)** menciptakan pola aliran dan harga yang mekanis
- rollover, likuidasi kaskade

Ini menempati peran yang sama dengan seasonality intraday di §4.a: komponen deterministik yang
merusak stasioneritas kovarians kalau tidak ditangani.

### 4.5 Funding merusak asumsi martingale

Ini yang paling dalam. Seluruh kerangka bertumpu pada `m_t` sebagai martingale. Harga perp
mengandung komponen funding yang **deterministik secara terjadwal** — bukan kejutan. Pada horizon
yang melintasi settlement funding, `m_t` bukan martingale murni.

Konsekuensi konkret: E4 (rasio varians) pada `k` yang melintasi funding akan tercemar. Perlu
diputuskan di muka: exclude, adjust, atau batasi `k` di bawah 8 jam. Ketiganya sah; memilih setelah
melihat hasil tidak.

Peta C2 kamu sudah mencatat funding accrual sebagai celah. Celah yang sama muncul lagi di sini —
tandanya ia struktural, bukan kebetulan.

### 4.6 Diskretisasi harga praktis absen

Tick BTCUSDT perp 0,1 USDT pada harga ~10⁵ ≈ 0,1 bps. Efektif kontinu. Sebagian besar literatur
2003 bergulat dengan pembulatan tick pada spread 1–5 sen dari harga $43 (≈2–12 bps) — di sana
diskretisasi material. Di sini tidak. Satu sumber bias hilang.

> `[SUPERSEDED]` Dua cacat. (1) Aritmetikanya: `0,1 / 10⁵ = 10⁻⁶ = 0,01 bps`, bukan 0,1 bps.
> (2) Premis harganya tidak pernah dicek — repo ini mengukur median mid **$63.719,75** lewat tiga
> rute, jadi nilai yang benar untuk instrumen ini pada data ini adalah **0,0157 bps**. (3) Dan
> kesimpulannya hanya berlaku separuh: spread median di data ini **sama dengan satu tick**, jadi
> untuk estimator spread (E1, E5) pembandingnya tick-lawan-spread dan keduanya sama besar —
> diskretisasi TIDAK absen di sana. Lihat `docs/VERIFY-hasbrouck-extraction.md` §6.

---

## 5. Implikasi look counter — dan ini yang paling mahal kalau bocor

Membaca sumber dan mengimplementasi estimator dengan kontrol simulasi = **0 look**. Data belum
disentuh.

Setiap **penerapan ke data nyata** dengan pilihan spesifikasi = **1 look**. Pilihan yang berbahaya,
karena tampak teknis padahal mengubah hasil:

| pilihan | di mana | catatan |
|---|---|---|
| orde lag VAR `K` | E6 | |
| horizon `k` | E4 | sumber sendiri bertanya "seberapa besar?" — tak terjawab |
| urutan Cholesky | E6, E7 | **paling berbahaya**; wajib interval, bukan titik |
| himpunan transformasi volume | E6 | `q V`, `q V²`, `q √V`, `q log V` = 4 look kalau semua dicoba |
| basis waktu (event vs kalender) | semua | |
| penanganan funding | E4, E7 | |
| jendela sampel | semua | |

> **RULE-EXTRACT-5.** Tidak ada estimator dari dokumen ini yang boleh menyentuh data nyata sebelum
> ada PREREG yang mendeklarasikan seluruh himpunan spesifikasi dengan cap `N_trials` eksplisit.
> Kontrol simulasi (E1–E7) tidak butuh PREREG — ia tidak memandang data.

---

## 6. Urutan implementasi yang disarankan

Dari yang paling robust ke yang paling rapuh — dan yang paling robust juga yang paling murah:

1. **E4** (rasio varians) — nol asumsi selain stasioneritas, tanpa fitting, tanpa sign. Kontrol
   positifnya (linearitas terhadap `1/k`) sekaligus menguji model Roll dan memberi `c` gratis.
2. **E3** (`θ(1)²σ²_ε`) — butuh fitting MA, hasilnya harus cocok dengan E4. **Dua estimator
   independen atas kuantitas yang sama = validasi silang.** Ketidakcocokan adalah temuan.
3. **E2** (`γ₀ + 2γ₁`) — kasus khusus E3, harus cocok pada MA(1). Assert ketiga.
4. **E1** (Roll) — hanya sebagai kontrol pipeline; diketahui bias di crypto.
5. **E6** (VAR + λ + `R²_w`) — butuh `q_t`; di sini keunggulan `isBuyerMaker` terbayar.
6. **E7** (VECM + information share) — perp vs spot; paling bernilai, paling banyak pilihan
   peneliti, paling butuh disiplin PREREG.
7. **E5** (batas bawah `σ²_s`) — kapan saja, murah, tapi selalu berlabel "batas bawah".

Langkah 1–3 saling memvalidasi tanpa satu pun butuh arah trade. Itu fondasi yang benar.

---

## 7. Yang BELUM diekstraksi

Dokumen ini mencakup §3, §4 (sebagian), §7, §8 (§8.a–8.d), §13, §17.b–17.c. Belum:

| seksi | isi | prioritas |
|---|---|---|
| §5, §6 | model sequential trade (Glosten-Milgrom), strategic trade (Kyle) | teori pendukung λ |
| §8.e–8.h | smoothing, filtering, pendekatan lain ke `σ²_s` | sedang |
| §9 | estimasi MA: MLE, moment, autoregresi; delta method, subsampling | **tinggi** — distribusi sampling, dibutuhkan untuk inferensi |
| §10, §11 | inventory control; invertibilitas, Wold ditinjau ulang | sedang |
| §12 | VMA/VAR, IRF, Cholesky, dekomposisi varians forecast | **tinggi** — mesin di balik E6 |
| §14 | model struktural: Glosten-Harris, MRR, Huang-Stoll | sedang |
| §15 | PIN | rendah — asumsi hitungan trade bentrok dengan aggTrades |
| §16 | apa yang sebenarnya diukur ukuran asimetri informasi | **tinggi** — kritis, murah |
| §18–§21 | limit order, eksekusi tak pasti, model ekuilibrium dinamis | hanya kalau ada data depth/bookTicker |
| §22 | asset pricing dengan biaya transaksi, ukuran likuiditas (Amihud dll.) | sedang |
| appendix | struktur pasar ekuitas AS 2003 | **lewati** — tidak berlaku |

Prioritas berikutnya: **§9 dan §12**. Tanpa §9 tidak ada standard error, dan estimator tanpa
distribusi sampling tidak bisa masuk gerbang mana pun. Tanpa §12 implementasi E6 akan menebak
mekanisme IRF dan Cholesky-nya.

> Sudah dikerjakan: `docs/EXTRACT-hasbrouck-s9-s12.md`.

---

## 8. Yang tidak bisa diverifikasi di ekstraksi ini

- **Peta glyph di §1.1 diverifikasi pada halaman yang saya baca visual, bukan seluruh 203 halaman.**
  Ia dipakai hanya untuk navigasi; RULE-EXTRACT-1 membuat ketidaklengkapannya tidak berbahaya.
- **Erratum §13.a ditemukan secara kebetulan**, bukan lewat pencarian sistematis. Kemungkinan besar
  ada erratum lain di seksi yang belum diekstraksi. Kontrol positif per estimator adalah pertahanan
  sebenarnya terhadap ini — bukan kecermatan membaca.
- **Prediksi `Var(Δ_k p)/k = σ²_u + 2c²/k` di E4 adalah turunan saya**, bukan kutipan sumber.
  Sumber hanya menyatakan konvergensi asimtotik. Turunan itu benar di bawah asumsi model Roll dan
  mudah diverifikasi ulang — tapi ia klaim saya, dan kontrol positifnya sekaligus jadi ujinya.
- **Vonis transfer crypto di §4 adalah penalaran, bukan pengukuran.** Setiap satu adalah hipotesis
  yang bisa diuji pada data kamu — dan pengujian itu sendiri adalah look yang harus dideklarasikan.
