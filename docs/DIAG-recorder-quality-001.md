# DIAG-recorder-quality-001 — kesehatan perekam depth-diff, dihitung dari tape-nya sendiri

**Tanggal: 2026-08-08.** Semua angka di bawah berasal dari **bytes yang benar-benar ditulis
perekam**, bukan dari penghitung dalam-proses yang mati bersama prosesnya (kelas A: saksi
tidak boleh berbagi nasib dengan yang disaksikan). Instrumen: `deploy/gcp/tape_qc.py`,
dijalankan di VM tiap boot, hasilnya diunggah ke bucket.

**Kontrol positif untuk instrumen ini** [DIUKUR 2026-08-08]: seluruh tape hari itu diunduh
kembali dari GCS **ke mesin lain (Mac)**, dirakit ulang dari 5 objek, lalu dihitung ulang
dengan jalur kode yang sama tetapi proses, mesin, dan salinan bytes yang berbeda. Hasilnya
cocok angka-per-angka dengan heartbeat yang ditulis VM: **131.569 frame, 2 hole, 70.627 byte
hole**. Angka pertama dari instrumen baru adalah kontrol, dan kontrol itu lolos.

---

## 1. Yang bisa dihitung dari tape — dan hasilnya

Jendela terpantau: `00:55:49Z .. 04:45:26Z` (3,83 jam) [DIUKUR 2026-08-08].

### 1a. Laju frame per jam

| jam UTC | fps | cakupan terukur | frames |
|---|---|---|---|
| 00 | 9,80 | 250 s | 2.452 |
| 01 | 9,17 | 3.600 s | 33.017 |
| 02 | 9,80 | 3.600 s | 35.293 |
| 03 | 9,80 | 3.600 s | 35.293 |
| 04 | 9,49 | 2.126 s | 20.172 |

Kolom **cakupan** ada karena versi pertama sensus ini tidak memilikinya dan langsung
menghasilkan alarm palsu — lihat §3. Laju rata-rata seluruh jendela: **9,55 fps**. Probe
independen dari Mac pada URL stream yang sama, saat yang sama: **9,80 pesan/detik**
[DIUKUR 2026-08-08] — dua mesin, dua benua, laju yang sama.

### 1b. Rantai sequence: gap dan resync

| verdict rantai | jumlah |
|---|---|
| `ok` (pu == prev u) | 131.551 |
| `first_ok` (straddle snapshot) | 11 |
| `drop_pre_snapshot` (u < lastUpdateId) | 7 |
| `gap` | **0** |
| `malformed` | **0** |

**Nol gap sequence, nol resync** sepanjang 3,83 jam. Snapshot 23 buah, semuanya `connect`
atau `periodic`; tidak satu pun `chain_break`. Artinya: setiap frame yang tiba menyambung
persis ke frame sebelumnya, dan book bisa direplay tanpa lubang di dalam tiap jendela
uptime.

### 1c. Proxy latensi (`recv_ms` − `E` event-time)

Seluruh jendela: **p50 2 ms · p90 4 ms · p99 11 ms · maks 581 ms** (n = 131.569)
[DIUKUR 2026-08-08]. Per jam: 01h `[2,2,8]`, 02h `[1,2,2]`, 03h `[2,3,3]`, 04h `[5,10,12]`.

Jam 00 memberi **`[-4,-2,-1]` ms — negatif, yang secara fisik mustahil** (menerima sebelum
peristiwa terjadi). Itu bukan noise, itu **jam VM belum sinkron** pada menit-menit pertama
setelah boot. Nilainya dibiarkan negatif dan dilaporkan negatif: sebuah proxy yang
menunjukkan kontaminasinya sendiri lebih berguna daripada proxy yang dirapikan. Konsekuensi
yang harus dibawa: **p50 2 ms adalah jumlah dari delay kirim venue DAN galat jam kotak ini**,
dan tape tidak memuat apa pun yang bisa memisahkan keduanya.

### 1d. Siklus hidup proses dan downtime perekaman

| metrik | nilai [DIUKUR 2026-08-08] |
|---|---|
| baris `start` | 11 |
| baris `stop` bersih (SIGTERM tertangkap) | 4 |
| terminasi **tidak bersih** (selisihnya) | **7** |
| jendela downtime (celah antar-frame > 5 s) | 10 |
| total downtime | 358 s = 6,0 menit |
| **uptime perekaman** | **97,40 %** dari jendela terpantau |

Sepuluh jendela itu, apa adanya:

```
01:03:38->01:04:36  57,4 s     01:20:54->01:21:04  10,3 s
01:06:58->01:07:34  35,6 s     04:14:19->04:15:18  58,7 s
01:13:20->01:14:01  41,5 s     04:15:33->04:15:43  10,2 s
01:16:04->01:16:51  47,4 s     04:35:26->04:36:16  49,8 s
01:19:54->01:20:35  41,5 s     04:36:30->04:36:36   5,7 s
```

**Setiap satu dari sepuluh jendela itu disebabkan oleh pekerjaanku sendiri**, bukan oleh
venue dan bukan oleh kegagalan perekam: rantai debug boot (lima boot 01:0x–01:2x),
pemasangan service account (04:14, butuh stop/start), dan dua reset deploy (04:15, 04:35).
Harganya **6 menit microstructure yang tidak bisa dibeli mundur**. Polanya terukur dan
berguna untuk keputusan berikutnya: **boot penuh ≈ 40–59 s, restart di tempat ≈ 6–10 s.**

### 1e. Hole di tape (tear mid-file)

Dua hole, 70.627 byte [DIUKUR 2026-08-08], keduanya dari `reset`/`stop` keras yang memotong
satu gzip member di tengah penulisan. Batas atas kehilangannya **≤ 200 frame per hole**
(satu buffer flush, konstanta `FLUSH_EVERY` di perekam) ≈ ≤ 20 detik per hole pada laju
terukur. Hole diunggah apa adanya sebagai objek `.hole` — dicatat, tidak pernah diperbaiki.

---

## 2. Yang TIDAK bisa dilihat perekam tentang dirinya sendiri — temuan

Item ini diminta secara eksplisit, dan jawabannya bukan "semuanya terekam".

1. **Kebenaran rekonstruksi book: [UNVERIFIED], dan akan tetap begitu.** Tape memuat
   snapshot + diff dan buktinya sendiri bahwa rantainya utuh — tapi "rantai utuh" hanya
   membuktikan tidak ada pesan yang hilang, **bukan** bahwa book hasil replay sama dengan
   book venue. Tidak ada sumber independen di tape untuk mengujinya. Yang akan memberikannya
   adalah BBO venue (`bookTicker`), dan itu tidak direkam siapa pun — dicatat di
   `docs/DIAG-data-ceiling-001.md`, bukan dikerjakan di sini.
2. **Isi member yang robek tidak bisa dihitung.** Hole punya ukuran byte, bukan jumlah
   frame. Yang bisa dinyatakan hanyalah batas atas (≤ 200 frame), bukan angka pasti.
3. **Proxy latensi tidak bisa dipisah** menjadi komponen venue dan komponen jam lokal.
   Nilai negatif di jam 00 membuktikan kontaminasi itu **ada**; tidak ada di tape yang bisa
   mengukur **besarnya**.
4. **Tear di ujung berkas tidak bisa dibedakan dari "sedang ditulis"** sampai ada data
   sesudahnya. Terukur langsung: sensus 04:36 melaporkan **1** hole, penelusuran 04:45 atas
   berkas yang sama melaporkan **2** — yang kedua baru bisa diklasifikasi setelah perekam
   yang restart menambahkan member baru di belakangnya. Angka hole karena itu adalah
   **lower bound saat dibaca**, bukan final.
5. **Frame yang tidak pernah dikirim venue tidak meninggalkan jejak.** Rantai `pu` menangkap
   apa pun yang hilang di jalan; ia tidak bisa menangkap apa yang tidak pernah berangkat.
   Ini keterbatasan yang dibagi setiap konsumen stream mana pun, dan disebut supaya tidak
   dibaca sebagai jaminan.

---

## 3. Alarm palsu yang dihasilkan instrumen ini sendiri, dan perbaikannya

Sensus versi pertama membagi jumlah frame dengan **3.600 detik** tanpa peduli berapa lama
tape benar-benar menutupi jam itu. Jam yang hanya berisi enam menit rekaman karena itu
dilaporkan **`0,68 fps`** — pembacaan yang tampak seperti keruntuhan throughput 13×,
padahal laju sebenarnya normal. Itu kelas D (skala tak sepadan: pembilang dan penyebut
mengukur hal berbeda), dan biayanya satu alarm palsu.

Perbaikannya: fps dihitung atas **rentang `recv_ms` yang benar-benar terukur**, dan kolom
cakupan dicetak **di sebelah** angkanya supaya tidak ada yang menyimpulkan ulang dari satu
angka telanjang. Ada test yang mengunci bentuk kegagalan persisnya (grep
`test_qc_fps_uses_measured_coverage_not_calendar_hour`).

Kegagalan kedua, lebih mahal, tercatat di `deploy/gcp/README.md`: pembaca tape berhenti di
tear pertama karena mengasumsikan tear hanya terjadi di ujung. Sync, heartbeat, dan QC
karena itu **buta terhadap 96,6 % berkas hari itu** sementara semuanya melaporkan sukses —
heartbeat mencetak `frames_since_last_hb: 0` sambil berkasnya jelas bertambah besar. Kelas
yang sudah dikenal repo ini: **sukses semu**. Perbaikannya kini punya test-nya sendiri (grep
`test_walk_tape_recovers_members_after_midfile_tear`).

---

## 4. Apa yang dokumen ini TIDAK buktikan

Uptime 97,40 % adalah properti **3,83 jam yang seluruhnya berisi pekerjaan deploy-ku**, bukan
estimasi uptime steady-state — hari tanpa deploy akan terlihat sangat berbeda ke dua arah
dan belum pernah diukur. Nol gap sequence membuktikan rantai `pu` utuh di jendela ini saja,
pada satu venue, satu simbol, satu kadensi; ia tidak menjanjikan apa pun tentang perilaku
saat volatilitas tinggi, yang belum pernah dilalui perekam ini. Angka latensi adalah proxy
gabungan yang tidak bisa dipisah (§2.3), jadi "p50 2 ms dari Tokyo" **bukan** klaim latensi
jaringan terukur. Dan seluruh dokumen ini mengukur **kesehatan instrumen**, bukan nilai
datanya: tape yang sehat, lengkap, dan tersalin dua tempat tetap belum menjawab satu pun
pertanyaan riset — itu urusan PREREG, bukan urusan sensus.
