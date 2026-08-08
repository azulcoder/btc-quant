# DIAG-orderflow-hierarchy-001 — hierarki, dari diagnosis agregat ke elemen per panel

**Tanggal: 2026-08-08.** Branch `orderflow-terminal`. **Nol berkas diubah** — item ini
mengukur dan mengklasifikasi saja, sesuai instruksi.

**Kesimpulan satu kalimat: pada permukaan statis, hanya 8 % elemen berada di tingkat
penekanan yang salah — tetapi permukaan statis BUKAN tempat angka yang dibaca untuk
mengambil keputusan berada, sehingga angka 8 % itu menjawab pertanyaan yang lebih kecil
daripada yang ditanyakan.**

## 1. Pemilihan tiga panel terpadat (item 3a) — aturan mekanis, bukan selera

Aturan: hitung keturunan tiap `<section class="panel area-*">` yang cocok dengan
`{span, td, th, h2, label, button, small, b, strong}` **selalu**, plus `div` **hanya** bila
ia punya teks langsung non-whitespace. Diparsing dengan `html.parser` (pohon sungguhan,
atribusi teks ke elemen pemiliknya), dan **direproduksi persis oleh metode regex independen**.

| panel | elemen teks | rincian tag |
|---|---|---|
| **area-hist** | **21** | span 6 · label 8 · button 3 · b 2 · h2 1 · div 1 |
| **area-auct** | **16** | b 5 · span 5 · label 4 · h2 1 · div 1 |
| **area-set** | **16** | label 6 · span 5 · b 4 · button 1 |
| area-fp | 13 | — peringkat 4, selisih jelas |

Seri `auct`/`set` di 16 dipecah oleh urutan dokumen (baris 518 sebelum 756); keduanya masuk
top-3 apa pun tie-break-nya. **Uji kekokohan:** diperingkat atas seluruh **37** section panel
(termasuk 7 anonim), top-3-nya **identik** — anonim tertinggi (panel aggregated-book) ada di
15, peringkat 4.

## 2. Klasifikasi peran (item 3b)

Tiga tingkat, didefinisikan sebelum melihat elemennya:

- **PRIMER** — angka yang dibaca untuk mengambil keputusan.
- **SEKUNDER** — konteks yang dibutuhkan untuk menafsirkan primer (status, sumber, mode).
- **TERSIER** — label, satuan, judul, hint: dibaca sekali lalu diabaikan.

Aturan penetapan peran dibuat mekanis supaya bisa diulang: `h2` dan `label` → TERSIER;
kelas mengandung `hint`/`note`/`desc`/`signal-tag` → TERSIER; `<b>` pendek non-numerik →
TERSIER (penekanan kata di dalam prosa); teks yang cocok pola numerik atau kelas
`val`/`price`/`poc`/`vah` → PRIMER; sisanya SEKUNDER.

Tingkat penekanan **aktual** diambil dari CSS efektif (`font-size`, `font-weight`) yang
menargetkan kelas/id/tag elemen itu, di `terminal.css` + `styles.css`.

## 3. Hasil per panel (item 3c)

| panel | elemen | tidak sesuai peran | % |
|---|---|---|---|
| area-hist | 20 | 3 | **15 %** |
| area-auct | 15 | 0 | **0 %** |
| area-set | 13 | 1 | **8 %** |
| **total** | **48** | **4** | **8 %** |

Keempat ketidaksesuaian punya bentuk yang **sama**: elemen berperan SEKUNDER (kontrol
interaktif — `step ›`, `live edge`, `pause`) digambar pada `var(--fs-base)` **bobot 700**,
yaitu tingkat penekanan yang lebih tinggi daripada sebagian besar angka di sekitarnya.
Bukan label yang terlalu menonjol — **tombol** yang terlalu menonjol.

## 4. Kenapa 8 % menjawab pertanyaan yang lebih kecil — dan ini bagian terpenting

Angka **56 %** dari audit kemarin dan angka **8 %** di sini **mengukur hal yang berbeda**,
dan menyandingkannya tanpa catatan akan menyesatkan:

- **56 %** = porsi *selector* di `terminal.css` yang berbagi satu kombinasi (ukuran, bobot).
  Itu ukuran **tidak-terdiferensiasinya stylesheet**.
- **8 %** = porsi *elemen* di tiga panel yang berada pada tingkat salah **untuk perannya**.
  Itu ukuran **kesalahan penempatan**, pada permukaan tertentu.

Dan permukaan itu terbatas dengan cara yang menentukan: ketiga panel terpilih **hampir
seluruhnya chrome statis** — header, hint, label kontrol. `area-set` bahkan **tidak punya
JS view sama sekali** (`grep "view-set\|SettingsView"` kosong); ia form pengaturan statis.
Akibatnya, di 48 elemen itu **PRIMER nyaris tidak ada**: angka yang benar-benar dibaca
trader tidak ada di HTML statis, melainkan **disuntikkan saat runtime**, dan **14 dari 28
panel adalah kanvas** (`docs/DIAG-orderflow-canvas-001.md` §5) di mana teks digambar oleh
**103 `fillText`**, 41 di antaranya membawa formatter angka di baris panggilannya.

**Jadi: 8 % adalah angka yang jujur untuk chrome statis, dan chrome statis bukan tempat
keputusan diambil.** Aturan pemilihan "panel terpadat menurut jumlah elemen teks statis"
secara sistematis memilih panel yang paling banyak label-nya, bukan yang paling padat
angkanya.

**Instrumen yang akan menjawab pertanyaan sebenarnya** — dan yang **tidak** dijalankan di
sini: enumerasi teks pada DOM **runtime** (setelah view mount, lewat harness Playwright yang
sudah ada) digabung dengan ekstraksi ukuran font dari 20 `ctx.font` kanvas. Keduanya
tersedia; keduanya di luar lingkup "ukur dan klasifikasikan saja".

## 5. Apa yang dokumen ini TIDAK buktikan

Ia tidak membuktikan hierarki terminal ini baik atau buruk — ia mengukur satu permukaan dan
menyatakan permukaan itu bukan yang menentukan. Penetapan peran bersifat mekanis dan karena
itu bisa salah pada kasus tepi: sebuah `<b>` yang sebenarnya angka akan jatuh ke TERSIER,
dan sebuah `<span>` prosa tanpa kelas hint akan jatuh ke SEKUNDER. Tingkat penekanan aktual
dibaca dari pencocokan selector tekstual, bukan dari `getComputedStyle` di halaman hidup,
jadi kaskade dan spesifisitas tidak diselesaikan sepenuhnya. Dan tidak ada satu pun angka
runtime maupun satu pun piksel kanvas yang masuk hitungan ini.
