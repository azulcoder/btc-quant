# DIAG-book-resolution-001 — apa yang bisa dan tidak bisa diselesaikan buku 1-per-detik

**Look: diagnostik provenance.** Tidak ada estimator yang diimpor, tidak ada Roll yang
dijalankan, tidak ada yang dibangun.
**Skrip:** `scripts/diag_book_resolution_001.py` · **mesin:** `reports/book-resolution-001.json`
**Semua angka `[DIUKUR]`**, dijalankan 2026-08-07, slice beku `2026-07-05..2026-08-03`.
Subset ASOF terdeklarasi: `2026-07-30, -31, 2026-08-01, 2026-08-03` — sama dengan BOOK-001.

Satu angka di seluruh dokumen ini `[DIASUMSIKAN]`: konversi tick→bps memakai mid rujukan yang
dipatok **64.000**, sementara BOOK-001 mengukur 64.069,88 / 64.023,30 / 64.022,05. Selisihnya
≤ 0,11 % dan tidak mengubah rasio mana pun, karena rasio membagi konversi yang sama.

---

## 1. Staleness ASOF — angka 0,02460 bps **TIDAK STABIL**

Staleness = `ts_trade − ts_snapshot` untuk snapshot terakhir pada-atau-sebelum tiap trade.

| venue | n trade | p50 | p90 | p99 | maks |
|---|---:|---:|---:|---:|---:|
| **binancef/BTCUSDT** | 3.340.826 | 923 ms | **3.719.531 ms** | **9.872.211 ms** | **14.275.826 ms** |
| bybit/BTCUSDT | 2.496.336 | 508 ms | 908 ms | 997 ms | 1.686 ms |
| okx/BTC-USDT-SWAP | 1.524.719 | 486 ms | 899 ms | 990 ms | 14.146.473 ms |

**Pada `binancef`, p90 adalah 62 menit dan p99 adalah 2,7 jam.** Lebih dari sepuluh persen trade
binancef dicocokkan ke snapshot yang sudah basi lebih dari satu jam. `bybit` dan `okx` normal di
p50–p99 (≈0,5–1 detik) tapi `okx` punya ekor maksimum 3,9 jam.

`√(E[c²])` tertimbang trade saat batas staleness diketatkan:

| venue | batas | trade tersisa | % tersisa | rms (tick) | rms (bps) | vs tanpa batas |
|---|---|---:|---:|---:|---:|---:|
| binancef | tanpa batas | 3.340.826 | 100,00 % | 1,5762 | **0,02463** | 1,000× |
| binancef | ≤ 100 ms | 185.305 | **5,55 %** | 3,6155 | **0,05649** | **2,294×** |
| binancef | ≤ 10 ms | 20.989 | **0,63 %** | 3,6203 | 0,05657 | 2,297× |
| bybit | tanpa batas | 2.496.336 | 100,00 % | 0,6620 | 0,01034 | 1,000× |
| bybit | ≤ 100 ms | 262.209 | 10,50 % | 1,2258 | 0,01915 | 1,852× |
| bybit | ≤ 10 ms | 31.430 | 1,26 % | 2,7123 | 0,04238 | **4,097×** |
| okx | tanpa batas | 1.524.719 | 100,00 % | 0,7377 | 0,01153 | 1,000× |
| okx | ≤ 100 ms | 160.187 | 10,51 % | 1,3497 | 0,02109 | 1,830× |
| okx | ≤ 10 ms | 17.410 | 1,14 % | 3,5829 | 0,05598 | **4,857×** |

> **Angka 0,02460 bps tidak stabil.** Membatasi staleness ke 100 ms menggandakannya menjadi
> 0,05649 bps — **2,294×** — sambil membuang 94,45 % trade. Ketiga venue bergerak ke arah yang
> sama dengan besaran 1,8×–4,9×.

Arah pergerakannya konsisten dan bisa dinyatakan tanpa menafsirkan: **semakin segar snapshot-nya,
semakin besar `√(E[c²])`-nya.** Snapshot basi membawa nilai `c` dari saat ia diambil, dan §3 di
bawah menunjukkan nilai itu hampir selalu 0,5 tick; mencocokkan trade ke snapshot berjam-jam
mengimpor konstanta itu.

## 3. Seberapa degenerate distribusinya

| venue | snapshot | `c` tepat 0,5 tick | fraksi |
|---|---:|---:|---:|
| binancef/BTCUSDT | 1.442.013 | 1.440.606 | **99,9024 %** |
| bybit/BTCUSDT | 1.036.632 | 1.035.605 | **99,9009 %** |
| okx/BTC-USDT-SWAP | 1.162.817 | 1.162.433 | **99,9670 %** |

Ketiganya di atas 99 % (minimum 99,9009 %).

> **Setiap kuantil buku adalah statistik konstan dan tidak bisa membedakan rezim apa pun, jam apa
> pun, atau hari apa pun.** Hasil invariansi jam dan `SE = 0` pada p50 di BOOK-001 adalah
> **konsekuensi** dari ini, **bukan temuan independen.** Keduanya tidak boleh dikutip sebagai
> bukti tentang pasar; keduanya bukti tentang sebuah besaran yang bernilai 0,5 tick di 999 dari
> setiap 1.000 snapshot.

Yang tersisa sebagai statistik yang bisa bervariasi hanyalah momen kedua — dan §1 baru saja
menunjukkan momen kedua itu bergerak 2,3× tergantung batas staleness.

## 4. Kelayakan P4 — kadensi, dan interval trade yang tak teramati

| venue | n jeda | p50 | p90 | p99 | maks | > 5 detik |
|---|---:|---:|---:|---:|---:|---:|
| binancef/BTCUSDT | 1.441.983 | 1.034 ms | 1.087 ms | 1.215 ms | 54.015.519 ms | 0,050 % |
| bybit/BTCUSDT | 1.036.604 | 1.001 ms | 1.060 ms | 1.240 ms | 53.983.820 ms | 0,250 % |
| okx/BTC-USDT-SWAP | 1.162.789 | 1.000 ms | 1.000 ms | 1.000 ms | 53.976.596 ms | 0,046 % |

Maksimum ~5,4×10⁷ ms = **~15 jam** di ketiganya — lubang feed, bukan kadensi.

**Fraksi pasangan trade berturut-turut TANPA snapshot di antaranya.** Definisi: untuk trade
`t_i < t_{i+1}`, pasangan dihitung bila **tidak ada** timestamp snapshot yang jatuh di
`(t_i, t_{i+1}]`. Kedua trade itu tidak bisa dibedakan oleh buku.

| venue | pasangan | tanpa snapshot di antara | fraksi |
|---|---:|---:|---:|
| binancef/BTCUSDT | 3.340.882 | 3.156.190 | **94,4718 %** |
| bybit/BTCUSDT | 2.496.447 | 2.406.888 | **96,4125 %** |
| okx/BTC-USDT-SWAP | 1.524.715 | 1.406.093 | **92,2201 %** |

> Pada kadensi ini, **92 %–96 % pasangan trade berturut-turut tidak terpisahkan oleh satu pun
> snapshot.** Itu batas atas terukur untuk berapa banyak siklus hidup order yang tidak teramati:
> apa pun yang terjadi pada buku di antara dua trade itu — order masuk, batal, terisi sebagian —
> tidak punya jejak di data ini.

Angka itu dilaporkan sebagai batas, bukan sebagai vonis atas P4. **Tidak ada yang dibangun di
sini.**
