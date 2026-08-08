# DIAG-control-a-v2-001 — kontrol rekonstruksi dibangun ulang, dan vonis OKX berbalik

**Tanggal: 2026-08-08.** Lanjutan langsung dari `docs/DIAG-control-a-validation-001.md`,
yang membuktikan kontrol v1 rusak. Dokumen ini mencari **penyebab persisnya**,
memperbaikinya, memvalidasi hasil perbaikannya, lalu menjalankan ulang kedua venue.
Tidak ada angka prediktif; nol return dilihat.

**Vonis satu kalimat: kontrol v1 menuntut sesuatu yang secara struktural mustahil; setelah
diperbaiki, arsip L2 OKX merekonstruksi LEBIH BAIK daripada tape Binance yang direkam
sendiri, di setiap kedalaman.**

## 1. Empat cacat, ditemukan berurutan, semuanya terukur

Semua diukur pada tape Tokyo — kasus yang seharusnya lolos (rantai `pu` terverifikasi,
0 gap sequence, 0 resync). Metrik v1: "seluruh top-20 cocok persis".

| # | cacat | top-20 sesudah diperbaiki |
|---|---|---|
| — | v1 sebagaimana ditulis | **29,3 %** |
| 1 | **seleksi posisional**: perekam menulis baris snapshot saat REST-nya *kembali*, jadi frame yang tiba selama panggilan itu mendarat sesudahnya dan terpotong | 34,1 % |
| 2 | **event straddle di ujung dibuang** padahal update-nya sudah ada di dalam snapshot penutup | 41,5 % |
| 3 | **pasangan yang melintasi jeda perekaman ikut dinilai** — 12 dari 41 pasangan melintasi restart; saat downtime tak ada frame yang pernah tiba, jadi mereka **0,0 % di semua kedalaman by construction** | 59,3 % (dari 27 pasangan bersih) |
| 4 | **kriterianya sendiri mustahil** — lihat §2 | — |

## 2. Cacat keempat: kriteria yang tidak bisa benar

Snapshot REST adalah **titik waktu**. Event diff adalah **rentang atomik** `[U, u]` yang
mengagregasi ribuan update individual (~6.000–8.800 id per event, terukur). Supaya
rekonstruksi bisa persis, `lastUpdateId` snapshot harus jatuh **tepat di ujung** sebuah
event.

Terukur: **0 dari 27 pasangan bersih** memenuhi itu — semuanya jatuh di tengah event
[DIUKUR 2026-08-08]. Peluangnya memang ~1/8.800 per pasangan.

Konsekuensinya mutlak: membuang event straddle → **kurang-terapkan**; menyertakannya →
**lebih-terapkan**; dan event itu **tidak bisa dibelah** karena tidak membawa id per level.
Jadi tidak ada implementasi mana pun yang bisa lolos kriteria v1. **v1 menuntut yang
mustahil lalu menyalahkan datanya.**

Buktinya terlihat langsung di data: pada pasangan yang gagal, update terakhir yang terpakai
punya `u` **melebihi** `L1` sebesar 3.113 / 4.386 / 3.977 / 3.923 — dan selisih ukurannya
mungil (14,668 vs 14,568; 16,283 vs 16,282).

## 3. Yang v1 sembunyikan: 97,8 % level sebenarnya cocok

Kriteria "seluruh top-N harus persis" menjatuhkan **satu pasangan penuh** karena **satu
level** meleset. Dihitung per level, bukan per pasangan: **10.561 dari 10.800 level cocok
persis = 97,79 %** [DIUKUR 2026-08-08]. Instrumen yang melaporkan 29 % untuk data yang
97,8 % benar bukan instrumen yang ketat — ia instrumen yang salah satuan.

## 4. Kontrol v2 — apa yang diukur sekarang

`scripts/control_a_v2.py`. Perubahan desain, masing-masing menjawab satu cacat di atas:

- **Agregasi per LEVEL**, bukan semua-atau-tidak per pasangan.
- **Pasangan yang melintasi jeda dikecualikan** (Binance punya baris `gap`/`start`/`stop`
  untuk mendeteksinya; OKX tidak punya penanda apa pun — dinyatakan, bukan diasumsikan).
- **Distribusi selisih** dilaporkan (median, p05, p95, fraksi positif). Instan pembandingan
  yang jatuh di tengah event menghasilkan derau **simetris**; bug sistematis menghasilkan
  **skew**. Ini yang memisahkan H1 dan H2, bukan pilihan naratif.
- **Uji H1 eksplisit**: untuk tiap level yang meleset, apakah harga itu pernah disentuh
  update di jendela itu.
- **Kontrol negatif** (urutan update diacak) — karena laju tanpa pembanding bukan bukti.
- **Tidak ada ambang yang dideklarasikan.** Kesalahan v1 adalah mengarang satu.

## 5. Hasil — kedua venue, tabel yang sama

Agreement **per level**, kontrol negatif dalam kurung [DIUKUR 2026-08-08]:

| kedalaman | **Binance** (29 pasangan bersih) | **OKX** (90 pasangan) |
|---|---|---|
| top-1 | 67,24 % *(acak 1,72 %)* | **88,33 %** *(acak 3,89 %)* |
| top-5 | 92,41 % *(acak 4,83 %)* | **97,11 %** *(acak 15,22 %)* |
| top-20 | 97,16 % *(acak 8,62 %)* | **97,94 %** *(acak 13,64 %)* |
| top-100 | 97,41 % *(acak 7,57 %)* | **97,81 %** *(acak 10,86 %)* |

**Pemisahan dari kontrol negatif 7–11×** di kedua venue: instrumennya punya daya pisah.
**OKX lebih baik dari Binance di setiap kedalaman.**

Diagnostik pendukung:

| | Binance | OKX |
|---|---|---|
| fraksi selisih positif | 50,7 % (simetris) | 56,1 % |
| median selisih | +0,0010 BTC | +1 kontrak |
| level meleset yang TIDAK pernah ter-update | **0/150 = 0,0 %** | **0/394 = 0,0 %** |
| peringkat meleset (median) | 42 | 58 |

**H1 (batas jendela 400/1000 — level menyeberang batas jadi basi): DITOLAK di kedua venue.**
Nol persen level yang meleset adalah level yang tak pernah ter-update; semuanya justru level
yang **baru saja** ter-update.

**H2 (granularitas waktu / instan pembandingan): DIDUKUNG.** Selisihnya simetris di sekitar
nol (50,7 % positif di Binance), mungil, dan menumpuk pada level yang paling sering berubah
— top-1 adalah kedalaman dengan agreement TERENDAH di kedua venue, persis yang diprediksi
kalau penyebabnya instan pembandingan, bukan cacat penerapan update.

*Catatan satuan (kelas D):* median selisih Binance dalam **BTC**, OKX dalam **kontrak**.
Keduanya tidak sebanding langsung dan sengaja tidak dijadikan perbandingan; yang
dibandingkan adalah **laju agreement**.

## 6. Pembalikan vonis

> **[PEMBALIKAN 2026-08-08]** Vonis **GAGAL** atas rekonstruksi arsip L2 OKX — yang kemarin
> sudah dicabut sebagai *belum-terbukti* — kini **dibalik menjadi TIDAK BERDASAR**. Dengan
> instrumen yang tervalidasi, arsip OKX mencapai **97,94 % agreement per level pada top-20**,
> lebih tinggi dari tape Binance yang direkam sendiri (97,16 %), dengan pemisahan 7× dari
> kontrol negatifnya. Kalimat asli di `docs/SAMPLE-okx-l2-001-RESULT.md` tetap di tempatnya,
> dicoret dan diberi penunjuk; tidak ada yang dihapus.
>
> Larangan "jangan hitung apa pun berbasis ukuran book OKX" **DICABUT**, dengan satu syarat
> yang tetap berlaku dan harus dikutip: rekonstruksi book dari snapshot+diff mana pun —
> OKX maupun Binance — **tidak pernah bisa persis pada instan sembarang**, karena event diff
> atomik. Ketidakpastiannya terukur: ~2 % level, simetris, terkonsentrasi pada level yang
> paling aktif. Setiap besaran yang sensitif terhadap ukuran level top-1 mewarisi ~12 %
> ketidakcocokan di Binance dan ~12 % di OKX pada instan snapshot.

## 7. Apa yang dokumen ini TIDAK buktikan

Ia tidak membuktikan arsip OKX bebas cacat — ia membuktikan arsip OKX **tidak lebih buruk
dari tape yang kita rekam sendiri**, diukur dengan instrumen yang daya pisahnya sudah
diuji. Ia juga tidak menyentuh apakah ukuran level itu **akurat terhadap book venue yang
sebenarnya**: kedua sisi perbandingan berasal dari produsen yang sama di tiap venue, dan
untuk OKX tetap **tidak ada tape BBO historis independen** (batas permanen, tetap berlaku).
Angkanya dari **satu hari per venue** (29 dan 90 pasangan snapshot); hari lain, rezim lain,
dan hari bervolatilitas tinggi belum diukur sama sekali. Dan tidak ada satu pun return yang
dilihat di sini — apakah book manapun membawa informasi yang bisa dipanen tetap pertanyaan
yang belum disentuh.
