# DIAG-control-a-regime-001 — apakah agreement rekonstruksi berkorelasi dengan volatilitas?

**Tanggal: 2026-08-08.** Menjalankan `docs/SAMPLE-okx-l2-002-regime-days.md`, yang di-commit
sebelum satu hari pun dipilih. Nol angka prediktif; nol return dilihat.

**Vonis satu kalimat: TIDAK KONKLUSIF — korelasinya tidak bisa ditegakkan maupun
dipatahkan dengan data yang berhasil diambil, dan peringatan §5 aturan tetap berlaku
justru karena itu.**

## 1. Yang berhasil dijalankan, dan yang tidak

Aturan menyeleksi 9 hari (MAX/MED/MIN realized-range per tahun 2024–2026), diberi peringkat
oleh kline harian Binance — sumber di luar arsip yang dinilai. Rentang volatilitas terpilih:
**0,0131 sampai 0,0860 = 6,6×**.

**Cakupan hari-penuh GAGAL, dan alasannya bukan memori.** Kontrol versi streaming memang
sanggup memproses hari penuh (~2,9 GB terdekompresi) dalam memori konstan — itu diuji dan
bekerja. Yang menghalangi adalah **throughput jaringan yang terukur runtuh** di tengah
giliran ini [DIUKUR 2026-08-08]:

| sumber | pagi | sore |
|---|---|---|
| OKX CDN | 1,4 MB/s | **0,07 MB/s** |
| Hugging Face | 0,9–3,3 MB/s | **0,02 MB/s** |

Pada 0,02 MB/s, satu hari penuh butuh ~5,6 jam. Diagnosis: uplink mesin lokal sedang
dipakai proses lain (bukan job repo ini — hanya satu job repo yang hidup saat diukur).
Maka: **nol hari OKX diproses penuh di giliran ini**, dan kesembilan hari memakai **prefiks
12 MB yang konsisten**. Mode dicatat per hari di `reports/okx-regime-control.jsonl`; tidak
ada satu pun hari yang diam-diam turun ke prefiks.

Satu hari penuh yang **berhasil** diproses adalah tape **Binance** `2026-08-08` — sudah ada
di disk lokal, jadi tidak melewati jaringan.

## 2. Hasil per hari (item 1c)

Prefiks 12 MB, agreement **per level** [DIUKUR 2026-08-08], diurutkan menurun berdasarkan
volatilitas:

| hari | realized_range | pasangan | top-1 | top-5 | top-20 | top-100 |
|---|---|---|---|---|---|---|
| 2025-04-07 | 0,0860 | 9 | 72,22 % | 87,78 % | 92,50 % | 91,22 % |
| 2026-04-07 | 0,0731 | **1** | 100 % | 100 % | 100 % | 100 % |
| 2024-08-07 | 0,0568 | 19 | 65,79 % | 86,32 % | 92,37 % | 94,89 % |
| 2025-12-07 | 0,0452 | 50 | 81,00 % | 94,40 % | 95,80 % | 94,10 % |
| 2024-10-07 | 0,0375 | 34 | 83,82 % | 95,29 % | 98,24 % | 97,97 % |
| 2026-01-07 | 0,0328 | **1** | 0 % | 70,00 % | 80,00 % | 86,50 % |
| 2026-03-07 | 0,0246 | **2** | 75,00 % | 90,00 % | 97,50 % | 97,75 % |
| 2024-12-07 | 0,0163 | 32 | **10,94 %** | 79,38 % | 91,80 % | 96,55 % |
| 2025-09-07 | 0,0131 | 78 | 85,26 % | 96,03 % | 98,08 % | 97,78 % |

Kontrol negatif (update teracak) pada `2024-08-07`: **top-1 0 % · top-5 10,00 % ·
top-20 11,05 % · top-100 10,76 %** — terpisah ~8× dari yang utuh, jadi instrumennya masih
punya daya pisah di rezim ini.

## 3. Korelasi (item 1d) — dan kenapa ia tidak menjawab

| kedalaman | Spearman, semua 9 hari | Spearman, hari dengan ≥10 pasangan (n=5) |
|---|---|---|
| top-1 | +0,100 | −0,400 |
| top-5 | +0,100 | −0,400 |
| top-20 | +0,117 | −0,200 |
| top-100 | −0,167 | −0,600 |

Pada n=5, nilai kritis Spearman di p=0,05 sekitar **0,90**. Jadi −0,600 pun **jauh dari
signifikan**. Pada seluruh 9 hari koefisiennya praktis nol.

**Konfound yang terukur, bukan didalilkan:** prefiks berukuran-byte tetap menutupi **rentang
waktu yang lebih pendek pada hari yang sibuk**. Spearman(jumlah pasangan, realized_range) =
**−0,417** — persis arah yang membuat hari bervolatilitas tinggi punya estimasi paling
lemah. Dua hari hanya menghasilkan **satu** pasangan snapshot, dan satu pasangan bukan
estimasi.

**Uji pendamping di Binance** (dalam-hari, karena tape baru punya satu hari): Spearman
agreement(top-20) vs realized range per jam = **+0,543 (n=6)**, arah **berlawanan**, dan
sama-sama jauh di bawah signifikansi. Blok `@0ms` hanya punya 2 jam sehingga tak bisa
dihitung terpisah — kadensi berubah di tengah hari (`@100ms` → `@0ms` pada 07h) dan itu
konfound tersendiri untuk perbandingan dalam-hari.

**Kesimpulan: TIDAK KONKLUSIF.** Dua uji, dua arah berlawanan, keduanya tak berdaya. Yang
bisa dinyatakan: agreement per level berada di **91,8 %–98,2 % pada top-20** di seluruh
rentang volatilitas 6,6× yang diuji, dan tidak ada hari yang runtuh ke wilayah kontrol
negatif.

## 4. Peringatan §5 aturan — TETAP BERLAKU, justru karena tidak konklusif

Aturan mendeklarasikan kalimat ini sebelum angkanya ada, untuk dipasang **kalau** korelasinya
ada. Korelasinya tidak tegak — tetapi juga **tidak terbantah**, dan titik estimasinya pada
hari-hari yang punya cukup pasangan **negatif di keempat kedalaman**. Postur yang aman
karena itu tidak berubah:

> Derau rekonstruksi book **mungkin berkorelasi dengan volatilitas, dan ini belum
> terselesaikan**. Kalau ia berkorelasi, ia **BUKAN** attenuation bias yang konservatif:
> derau yang tak berkorelasi dengan sinyal hanya menarik estimasi ke arah nol — aman, karena
> temuan positif tetap temuan. Derau yang **berkorelasi dengan volatilitas** bisa
> **menciptakan** hubungan yang tidak ada pada setiap besaran yang juga berhubungan dengan
> volatilitas, dan hampir semua besaran mikrostruktur begitu (spread, imbalance, kedalaman,
> intensitas trade). **Setiap PREREG yang memakai ukuran book hasil rekonstruksi wajib
> mengutip batas ini dan menyatakan bagaimana ia dikendalikan** — sampai ada uji berdaya
> yang menutupnya.

Uji yang akan menutupnya sudah jelas bentuknya dan **tidak dijalankan di sini**: hari penuh
(bukan prefiks) pada ≥9 hari, sehingga jumlah pasangan tidak lagi anti-berkorelasi dengan
volatilitas. Itu butuh throughput yang, sore ini, tidak ada.

## 5. Apa yang dokumen ini TIDAK buktikan

Ia tidak membuktikan derau rekonstruksi bebas dari volatilitas — ia gagal menegakkan
maupun mematahkannya, dan kegagalan itu berasal dari cakupan, bukan dari alam. Ia juga
tidak menyentuh apakah angka 97,9 % dari `DIAG-control-a-v2-001` berlaku umum: di sembilan
hari ini top-20 bergerak **91,8 %–98,2 %**, jadi 97,9 % adalah nilai di ujung baik rentang,
bukan konstanta. Dua hari dengan satu pasangan snapshot dilaporkan apa adanya dan tidak
boleh dibaca sebagai pengukuran. Dan tidak ada satu pun return dilihat di seluruh dokumen
ini.
