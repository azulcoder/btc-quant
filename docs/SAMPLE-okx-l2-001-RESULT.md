# SAMPLE-okx-l2-001 — HASIL: skema, dan kontrol rekonstruksi yang GAGAL

**Tanggal: 2026-08-08.** Dijalankan persis sebagaimana dideklarasikan di
`docs/SAMPLE-okx-l2-001.md`, yang di-commit sebelum satu byte pun diunduh. Tidak ada satu
pun angka prediktif dihitung di sini (§6 deklarasi).

> **[PEMBALIKAN 2026-08-08 — baca ini lebih dulu]** Vonis GAGAL di bawah **DIBALIK menjadi
> TIDAK BERDASAR**. Setelah kontrolnya dibangun ulang (`docs/DIAG-control-a-v2-001.md`),
> arsip OKX mencapai **97,94 % agreement per level di top-20** — LEBIH TINGGI dari tape
> Binance yang direkam sendiri (97,16 %), dengan pemisahan 7x dari kontrol negatifnya.
> Penyebab v1: kriterianya menuntut kecocokan persis pada instan yang jatuh di tengah event
> diff atomik — mustahil untuk implementasi mana pun (0 dari 27 pasangan jatuh di batas
> event). Larangan menghitung di atas ukuran book OKX **DICABUT**; syarat yang tersisa ada
> di dokumen itu §6.
>
> Riwayat pencabutan pertama, dipertahankan: vonis GAGAL semula **DICABUT sebagai
> BELUM-TERBUKTI**. Kontrol A kemudian diuji pada kasus yang seharusnya lolos — tape Tokyo
> binancef, yang rantainya diatestasi terpisah dengan 0 gap dan 0 resync — dan **mencetak
> 29,3 % pada top-20 di sana, lebih buruk dari OKX di setiap kedalaman**. Instrumennya yang
> rusak. Angka-angka di §2 tetap tercatat karena terukur, tetapi **tidak boleh dibaca
> sebagai pernyataan tentang arsip OKX**. Bukti dan tabel berdampingannya:
> `docs/DIAG-control-a-validation-001.md`. Kalimat aslinya dipertahankan di bawah, tidak
> dihapus.

**~~Vonis satu kalimat: arsip L2 OKX bisa diunduh dan dibaca, harga best bid/ask-nya
terekonstruksi PERSIS, tetapi UKURAN level tidak — kontrol A yang kudeklarasikan GAGAL di
52,2 %, dan tidak ada riset yang boleh berdiri di atas ukuran book OKX sampai penyebabnya
diketahui.~~** [DICABUT — lihat kotak di atas]

## 1. Skema sebenarnya (item 2d)

Diukur dari prefiks 24 MB hari `2024-01-07`, 457.349 baris terurai [DIUKUR 2026-08-08]:

```
kunci top-level : ['action', 'asks', 'bids', 'instId', 'ts']
instId          : BTC-USDT-SWAP
ts              : 1704585660005  — epoch MILIDETIK, 13 digit
entri level     : ['43974.4', '365.0', '22']  ->  [harga, ukuran, jumlah_order]
level/sisi      : snapshot 400 bid / 400 ask · update 15 bid / 14 ask (tipikal)
tick size       : 0,1 USDT (gap minimum antar-level di 60 ask teratas)
kadensi update  : median 10 ms · p90 20 ms · maks 423 ms   (n = 457.257)
kadensi snapshot: 60 detik tepat (1704585660005 -> 1704585720005)
komposisi       : 457.258 update + 91 snapshot dalam prefiks ini
```

Kadensi 10 ms menempatkan arsip ini pada kelas `books-l2-tbt` — kanal yang **live-nya
tergerbang VIP4** tapi **arsip hariannya keyless**. Satuan ukuran adalah kontrak (kolom
kedua), dengan jumlah order sebagai kolom ketiga; itu lebih kaya dari Binance depth diff,
yang tidak memberi jumlah order sama sekali.

**Yang TIDAK ada di arsip, dan ini penting:** tidak ada `seqId`, tidak ada `prevSeqId`,
tidak ada `checksum`. Kanal `books` live OKX punya ketiganya. Artinya **arsip ini tidak
menyediakan cara apa pun untuk mendeteksi update yang hilang** — tidak ada rantai yang bisa
diputus dengan nyaring, tidak seperti aturan `pu` Binance yang dipakai perekam Tokyo.
Absennya mekanisme deteksi itu sendiri adalah temuan, dan ia menjadi tersangka utama §2.

## 2. Kontrol A — rekonstruksi vs snapshot berikutnya: **GAGAL**

Deterministik, ambang 100 % sah (deklarasi §5a). Hasilnya, apa adanya, tidak diperhalus:

| kedalaman dibandingkan | cocok PERSIS (harga + ukuran) |
|---|---|
| top-1 | 72/90 = **80,0 %** |
| top-5 | 68/90 = 75,6 % |
| **top-20 (ambang yang dideklarasikan)** | **47/90 = 52,2 % → GAGAL** |
| top-100 | 15/90 = 16,7 % |

**Vonis: GAGAL.** Ambangnya tidak kuturunkan dan definisinya tidak kuubah agar lolos.

Diagnostik atas kegagalan itu — informasi tentang bentuk kegagalan, bukan pelunakan vonis:

```
|Δ best bid| : median 0,00 · maksimum 0,00     (90/90 pasangan)
|Δ best ask| : median 0,00 · maksimum 0,00     (90/90 pasangan)
jumlah level : 400/400 di rekonstruksiku vs 400/400 di snapshot venue
```

**HARGA best bid dan best ask cocok persis di setiap pasangan snapshot.** Yang meleset
adalah **UKURAN**, dan tingkat kesalahannya tumbuh dengan kedalaman: 20 % di touch,
48 % pada 20 level, 83 % pada 100 level. Jumlah level tetap 400 di kedua sisi, jadi ini
bukan sekadar jendela yang bergeser.

Konsekuensinya tajam dan harus dipatuhi:

* Apa pun yang hanya bergantung pada **harga touch** — spread, mid, arah — terekonstruksi
  persis pada batas snapshot yang diuji.
* Apa pun yang bergantung pada **ukuran** — order-book imbalance, kedalaman, antrean,
  microprice — mewarisi kesalahan yang belum diketahui besarnya. **Tidak boleh dihitung
  sampai penyebabnya diketahui.**

Hipotesis penyebab, dilabeli sebagai hipotesis dan **tidak** diuji di giliran ini:
update yang hilang tanpa jejak (didukung oleh absennya `seqId`/`checksum`); snapshot yang
diambil pada instan sedikit berbeda dari `ts`-nya; atau ambiguitas urutan untuk update yang
berbagi milidetik yang sama dengan snapshot.

## 3. Kontrol B — trade di dalam spread, dengan kontrol negatif

Statistik, jadi tidak ada ambang yang kudeklarasikan sendiri; daya pisahnya diukur lewat
kontrol negatif (deklarasi §5b). Arsip trades adalah berkas dan pipeline yang berbeda
(418.272 baris untuk hari yang sama) [DIUKUR 2026-08-08]:

| pipeline | n | di dalam spread | median pelanggaran | p99 | maks |
|---|---|---|---|---|---|
| **utuh** | 36.485 | **59,81 %** | 3,00 | 24,20 | 36,60 |
| **update diacak** (kontrol negatif) | 31.024 | **0,30 %** | 155,70 | 285,60 | 293,50 |

**Daya pisahnya besar** — median pelanggaran 3,00 vs 155,70, sekitar 52×. Jadi pipeline-nya
jelas melakukan sesuatu yang benar, dan kontrol ini punya daya pisah.

**Tapi levelnya tidak konsisten dengan book yang benar.** Book yang benar seharusnya
memberi hampir semua trade di dalam atau tepat pada touch; 59,81 % bukan itu. Sisanya
belum terjelaskan, dan dua kandidat penyebabnya belum dipisahkan: semantik timestamp yang
berbeda antara dua arsip (waktu matching engine vs waktu publikasi), dan divergensi ukuran
yang ditemukan kontrol A. **Angka ini dilaporkan sebagai belum terjelaskan, bukan sebagai
lolos.**

## 4. Batas yang dinyatakan sebelum riset apa pun (deklarasi §5c)

**Tidak ada tape BBO historis yang independen untuk OKX.** Kanal `bbo-tbt` live tanpa
replay; candle dan trades tidak memuat quote. Jadi kebenaran book OKX hasil rekonstruksi
hanya bisa diuji sejauh kontrol A (internal, sekarang GAGAL) dan kontrol B (semi-independen,
punya daya pisah tapi levelnya belum terjelaskan). Ini **batas permanen dari data yang
tersedia**, dan setiap PREREG yang memakai sampel ini wajib mengutipnya.

## 5. Apa yang hasil ini TIDAK buktikan

Kegagalan kontrol A **bukan** bukti bahwa arsip OKX rusak — sama mungkinnya bahwa semantik
penerapan update-ku yang salah, dan justru itulah kenapa kontrol ini dideklarasikan sebelum
data diunduh. Ia juga tidak mengubah temuan bahwa arsipnya ada, keyless, dan bisa diunduh:
akuisisi tetap sah, yang tidak sah adalah membangun riset di atas ukuran book sebelum
selisih ini terjelaskan. Angkanya berasal dari **satu hari dan prefiks 24 MB** (90 pasangan
snapshot, ~76 menit pasar) — perilaku pada hari lain, rezim lain, atau kedalaman lain belum
diukur sama sekali. Dan tidak ada apa pun di sini yang menyentuh pertanyaan apakah book OKX
membawa informasi yang bisa dipanen; tidak ada satu pun return yang dilihat, sesuai §6
deklarasi.
