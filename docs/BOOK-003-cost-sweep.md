# BOOK-003 — kegagalan 3c diselesaikan, dan sweep sensitivitas biaya

**Look: diagnostik provenance.** Tidak ada return, P&L, Sharpe, atau equity curve. Sisi biaya saja.
Pajak tetap di luar; parameternya 0 dan tidak ada angka pajak di sini.
**Skrip:** `scripts/diag_funding_paired_001.py`, `scripts/diag_cost_sweep_001.py`
**Mesin:** `reports/funding-paired-001.json`, `reports/cost-sweep-001.json` (+ `-cells.json`)
**Semua angka `[DIUKUR]`**, 2026-08-08.

---

## 1. Uji berpasangan — 3c ditutup, tapi bukan tanpa catatan

Rute B dan settled dipasangkan pada **slot settlement yang sama**, bukan median lawan median:

| venue | rute B n | berpasangan | cocok persis | `|d|` p50 | `|d|` p95 | `|d|` maks | signed p50 |
|---|---:|---:|---:|---:|---:|---:|---:|
| binancef | 61 | 61 | 34 | 0,0000 | 0,0105 | 0,0232 | +0,0000 |
| bybit | 54 | 54 | 8 | 0,0034 | **0,7697** | **0,9716** | +0,0012 |
| okx | 66 | 66 | 5 | 0,0023 | 0,0074 | 0,0150 | −0,0002 |

**Median cocok di ketiganya** (`|d|` p50 ≤ 0,0034 bps), jadi diskrepansi 3c adalah **SELEKSI**,
bukan ketidaksepakatan nilai. **Settled adalah sumber yang benar, dan rute B pensiun sebagai
estimator funding.**

**Catatan yang gerbang otomatisku lewatkan.** Vonis skrip hanya memeriksa `p50`. Ekor bybit
**tidak** ~0: `p95 = 0,7697` dan maks `0,9716` bps — 5 % pasangan berselisih lebih dari 0,77 bps.
Itu tidak membalik kesimpulan, ia memperkuatnya: rute B bukan hanya bias seleksi, ia juga tidak
presisi pada settlement yang bergerak di menit-menit terakhir. Tapi gerbang yang memvonis "~0"
atas dasar median saja adalah gerbang yang bisa melewatkan sebaran, dan itu dicatat di sini
alih-alih dibiarkan.

### 1d. Settlement yang hilang TIDAK acak — dan arahnya berlawanan dengan hipotesisnya

| venue | tertangkap | hilang | `|rate|` p50 tertangkap | `|rate|` p50 hilang | rasio |
|---|---:|---:|---:|---:|---:|
| binancef | 61 | 30 | 0,6160 | 0,5805 | 0,94 |
| bybit | 54 | 37 | 0,4602 | **0,3242** | **0,70** |
| okx | 66 | 25 | 0,4541 | 0,3781 | 0,83 |

Hipotesisnya: kalau yang hilang lebih ekstrem, rute B bias menurut konstruksi. Yang terukur
**sebaliknya** — yang hilang justru **kurang** ekstrem, jadi rute B bias **ke atas**. Tetap bias
menurut konstruksi; arahnya saja terbalik. Itu menjelaskan mengapa rute B bybit (0,4844) berada
di atas settled (0,3690).

## 2. OKX: **CAP API**, bukan umur kontrak — dan 3d dikoreksi

`limit=300` mengembalikan **286 baris dalam satu halaman**, `2026-05-04..2026-08-07`. Paginasi
`before=2020` maupun `after=2021` tidak menghasilkan apa pun lebih tua. Jadi 286 adalah seluruh
riwayat yang endpoint terbitkan.

| venue | kontrak listing | settled paling awal | jarak | vonis |
|---|---|---|---:|---|
| binancef | 2019-09-08 | 2019-09-10 | 2 hari | **UMUR KONTRAK** |
| bybit | 2020-03-15 | 2020-03-25 | 10 hari | **UMUR KONTRAK** |
| okx | 2019-11-12 | 2026-05-04 | **6,5 tahun** | **CAP API** |

> **Koreksi tabel 3d di `BOOK-002`.** Tabel itu mencampur keduanya di bawah satu judul
> "tahun TANPA funding terukur". Untuk `binancef` dan `bybit` angka itu bukan lubang — perp-nya
> memang belum ada. **Jendela backtest perp ADALAH 2019-09-10 ke depan**, dan itu batas
> instrumen, bukan cacat data. Hanya baris `okx` yang benar-benar lubang, dan lubangnya adalah
> keterbatasan endpoint yang mungkin bisa ditutup dari sumber lain.
>
> Ambang 10-hari yang memisahkan keduanya di skrip probe **sewenang-wenang**; yang mengikat
> adalah angka mentahnya — 2 hari, 10 hari, dan 6,5 tahun.

## 4. Sweep sensitivitas — 3.760 sel

Jalan karena item 1 menutup dengan settled sebagai sumber yang benar.

**Dua jendela, dinyatakan di setiap tabel:** sel `perp` memakai `2019-09-10` ke depan; sel `spot`
memakai seluruh 11,04 tahun. Angka dari jendela berbeda **tidak** dibandingkan tanpa label.
Notional dipatok `$100.000`, tempat impact terukur `0,0000` di kedua venue. `okx` dikecualikan —
cap API-nya menyisakan satu tahun kalender saja.

### Bug tanda di sweep versi pertama, ditemukan oleh hasil yang tidak masuk akal

Versi pertama melaporkan **`buy_and_hold` lebih murah di perp** — mustahil untuk posisi yang
ditahan 365 hari/tahun dan membayar funding penuh. Sebabnya: `costs.cost_model` mengembalikan
`bps_per_leg` sebagai **biaya** (positif = bayar) dan `bps_carry` sebagai **P&L** (negatif =
bayar). Menjumlahkannya mengurangkan funding yang dibayar long dari biayanya, bukan menambahkan.

`buy_and_hold` 2026 p50: sweep pertama mencetak **−268,70** bps/tahun; yang benar **+269,60**.

Diperbaiki di tiga tempat sekaligus, karena satu saja akan mengulanginya: konvensi ganda itu
sekarang **didokumentasikan di docstring `cost_model`**, **dikunci oleh test**
(`test_the_two_sign_conventions_are_locked_and_documented`), dan sweep-nya menegasikan carry
secara eksplisit dengan komentar yang menyebut kegagalannya.

### Kontrol

| # | kontrol | hasil |
|---|---|---|
| (i) | semua term nol → nol | `bps_per_leg 0.0 · bps_carry -0.0 · bps_tax 0.0` **PASS** |
| (ii) | buku paling seimbang → carry ≈ nol | `pairs_ou`, `|signed|/|abs|` = **0,0759**, carry +3,26 bps/thn pada funding konstan 1,0 **PASS** |
| (iii) | long 365 hari/thn harus **membayar** | carry **+269,15** bps/thn **PASS** |

Kontrol (iii) ditambahkan **setelah** bug tanda ditemukan — ia kontrol yang seharusnya ada sejak
awal, dan (i) maupun (ii) tidak bisa menangkapnya karena keduanya buta terhadap arah.

### Dekomposisi varians biaya total, per strategi (eta², menurun)

| strategi | dominan | kedua | sisanya |
|---|---|---|---|
| `buy_and_hold` | **regime 96,7 %** | instrument 0,7 % | venue 0,3 % · fee 0,0 % · order_type 0,0 % |
| `ma_trend_filter` | **regime 96,7 %** | instrument 0,7 % | venue 0,3 % · fee 0,0 % |
| `tsmom_dir` | **regime 94,7 %** | fee 2,1 % | instrument 0,7 % · venue 0,3 % |
| `tsmom` | **regime 93,9 %** | fee 2,9 % | instrument 0,7 % · venue 0,3 % |
| `tsmom_voltarget` | **regime 93,9 %** | fee 2,9 % | instrument 0,7 % · venue 0,3 % |
| `pairs_coint` | **fee 98,9 %** | regime 1,1 % | venue 0,0 % · order_type 0,0 % |
| `pairs_ou` | **fee 90,3 %** | regime 9,3 % | instrument 0,4 % |
| `tsmom_ls` | **fee 82,9 %** | regime 16,5 % | instrument 0,3 % |

Dua kelompok yang bersih: strategi yang **menahan** didominasi **rezim funding** (93–97 %),
strategi ber-**turnover** didominasi **fee** (83–99 %).

**`order_type` menjelaskan ~0,0 % di setiap strategi.** Maker menghemat half-spread, dan
half-spread itu `0,0078` bps melawan grid fee `1`–`20` bps. Pilihan maker-vs-taker tidak
menggerakkan biaya total lewat spread — kalaupun ia berarti, ia berarti lewat **tarif fee**-nya,
yang di sweep ini adalah dimensi terpisah.

## 5. Instrument mana yang lebih murah pada funding p50, per tahun

`binancef`, taker, fee 5,0. **Jendela spot = seluruh sampel; jendela perp = 2019-09-10 ke depan.**

| strategi | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|
| `buy_and_hold` | spot | spot | spot | spot | spot | spot | spot | spot |
| `ma_trend_filter` | spot | spot | spot | spot | spot | spot | spot | spot |
| `pairs_ou` | spot | spot | spot | spot | spot | spot | spot | spot |
| `tsmom` | spot | spot | spot | spot | spot | spot | spot | spot |
| `tsmom_dir` | spot | spot | spot | spot | spot | spot | spot | spot |
| `tsmom_ls` | spot | spot | spot | spot | spot | spot | spot | spot |
| `tsmom_voltarget` | spot | spot | spot | spot | spot | spot | spot | spot |
| **`pairs_coint`** | **perp** | **perp** | **perp** | spot | spot | **perp** | spot | spot |

**Dua pola berbeda.** Tujuh strategi menjawab `spot` di setiap tahun; `pairs_coint` menjawab
`perp` di 2019, 2020, 2021 dan 2024, lalu `spot` di sisanya.

> **Jawabannya berbeda antar strategi, jadi pilihan instrumen adalah keputusan PER STRATEGI,
> bukan keputusan global.** Dan untuk satu strategi ia juga berbeda antar tahun, jadi ia bukan
> keputusan sekali-untuk-selamanya.

Tidak ada rekomendasi arah di dokumen ini.
