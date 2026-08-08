# PLAN-orderflow-terminal-002 — rencana refactor berlapis. DITULIS, TIDAK DIEKSEKUSI.

**Tanggal: 2026-08-08.** Branch `orderflow-terminal`. Tidak ada satu baris pun dari rencana ini
yang dijalankan. Setiap usulan wajib menyebut angka dari
`docs/DIAG-orderflow-terminal-census-001.md` atau `docs/DIAG-orderflow-profile-001.md`;
usulan tanpa angka pendukung tidak dimasukkan, dan §6 mencatat apa yang **dikeluarkan** karena
itu.

**Syarat mutlak yang berlaku untuk SETIAP lapis: refactor yang mengubah angka bukan refactor.**
Tiap lapis di bawah menyebut kontrol paritasnya sendiri, dan tanpa kontrol itu lapisnya tidak
boleh dianggap selesai.

---

## LAPIS 0 — format penyimpanan: **YA, dengan angka**

**Angka yang membenarkannya** (profil §2): pada tape depth-diff satu hari yang identik,
JSONL.gz baca+parse+agregasi **2,34 s** versus Parquet+ZSTD baca+agregasi **0,03 s** =
**69,0×**; konversi sekali **6,81 s**; ukuran **0,89×** (60,9 MB vs 68,1 MB) meski baris
meledak 457 ribu → 15,8 juta. Dictionary encoding pada kolom harga yang membuat berkas justru
menyusut.

**Usul.** Tape depth-diff mendapat representasi kolumnar turunan, di samping JSONL.gz — bukan
menggantinya. JSONL.gz tetap **arsip kanonik** (append-only, byte-identik dengan yang direkam,
sudah tersalin ke GCS dan HF); Parquet adalah **artefak turunan yang bisa dibuang dan
dibangun ulang**. Skema yang terukur bekerja: `recv_ms int64 · E int64 · side dict · price dict
· size`, ZSTD, diurutkan menurut `recv_ms`.

**Risiko.** Dua representasi bisa menyimpang. Mitigasi: Parquet **selalu** diturunkan dari
JSONL.gz oleh satu skrip, tanpa jalur tulis lain, dan hash sumbernya dicatat di dalam artefak.

**Kontrol paritas (wajib).** Untuk hari yang sama: jumlah baris level, jumlah frame,
`sum(size)` per sisi, `min/max(recv_ms)`, dan histogram jumlah level per menit — **harus
identik angka-untuk-angka** antara jalur JSONL.gz dan jalur Parquet. Ketidaksamaan mana pun
membatalkan konversi hari itu.

---

## LAPIS 1 — mesin rekonstruksi book: checkpoint

**Angka yang membenarkannya** (profil §1b, sensus §2): agregasi sudah di DuckDB dan hanya
12 % wall-clock terlihat di Python, jadi yang mahal bukan CPU per baris melainkan **keharusan
memutar ulang dari awal hari**. Kontrol rekonstruksi (`docs/DIAG-control-a-v2-001.md`) memakai
snapshot venue tiap **60 detik** (OKX) dan **900 detik** (perekam Binance) sebagai satu-satunya
titik masuk; tanpa checkpoint, akses acak ke tengah hari berarti memutar seluruh prefiksnya.

**Usul N = 300 detik (5 menit),** dengan alasan terukur bukan selera:
- Batas bawah: snapshot REST perekam sudah tiap 900 s; N < 900 memberi titik masuk yang
  **lebih rapat dari yang ada sekarang**, yang adalah seluruh gunanya.
- Batas atas: pada laju terukur **37 fps** dan ~15,8 juta baris level/hari (profil §2), satu
  jendela 300 s ≈ 11 ribu frame — cukup kecil untuk direplay dalam milidetik, cukup besar
  supaya checkpoint-nya tidak mendominasi ukuran (288 checkpoint/hari × ~800 level ≈ 230 ribu
  baris, ~1,5 % dari 15,8 juta).
- N=60 akan meniru OKX tetapi melipatkan lima ukuran checkpoint tanpa manfaat terukur, karena
  tidak ada konsumen yang meminta granularitas di bawah 5 menit.

**Risiko.** Checkpoint bisa merekam book yang **salah** dan menyebarkannya ke setiap konsumen
yang mempercayainya. Ini risiko paling serius di seluruh rencana, karena `DIAG-control-a-v2-001`
mengukur agreement per level **97,2 % (Binance) / 97,9 % (OKX)** pada top-20 — bukan 100 %,
dan `DIAG-control-a-regime-001` mengukur rentangnya **91,8 %–98,2 %** lintas rezim.

**Kontrol paritas (wajib).** Setiap checkpoint diverifikasi terhadap **snapshot venue
berikutnya** dengan kontrol A v2 yang sudah ada, dan **agreement per level-nya dicatat di
dalam checkpoint itu sendiri** — sehingga konsumen tidak bisa memakainya tanpa melihat
ketidakpastiannya. Checkpoint yang dibangun dari jendela yang melintasi jeda perekaman
**ditolak**, bukan ditandai (cacat 3 di `DIAG-control-a-v2-001`).

---

## LAPIS 2 — lapisan query: **agregasi SUDAH di DuckDB; jangan diulang**

**Angka yang membenarkannya** (profil §1b): cProfile melihat **0,196 s dari 1,64 s** wall —
**88 % sudah di dalam DuckDB C++**. Jadi usulan "dorong agregasi ke DuckDB" untuk
`order_flow_bars` **sudah selesai sebelum rencana ini ditulis**, dan mengulanginya akan
menghasilkan nol.

**Yang MASIH loop-Python/JS, dengan buktinya** (sensus §2):
- `_profile_endpoint` di `collector.py` menggabungkan hasil per hari **dengan loop dict
  Python** setelah `GROUP BY` DuckDB — kandidat sah, tetapi dampaknya belum diukur.
- `aggregateTradeRows()` di `terminal.js` mengagregasi parquet **di dalam tab browser** dengan
  loop `for…of` — ini bukan pekerjaan DuckDB, ini pekerjaan Lapis 4/arsitektur.

**Usul.** Satu perubahan saja, dan hanya setelah diukur: pindahkan penggabungan lintas-hari
`_profile_endpoint` ke satu query DuckDB. **Prasyarat: ukur dulu** biaya loop itu; profil yang
ada tidak mencakup endpoint HTTP, jadi hari ini **tidak ada angka** yang membenarkan
perubahannya. Tanpa angka itu, lapis ini **tidak dikerjakan**.

**Kontrol paritas (wajib).** Respons JSON `/v1/profile` untuk rentang yang sama harus
**byte-identik** sebelum dan sesudah, untuk minimal 3 rentang termasuk satu yang melintasi
batas hari.

---

## LAPIS 3 — metrik turunan: prakalkulasi sekali, baca berkali

**Angka yang membenarkannya** (sensus §4): **enam dari sembilan** metrik standar yang hilang
adalah **HAVE-DATA** — bahan bakunya `(exchange, symbol, trade_id, ts_ms, price, qty,
aggressor_buy)` sudah ada di **2.111 hari** arsip aggTrades. Dan kontras yang paling tajam:
registry volume-profile di disk hanya **32 hari** karena `backfill_levels.py` membaca hanya
prefix `data/` dan tidak pernah menyentuh `vision/` — **2.079 hari bahan bakunya menganggur**.
Ini buah terendah di seluruh dokumen: datanya ada, kodenya ada di JS, yang tidak ada adalah
jembatannya.

**Usul, berurutan menurut (punya data & belum diimplementasi):**
1. **footprint bar multi-resolusi (1 s / 1 m / 5 m)** — vol bid/ask per level per bar dari
   aggTrades. Ini fondasi bagi delta divergence, stacking, dan absorption-versi-trade.
2. **volume profile / VPOC harian** atas 2.111 hari, dengan split buy/sell yang registry
   32-hari sekarang **tidak** punya.
3. **trade size distribution** sungguhan (count per bin + kuantil + momen), menggantikan
   taksonomi 4-bin `[1e4,1e5,1e6]` USD yang dipakai tiga kali dengan bentuk berbeda.
4. **aggressor run-length** — nol di mana pun hari ini (grep bersih), dan paling murah dari
   semuanya.

**Risiko.** Artefak turunan menjadi sumber kebenaran kedua yang bisa menyimpang dari tape.
Mitigasi: satu penurun, hash sumber di dalam artefak, dan **artefak turunan tidak pernah
masuk harness OOS tanpa PREREG** — lingkup itu di luar rencana ini.

**Kontrol paritas (wajib).** Untuk satu hari yang tumpang tindih, metrik hasil prakalkulasi
harus cocok **angka-untuk-angka** dengan (a) perhitungan langsung dari aggTrades tanpa
prakalkulasi, dan (b) untuk volume profile, dengan `data/ticks/levels.jsonl` yang sudah ada
pada 32 hari yang tersedia — dengan catatan tick $10 dan aturan snap `Math.floor` vs `round()`
yang sensus §2 catat sebagai **sudah tidak sepakat** antara klien dan server; ketidaksepakatan
itu harus diselesaikan **sebelum** dipakai sebagai kontrol, bukan sesudah.

---

## LAPIS 4 — UI: **BUKAN prioritas, dan aku tidak mengklaim sebaliknya**

**Angka yang ada** (profil §3): halaman terminal mengirim **≈1,15 MB** aset tak-terkompresi
(`terminal-views.js` 311 KB + `terminal.js` 276 KB + `terminal-state.js` 228 KB + …), dan
**agregasi terjadi di klien** pada dua dari tiga rantai yang memberi makan halaman.

**Angka yang TIDAK ada: waktu render.** `verify_terminal_browser.py` ada tetapi sengaja di luar
CI (butuh Playwright), dan menjalankannya di luar lingkup giliran ini. Karena syarat rencana
ini adalah *tiap usulan menyebut angka*, maka: **UI tidak diusulkan untuk disentuh.** Item 4
mensyaratkan lapis ini hanya dikerjakan kalau item 2d menunjukkan UI benar-benar bottleneck —
**item 2d tidak menunjukkan itu**, karena ia tidak mengukurnya.

Yang bisa dinyatakan tanpa mengklaim lebih: agregasi klien atas array besar **adalah** temuan,
dan **1.261 literal numerik inline** plus **495 dari 564 fungsi tanpa test** (sensus §5) adalah
utang yang nyata. Tapi utang bukan bottleneck sampai ada angka yang membuatnya begitu.

---

## 5. Urutan eksekusi kalau rencana ini disetujui

Bukan pilihanku untuk memulai; ini urutannya kalau kau memulai. Lapis 0 lebih dulu karena ia
satu-satunya yang punya angka percepatan langsung (69×) dan karena Lapis 1 dan 3 keduanya
membaca dari format yang ia hasilkan. Lapis 3 mengikuti karena buah terendahnya paling besar
(2.079 hari bahan baku menganggur). Lapis 1 setelahnya karena checkpoint yang salah lebih
berbahaya daripada tidak ada checkpoint. Lapis 2 hanya setelah ada pengukuran endpoint. Lapis 4
tidak dikerjakan sampai ada angka render.

## 6. Yang DIKELUARKAN dari rencana ini karena tidak punya angka pendukung

Dicatat supaya ketiadaannya terlihat, bukan tersembunyi:

- **Menulis ulang `FootprintView` (645 baris, `draw()` 382 baris).** Utang jelas, dampak
  wall-clock tidak terukur. Keluar.
- **Menyatukan empat salinan value-area 70 %.** Duplikasi terukur (4 salinan), manfaat
  performa tidak terukur; ini kerapian, bukan optimasi. Keluar dari rencana *refactor
  performa*, dan itu bukan penilaian bahwa ia tidak penting.
- **Memindahkan akuisisi ke VM Tokyo.** Rasio VM→GCS 2,6× (di bawah ambang 10× yang kau
  sebut), dan angka yang menentukan — VM→OKX — **tidak terukur** karena mengukurnya menuntut
  reset yang akan menghentikan perekam. Keluar sampai terukur.
- **Menambah stream apa pun** (`bookTicker` sebagai kontrol positif BBO). Nilainya nyata dan
  tercatat di `docs/DIAG-data-ceiling-001.md`, tapi itu akuisisi, bukan refactor terminal.
  Keluar dari lingkup.
- **Menyentuh `_profile_endpoint` sekarang.** Loop Python-nya nyata tetapi biayanya belum
  diukur. Keluar sampai diukur.

## 7. Apa yang rencana ini TIDAK klaim

Ia tidak mengklaim tahu berapa cepat terminal akan jadi — satu-satunya percepatan yang terukur
adalah 69× pada satu operasi baca+agregasi pada satu hari satu venue, bukan pada beban kerja
ujung-ke-ujung. Ia tidak mengklaim checkpoint akan benar; ia mengklaim checkpoint tanpa
agreement-per-level yang tercatat di dalamnya **tidak boleh** dipercaya, karena angka itu
terukur 91,8 %–98,2 % dan bukan 100 %. Ia tidak menyentuh apakah metrik mana pun memprediksi
apa pun — nol return dilihat di seluruh giliran ini. Dan ia tidak dieksekusi: setiap lapis di
atas adalah usulan yang menunggu keputusanmu.
