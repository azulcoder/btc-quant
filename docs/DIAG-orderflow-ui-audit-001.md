# DIAG-orderflow-ui-audit-001 — "jelek" dijadikan terukur, plus baseline paritas visual

**Tanggal: 2026-08-08.** Branch `orderflow-terminal`. **Nol berkas `dashboard/` diubah.**
Perekam Tokyo dan collector Mac tidak disentuh. Nol angka prediktif.

**Kesimpulan satu kalimat: terminal ini jauh lebih sehat dari dugaan pada tiga hal yang
paling sering rusak di UI finansial (tabular numerals ADA, palet colorblind-safe ADA, tidak
ada kebocoran memori), dan tidak konsisten pada satu hal yang terukur besar — sistem token
yang sudah ada tetapi dilangkahi oleh ratusan literal.**

## 1. Inventaris konsistensi (item 1a, 1b)

17 berkas `dashboard/` (vendor dikecualikan). Patokan design system dikutip sebagai
**[DIASUMSIKAN]**, bukan hukum — ia konvensi industri, bukan fisika.

| dimensi | distinct | pemakaian | patokan [DIASUMSIKAN] | lipat |
|---|---|---|---|---|
| **spacing** | **82** | 424 | 6–8 langkah | **10×** |
| **warna** | **74** | 151 | 8–16 semantik | **5×** |
| **font-size** | **27** | 186 | 5–7 langkah | **4×** |
| **radius** | 11 | 81 | 2–4 | 3× |
| font-family | 5 | 85 | 1–3 | 2× |
| **font-weight** | **3** | 72 | 2–3 | **dalam patokan** |

Sepuluh teratas per dimensi menunjukkan sesuatu yang lebih berguna daripada angka
mentahnya: **sistem token SUDAH ADA dan sedang dilangkahi.**

```
font-size : 88x var(--fs-xs) · 24x var(--fs-2xs) · 15x var(--fs-sm) · 15x var(--fs-3xs)
            …lalu literal: 4x 12px · 3x 13.5px · 2x 14px · 2x 11px
spacing   : 70x var(--sp-2) · 30x var(--sp-1) · 26x var(--sp-3)
            …lalu literal: 48x 2px · 41x 1px · 22x 4px · 22x 3px · 14x 6px · 11x 10px
radius    : 35x var(--radius) …lalu literal: 21x 3px · 5x 2px · 4x 8px · 3x 4px
warna     : 63 custom property terdeklarasi, 44 di antaranya warna yang bisa di-resolve
```

Jadi masalahnya bukan "tidak punya design system". Masalahnya **kebocoran**: token dipakai
untuk mayoritas, literal dipakai untuk sisanya, dan sisanya berjumlah ratusan.

## 2. Tabular numerals (item 1c) — **premisnya SALAH, dan itu kabar baik**

Diminta memeriksa apakah `font-variant-numeric: tabular-nums` **tidak ada**. Terukur, ia
**ADA**:

```
terminal.css   : 17x tabular-nums · 17x font-variant-numeric
styles.css     :  5x tabular-nums ·  4x font-variant-numeric · 1x font-feature-settings 'tnum'
hasbrouck.html :  2x tabular-nums ·  2x font-variant-numeric
total          : 49 kemunculan
```

**Elemen penampil-angka yang terdampak: 0 yang bisa kubuktikan terdampak.** Sebab paling
umum angka bergoyang di UI finansial sudah ditangani di berkas ini. Aku tidak membuktikan
**setiap** elemen numerik tercakup — itu butuh inspeksi computed-style per elemen yang tidak
kujalankan — jadi klaimnya dibatasi: **mekanismenya dipakai secara luas, bukan absen.**

## 3. Kontras WCAG (item 1d) — dan detektorku sendiri yang buta lebih dulu

Jalur pertama melaporkan **0 kegagalan**. Itu **salah**, dan sebabnya instruktif: detektornya
hanya menilai pasangan di mana `color` dan `background` sama-sama literal dalam satu rule.
Codebase ini mendeklarasikan paletnya sebagai custom property, jadi pasangan literal hampir
tidak ada — "0" berarti **tidak ada yang dilihat**, bukan tidak ada yang salah (kelas B).

Setelah `var()` di-resolve (63 properti, 44 warna), pada pasangan `fg/bg` **nyata**:

| rasio | lantai | fg atas bg | lokasi |
|---|---|---|---|
| 4,31:1 | 4,5 | `#07101f` atas `var(--accent)` | `styles.css :: button` |
| 4,31:1 | 4,5 | `#07101f` atas `var(--accent)` | `terminal.css :: #set-pause[aria-pressed="true"]` |
| 4,46:1 | 4,5 | `var(--ink)` atas `var(--accent-soft)` | `hasbrouck.html :: .themer button:hover` |
| ~~1,0:1~~ | — | ~~`var(--up)` atas `rgba(38,166,154,0.1)`~~ | **ARTEFAK** — alpha tidak dikomposisi oleh detektorku; jangan dihitung |

**Tiga kegagalan nyata, semuanya marginal** (4,31–4,46 vs lantai 4,5) dan semuanya pada
kontrol interaktif, bukan pada angka. Tidak ada satu pun teks data yang gagal.

## 4. Warna sebagai satu-satunya encoding (item 1e) — **sudah ditangani**

| pembeda kedua di seluruh `dashboard/` | jumlah |
|---|---|
| tanda `+`/`−` | 233 |
| panah/segitiga (`↑↓▲▼`) | 29 |
| ikon/glyph (`●■•`) | 23 |
| teks eksplisit `BID`/`ASK`/`BUY`/`SELL` | 6 |

Dan lebih jauh dari yang diminta: `styles.css` **sudah mendeklarasikan palet colorblind-safe**
sebagai tema alternatif — `--up: #009E73` (bluish-green) dan `--down: #D55E00` (vermillion),
pasangan Okabe–Ito yang memang dirancang untuk defisiensi merah-hijau. Komentarnya bahkan
mencatat luminансi relatifnya (`L≈0.300` vs `L≈0.251`), yang berarti naik dan turun **juga**
berbeda dalam terang, bukan hanya rona.

**Batas klaim:** ini pengukuran keberadaan mekanisme di kode, bukan bukti bahwa setiap view
kanvas memakainya. Kanvas menggambar tanpa kelas CSS, jadi verifikasi per-view menuntut
inspeksi piksel yang tidak kujalankan.

## 5. Hierarki (item 1f)

Pada `terminal.css`, kombinasi (font-size, font-weight) distinct: **16**. Distribusinya:

```
62 selector  size=var(--fs-xs)   weight=(inherit)   <- 56% dari 110
19 selector  size=var(--fs-2xs)  weight=(inherit)
 7 selector  size=var(--fs-3xs)  weight=(inherit)
 6 selector  size=var(--fs-3xs)  weight=700
 3 selector  size=var(--fs-2xs)  weight=600
```

**56 % selector berbagi satu tingkat penekanan yang sama.** Itu ukuran "semua sama menonjol"
yang diminta: lebih dari separuh permukaan tidak membedakan dirinya dari sisanya lewat
ukuran maupun bobot. Tiga bobot memang dipakai (400/600/700), tetapi hanya pada 10 dari 110
selector.

## 6. Baseline paritas visual (item 2)

**Terminal BISA jalan tanpa WebSocket live**, dan seam-nya sudah ada sebelum giliran ini:
`?replay=1` membuat `terminal-replay.js` menggerakkan adapter yang tidak diubah dari
`scripts/fixtures_ws.json` pada jam sintetis, tanpa jaringan di luar localhost. Itu yang
membuat baseline piksel mungkin sama sekali.

`scripts/ui_baseline_shots.py` · baseline: **29 screenshot** di `reports/ui-baseline/`
(976 KB), viewport 1680×1050, 28 area panel dibaca dari `terminal.html` (bukan ditebak) plus
halaman penuh.

**Kontrol diuji DUA ARAH (kelas I), dan versi pertamanya GAGAL — itu bagian penting:**

| percobaan | hasil |
|---|---|
| v1: substitusi warna `#26a69a` di `terminal.css` | **tidak cocok apa pun** → kontrol jujur melaporkan **DOES NOT DISCRIMINATE**. Token `--up` ternyata ada di **`styles.css`**, huruf **besar**. |
| v2: 29 panel, substitusi di `styles.css` case-insensitive | pass-case **27/29**, dua panel **berbeda antar dua run identik** |
| **v3 final** | **pass-case 27/27 identik** · fail-case **tertangkap** (`full`, `stats`) · **VERDICT: WORKS** |

**Dua panel dikarantina, dengan alasannya tercatat di dalam kode, bukan diam-diam dibuang:**
`auct` menarik dari API `/v1/profile` di localhost dan `news` membaca `econ_calendar.json`
yang gitignored — **keduanya tidak diberi makan oleh fixture replay**, jadi mereka memang
tidak bisa deterministik. Baseline-lah yang menemukan itu; keduanya tetap difoto, hanya tidak
boleh ikut memutuskan vonis.

**Sensitivitas kontrol, jujur:** perubahan warna naik/turun hanya tertangkap di 2 dari 27
panel yang dinilai. Itu benar dan diharapkan — panel yang tidak merender token itu memang
tidak boleh berubah. Artinya baseline ini menangkap perubahan **di panel yang terdampak**,
bukan perubahan apa pun di mana pun.

### Waktu render (item 2d) — angka yang PLAN-002 tinggalkan kosong

```
DOMContentLoaded        : 0,151 s
panel pertama terisi    : 0,159 s
frame time (60 frame)   : p50 16,7 ms · p95 17,5 ms · maks 17,6 ms
page errors             : 0
```

**p50 16,7 ms = tepat 60 fps.** Loop render tidak melewatkan frame dalam mode replay.

## 7. Runtime nyata (item 3)

### 7a. `/v1/profile`, satu hari penuh, tick $10 — **bukan bottleneck**

```
sampel: 0,20 s · 0,07 s · 0,06 s   ·   respons 11,7 KB · 122 level
```

PLAN-002 Lapis 2 menyisakan satu kandidat: loop dict Python yang menggabungkan hasil
per-hari di `_profile_endpoint`. **Terukur: kandidat itu tidak layak dikerjakan.** 60–200 ms
untuk sehari penuh, dan responsnya 11,7 KB.

### 7b. Byte yang benar-benar mengalir ke browser (60 detik operasi normal, mode live)

```
muat halaman            : 1.643.835 B aset, 2,19 s
WebSocket masuk         : 3.591.234 B  (5.231 frame)
HTTP/fetch masuk        :   426.434 B
TOTAL 60 s              : 4.017.668 B = 65,4 KB/s  ->  5.785 MB/hari pada laju ini
```

| | porsi |
|---|---|
| tiba **mentah**, diagregasi **di klien** (WS) | **89,4 %** |
| tiba **sudah teragregasi** (HTTP/parquet) | 10,6 % |

**Hampir sembilan dari sepuluh byte yang masuk ke tab adalah tick mentah yang diagregasi
JavaScript di dalam browser.** Ini angka yang menopang temuan sensus bahwa dua dari tiga
rantai mengagregasi di klien.

### 7c. Memori tab — dan kontrol resolusi yang menyelamatkan kesimpulannya

Pengukuran pertama (5 menit, 20 sampel) memberi **15,2 MB datar, 0/19 langkah naik**. Terlalu
rapi. Kontrol resolusi dengan `--enable-precise-memory-info`:

```
sampel (MB): 8,09 · 13,31 · 17,37 · 24,53 · 40,52 · 30,46 · 12,99 · 33,73 · 37,68 · 19,67 · 21,77 · 24,90
nilai distinct: 12/12 · min 8,09 · maks 40,52
```

**Tanpa flag itu Chrome membekukan `usedJSHeapSize`, jadi "datar 15,2 MB" adalah artefak
instrumen, bukan bukti.** Dengan resolusi nyata, polanya **gigi gergaji** — naik lalu turun,
yaitu GC bekerja. **Tidak ada tanda kebocoran** pada jendela ~96 detik yang terukur; jendela
5 menit yang asli tidak bisa dipakai karena instrumennya buta saat itu.

## 8. Apa yang audit ini TIDAK buktikan

Ia mengukur **konsistensi**, bukan keindahan — tidak ada di sini yang mengklaim terminal ini
terlihat bagus atau jelek, hanya berapa banyak keputusan berbeda yang dibuat untuk hal yang
sama. Cakupan kontras terbatas pada pasangan `color`+`background` dalam satu rule CSS setelah
`var()` di-resolve; warna yang diset dari JavaScript atau digambar ke kanvas **tidak
tersentuh**, dan alpha tidak dikomposisi. Klaim tabular-numerals dan colorblind-palette adalah
klaim tentang **keberadaan mekanisme di kode**, bukan bukti setiap elemen memakainya. Baseline
piksel valid untuk satu viewport (1680×1050), satu fixture, satu versi Chromium — ia akan
menyala pada perubahan font rendering yang tidak ada hubungannya dengan kode. Angka jaringan
berasal dari satu jendela 60 detik pada satu sore dan akan berbeda pada rezim pasar lain.
Dan memori diukur ~96 detik, bukan sepanjang hari — kebocoran lambat masih mungkin dan belum
terbantah.
