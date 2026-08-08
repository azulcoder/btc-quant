# PLAN-orderflow-ui-001 — design system berlapis. DITULIS, TIDAK DIEKSEKUSI.

**Tanggal: 2026-08-08.** Branch `orderflow-terminal`. Nol berkas `dashboard/` diubah. Setiap
usulan menyebut angka dari `docs/DIAG-orderflow-ui-audit-001.md`; §6 mencatat apa yang
**dikeluarkan** karena tidak punya angka pendukung.

**Syarat merge untuk setiap lapis: paritas screenshot terhadap `reports/ui-baseline/` harus
hijau, atau perbedaannya dijelaskan per-panel.** Baseline itu sudah ada dan kontrolnya sudah
terbukti menyala (audit §6).

> **REVISI 2026-08-08 (kedua), setelah `DIAG-orderflow-canvas-001` dan
> `DIAG-orderflow-hierarchy-001`.** Tiga hal di rencana v1 dibatalkan oleh angka baru, dan
> pembatalannya ditulis di tempatnya masing-masing, bukan dirapikan: Lapis 1 **tidak punya
> pekerjaan** (ketiga kegagalan kontras adalah artefak scanner), prasyarat "kanvas liar"
> untuk Lapis 0 **tidak terbukti** (kanvas 100 % token-sourced), dan Lapis 3 mendapat angka
> per-panel yang jauh lebih kecil dari agregatnya — beserta alasan kenapa angka itu menjawab
> pertanyaan yang lebih kecil.

**Temuan yang membentuk seluruh rencana ini: sistem token SUDAH ADA dan sedang dilangkahi.**
Ini bukan proyek "bangun design system dari nol" — ini proyek "tutup kebocoran".

---

## LAPIS 0 — token: menyerap literal yang melangkahi sistem yang sudah ada

> **PRASYARAT v1 DICABUT [2026-08-08].** v1 mensyaratkan Lapis 0 menunggu audit kanvas,
> dengan alasan: kalau kanvas memuat mayoritas warna, merapikan CSS saja akan membuat
> inkonsistensi **lebih** terlihat. Auditnya sudah dijalankan dan **membalik premisnya**
> [DIUKUR 2026-08-08]: dari **174 assignment warna kanvas, LITERAL = 0**; 109 membaca token
> langsung, 65 sisanya tetap berujung token; 20 hex literal yang ada semuanya duduk di posisi
> *fallback* `cssVar('--x', '#hex')` dan **cocok persis** dengan nilai tokennya (nol drift).
> **Total warna distinct terminal tetap 74** — kanvas tidak menambah satu hue pun, jadi angka
> 74 itu **bukan batas bawah**. Kanvas sudah berada di ujung yang benar; **CSS-lah yang
> tertinggal**, dan Lapis 0 boleh berjalan tanpa menunggu apa pun.

**Angka yang membenarkannya** (audit §1): spacing **82 distinct** (10× patokan), warna **74**
(5×), font-size **27** (4×), radius **11** (3×) — sementara `var(--fs-xs)` sudah dipakai 88×,
`var(--sp-2)` 70×, `var(--radius)` 35×, dan **63 custom property sudah terdeklarasi**.

**Yang AKAN terserap** (dari 10 teratas tiap dimensi, terukur):

| dimensi | literal yang bisa dipetakan ke token yang sudah ada | pemakaian |
|---|---|---|
| spacing | `2px`, `1px`, `4px`, `3px`, `6px`, `10px`, `14px` | **168 pemakaian** |
| radius | `3px`, `2px`, `8px`, `4px`, `1px`, `7px`, `10px` | **38 pemakaian** |
| font-size | `12px`, `13.5px`, `14px`, `11px` | **11 pemakaian** |

**Yang TIDAK boleh terserap, dan ini pemisahan yang penting:**

- **`50%` pada radius** (5 pemakaian) bukan langkah skala — ia "lingkaran". Token geometris,
  bukan token skala; kalau dicampur ke skala radius ia akan salah pada setiap ukuran.
- **Ambang bisnis bukan token.** Tier ukuran `{sig:1e5, large:2.5e5, huge:1e6, whale:5e6}`
  (sensus §5) adalah **parameter riset**, bukan keputusan visual. Memindahkannya ke berkas
  token akan membuat perubahan ambang terlihat seperti perubahan tema. **Keluar.**
- **`1px` sebagai border** vs `1px` sebagai gap adalah dua keputusan berbeda yang kebetulan
  bernilai sama; menyatukannya lewat satu token akan menautkan keduanya selamanya.
  Butuh dipisah saat pemetaan, bukan disapu rata.

**Risiko.** Substitusi mekanis akan mengubah piksel di tempat yang tidak diniatkan (misalnya
`3px` → `var(--sp-1)` kalau `--sp-1` ternyata 4px). **Verifikasi:** paritas screenshot 27
panel; setiap panel yang berubah harus dibenarkan satu per satu atau substitusinya dibatalkan.

### LAPIS 0b — token yang bisa dibaca kanvas: **sudah ada, jangan bangun ulang**

Diminta sebagai lapis baru: satu sumber token yang dibaca kanvas lewat `getPropertyValue`
saat init, bukan disalin sebagai literal. **Terukur, mekanismenya sudah persis itu**
[DIUKUR 2026-08-08]: `cssVar()` memanggil
`getComputedStyle(document.documentElement).getPropertyValue(name)`, dan `pal()` membangun
peta **17 token setiap draw** — sengaja tidak di-cache agar toggle tema langsung terpakai.

Jadi yang tersisa untuk 0b bukan membangun, melainkan **dua kebersihan kecil dengan
angkanya**:

1. **20 fallback literal** di `cssVar('--x', '#hex')` adalah duplikasi nilai token. Semuanya
   saat ini **cocok persis**, jadi mereka tidak berbahaya hari ini — bahayanya muncul kalau
   token berubah dan fallback tidak. **Usul: satu test yang menegakkan kecocokan itu**, bukan
   penghapusan fallback (fallback melindungi dari `getPropertyValue` kosong saat init).
2. **Dua ukuran font kanvas, 6px dan 7px, berada DI BAWAH `--fs-3xs: 9px`** — literal yang
   melangkahi skala, dan letaknya di kanvas, bukan CSS. Ini satu-satunya kebocoran token
   nyata yang ditemukan di sisi kanvas.

**Verifikasi 0b:** test kecocokan fallback↔token berjalan tanpa browser (parsing CSS + JS),
jadi ia bisa masuk CI — tidak seperti paritas piksel.

---

## LAPIS 1 — perbaikan satu-baris berdampak besar

**Tabular numerals: TIDAK ADA PEKERJAAN — dan sekarang diperiksa DUA KALI.** Audit §2
mengukur 49 kemunculan di CSS. Pemeriksaan kanvas yang terpisah [DIUKUR 2026-08-08]
menemukan **nol** flag fitur font di kanvas, **tetapi** angka kanvas tetap tabular karena
woff2 yang dikirim diukur langsung: kesepuluh digit ber-advance identik **600/1000-em**,
`post.isFixedPitch = 1`. Tabularitas datang dari font monospace-nya, bukan dari flag.

**Defect font yang NYATA, ditemukan menggantikan yang tidak ada:** woff2 IBM Plex Mono yang
di-subset hanya memapar GSUB `['ccmp','dnom','frac','numr']` — **tidak ada fitur `zero`,
tidak ada glyph slashed-zero**. Maka `slashed-zero` di **16 lokasi CSS** dan
`font-feature-settings: 'zero' 1` **INERT di DOM maupun kanvas**. Perbaikannya bukan CSS:
**subset font harus menyertakan fitur `zero`**, atau ke-16 deklarasi itu dihapus karena
berbohong tentang apa yang dirender. **Usul: hapus deklarasinya** — lebih murah, dan sebuah
deklarasi yang tidak berefek adalah dokumentasi yang salah.

**Kontras: ~~tiga perbaikan~~ NOL pekerjaan — ketiganya artefak scanner** (§di bawah):

> **[PEMBATALAN 2026-08-08]** Ketiga "kegagalan" di v1 adalah **artefak scanner-ku sendiri**,
> bukan cacat produk. Dua sebab, keduanya terukur:
> 1. **Bocor variabel antar-halaman.** Scanner v1 membangun satu peta `var()` global atas
>    seluruh folder dengan urutan nama berkas, dan `hasbrouck.html` — halaman terpisah dengan
>    temanya sendiri — mendeklarasikan `--accent: #2a78d6`. Maka setiap `var(--accent)` di
>    terminal ter-resolve ke **biru hasbrouck**, bukan **amber `#E0A33E`** milik terminal.
>    Pasangan sebenarnya `#07101f` atas `#E0A33E` = **8,59:1, LOLOS**.
> 2. **Alpha tidak dikomposisi**, yang menghasilkan "1,0:1" untuk warna di atas tint 10 %
>    dirinya sendiri.
>
> Scanner sudah diperbaiki (scope per-halaman + komposisi alpha) dan **diuji punya daya**:
> 51 pasangan `fg/bg` benar-benar dinilai, dan pasangan buruk yang sengaja disuntik ke
> **salinan** terdeteksi pada 1,19:1. Pada dashboard nyata: **0 kegagalan kontras.**
>
> **Tidak ada satu berkas `dashboard/` yang diubah**, dan paritas piksel dijalankan untuk
> membuktikannya: **27/27 panel identik** terhadap baseline yang sudah di-commit.
> Memperbaiki produk untuk menyenangkan instrumen yang rusak adalah kerusakan, bukan
> perbaikan.

**Sisa Lapis 1 setelah pembatalan:** satu item, yaitu menghapus 16 deklarasi `slashed-zero`
yang inert (atau menambah fitur `zero` ke subset font). Dampaknya bukan visual — ia
menghapus kebohongan dari stylesheet.

---

## LAPIS 2 — encoding kedua: **TIDAK DIUSULKAN, karena sudah ada**

**Angka** (audit §4): 233 tanda `+`/`−`, 29 panah, 23 glyph, 6 label teks eksplisit. Dan
`styles.css` **sudah** mendeklarasikan palet Okabe–Ito colorblind-safe (`--up: #009E73`,
`--down: #D55E00`) dengan luminansi yang sengaja dibedakan.

Yang **tidak** terukur: apakah setiap **view kanvas** memakainya. Kanvas menggambar tanpa
kelas CSS, jadi grep tidak bisa menjawabnya.

**Usul, dan ini satu-satunya di lapis ini:** ukur dulu — screenshot tiap panel di bawah
simulasi deuteranopia dan bandingkan pembeda non-warnanya. Baseline dan harness-nya sudah ada
(`ui_baseline_shots.py`), jadi biayanya kecil. **Tanpa pengukuran itu, tidak ada pekerjaan
encoding yang diusulkan** — mengusulkannya sekarang berarti memperbaiki masalah yang belum
terbukti ada.

---

## LAPIS 3 — hierarki

**Angka v1** (audit §5): 56 % selector `terminal.css` berbagi satu kombinasi penekanan.

**Angka BARU, per panel** (`DIAG-orderflow-hierarchy-001`, [DIUKUR 2026-08-08]) — dan ini
yang menggantikan agregat itu sebagai dasar rencana:

| panel | elemen teks | tidak sesuai peran | % |
|---|---|---|---|
| `area-hist` | 20 | 3 | **15 %** |
| `area-auct` | 15 | 0 | **0 %** |
| `area-set` | 13 | 1 | **8 %** |
| total | 48 | 4 | **8 %** |

Keempat ketidaksesuaian berbentuk sama: **kontrol interaktif** (`step ›`, `live edge`,
`pause`) digambar pada `var(--fs-base)` bobot **700** — lebih menonjol dari angka di
sekitarnya. Jadi usulan v1 "turunkan label" **salah sasaran**: yang terlalu menonjol adalah
**tombol**, bukan label.

> **Batas yang harus dibaca bersama angkanya:** ketiga panel itu hampir seluruhnya **chrome
> statis**, dan `area-set` bahkan tidak punya JS view. **PRIMER nyaris tidak ada** di 48
> elemen tersebut — angka yang dibaca trader disuntikkan saat runtime, dan **14 dari 28 panel
> adalah kanvas** dengan 103 `fillText`. **8 % itu jujur untuk chrome statis, dan chrome
> statis bukan tempat keputusan diambil.** Lapis 3 karena itu **tidak boleh dieksekusi**
> sampai hierarki diukur pada DOM runtime + teks kanvas; harness-nya sudah ada, pengukurannya
> belum.

**Usul yang direvisi: turunkan TOMBOL, bukan label.** Empat elemen terukur, semuanya kontrol
interaktif pada `var(--fs-base)`/700. Menurunkannya ke `var(--fs-sm)`/600 memakai token yang
sudah ada. Ini menyentuh 4 elemen, bukan sebuah kelas selebar stylesheet — dan itu justru
kelebihannya: dampaknya bisa dilihat panel per panel di baseline.

**Risiko.** Hierarki adalah penilaian, dan angka 56 % **tidak memberitahu elemen mana** yang
salah — ia hanya membuktikan bahwa pembedaannya tidak dibuat. **Verifikasi:** paritas
screenshot per panel, plus penghitungan ulang distribusi (56 % harus turun) sebagai angka
sebelum/sesudah.

---

## LAPIS 4 — dedup 34 factory `*View` dan 4 salinan value-area

**Angka** (sensus `DIAG-orderflow-terminal-census-001` §5): 34 fungsi ≥100 baris, terbesar
`FootprintView` **645 baris** dengan `draw()` **382 baris**; ekspansi value-area 70 % dalam
**empat salinan tangan**; **495 dari 564 nama fungsi tidak muncul di `tests/`**.

**Prasyarat mutlak: baseline item 2 hijau** — dan sekarang **sudah**: 27/27 panel identik
antar run, kontrol terbukti menyala. Jadi prasyaratnya terpenuhi, tapi **hanya untuk
27 panel pada satu viewport dan satu fixture**.

**Usul urutan:** value-area lebih dulu (4 salinan → 1, dan salah satunya `_poc_va` di
`collector.py` yang berarti paritas Python↔JS bisa diuji dengan angka, bukan piksel), lalu
`FootprintView.draw()`. **Bukan** 34 factory sekaligus.

**Risiko tertinggi di seluruh rencana.** Ini satu-satunya lapis yang mengubah **logika**, dan
paritas screenshot tidak cukup: dua implementasi bisa menggambar piksel yang sama dari angka
yang berbeda pada fixture ini. **Verifikasi:** paritas screenshot **DAN** paritas
angka-untuk-angka untuk value-area (POC/VAH/VAL) antara empat salinan pada input yang sama,
sebelum tiga di antaranya dihapus.

---

## 5. Urutan kalau rencana ini disetujui

Lapis 1 lebih dulu — tiga kegagalan kontras hilang dengan satu perubahan token, dan itu
satu-satunya defect **fungsional** yang terukur di seluruh audit. Lapis 0 berikutnya, karena
ia menyerap 217 pemakaian literal dan membuat lapis berikutnya lebih murah. Lapis 3 setelah
token stabil. Lapis 4 terakhir dan paling hati-hati. Lapis 2 tidak dikerjakan sampai
pengukuran deuteranopia ada.

## 6. Yang DIKELUARKAN karena tidak punya angka pendukung

**Dipindahkan ke sini oleh revisi kedua [2026-08-08], karena angka baru membatalkannya:**

- **Memperbaiki tiga kegagalan kontras.** Ketiganya artefak scanner (bocor variabel
  antar-halaman + alpha tak dikomposisi). Pasangan sebenarnya 8,59:1. Setelah scanner
  diperbaiki dan diuji punya daya: **0 kegagalan**. Keluar.
- **Prasyarat "tunggu audit kanvas" untuk Lapis 0.** Kanvas 100 % token-sourced, 0 literal,
  total warna tetap 74. Prasyaratnya tidak berlaku. Keluar.
- **Membangun token yang bisa dibaca kanvas (Lapis 0b versi v1).** `cssVar()` + `pal()` sudah
  melakukannya persis. Yang tersisa hanya dua kebersihan kecil, dicatat di 0b. Keluar sebagai
  pekerjaan pembangunan.
- **"Turunkan label" di Lapis 3.** Terukur per panel: yang terlalu menonjol adalah **tombol**,
  bukan label. Usulan v1 salah sasaran dan diganti. Keluar.
- **Mengeksekusi Lapis 3 sekarang.** 8 % diukur pada chrome statis, sementara PRIMER hidup di
  runtime DOM dan di 14 panel kanvas. Keluar sampai hierarki diukur pada permukaan yang benar.

**Tetap keluar dari revisi pertama:**

- **Menambah tabular-nums.** Sudah ada 49 kemunculan, dan kanvas tabular lewat font monospace
  (advance 600/1000-em terukur). Keluar.
- **Menulis ulang encoding warna.** Palet colorblind-safe sudah terdeklarasi dan 291 pembeda
  kedua sudah terukur. Keluar sampai simulasi deuteranopia menunjukkan celah nyata.
- **Optimasi render.** p50 frame time **16,7 ms = 60 fps**, DOMContentLoaded 0,151 s, panel
  pertama terisi 0,159 s, 0 page error. Tidak ada angka yang menyebut render sebagai masalah.
  Keluar.
- **Memperbaiki kebocoran memori.** Tidak ada kebocoran terukur (gigi gergaji GC, bukan
  monoton). Keluar — dengan catatan jendelanya hanya ~96 detik.
- **Menyentuh `_profile_endpoint`.** 0,06–0,20 s untuk sehari penuh. Kandidat PLAN-002 Lapis 2
  ini **terukur tidak layak**. Keluar.
- **Memindahkan agregasi klien ke server.** 89,4 % byte masuk adalah tick mentah yang
  diagregasi di klien — angkanya besar dan nyata, **tetapi** frame time 60 fps dan memori
  stabil berarti tidak ada bukti bahwa itu **merugikan**. Ini keputusan arsitektur (di mana
  data dipersistensi), bukan perbaikan UI. Keluar dari rencana ini, dan lihat §7.

## 7. Yang lebih besar dari visual (item 5) — ukurannya, bukan usulannya

Terminal JS sudah menghitung footprint, CVD, volume profile, VPOC, absorption, stacked
imbalance, iceberg, dan heatmap — dan **tidak satu pun dipersistensi**. Untuk mempersistensinya
dibutuhkan tiga hal, dan biayanya bisa diperkirakan dari angka yang sudah ada: **satu seam
ekspor** dari store JS (yang sudah punya hook read-only `window.__BTCQ_TERMINAL_DEBUG`, jadi
jalurnya ada), **satu penerima** yang menulis ke tape, dan **satu keputusan rail** — karena
§0.1 menyatakan setiap seri terminal LIVE-DESCRIPTIVE dan tidak pernah masuk harness, jadi
mempersistensikannya tanpa mengubah rail hanya menghasilkan arsip yang tetap tidak boleh
dipakai. Ukurannya: pada **65,4 KB/s** yang masuk sekarang, tape mentahnya **5.785 MB/hari**
kalau disimpan apa adanya, versus metrik turunannya yang jauh lebih kecil — profil sehari
penuh keluar sebagai **11,7 KB / 122 level** dari `/v1/profile`, dan footprint 1 menit
(~50 level × 1.440 bar × 2 kolom) diperkirakan **~3 MB/hari**, yaitu ~1/2.000 dari tape
mentah. **Konsekuensi untuk aturan berhenti "batasnya di DATA"**: aturan itu mengarahkan
program mengumpulkan tape resolusi tertinggi sampai N tahun, dan angka-angka di atas
menunjukkan bahwa **metrik yang sudah dihitung terminal bukan pengganti tape mentah melainkan
ringkasan yang 2.000× lebih murah** — mempersistensikannya mempercepat jam N-tahun untuk
pertanyaan berbasis metrik, tetapi **tidak** untuk pertanyaan yang butuh book penuh, dan ia
tidak memindahkan satu hari pun ke masa lalu. Ini pernyataan ukuran, bukan usulan.

## 8. Apa yang rencana ini TIDAK klaim

Ia tidak mengklaim terminal akan terlihat lebih baik — tidak ada di sini yang mengukur
keindahan, hanya konsistensi dan tiga defect fungsional. Ia tidak mengklaim 27 panel yang
punya baseline mencakup seluruh permukaan: dua dikarantina karena non-determinisme yang
sah, dan baseline hanya valid untuk satu viewport, satu fixture, satu versi Chromium. Ia
tidak mengklaim kanvas aman terhadap defisiensi warna — itu belum diukur, dan Lapis 2 ada
justru untuk mengatakannya. Dan tidak ada satu lapis pun yang dieksekusi.
