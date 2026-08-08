# DIAG-orderflow-canvas-001 — lubang kanvas yang audit kemarin tidak sentuh

**Tanggal: 2026-08-08.** Branch `orderflow-terminal`. **Nol berkas `dashboard/` diubah.**
Perekam Tokyo dan collector Mac tidak disentuh.

**Kesimpulan satu kalimat: kanvas ternyata bagian PALING BERSIH dari codebase ini —
174 assignment warna, NOL literal, semuanya bersumber token lewat satu helper — sehingga
prasyarat yang dikhawatirkan ("CSS rapi di sebelah kanvas liar") TIDAK berlaku, dan yang
justru ditemukan adalah satu defect font yang inert di DOM maupun kanvas.**

## 1. Permukaan kanvas: satu berkas, bukan sepuluh

`grep -l` atas sepuluh berkas JS yang diaudit: **sembilan mengembalikan nol**. Seluruh
kanvas terminal ada di **`dashboard/terminal-views.js`**, dan di seluruh dashboard hanya ada
**satu** `getContext('2d')` (di `fitCanvas()`). `charts.js` ternyata berbasis SVG, bukan
kanvas.

## 2. Assignment warna kanvas (item 2a)

| properti | jumlah |
|---|---|
| `fillStyle` | 127 assignment (129 kemunculan mentah − 2 prosa di komentar) |
| `strokeStyle` | 47 |
| `shadowColor` | **0** |
| `createLinearGradient` / `createRadialGradient` / `addColorStop` | **0** |
| **total assignment riil** | **174** |

Nol gradient dan nol shadow di seluruh kode terminal — itu sendiri temuan: permukaannya
datar secara sengaja.

**Pembagian bucket:**

| bucket | jumlah | porsi |
|---|---|---|
| **LITERAL** (hex/rgb ditulis inline di titik assignment) | **0** | **0 %** |
| TOKEN-READ (`p.<token>` atau `cssVar()` langsung) | 109 | 62,6 % |
| RUNTIME-COMPUTED | 65 | 37,4 % |

Kontrol atas nol itu lebih kuat dari sekadar mencari `#`: pola yang lebih longgar — **string
ber-kutip APA PUN** yang di-assign ke `fillStyle`/`strokeStyle` — juga mengembalikan exit 1.
Jadi nol-nya karena memang nol, bukan karena regex terlalu sempit.

Dan 65 RUNTIME-COMPUTED itu **tetap bersumber token**: 46 adalah wrapper
`rgba(<token>, alpha)`, 6 ternary yang memilih antar token, 13 variabel tak-langsung yang
ditelusuri satu per satu sampai definisinya dan semuanya berujung `p.*`. **Provenance hue:
174/174 = 100 % token-sourced.**

**Helper pembaca token ada dan terdokumentasi:** `cssVar()` memanggil
`getComputedStyle(document.documentElement).getPropertyValue(name)`; `pal()` membangun peta
17-token **setiap draw** (sengaja tidak di-cache supaya toggle tema langsung kena); plus
`rgba()`, `lerpHex()`, `exColor()`.

**19–20 nilai hex literal yang ada di berkas itu semuanya menempati posisi fallback**
argumen kedua `cssVar('--x', '#hex')`. Diperiksa terhadap stylesheet: **setiap fallback
cocok persis dengan nilai token di CSS** — termasuk rantai `--fg → var(--g-050) → #e4e8ee`.
**Nol drift.** Fallback itu dead-but-correct, bukan jalur yang diam-diam melukis warna lain.

## 3. Hitung ulang warna total: CSS + kanvas (item 2b)

| sumber | distinct |
|---|---|
| CSS (audit kemarin) | **74** |
| kanvas — literal yang mewarnai piksel | **0** |
| kanvas — fallback literal (tidak pernah dipakai selama token ada) | 20, semuanya duplikat nilai CSS |

**Total warna distinct terminal = 74, tidak berubah.** Angka 74 dari audit kemarin **bukan
batas bawah** untuk permukaan kanvas — ia sudah mencakup semuanya, karena kanvas tidak
memperkenalkan satu pun hue baru.

> **Konsekuensi langsung untuk `PLAN-orderflow-ui-001` Lapis 0:** kekhawatiran bahwa
> menyerap literal CSS akan meninggalkan "kanvas liar" **tidak terbukti**. Kanvas sudah
> berada di ujung yang benar; **CSS-lah yang tertinggal.**

## 4. Font kanvas, dan pengecekan tabular yang berbeda dari kemarin (item 2c)

| | nilai |
|---|---|
| `ctx.font =` | **20**, semuanya di `terminal-views.js` |
| `fillText` | **103** call site · `strokeText` **0** |
| keluarga distinct di kanvas | **1** — setiap string berakhir `cssVar('--mono', 'monospace')` |
| ukuran | 8/9/10/11px statis + `fpx` runtime clamp 6–10px → **{6,7,8,9,10,11}** |

**Dua ukuran, 6px dan 7px, berada DI BAWAH lantai skala tipografi milik sistem itu sendiri**
(`--fs-3xs: 9px`). Itu literal yang melangkahi token, dan letaknya di kanvas.

**Tabular numerals di kanvas — premisnya SALAH, tapi bukan karena alasan yang kode
sarankan.** Tidak ada `ctx.fontVariantCaps`, `ctx.letterSpacing`, `ctx.fontStretch`,
`ctx.fontKerning`, `textRendering`, `OffscreenCanvas`, `document.fonts`, `FontFace` — semua
nol. Tidak ada aturan CSS yang menargetkan `canvas` dengan `font-variant-numeric`.
**Namun angka di kanvas TETAP tabular**, karena woff2 yang dikirim **diukur langsung**:
kesepuluh digit punya advance identik **600/1000-em** dengan `post.isFixedPitch = 1`.
Tabularitas di sini adalah properti **font monospace**, bukan flag — dan karena ketujuh
keluarga di rantai fallback `--mono` semuanya monospace, digit tabular bertahan bahkan
selama jendela FOUT `font-display: swap`.

**Defect nyata yang ditemukan justru di tempat lain:** woff2 IBM Plex Mono yang di-subset
hanya memapar fitur GSUB `['ccmp','dnom','frac','numr']` — **tidak ada fitur `zero`, tidak
ada glyph slashed-zero** (hanya `zero.numr`/`zero.dnom`). Artinya `slashed-zero` di **16
lokasi CSS** dan `font-feature-settings: 'zero' 1` di `styles.css` **INERT** — bukan hanya
di kanvas, tetapi **di DOM juga**. CSS meminta sesuatu yang fontnya tidak bisa berikan.

Dari 103 `fillText`, **41 membawa formatter angka atau ekspresi numerik di baris panggilan
itu sendiri**, ~12 lagi menjadi teks numerik satu hop lewat variabel lokal.

## 5. Panel: kanvas atau DOM (item 2d)

**Metode, dinyatakan karena hasilnya bergantung padanya:** `grep -c '<canvas' terminal.html`
mengembalikan **0** — tidak ada satu pun tag kanvas literal di HTML, jadi pertanyaan ini
**tidak bisa dijawab dengan mem-parsing HTML saja**. Setiap kanvas disuntikkan saat runtime
dari 11 `document.createElement('canvas')` plus satu string innerHTML `<canvas class="ti-spark">`,
plus 5 `LightweightCharts.createChart` yang renderer-nya berbasis kanvas.

Klasifikasi dilakukan dengan menelusuri id target mount tiap panel lewat **37 call site
`.mount($('view-*'))`** di `terminal.js` ke konstruktor View-nya, lalu memindai badan tiap
View. Hasil: **14 CANVAS / 14 DOM** dari 28 panel `area-`.

Struktur tambahan yang ditemukan: `terminal.html` sebenarnya memuat **37** `<section
class="panel...">` — 28 ber-`area-`, 7 anonim di dalam `div.term-col.area-mid`, dan 2
`panel local-only` yang kosong.

## 6. Apa yang audit ini TIDAK buktikan

Ia membuktikan **provenance** warna kanvas (semuanya token), bukan **kebenaran** warna yang
dihasilkan: sebuah token bisa dibaca dengan benar dan tetap salah dipakai, dan kontras di
kanvas sama sekali belum diukur — scanner WCAG hanya menilai pasangan CSS. Klaim tabularitas
berdiri di atas pengukuran metrik font pada woff2 yang di-subset di repo; kalau rantai
fallback pernah jatuh ke font sistem non-monospace di mesin lain, kesimpulannya berubah dan
itu belum diuji. Pembagian 14/14 bergantung pada penelusuran mount-target yang statis —
panel yang memilih renderer-nya saat runtime akan salah klasifikasi. Dan tidak ada satu pun
piksel yang benar-benar diinspeksi: seluruh dokumen ini membaca kode, bukan hasil gambarnya.
