# DIAG-orderflow-terminal-census-001 — sensus terminal orderflow, read-only

**Tanggal: 2026-08-08.** Branch `orderflow-terminal`. Read-only: nol berkas produksi diubah,
nol server dijalankan, nol angka prediktif dihitung. Lima agen pembaca paralel, tiap klaim
membawa perintah dan keluaran mentahnya.

## 0. Temuan yang mengubah cara membaca sisanya

**Ada DUA layer orderflow di repo ini, dan keduanya tidak saling tersambung.**

| | layer JS terminal | layer riset Python |
|---|---|---|
| berkas | `dashboard/terminal*.js` (21.538 baris) | `btcquant/orderflow.py` (3.397 baris) |
| metrik | footprint, CVD, volume profile/VPOC, absorption, stacked imbalance, iceberg heuristic, book heatmap — **hampir semuanya ada** | **53 kolom**, dan **nol** di antaranya footprint per-level, VPOC, absorption, stacking, heatmap, iceberg, block, size-distribution, run-length |
| status | LIVE-DESCRIPTIVE per rail §0.1; ring terbatas (footprint 120 bar, heatmap ~1 jam); **tidak dipersistensi sama sekali** | memberi makan `walk_forward` / DSR / PBO |
| bisa diuji harness? | **tidak** | ya |

Jadi kalimat "metrik X belum ada" harus selalu dibaca: *belum ada di layer yang bisa diuji*.
Delapan dari sembilan metrik yang hilang **sudah punya kode referensi di JS** — yang tidak ada
adalah jembatannya ke Python.

**Temuan negatif kedua, sama pentingnya:** `order_flow_bars()` **bukan bagian dari jalur data
terminal**. `grep -rn "order_flow_bars\|orderflow.py" dashboard/` keluar kosong (exit 1)
[DIUKUR]. Halaman browser tidak punya Python di jalurnya sama sekali.

## 1. Berkas (item 1a)

44 berkas, **73.930 baris** total [DIUKUR: `cat <44 files> | wc -l`]. Tidak ada direktori
`terminal/` — `find . -type d -name '*terminal*'` kosong.

| kelompok | berkas | baris | terbesar |
|---|---|---|---|
| modul Python | 3 | 7.549 | `collector.py` 4.071 · `orderflow.py` 3.397 |
| dashboard JS/HTML/CSS | 12 | 25.354 | `terminal-views.js` 5.964 · `terminal.js` 5.176 · `terminal-state.js` 4.369 |
| skrip/CLI | 15 | 13.194 | `check_terminal.cjs` 5.125 |
| fixture | 2 | 17.206 | `fixtures_ws.json` 17.121 |
| test | 8 | 7.792 | `test_collector.py` 3.102 |
| dokumen desain | 4 | 2.835 | |

Peran, dari docstring masing-masing (dikutip, bukan diringkas dari ingatan):

- `btcquant/orderflow.py` — *"event-time order-flow bars from the recorded tick store … the research-side keystone"*. Ubah terakhir 2026-08-02.
- `btcquant/collector.py` — *"tick collector daemon … Keyless, research-only accumulation of BTC perp microstructure into a local DuckDB store"*. Ubah terakhir 2026-08-08.
- `dashboard/terminal.html` — *"btc-quant orderflow terminal — live descriptive only"*; banner kejujuran ditulis sebagai HTML statis *"on purpose — the banner exists even if every script fails."*
- `dashboard/terminal.js` — bootstrap; *"adapters → sink() → stores → rAF loop → views"*.
- `dashboard/terminal-views.js` — renderer canvas/DOM; *"a view fed the same slice twice must draw the same pixels."*
- `dashboard/terminal-state.js` — *"pure in-memory stores + structure builders"*.

**Celah test yang struktural:** `ls tests/ | grep -Ei '\.(js|cjs|mjs)$'` keluar dengan exit 1
— **tidak ada satu pun test JS di `tests/`**, sehingga 10 berkas terminal (21.538 baris, ~29 %
basis kode) punya **nol** cakupan pytest. Satu-satunya pemeriksa otomatisnya
`scripts/check_terminal.cjs` (di CI) plus dua harness non-CI.

## 2. Aliran data (item 1b) — lima rantai, bukan satu

**Rantai A — live, dominan, TANPA Python dan TANPA server.**
`terminal.html` disajikan `python3 -m http.server --directory dashboard`, lalu JS-nya membuka
WebSocket **langsung ke sembilan endpoint venue**:
`makeSocket(adapter, api)` (`livewire.js`) → `sink(ev)` (`terminal.js`) → store in-memory
(`FootprintStore`, `CvdStore`, `AggBookStore`, `VpinStore` di `terminal-state.js`) → satu loop
rAF `frame()` (`terminal.js`) → view. **Seluruh agregasinya loop JS di klien.**

**Rantai B — satu-satunya tempat DuckDB melayani browser.**
`ThreadingHTTPServer` stdlib di dalam `collector.py`, `127.0.0.1:8788`, melayani
`/v1/profile`, `/v1/vwap`, `/v1/levels`, `/v1/trades` dari `data/ticks/YYYY-MM-DD.duckdb`.
Agregasi profile adalah `GROUP BY` DuckDB sungguhan yang dibangun `_profile_sql()`, dengan
dedupe `GROUP BY trade_id` di subquery, lalu digabung per hari **oleh loop dict Python** di
`_profile_endpoint`. Terukur hidup: `GET /health` → HTTP 200.

**Rantai C — browser menembus langsung ke Hugging Face.**
Tab mengunduh `…/resolve/main/data/date=<DATE>/trades.parquet`, mem-parsing dengan
`hyparquet.js` **di dalam tab**, lalu mengagregasi di `aggregateTradeRows()` — loop `for…of`
JS, dengan ketidaksepakatan aturan-snap `Math.floor` (klien) vs `round()` (server) yang
didokumentasikan sendiri.

**Rantai E — jalur riset, terpisah total dari halaman.**
`order_flow_bars()` → `_open_source()` (ATTACH READ_ONLY / `read_parquet('hf://…')` /
`read_parquet` atas `data/vision/`) → `_trade_bars` / `_book_bars` / `_liq_bars` (SQL DuckDB)
→ `_assemble()` (numpy/pandas) → cache `data/orderflow/<spec_hash>/<range>.parquet`.
Pemanggilnya **hanya** `scripts/orderflow_smoke.py` dan `tests/test_orderflow.py`.

## 3. Metrik yang ADA, dan status verifikasinya (item 1c)

`orderflow.py` memancarkan **53 template kolom** [DIUKUR dari `PROVENANCE` dict-nya sendiri],
semuanya dihitung di SQL DuckDB dalam empat fungsi, lalu di-mask sekali di `_assemble`.
`tests/test_orderflow.py` = 72 test, **72 lulus** [DIUKUR: `72 passed in 94.74s`].

**Punya kontrol positif sungguhan (7 famili)** — masing-masing punya fixture dengan
aritmetiknya ditulis eksplisit, atau rute independen kedua (loop Python naif, bentuk aljabar
kedua, `np.linalg.lstsq`, array okupansi 86.400 slot): signed delta/CVD · size buckets · OFI ·
microprice/book-imbalance · depth slope · VPIN · coverage.

**Tidak punya test sama sekali (4 kolom)** [DIUKUR: `grep -rn` per nama → exit 1]:
`spread_bps_{b}` · `sell_volume_{v}` · `depth_ask_{b}` · `ofi_gap_pairs_{b}`.

**Hanya disentuh assertion struktural/NaN — bukan kontrol positif:** `mid`, `spread`,
`depth_bid`, `depth_levels`, `book_snapshots`, `trade_count`, `dollar_volume`, `vwap`,
semantik NaN `liq_*`, `vpin_age_s`. **`open`/`high` tidak punya kontrol nilai di mana pun**;
`close`/`low` hanya dipatok pada fixture harga-konstan di mana setiap print = 100,0 — fixture
yang **tidak bisa** membedakan first/last/min/max.

**Verifikasi terhadap sumber independen:** `RESEARCH-orderflow-runlog.md` §3.1 mencatat
rekomputasi independen atas arsip nyata untuk tujuh fitur dengan toleransi sampai 5,684e-13 —
tetapi **tidak ada skrip di repo yang mereproduksi angka-angka itu** [DIUKUR: grep atas
`scripts/` dan `Makefile` → kosong], dan tidak ada nama metrik orderflow yang muncul di
`reports/`. Jadi verifikasi itu **tidak bisa dijalankan ulang** hari ini.

## 4. Metrik yang BELUM ada, dan apakah datanya sudah kita punya (item 1d)

Sumbu prioritas satu-satunya: **HAVE-DATA vs NEED-DATA.**

| metrik | ada di JS? | ada di Python (bisa diuji)? | data | sumber |
|---|---|---|---|---|
| footprint bar (vol bid/ask per level per bar) | ya | **tidak** | **HAVE** | `vision/` aggTrades, 2.111 hari |
| CVD | ya | **ada** (signed delta/CVD, terkontrol) | HAVE | idem |
| delta divergence | sebagian | **tidak** | **HAVE** | idem |
| volume profile / VPOC | ya | **tidak** | **HAVE** | idem |
| absorption (definisi trade) | ya | **tidak** | **HAVE** | idem |
| imbalance stacking | ya | **tidak** | **HAVE** | idem |
| block trade detection | ya (tier ukuran) | **tidak** | **HAVE** | idem |
| trade size distribution | **tidak** (hanya taksonomi 4-bin `[1e4,1e5,1e6]` USD, tanpa count/kuantil/momen) | **tidak** | **HAVE** | idem |
| aggressor run-length | **tidak** (grep bersih, exit 1) | **tidak** | **HAVE** | idem |
| liquidity heatmap historis | ya (live, ring ~1 jam) | **tidak** | **NEED** | tape L2 diff — **baru 1 hari** |
| iceberg detection | heuristik | **tidak** | **NEED** | idem |
| absorption (definisi book) | ya | **tidak** | **NEED** | idem |

**Enam dari sembilan yang hilang adalah HAVE-DATA dan bisa dibangun hari ini** dari arsip
aggTrades di HF prefix `vision/` — skema `(exchange, symbol, trade_id, ts_ms, price, qty,
aggressor_buy)` adalah persis trades + sisi agresor yang mereka butuhkan.

**Kontras paling tajam:** registry volume-profile yang benar-benar ada di disk hanya
**32 hari** (`data/ticks/levels.jsonl`, bybit, tick $10, volume total tanpa split buy/sell),
karena `scripts/backfill_levels.py` membaca **hanya** prefix `data/` dan tidak pernah
menyentuh `vision/`. **2.079 hari bahan bakunya menganggur.**

## 5. Utang teknis yang terlihat (item 1e)

Semua [DIUKUR] dengan perintah mekanis:

- **34 fungsi ≥100 baris** (47 kalau nested dihitung). Terbesar: `FootprintView` **645 baris**
  dengan satu `draw()` **382 baris**.
- **Duplikasi**: ekspansi value-area 70 % ada dalam **empat salinan tangan independen** — dua
  di `terminal-state.js` sendiri (`valueArea70` sudah diekstrak untuk `buildTpo`/`buildKlineVp`
  tapi `ProfileStore.profile()` tak pernah dimigrasikan), plus `pocVa70` di `terminal.js` dan
  `_poc_va` di `collector.py`. Idiom abortable-fetch ada dalam **lima salinan dengan tiga
  timeout berbeda** (8 s / 10 s / 120 s). `makeRing`/`finiteOr`/`posOr` dicerminkan verbatim
  antar dua berkas *by policy*. Default tier ukuran `{sig:1e5, large:2.5e5, huge:1e6,
  whale:5e6}` duplikat literal.
- **Jalur tanpa test**: dari **564 nama fungsi** di modul terminal, **495 tidak pernah muncul
  di `tests/`** dan **357 tidak muncul di `tests/` maupun `check_terminal.cjs``. Seluruh
  **34 factory `*View`** (5.964 baris) ada di himpunan tak-ter-test itu. `terminal.js`
  (5.176 baris) **tidak bisa di-load di Node sama sekali**, jadi tidak ada apa pun di CI yang
  mengeksekusinya.
- **Konstanta hardcode**: **1.261 literal numerik non-trivial** inline di delapan modul (776
  di `terminal-views.js` sendiri). Empat guard "degenerate row count" memakai empat ambang
  ajaib yang tidak berhubungan (800 / 2000 / 400 / 4000). 22 endpoint exchange sebagai string
  literal.
- **Asumsi tak dinyatakan**, yang paling substantif dan terkonfirmasi ujung-ke-ujung: sebuah
  asumsi **numeraire** — `AggBookStore.grouped()` menjumlahkan `qty` per harga lintas venue
  yang satuan `qty`-nya tidak sama.

## 6. Apa yang sensus ini TIDAK buktikan

Ia memetakan apa yang ADA, bukan apa yang BENAR: tidak satu pun metrik di sini dijalankan
untuk menguji nilainya, dan "punya kontrol positif" berarti kontrol itu ada di suite, bukan
bahwa angkanya pernah dicocokkan dengan venue. Cakupannya dibaca dari header dan grep
mekanis; badan lima berkas terbesar tidak dibaca seluruhnya, jadi duplikasi dan asumsi yang
lebih halus bisa lolos. Dan tidak ada satu pun return yang dilihat — sensus ini tidak
menyentuh pertanyaan apakah metrik mana pun memprediksi apa pun.
