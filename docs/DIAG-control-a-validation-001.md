# DIAG-control-a-validation-001 — kontrol A diuji pada kasus yang SEHARUSNYA lolos

**Tanggal: 2026-08-08.** Class I, rail `CLAUDE.md` (grep `the verifier cries wolf`):
*verifier diuji pada kasus yang LOLOS, bukan hanya kasus yang gagal.*

**Vonis satu kalimat: kontrol A rusak, dan vonis GAGAL atas arsip L2 OKX DICABUT sebagai
belum-terbukti.**

## 1. Kenapa uji ini harus ada

Kontrol A lahir kemarin dan langsung memvonis GAGAL data yang sedang diadili (arsip L2 OKX,
52,2 % pada top-20). Sebuah checker yang hanya pernah dijalankan pada kasus yang ia gagalkan
tidak memberi tahu apa pun tentang **laju positif-palsunya sendiri**. Rail-nya sudah ada di
repo ini justru karena presisi buruk pada checker **menghancurkan pekerjaan yang benar**,
bukan sekadar menambah derau.

Kasus yang seharusnya lolos: **tape Tokyo (binancef)**. Integritas rantainya diatestasi
secara terpisah dan oleh mekanisme yang berbeda — aturan rantai `pu` diperiksa frame demi
frame saat perekaman, dengan hasil **0 gap sequence dan 0 resync** sepanjang hari
(`docs/DIAG-recorder-quality-001.md`). Kalau ada instrumen yang seharusnya lolos, itu dia.

## 2. Hasil, berdampingan, di tabel yang sama (item 1d)

Kontrol yang **sama persis**, empat kedalaman yang sama:

| kedalaman | **Binance** — kontrol apa adanya | **Binance** — setelah 2 cacat kontrol diperbaiki | **OKX** — kontrol apa adanya |
|---|---|---|---|
| top-1 | **36,6 %** | 46,3 % | **80,0 %** |
| top-5 | **34,1 %** | 46,3 % | **75,6 %** |
| **top-20** | **29,3 %** | **41,5 %** | **52,2 %** |
| top-100 | **7,3 %** | 22,0 % | **16,7 %** |

`n` = 41 pasangan snapshot (Binance) dan 90 pasangan snapshot (OKX) [DIUKUR 2026-08-08].

**Binance — venue yang rantainya terbukti utuh — mencetak angka LEBIH BURUK dari OKX di
setiap kedalaman.** Itu bukan hasil yang bisa dibaca sebagai "Binance juga rusak"; itu
tanda bahwa yang mengukur sedang rusak.

## 3. Bukti tambahan: tiap perbaikan pada KONTROL menaikkan skor

Bukan hanya gagal — gagalnya bergerak setiap kali cacat pada instrumennya sendiri diperbaiki
[DIUKUR 2026-08-08], sementara datanya tidak berubah sama sekali:

| versi kontrol | top-20 |
|---|---|
| pemilihan **posisional** (sebagaimana ditulis semula) | 29,3 % |
| **cacat 1 diperbaiki** — pilih murni dari rentang update-id, abaikan posisi | 34,1 % |
| **cacat 2 diperbaiki** — sertakan event yang meng-straddle `L1` di ujung interval | 41,5 % |

Dua cacat itu nyata dan terukur:

1. **Pemilihan posisional bocor.** Perekam menuliskan baris snapshot ketika panggilan REST
   **kembali**; frame yang tiba selagi panggilan itu masih terbang mendarat *sesudah* baris
   snapshot di berkas. Mengiris berkas berdasarkan posisi karena itu membuang frame yang
   sah. Terukur pada satu interval: 182 frame terpilih untuk jendela 29 detik, padahal
   harga terbaiknya sendiri disentuh 115 update.
2. **Event batas-akhir dibuang.** Event `depthUpdate` membawa rentang `[U, u]`. Event yang
   rentangnya melintasi `lastUpdateId` snapshot penutup sudah **ikut** di dalam snapshot itu,
   tapi filter `u <= L1` membuangnya utuh.

Bahwa masih ada 58,5 % yang meleset setelah dua perbaikan berarti **masih ada cacat ketiga
yang belum kutemukan**. Itu dinyatakan sebagai tidak diketahui, bukan ditambal dengan
toleransi.

Satu pengukuran pendukung: mode `time-only` (satu-satunya penyelarasan yang diizinkan arsip
OKX) memberi angka **identik** dengan mode posisional pada Binance — jadi pada tape ini
penyelarasan waktu vs sequence **bukan** sumber selisihnya.

## 4. Konsekuensi — pencabutan bertanggal

> **[PENCABUTAN 2026-08-08]** Vonis **GAGAL** atas rekonstruksi arsip L2 OKX di
> `docs/SAMPLE-okx-l2-001-RESULT.md` §2 **DICABUT sebagai BELUM-TERBUKTI**. Angkanya
> (top-20 = 52,2 %) tidak ditarik — ia terukur dan tetap tercatat — tetapi **tidak boleh
> lagi dibaca sebagai pernyataan tentang arsip OKX**, karena instrumen yang menghasilkannya
> mencetak 29,3 % pada tape yang rantainya terbukti utuh. Kalimat lamanya tidak dihapus;
> pencabutan ini yang berlaku.
>
> Yang **TIDAK** dicabut, karena tidak bergantung pada kontrol A: skema arsip OKX (§1
> dokumen itu), absennya `seqId`/`checksum`, dan kontrol B beserta kontrol negatifnya.
> Juga tidak dicabut: pengamatan bahwa **harga** best bid/ask OKX cocok persis di 90/90
> pasangan — itu justru menguat, karena kontrol yang sama pada Binance memberi
> `|Δ best bid| median 0,00` juga.

**Item 2 dari perintah (memisahkan H1 batas-jendela vs H2 granularitas-waktu) menjadi
moot** dan sengaja TIDAK dikerjakan: premisnya adalah bahwa divergensi ukuran itu milik
OKX, dan premis itu baru saja gugur. Mengukur H1/H2 dengan instrumen yang laju
positif-palsunya 70 % hanya akan menghasilkan dua angka yang sama tidak berartinya.

## 5. Apa yang dokumen ini TIDAK buktikan

Ia **tidak** membuktikan arsip OKX benar — ia hanya membuktikan bahwa kita belum punya alat
yang bisa menilainya. Ia juga tidak membuktikan tape Binance sempurna: yang diatestasi
adalah rantai `pu`-nya utuh, dan itu pernyataan tentang kelengkapan pesan, bukan tentang
kesamaan book hasil replay dengan book venue. Angkanya berasal dari **satu hari** di tiap
venue (41 dan 90 pasangan snapshot); hari lain belum diukur. Dan tidak ada di sini yang
menyentuh pertanyaan apakah book venue mana pun membawa informasi yang bisa dipanen — nol
return dilihat, sesuai perintah.
