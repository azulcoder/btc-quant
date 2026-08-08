# EXTERNAL-scalping-feasibility-001 — laporan eksternal, diaudit terhadap pengukuran repo

**Provenance.** Laporan riset eksternal ("Microstructure Crypto & Kelayakan Scalping Intraday
BTC Perpetual untuk Retail VIP 0") diserahkan azul 2026-08-08 dari berkas unduhan lokal. Ia
dihasilkan di luar repo ini, **mengutip balik banyak angka repo ini**, dan membawa klaim
literatur yang sumbernya tidak ada di mesin ini.

**Aturan baca yang mengikat, sama dengan `docs/EXTRACT-*`:** setiap klaim tentang literatur
eksternal (paper, tarif fee, program venue) adalah **`[UNVERIFIED]`** — sumbernya tidak bisa
dibaca di sini, dan laporan itu sendiri mengakui banyak angkanya dari sumber sekunder. Yang
diaudit di dokumen ini adalah (a) apakah kutipan-baliknya atas angka repo **benar**, dan
(b) di mana laporan itu **di depan** atau **di belakang** keadaan repo.

**Look:** dokumen ini sendiri nol look (audit teks lawan angka yang sudah tercatat). Dua
pengukuran pendamping yang dipicu laporan ini dicatat terpisah: sensus gerakan
(`docs/DIAG-movement-census-001.md`, +1 diagnostic) dan audit MinBTL (aritmetika murni atas
fungsi repo, bukan look).

---

## 1. Audit kutipan-balik — angka repo yang laporan itu pakai

| klaim laporan | pengukuran repo | vonis |
|---|---|---|
| half-spread median 0,0078 bps | `BOOK-001`: 2×p50 = 0,01561 bps penuh, direproduksi tiga jalur | **BENAR DIKUTIP** |
| snapshot "1 Hz" | `DIAG-book-resolution-001`: kadensi p50 1.000–1.034 ms | **BENAR DIKUTIP** |
| "92–96 % pasangan trade tak dipisah snapshot" | 94,47 / 96,41 / 92,22 % per venue | **BENAR DIKUTIP** |
| funding median 3,0 → 0,74 bps/hari (2019→2026) | `BOOK-002`: 3,0000 → 0,7374 | **BENAR DIKUTIP** |
| turnover board 0,09–34,56 leg/tahun | sensus turnover (default AST) | **BENAR DIKUTIP** |
| impact $10rb/$100rb = 0 di luar half-spread | `DIAG-cost-ledger-001` §1c | **BENAR DIKUTIP** |
| spread "tepat 1 tick >99,9 %" | 99,9024–99,9670 % per venue | **BENAR DIKUTIP** |
| "queue modeling mustahil dari snapshot 1 Hz" | `DIAG-cost-ledger-001` §2b: mustahil **pada kadensi berapa pun** — tipe datanya yang salah, bukan lajunya | **BENAR, malah understated** |

Kutipan-baliknya jujur. Yang salah bukan angkanya melainkan dua **framing**, di §2 dan §3.

## 2. KOREKSI 1 — "look counter 567 ≈ N trials" tidak mengikuti aturan repo, dan MinBTL-nya terbalik arah

Laporan memakai total look sebagai ancaman multiple-testing dan menyimpulkan "hampir dijamin
Sharpe palsu". Dua hal yang tidak akurat:

**(a)** Counter repo memisahkan look **diagnostic** (integritas tape, tanpa pemilihan) dari
**predictive/selection** (kolomnya di `docs/EDA-microstructure-001.md`, pemilik tunggalnya).
Laporan sendiri mengakui pembedaan itu di F.4-nya, tapi headline-nya memakai total.

**(b)** Dihitung dengan implementasi Bailey milik repo sendiri (`risk.min_backtest_length`,
`reports/minbtl-audit-001.json`, 2026-08-08 `[DIUKUR]`):

| N trials | MinBTL (tahun) | vs board harian 11,05 thn | vs slice 30-hari |
|---:|---:|---|---|
| 8 | 2,85 | LOLOS | GAGAL |
| 45 | 3,41 | LOLOS | GAGAL |
| 81 | 3,58 | LOLOS | GAGAL |
| **567** | **4,10** | **LOLOS** | GAGAL |

MinBTL tumbuh ~ln N dan mendatar. **Board harian lolos bahkan pada N=567**, jadi "hampir
dijamin artefak" **tidak** berlaku untuk board harian menurut alat yang laporan itu sendiri
rujuk. Sebaliknya, **slice 30-hari mendukung N = 1** — satu trial — jadi untuk riset intraday
di data collector, peringatan laporan justru **terlalu lunak**. Dua caveat yang tetap: MinBTL
adalah panduan orde-besaran (necessary, bukan sufficient), dan gerbang yang benar-benar
mengikat di repo tetap DSR net-of-cost + kill criterion, yang lebih ketat.

## 3. KOREKSI 2 — resep PBO-nya di belakang keadaan repo

Laporan menganjurkan "PBO < 0,2 idealnya". Repo sudah **mengukur** bahwa pada T=2.615 estimator
PBO-nya sendiri punya pita derau [0,13, 0,91] — ambang 0,2 **tak termeasurabel** di sana — dan
sudah menggantinya dengan uji calibrated-null yang dijalankan sebagaimana dideklarasikan
(vonis ABSTAINS, bulat 4 arm; `docs/PREREG-pbo-null-001.md`). Resep DSR/PSR/CPCV di F-nya
sudah terimplementasi lebih ketat di `btcquant/risk.py` dan `backtest.py`. Tidak ada yang
diadopsi dari bagian F selain konfirmasi arah.

## 4. Satu inkonsistensi internal laporan, dicatat

Contoh A.1-nya: target/stop simetris ±10 bps (b=1), biaya 10 bps → "harus menang mendekati
75 %". Rumusnya sendiri memberi `p* = (L+c)/(W+L) = (10+10)/(10+10) = 100 %`. Angka 75 %
miliknya berkorespondensi dengan ±20 bps, bukan ±10. Kesimpulan kualitatifnya tidak berubah —
dan versi terukurnya sudah ada di repo: `PREREG-scalp-001` menolak premis 1–30 s dengan
p* 118–222 %, **dideklarasikan dan dihitung sebelum laporan ini ada**.

## 5. Konvergensi independen — tesis inti laporan sudah menjadi vonis repo lebih dulu

Tesis negatif utamanya (edge microstructure sub-biaya pada horizon detik; taker VIP 0 tidak
viable; jalur waras = horizon lebih panjang atau maker/carry) **identik** dengan vonis yang
sudah tercatat di repo lewat rute yang sepenuhnya berbeda:

- `RESEARCH.md` (grep `Predictability is real but tiny and short-lived`): prediktabilitas
  <10 bps per 10 s vs ~10 bps biaya per trade — overlay eksekusi, bukan strategi mandiri.
- `PREREG-scalp-001`: premis 1:1 pada 1–30 s **ditolak aritmetis** di ketiga model eksekusi;
  satu reformulasi (bar 5-menit, R=3, p* 51,1 %, cap N_trials 3) **declared-not-activated** —
  yaitu persis "pivot ke horizon menengah" yang laporan rekomendasikan, sudah menunggu
  keputusan aktivasi sebelum laporan ini ditulis.
- `EDA-execution-001` §E0: maker murni di buku 1-tick **tidak viable aritmetis** di VIP 0
  (−3,98 bps/RT; break-even fee maker 0,00785 bps/sisi).

Dua rute independen yang tiba di kesimpulan yang sama adalah bentuk validasi yang tidak bisa
dibeli. Yang **baru** dari laporan: peta venue rebate (UNVERIFIED, keputusan bisnis), dan
penekanan bahwa jalur maker menuntut L2 diff — yang repo jawab hari ini dengan perekamnya.

## 6. Program laporan, dipetakan ke keadaan repo (2026-08-08)

| rekomendasi laporan | status di repo |
|---|---|
| Tahap 0: audit look counter + MinBTL | **SELESAI HARI INI** — §2 di atas |
| Tahap 0: distribusi gerakan vs hurdle dari data sendiri | **SELESAI HARI INI** — `DIAG-movement-census-001` (laporan sendiri menyebut angkanya asumsi) |
| markout / adverse selection dari data existing | **kandidat PREREG bernama**: `PREREG-markout-001` — belum ditulis; menyentuh E[Δp | sisi trade], jadi look prediktif dengan cap N_trials |
| uji prediktif OBI/OFI net-of-cost | **kandidat PREREG bernama**: `PREREG-obi-predictive-001`; literatur yang laporan kutip memprediksi hasil sub-biaya, dan vonis RESEARCH.md sudah searah |
| backtest funding/basis carry ber-biaya | **kandidat PREREG bernama**: `PREREG-funding-carry-001` — menyentuh return, wajib deklarasi penuh + LockBox-aware. Aritmetika kasar dari data settled: median 2026 0,7374 bps/hari ≈ 2,7 %/thn tanpa leverage — jauh di bawah klaim "8–20 % APY" yang laporan sendiri tandai sebagai marketing |
| rekam L2 diff (gap terbesar) | **DIBANGUN HARI INI** — `scripts/record_depth_diffs.py`, smoke live 45 s: ~8 frame/s, rantai utuh, ~164 MB/hari terkompresi `[DIUKUR]`; deploy `deploy/gcp` Phase 2b |
| pindah venue rebate | **UNVERIFIED + keputusan bisnis** — tarif dari sumber sekunder; bukan keputusan sesi |
| pivot 15 menit–4 jam | **sudah ada sebagai reformulasi PREREG-scalp yang declared-not-activated** — aktivasinya keputusan azul, dan MinBTL slice (N=1) berarti evaluasinya harus di data harian/multi-bulan, bukan slice 30-hari |

## 7. Yang repo TOLAK dari laporan

- **Ambang PBO 0,2** — tak termeasurabel pada T repo; digantikan calibrated-null (§3).
- **"567 ≈ N"** — melanggar pembedaan kolom counter milik repo (§2).
- **Angka fee/venue sebagai fakta** — `[UNVERIFIED]`, dan laporan sendiri mendaftar caveat-nya.
- **RMS per horizon dari "volatilitas tipikal"** — digantikan pengukuran (`DIAG-movement-census-001`).
- **Studi day-trader (Chague, Barber) sebagai bukti** — `[UNVERIFIED]` di mesin ini; secara
  arah konsisten, tapi tidak satu pun keputusan repo bersandar padanya.
