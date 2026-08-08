# BOOK-002 — riwayat funding settled, dan kontrol positif yang GAGAL

**Look: diagnostik provenance.** Tidak ada return, P&L, Sharpe, atau equity curve yang dihitung,
dibaca, atau ditampilkan. Sisi biaya saja.
**Pajak sengaja dikeluarkan** — ia lapisan yurisdiksi, bukan properti sistem. Parameternya ada di
tanda tangan `costs.cost_model` dengan nilai 0 supaya tidak hilang diam-diam; tidak ada angka
pajak di dokumen ini.
**Skrip:** `scripts/backfill_funding_history.py` · **mesin:** `reports/funding-history-001.json`,
`data/funding_history/*.jsonl` (append-only) · **Semua angka `[DIUKUR]`**, 2026-08-08.

> ## KONTROL POSITIF GAGAL — item 5 TIDAK DIKERJAKAN
>
> Sweep sensitivitas bergantung pada rezim funding dari dokumen ini. Kontrolnya gagal untuk satu
> venue, jadi membangun sweep di atasnya berarti membangun di atas angka yang salah untuk
> setidaknya satu sel. Berhenti di §3c sebagaimana diperintahkan.

---

## 3a. Backfill — sumber, dan mengapa ia tabel terpisah

`funding_mark` berisi rate **prediktif** untuk settlement yang belum terjadi, dicuplik tiap poll
(dikutip di `DIAG-funding-and-turnover-001` §1a). Ini besaran yang berbeda dari yang dibayar.
Riwayat **settled** ditarik dari REST publik tiap venue — keyless, sesuai rail — ke
`data/funding_history/{venue}.jsonl`, **append-only**, tidak pernah ditulis ulang, dan **tidak
dicampur** dengan `funding_mark`.

| venue | endpoint | settlement | paling awal |
|---|---|---:|---|
| binancef | `/fapi/v1/fundingRate` | **7.571** | 2019-09-10 |
| bybit | `/v5/market/funding/history` | **6.979** | 2020-03-25 |
| okx | `/api/v5/public/funding-rate-history` | **286** | 2026-05-04 |

**Dua bug pagination milikku, dicatat karena keduanya senyap.** Versi pertama memaginasi
`binancef` **maju** dari `startTime=0` dan mendapat **500 baris terbaru**, bukan tertua —
`startTime=0` tidak berarti "dari awal" di endpoint itu — lalu berhenti karena halamannya pendek.
Dan kursor berikutnya dihitung dari `rows` **sebelum** dicek kosong, jadi bybit dan okx mati di
`max()`/`min()` atas urutan kosong. Ketiganya sekarang memaginasi **mundur** dengan guard
halaman-kosong.

## 3b. Per tahun kalender — rezimnya memang bergeser

`binancef`, bps per settlement 8 jam:

| tahun | n | p05 | p50 | p95 | maks | min | % neg | bps/hari |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2019 | 338 | −1,5188 | 1,0000 | 1,4176 | 7,8363 | −5,2458 | 18,3 % | 3,0000 |
| 2020 | 1.098 | −1,2963 | 1,0000 | 6,8280 | **30,0000** | **−30,0000** | 14,3 % | 3,0000 |
| 2021 | 1.095 | −0,5200 | 1,0000 | 10,9850 | 24,8993 | −8,9697 | 7,3 % | 3,0000 |
| 2022 | 1.095 | −0,7789 | 0,5135 | 1,0000 | 1,0000 | −11,9172 | 22,1 % | 1,5405 |
| 2023 | 1.095 | −0,1595 | 0,8265 | 1,4673 | 5,5170 | −1,1006 | 10,1 % | 2,4795 |
| 2024 | 1.098 | −0,1603 | 1,0000 | 3,4429 | 8,8148 | −1,1235 | 8,4 % | 3,0000 |
| 2025 | 1.095 | −0,2417 | 0,4826 | 1,0000 | 1,0000 | −1,2232 | 12,9 % | 1,4478 |
| 2026 | 657 | −0,6771 | **0,2458** | 0,9492 | 1,0000 | −1,5178 | **31,7 %** | **0,7374** |

`bybit`:

| tahun | n | p05 | p50 | p95 | maks | min | % neg | bps/hari |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2020 | 844 | −2,2305 | 1,0000 | 9,3337 | 28,2927 | **−37,5000** | 10,3 % | 3,0000 |
| 2021 | 1.095 | −1,6343 | 1,0000 | 15,7412 | **37,5000** | −21,4698 | 9,1 % | 3,0000 |
| 2022 | 1.095 | −1,7315 | 0,8221 | 1,0000 | 3,6890 | −9,0728 | 27,7 % | 2,4663 |
| 2023 | 1.095 | −0,1869 | 1,0000 | 1,8851 | 5,9775 | −4,5309 | 9,3 % | 3,0000 |
| 2024 | 1.098 | −0,2975 | 1,0000 | 3,9369 | 11,2788 | −1,5398 | 10,6 % | 3,0000 |
| 2025 | 1.095 | −0,3308 | 0,5122 | 1,0000 | 5,1390 | −2,9357 | 18,2 % | 1,5366 |
| 2026 | 657 | −0,5872 | **0,1642** | 0,9842 | 1,0000 | −1,6192 | **35,6 %** | **0,4926** |

`okx`: satu tahun saja, 2026 — p50 0,3204 · p95 1,0000 · % neg 22,0 % · 0,9612 bps/hari.

**Rezimnya bergeser, dan jauh.** `p50` binancef bergerak 1,0000 → 0,2458 bps antara 2019 dan
2026, `bps/hari` dari 3,0000 ke 0,7374 — **faktor 4**. Fraksi negatif naik dari 7,3 % (2021) ke
31,7 % (2026). Dan ekornya berpindah kelas: `maks` 2020–2021 mencapai **30,0000 dan 37,5000 bps
per settlement**, sementara sejak 2025 tidak satu pun melewati 1,0000.

Angka 1,8441 bps/hari yang dipakai neraca sebelumnya diestimasi dari **30 hari** di 2026. Tahun
2026 penuh memberi **0,7374**. Jendela 30 hari itu bukan tahunnya.

## 3c. KONTROL POSITIF — **GAGAL**

Jendela beku `2026-07-05..2026-08-03`, settled vs rute B `DIAG-cost-ledger-001`:

| venue | n settled | settled p50 | rute B p50 | selisih | rel |
|---|---:|---:|---:|---:|---:|
| binancef | 90 | 0,6059 | 0,6226 | −0,0167 | −2,7 % |
| bybit | 91 | **0,3690** | **0,4844** | **−0,1154** | **−23,8 %** |
| okx | 91 | 0,4221 | 0,4542 | −0,0321 | −7,1 % |

**`bybit` gagal** pada ambang 0,05 bps. `binancef` dan `okx` lolos.

Satu asimetri terukur yang relevan dan **tidak** kuadopsi sebagai penjelasan: settled punya
**90–91** settlement di jendela itu — jumlah yang benar untuk 30 hari × 3 — sementara rute B
hanya punya **61 / 54 / 66** sampel, karena ia mensyaratkan cuplikan dalam ±5 menit dari jam
settlement dan collector tidak selalu hidup di sana. Rute B bybit karena itu adalah **54 dari 91**
settlement, dan subsetnya dipilih oleh uptime collector, bukan acak.

Mana yang salah tidak diputuskan di sini. Riwayat settled adalah apa yang venue benar-benar
membebankan; rute B adalah proksi dari rate prediktif. Keduanya berselisih, dan instruksinya
berhenti.

## 3d. Batas cakupan untuk board 2015-07-20 .. 2026-08-04 (11,04 tahun)

| venue | biaya perp terukur mulai | tahun TANPA funding terukur |
|---|---|---:|
| binancef | 2019-09-10 | **4,14** |
| bybit | 2020-03-25 | **4,68** |
| okx | 2026-05-04 | **10,79** |

**Tidak diisi, tidak diekstrapolasi.** Sepertiga awal board tidak punya biaya perp terukur sama
sekali, dan tidak bisa punya — perp-nya belum ada.

## 4. `btcquant/costs.py` — selesai, dan independen dari kegagalan di atas

Fungsi, bukan konstanta. Tanda tangannya persis seperti dideklarasikan, dan `fee_bps_per_side`
serta `funding_bps_per_day` **wajib** — keduanya `raise` kalau kosong. Modul ini karena itu tidak
terpengaruh oleh §3c: ia tidak menanam angka funding, ia menuntut pemanggil menyediakannya.

Aturan yang dikunci test (`tests/test_costs.py`, 12 lulus):

- **Kontrol positif**: parameter lama merekonstruksi `10,0078` bps/leg, dan selisihnya terhadap
  konstanta 12,0 adalah `1,9922` — yaitu term "slippage 2,0" yang modul ini **ukur** sebesar
  `0,0078`.
- **Kontrol negatif**: fee tanpa nilai `raise`; funding perp tanpa nilai `raise`; funding yang
  diberikan ke `spot` **`raise`** alih-alih diabaikan diam-diam — karena diabaikan diam-diam
  adalah persis bagaimana sensus turnover membebankan funding perp ke backtest spot dan tidak
  ada yang menyadarinya sampai angkanya terbit.
- **Test struktural** yang gagal kalau default menyelinap kembali ke salah satu parameter.
  `tax_bps_per_sale=0` adalah satu-satunya default yang diizinkan, dan diizinkan karena nol bukan
  estimasi — ia parameter yang ditahan terbuka.
- **Funding bertanda**: long membayar `−18,441` bps pada 10 hari di 1,8441 bps/hari; short
  **menerima** `+18,441`. Rate negatif membalik keduanya lewat aturan yang sama, bukan lewat
  kasus khusus.
- `coinbase` half-spread dan impact **`UNAVAILABLE`**, bukan dipinjam dari binancef.
- Notional di atas grid terukur **ditolak**, tidak diekstrapolasi.
- Test terakhir gagal kalau `costs` pernah tersambung ke `backtest.py` — sambungan itu keputusan
  tersendiri.

**Cacat yang diperbaiki dan dicatat:** `scripts/diag_turnover_census_001.py` membebankan funding
sebagai biaya murni ke long dan short sama saja. Itu menghitung ganda terhadap setiap leg short.
`costs.cost_model` menandainya dengan `position_sign`.

---

# ANOTASI 2026-08-08 — §3c DITUTUP, dan §3d DIKOREKSI

**§3c.** Uji berpasangan pada slot settlement yang sama menutupnya: `|d|` p50 adalah
`0,0000 / 0,0034 / 0,0023` bps. Diskrepansi itu **SELEKSI**, bukan ketidaksepakatan nilai.
**Settled adalah sumber yang benar; rute B pensiun sebagai estimator funding.** Detail, termasuk
ekor bybit yang **tidak** ~0 (`p95 = 0,7697`), ada di `docs/BOOK-003-cost-sweep.md` §1.

**§3d.** Tabel "tahun TANPA funding terukur" mencampur dua hal berbeda. Untuk `binancef`
(kontrak listing 2019-09-08, settled paling awal 2019-09-10) dan `bybit` (2020-03-15 vs
2020-03-25) itu **umur kontrak**, bukan lubang — jendela backtest perp memang mulai di sana.
Hanya `okx` yang lubang: kontraknya listing 2019-11-12 tapi endpoint-nya hanya menerbitkan 286
record, dan `limit=300` mengembalikan semuanya dalam satu halaman. Itu **CAP API**.
