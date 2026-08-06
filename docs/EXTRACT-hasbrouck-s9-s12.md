# EXTRACT-hasbrouck-s9-s12 — §9 (inferensi), §12 (mesin VAR), + verifikasi EXTRACT-001

**Status:** DRAFT — belum ada satu pun return diskor. **Nol** tambahan ke look counter.
**Sumber:** sama dengan EXTRACT-001. © 2004 Joel Hasbrouck, all rights reserved.
**Prasyarat:** baca `docs/EXTRACT-hasbrouck-001.md` dulu. Bagian A **mengamandemen** dokumen itu.
**Aturan tetap:** RULE-EXTRACT-1 (persamaan hanya dari raster); PDF sumber tidak boleh masuk repo
(repo ini publik).

> **Diverifikasi ulang secara independen** oleh sesi Claude Code 2026-08-06 —
> `docs/VERIFY-hasbrouck-extraction.md`, yang mereplikasi ulang setiap klaim numerik dengan
> kode yang ditulis dari nol, bukan dengan menjalankan kembali skrip dokumen ini.

> **Catatan penamaan.** Saat menulis ini, disebutkan ada file bernama `EXTRACT-hasbrouck-002.md`
> di direktori output yang **bukan** tulisan sesi itu. **Berkas itu tidak pernah tiba di repo dan
> tidak ada di mesin ini** (dicari 2026-08-06, nihil) — jadi tidak ada yang bisa ditimpa dan tidak
> ada klaim asing yang tercampur. Catatan ini disimpan karena ia menjelaskan mengapa dokumen ini
> tidak bernama `-002`.

---

# BAGIAN A — Verifikasi EXTRACT-001

Setiap klaim numerik di 001 diuji Monte Carlo (N = 4×10⁶, seed tetap).
**Sepuluh klaim lolos utuh, tiga butuh koreksi.**

## A.1 Lolos tanpa perubahan

| klaim | teori | empiris |
|---|---|---|
| Roll `γ₀ = 2c² + σ²_u` | 5,400000e−07 | 5,399836e−07 |
| Roll `γ₁ = −c²` | −2,500000e−07 | −2,500682e−07 |
| Roll `γ_k = 0`, k≥2 | 0 | 2,2e−10 / −3,7e−10 |
| gRoll `γ₀ = c² + (c+λ)² + σ²_u` | 6,900000e−07 | 6,900576e−07 |
| gRoll `γ₁ = −c(c+λ)` | −2,800000e−07 | −2,800788e−07 |
| E2 `σ²_w = γ₀ + 2γ₁ = λ² + σ²_u` | 1,300000e−07 | 1,299001e−07 |
| E3 `σ²_w = (1+θ)²σ²_ε` | 1,297286e−07 | 1,297286e−07 |
| E3 relasi akar `θ₁·θ₂ = 1` | 1 | 1,000000 |
| E5 batas TEPAT saat `σ²_u = 0` | 1,600000e−07 | 1,598470e−07 |
| E5 batas UNDERSTATE saat `λ = 0` | < 1,6e−07 | 9,765e−08 |

Dua kasus khusus E5 menggigit ke arah yang benar — kontrol positif dan negatif untuk satu
estimator yang sama.

## A.2 KOREKSI C1 — slope E4 bukan `2c²`

001 menyatakan slope regresi `Var(Δ_k p)/k` atas `1/k` adalah `2c²`, memberi "estimator gratis
untuk `c`". **Salah di model umum.** Turunan yang benar untuk sembarang `Δp` yang MA(1):

```
Var(Δ_k p)   = k·γ₀ + 2(k−1)·γ₁
Var(Δ_k p)/k = (γ₀ + 2γ₁) − 2γ₁/k = σ²_w − 2γ₁/k
```

Slope = **−2γ₁ = 2c(c+λ)**, sama dengan `2c²` hanya bila `λ = 0`.

Verifikasi (c = 4e−4, λ = 3e−4, σ_u = 2e−4):

| k | empiris | prediksi `σ²_w − 2γ₁/k` | selisih rel |
|---|---|---|---|
| 1 | 6,900360e−07 | 6,900360e−07 | 0,0e+00 |
| 4 | 2,697076e−07 | 2,698054e−07 | −3,6e−04 |
| 32 | 1,473499e−07 | 1,472382e−07 | +7,6e−04 |
| 256 | 1,324399e−07 | 1,319173e−07 | +4,0e−03 |
| 1024 | 1,320442e−07 | 1,302758e−07 | +1,4e−02 |

Regresi atas `1/k`: slope 5,597e−07 vs `−2γ₁` = 5,603e−07; intersep 1,301e−07 vs `σ²_w` =
1,297e−07. `2c²` = 3,200e−07 — meleset faktor 1,75.

> **Amandemen 001 §E4.** Hapus klaim "estimator gratis untuk `c`". Regresi memulihkan `σ²_w`
> (intersep) dan `−2γ₁` (slope) — keduanya sudah tersedia dari `γ̂₀, γ̂₁` di bawah MA(1).
> **E4 tidak memberi identifikasi tambahan.** Nilainya di tempat lain — C2.

## A.3 KOREKSI C2 — nilai E4 adalah robustness; E2 bias bila orde MA > 1

Uji pada MA(3) sejati (`θ = [0,6, −0,3, 0,15]`, `σ_ε = 1e−4`):

| estimator | nilai | error |
|---|---|---|
| `σ²_w` sejati = `θ(1)²σ²_ε` | 2,1025e−08 | — |
| `γ₀ + 2(γ₁+γ₂+γ₃)` (orde benar) | 2,0955e−08 | −0,3 % |
| **`γ₀ + 2γ₁` (asumsi MA(1) salah)** | **2,2167e−08** | **+5,4 %** |
| `Var(Δ_k p)/k`, k = 256 | 2,0651e−08 | −1,8 % |
| `Var(Δ_k p)/k`, k = 4096 | 2,1134e−08 | +0,5 % |

E4 mendekati nilai benar **tanpa tahu orde MA**. E2 bias 5,4 % karena mengasumsikan MA(1).

> **Amandemen 001 §6.** Framing "E4/E3/E2 saling memvalidasi, ketidakcocokan adalah temuan"
> terlalu kuat. E2 hanya sah bila `γ_k ≈ 0` untuk `k ≥ 2`. Ketidakcocokan E4 vs E2 pada data nyata
> adalah **hasil yang diharapkan** — ia mengukur jarak proses dari MA(1), bukan bug. Uji
> `γ̂_k = 0` dulu; kalau ditolak, E2 gugur.

## A.4 KOREKSI C3 — non-identifikasi adalah KONTINUM; Roll adalah batas bawah spread

001 menggambarkan non-identifikasi sebagai "dua set parameter yang bertabrakan". Strukturnya
lebih tajam: **satu keluarga satu-parameter penuh.**

Dari `γ₁ = −c·s` dengan `s ≡ c + λ`: `s = −γ₁/c`, dan `σ²_u = γ₀ − c² − γ₁²/c²`. Syarat
`σ²_u ≥ 0` mengurung:

```
c² ∈ [ ½(γ₀ − √(γ₀²−4γ₁²)) ,  ½(γ₀ + √(γ₀²−4γ₁²)) ]
```

Untuk `γ₀ = 6,9e−07`, `γ₁ = −2,8e−07`, seluruh keluarga memberi `(γ₀, γ₁, σ²_w)` identik:

| c | λ | σ²_u | γ₀ | γ₁ | σ²_w |
|---|---|---|---|---|---|
| 3,787e−04 | 3,606e−04 | 0,0 | 6,9000e−07 | −2,8000e−07 | 1,3000e−07 |
| 4,000e−04 | 3,000e−04 | 4,000e−08 | 6,9000e−07 | −2,8000e−07 | 1,3000e−07 |
| **5,292e−04** | **0,0** | 1,300e−07 | 6,9000e−07 | −2,8000e−07 | 1,3000e−07 |
| 6,000e−04 | −1,333e−04 | 1,122e−07 | 6,9000e−07 | −2,8000e−07 | 1,3000e−07 |
| 7,393e−04 | −3,606e−04 | 0,0 | 6,9000e−07 | −2,8000e−07 | 1,3000e−07 |

Tiga hal yang keluar dari struktur ini, tak satu pun ada di 001:

1. **Ujung bawah interval = kasus informasi privat eksklusif** (`σ²_u = 0`). Kasus khusus yang
   "teridentifikasi" bukan asumsi terpisah — ia titik ujung keluarga.
2. **Estimator Roll `ĉ = √(−γ₁)` adalah anggota `λ = 0`** — interior, bukan ujung. Roll bukan
   "jawabannya"; ia satu titik yang dipilih oleh asumsi `λ = 0`.
3. **Konsekuensi baru yang bisa diuji.** Spread sebenarnya `2s = −2γ₁/c` menurun monoton terhadap
   `c`. Karena `λ ≥ 0` mensyaratkan `c ≤ √(−γ₁)`, spread minimum tercapai persis di estimator Roll:

   ```
   spread_Roll = 2√(−γ₁)  ≤  spread sebenarnya 2(c+λ)      untuk setiap λ ≥ 0
   ```

   **Spread Roll adalah batas bawah di bawah model generalized.** Contoh: Roll memberi 1,058e−03;
   ujung `σ²_u = 0` memberi 1,479e−03 — 40 % lebih besar.

   Ini menjelaskan arah temuan empiris §3.b (estimasi Roll jauh di bawah spread NYSE terukur)
   **tanpa** cerita autokorelasi `q_t`. Dua mekanisme independen mendorong searah.

> **Amandemen 001 §E1.** Di bawah model generalized dengan `λ ≥ 0`: spread Roll adalah **batas
> bawah**, `ĉ_Roll = √(−γ₁)` adalah **batas atas** untuk `c`. Laporkan sebagai batas, bukan titik
> — pola yang sama dengan E5.

## A.5 Yang tetap berdiri

RULE-EXTRACT-2 (`γ̂₁ > 0 → ABSTAIN`), peringatan invertibilitas, RULE-EXTRACT-3 (larangan membaca
satu koefisien VAR sebagai λ), erratum §13.a, seluruh analisis transfer crypto §4, dan daftar look
§5 — tidak berubah.

---

# BAGIAN B — §9: estimasi dan distribusi sampling

## B.1 Mengapa MLE ditolak — dan mengapa alasannya berbeda di crypto (§9.a)

Sumber menolak MLE Gaussian dengan dua alasan yang **arahnya berlawanan** untuk data crypto:

| alasan sumber (ekuitas 2003) | berlaku di perp crypto? |
|---|---|
| normalitas tak plausibel; grid harga kasar (tick $0,01), perubahan mayoritas 0/1/2 tick | **tidak** — tick 0,1 USDT pada ~10⁵ = 0,01 bps, efektif kontinu |
| likelihood eksak kurang penting karena observasi sangat banyak → asimtotik tercapai | **ya, lebih kuat** — jutaan print/hari |

Kesimpulannya sama (pakai moment estimator) tapi bukan karena alasan yang sama. Ini penting:
kalau nanti ada yang berargumen "boleh MLE karena harga crypto kontinu", jawabannya adalah
efisiensi asimtotiknya tidak sepadan biayanya pada N sebesar ini — **bukan** bahwa normalitas
sudah plausibel. Return crypto tetap fat-tailed.

## E8 — GMM / moment estimator MA(1) (§9.a)

```
γ₀ = E x²_t        = (1 + θ²)σ²_ε
γ₁ = E x_t x_{t−1} =      θ·σ²_ε
```

GMM memilih `{θ, σ²_ε}` yang meminimalkan deviasi momen sampel dari momen model, dan memberi
hasil distribusional.

**Batas skalabilitas yang dinyatakan eksplisit.** Untuk MA(q): `q+1` parameter, `q+1` autokovarians
tak-nol, tapi **`2^q` set parameter menghasilkan autokovarians yang sama dan hanya satu invertibel.**
Bahkan pada `q` sedang ini numerik yang berat; multivariat lebih buruk.

Generalisasi langsung dari dua akar MA(1) yang sudah diverifikasi (`θ₁θ₂ = 1`).
Konsekuensi: **jangan tulis pencari akar MA(q) sendiri.** Pakai jalur AR di E9.

## E9 — Estimasi lewat autoregresi terpotong (§9.a) ⭐

**Ide.** MA(1) punya representasi AR tak-hingga dengan koefisien meluruh geometris:

```
x_t = ε_t + θε_{t−1}   ⟺   x_t = −θx_{t−1} + θ²x_{t−2} − θ³x_{t−3} + … + ε_t
```

Potong di `K`, estimasi OLS: `x_t = φ₁x_{t−1} + … + φ_K x_{t−K} + ε^a_t`. `K` dipilih cukup besar
sehingga `ε^a_t` tak lagi berautokorelasi. Konsisten, dan **tidak butuh tahu orde MA sebenarnya**
— itu kelebihannya atas E8.

**Hasil kuncinya:** untuk `σ²_w` kita hanya butuh **jumlah** koefisien AR, bukan inversi
polinomial penuh:

```
φ(L) = 1 − φ₁L − φ₂L² − … − φ_K L^K
θ(1) = 1/φ(1)
σ²_w = θ(1)²σ²_ε = σ²_ε / φ(1)²        dengan  φ(1) = 1 − Σφ_i
```

Murah dan stabil secara numerik. Pada N sebesar milikmu, itu menentukan.

### ⚠️ ERRATUM SUMBER #2 — diverifikasi numerik, biaya tinggi

Teks §9.a menulis hasil ini sebagai `σ²_ε/φ(1)` (**tanpa kuadrat**) dan menyebut `φ(1)` sebagai
"jumlah koefisien autoregresif" (seharusnya **1 dikurangi** jumlah itu). Dua cacat dalam satu
kalimat. Diuji pada MA(1), `θ = −0,4`, `σ_ε = 1e−4`, AR(30), `σ²_w` sejati = 3,600e−09:

| formula | hasil | rasio vs sejati |
|---|---|---|
| `σ²_ε/φ(1)` — literal dari teks | 6,020e−09 | 1,67× |
| **`σ²_ε/φ(1)²`, `φ(1)=1−Σφ`** | **3,624e−09** | **1,007×** ✅ |
| `σ²_ε/Σφ` — "jumlah koefisien" literal | **−1,513e−08** | **negatif** |
| `σ²_ε/(Σφ)²` | 2,288e−08 | 6,36× |

Pembacaan "jumlah koefisien" menghasilkan **varians negatif**. Implementasi yang lalu memanggil
`abs()` atau `clip(0)` — refleks yang sangat umum — menghasilkan angka positif yang salah 6,4×
tanpa satu pun tanda peringatan.

> **RULE-EXTRACT-6.** `σ²_w` dari jalur AR WAJIB `σ²_ε/φ(1)²` dengan `φ(1) = 1 − Σφ_i`.
> Kontrol positif wajib: MA(1) tersimulasi dengan `θ` diketahui harus memulihkan `(1+θ)²σ²_ε`
> lewat jalur AR **dan** jalur MA langsung, dan keduanya harus cocok. Varians negatif harus
> GAGAL KERAS, tidak pernah di-clip.

## E10 — Impulse response dari koefisien AR (§9.a)

Koefisien MA (= impact multipliers = IRF) dibangun rekursif dari AR:

```
θ₀ = 1
θ₁ = φ₁
θ₂ = φ₁² + φ₂
…
θ_k = Σ_{j=1}^{min(k,K)} φ_j θ_{k−j}
```

**Untuk seri terdiferensiasi seperti `Δp_t`, yang bermakna adalah IRF KUMULATIF:**

```
E[ Σ_{j=0}^{k} Δp_{t+j} | ε_t ]
```

Diplot terhadap waktu, ini lintasan respons **level harga** terhadap kejutan — objek yang
RULE-EXTRACT-3 maksud sebagai λ, bukan koefisien VAR tunggal.

## E11 — Standard error: delta method vs subsampling (§9.b)

### Delta method

```
√T(f(θ̂) − f(θ)) → N(0, J Ω J′)      J = (∂f_i/∂θ_j)
```

Prosedur: AR(K) OLS → `φ̂`, `σ̂²_ε`, `Var(φ̂)` → susun `Ψ̂ = (φ̂, σ̂²_ε)` → Jacobian pemetaan ke
`{σ²_w, σ²_s}` (boleh numerik) → terapkan. Pada kasus Gaussian, `φ̂` dan `σ̂²_ε` asimtotik
independen, yang menyederhanakan `Ψ̂`.

**Dan sumber menolak pendekatan ini untuk kasus di tangan kita.** Delta bekerja baik bila
pemetaannya kira-kira linear; parameter dekomposisi random-walk dan IRF **sangat nonlinier**.
Itu penilaian sumber sendiri, bukan tambahan saya.

⚠️ Peringatan Wold yang menyertainya: bila `K` lebih rendah dari yang benar, atau `σ²_ε` punya
variasi deterministik, maka `ε^a_t` berautokorelasi dan/atau heteroskedastik — `Var(φ̂)` OLS biasa
tidak sah; pakai White atau Newey-West. **Untuk crypto ini bukan kemungkinan melainkan kepastian**
(volatilitas intraday jelas bervariasi). Newey-West adalah default, bukan opsi.

### Subsampling / Fama-MacBeth ⭐ — yang seharusnya dipakai

```
σ̂²_{w,d}  untuk d = 1..D  (satu per hari)
estimator = mean_d(σ̂²_{w,d})
SE        = sd_d(σ̂²_{w,d}) / √D
```

Sah formal bila hari-hari independen. Sumber menilainya cukup akurat untuk efek mikrostruktur
jangka pendek — domain kita.

**Mengapa ini tepat untuk repo ini,** di luar alasan sumber:

- Menghindari Jacobian pada pemetaan sangat nonlinier — masalah yang sumber sebut sendiri.
- Menghasilkan **deret waktu** `σ̂²_{w,d}`, bukan satu angka: bisa diperiksa untuk tren dan rezim,
  dan bisa diikat ke `recorded-damage.json` — hari dengan kerusakan terekam dikecualikan secara
  terprinsip, bukan ad hoc.
- Batas hari sudah jadi unit partisi datamu (`date=YYYY-MM-DD`). Subsampel jatuh persis di batas
  partisi. Tidak ada kerja tambahan.
- Memberi kontrol yang bisa diuji: bandingkan SE subsampling vs delta pada data simulasi.
  Selisih besar berarti nonlinearitasnya material — terukur, bukan diasumsikan.

## B.2 Starting values — nasihat sumber TIDAK berlaku di crypto (§9.b)

Sumber memperingatkan soal return semalam: perlakukan tiap hari sebagai sampel terpisah dan
**buang perubahan harga pertama tiap hari**, karena harga buka/tutup ditentukan mekanisme berbeda
(call auction vs continuous) dan dinamika harga efisien semalam berbeda.

**Di perp crypto ini tidak berlaku secara mekanis.** Tidak ada auction, tidak ada penutupan,
tidak ada gap. Perubahan harga pertama tiap hari UTC adalah perubahan biasa. **Membuangnya
membuang data sah** — pada seluruh backfill arsip plus data live, itu bukan jumlah sepele
(hitungan hari lubang dan partisi: `docs/STATUS.md` §2, satu-satunya pemiliknya).

Tapi motivasinya tetap hidup dalam bentuk lain, dan lebih halus:

- Batas hari UTC **bertepatan dengan settlement funding 00:00** — ada peristiwa mekanis tepat di
  batas subsampel. Bukan ketiadaan dinamika, melainkan dinamika berbeda.
- Batas hari juga batas arsip Vision. Hari dengan lubang terekam punya sifat statistik berbeda
  karena kerusakan, bukan karena pasar.

> **RULE-EXTRACT-7.** Subsampling harian sah di crypto **tanpa** membuang perubahan pertama tiap
> hari. Yang wajib dideklarasikan sebagai gantinya: perlakuan terhadap (a) jendela funding 00:00
> UTC di batas subsampel, (b) hari yang punya entri di `recorded-damage.json`. Keduanya keputusan
> pre-registration, bukan pilihan saat menganalisis.

Untuk VAR orde `K`: sumber memberi dua opsi — set perubahan tertinggal yang tak teramati ke nol,
atau mulai sampel dari `Δp_K`. Opsi kedua lebih bersih dan biayanya nol pada N ini.

---

# BAGIAN C — §12: mesin VAR, IRF, Cholesky

## M1 — VMA, VAR, IRF (§12.a, §12.b)

Untuk `y_t = (y_{1,t}, y_{2,t})′` dengan VMA `y_t = θ(L)ε_t`, `Ω = Var(ε_t)`:

```
E[y_{t+k} | ε_t] = θ_k ε_t          θ₀ = I
```

IRF = deret entri `(i,j)` dari `θ(L)`: efek variabel `j` ke variabel `i` lintas waktu.

**Masalah yang membuat IRF mentah tidak bermakna.** "Kejutan satu unit ke `y_2` menyebabkan
`y_{1,t+k}` bergerak `θ_{k,1,2}`" mengandaikan satu variabel bisa diubah sementara yang lain
ditahan. Bila kedua inovasi berkorelasi kontemporer, **kejutan seperti itu praktis tak pernah
terjadi**. Analogi sumber: return harian dua indeks tumpang tindih — hari di mana satu naik dan
satunya diam itu langka.

Analogi crypto langsung: kejutan pada `Δp_t` dengan `q_t` ditahan konstan hampir tidak pernah
terjadi, karena trade adalah yang menggerakkan harga.

## M2 — Faktorisasi Cholesky (§12.c)

```
Ω = F′F        F lower triangular
ε_t = F′z_t    z_t ~ N(0, I)
```

Untuk `Ω` 2×2 dengan korelasi `ρ`:

```
        ⎛ σ₁      ρσ₂        ⎞              ⎛ σ₁        0        ⎞
F   =   ⎜                    ⎟      F′  =   ⎜                    ⎟
        ⎝ 0   √(1−ρ²)·σ₂     ⎠              ⎝ ρσ₂   √(1−ρ²)·σ₂   ⎠
```

Struktur faktornya: `z₁` menjelaskan seluruh `x₁`, `z₂` hanya bagian `x₂` yang tidak ada di `x₁`.
**Murni konsekuensi urutan** — membalik urutan menjadikan `x₂` penggerak utama.

**VMA terorthogonalisasi:**

```
y_t = F′z_t + θ₁F′z_{t−1} + θ₂F′z_{t−2} + …
```

`θ_i F′` adalah koefisien IRF terorthogonalisasi. Dengan `z_t = (1, 0, …, 0)′`, `F′z_t` memberi
efek kontemporer kejutan satu-SD ke `ε_1` **termasuk** efeknya ke semua variabel lain;
`θ₁F′z_t` memberi efek periode berikutnya.

> Menegaskan arah kausal setara dengan menegaskan struktur rekursif. Cholesky adalah alat
> **menghitungnya**, bukan alat menemukannya. Urutan adalah asumsi yang kamu bawa.

Catatan implementasi: untuk menjelajah urutan alternatif, permutasikan variabel pada matriks
koefisien dan kovarians lalu hitung ulang Cholesky — tak perlu mengestimasi ulang modelnya.

## M3 — Atribusi daya penjelas: runtuhnya asumsi "pertama = maksimum" (§12.d, §12.e) ⭐

```
β′Var(x)β = β₁²σ₁² + 2β₁β₂σ₁₂ + β₂²σ₂²
```

Bila `σ₁₂ = 0` dekomposisinya bersih. Bila `σ₁₂ ≠ 0`, suku silang harus diatribusikan, dan ada dua
ekstrem. **Karena suku kovarians bisa negatif, tidak bisa dikatakan a priori urutan mana yang
memaksimalkan daya penjelas.**

### Kontra-contoh, diverifikasi numerik

Kasus ekstraksi sinyal dari sumber: `v ~ N(0, σ²_v)`, `s = v + ε`, `ε ⊥ v`.
Direplikasi (σ_v = 1, σ_ε = 0,7, N = 5×10⁵):

| proyeksi | R² |
|---|---|
| `v` pada `ε` saja — ε "pertama" | 0,000003 |
| `v` pada `s` saja | 0,669547 |
| `v` pada `(s, ε)` — ε "terakhir" | 1,000000 |

R² inkremental `ε`: **0,0000 kalau pertama, 0,3305 kalau terakhir.** Daya penjelasnya
dimaksimalkan dengan menaruhnya **terakhir** — kebalikan persis dari intuisi standar.

Ini menghancurkan pintasan yang tersirat di 001 ("taruh trade pertama untuk batas atas").
Keduanya harus dihitung, lalu `min`/`max` diambil dari hasilnya.

> **RULE-EXTRACT-8** (menggantikan RULE-EXTRACT-4 di 001). Batas information share dan dekomposisi
> varians WAJIB dihitung dengan **mengevaluasi seluruh himpunan bagian**, lalu ambil min dan max
> dari nilai terhitung. Menganggap satu urutan sebagai batas atas **berdasarkan penalaran**
> dilarang — sumber memberi kontra-contoh eksplisit, sudah direplikasi di sini.

### Kombinasi, bukan permutasi

R² inkremental dari menambahkan `x*` hanya bergantung pada **himpunan** yang mendahului, bukan
urutannya. Jadi `2ⁿ` kasus, bukan `n!`:

| n | `2ⁿ` | `n!` |
|---|---|---|
| 3 | 8 | 6 |
| 5 | 32 | 120 |
| 9 | **512** | **362.880** |

Catatan presisi: `2ⁿ < n!` baru berlaku `n ≥ 4`; pada `n = 3` justru `8 > 6`. Tidak mengubah apa
pun praktis, tapi klaim "lebih kecil" di sumber tidak universal.

Ini yang membuat batas information share **layak dihitung** pada spesifikasi VAR realistis:
`Δp_t`, `q_t`, `q_tV_t`, `q_t√V_t`, `q_t log V_t` → n = 5 → 32 evaluasi, bukan 120.

## M4 — Dekomposisi varians forecast (§12.e)

```
forecast error (lead k) = ε_{t+k} + θ₁ε_{t+k−1} + … + θ_{k−1}ε_{t+1}
kovariansnya            = Σ_{j=0}^{k−1} θ_j Ω θ_j′
```

Dengan `Ω` diagonal ini terpisah bersih; dengan off-diagonal gunakan batas M3. Limit `k → ∞`
memberi total varians variabel sistem, `Var(y_t)`.

### ⚠️ PERANGKAP YANG DINAMAI SUMBER — wajib jadi test

```
Σ_j θ_j Ω θ_j′   ≠   [Σ_j θ_j] Ω [Σ_j θ_j]′
```

Sumber menyebut kebingungan di sini "especially problematic" justru ketika salah satu variabelnya
`Δp_t` — persis kasus kita. Keduanya **kuantitas berbeda secara ekonomi**:

| ekspresi | apa yang diukur |
|---|---|
| `Σ_j θ_j Ω θ_j′` (jumlah dari hasil kali) | limitnya `Var(Δp_t)` |
| `[Σ_j θ_j] Ω [Σ_j θ_j]′` (hasil kali dari jumlah) | `θ(1)Ωθ(1)′`, elemen pertamanya `σ²_w` |

Demo numerik (VMA orde 2, Ω non-diagonal): 1,358 vs 2,445 — **faktor 1,8**. Keduanya bilangan
positif yang masuk akal. Tidak ada yang akan error.

> **RULE-EXTRACT-9.** Test wajib: assert bahwa `Σθ_jΩθ_j′` dan `θ(1)Ωθ(1)′` menghasilkan angka
> **berbeda** pada fixture non-trivial, dengan komentar yang menyebut mana `σ²_w` dan mana
> `Var(Δp)`. Test yang mengunci perbedaan itu satu-satunya yang menangkap tertukarnya.

---

# BAGIAN D — konsekuensi gabungan

## D.1 Estimator yang direkomendasikan, direvisi

| # | estimator | jalur | SE |
|---|---|---|---|
| 1 | `σ²_w` | AR(K) OLS → `σ²_ε/φ(1)²` (E9) | subsampling harian (E11) |
| 2 | `σ²_w` cek silang | `Var(Δ_k p)/k` (E4) | subsampling harian |
| 3 | uji orde MA | `γ̂_k = 0` untuk `k ≥ 2` | Newey-West |
| 4 | batas `σ²_s` | `θ²σ²_ε` (E5) | subsampling harian |
| 5 | batas spread | `[2√(−γ̂₁), ∞)` (C3) | subsampling harian |
| 6 | λ | IRF kumulatif dari VAR (E10, M1) | subsampling harian |
| 7 | `R²_w` | batas atas seluruh `2ⁿ` himpunan bagian (M3) | subsampling harian |
| 8 | information share | batas atas seluruh himpunan bagian (M3, E7) | subsampling harian |

Butir 1 dan 2 mengukur kuantitas sama lewat jalur independen; ketidakcocokan besar berarti `K`
terlalu kecil atau stasioneritas gagal. Butir 3 menentukan apakah E2 sah sama sekali.

## D.2 Daftar erratum sumber (kumulatif)

| # | seksi | isi | biaya kalau diikuti literal |
|---|---|---|---|
| 1 | §13.a | sell ditulis `q_t = 1`, seharusnya `−1` | seluruh λ terbalik tanda |
| 2 | §9.a | `σ²_w = σ²_ε/φ(1)` tanpa kuadrat; `φ(1)` disebut "jumlah koefisien AR" (seharusnya 1 − jumlah) | varians negatif, atau salah 1,7–6,4× |
| 3 | §12.e | `2ⁿ < n!` dinyatakan umum; baru berlaku `n ≥ 4` | tidak ada — kosmetik |

Dua dari tiga ditemukan **secara kebetulan** saat membaca untuk keperluan lain, bukan lewat
pencarian sistematis. Kesimpulannya bukan "baca lebih teliti" melainkan: **kontrol positif per
estimator adalah satu-satunya pertahanan yang berskala.** Erratum #2 ditemukan oleh simulasi,
bukan oleh mata.

## D.3 Look counter — pilihan baru dari §9 dan §12

Ditambahkan ke daftar 001 §5:

| pilihan | di mana | catatan |
|---|---|---|
| orde truncation AR `K` | E9 | menentukan `σ²_w`; kriteria pemilihan (AIC/BIC/uji autokorelasi) harus dideklarasikan |
| estimator kovarians (OLS/White/Newey-West) + bandwidth | E11 | Newey-West wajib di crypto; bandwidth adalah look |
| unit subsampel (hari/sesi/jam) | E11 | hari adalah default alami — deklarasikan penyimpangan |
| perlakuan jendela funding di batas subsampel | RULE-EXTRACT-7 | |
| perlakuan hari ber-`recorded-damage` | RULE-EXTRACT-7 | |
| himpunan variabel dalam VAR | M3 | menentukan `n`, dan `2ⁿ` evaluasi batas |

## D.4 Yang tidak bisa diverifikasi di dokumen ini

- **Verifikasi Monte Carlo membuktikan formulanya, bukan penerapannya.** Semua simulasi memakai
  `q_t` iid dan `u_t` Gaussian homoskedastik. Data nyata melanggar keduanya. Yang terbukti:
  aljabar dan implementasinya benar. Yang tidak: bahwa asumsinya berlaku di datamu.
- **Erratum #2 diverifikasi pada MA(1) dengan satu nilai `θ`.** Kesimpulan `/φ(1)²` mengikuti
  aljabar (`θ(1) = 1/φ(1)`, `σ²_w = θ(1)²σ²_ε`), jadi tidak bergantung nilai itu — tapi satu nilai
  bukan sapuan parameter.
- **§9.c (Case Study I) dan sebagian §12.a–12.b belum dibaca visual seluruhnya.** Yang diekstraksi
  adalah hasil yang dipakai E9/E10/M1–M4; detail lain mungkin terlewat.
- **Klaim "Roll = batas bawah spread" (C3) adalah turunan saya**, mengikuti aljabar keluarga
  non-identifikasi dan diverifikasi numerik pada satu `(γ₀, γ₁)`. Sumber tidak menyatakannya.
  Perlakukan sebagai proposisi milik repo ini dengan buktinya di test — **bukan** sitasi Hasbrouck.
- **Perbandingan SE delta vs subsampling belum dijalankan.** Sumber menilai delta bermasalah karena
  nonlinearitas; saya belum mengukur selisihnya. Itu kontrol murah dan seharusnya jadi test pertama
  saat E11 diimplementasi.
