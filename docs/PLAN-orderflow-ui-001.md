# PLAN-orderflow-ui-001 — design system berlapis. DITULIS, TIDAK DIEKSEKUSI.

**Tanggal: 2026-08-08.** Branch `orderflow-terminal`. Nol berkas `dashboard/` diubah. Setiap
usulan menyebut angka dari `docs/DIAG-orderflow-ui-audit-001.md`; §6 mencatat apa yang
**dikeluarkan** karena tidak punya angka pendukung.

**Syarat merge untuk setiap lapis: paritas screenshot terhadap `reports/ui-baseline/` harus
hijau, atau perbedaannya dijelaskan per-panel.** Baseline itu sudah ada dan kontrolnya sudah
terbukti menyala (audit §6).

**Temuan yang membentuk seluruh rencana ini: sistem token SUDAH ADA dan sedang dilangkahi.**
Ini bukan proyek "bangun design system dari nol" — ini proyek "tutup kebocoran".

---

## LAPIS 0 — token: menyerap literal yang melangkahi sistem yang sudah ada

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

---

## LAPIS 1 — perbaikan satu-baris berdampak besar

**Tabular numerals: TIDAK ADA PEKERJAAN.** Audit §2 mengukur **49 kemunculan**
`tabular-nums`/`font-variant-numeric`/`tnum` di tiga berkas. Premis bahwa ia hilang **salah**,
dan aku tidak mengusulkan pekerjaan untuk masalah yang tidak ada.

**Kontras: tiga perbaikan, semuanya marginal** (audit §3):

| lokasi | sekarang | lantai | perbaikan minimum |
|---|---|---|---|
| `styles.css :: button` | 4,31:1 | 4,5 | gelapkan `#07101f` atau terangkan `--accent` sedikit |
| `terminal.css :: #set-pause[aria-pressed="true"]` | 4,31:1 | 4,5 | idem (token yang sama) |
| `hasbrouck.html :: .themer button:hover` | 4,46:1 | 4,5 | idem |

Ketiganya menyentuh **satu token** (`--accent`) plus satu warna teks. **Dampak: 3 dari 3
kegagalan kontras yang terukur hilang dengan satu perubahan token.** Tidak ada teks data yang
gagal, jadi ini memperbaiki kontrol interaktif, bukan keterbacaan angka.

**Risiko.** Mengubah `--accent` menyentuh setiap permukaan beraksen. **Verifikasi:** paritas
screenshot akan menyala di banyak panel **secara sengaja** — di sinilah baseline harus dibaca
sebagai daftar-untuk-ditinjau, bukan lampu merah.

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

**Angka** (audit §5): **56 % selector `terminal.css` (62 dari 110) berbagi satu kombinasi
penekanan** `size=var(--fs-xs)`, `weight=(inherit)`. Tiga bobot tersedia (400/600/700) tetapi
hanya dipakai di 10 dari 110 selector.

**Panel dan elemen mana** — dari audit, panel terpadat yang punya baseline dan terbukti
sensitif terhadap perubahan token adalah **`stats`** (satu dari dua yang menangkap perubahan
warna) dan halaman penuh.

**Usul: turunkan, jangan naikkan.** Yang harus **turun** penekanannya adalah label dan satuan
— elemen yang saat ini berbagi `--fs-xs` dengan angkanya sendiri. Menaikkan yang penting akan
memperbesar 27 ukuran font distinct menjadi lebih banyak lagi; menurunkan yang tidak penting
memakai token yang sudah ada (`--fs-2xs`, `--fs-3xs`, yang sudah dipakai 24× dan 15×).

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

- **Menambah tabular-nums.** Sudah ada 49 kemunculan. Keluar.
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
