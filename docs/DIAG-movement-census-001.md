# DIAG-movement-census-001 — |gerakan| terealisasi per horizon, lawan hurdle fee

**Look: diagnostik provenance** (+1 kolom diagnostic). Sensus properti tape — tanpa sinyal,
tanpa pemilihan, tanpa vonis viabilitas: ambang di tabel adalah konstanta fee terpublikasi yang
dideklarasikan sebelum run, dan GO/NO-GO apa pun di atasnya milik sebuah PREREG.
**Skrip:** `scripts/diag_movement_census_001.py` · **mesin:** `reports/movement-census-001.json`
**Semua angka `[DIUKUR]`**, 2026-08-08. Slice beku, `binancef`/BTCUSDT, hari rusak dikecualikan
dan dihitung; tiga hari kosong binancef (2026-07-23/24/25, lubang feed yang sudah tercatat)
dilewati dan dihitung.

## Tabel

| τ | n | skipped | p50 | p90 | p99† | RMS | RMS/√τ | >4 bps | >7 bps | >10 bps | >20 bps |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 s | 1.770.160 | 109.205 | 0,015 | 0,675 | 2,210 | 0,530 | 0,5299 | 0,16 % | 0,03 % | 0,01 % | 0,00 % |
| 10 s | 186.307 | 1.612 | 0,569 | 2,716 | 6,574 | 1,848 | 0,5844 | 4,21 % | 0,80 % | 0,23 % | 0,03 % |
| 60 s | 31.123 | 180 | 2,033 | 6,846 | 15,622 | 4,648 | 0,6001 | 25,14 % | 9,57 % | 4,06 % | 0,44 % |
| 5 m | 6.208 | 36 | 4,577 | 15,202 | 34,499 | 10,040 | 0,5796 | 54,69 % | 34,21 % | 21,50 % | 5,17 % |
| 15 m | 2.054 | 13 | 7,937 | 25,370 | 62,888 | 17,007 | 0,5669 | 73,08 % | 54,63 % | 40,94 % | 15,24 % |
| 1 h | 497 | 5 | 17,172 | 50,700 | 115,310 | 34,782 | 0,5797 | 84,51 % | 75,25 % | 66,80 % | 44,27 % |
| 4 h | 110 | 0 | 33,936 | 100,096 | 173,527† | 66,864 | 0,5572 | 93,64 % | 87,27 % | 80,91 % | 65,45 % |

Satuan bps dari log-harga. Ambang: 4 = maker RT, 7 = maker-in/taker-out, 10 = taker RT, 20 = 2×.

† **p99 pada n = 497 dan n = 110 adalah order statistic, bukan estimasi ekor** — pada n = 110 ia
praktis nilai terbesar kedua. Temuan refutasi; jangan dikutip sebagai kuantil populasi.

## Tiga bacaan, tanpa vonis

**1. Difusi hampir murni.** `RMS/√τ` konstan 0,53–0,60 melintasi empat orde besaran waktu.
Skala yang laporan eksternal **asumsikan**, sekarang terukur — dan laporan itu sedikit
*understate*: RMS 60 s terukur 4,65 bps (asumsinya 2,5–4), 1 jam 34,8 (asumsinya 20–30).

**2. Median menyeberangi hurdle taker antara 15 menit dan 1 jam** (p50: 7,94 bps di 15 m →
17,17 di 1 h). Ini **konvergen dengan pengukuran repo yang sudah ada** —
`EDA-microstructure-001.md` (grep `first exceeds the 10.02 bps taker round-trip`) menaruh
persilangan median di ~30 menit dari rute berbeda. Dua instrumen, satu jawaban.

**3. Pada horizon detik, gerakannya sendiri lebih kecil dari biayanya.** p50 pada 1 s adalah
**0,015 bps** — dua orde besaran di bawah half-spread pun. Bahkan p99 pada 10 s (6,57 bps)
belum menyentuh RT taker. Ini pengukuran, bukan vonis; vonis aritmetisnya sudah lama ada di
`PREREG-scalp-001`.

## Keterbatasan, dari refutasi adversarial

- **Anchor `arange(t0, t1−τ, τ)` membuang jendela valid terakhir tiap hari** — pada τ = 4 h itu
  1 dari 6 anchor, dan karena anchornya berpola dari awal-hari, jendela 20:00–24:00 UTC
  sistematis kurang terwakili di baris 4 h. Tercatat, tidak ditambal — menambalnya mengubah n
  dan itu spesifikasi baru.
- **`arg_max(price, ts_ms)` pada tie milidetik bergantung urutan fisik baris** (class-H,
  terukur di DuckDB). Efeknya terbatas pada rentang harga intra-milidetik (~1–2 tick);
  tidak dijalankan ulang untuk ini.
- Jalur harga-salah pada anchor pra-trade **terbukti unreachable** dua lapis, dengan kontrol
  positif; log-vs-simple-return dan jendela ber-lubang-interior adalah keputusan desain yang
  dipertahankan dengan alasan terukur (selisih ≤ 0,10 % pada ambang; endpoint mendefinisikan
  measurand).
