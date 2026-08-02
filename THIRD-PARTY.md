# Third-party components

btc-quant itself is licensed under the Business Source License 1.1 ([LICENSE](LICENSE)).
This file inventories everything in this repository that someone *else* wrote, under what
terms it is here, and how each obligation is met.

Every entry below was **verified against the artefact in this repo**, not copied from a
dependency manifest: hashes were computed over the vendored files, the upstream tarballs
were re-downloaded and compared byte-for-byte, and the font metadata was read out of the
`name`/`cmap` tables. Where a claim could not be verified, the entry says so.

Inventory verified **2026-08-02**. Re-verify when a vendored file changes.

---

## 1. Vendored code — redistributed inside this repository

Exactly two JavaScript files and four font files are third-party. `git ls-files` returns
no other bundled third-party source; `btcquant/`, `scripts/` and `tests/` contain no
copied or ported third-party code.

| Component | Version | License | File in repo | Modified? |
|---|---|---|---|---|
| TradingView Lightweight Charts™ | 4.2.0 | Apache-2.0 | `dashboard/vendor/lightweight-charts.js` | No — byte-identical to upstream |
| hyparquet | 1.26.2 | MIT | bundled into `dashboard/vendor/hyparquet.js` | Yes — re-bundled (see below) |
| fzstd | 0.1.1 | MIT | bundled into `dashboard/vendor/hyparquet.js` | Yes — re-bundled (see below) |
| IBM Plex Mono (Regular, SemiBold) | 2.3 | OFL-1.1 | `dashboard/vendor/fonts/ibm-plex-mono-{400,600}.woff2` | Yes — latin subset |
| Inter (Regular, SemiBold) | 4.001 | OFL-1.1 | `dashboard/vendor/fonts/inter-{400,600}.woff2` | Yes — latin subset |

Full license texts live in [`LICENSES/`](LICENSES/) — see [§5](#5-why-the-licenses-directory-exists).

### 1.1 TradingView Lightweight Charts™ 4.2.0 — Apache-2.0

- **Upstream:** <https://github.com/tradingview/lightweight-charts>, npm `lightweight-charts@4.2.0`
  (registry `license` field: `Apache-2.0`; tarball sha1 `507c44bc6ddbf08b6197736a2821d5b1bd2adc8e`).
- **Copyright:** Copyright (c) 2023–2024 TradingView, Inc.
- **File:** `dashboard/vendor/lightweight-charts.js` — 163,551 bytes,
  sha256 `46fc69534ec098f095bbcd1d9a26d693d39a8b9eeff7343536765b3dd28c2bdf`.
- **Verified unmodified.** The npm tarball was re-downloaded and its
  `dist/lightweight-charts.standalone.production.js` compared to the vendored file:
  `diff` is empty and the sha256 matches exactly. The upstream `@license` banner is intact
  at the top of the file.
- **How it is used:** loaded as a classic script (`window.LightweightCharts`) by
  `dashboard/index.html:533` and `dashboard/terminal.html:835`; it draws the historical
  candle panels and the footprint host chart. Vendored deliberately — no CDN at runtime.
- **Obligations met:**
  - §4(a) *"give any other recipients a copy of this License"* → [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt),
    the canonical text from <https://www.apache.org/licenses/LICENSE-2.0.txt>.
  - §4(d) *NOTICE* → the upstream repository ships a `NOTICE` file (the npm tarball does not),
    reproduced verbatim at [`LICENSES/lightweight-charts-NOTICE.txt`](LICENSES/lightweight-charts-NOTICE.txt).
  - §4(b) *"carry prominent notices stating that You changed the files"* → not applicable;
    the file is unmodified.
  - Attribution is carried in [README.md](README.md) and in the terminal page footer
    (`dashboard/terminal.html:815`).
- **Note on the on-canvas watermark:** `layout.attributionLogo: false` is set on the chart
  panels (`dashboard/terminal-views.js:379` and three others), which removes the on-canvas
  TradingView mark. That is a library *option*, not a license term — Apache-2.0 requires the
  notices above, which are carried in text instead. The mark was relocated, not dropped.

### 1.2 hyparquet 1.26.2 + fzstd 0.1.1 — MIT

- **Upstream:** hyparquet — <https://github.com/hyparam/hyparquet>, npm author `Hyperparam`,
  homepage <https://hyperparam.app>; fzstd — <https://github.com/101arrowz/fzstd>,
  Copyright (c) 2020 Arjun Barrett.
- **File:** `dashboard/vendor/hyparquet.js` — sha256
  `57b6a77044243de843da97095abcfd22f6e3ce065e1a0b455390130675897604`. Both packages are
  flattened into this one classic script.
- **Provenance verified.** The file's own header claims npm tarball sha1
  `fbaf8731ea310e83d8433c5948c4102a1c74bc05` (hyparquet) and
  `a3da29f2fff45070ca90073f866d97e0c56a4a52` (fzstd). Both tarballs were re-downloaded from
  `registry.npmjs.org`; the computed sha1s and the registry's own `dist.shasum` values match
  those claims exactly. Both registry entries declare `"license": "MIT"`.
- **Modified — and it says so.** The vendored file is a derivative: the browser entry of
  hyparquet plus fzstd, bundled with `esbuild --bundle --format=iife` (0.25.5), with a
  19-line entry module wiring `compressors = { ZSTD: … }`, kept unminified. The 40-line
  header in the file records the build exactly, including what was deliberately excluded
  (`src/node.js`). MIT permits this; the header carries the MIT permission notice inline,
  which is the condition MIT actually imposes.
- **How it is used:** `dashboard/terminal-hfdata.js` reads archived-day parquet straight from
  the Hugging Face dataset in the browser (ZSTD-compressed, hence fzstd).
- **Obligations met:** the copyright and permission notice are in the vendored file's header;
  the upstream texts are also shipped verbatim as
  [`LICENSES/MIT-hyparquet.txt`](LICENSES/MIT-hyparquet.txt) and
  [`LICENSES/MIT-fzstd.txt`](LICENSES/MIT-fzstd.txt).
- **One honest gap upstream:** hyparquet's own `LICENSE` file contains the MIT permission
  text with **no copyright line at all**. The attribution used here (`(c) Hyperparam`) comes
  from the package's `author` field, not from a copyright notice, because upstream does not
  provide one.

## 2. Web fonts — OFL-1.1, vendored and subsetted

Both families are self-hosted (`dashboard/styles.css:18-21`), no CDN, no Google Fonts request
at runtime. Read out of the font `name` and `cmap` tables directly:

| File | Family / style | Version (nameID 5) | Copyright (nameID 0) | Chars | Glyphs |
|---|---|---|---|---|---|
| `ibm-plex-mono-400.woff2` | IBM Plex Mono Regular | Version 2.3 | Copyright 2017 IBM Corp. All rights reserved. | 229 | 280 |
| `ibm-plex-mono-600.woff2` | IBM Plex Mono SemiBold | Version 2.3 | Copyright 2017 IBM Corp. All rights reserved. | 229 | 280 |
| `inter-400.woff2` | Inter Regular | Version 4.001;git-66647c0bb | Copyright (c) 2016 The Inter Project Authors (https://github.com/rsms/inter) | 230 | 518 |
| `inter-600.woff2` | Inter SemiBold | Version 4.001;git-66647c0bb | Copyright (c) 2016 The Inter Project Authors (https://github.com/rsms/inter) | 230 | 518 |

- **License:** SIL Open Font License 1.1 for both, declared in the font metadata itself
  (nameID 14 → `http://scripts.sil.org/OFL` for IBM Plex, `https://openfontlicense.org` for
  Inter) and in the upstream `LICENSE.txt` files, shipped here verbatim as
  [`LICENSES/OFL-1.1-IBM-Plex.txt`](LICENSES/OFL-1.1-IBM-Plex.txt) and
  [`LICENSES/OFL-1.1-Inter.txt`](LICENSES/OFL-1.1-Inter.txt). Each of those files carries its
  own copyright line followed by the full OFL text, which is exactly what OFL §2 requires when
  the font is redistributed. (IBM's copy is byte-identical to the canonical SIL text; rsms's
  copy differs only in spelling the heading "PERMISSION AND CONDITIONS" instead of
  "PERMISSION & CONDITIONS".)
- **Coverage measured:** U+0020–U+007E and U+00A0–U+00FF plus `U+0131`, `U+0152-0153`,
  `U+02BB-02BC`, `U+02C6`, `U+02DA`, `U+02DC`, combining marks, general punctuation,
  `U+20AC`, `U+2122`, `U+2191`, `U+2193`, `U+2212`, and `U+2215` (Plex) / `U+FEFF` (Inter).
  That is a latin subset — the same range set a standard `latin` webfont subset produces.
- **Not recorded:** unlike the JS vendors, the repository has no provenance header for the
  fonts. The commit that added them (`283a390`, 2026-06-13) does not record which upstream
  build or subsetting tool produced these exact bytes. Everything above is read out of the
  files; the toolchain is not attested.
- **Open point — Reserved Font Name (low severity, real).** Subsetting a webfont is
  "modification" under the SIL OFL-FAQ (entry 2.6), and a Modified Version may not use a
  Reserved Font Name unless it preserves Functional Equivalence (entry 2.8), which a reduced
  character inventory does not.
  - **Inter declares no RFN** — its copyright line has no "with Reserved Font Name" clause —
    so the subset may keep the name `Inter`. No issue.
  - **IBM Plex declares an RFN**: *Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"*.
    The subsetted files here still identify as `IBM Plex Mono`. Strictly read, that is an RFN
    use a Modified Version is not granted. It affects naming only — the right to use,
    redistribute and bundle the font is unaffected, and it creates no conflict with this
    project's license. Three ways to close it, in increasing effort: ship the unsubsetted
    upstream woff2; rename the family in the subset (e.g. `BQ Mono`) and update
    `dashboard/styles.css`; or obtain written permission from IBM. Recorded here rather than
    quietly ignored.

## 3. Remote third-party components — referenced, not redistributed

- **TradingView Advanced Chart widget** — `dashboard/index.html:288` loads
  `https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js` at runtime.
  No TradingView code is copied into this repository; the widget runs from TradingView's own
  host, under **TradingView's widget terms, not Apache-2.0**. Those terms require the
  "Track all markets on TradingView" attribution link, which the page carries
  (`dashboard/index.html:283-286`) and which must not be removed.
- **Public market data** — all exchange REST/WS endpoints and the Hugging Face dataset are
  accessed keylessly at runtime. Data is not code and none of it is redistributed here; the
  applicable terms are each venue's own.

## 4. Declared dependencies — installed by the user, not redistributed

Nothing in `requirements*.txt` is vendored; pip fetches these at install time and none of the
packages ship inside this repository. Licenses as declared on PyPI (checked 2026-08-02):

| Package | Declared license |
|---|---|
| numpy | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| pandas | BSD-3-Clause |
| scipy | BSD-3-Clause |
| statsmodels | BSD-3-Clause |
| matplotlib | Matplotlib license (PSF-based, BSD-compatible) |
| requests | Apache-2.0 |
| pytest | MIT |
| mlflow *(optional)* | Apache-2.0 |
| dvc *(optional)* | Apache-2.0 |
| duckdb *(collector)* | MIT |
| websockets *(collector)* | BSD-3-Clause |
| huggingface_hub *(collector)* | Apache-2.0 |

All permissive. No copyleft dependency, optional or otherwise.

## 5. Why the `LICENSES/` directory exists

Apache-2.0 §4(a) requires that recipients of the work get a copy of the Apache license text,
and OFL-1.1 §2 requires that each redistributed copy of the font carry its copyright notice
and license. The repository root's `LICENSE` is taken by btc-quant's own Business Source
License, and a reader must never have to guess which text governs which file. So each
third-party license is shipped as its own verbatim file under `LICENSES/`, named after the
component it covers, and referenced from the entry above. Pasting them into an appendix of
this file would have meant reflowing legal text into Markdown — a modification of a document
that is only useful unmodified.

| File | Covers |
|---|---|
| `LICENSES/Apache-2.0.txt` | Lightweight Charts (also btc-quant's own Change License, from 2030-08-02) |
| `LICENSES/lightweight-charts-NOTICE.txt` | Lightweight Charts NOTICE (Apache-2.0 §4(d)) |
| `LICENSES/MIT-hyparquet.txt` | hyparquet |
| `LICENSES/MIT-fzstd.txt` | fzstd |
| `LICENSES/OFL-1.1-IBM-Plex.txt` | IBM Plex Mono |
| `LICENSES/OFL-1.1-Inter.txt` | Inter |

## 6. License-compatibility check

The question that matters for a BSL project: **can anything vendored here force btc-quant's
own source to be released under different terms?** Checked component by component.

- **No copyleft anywhere.** Apache-2.0, MIT and OFL-1.1 are all permissive. None of them
  extends to the code that merely uses the component, so none of them constrains the terms
  under which btc-quant itself is licensed. OFL-1.1 is the only one with a
  "must-stay-under-this-license" clause and it is scoped to the *Font Software* alone
  (OFL §2, §4, §5); OFL §5 also forbids selling the fonts *by themselves*, which this
  repository does not do.
- **No GPL, LGPL, AGPL or MPL component is present** — verified across vendored files,
  `requirements*.txt`, and a repo-wide search for copied or ported sources.
- **Apache-2.0 inbound is compatible with BSL 1.1 outbound.** Apache-2.0 imposes attribution
  and notice obligations (§4), met in §1.1 above, and no reciprocity.
- **Apache-2.0 is a valid Change License.** BSL's Covenant 1 requires the Change License to be
  GPL v2.0 or later, or a license compatible with "GPL Version 2.0 or a later version". The FSF
  states that Apache-2.0 is compatible with GPL version 3 (and, explicitly, not with version
  2). Compatibility with a *later* version satisfies the covenant as written, and Apache-2.0
  is the Change License used by other BSL 1.1 adopters. On 2030-08-02 the vendored
  Apache-2.0 chart library and btc-quant's own converted source land under the same license.
- **Clean-room boundary.** No code from GPL-3.0 projects has been read into this repository —
  in particular, none from flowsurface (GPL-3.0-or-later in its root and `data` crates),
  whose shaders and rendering code are studied side-by-side as a comparison tool only. A
  repo-wide search for `flowsurface`, `wgsl`, `wgpu` and `iced` returns nothing. Ideas and
  measured behaviour are not copyrightable; source is, and none was taken.
- **Public URL schemes are not third-party IP.** `data.binance.vision` is Binance's public
  archive path convention, freely usable, and the collector already ingests the same
  `/fapi/v1/aggTrades` stream keyed on `(exchange, symbol, trade_id)`.

**Result: no conflict found.** One open point, recorded in §2: the subsetted IBM Plex Mono
files retain the Reserved Font Name "Plex". It is a naming obligation under OFL-1.1, not a
compatibility problem, and it does not affect btc-quant's own license.
