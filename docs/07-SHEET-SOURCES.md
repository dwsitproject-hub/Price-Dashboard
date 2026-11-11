# 07 — Google Sheet sources: review and mapping

Review of the four workbooks supplied on 2026-11-11 for **Waste/Bio, Basis/Hedge Ratios, Product Spreads and SND**,
read in place (no copies kept), compared with the legacy readers in `legacy/scripts/` and mapped to the new app.

| # | Workbook | ID | Linked tab (gid) | Access | Legacy reader |
|---|---|---|---|---|---|
| A | KPN Dws – Market Price Report | `1josHJfUqq7b_PSMoIWF3gwFkFRRLUFNlYT9aSPdoMAY` | Market Price (750767412) | **Private** (CSV export → 401) | none (new) |
| B | China-Parity Sheet | `16r7vkke3Kc-TVB4vKsJI01ndTRxIeiaO4i6FoHYfhE4` | Dashboard Data (466961326) | Public link (CSV 200) | `parity_data.fetch_china_parity` |
| C | India-Parity Sheet.xlsx | `1tY63YWHUm8LHEKUJAtObl4zsIiAyeVmZ` | Calculation (741962089) | Public link (CSV 200) | `gsheet_data.py` (gid 998039497), `parity_data.fetch_india_parity` (gid 1151879540) |
| D | Argus Historical Prices Link | `1VfaXyEbok2X4rxn4W4tGekWIg_IiRpOjTtT4zyzgksc` | Prices (78291884) | **Private** (CSV export → 401) | `argus_data.py` (service account) |

None of the four contains Malaysia/Indonesia/India supply-and-demand balances. **SND keeps its current admin-upload
sources** (§6) until an SND workbook is supplied.

---

## 1. Workbook A — KPN Dws Market Price Report → Basis / Hedge Ratios

43 tabs. Three are dashboard data; the rest are Indonesian levy/tax/HPE working sheets.

### 1.1 `Market Price` (linked tab) — 149 columns, one row per calendar day, 1 Jan 2026 → today

Filled on trading days (≈ 220 rows so far); rows to Jul 2027 are pre-dated blanks. Prices are USD/MT unless the
header says IDR/MYR/CNY. Column groups (0-based index):

| Cols | Group | Series |
|---|---|---|
| 1–4 | FX | USDIDR, JISDOR, USDMYR, USDCNY (USDCNY empty) |
| 5–15 | Indonesia tender | KPB CPO, Astra PK, est. CPKO, SJIM CPKO, KPB CPKO — IDR and USD |
| 16–21 | BMD FCPO | spot / 2nd / 3rd, MYR and USD |
| 22–32 | Malaysia (Dow Jones–Winquote) FOB | spot RPO, ROL, RPS, PFAD, CPKO, PKO; M+2, M+3 RPO/ROL |
| 33–40, 124–129 | Malaysia (Reuters) FOB | spot CPO, RPO, ROL, RPS, PFAD, CPKO, RPKO; PFAD ask; M+1 CPO…PFAD |
| 41–44 | Rotterdam (Reuters) | CPO, CPKO FOB and CIF |
| 45–53, 132–136 | **Aastar Tradable Px** | spot CPO, RPO, ROL, RPS, PFAD, COL, CPS, ROL packed, FAME; M+1 CPO…PFAD |
| 54–61 | Palm local | ROL bulk USD/IDR, cooking-oil pack increments |
| 62–66 | Lauric | CPKO, RPKO, PKFAD, PKE, PKM |
| 67–71 | DCE | olein spot/2nd (CNY, USD) |
| 72–77 | Spreads | CBOT soyoil + **POBO**, Singapore gasoil + **POGO** |
| 78–84 | Local olein (IDR) | Jambi, Palembang, Lampung, Jabodetabek, Semarang, Surabaya, Pontianak |
| 85 | ICDX | CPOLDI |
| 86–99 | Indonesia (Reuters) | spot and M+1 CPO, RPO, ROL, RPS, PFAD, CPKO, RPKO |
| 100–114 | Oleochemical tradeables | IEPO, IEPS, SPOFA, PA85, C16-18, BDFA, light/heavy end, oleic, stearic, IEPKO, CG, RG, SRPKOFA, FM |
| 115–116 | Waste tradeables | RUCO, UCOFAD |
| 117–123 | Lauric/shortening | Rotterdam CNO FOB/CIF; shortening hard/soft/liquid |
| 130–131 | Biodiesel | FAME HIP BBN, KPB + 85 |
| 138–145 | Other | Argus Shell, ICIS CG CIF China, RG FOB SEA, methanol, POME discount/price (IDR, USD) |

### 1.2 `Premiums` — Tradable premium, 1 Jan → 11 Nov 2026, every calendar day (weekends carried)

For each product (CPO, RPO, ROL, RPS, PFAD, …): **Reuters Indo · Reuters Msia · Tradable**, each as raw, smoothed and
premium, plus *Levy + Duty* and *Local Tradable*. This is the same layout `tradable_prem_data._locate_columns` parses
(product on the row above, "Reuters Indo / Reuters Msia / Tradable" header, first raw match wins). The legacy reader
pointed at a different workbook (`1KBoMLaZa…`, gid 0); this tab is its upstream.

### 1.3 `Argus` — UCO FOB MY, RUCO Indo FOB, Argus refined POME and POME RED Malaysia, Shell, coal; Nov 2024 → Nov 2026

### 1.4 Not mapped (policy working sheets)

Zero Levy, SH/BM year sheets, Daily Levy Tax, Levy Tax Table/Band/Smry, Daily HPE, HPE2024, HPE Forecast/Movt,
HIP BBN (+ variants), KPB Astra, Agrinas, Msia Export Tax, ICDX Trd, LD Inv/Carry, Monthly Summary, scratch sheets.
They hold Indonesian export levy, duty and reference-price calculations rather than market series. A later
"Levy & Duty" view could use them, but nothing in the legacy app does.

### 1.5 Mapping

| App place | Source | Series |
|---|---|---|
| Basis/Hedge Ratios → **Tradable Prem** (new sub-tab) | `Premiums` | Premium = Tradable − Reuters raw, Malaysia and Indonesia, for CPO, RPO, ROL, RPS, PFAD (legacy `TRADABLE_PREM_PRODUCTS`); legacy outlier rule (±25 % vs 5-print median) kept. Seasonal overlay. **Only 2026 is on this tab.** |
| Basis/Hedge Ratios → **Prices** | existing LSEG series (`basis_premiums`) | unchanged |
| Basis/Hedge Ratios → **Hedge Ratios** (new sub-tab: Tabular, Graphs) | LSEG cash M1/M2 vs FCPOc3 (already in DB) + waste legs: *CPO-KPB (PTP)* ← `Market Price` col 11 (KPB CPO USD); *RPOME (Argus)* ← Workbook D col 35 | Rolling betas, 30/60-day windows, 3M/6M/1Y graphs. **Blocked: the formulas live in `CashBookHedging/hedging.py` + `waste_loader.py`, which are not in this repo.** |
| Outright Prices → new groups (optional) | `Market Price` | Aastar Tradable spot/M+1, Indonesia local olein by city, oleo and waste tradeables, lauric |

---

## 2. Workbook B — China-Parity → Parity · Ind/China + Product Spreads (China)

23 tabs. The linked tab is a snapshot; the history tabs are proper daily time series.

### 2.1 `Dashboard Data` (linked) — snapshot, refreshed daily

Two instruments, each with a **daily** block (Today, D-1…D-4) and a **weekly** block (Today, W-1…W-4), columns
`Label | Date | Nearest | Spot | M1 | M2 | M3 | M4`, plus week-on-week change tables on the right:

1. **POBO** (average of North, Middle and South China)
2. **Palm Olein Import Margin (South China)**

> **Legacy defect found.** `fetch_china_parity` takes the first two `Date/Nearest/Spot` header rows as daily and
> weekly. With the reshaped tab those are POBO daily/weekly, which it titles "Palm Olein vs CSBO"; the import-margin
> block is never read. The port must read every block and take the title from the line above each header.

### 2.2 History tabs (daily, Jan 2022 → 10 Nov 2026)

| Tab | Columns |
|---|---|
| Relative Pricing | Palm spot North/East/South China; DCE palm M1–M4; soyoil spot N/E/S; DCE soyoil M1–M4; custom FX; … (25 cols) |
| Palm Import Parity | Palm CNF (most active); spot CNY and USD by region; FX, VAT, duty, port cost; DCE main; landed cost; landed vs DCE; basis by region; import margin (37 cols) |
| Soyoil Import Parity | Brazil and Argentina CNF; spot; FX, duty, VAT, port cost; landed cost; Brazil and Argentina import margin |
| Cross-oil Spread | DCE palm, SBO, RSO main; **POBO, PORSO, BORSO** (CNY/t) |
| DCE Palm / Soyoil Processed | M1–M5 |
| China Palm CNF, China Soyoil Spot, Arg/Braz Soyoil CNF, ZCE RSO, Daily USD/CNY | inputs |

### 2.3 Mapping

| App place | Source | Series |
|---|---|---|
| Parity → **Ind/China** (new sub-tab next to Malindo), China half | `Dashboard Data` | Forward-curve snapshot charts (x = Spot…M4, one line per row), daily and weekly, for POBO and the South China olein import margin; WoW table |
| Product Spreads → new group **China** | `Cross-oil Spread`, `Palm Import Parity`, `Soyoil Import Parity` | DCE POBO, PORSO, BORSO; palm import margin; Brazil/Argentina soyoil import margin; palm basis by region. Seasonal overlay from 2022 |

---

## 3. Workbook C — India-Parity (xlsx on Drive) → Product Spreads · India + Parity · Ind/China

18 tabs. Three matter.

### 3.1 `Calculation` (linked, gid 741962089)

* Left side (A:F): one 25-row costing block per date (515 dates, 2 Oct 2024 → 10 Nov 2026): customs FX, HS cargo
  price, tariff value, BCD/AIDC/SWS duty, CNF offer, port expense, landed cost, import margin (INR and USD), refining
  cost, local refined price, processing margin — for RBD olein, CPO, CDSBO, CSFO, PFAD.
* Right side (H:AA): **tidy daily table, 515 rows**, USD/MT:
  CPO (CNF, Ref Parity, HS, Parity) · RBD Olein (CNF, Ref Parity) · CDSBO (CNF, Ref Parity, HS, Parity) ·
  CSFO (CIF, Ref Parity, HS, Parity) · PFAD (CNF, Parity) · Duty-paid CPO, CDSBO and CDSBO − CPO.

### 3.2 gid 998039497 — India local prices (legacy India Local source)

Date, FX, imported CPO refined to olein, olein and its spread, refined soyoil, sunflower, groundnut, rapeseed,
cottonseed and rice-bran oil with their spreads; ≈ 395 daily rows. Legacy builds 6 outrights and 15 pairwise spreads.

### 3.3 `Dashboard Data` (gid 1151879540) — India parity snapshot

Olein Eq − SBO (M1–M6), Olein Eq − SFO Black Sea (M1–M4), CSBO − CSFO (M1–M4); daily and weekly blocks. Matches the
legacy parser, including the "CSBO → SBO" rename aliases. `#DIV/0!` cells are gaps.

### 3.4 Mapping

| App place | Source | Series |
|---|---|---|
| Product Spreads → group **India Local** | gid 998039497 | 15 pairwise spreads (legacy keys `INDIA_*_vs_*`) |
| Outright Prices → group **India Local** | gid 998039497 | 6 outrights (legacy `INDIA_OUTRIGHT_KEYS`) |
| Product Spreads → new group **India Import Parity** | `Calculation` H:AA | Refining parity and HS parity for CPO, CDSBO, CSFO, PFAD; olein ref parity; duty-paid CDSBO − CPO. Seasonal from Oct 2024 |
| Parity → **Ind/China**, India half | `Dashboard Data` | 3 forward-curve snapshot charts, daily and weekly |

---

## 4. Workbook D — Argus Historical Prices → Waste/Bio

Two tabs; `Prices` is the data. Row 0 is the Argus copyright and timestamp; row 1 holds the full Argus descriptions;
dates are dd/mm/yyyy from 10 Sep 2007. 75 series.

### 4.1 Coverage vs the legacy reader (`argus_data.py`, matches by header text)

| Status | Columns |
|---|---|
| Read by legacy | UCOME (Malacca prompt; ARA swap M1–M3; barge prompt; China bulk), HVO Class II (M1–M3, prompt, diff M1), HVO Class IV diff, RED SAF HEFA (M1–M3, diff), FAME 0 and FAME −10 (M1–M3, diffs), Advanced FAME 0 (prompt, diff), RSO Dutch mill (EUR), RME (M1–M3, diff), Soya OME diff, POME OME barge, RED UCO hydrotreating, UCO ARA, UCO China, SME Argentina, refined POME Indonesia and Malaysia, SBO Argentina, B24/B30 bunkers (6 ports), bionaphtha UCO, glycerine China, freight China/SEA → ARA, UK/DE/NL tickets |
| **New, not read** | 69 CARB CCA (USc/USG) · 70 LCFS soy CI · 71 LCFS UCO CI · 72 US Gulf crude degummed SBO (USc/lb) · 73 US West Coast RBD SBO (USc/lb) · 74 D4 RIN (USc/RIN) · 75 B99 NYH barge (USc/USG) |
| Duplicates | 52–57 repeat FAME 0 / FAME −10 M1–M3 (14–19); 49–50 repeat 10/9; 67 repeats 23 as a "diff midpoint". First match wins, as in legacy |
| Stale | 32 UCO China last print 30 Sep 2026; 44–45 freight last print 20 Jun 2025; tickets are weekly/monthly snapshots (2026 only) |

### 4.2 Mapping — new module **Waste/Bio** (legacy "Argus" tab, same sub-tabs)

| Sub-tab | Content (legacy formulas, ported as-is) |
|---|---|
| Calendar Spreads → Historical / Forward Curve / YTD | M1−M2, M2−M3, M1−M3 for UCOME, FAME 0, FAME −10, HVO, HEFA, RME |
| Product Spreads → Seasonal / Forward Curve | cross-product spreads per maturity |
| Outright Prices | swap curves + flat prompt series |
| Biofuel Premium → 7-28 Day Diff / Fwd Diff (Graph, Tabular) | "diff to gasoil" quotes; product M1 − LGOc1 |
| Bunker Market | B24/B30 by port |
| European Margin | RPOME/UCO → UCOME/HVO/SAF and RSO → RME landed-cost margins (freight, duty, opex constants) |
| Byproduct · Freight · Tickets | bionaphtha, glycerine · freight · UK/DE/NL tickets + blend margin |
| Arb | Arg SME → ARA arb, quarterly MIP table, China UCOME CIF Malaysia arb |
| Features | legacy feature view |
| **US Compliance** (proposed, new) | columns 69–75 as outrights in native units; LCFS/RIN/CCA have no conversion |

LSEG inputs the formulas need are already in the catalog: `LGOc1–c3`, `EUR=`, `GBP=`, `BO-ARGUPR-C1`.

---

## 5. Implementation (built 2026-11-11)

| Workbook | Read by | Tabs (gid) | Series | Where |
|---|---|---|---|---|
| A — KPN Market Price | Sheets API (service account), else uploaded .xlsx | Premiums (221189267) | 10 `TP_<MY/ID>_<product>` | Basis / Hedge Ratios → **Tradable Prem** |
| B — China-Parity | public CSV export | Dashboard Data (466961326), Cross-oil Spread (1329015366), Palm Import Parity (674600137), Soyoil Import Parity (809669896) | 11 `CHINA_*` + snapshot | Product Spreads → China — DCE cross-oil / import margins / palm basis; Parity → **Ind/China** |
| C — India-Parity | public CSV export | data2 (998039497), Dashboard Data (1151879540), Calculation (741962089) | 40 `INDIA_*` + snapshot | Outright Prices → India Local / India Import; Product Spreads → India Local / India Import Parity; Parity → **Ind/China** |
| D — Argus | Sheets API (service account), else uploaded .xlsx | Prices (78291884) | 204 `ARGUS_*` | **Waste / Bio** (12 sub-sections, forward curves, YTD, Fwd Diff table, Arb, Tickets calc) |

- Job `sheet_refresh` (worker): after every successful pipeline run, on `SHEETS_CRON` (default weekdays 08:30
  WIB), on first start, and from **Administration → Sheet sources** (per workbook or all). A failing workbook keeps
  its previous data and records the error; the others still load.
- Verified on 2026-11-11 against the live public sheets: India local prices and India parity are value-for-value
  identical to the legacy readers; the China snapshot's first instrument equals the legacy output. Argus,
  Tradable Prem and the MIP table are golden-tested against the legacy functions on synthetic grids
  (`backend/tests/test_sheets.py`).
- Code: `backend/app/sheets/`, `backend/app/integrations/gsheets.py`, migration `0007_sheets`; UI:
  `frontend/src/pages/WasteBioPages.tsx`, `ParitySnapshotPage.tsx`, `admin/SheetSourcesPage.tsx`.
- Defects of the legacy fixed in the port (TD §11B): the LCFS-UCO credit overwrote UCO fob ARA; "UCO (China) vs
  FCPO" chart was always empty; the China snapshot mislabelled POBO and dropped the import-margin blocks.

## 6. Open items

1. **Service account** for A and D: create it, share both workbooks with its e-mail (Viewer), paste the key under
   Administration → Sheet sources (staging guide §17.1). Until then those two show "skipped"; an .xlsx upload works
   as a fallback.
2. **Tradable Prem history**: the `Premiums` tab starts 1 Jan 2026. Is 2024–2025 still in the old
   `1KBoMLaZa…` workbook, and should it be backfilled once?
3. **Hedge Ratios** needs `CashBookHedging/hedging.py`, `waste_loader.py` and `config.py`, which are not in this
   repo. Its waste legs map to Market Price col 11 (KPB CPO USD) and Argus refined POME Indonesia.
4. **SND**: none of the four links is an SND workbook; the upload flow stays.
5. Optional, not built: Market Price outrights (Aastar tradable spot/M+1, local olein by city, oleo and waste
   tradeables), the Indonesian levy / HPE tabs, the China forward import margin M1–M5.
