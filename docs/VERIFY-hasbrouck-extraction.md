# VERIFY-hasbrouck-extraction — replikasi independen atas dua dokumen ekstraksi

**Status:** verifikasi selesai untuk klaim yang bisa dicek di mesin ini. **Nol** tambahan ke look
counter — tidak ada data nyata yang disentuh, seluruhnya simulasi dan aljabar.
**Yang diverifikasi:** `docs/EXTRACT-hasbrouck-001.md` dan `docs/EXTRACT-hasbrouck-s9-s12.md`,
keduanya tiba dari sesi lain pada 2026-08-06.

## Metode, dan mengapa ini bukan sekadar menjalankan ulang

Kedua dokumen datang lengkap dengan skrip verifikasinya sendiri. **Skrip itu tidak dijalankan.**
Replikasi yang memakai instrumen aslinya tidak membuktikan apa pun — ia hanya membuktikan bahwa
kode yang sama memberi hasil yang sama. Setiap angka di bawah berasal dari kode yang ditulis dari
nol, dan setiap klaim aljabar diturunkan tangan lebih dulu lalu diuji numerik. Ketika keduanya
berselisih, yang dicatat adalah selisihnya — bukan simulasi yang disetel sampai cocok.

Di tiga tempat verifikasi ditutup dengan **bentuk tertutup**, bukan simulasi saja, karena simulasi
tidak bisa membedakan "benar" dari "kebetulan cocok pada satu parameter".

---

## 1. Ringkasan vonis

| kelompok | klaim | vonis |
|---|---|---|
| momen Roll & generalized Roll | `γ₀`, `γ₁`, `γ_k = 0` | **BENAR** |
| E2 `σ²_w = γ₀ + 2γ₁` | identifikasi meski komponennya tidak | **BENAR** |
| E3 `σ²_w = θ(1)²σ²_ε`, dua akar `θ₁θ₂ = 1` | | **BENAR** |
| E4 `Var(Δ_k p)/k = σ²_w − 2γ₁/k` | slope `−2γ₁` | **BENAR** di MA(1); digeneralkan di §3b |
| E4 "estimator gratis untuk `c`" (001) | | **DIKOREKSI** — hanya sah bila `λ = 0` |
| E2 bias saat orde MA > 1 | dokumen: "+5,4 %" | **DIKOREKSI** — jauh lebih ganas, lihat §3 |
| peta keras-vs-senyap rumus AR | **klaimku sendiri** | **DIKOREKSI** — bocor dua arah, §5 |
| non-identifikasi = kontinum satu-parameter | | **BENAR** |
| Roll = batas bawah spread, `ĉ_Roll` = batas atas `c` | | **BENAR**, dan **dipertajam** — §4 |
| E5 tepat saat `σ²_u = 0`, understate saat `λ = 0` | | **BENAR** |
| E9 `σ²_w = σ²_ε/φ(1)²`, `φ(1) = 1 − Σφ` | | **BENAR**, dan diperluas ke sapuan θ — §5 |
| M4 mana `σ²_w` mana `Var(Δp)` | | **BENAR**, diukur langsung dari serinya |
| tick 0,1 USDT ≈ 0,1 bps | | **SALAH** faktor 10 — §6 |
| `isBuyerMaker = true → q = −1` | | **BENAR**, cocok konvensi repo |
| semua klaim TENTANG teks sumber | glyph, erratum §13.a, kata-kata §9.a | **TAK BISA DIVERIFIKASI** — §7 |

---

## 2. Yang lolos tanpa perubahan

Momen generalized Roll diturunkan ulang dari `Δp_t = −c·q_{t−1} + (c+λ)·q_t + u_t` dengan `q` iid
`±1` dan `u` iid, lalu dicek pada N = 4×10⁶. `γ₀`, `γ₁`, dan `γ_k = 0` untuk `k ≥ 2` semuanya
cocok. `σ²_w = γ₀ + 2γ₁ = λ² + σ²_u` cocok. Relasi dua akar MA(1) `θ₁·θ₂ = 1` cocok.

Turunan E4 juga benar: untuk `Δp` yang MA(1),
`Var(Δ_k p) = k·γ₀ + 2(k−1)·γ₁`, sehingga `Var(Δ_k p)/k = σ²_w − 2γ₁/k`. Regresi atas `1/k`
memberi intersep `σ²_w` dan slope `−2γ₁`.

---

## 3. KOREKSI — bias E2 di bawah misspesifikasi orde jauh lebih ganas dari "+5,4 %"

Dokumen s9-s12 menguji satu proses MA(3) (`θ = [0,6, −0,3, 0,15]`) dan melaporkan bias E2
**+5,4 %**. Angka itu **replikasi** — aku dapat +5,87 % pada seed dan N-ku sendiri, dan bentuk
tertutupnya memberi rasio 1,06. Tapi satu titik itu menyesatkan tentang besarnya bahaya.

Bentuk tertutup untuk MA(3): `γ₀ = (1+θ₁²+θ₂²+θ₃²)σ²_ε`,
`γ₁ = (θ₁ + θ₁θ₂ + θ₂θ₃)σ²_ε`, sedangkan yang benar `σ²_w = (1+θ₁+θ₂+θ₃)²σ²_ε`.
Empat pola tanda, aljabar dan simulasi cocok:

| `θ` | `E2 = γ₀+2γ₁` | benar `θ(1)²` | rasio |
|---|---|---|---|
| `[0,6, −0,3, 0,15]` (kasus dokumen) | 2,2225 σ² | 2,1025 σ² | **1,06** |
| `[0,6, 0,3, 0,15]` | 3,1225 σ² | 4,2025 σ² | **0,74** |
| `[−0,6, −0,3, −0,15]` | 0,7225 σ² | 0,0025 σ² | **289** |
| `[−0,6, 0,3, −0,15]` | **−0,1775 σ²** | 0,3025 σ² | **−0,59** |

Dua konsekuensi yang tidak ada di dokumen mana pun:

1. **`γ₀ + 2γ₁` bisa NEGATIF.** Pada pola berselang-seling ia mengembalikan varians negatif. Sebuah
   estimator varians yang negatif adalah kegagalan keras — dan itu kabar baik, karena ia terlihat.
2. **Ketika `θ(1)` mendekati nol, rasionya meledak** — 289× di baris ketiga. Di situ `σ²_w` sejati
   nyaris nol sementara `γ₀ + 2γ₁` tetap `O(σ²)`. Ini kegagalan senyap: angkanya positif, kecil,
   dan sama sekali salah.

Jadi amandemen dokumen ("uji `γ̂_k = 0` dulu; kalau ditolak, E2 gugur") bukan praktik baik yang
disarankan — ia **prasyarat**. Tanpa uji itu, E2 bisa melaporkan bilangan negatif atau salah dua
orde besaran, bergantung pada struktur autokovarians yang justru tidak diketahui.

**Satu klaim presisi yang tidak bertahan.** Dokumen melaporkan E4 pada `k = 4096` meleset hanya
+0,5 %. Replikasiku memberi **−4,49 %** pada `k` yang sama. Keduanya konsisten dengan estimator
yang berisik: pada N = 4×10⁶ dan `k = 4096` hanya tersisa ~10³ jendela tak-tumpang-tindih, jadi
kesalahan sampelnya besar. Angka "+0,5 %" itu bukan salah — ia **dilaporkan tanpa error bar**,
dan presisi yang disiratkannya tidak ada. E4 pada `k` sangat besar butuh SE, bukan satu angka.

### 3b. E4 digeneralkan — dan aturan implementasi yang keluar darinya

Turunan E4 di kedua dokumen berhenti di MA(1). Generalisasinya diturunkan dan diverifikasi dua
rute independen (bobot segitiga autokovarians, dan penghitungan koefisien atas `ε`), sepakat
sampai presisi mesin. Untuk `Δp` yang MA(q) dan `k > q`:

```
Var(Δ_k p)/k = σ²_w − (2/k)·Σ_{h=1}^{q} h·γ_h
```

Masih **eksak linear** dalam `1/k`, tapi asimetris dalam cara yang menentukan:

- **intersep tetap `σ²_w` untuk orde MA berapa pun.** Inilah alasan sebenarnya E4 robust — bukan
  "mendekati nilai benar tanpa tahu orde", melainkan intersepnya memang tidak bergantung orde.
- **slope `= −2γ₁` hanya bila `q = 1`.** Umumnya `−2Σ h·γ_h`. Pada fixture MA(3) dokumen sendiri
  slope eksaknya `−8,10e−09` sedangkan `−2γ₁` memberi `−7,50e−09` — meleset 8,0 %. Pada
  `θ = [1, −1, 1]` slope eksaknya **nol** sementara `−2γ₁ = +4,0e−08`: galat relatif tak hingga,
  sedangkan intersepnya tetap tepat.

> **Aturan implementasi E4.** Keluarkan **intersep saja, tidak pernah slope**, dan batasi grid ke
> `k` yang cukup besar. "Cukup besar" tidak bisa dirumuskan sebagai `k > q̂` saja: dengan order
> flow terautokorelasi — kasus nyata, dan yang sumbernya sendiri peringatkan — `Δp` berhenti
> menjadi MA berhingga. Diukur pada model Roll dengan `q_t` AR(1) `ρ = 0,5`: linearitas sisa
> 3,67 %, bias intersep +1,85 %, dan slope terukur tidak cocok dengan `−2γ₁` **maupun** `2c·s`.
> Grid yang memuat `k ≤ q` merusak intersep sampai puluhan persen.

Ini memperkuat, bukan membatalkan, amandemen C1 dokumen: E4 memang tidak memberi identifikasi
tambahan, dan sekarang alasannya tepat — yang berguna dari E4 adalah intersepnya, dan intersep itu
`σ²_w`, yang sudah punya nama.

---

## 4. DIPERTAJAM — E1, E5 dan C3 adalah tiga tampak dari SATU himpunan teridentifikasi

Dokumen s9-s12 membuktikan spread Roll adalah batas bawah dan `ĉ_Roll = √(−γ₁)` batas atas untuk
`c`, tapi menyajikannya sebagai dua fakta terpisah. Strukturnya lebih rapi, dan buktinya satu baris.

**Proposisi.** Karena `γ₁ = −c·s` dengan `s ≡ c + λ`:

```
ĉ_Roll = √(−γ₁) = √(c·s)
```

yaitu **rata-rata geometrik** `c` dan `s`. Dari AM-GM, sembarang rata-rata geometrik terletak di
antara kedua sukunya, jadi untuk `λ ≥ 0`:

```
c  ≤  ĉ_Roll  ≤  c + λ
```

dan kedua batas ketat hanya pada `λ = 0`. Itu membuktikan kedua klaim C3 sekaligus tanpa argumen
monotonisitas terpisah.

**Dan himpunan teridentifikasinya tertutup di kedua ujung.** Syarat `σ²_u ≥ 0` memberi
`c² ∈ [½(γ₀ − √(γ₀²−4γ₁²)), ½(γ₀ + √(γ₀²−4γ₁²))]`. Ujung bawahnya **persis** ekspresi batas E5.
Menambahkan restriksi ekonomi `λ ≥ 0` memotong ujung atas menjadi `c² ≤ −γ₁`. Jadi:

```
c²  ∈  [ ½(γ₀ − √(γ₀² − 4γ₁²)) ,  −γ₁ ]
        └── batas E5 ──┘          └ Roll ┘
```

**E5 adalah infimum himpunan teridentifikasi, Roll adalah supremumnya.** Bukan dua estimator
berbeda dengan dua peringatan berbeda — dua ujung satu interval.

Interval itu selalu tak kosong: syaratnya `γ₀ + 2γ₁ ≤ √(γ₀² − 4γ₁²)`, yang setelah dikuadratkan
menjadi `4γ₁(γ₀ + 2γ₁) ≤ 0`, benar karena `γ₁ < 0` dan `γ₀ + 2γ₁ = σ²_w ≥ 0`.

**Diverifikasi** [DIUKUR]: 0 pelanggaran atas 200.000 undian parameter acak; identitas rata-rata
geometrik tepat sampai presisi mesin; dan pada data tersimulasi (N = 4×10⁶, `c` = 4,0e−04,
`λ` = 3,0e−04) interval terestimasinya `c ∈ [3,783e−04, 5,288e−04]` memuat `c` sejati.

**Konsekuensi praktis.** Melaporkan `ĉ_Roll` sebagai estimasi titik untuk separuh-spread selalu
overstatement, dan melaporkannya sebagai estimasi spread selalu understatement — dua arah
berlawanan dari satu angka yang sama. Yang benar dilaporkan adalah intervalnya, dan interval itu
gratis: kedua ujungnya sudah dihitung dari `γ̂₀` dan `γ̂₁`.

**Satu kehati-hatian atas rumusanku sendiri.** Mengatakan "batas E5 **adalah** ujung bawah
interval" sebagian tautologis: keduanya ekspresi yang sama, jadi kesamaannya bukan temuan. Yang
merupakan teorema adalah bahwa varians pricing-error Wold/Beveridge-Nelson untuk MA(1) **sama
dengan** ekspresi itu — dan karena itulah ia jatuh tepat di ujung interval. Nyatakan yang kedua,
bukan yang pertama. Infimumnya tercapai (himpunannya tertutup), jadi ia minimum sejati, bukan
batas yang tak terjangkau.

> Ini **proposisi milik repo ini**, bukan sitasi Hasbrouck. Buktinya di atas; ia harus punya test
> sendiri sebelum dipakai.

---

## 5. E9 — rumus AR benar, dan petanya lebih berguna dari satu angka

`φ(L)Δp = ε` dengan `φ(L) = 1 − Σφ_i L^i` memberi `θ(1) = 1/φ(1)`, jadi
`σ²_w = σ²_ε/φ(1)²`. Diuji atas sapuan `θ ∈ {−0,8, −0,4, −0,1, 0,1, 0,4, 0,8}`, AR(30),
N = 2×10⁶ per titik — rumus yang benar memulihkan `σ²_w` dalam **0,3–0,9 %** di seluruh sapuan.

Yang lebih berguna adalah peta kegagalan bacaan literalnya, yang dokumen hanya sampel di satu `θ`:

| bacaan | perilaku sepanjang sapuan |
|---|---|
| `σ²_ε/φ(1)²` **(benar)** | rasio 0,991–1,009 |
| `σ²_ε/φ(1)` (tanpa kuadrat) | rasio **0,556–5,026** — kadang terlalu besar, kadang terlalu kecil |
| `σ²_ε/Σφ` ("jumlah koefisien") | **negatif untuk `θ < 0`**; positif tapi salah 0,69–9,47× untuk `θ > 0` |

Dua hal yang penting dan tidak ada di dokumen:

1. **Error bacaan-tanpa-kuadrat tidak berarah tetap.** Ia menyeberangi 1,0 di sekitar `θ ≈ 0`.
   Jadi tidak ada kalibrasi yang bisa menyelamatkannya, dan "meleset 1,67×" adalah sifat satu
   titik, bukan sifat bugnya.
2. **Tanda `θ` menentukan bug-nya keras atau senyap — tapi hanya di populasi.** Di nilai
   parameter sejati, `θ < 0` membuat bacaan `Σφ` memberi varians negatif (terlihat) dan `θ > 0`
   memberi bilangan positif yang masuk akal (tidak terlihat).

> **KOREKSI ATAS §5 BUTIR 2 — kesalahanku, ditemukan oleh verifikasi adversarial.** Aku sempat
> menulis bahwa karena tanda tangan mikrostruktur adalah `γ₁ < 0` ⇒ `θ < 0`, "di kasus yang
> benar-benar kita hadapi bug ini gagal keras". **Itu salah.** `Σφ̂` punya galat sampel, jadi
> tandanya adalah variabel acak, bukan fakta. Skala yang mengatur ada di `z = θ·√(N/K)`, dan
> kuukur sendiri (Yule-Walker, 40 seed/sel, N = 2×10⁵):
>
> | `θ` | `z` | janji peta ditepati |
> |---|---|---|
> | −0,40 | −23,1 | 40/40 |
> | −0,02 | −1,15 | 34/40 |
> | −0,01 | −0,58 | **29/40** |
> | +0,01 | +0,58 | 28/40 |
>
> Arah yang justru kritis ikut bocor: pada `θ = −0,01` rumus salah itu **gagal memberi varians
> negatif pada 27,5 % seed** — alarmnya diam tepat di seri yang bounce-driven. Rezim bocornya
> adalah `|θ|` kecil dengan `K` besar dan `N` terbatas, yaitu bar teragregasi dengan banyak lag —
> bukan sudut eksotis. Aturan yang bisa dipakai: butuh kira-kira `|θ| > 3·√(K/N)` sebelum tanda
> boleh diperlakukan sebagai informasi.
>
> Pelajarannya bukan "peta itu tak berguna" melainkan **jangan pernah menggantungkan keselamatan
> pada tanda sebuah estimasi**. RULE-EXTRACT-6 tetap berdiri, dan justru menguat: kontrol
> positifnya yang wajib, bukan harapan bahwa bugnya akan mengumumkan diri.

3. **Satu titik di mana rumus yang salah dua kali justru tepat.** Untuk bacaan `σ²_ε/Σφ`, rasio
   terhadap nilai benar adalah `1/(θ(1+θ))`. Itu sama dengan 1 ketika `θ² + θ − 1 = 0`, yaitu
   `θ = (√5 − 1)/2 ≈ 0,6180` — konjugat rasio emas, dan invertibel. Di titik itu formula yang
   mengabaikan kuadrat **dan** salah membaca `φ(1)` memberi jawaban tepat. Tidak ada nilai
   praktisnya; nilainya retoris, dan tajam: **"angkanya keluar masuk akal" bukan bukti apa pun
   tentang rumusnya.**

---

## 5b. Kontra-contoh urutan (M3) — benar, dengan satu penjagaan kata

Direplikasi penuh: `v ~ N(0,1)`, `ε ~ N(0, 0,7²)` independen, `s = v + ε`. `R²(v|ε) = 0,000000`
saat `ε` ditaruh pertama; `R²(v|s) = 0,670869` terhadap bentuk tertutup `σ²_v/(σ²_v+σ²_ε)` =
0,671141; `R²(v|s, ε) = 1,000000` **eksak**, karena `v = s − ε` secara identik sehingga pasangan
itu merentang `v`. Jadi daya inkremental `ε` naik dari 0 ke 0,3291 semata-mata karena posisinya.
Klaim `2ⁿ < n!` benar untuk `n ≥ 4` dan salah di `n = 2, 3` — seperti dinyatakan dokumen.

> **Penjagaan kata.** Kesimpulan yang sah adalah "menaruh sebuah variabel **pertama** tidak
> memberi batas atas", **bukan** "menaruhnya terakhir memberi batas atas". Maksimumnya bisa
> berada di posisi tengah, di dalam himpunan bagian. Itulah sebabnya RULE-EXTRACT-8 mewajibkan
> mengevaluasi seluruh `2ⁿ` himpunan bagian lalu mengambil min/max dari **nilai terhitung** —
> aturan itu benar, dan penalaran pintas apa pun tentang posisi mana yang ekstrem tetap dilarang,
> termasuk pintas "terakhir".

Konvensi varians harus dinyatakan eksplisit saat mengutip fixture ini: `N(0; 0,7)` ambigu antara
sd dan varians, dan angkanya berubah.

## 6. KOREKSI — aritmetika tick, dan koreksi atas koreksiku sendiri

Kedua dokumen menyatakan tick BTCUSDT perp 0,1 USDT pada harga ~10⁵ ≈ **0,1 bps**. Itu salah, dan
koreksi pertamaku juga tidak lengkap.

Aritmetikanya: `0,1 / 100.000 = 1×10⁻⁶ = 0,01 bps`. Benar sebagai aritmetika — **tapi atas harga
yang tidak pernah diperiksa siapa pun.** Repo ini sudah mengukur harganya sendiri, tiga rute
independen di `docs/EDA-execution-001.md` (grep `physical anchor`):

| rute | nilai |
|---|---|
| kueri sendiri, `depth_snapshots` binancef 2026-08-03, n = 41.494 | 0,0157 bps |
| `EDA-microstructure-001.md` §2a, 859.264 snapshot lintas 26 hari | 0,0156 bps |
| **jangkar fisik** — tick $0,10 ÷ median mid **$63.719,75** | 0,0157 bps |

Jadi nilai yang benar **untuk instrumen ini pada data ini adalah 0,0157 bps**. Dokumen meleset
~6,4×; koreksi pertamaku meleset 1,57× ke arah lain karena mewarisi premis "~10⁵" tanpa
mengeceknya. Yang menangkapnya adalah data repo sendiri, bukan penalaran ulang — dan itu
justru poinnya.

**Dan konsekuensinya lebih dari kosmetik.** Spread median di data ini **sama dengan satu tick**
(EDA-execution: satu round trip maker = satu tick = 0,0157 bps). Untuk kuantitas *perubahan harga*
klaim "diskretisasi praktis absen" bertahan — tick jauh di bawah volatilitas per-trade. Tapi
untuk **estimator spread** — E1, E5, dan interval `[E5, Roll]` di §4 — pembanding yang relevan
bukan tick lawan volatilitas melainkan **tick lawan spread**, dan di situ keduanya **sama besar**.
Buku selebar satu tick adalah justru rezim di mana diskretisasi mengikat untuk keluarga Roll.

Klaim §4.6 karena itu benar dalam satu lingkup dan menyesatkan di lingkup lain, dan lingkup yang
menyesatkan itu persis yang dipakai untuk membenarkan "satu sumber bias hilang" bagi estimator
spread. Pembanding 2003-nya sendiri benar (1–5 sen pada $43 = 2,3–11,6 bps).

---

## 7. Konvensi arah trade — cocok dengan repo, dan sudah punya test

Dokumen menyatakan `isBuyerMaker == true → taker = penjual → q_t = −1`. Repo memakai konvensi
yang sama: `aggressor_buy = not m` (grep `aggressor_buy = not m` di `STRATEGY.md`), dan rail §0.6
menyatakan `aggTrade.m` (isBuyerMaker) `true` → **SELL** aggressor. Lebih dari cocok — ia sudah
**diuji**: `tests/test_vision.py::test_aggressor_convention_matches_collector_normalizer`
membandingkan normalizer collector terhadap arsip pada data dengan kedua nilai aggressor hadir,
sehingga tidak bisa lolos pada konstanta.

Peringatan dokumen ("wajib satu unit test yang mengunci pemetaan ini") karena itu **sudah
terpenuhi**, dan test itu yang harus disitasi ketika estimator dibangun — bukan test baru.

---

## 8. Yang TIDAK bisa diverifikasi di sini, dan harus diperlakukan sebagai [UNVERIFIED]

PDF sumbernya **tidak ada di mesin ini** (dicari 2026-08-06: nihil di repo, Downloads, Desktop,
Documents). Karena itu setiap klaim *tentang teks sumber* tidak punya pemeriksa di sini:

- peta glyph `pdftotext` (§1.1 dari 001) — mekanismenya masuk akal dan RULE-EXTRACT-1 membuat
  ketidaklengkapannya tidak berbahaya, tapi tabelnya sendiri tak terverifikasi;
- **erratum #1** (§13.a menulis sell sebagai `q_t = +1`) — arah koreksinya konsisten dengan §3.a
  seperti dikutip, tapi keberadaan cacatnya tak bisa dicek;
- **erratum #2** (kata-kata §9.a) — *aljabarnya* sudah kuverifikasi mandiri dan `σ²_ε/φ(1)²`
  memang yang benar; yang tak terverifikasi adalah **bahwa sumber menulisnya keliru**;
- alasan penolakan MLE, penomoran seksi, dan seluruh nomor persamaan (`8.c.13`, `13.b.32–36`,
  `17.b.6/8/9`).

Pembedaan ini penting dan mudah kabur: **matematikanya terverifikasi, atribusinya tidak.** Selama
PDF-nya tidak ada di mesin ini, tidak boleh ada sitasi Hasbrouck yang diperlakukan sebagai
terperiksa.

Berkas `EXTRACT-hasbrouck-002.md` yang disebut ada "di direktori output" **tidak ada di mesin ini**
— jadi tidak ada klaim asing yang tercampur, dan tidak ada yang tertimpa.

---

## 9. Hak cipta — aturan dokumen sekarang punya penegak

Kedua dokumen mewajibkan PDF sumber tidak pernah masuk repo. Sebelum ini `.gitignore` **tidak punya
aturan apa pun** yang menutupinya (dicek: nol pola PDF, nol PDF terlacak). Ditambahkan aturan
selimut `refs/`, `*.pdf`, `*.djvu`, `*.epub` — selimut dan bukan per-berkas, supaya sumber
berikutnya tidak tiba tanpa perlindungan.

Isi kedua dokumen sendiri: persamaan dinyatakan ulang dalam notasi repo plus prosa tulisan sendiri,
dengan sitasi nomor seksi. Tidak ditemukan blok yang tampak sebagai salinan prosa sumber.

---

## 10. Look counter — klaim "nol" bertahan

Keduanya mengklaim menambah nol look. Klaim itu benar di bawah definisi repo: sebuah look adalah
spesifikasi yang **dievaluasi terhadap data nyata**. Kedua dokumen, dan verifikasi ini, hanya
menyentuh simulasi dan aljabar. Data eksplorasi tidak dibuka, LockBox tidak disentuh.

Yang perlu dijaga adalah batasnya, dan RULE-EXTRACT-5 sudah menyatakannya: begitu satu estimator
menyentuh partisi nyata, spesifikasinya harus sudah dideklarasikan dengan cap `N_trials`. Daftar
pilihan di 001 §5 dan s9-s12 §D.3 adalah kandidat cap itu, bukan sekadar catatan.

---

## 11. Yang harus dikerjakan berikutnya, menurut verifikasi ini

1. **Uji `γ̂_k = 0` untuk `k ≥ 2` adalah gerbang, bukan diagnostik.** §3 menunjukkan E2 bisa negatif
   atau meleset 289× tanpanya. Ia harus mendahului setiap pemakaian E2.
2. **E4 mengeluarkan intersep, tidak pernah slope**, dengan grid `k` yang cukup besar (§3b).
   Intersep tidak bergantung orde MA; slope bergantung, dan bisa nol saat `−2γ₁` tidak.
3. **Interval `[E5, Roll]` adalah keluaran yang benar untuk `c`**, dan gratis. Estimasi titik
   apa pun untuk separuh-spread dari `γ̂₁` saja adalah overstatement dengan arah yang diketahui.
   Tapi lihat §6: pada buku selebar satu tick, diskretisasi mengikat untuk keluarga Roll, jadi
   interval itu sendiri belum tentu memuat `c` sebelum efek tick diperhitungkan — **belum diuji**.
4. **E4 pada `k` besar butuh SE.** Presisi yang disiratkan "+0,5 %" tidak ada; subsampling harian
   (E11) memberinya tanpa kerja tambahan.
5. **Jangan gantungkan keselamatan pada tanda sebuah estimasi** (§5). Kontrol positif yang wajib,
   bukan harapan bahwa implementasi yang salah akan mengumumkan dirinya.
6. **Sitasi Hasbrouck tidak boleh diperlakukan terperiksa** sampai PDF-nya ada di mesin ini.
   Matematikanya berdiri sendiri; atribusinya tidak.

---

## 12. Catatan tentang proses verifikasi ini

Lima belas agen dipakai: lima menurunkan ulang, sepuluh mencoba **membantah** hasil kelimanya.
Empat temuan bertahan, enam gugur — dan yang gugur sebagian besar gugur pada *koreksi yang
diusulkannya*, bukan pada aljabar intinya. Dua kesalahan yang ditemukan adalah **milikku sendiri**:
peta keras-vs-senyap di §5, dan premis harga di §6 yang kuwarisi dari dokumennya tanpa mengecek
data repo. Keduanya kuuji ulang sendiri sebelum kuterima, dan keduanya terkonfirmasi.

Itu perilaku yang diharapkan dari lapisan refutasi, bukan tanda ada yang salah dengannya. Yang
perlu dicatat sebaliknya: **tak satu pun dari kedua kesalahanku akan tertangkap oleh test**. Peta
tanda itu benar di populasi dan hanya gagal secara probabilistik; premis harga itu aritmetika yang
benar atas angka yang salah. Keduanya kelas G — klaim tanpa pemeriksa — dan yang menemukannya
adalah seseorang yang diminta membantah, bukan seseorang yang diminta memeriksa.
