# LSEGDashboard — Project Guide (CLAUDE.md)

> Read this first whenever working on this project. It is the single source of truth for
> vision, architecture, conventions, and known gotchas — written to minimise re-exploration
> and token spend. Keep it updated when structure changes.

---

## 1. Vision & Purpose

A self-hosted **edible-oils / vegoil-complex market dashboard** for a commodity/algo trader
(palm-oil spreads: FCPO, DCP, BO, LGO, plus the broader soy/energy complex). It pulls daily
price/volume/OI from **LSEG DataScope Select** (REST), normalises everything to **USD/MT**,
computes a large library of **spreads, basis, and crush** series, and serves them as
**seasonal overlay charts** (one line per year) in a Dash web app. It also layers on
OI analysis, supply & demand (SND), hedge ratios, tradable premiums, an AI narrative tab,
and a document-intelligence "News" tab from broker/Argus/Oil World reports.

**Core chart paradigm:** almost every chart is a *seasonality plot* — x-axis = day-of-year
(Jan→Dec), one coloured line per calendar year, current year highlighted. The year-picker
selects which years overlay. This is the dominant visual idiom — preserve it.

---

## 2. Quick Orientation / Run Commands

- **Dashboard:** `scripts/dashboard.py` → Dash app on **http://0.0.0.0:8055** (`app.run(..., port=8055)`).
- **Start manually:** `start_dashboard.bat` (runs `python scripts\dashboard.py`).
- **Daily data refresh + restart:** `update.bat` → runs `update.py`, kills the process on
  port 8055, restarts dashboard with `pythonw.exe` (headless).
- **Scheduled tasks:** `setup_scheduler.bat` creates two Windows scheduled tasks:
  - `LSEGDashboard_Update` — runs `update.bat` 08:00 Mon–Fri.
  - `LSEGDashboard_Start` — auto-starts dashboard on logon.
- **Python:** `C:\Program Files\Python310\python.exe` (Python 3.10, hardcoded in .bat files).
- **Deps:** `requirements.txt` — requests, pandas, plotly, dash, scikit-learn, kneed,
  anthropic, pymupdf, python-docx, openpyxl, pyarrow, google-api-python-client, google-auth.

---

## 3. Data Pipeline (the spine)

`update.py` orchestrates the daily run, in order:

```
fetch.py      → pull RICs from LSEG → data/raw/all_instruments_raw.csv
process.py    → resolve price, convert to USD/MT, compute spreads → data/processed/*.csv
oi_analyze.py → futures OI/volume analysis → data/oi/...
oi_summary.py → OI summary tables
gsheet_data.fetch_india_data()        (non-critical) India local prices
tradable_prem_data.fetch_tradable_prem() (non-critical) KPN sheet premiums
dcp_gsheet_push.push_dcp_prices()     (non-critical) push DCPc1-6 USD/MT → Google Sheet
doc_extract.py (non-critical)         document intelligence → data/doc_intel/
```

Non-critical steps are wrapped in try/except — a failure logs a WARNING but never aborts the
price pull. All output goes to `logs/update_log.txt`.

The dashboard **reads only the processed CSVs / parquet at startup** (and on tab callbacks).
It does NOT call the LSEG API itself. So: **to see new data in the dashboard you must
re-run the pipeline (`update.bat`) and restart the app.**

### Fetch is incremental by default
`fetch.py` pulls only a trailing **`LOOKBACK_DAYS = 7`** window and merges into the existing
raw CSV, dedup on `(RIC, Trade Date)` with `keep="last"` (newest settlement/OI wins).
Full history (`START_DATE = 2015-01-01`) is used only when the raw CSV is missing or when
run with **`--full`** (first-time backfill or after adding new RICs).

---

## 4. LSEG DataScope Select API (`scripts/auth.py`, `scripts/fetch.py`)

- **Auth:** POST to `…/Authentication/RequestToken` with username/password from
  `credentials.ini` → Bearer token cached in `token.json` (~23h lifetime, refreshed 1h early).
  On 401 during extraction: delete `token.json` and retry.
- **Extraction:** async pattern — POST `ExtractWithNotes` with
  `ElektronTimeseriesExtractionRequest`, header `Prefer: respond-async` → returns **202** with
  a `Location` header → **poll** that URL every 10s until **200**.
- **Fields requested:** `["RIC", "Trade Date", "Last", "Settlement Price", "Bid", "Ask", "Volume", "Open Interest"]`.
  - ⚠️ **Field-name gotcha:** Elektron Timeseries uses plain `"Open"/"High"/"Low"/"Last"`.
    `"Open Price"` / `"Close"` are **rejected** by the API. (See user memory `reference_datascope_fields`.)
- **Price resolution (`process.py load_prices`):** FX RICs → mid `(Bid+Ask)/2`; everything
  else → `Last`, fallback `Settlement Price`.

---

## 5. RIC Universe & Unit Conversions (`config.py`)

`RICS` dict groups instruments; `ALL_RICS` is the flat list sent to the API.

**Futures (c1–c7):** FCPO (MDEX palm), BO (CBOT soyoil), LGO (ICE gasoil), DCP (Dalian
palm olein), DBY (Dalian soyoil), S (CBOT soybean), SM (soymeal), HO (heating oil),
CL (WTI), LCO (Brent). Plus FCPO full curve c1–c12 for SND. **FX:** `MYR=`, `CNY=`.
**Cash:** Malaysia, Indonesia, US, South America (Argentina), China (Dalian), Black Sea (sunflower).

**Everything is normalised to USD/MT before spreads** (`process.py convert_currencies`):
- `MYR_RICS` (FCPO, CPO-MYSTH) → ÷ `MYR=`
- `CNY_RICS` (DCP, DBY, JCI-SOA-DALN) → ÷ `CNY=`
- `CENTSLB_RICS` (BO) → × 22.0462  (cents/lb → USD/MT)
- S (cents/bushel) → × 0.3674 · SM (USD/short ton) → × 1.1023 · HO (USD/gallon) → × 264.172
- CL & LCO (USD/barrel) → × 6.2898

The crush spread uses **raw (unconverted) local-unit prices** — `process.py` keeps a parallel
`wide_raw` for that. Native units per instrument are documented in `dashboard.py`
`SPREAD_LOCAL_UNITS` and `OUTRIGHT_UNITS` (also drives the dashboard's $/TN ↔ Local toggle).

**Adding a new instrument:** add RIC to `config.RICS` + relevant conversion set, add a spread
definition in `process.py calc_spreads`, add a title in `dashboard.py TITLES`, add the key to
the relevant `SUBSECTIONS` group, then run `fetch.py --full` once to backfill.

---

## 6. Spread Library (`process.py calc_spreads`) → `data/processed/*.csv`

Each output CSV is tidy: `date, spread_value`. Sections:
1. **Calendar spreads** — c1c3, c3c5, c5c7 per product.
2. **Product spreads** (c1,c2,c3,c5,c7) — BOPO, BOGO, POGO, DCP-FCPO/BO/LGO,
   DBY-FCPO/BO/LGO, DCP-DBY, SM-PO, BOHO, Brent-WTI.
3. **Malaysia cash premium** vs FCPOc3 · 4. **Indonesia cash premium** vs FCPOc3.
5. **Malindo** (Malaysia − Indonesia) · 6. **Cash product spreads** (olein/stearin/PFAD).
7. **CBOT crush** ($/bushel, raw units) · 7b. **SA crush** (USD/MT).
8. US basis · 9. SA basis · 10. China basis · 11. Cross-geography (SA/sun/CPO).
12. **Oilshare** — `OIL_SHARE_c1` (CBOT) + `SA_OIL_SHARE` (Argentina). This is CME
    Group's own published "Oilshare" metric (it has real CME futures/options products) —
    verified against CME's methodology and a farmdoc/DTN July-2025 piece: oil's %
    share of combined oil+meal crush revenue, long-run average ~37.5%, elevated
    >47.9% (2 std dev) only ~3% of the time historically. Our CBOT series matches
    almost exactly (mean 38.0%, current ~52%, in line with DTN's independently
    reported ~50% in mid-2025 — both reflect biofuel/RD-driven oil demand).
    `OIL_SHARE_c1` reuses the raw BO/SM c1 prices and bushel-yield factors from the
    Section 7 crush calc (`OilValue = BO¢/lb × 0.11`, `MealValue = SM$/shortton × 0.022`).
    `SA_OIL_SHARE` reuses the SA_CRUSH physical extraction yields (18.5% oil / 79.0%
    meal by weight, already USD/MT) on `BO-ARGUPR-C1` / `SM-ARGUPR-P1`.
    **Note:** neither series prices the ~5lb/bushel (8.3%) of hulls/waste — this
    matches CME's own methodology, which only splits revenue between oil and meal
    since hulls/waste aren't independently exchange-priced.

---

## 7. Dashboard Structure (`scripts/dashboard.py`, ~3000 lines)

Top-level `dcc.Tabs` (`id="section-tabs"`):
- **Spread sections** (driven by `SUBSECTIONS` dict, each = nested groups of series keys):
  `Calendar Spreads`, `Product Spreads`, `Basis/Hedge Ratios`, `Crush Spread`,
  `Leading Signals`, `Outright Prices`. (`Malindo Spreads` is still a `SUBSECTIONS`
  key — data/year logic keys off it unchanged — but is **no longer its own top-level
  tab**; it moved under `Parity → Malindo`, see below, 2026-09-21.)
  - **Leading Signals** — two charts, both CME's "Oilshare" metric (% of oil+meal
    crush revenue driven by oil): `OIL_SHARE_c1` ("CBOT Soybean Oilshare C1 (%)")
    under a "CBOT Soybean Crush" subsection, and `SA_OIL_SHARE` ("SA Oilshare —
    Up River FOB (%)") under a "South America" subsection — mirrors the Crush
    Spread tab's subsection layout. Both driven by the same global year-picker as
    every other spread tab. Computed in `process.py` Section 12 (see §6). Added as
    just another `SUBSECTIONS` entry — no special-cased callback logic needed
    (falls through the generic `update_charts` branch, same as Crush Spread /
    Basis-Hedge-Ratios).
- **Technical Analysis** — K-Means horizontal S/R levels (sklearn + kneed elbow) via
  `create_instrument_plot`.
- **OI/Volume** (renamed from "OI Analysis" 2026-09-28) — **single flat tab,
  no sub-tabs** (the original per-contract "Individual" sub-tab — signal
  scorecards, roll-masked charts, `oi-table`/`oi-chart` pattern-matching
  components — was **fully removed 2026-09-28** per trader's explicit ask;
  `oi_analyze.py`/`oi_summary.py` still run in `update.py` in case it's
  wanted again, but `dashboard.py` no longer reads their output at all). What
  remains — what used to be the "Cumulative" sub-tab — is one table per
  instrument, **10 instruments** as of 2026-09-28: FCPO Palm Oil, CBOT
  Soybean Oil (BO), ICE Gas Oil (LGO), DCP Palm Olein, Dalian Soybean Oil
  (DBY), ICE Brent (LCO), NYMEX Heating Oil (HO), NYMEX WTI (CL), CBOT
  Soybean Meal (SM), CBOT Soybean (S) — **in that display order**
  (`dashboard.py`'s `OI_CUMULATIVE_LAYOUT_ORDER`, per trader's explicit ask:
  FCPO Palm Oil/CBOT Soybean Oil/ICE Gas Oil lead as 1-3, then the two
  Dalian instruments pushed up to 4-5). Display names (not
  the bare exchange codes) come from `OI_CUMULATIVE_DISPLAY_NAMES`. 30 trading
  days per table, **8 columns**: Date | Total OI (lots) | Delta OI | Total
  Volume (lots) | Delta Volume | **OI/Vol %** | Avg Px (native unit) | Px Chg.
  **Layout**: flex-wrap (`display:flex, flexWrap:wrap`, each table
  `flex:"1 1 680px", maxWidth:"820px"`) — packs as many tables per row as the
  window fits, rather than a fixed column count, per trader's explicit ask to
  "go back to ... a lot more instruments in one go" after an intermediate
  fixed-2-column-grid version. **Total OI/Total Volume are NOT capped at c7**
  (or c12 for FCPO) — both sum every contract month the exchange still
  reports live data for, since liquidity is non-monotonic across the curve
  for the quarterly-cycle products (e.g. DCPc8 ≈287k lots dwarfs DCPc7's
  ≈340).
  - **OI/Vol % (added 2026-09-28, trader's explicit ask)** — Delta OI ÷
    Total Volume × 100, rounded to a whole %: the share of the day's
    turnover that became net *new* Open Interest, vs same-day offsetting/
    day-trading churn. `oi_cumulative.py`'s `build_cumulative()` computes
    `oi_vol_pct` (NaN when volume is 0/missing, via `.where(total_volume>0)`
    rather than dividing by zero). **Flagged when |ratio| > 100%**
    (`_OI_VOL_FLAG_THRESHOLD` in `dashboard.py`) — you cannot durably create
    more new contracts in a day than the day's total volume, so a ratio past
    100% is almost always a vendor OI correction/rollover artifact rather
    than a real trading fact (see the FCPO flag below). Trader's explicit
    ask was "flag as error, or remove entirely, or darken so one can't see
    it" — implemented as **muted**: the cell renders "–" in light gray
    (`#dcdcdc` on white) instead of the number, with the real value kept in
    the `title` hover tooltip for anyone who wants to check it, rather than
    fully deleting the data point. Verified against full history: only 17
    of ~29,400 rows exceed the threshold (mostly 2019 LCO/2022 FCPO
    volatility events, and the 2026-06-18 HO/CL data glitch) — confirms the
    threshold catches genuine rare anomalies, not everyday noise.
    **2nd tier added same day** — `_OI_VOL_NOTABLE_THRESHOLD = 10.0`:
    |ratio| between 10% and the 100% anomaly cutoff is a real, unmuted
    number but is genuinely large fresh-positioning activity worth a
    trader's attention, per the explicit ask "if >10%, flag it somehow so
    it calls for the viewer's attention... slight shade on that row". The
    whole `html.Tr` gets `backgroundColor: "#fff6d9"` (light amber) and the
    OI/Vol % cell itself renders bold `#b26a00`; the 100%+ anomaly tier is
    unaffected (still muted gray, no row shade — a likely data error isn't
    something to draw the eye to).
  - **Avg Px switched to native currency/unit (2026-09-28, trader's explicit
    ask — "Better to show all Avg Px in local currency")**: `oi_cumulative.py`
    no longer converts prices to USD/MT at all (the `_convert_price()`
    helper and its MYR=/CNY= FX-rate handling were removed outright, not
    just bypassed) — `avg_price` is now the straight c1-c5 mean of each
    RIC's raw quoted price. `dashboard.py`'s `_oi_cumulative_table()` reuses
    the existing `SPREAD_LOCAL_UNITS` dict (§7 Cash Premium/Basis area, the
    same one the Local-unit toggle elsewhere in the dashboard uses) to label
    each table's own header, e.g. "Avg Px (MYR/MT)" for FCPO, "Avg Px
    (cents/lb)" for BO, "Avg Px (USD/gallon)" for HO — no new unit mapping
    duplicated. New `_fmt_local_price()` helper: 2 decimals when
    `abs(value) < 200` (HO ~$4.47/gal, CL/LCO ~$85-98/bbl, BO ~68 cents/lb
    would round to near-meaningless whole numbers otherwise), whole number
    above that (FCPO/DCP/DBY thousands-range MYR/CNY, SM/S hundreds-range).
    Verified end-to-end: HO's rendered table shows `4.47` (not the old
    ~1,182 USD/MT-converted figure) in the live `/_dash-layout` output.
  - **New extended-curve contract ranges** — determined empirically via
    one-time probe pulls (c1-c24 first pass; c1-c48/c1-c90 follow-up probes
    for LGO/CL/LCO specifically since they still had live OI at the first
    cutoff — NYMEX WTI and ICE Brent carry real Open Interest on far-dated
    December strips out to 6-7 years, a genuine market-structure feature, not
    a data error): `config.OI_CUMULATIVE_RANGE` = FCPO 17, DCP 12, BO 18,
    SM 18, S 17, LGO 40, HO 28, CL 87, LCO 74, DBY 8 (each is the last
    contract-month RIC LSEG still reports a non-null OI for; beyond it the
    RIC either returns no rows, or price-only rows with OI always null).
    `config.OI_CUMULATIVE_RICS`/`_OI_CUM_ALL_RICS` derive from this;
    `_EXTRA_OI_CUM` (now 319 RICs total across both rounds) is appended to
    `ALL_RICS` so the daily incremental `fetch.py` pulls them going forward —
    **345 RICs total now, up from the original 101**.
  - **Per-instrument arrow thresholds** (added 2026-09-28, trader's explicit
    ask, replacing the original fixed 1%/2% cutoffs) — `oi_cumulative.py`'s
    `build_arrow_thresholds()` computes the median and 75th-percentile of
    `abs(day-on-day % move)` in the avg-c1-c5 price, per product, over the
    trailing ~3 years (`ARROW_THRESHOLD_START = "2023-01-01"`), and writes
    `data/processed/oi_arrow_thresholds.csv`. `dashboard.py`'s `_oi_arrow(pct,
    product)` looks up that product's (median, p75) — below median = ×1
    arrow, median-to-p75 = ×2, above p75 = ×3 — so "how big is this move" is
    judged against each instrument's own volatility (e.g. DBY/S median
    ≈0.6%, CL/LGO/HO median ≈1.2-1.3%) rather than one fixed % for every
    product. Falls back to a fixed (1.0%, 2.0%) pair
    (`_OI_ARROW_FALLBACK`) if a product is missing from the CSV.
  - **Pipeline step `scripts/oi_cumulative.py`** (wired into `update.py`
    right after `oi_summary.py`) — sums both OI and Volume across
    `OI_CUMULATIVE_RICS` per product per day (`min_count=1`, so an all-NaN
    day is a real gap not a fake 0), computes day-on-day OI change and
    day-on-day Volume change (added 2026-09-28, trader's explicit ask), and
    an avg-c1-c5 outright price (converted to USD/MT inline — FCPO÷MYR=,
    DCP÷CNY=, DBY÷CNY=, BO×22.0462, S×S_CONV, SM×SM_CONV, HO×`config.HO_CONV`,
    CL×`config.CL_CONV`, LCO×`config.LCO_CONV`, LGO already USD/MT) with its
    own day-on-day % change, then the arrow-threshold table above. Writes
    tidy `data/processed/oi_cumulative.csv` (now with `total_volume`/
    `volume_chg` columns) + `oi_arrow_thresholds.csv`; `dashboard.py`'s
    `_oi_cumulative_load()`/`_oi_arrow_thresholds_load()` read them into
    `OI_CUMULATIVE`/`OI_ARROW_THRESHOLDS` at startup, `_oi_cumulative_table()`
    renders each product's static table.
  - **One-time backfill gotcha (hit twice)**: the naive approach (re-running
    `fetch.py --full` for every RIC's full 2015-history in one request) hit a
    server-side `ReadTimeout` on the poll GET the first time (176 RICs) — the
    same large-pull issue documented in `project_lseg_dashboard` memory that
    originally made `fetch.py` incremental. Fixed both times with the
    **one-time** `scripts/oi_extra_backfill.py`: pulls only the *new* RICs
    (not ones already held), chunked **by year** (12 requests), retrying
    transient poll `ReadTimeout`s instead of failing, staging incrementally
    to `data/raw/_oi_extra_backfill_staging.csv` (resumable — re-running
    skips years already staged) before merging into
    `data/raw/all_instruments_raw.csv`. Reused as-is for the second round
    (HO/CL/LCO/DBY, 244 new RICs) since it reads `config._EXTRA_OI_CUM`
    dynamically — no script changes needed, just re-run it after extending
    `config.OI_CUMULATIVE_RANGE`. This script is a one-time tool, not part of
    the daily pipeline — kept for the next time a similar wide-RIC-list
    backfill is needed (staging CSV deleted after each successful merge).
    Verified end-to-end both rounds: round 1 (2026-09-28) 221,579 rows
    backfilled, 290,426+221,579=512,002 total; round 2 (same day) 714,625
    rows backfilled, 512,002+714,625=1,005,033 total, all 244 target RICs
    present. `oi_analyze.py`/`oi_summary.py` unaffected both times (still 70
    RICs, unchanged from `RICS["futures"]`). Dashboard restarted after each
    round and `/_dash-layout` confirmed the tab/labels/pairing live on port
    8055.
  - **FCPO data-quality flag (2026-09-28, not a bug)**: trader asked to
    cross-check FCPO's 2026-09-25 Total OI (476,544, +138,876/+41% vs the
    prior day) — verified it is **not** a Cumulative-tab summing artifact:
    every individual FCPO contract from c1 through c16 shows a similar
    proportional (+20% to +60%) jump that same date in the raw per-contract
    LSEG feed itself (confirmed via a direct raw-CSV query, single row per
    date, no duplicates) — it's present in the per-contract data itself, not
    introduced by the summing logic. Every other product (DCP/BO/SM/
    S/LGO) shows only normal single-digit % day-on-day changes over the same
    window, so this is isolated to BMD FCPO's reported Open Interest field,
    not a pipeline-wide issue. Left as-is (real vendor data) — flagged to the
    trader as either a genuine large repositioning day or an upstream
    vendor-side correction, unverifiable from here without an independent
    Bursa Malaysia OI source.
- **SND** — supply & demand seasonal charts (Graphs/Exports/Tabular sub-tabs).
- **AI** — on-demand Gemini narrative report per instrument/timeframe.
- **News** — geography × product matrix blending LSEG data + document intelligence.
- **Calendar** — upcoming ~30 days of commodity/energy/biofuel/positioning/macro data
  releases (WASDE, MPOB, EIA petroleum status + SPR, CFTC COT, OPEC, EPA RFS, …),
  compiled daily by **Claude Sonnet + web search** — run through the **Claude Code CLI**
  on the Max/Pro subscription (not the API key), see §8 — via `calendar_data.py` into a cached CSV
  and rendered as a sortable/filterable `dash_table.DataTable` with hyperlinked official
  sources and today/this-week highlighting. Standalone content div (`calendar-content`) +
  its own visibility toggle, mirroring the News tab.

- **CFTC** (top-level tab, added 2026-10-06) — Commitments of Traders spec
  positioning for the vegoil/biofuels complex. Standalone content div
  (`cftc-content`) + its own visibility toggle, mirroring the Calendar tab —
  no sub-tabs, no year-picker/unit-toggle. Data module `scripts/cftc_data.py`:
  **source is manual** — the user saves CFTC's "Disaggregated Commitments of
  Traders — Options and Futures Combined" long-format report pages (plain
  fixed-width text wrapped in a `<pre>` tag, e.g.
  `https://www.cftc.gov/dea/options/ag_lof.htm`) into
  `Biofuels/CFTC/<category>/*.html` each week (category = `Ag` / `Petroleum` /
  `Nat Gas`, one subfolder per saved page — these live in the sibling
  `Biofuels/` tree, not under `LSEGDashboard/`). `load_cftc()` globs
  `Biofuels/CFTC/*/*.html`, parses **every** commodity block in **every**
  file with `parse_cftc_file()` (regex over the fixed-width text; header
  line `"<NAME> - <EXCHANGE>  Code-<code>"`, then the "All" maturities
  Positions row, then the "Changes in Commitments from: <date>" row) — no
  stateful history CSV; it's cheap to re-parse a handful of small local files
  from scratch every time, so nothing can go stale or desync. Each block's
  embedded "Changes in Commitments" figures already encode the **prior**
  week's levels (current − change), so `_expand_with_prior()` synthesizes
  that as its own row — **a single saved file already yields 2 points** on
  the time axis; each new weekly file the user drops in adds one more.
  Rows are deduped on `(cftc_code, report_date)`. `RELEVANT_PRODUCTS` (12,
  matched by **CFTC code**, not name — names are truncated to a fixed column
  width in this report) is the trader's hand-picked subset of the
  hundred-plus commodities in the 3 files: CME Malaysian Crude Palm Oil,
  CBOT Soybean Oil/Meal/Soybeans, ICE Canola, CBOT Corn, NYMEX ULSD (diesel/
  gasoil proxy), NYMEX WTI, NYMEX Brent (financial), NYMEX RBOB Gasoline,
  NYMEX Ethanol, NYMEX Henry Hub. "Managed Money" (CFTC's disaggregated-report
  category for funds/CTAs/macro specs) is the number this tab is built
  around — net position (Long−Short) and net-%-of-OI, both diverging
  blue=net-long/red=net-short. Layout: (1) headline horizontal bar — week-
  over-week Δ in MM net position, one bar per tracked product, the main
  "where did spec add/reduce this week" view; (2) horizontal bar — current MM
  net position as % of Open Interest (normalises across products of very
  different OI); (3) grid of per-product small multiples — MM/Swap Dealer/
  Producer-Merchant net position over time (plain line chart, not the
  seasonal-year-overlay idiom used elsewhere, since COT is weekly not daily);
  (4) a plain `html.Table` snapshot (not `dash_table.DataTable`, mirroring
  the OI/Volume tab's own manual-table convention) with per-product OI, MM
  long/short/net/%OI, WoW Δ, Swap net, Producer/Merchant net. **Verified
  end-to-end**: `load_cftc()` parsed 137 commodities / 2 report dates / 272
  rows from the 3 seed files (Ag 30 + Petroleum 23 + Nat Gas 84 commodities);
  all 12 `RELEVANT_PRODUCTS` resolved with sane current + WoW-Δ values
  (confirmed by hand against the raw text, e.g. CBOT Soybean Meal MM net
  +207,416 contracts / +28.8% of OI / +15,047 WoW); `_toggle_cftc_view`/
  `_init_cftc_tab` called directly in-process (no exceptions, 8-child layout
  built) — a live second dashboard instance was also briefly started on 8055
  to sanity-check `/_dash-layout` but Windows let two processes both claim
  the LISTENING socket, so that check was abandoned and the duplicate killed
  immediately; the in-process call is the real verification. **To refresh**:
  save next week's 3 report pages into the same `Biofuels/CFTC/<category>/`
  folders (any filename, must end `.html`) and restart the dashboard — no
  pipeline/`update.py` wiring, this tab reads straight from those files at
  startup, same convention as Parity's live Google-Sheets fetch.

- **Argus** (top-level tab, added 2026-09-03, **rebuilt 2026-09-08**) — biofuels
  reference prices from the private Argus "Prices" Google Sheet, plus FCPO
  merged in from LSEG (exact-date join, already USD/MT). **2026-09-08 root
  cause + rebuild**: the tab looked stale/inconsistent (series count flapping
  60→55, all series frozen at one date) because the *upstream Excel* that gets
  synced into the Google Sheet (via an Apps Script that copies a Drive-synced
  `.xlsx` from an Argus Excel plugin, converting it to native Sheets to read
  it — see the Apps Script docstring in the sheet's script project) had its
  column layout reshaped/corrupted, not because of anything in this repo —
  `argus_data.py`'s fetch/join/cache-write were succeeding every day
  regardless. The user rebuilt the source Excel; `argus_data.py` was rewritten
  the same day for the new 34-column layout it now produces. If this recurs,
  check the Apps Script's Executions log (row/column count per run) and the
  source `.xlsx`'s Drive "last modified" date *before* assuming this code
  broke — see `project_lseg_dashboard` memory for the full chain.

  **Current layout** — 6 curve products (M1/M2/M3, London close): UCOME (fob
  ARA), FAME 0, FAME -10, HVO (Class II, fob ARA), RED SAF (HEFA-SPK, fob ARA),
  Rapeseed OME. Plus flat/prompt-only series: UCOME fob Strait of Malacca
  (Singapore close), UCOME RED ARA range barge fob, HVO fob ARA range prompt,
  Advanced FAME 0 (barge, prompt — **now a plain outright**, not a diff like
  before), Rapeseed oil fob Dutch Mill (**EUR/t, native — no FX conversion**,
  per trader's explicit choice, since nothing else needs it to spread), POME,
  RED UCO (hydrotreating grade), UCO fob ARA, UCO fob China, SME Argentina
  (Houston close). Five sub-tabs (own `argus-sub-tabs` hidden holder +
  `ARGUS_SUBSECTIONS` grid dict, mirrors the Basis/Hedge Ratios nested-sub-tab
  pattern, **not** the plain single-grid `SUBSECTIONS` pattern):
  - **Calendar Spreads** — M1-M2, M2-M3, M1-M3 for each of the 6 curve products.
    (HEFA-SPK M2 has no data in the sheet at all — a real Argus liquidity gap,
    not a bug — so its M1-M2/M2-M3 calendar spreads show "no data".)
  - **Product Spreads** — **rebuilt 2026-09-24 to cover every pairwise
    combination of the 6 curve products** (all C(6,2)=15 — trader's explicit
    ask, prompted by noticing Rapeseed OME − FAME -10 was missing). Each pair
    is shown exactly once, direction fixed by a precedence order the trader
    specified: **SAF/HEFA-SPK > HVO > UCOME > RME (Rapeseed OME) > FAME -10 >
    FAME 0** — the higher-precedence product is always the minuend, so e.g.
    "HVO vs UCOME" is always HVO − UCOME and the reverse is never separately
    shown. This *flipped* one pre-existing spread's sign: "Rapeseed OME vs
    UCOME" (was RSO_OME − UCOME) is now "UCOME vs Rapeseed OME" (UCOME −
    RSO_OME), since UCOME outranks RME — same underlying key renamed
    `ARGUS_PS_UCOME_RSO_OME`. `PRODUCT_SPREAD_DEFS` in `argus_data.py` is
    still the single source of truth (titles + computation both derive from
    it); `ARGUS_SUBSECTIONS["ar-product"]` in dashboard.py lists the 15 in
    precedence-grouped order (HEFA-SPK's 5 pairs, then HVO's 4, UCOME's 3,
    Rapeseed OME's 2, FAME -10's 1). Plus the spreads outside this 6-product
    rule, unchanged: UCOME vs UCO(China), UCOME vs FCPO, UCO(China) vs FCPO,
    UCOME vs its Malacca and barge prompts (delivery/location basis, added
    2026-09-08), and UCO ARA vs UCO China (geography arb, added 2026-09-08).
    A pair where *neither* leg has a curve (only the UCO arb) produces a
    single flat spread instead of one per maturity. Two legitimate "no data"
    gaps, not bugs: all 5 HEFA-SPK-M2 pairs (the pre-existing HEFA-SPK M2
    liquidity gap noted above) and UCO(China) vs FCPO at all 3 maturities.
    Verified in-process: standalone `fetch_argus()` run confirmed all 15
    pairs compute with internally-consistent values (e.g. HEFA−UCOME =
    HEFA−HVO + HVO−UCOME exactly) and the old reversed
    `ARGUS_PS_RSO_OME_UCOME` key no longer appears; `update_charts("Argus",
    ..., "ar-product", ...)` returned 53 graphs + 8 no-data placeholders
    (61 = 15×3 + 16 non-core, matching expectations exactly). Dashboard
    restarted and confirmed live on port 8055.
  - **Outright Prices** — every raw series except the "diff to gasoil" ones
    (see Biofuel Premium below) and FCPO (not repeated here — see the main
    Outright Prices tab).
  - **Biofuel Premium** (new 2026-09-08) — every raw "diff to Gasoil"/"diff to
    Ice Gasoil" quote (FAME 0, FAME -10, HVO M1, HEFA-SPK M1, Rapeseed OME,
    Soya OME) lives here instead of Outright Prices, per trader's explicit
    instruction, plus a computed **Advanced Premium** = Advanced FAME 0
    (barge, prompt) − UCOME (barge, prompt) — both are the "ARA range barge
    fob prompt" quotes, so it's a same-delivery-point comparison. Has its own
    2 nested sub-sections (`premium-sub-tabs`): **7-28 Day Diff** (the raw
    quotes above) and **Fwd Diff** (added 2026-09-21, see below). **Fwd
    Diff itself got a 4th nesting level, `fwddiff-sub-tabs`, on 2026-09-24
    — Graph | Tabular** (own `NAV_SUBTABS["Argus"]["nested"]["ar-premium"]
    ["nested"]["prem-fwd"]` entry; `build_left_nav`/`_render_nav_level` were
    generalized to recurse to arbitrary depth instead of the old
    hardcoded-2-levels loop, so this pattern is reusable for any future
    sub-tab-under-a-sub-tab). **Graph** renders each of the 6 curve
    products' forward-curve-style chart (`make_argus_fwd_diff_chart`,
    x=M1/M2/M3, one line per year). **Tabular** renders the same 6
    products' companion table instead (`make_argus_fwd_diff_table`) — one
    row per selected year (most recent first), columns M1/M2/M3, exactly
    the same as-of snapshot numbers the chart plots (both share
    `_argus_fwd_diff_snapshot_rows` so they can't drift apart) — per
    trader's explicit ask to read the premium numbers precisely rather than
    eyeballing the chart. **First attempt (same day) stacked chart+table in
    one grid cell inside a `display:flex` div instead of separate tabs —
    caused an infinite y-axis growth loop** (`dcc.Graph`'s `responsive`
    prop defaults to `'auto'`, which forces `height:100%` +
    `ResizeObserver` auto-sizing specifically when the graph's *immediate
    parent* is a flex container; with the table sharing that flex box,
    container and graph height fed back into each other). Trader asked for
    separate tabs instead once the stacked version broke, which sidesteps
    the bug entirely (each product's grid cell holds exactly one `dcc.Graph`
    XOR one `html.Table`, same shape as every other chart in this file, all
    of which sit alone in a `display:grid` cell) — **general rule for this
    file: never put a bare `dcc.Graph` inside a `display:flex` parent
    without a fixed height or `config.responsive=False`.** Verified
    in-process: `update_charts("Argus", ..., "prem-fwd", "fd-graph", ...)`
    → 6 graphs/0 tables, `"fd-tabular"` → 0 graphs/6 tables; left-nav
    checked 4 levels deep (Argus → Biofuel Premium → Fwd Diff → Graph/
    Tabular, correct indent/bold-active state); dashboard restarted and
    confirmed live on port 8055.
  - **Features** — 1) $ Value/CFPP = (FAME -10 − FAME 0)/10, per M1/M2/M3;
    2) $ Value/GHG = (UCOME − FAME 0)/22, per M1/M2/M3; 3) (added 2026-09-14)
    **UCOME Synthetic Cap** = Gasoil + 2×(FAME 0 − Gasoil) [= 2×FAME0 − Gasoil],
    per M1/M2/M3, paired with **"Actual Double Counting Benefit"**
    (`ARGUS_FEAT_SYNCAP_MULTIPLE_M{i}`) — the multiple the market is actually
    implying, reverse-solved from the same formula:
    (UCOME − Gasoil) / (FAME0 − Gasoil). A value of 2 means UCOME prices
    exactly at its synthetic (double-counted) cap; below 2 = cheap to the cap.
    Iterated 2026-09-14: started as a raw-UCOME-price reference chart, then a
    Synthetic-Cap/UCOME ratio, settled on this reverse-solved multiple per
    trader's explicit ask — directly comparable to the "2" used in the cap
    formula. **Denominator floor (2026-09-14):** the multiple divides by
    `FAME0 − Gasoil`, which is unstable when that gap is thin — root-caused
    to a genuinely choppy 3-week stretch in the LGO Gasoil leg (2026-03-18 to
    05-01, likely a front-month contract-roll effect, cross-checked against
    Argus's own "diff to Gasoil" reference which stayed smooth over the same
    dates) that produced swings from -61x to +18x. Masked to NaN whenever
    `abs(FAME0 − Gasoil) < $100/MT` — below that the ratio isn't economically
    meaningful. Real sustained events (e.g. 2022-06, 3-5.5x on a $130-290
    denom) sit well clear of the floor and still show. Gasoil leg is
    **LGOc1/c2/c3** (ICE Gasoil,
    already USD/MT) pulled the same way as the FCPO leg
    (`argus_data._load_gasoil_usdmt()`, joined on exact date), per trader's
    explicit choice over backing Gasoil out of Argus's own prompt-only "diff
    to Gasoil" column — LGO gives a genuine M1/M2/M3 curve instead of one
    prompt value reused across all three maturities.

  **2026-09-21 — 4 new sub-tabs + 2 more premiums** (source sheet gained 15
  columns, 51 total; all 51 now classify — verified via a standalone
  `_classify_header` sweep against the live sheet). 9 sub-tabs total:
  - **Bunker Market** (new) — B24 marine UCOME+VLSFO dob Singapore/Fujairah/
    Guangzhou, B30 dob Houston/Los Angeles/Singapore. Port is parsed from the
    text immediately after "DOB" in the header, not a plain substring scan —
    the header's own timestamp field ("..., Singapore close," / "Houston
    close,") repeats a *different* city (the exchange close time) that would
    otherwise false-match the wrong port.
  - **Rough Margin** (new, per trader's explicit ask) — UCOME/HVO/SAF
    (HEFA-SPK) minus a feedstock reference, per M1/M2/M3: Refined POME
    (Indonesia leg — trader's explicit pick; the sheet also has a Malaysia
    leg, tracked under Outright Prices but not used here) and UCO (China).
  - **Byproduct** (new) — Bionaphtha (UCO-based, fob ARA), Glycerine (crude,
    veg-oil based, cfr China). User also asked for Methanol; **no such column
    exists in the sheet** — flagged rather than fabricated.
  - **Freight** (new) — 2 "Specialised coated IMO2 biodiesel" prompt rates
    (China→ARA, Singapore/Malaysia→ARA, 5kt).
  - **Biofuel Premium** — 2 more diff-to-Gasoil quotes: Advanced FAME 0, and
    a new HVO **Class IV** diff to Ice Gasoil (the sheet added Class IV as a
    separate column from the existing Class II `HVO_DIFF_GASOIL_M1`; fixed a
    latent collision where `_classify_header`'s old `"ADVANCED" in h and
    "FAME 0" in h` branch returned the same key for both the flat Advanced
    FAME 0 outright and its diff-to-Gasoil column).
  - **Outright Prices** ("Other feedstocks") — Refined POME now correctly
    splits into Indonesia and Malaysia (previously the old `"POME" in h`
    branch collapsed all 3 POME-labeled columns — the POME-biodiesel-OME
    product plus both Refined POME feedstock columns — onto one shared key,
    silently dropping 2 of the 3), plus Soybean Oil Argentina (distinct from
    the existing SME Argentina biodiesel column).
  93→124 series. Fetch verified against the live sheet (2026-09-21); dashboard
  restart still pending.

  **2026-10-08 — Tickets sub-tab** (`ar-tickets`, 10th sub-tab; sheet grew to
  67 cols, 9 new): compliance-ticket prices, three labelled sections in order
  **UK → Germany → Netherlands**. UK: Crop-based + Other RTFC (p/RTFC, *monthly*
  snapshot, only 5 pts since 2026-05); Germany: Advanced / Advanced sub-quota
  (EUR/GJ, ~0.2) / Conventional / Other GHG quota (EUR/t CO2e, *Wed* snapshot,
  20 pts); Netherlands: LRE-G / LRE-C / ZRE-G (EUR cent/kg CO2e, Wed snapshot).
  Native units, no conversion. `TICKET_DEFS` in `argus_data.py` is the source of
  truth (keys `ARGUS_TICKET_TKT_*`); plain seasonal-overlay via generic
  `ARGUS_SUBSECTIONS["ar-tickets"]`. History is 2026-only so far, so one line each.

  **2026-10-12 — Blend margin on Tickets tab** (first sections, plus a calc-explainer
  panel `_ticket_calc_block()` above them): ticket value (USD/t of biofuel) −
  product M1 − LGOc1 for all products (BLEND_DIFF_COL = None; was Argus diff for FAME/HVO until 2026-10-12), for FAME(0)/UCOME/HVO × UK/DE/NL. Series
  `ARGUS_BLEND_{FAME|UCOME|HVO}_{UK|DE|NL}` (+ `ARGUS_BLENDTKT_*` ticket value,
  `ARGUS_FX_GBPUSD/EURUSD`). All assumptions (density, MJ/kg, GHG saving, 94/74 g/MJ,
  which ticket pairs with which product, ticket ffill limits) are constants
  at the top of the "Blend margin" block in `argus_data.py` — the panel text reads them.
  New LSEG RICs `GBP=`/`EUR=` (USD per unit) live in `config.RICS["fx_usd"]` (separate
  from `"fx"` so `dcp_gsheet_push` columns are unchanged); backfilled once via a
  targeted pull, daily incremental fetch now includes them. Tickets are held forward
  (UK 35d, DE/NL 8d) — a deliberate exception to the exact-date rule.
  Update 2026-10-12: UK blend charts are in **pence per litre of fuel** (ticket = RTFC/l ×
  RTFC price; premium = USD/t ÷ GBPUSD ÷ litres/t); DE/NL stay USD/t. DE/NL use the
  non-advanced ticket (Conventional / LRE-C) for FAME, UCOME and HVO (UCO = 9B); 9A
  (advanced) tickets are not used. nEHS levy dropped 2026-10-12 (matches expert German 9A). UCOME premium = UCOME M1 − LGOc1 (Argus has no UCOME diff).

  **2026-10-12 — Arb sub-tab** (`ar-arb`, between Tickets and Features): home for
  import-arbitrage models; more arbs get added as new `ARGUS_SUBSECTIONS["ar-arb"]`
  sections. First chart `ARGUS_ARB_BD_ARG_SME` ("BD Arb vs Arg SME", **EUR/t**):
  `(SBO_arg/EURUSD + 139 + 100/EURUSD) × 1.059 − FAME0_M2/EURUSD`. SBO_arg = LSEG
  `BO-ARGUPR-C1` (FOB Up River cash, USD/MT, via `_load_sbo_arg_usdmt()`, column
  `SBO_ARG_LSEG`), EURUSD = LSEG `EUR=`, FAME 0 M2 = Argus. Positive = import route
  dearer than ARA (arb shut); negative = open. Assumptions to revisit: 5.9% duty on
  the whole landed value (oil+conversion+freight), 1 t oil → 1 t SME (no yield
  loss), $100 freight converted at spot EURUSD. Constants `ARB_CONVERSION_EUR`,
  `ARB_FREIGHT_USD`, `ARB_IMPORT_DUTY`, `ARB_FAME_MATURITY` sit above
  `PRODUCT_LABELS` in `argus_data.py`. Series ends at the last Argus sheet date
  (LSEG runs ~2 days ahead). Verified by hand for 2026-10-07: +€31.6/t.
  **Forward curve not available yet**: LSEG has `BO-ARGUPR-P1..P6` (c2..c7) but
  the DataScope "Tick History Cash Quota" is 20/20 so they're refused — needs an
  LSEG quota increase before a c2/c3/c4 curve can be pulled.

  **2026-10-13 — Arb tab rebuilt as a static MIP table** (first a single
  stat tile the same day, then generalized to a quarterly table + current-
  quarter month breakdown per a same-day follow-up ask). No line plot —
  the daily `ARGUS_ARB_BD_ARG_SME` series is still computed in
  `_compute_series` (cheap, kept for history/CSV) but no longer rendered;
  `ARGUS_SUBSECTIONS["ar-arb"]` is now an empty dict and `is_argus and
  argus_sub == "ar-arb"` early-returns `[_argus_arb_tile()]` in
  `update_charts`, bypassing the generic `SUBSECTIONS` grid entirely (same
  early-return idiom as Parity→Ind/China).

  **MIP logic (`argus_data.py`)**: physical SME import pricing is fixed one
  month ahead of the delivery quarter, off the trailing 3-month average up
  to that point — e.g. Q4 (Oct-Dec) is priced off the Jun-Aug average,
  confirmed in September. Generalized as `_quarter_bounds(year, q)`: for a
  quarter starting at calendar month M, the averaging window is calendar
  months `[M-4, M-3, M-2]` and the EURUSD reference is the last trading day
  on/before the end of month `M-2` (same lock-in date as the window's own
  end). `compute_mip_quarter(year, q)` / `compute_mip_table(quarters)` give
  one quarter / many quarters off this window — same
  `ARB_CONVERSION_EUR`/`ARB_FREIGHT_USD`/`ARB_IMPORT_DUTY` constants as the
  daily series: `mip_eur = (avg_SBO/EURUSD + 139 + 100/EURUSD) × 1.059`.
  `current_quarter()` returns the (year, quarter) containing today — the
  "confirmed" quarter whose MIP is live/actionable. Dashboard requests 8
  quarters at startup (`_ARGUS_MIP_QUARTERS_REQUESTED` = 2025 Q1 → 2026 Q4,
  trader's explicit ask) into global `ARGUS_MIP_QUARTERS`/`ARGUS_MIP_CURRENT`
  — computed once, not per-request (`ARGUS_MIP_CURRENT` reuses the row
  already in `ARGUS_MIP_QUARTERS` rather than recomputing, falling back to a
  fresh call only if the running year/quarter ever falls outside the
  hardcoded request list).

  **`_argus_arb_tile()` (`dashboard.py`)** renders three plain `html.Table`s
  (no chart, no interactive input — fully automatic): (1) **MIP history**,
  one row per requested quarter — fixing window, EURUSD reference
  (date+rate), avg SBO Argentina, MIP (EUR/t), current quarter highlighted;
  (2) **SME landed-value waterfall** and (3) **per-month arb comparison**
  for the confirmed quarter — see the 2026-10-14 entry below, which
  replaced an earlier single-table version of (2)/(3) that just showed the
  final MIP number per month without the cost build-up.

  **2026-10-16 — MIP EURUSD = fixing-window average** (trader's ask): `_mip_quarter_from_legs` now
  uses the mean EURUSD over the same fixing window as the SBO average (e.g. Q4 = Jun-Aug avg) instead
  of the last-trading-day spot. `fx_date` now holds a label ("avg start → end"). Q4 2026: EURUSD 1.1504,
  MIP €1,320.9 (was €1,309.4 on spot). Live-EURUSD rows (RME premium, FAME 0 comparison) unchanged.

  **2026-10-14 — line-item waterfall + RME premium, per trader's explicit
  ask** ("current format to see the arb is not good... one row per item
  that goes into it"): the confirmed-quarter section is now two tables
  instead of one. **Waterfall** (`_arb_row()` helper, 3 columns: Item |
  Detail | Amount): avg SBO Argentina → ÷EURUSD → +€139 conversion → +freight÷EURUSD
  → = subtotal (pre-duty) → +5.9% duty → = landed cost (post-duty, same
  number as that quarter's row in table (1)) → **+ RME premium** (new) →
  = **SME landed value (ARA)** (final, bold). RME premium = trader's
  explicit ask: ¼ × the *latest* M1 spread between RME (Rapeseed Methyl
  Ester = Rapeseed OME, `ARGUS_OUT_RSO_OME_M1`) and FAME 0 FOB ARA
  (`ARGUS_OUT_FAME0_M1`) — reuses the existing `ARGUS_PS_RSO_OME_FAME0_M1`
  product-spread series rather than resubtracting, ÷ the **latest** EURUSD
  (`ARGUS_FX_EURUSD`, live) rather than the quarter's locked fixing-date
  EURUSD, since this row is explicitly a live/current overlay, not part of
  the quarter's own historical fixing — it is the only row in the
  waterfall that doesn't use the quarter's own fixing window/FX. Not
  applied retroactively to the 8-quarter history table (1), since "latest"
  by definition only makes sense for the current live comparison.
  **Comparison table** (unchanged columns otherwise, header renamed "MIP"→
  "SME landed value"): still one row per month (Oct/Nov/Dec → M1/M2/M3),
  but now both the comparison's FAME 0 conversion *and* the RME premium
  row use the same live EURUSD (previously the comparison used the
  quarter's locked fixing EURUSD) — kept consistent since the RME premium
  introduced a second, live-data FX basis into the same waterfall; mixing
  the quarter-fixing FX for the comparison while the waterfall's last row
  uses live FX would have been more confusing, not less. Colored **ARB
  OPEN (green, `#1a7f37`/`#e6f4ea`) / ARB CLOSED (red, `#c0392b`/`#fbe9e7`)**
  status label (`_arb_status()`) unchanged — text label always shown
  alongside color, per trader's original color spec. Verified end-to-end
  for Q4 2026 (2026-10-14 data): waterfall €1,011.4 oil + €139.0 conversion
  + €86.1 freight = €1,236.5 subtotal + €73.0 duty = €1,309.4 landed (matches
  table (1)'s Q4 2026 MIP exactly) + €33.6 RME premium (¼ × ($1,670.0 −
  $1,520.0) ÷ 1.1155) = €1,343.0 SME landed value; Oct (M1, FAME 0 $1,520 →
  €1,362.6) diff +€19.6 → ARB OPEN; Nov (M2, $1,484 → €1,330.3) diff −€12.7
  → ARB CLOSED; Dec (M3, $1,467 → €1,315.1) diff −€27.9 → ARB CLOSED. Full
  dashboard restart confirmed live on port 8055.

  **2026-10-14 — 2nd Arb section: China UCOME CIF Malaysia**: trader asked
  for a second, independent arb model, stacked below the SME one (same
  `argus_sub == "ar-arb"` early-return in `update_charts` now returns
  `[_argus_arb_tile(), _argus_china_ucome_arb_tile()]`). Its exact formula:
  `0.60*(China UCOME FOB + 50 freight + 8 trucking + 6 fobbing) +
  0.40*(POME FOB MSIA + 150 net costs) − (POME FOB MSIA + 150 net costs)`
  — algebraically `0.60*(china_route_landed − pome_reference)` but rendered
  as the literal unsimplified waterfall (13 rows, `_arb_row()`, reused from
  the SME tile above) per the trader's same "one row per item" ask. All
  legs already USD/t (no FX conversion needed, unlike the SME arb). **"China
  UCOME FOB" needed a new Argus sheet column** — asked the trader directly
  rather than guessing, since the sheet only had "UCO fob China" (raw
  feedstock) at the time, not UCOME (finished biodiesel); the trader added
  one ("Biodiesel UCOME (used cooking oil) RED bulk China fob prompt,
  Singapore close, midpoint, USD/t, fob", new col 68/69 in the sheet).
  `argus_data.py`: `_classify_header` gained a `"CHINA" in h` check inside
  the `"UCOME" in h` branch (checked *before* the generic month-based
  curve-product fallback, which would otherwise silently return `None` for
  this header since it's a flat/prompt column, not a "swap month N" one) →
  new key `UCOME_CHINA`, added to `FLAT_OUTRIGHT_KEYS` and `PRODUCT_LABELS`
  → emits as `ARGUS_OUT_UCOME_CHINA` (also now listed under Outright Prices
  → "UCOME" alongside the ARA swap curve, automatically, since it's in
  `FLAT_OUTRIGHT_KEYS`). **Needed a fresh standalone `argus_data.py` run**
  to write the new column into the `argus_data.csv` cache before the
  dashboard's `load_argus()` (cache-only read, no live fetch) would see it
  — 186→187 series. Sign convention is the *opposite* of `_arb_status()`:
  `diff = blended_cif − pome_reference`; diff > 0 means the China-blend
  route costs more than pure POME (no incentive) → **ARB CLOSED**; diff < 0
  → **ARB OPEN** — handled inline in `_argus_china_ucome_arb_tile()` rather
  than reusing `_arb_status()`, whose sign meaning is for the opposite
  (FAME0-minus-landed-cost) orientation. Verified end-to-end for
  2026-10-13 data: China UCOME FOB $1,220.0 + 50 + 8 + 6 = $1,284.0 landed
  × 60% = $770.4; POME FOB Malaysia $1,100.0 + 150 = $1,250.0 reference ×
  40% = $500.0; blended CIF = $1,270.4; diff = +$20.4/t → ARB CLOSED
  (matches the algebraic shortcut 0.60×(1,284.0−1,250.0)=$20.4 exactly).
  Full dashboard restart confirmed live on port 8055.

  **2026-10-14 same-day follow-up — net costs 150→175, renamed row, +
  historical chart**: trader corrected the POME net-costs constant to
  **175** (confirmed explicitly used in *both* places it appears — the 40%
  weighted leg and the final unweighted reference subtraction — they're
  the same `POME_MY_NET_COSTS` constant, so this was a one-line change).
  The final blended-value row is now labeled **"= China UCOME CIF Malaysia
  Blended"** (was "= Blended CIF Malaysia value"). Trader also asked for
  "a line graph as you have historical" — added a **daily**
  `ARGUS_ARB_CHINA_UCOME_CIF_MY` series in `argus_data._compute_series`
  (same diff formula, computed for every date both `UCOME_CHINA` and
  `POME_REFINED_MALAYSIA` have a value — both are native Argus sheet
  columns on the same date index, so no LSEG join/lag like the SME arb
  needs) rendered via the standard seasonal-overlay `make_chart()` below
  the waterfall, respecting the tab's own year-picker. **Refactored the
  constants into `argus_data.py`** (`CHINA_UCOME_FREIGHT_MY`/
  `_TRUCKING_MY`/`_FOBBING_MY`, `POME_MY_NET_COSTS`, `CHINA_UCOME_WEIGHT`,
  `POME_MY_WEIGHT` — previously local to `dashboard.py`) so the live
  waterfall and the historical daily series share one source of truth and
  can't drift apart; `_argus_china_ucome_arb_tile()` now takes
  `selected_years` and references them via `_argus_mod.*`. History goes
  back to 2020-02-07 (limited by `POME_REFINED_MALAYSIA`'s start — `UCOME_CHINA`
  itself goes back to 2018). Needed another standalone `python argus_data.py`
  run to write the new series into the cache (187→188 series) before
  `load_argus()` would see it. Verified for 2026-10-13: diff recomputed to
  +$5.4/t (vs +$20.4/t under the old 150 constant) — ARB CLOSED still holds,
  just a smaller margin now that the reference cost is higher. Full
  dashboard restart confirmed live on port 8055.

  **Performance fix along the way**: `compute_mip_table` over 8 quarters
  first called the existing `_load_sbo_arg_usdmt()`/`_load_fx_usd()`
  per-quarter (16 total), each independently re-reading and re-pivoting the
  full LSEG raw CSV (~1M rows post the 2026-09-28 OI backfill) — measured
  at 2+ minutes. Fixed in two layers: (1) `_load_mip_legs()` loads SBO/FX
  **once** and `_mip_quarter_from_legs()` reuses them across all 8 quarters;
  (2) added `@functools.lru_cache(maxsize=1)` module-level memoization
  (`_cached_raw_wide()` / `_cached_converted_wide()`) to the shared
  `process.to_wide(process.load_prices())` call itself, since
  `_load_fcpo_usdmt`/`_load_gasoil_usdmt`/`_load_fx_usd`/`_load_sbo_arg_usdmt`
  each independently triggered it before (this also now makes the
  pre-existing `fetch_argus()` — which already calls 4 of these loaders —
  faster, not just the new MIP table). None of the 4 loaders' callers mutate
  the returned frame in place, so sharing the cached object is safe.
  Verified: `compute_mip_table()` standalone ~43s (one raw-CSV load) vs 2+
  minutes before; a subsequent `fetch_argus()` call in the same process then
  took ~6s (cache hit, no reload).

  **Data-quality fix carried over from the single-tile version**:
  `_load_sbo_arg_usdmt()`/`_load_fx_usd()`'s date index has exactly one
  `NaT` row (unparseable raw trade date), which breaks pandas' `.loc[start:
  end]` range slicing ("non-monotonic DatetimeIndex") — dropped in
  `_load_mip_legs()` (`sbo[sbo.index.notna()].sort_index()`) rather than in
  the shared `process.py`/`_load_*` loaders every other Argus series also
  depends on.

  Verified end-to-end: `compute_mip_table()` for all 8 quarters (values
  sanity-checked by hand, e.g. Q4 2026 €1,309.4/t off a $1,175.0/t avg SBO
  and EURUSD 1.16175 on 2026-08-31), `current_quarter()` → `(2026, 4)`,
  `update_charts("Argus", ..., "ar-arb", ...)` in-process (startup log:
  `Argus MIP: 8/8 quarters priced (current: Q4 2026)`), full dashboard
  restart confirmed live on port 8055.

  **European Margin opex (updated 2026-10-14, trader's explicit call)**:
  landed-cost margins (`EURO_MARGIN_DEFS` in `argus_data.py`) now use opex of
  **$150 UCOME/BD, $250 HVO, $300 SAF** (were $100/$200/$250). RSO→RME stays at
  `RSO_OPEX` = $120. Feedstock freight/duty unchanged (RPOME $150/0%, UCO $75/2%).

  Data module `scripts/argus_data.py`: `fetch_argus()` (live pull,
  keyword-based header classification tolerant to column reordering —
  `_classify_header` checks specific tokens like ADVANCED/MALACCA/BARGE/
  HYDROTREATING before the generic product name they'd otherwise also match —
  computes all derived series, writes tidy `data/processed/argus_data.csv`)
  + `load_argus()` (cache → `{series_key: chart_df}` for the dashboard).
  `CURVE_PRODUCTS`, `FLAT_OUTRIGHT_KEYS`, `PREMIUM_DIFF_LABELS` and
  `PRODUCT_SPREAD_DEFS` are the single source of truth — titles
  (`_build_titles`) and series computation (`_compute_series`) both derive
  from them, so adding a product means editing one list, not two parallel
  ones. FCPO leg reuses `process.load_prices/to_wide/convert_currencies`
  directly (same conversion + ffill-across-holidays convention as every other
  spread) then joins on exact Argus dates — **no as-of/nearest-date
  approximation**, so FCPO-involving series lag ~1 trading day behind
  pure-Argus series (LSEG EOD lag, see §13). Needs the **same
  service-account key** as Tradable Prem (`secrets/gsheet_service_account.json`)
  shared as Viewer on the Argus sheet (`config.ARGUS_SHEET_ID`/`ARGUS_GID`) —
  absent/unshared ⇒ fails soft, tab shows "no data" per chart. Wired into
  `update.py` as a non-critical step (after `process.py`, since it depends on
  `data/raw/all_instruments_raw.csv` for the FCPO leg, and now also the Gasoil
  leg for the Synthetic Cap feature). Verified end-to-end
  2026-09-08: standalone `argus_data.py` run against the rebuilt sheet (87
  series, sensible values, dates current), then a full dashboard restart with
  "Argus: 87 series loaded." in the startup log. (2026-09-14: 93 series after
  adding the Synthetic Cap feature's 6 series — standalone `argus_data.py` run
  verified sensible values; dashboard restart still pending.)

- **Parity** (top-level tab, added 2026-09-21) — 2 sub-tabs (own hidden
  `parity-sub-tabs` holder + `NAV_SUBTABS["Parity"]`, mirrors the Argus
  nested-sub-tab pattern):
  - **Malindo** — exactly the old top-level "Malindo Spreads" tab, just
    relocated. Still driven by the generic `SUBSECTIONS["Malindo Spreads"]`
    grid + the global year-picker (seasonal-overlay idiom, unchanged) — in
    `update_charts`, `groups = SUBSECTIONS.get("Malindo Spreads", {})` when
    `is_parity and parity_sub == "pa-malindo"`.
  - **Ind/China** — a *different* idiom: not seasonal-overlay. Each chart is
    a **forward-curve snapshot** (x = the sheet's own maturity labels, one
    line per row of a small rolling table: Today + D-1..D-4 for "Daily",
    Today + W-1..W-4 for "Weekly") via the new `make_snapshot_curve_chart()`
    — same category-axis idiom as `make_forward_curve_chart` (Calendar
    Spreads → Forward Curve), but the trace dimension is "which snapshot
    row" instead of "which year". Bypasses the generic `SUBSECTIONS` loop
    entirely (`if is_parity and parity_sub == "pa-indchina": ...` near the
    top of `update_charts`, same early-return pattern as the Forward Curve
    sub-tab), so the year-picker/unit-toggle are hidden for this sub-tab
    (`main-controls` display:none) — the Dropdown keeps its default
    `value` under the hood, so `update_charts`'s `if not selected_years`
    guard never fires.
    8 charts total: **China** "Palm Olein vs CSBO" (Daily + Weekly, x =
    Spot/M1-M4), and **India** 3 instruments × Daily/Weekly (Olein Eq vs
    CSBO, Olein Eq vs CSFO Black Sea, CSBO vs CSFO — x = M1-M6 for the
    first, M1-M4 for the other two; the India sheet has **no numeric Spot
    column**, only two date columns, so its x-axis starts at M1 — confirmed
    with the trader before building, not assumed).
    Data module `scripts/parity_data.py`: both sheets are **public** (CSV
    export, no service-account key needed — same pattern as
    `gsheet_data.py`, unlike Tradable Prem/Argus). `fetch_china_parity()` /
    `fetch_india_parity()` parse the sheets' small positional snapshot
    tables (locate the `Date/Nearest/Spot` or `Date/Nearest` sub-header
    rows, read the next 5 rows). India's instrument blocks are found from
    the merged-cell group-header row directly above each sub-header (only
    the first cell of a Google Sheets merge survives CSV export — block
    boundaries come from consecutive non-null cells). `"#DIV/0!"` cells
    (thin-liquidity months) coerce to NaN and are **dropped from that row's
    x/y** — a real gap in the line, not a fake 0. Both sheets are **fetched
    live at dashboard startup** (not part of the `update.py` pipeline,
    same as India Local's `gsheet_data.fetch_india_data()` — restart the
    dashboard to refresh). Verified end-to-end: standalone `parity_data.py`
    run against both live sheets, then `update_charts`/`_toggle_ta_view`/
    `build_left_nav` called directly in-process (8 Ind/China charts, 5
    traces each, sensible values), then a live restart with
    `/_dash-layout` confirmed to contain `Parity`/`parity-sub-tabs`/
    `pa-indchina` and no longer `Malindo Spreads` as a tab label.

- **Margins** — embeds the standalone **plant-margin dashboard** (a self-contained
  Claude-artifact HTML kept in the sibling `Biofuels/Margin/`) inside an `html.Iframe`
  (`id="margins-iframe"`). At startup `prepare_margins_asset()` copies the **newest**
  `plant_margin_dashboard_*.html` into `assets/margin/plant_margin_dashboard.html`,
  injecting `_MARGIN_SHIM` (hides the HTML's own `#tabs` bar, adds a `postMessage`
  listener, and **overrides the app's global `dl()`** so file downloads work — the
  artifact bridge baked into the HTML otherwise hijacks every `<a download>` click and
  forwards it to a non-existent parent frame, silently killing the "Save prices" button).
  The iframe is served by an **explicit Flask route** `@app.server.route("/margins-embed")`
  (NOT the `assets/` URL — see the assets caveat below), so the src is `/margins-embed?v=<date>`.
  The HTML's six internal tabs (Overview/Variables/Yield & Price Theory/Flows/Summary/Rules)
  are surfaced as native Dash sub-tabs (`margins-sub-tabs`, values `mg-*`); a **clientside
  callback** posts `{type:'margins-select',tab:…}` to the single shared iframe so state
  (edited prices, the "Save prices" feature) is preserved. Fails soft — no source file ⇒
  placeholder note. To update: Save a new dated HTML into `Biofuels/Margin/` and restart
  (the `?v=<date>` query busts the browser cache).

- **HVO/SAF Atlas** — embeds the standalone **global HVO / renewable-diesel / SAF plant
  atlas** (self-contained Claude-artifact HTML in the sibling
  `Biofuels/Claude/hvo_saf_global_plant_atlas.html`) in an `html.Iframe`
  (`id="hvo-iframe"`), served by the Flask route `/hvo-embed?v=<mtime>`. At startup
  `prepare_hvo_asset()` makes a **byte-exact** copy into
  `assets/hvo/hvo_saf_global_plant_atlas.html` — **no shim is injected** (unlike Margins):
  the atlas has no internal tab bar, no `<a download>` buttons and no artifact bridge, so
  there is nothing to override. Consequently it has **no sub-tabs** — just a standalone
  content div (`hvo-content`) with its own visibility toggle. **Static by design** — it is
  *not* part of the daily pipeline and only changes when the source HTML is re-saved
  (the `?v=<mtime>` query busts the browser cache on restart). Fails soft — missing source
  ⇒ placeholder note. ⚠️ It loads d3 / topojson / datamaps from **cdnjs**, so the browser
  needs internet access for the map to draw.

Sub-tab wrappers: Cash Premium/Basis → `Prices | Hedge Ratios | Tradable Prem`;
Calendar Spreads → `Calendar Spreads | Forward Curve`; Hedge Ratios → `Tabular | Graphs`;
Margins → `Overview | Variables | Yield & Price Theory | Flows + overwrites | Summary | Rules`.

**Controls:** `year-picker` (multi-year overlay), `ps-contract-picker` (c1/c2/c3/c5/c7 for
product spreads), `unit-toggle` ($/TN vs Local). `make_chart()` is the seasonal-overlay
renderer (x = `month_day` mapped onto year 2000, one trace/year). `load_all_spreads()` loads
every processed CSV into a dict keyed by filename stem.

**Assets:** `assets/oi_hover.js`, `assets/tab_resize.js`.
⚠️ **Assets caveat:** the app runs as a script (`Dash(__name__)`, `__name__=="__main__"`),
so Dash resolves its assets folder to `scripts/assets/` (which does not exist) — meaning
`/assets/*` URLs 404 and these JS helpers do **not** actually load in production. Anything
that must be served should use an explicit `@app.server.route(...)` (as the Margins tab
does) rather than the `assets/` URL. `assets/margin/plant_margin_dashboard.html` is the
shimmed copy of the plant-margin dashboard, regenerated at startup by
`prepare_margins_asset()` and served via the `/margins-embed` Flask route.

---

## 8. External Data Sources (beyond LSEG)

| Source | Module | Notes |
|---|---|---|
| India local edible-oil prices | `gsheet_data.py` | **Public** Google Sheet CSV export. USD/MT + INR/MT. |
| Tradable Premium ("Tradeable Prices Pull", gid 0) | `tradable_prem_data.py` | **Private** sheet → needs service-account key (§9), shared as Viewer. **Redefined 2026-10-09:** per product (CPO/RPO/ROL/RPS/PFAD) premium = raw `Tradable` − raw `Reuters Msia` (Malaysia section) / raw `Reuters Indo` (Indonesia section), USD/MT. Raw columns only (exact header match; smoothed twins ignored), so weekends/holidays have no point. No Spot/Spot+1 leg any more (cache cols: region, product, date, premium; keys `TP_<MY\|ID>_<product>`). |
| Malaysia/Indonesia SND | `snd_data.py` | UOB Malindo Excel (`data/SND/UOBMalindoSnd_MY-ID_SND.xlsx`) for history + public Google Sheet for 2026. |
| Hedge ratios / betas | `hedging_data.py` | Adapter into the **sibling `CashBookHedging/`** project — reads its `cache/prices.parquet` and reuses its beta logic. Cross-project dependency. |
| DCP price push (outbound) | `dcp_gsheet_push.py` | **Writes** DCPc1–c6 settlement (USD/MT) to a Google Sheet daily. Reuses the service-account key with **write** scope; sheet shared as **Editor**. No-ops if `config.DCP_GSHEET_ID` blank or key absent. |
| Event calendar (Calendar tab) | `calendar_data.py` | **Claude Sonnet + web search** compiles the next ~30 days of official commodity/energy/biofuel/positioning/macro release dates → tidy `data/processed/calendar_events.csv`. Runs via the **Claude Code CLI** (`claude -p … --model sonnet --allowedTools WebSearch,WebFetch`, prompt on stdin) so it bills the **Max/Pro subscription**, NOT the (exhausted) `ANTHROPIC_API_KEY` — the fetch drops that env var from the child process so Claude Code uses its OAuth login. Fails soft — missing CLI / failed pull / empty result logs a WARNING and keeps the previous cache. |
| Argus biofuels prices (Argus tab) | `argus_data.py` | **Private** sheet → needs the same service-account key as Tradable Prem (§9). UCOME/FAME 0/FAME -10/HVO/HEFA-SPK swap curves + UCO + FAME 0 diffs, all USD/t; FCPO leg merged in from LSEG raw data. |
| China/India parity snapshots (Parity → Ind/China tab) | `parity_data.py` | **Public** sheets (CSV export, no key needed). China "Palm Olein VS CSBO"; India (same workbook as `gsheet_data.py`, different gid) 3 instruments. Both are rolling Today+D-1..D-4 / Today+W-1..W-4 snapshot tables, not full time series. |
| CFTC Commitments of Traders (CFTC tab) | `cftc_data.py` | **Manual, local files** — no API. User saves CFTC's long-format "Disaggregated...Combined" report pages into `Biofuels/CFTC/<Ag\|Petroleum\|Nat Gas>/*.html` weekly; re-parsed from scratch at every dashboard start (no history CSV). 12 hand-picked products (`RELEVANT_PRODUCTS`) from the vegoil/energy complex; Managed Money net position is the headline metric. |

---

## 9. Credentials & Secrets (all git-ignored, never commit)

- **`credentials.ini`** (project root) — sections:
  - `[credentials]` `username` / `password` → LSEG DataScope (username = numeric LoginId).
  - `[gemini]` `api_key` → AI tab narrative (`ai_analysis.py`, model `gemini-2.0-flash`).
  - Anthropic key (for `doc_extract.py`) — via `anthropic` SDK config / env.
- **`token.json`** — cached LSEG bearer token (auto-managed; safe to delete to force refresh).
- **`secrets/gsheet_service_account.json`** — Google service-account key for the **private**
  Tradable Prem sheet. Absent ⇒ that tab shows "awaiting credential" (no-op, never breaks
  the pull). Setup steps in `secrets/README.txt`. (See user memory `project_lseg_dashboard`.)

---

## 10. AI / Document Intelligence

- **AI tab** (`ai_analysis.py`) — computes 7 quant sections for a chosen instrument/timeframe
  (seasonality, S/R via K-Means, etc.) then calls **Gemini** (`gemini-2.0-flash`) for a
  narrative. Loaded lazily; no-ops cleanly if the gemini key is the placeholder.
- **Doc pipeline** (`doc_extract.py`) — scans `data/documents/inbox/`, classifies
  (broker_commentary / oil_world / energy[Argus]), extracts price+sentiment with **Claude**
  (`anthropic`), writes Parquet to `data/doc_intel/`, consumed by the **News tab**
  (`news_dashboard.py`). Source PDFs/DOCX live under `data/documents/<type>/`.

---

## 11. Directory Map

```
LSEGDashboard/
  config.py            RIC universe, conversions, dates, sheet IDs
  update.py            daily orchestrator
  *.bat                start / update / scheduler
  credentials.ini      LSEG + gemini keys (gitignored)
  token.json           cached LSEG token
  secrets/             gsheet service-account key (gitignored) + README
  scripts/
    auth.py fetch.py process.py          ← core pipeline
    oi_*.py                              ← OI analysis (BMD + Dalian)
    dashboard.py                         ← the Dash app (everything renders here)
    gsheet_data.py tradable_prem_data.py snd_data.py hedging_data.py  ← external loaders
    parity_data.py                       ← Parity/Ind-China China+India sheet snapshots
    cftc_data.py                         ← CFTC tab: parses Biofuels/CFTC/*/*.html (manual weekly saves)
    ai_analysis.py doc_extract.py news_dashboard.py                   ← AI / docs
    bo_options_*.py oi_*_dalian*.py test_*.py  ← ad-hoc research/options scripts (not in app)
  data/
    raw/all_instruments_raw.csv          ← single source pull
    processed/*.csv                      ← spread series (dashboard reads these)
    oi/ options/ SND/ doc_intel/ documents/ ChainCoverage/
  assets/  logs/  output/
    assets/margin/plant_margin_dashboard.html  ← shimmed copy of the Biofuels/Margin HTML (Margins tab)
    assets/hvo/hvo_saf_global_plant_atlas.html ← byte-exact copy of the Biofuels/Claude atlas (HVO/SAF Atlas tab)
```

---

## 12. Conventions & Rules (how to work here)

- **Don't break the seasonal-overlay idiom.** New charts should overlay years unless the user
  asks otherwise.
- **Pipeline before UI.** New series = define in `process.py`, register a `TITLES` entry, add
  to `SUBSECTIONS`. The dashboard only ever reads `data/processed/`.
- **Always normalise to USD/MT** for spreads; keep native units only where a raw calc (crush)
  or the Local toggle needs them. Update `SPREAD_LOCAL_UNITS` / `OUTRIGHT_UNITS` together.
- **Non-critical sources fail soft** — keep the try/except-and-WARNING pattern in `update.py`
  so a broken sheet/key never blocks the price pull.
- **Field names** for the LSEG API: plain `Open/High/Low/Last/Settlement Price` — never
  `Open Price`/`Close`.
- **After a code change that affects data, re-run `update.bat`** (or just `process.py`) and
  restart the app — the dashboard caches at startup.
- **Windows-only paths** hardcoded in `.bat` files (`C:\Users\Algo.Trader\...` +
  Python310). Keep them consistent if the folder moves.
- Ad-hoc `scripts/test_*.py`, `bo_options_*.py`, `regime_chart.py`, `crush_corr.py` are
  research/one-off tools, **not wired into the dashboard** — safe to ignore for app work.

---

## 13. Known Problems / Gotchas (history)

- **Anthropic credit exhaustion** — `doc_extract.py` recently failed every doc with
  HTTP 400 *"credit balance is too low"* (see `logs/update_log.txt`, 2026-06-22). News-tab
  intelligence is stale until the Anthropic account is topped up. It fails soft, so the rest
  of the update still completes.
- **Tradable Prem tab** needs the service-account key (`secrets/gsheet_service_account.json`).
  Without it the tab is blank ("awaiting credential").
- **Incremental merge edge case** — a holiday stretch with no trading days returns no rows;
  `fetch.py` keeps the existing CSV rather than wiping it. Don't "fix" this.
- **`update.bat` failure branch has a `pause`** — a failed run leaves a zombie `cmd.exe`
  hung forever waiting on the prompt (the scheduled task never exits). `fetch.py` also has
  **no retry**. The `pause` should be removed; until then, check for orphaned cmd windows.
- **LSEG EOD data lags ~1 trading day** — today's settlement/OI typically isn't available
  until the next day's pull. Don't expect same-day numbers.
- **Token 401s** mid-extraction → delete `token.json` and re-run.
- **CashBookHedging coupling** — the Hedge Ratios tab depends on the sibling project's
  `cache/prices.parquet` being fresh (refreshed by *its* `run_daily.bat`). If betas look
  stale, check that project, not this one.
- **Dashboard must be restarted to pick up new processed data** — it is not hot-reloading in
  production (`debug=False`, launched via `pythonw.exe`).
- **Never put a bare `dcc.Graph` inside a `display:flex` parent** without either a fixed
  parent height or explicit `config={"responsive": False}`. `dcc.Graph`'s `responsive` prop
  defaults to `'auto'`, which forces `height:100%` + a `ResizeObserver` specifically when the
  graph's *immediate* parent is a flex container — if a sibling in that same flex box also
  affects the container's height (e.g. a table stacked under the chart), the graph and
  container heights feed back into each other and the chart grows without bound on every
  render. Hit 2026-09-24 stacking a Fwd Diff chart+table in one flex div (see §7, Biofuel
  Premium); fixed by using separate Graph/Tabular sub-tabs instead so each grid cell holds
  exactly one `dcc.Graph` or one table, same as every other chart in this file (all sit alone
  in a `display:grid` cell, which never triggers `'auto'` responsive mode).

---

*Last updated: 2026-10-14 (China UCOME CIF Malaysia arb: net-costs constant
corrected 150→175 (`argus_data.POME_MY_NET_COSTS`, used consistently in both
the 40% weighted leg and the final reference), final row relabeled "= China
UCOME CIF Malaysia Blended", and a **historical seasonal-overlay chart**
added below the waterfall — new daily `ARGUS_ARB_CHINA_UCOME_CIF_MY` series
in `argus_data._compute_series` (both legs are native Argus columns, no
LSEG join/lag), rendered via the standard `make_chart()`, respecting the
tab's year-picker. Constants moved from `dashboard.py` into `argus_data.py`
so the live waterfall and the historical series share one source of truth.
Earlier same day: Argus → Arb tab gained a **2nd section, China
UCOME CIF Malaysia** — a blended-CIF waterfall (60% China UCOME import
route + 40% POME-fob-Malaysia route) vs. the pure-POME reference, trader's
exact formula, 13 rows via the shared `_arb_row()` helper. Needed a new
Argus sheet column (`UCOME_CHINA`, "Biodiesel UCOME ... RED bulk China fob
prompt") — asked the trader rather than guessing since the sheet only had
raw UCO (feedstock), not UCOME (finished biodiesel), for China; the trader
added the column. Sign convention is inverted vs. the SME arb's
`_arb_status()` — handled inline. Same-day, earlier still: the SME arb's
confirmed-quarter section was rebuilt as a **line-item waterfall**, per
trader's explicit ask for visibility into the calculation — one row per
cost component (oil, conversion, freight, duty) ending in a new **RME
premium** row: ¼ × the latest RME (Rapeseed Methyl Ester)-vs-FAME-0 M1
spread ÷ the latest EURUSD, the only row using live rather than
quarter-fixing data = "SME landed value (ARA)". The per-month comparison
table against Argus FAME 0 (M1/M2/M3 → the quarter's 3 months) now also
converts via live EURUSD for consistency with that new row. Full detail in
§7.
Previous update — 2026-10-13 (Argus → Arb tab rebuilt as a **quarterly MIP
table** — no chart, no manual input. `argus_data.compute_mip_quarter`/
`compute_mip_table` generalize the SME-landed-into-ARA MIP formula to any
quarter (3-month fixing window ending 2 months before the quarter starts,
e.g. Q4 off the Jun-Aug average) and `current_quarter()` finds today's
quarter. Dashboard shows 8 quarters (2025 Q1 → 2026 Q4) plus a per-month
breakdown of the current/confirmed quarter against the live Argus FAME 0
curve (month 1/2/3 → M1/M2/M3), colored green "ARB OPEN" / red "ARB
CLOSED". Also fixed a 2+-minute startup slowdown this surfaced — added
`@functools.lru_cache` to the shared `process.to_wide(process.load_prices())`
call so the 4 Argus loaders that all needed it (FCPO/Gasoil/FX/SBO legs)
stop re-reading the ~1M-row raw CSV independently. Full detail in §7.).
Previous update — 2026-10-06 (new top-level **CFTC** tab: Commitments of Traders
spec positioning for the vegoil/biofuels complex, parsed from CFTC long-format
report pages the user saves manually each week into `Biofuels/CFTC/<Ag|
Petroleum|Nat Gas>/*.html` — no API, no pipeline wiring, re-parsed fresh at
every dashboard start via new `scripts/cftc_data.py`. 12 hand-picked products
by CFTC code; headline metric is Managed Money net position (Long−Short) —
weekly-change bar, %-of-OI bar, per-product net-position history small
multiples, and a snapshot table. Full detail in §7.).
Previous update — 2026-09-28 (5th same-day follow-up: OI/Vol % got a 2nd
"notable" tier — 10-100% shades the whole row amber (`#fff6d9`) to draw
attention to a real, large fresh-positioning day, separate from the >100%
muted "likely data anomaly" tier. Reordered the tab: DCP Palm Olein/Dalian
Soybean Oil pushed up to positions 4-5, right after
FCPO/BO/LGO. Full detail in §7.).
Previous update — 2026-09-28 (4th same-day follow-up: added **OI/Vol %** column
(Delta OI ÷ Total Volume, whole %) — flags/mutes |ratio|>100% as a likely
data anomaly rather than showing it at face value (rendered "–" in light
gray, real value in the hover tooltip). Switched **Avg Px to each
instrument's native currency/unit** (MYR/MT, cents/lb, USD/gallon, etc. —
reusing the existing `SPREAD_LOCAL_UNITS` dict) instead of converting
everything to USD/MT; `oi_cumulative.py`'s USD/MT conversion code was
removed outright. 8 columns total now: Date | Total OI | Delta OI | Total
Volume | Delta Volume | OI/Vol % | Avg Px (native unit) | Px Chg. Full
detail in §7.).
Previous update — 2026-09-28 (3rd same-day follow-up: **Individual sub-tab
removed entirely** — OI/Volume is now a single flat tab, no sub-tabs; its
old data (`oi_analysis.csv`/`oi_summary.csv`) is still produced by the
pipeline but no longer read by `dashboard.py`. Added **Total Volume/Delta
Volume** columns (sum across the same extended contract range as Total OI)
— 7 columns total now: Date | Total OI | Delta OI | Total Volume | Delta
Volume | Avg Px (USD/MT) | Px Chg. Display order changed to lead with FCPO
Palm Oil/CBOT Soybean Oil/ICE Gas Oil per trader's ask
(`OI_CUMULATIVE_LAYOUT_ORDER`); layout reverted from a fixed 2-column grid
back to flex-wrap so more tables pack per row on wide screens. Full detail
in §7.).
Previous update — 2026-09-28 (same-day follow-up: Cumulative sub-tab extended
from 6 to **10 instruments** — added NYMEX Heating Oil, NYMEX WTI, ICE
Brent, Dalian Soybean Oil, ranges probed out to c87/c74 since WTI/Brent
carry real OI on far-dated December strips; display names given per trader's
ask (`OI_CUMULATIVE_DISPLAY_NAMES`); tables laid out in an explicit 2-column
grid in trader-specified pairs (`OI_CUMULATIVE_LAYOUT_ORDER`); arrow
thresholds switched from one fixed 1%/2% cutoff to **per-instrument**
median/75th-percentile of trailing-3yr abs % move
(`oi_arrow_thresholds.csv`); Avg Px now whole-number; 244 more RICs
backfilled (345 total, up from 101) via the same resumable
`oi_extra_backfill.py`; FCPO's 2026-09-25 Total OI jump cross-checked and
confirmed to be real per-contract LSEG data, not a summing bug. Full detail
in §7.).
Previous update — 2026-09-28 ("OI Analysis" renamed **OI/Volume**, split into
Individual (unchanged) | Cumulative (new) sub-tabs — Total OI summed across
every live contract month, not just c1-c7/c12, per product-specific ranges in
`config.OI_CUMULATIVE_RANGE` (FCPO 17, DCP 12, BO 18, SM 18, S 17, LGO 40,
found via a one-time c1-c24/c1-c48 probe pull); new pipeline step
`scripts/oi_cumulative.py`; 75 new RICs backfilled via the resumable
`scripts/oi_extra_backfill.py` (yearly chunks) after a single full-176-RIC
`fetch.py --full` pull hit the known large-pull `ReadTimeout`. Full detail
in §7.).
Previous update
2026-09-24 (Argus → Biofuel Premium → Fwd Diff got its own
4th-level `fwddiff-sub-tabs` nesting — Graph | Tabular — after a first
attempt at showing both at once via a flex-stacked div hit the infinite
resize-loop gotcha now documented in §13; `build_left_nav`/`_render_nav_level`
generalized to recurse to arbitrary sub-tab depth. Full detail in §7.).
Previous update
2026-09-21 (new top-level **Parity** tab: 2 sub-tabs — Malindo
(the old top-level "Malindo Spreads" tab, relocated, data/logic unchanged)
and Ind/China (new forward-curve-snapshot idiom, 8 charts from 2 public
Google Sheets via new `scripts/parity_data.py` — China "Palm Olein vs CSBO"
+ India 3-instrument parity, both Daily/Weekly). Same-day: Argus tab
extended for 15 new sheet columns — 4 new
sub-tabs: Bunker Market, Rough Margin, Byproduct, Freight; 2 more Biofuel
Premium quotes (Advanced FAME 0, HVO Class IV) diff-to-Gasoil; Outright
Prices gained Refined POME Indonesia/Malaysia + Soybean Oil Argentina; fixed
2 latent `_classify_header` key collisions along the way (Advanced FAME 0
outright vs. its diff-to-Gasoil column; the 3 POME-labeled columns sharing
one key). 9 sub-tabs total, 93→124 series. Previous update 2026-09-08: Argus
tab rebuilt — root cause of the staleness was an upstream Excel layout
corruption in the Drive→Apps-Script→Sheet sync, not this repo; user rebuilt
the source Excel, `argus_data.py` rewritten for its new 34-column layout: 6
curve products, new Malacca/barge/feedstock prompt series, and a new Biofuel
Premium sub-tab for the diff-to-gasoil quotes, 5 sub-tabs total). Update this
file whenever tabs, RICs, the pipeline, or credentials change.*
