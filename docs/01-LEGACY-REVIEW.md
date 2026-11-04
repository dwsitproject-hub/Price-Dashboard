# 01 — Legacy Review & Critique

| | |
|---|---|
| **Subject** | `CLAUDE.md` (legacy project guide, 1,128 lines / ~80 KB) and the code it describes (`scripts/`, `config.py`, `update.py`, `*.bat`) |
| **Repository** | `dwsitproject-hub/Price-Dashboard`, branch `main`, commit `839c90a` |
| **Reviewed on** | 2026-11-04 |
| **Purpose** | Understand the application completely, assess production readiness, and set the baseline for the rebuild (PRD → TD → Plan) |

The original guide is preserved unchanged at [`docs/legacy/CLAUDE-legacy.md`](legacy/CLAUDE-legacy.md).

---

## 1. What the application is

AASTAR Price Dashboard (legacy name *LSEGDashboard*) is a **market-intelligence dashboard for the edible-oils /
vegoil / biofuels complex**, used by a commodity trader to analyse palm-oil spreads (FCPO, DCP, BO, LGO) and the
wider soy / energy complex.

| Layer | What it does |
|---|---|
| **Ingest** | Pulls daily Last / Settlement / Bid / Ask / Volume / Open Interest for **345 RICs** from **LSEG DataScope Select** (REST, async extraction). Incremental 7-day window, merged into one raw CSV (~1 M rows). |
| **Normalise** | Converts every price to **USD/MT** (MYR÷`MYR=`, CNY÷`CNY=`, cents/lb×22.0462, cents/bu×0.3674, USD/st×1.1023, USD/gal×264.172, USD/bbl×6.2898). |
| **Compute** | ~180 derived series: calendar spreads, product spreads (14 pairs × 5 tenors), Malaysia/Indonesia cash premiums vs FCPO c3, Malindo, cash product spreads, CBOT and SA crush, US/SA/China basis, cross-geography spreads, CME Oilshare, synthetic FOB Malaysia CPO, OI cumulative tables. |
| **Enrich** | Google Sheets (India local, Tradable Premium, Argus biofuels — ~188 series, Parity), UOB SND Excel, CFTC COT pages saved by hand, broker PDFs (Claude extraction), event calendar (Claude CLI + web search), Gemini narrative, two embedded HTML artefacts (plant margins, HVO/SAF atlas). |
| **Present** | One 5,634-line Plotly **Dash** app on `0.0.0.0:8055` with ~15 top-level tabs and up to 4 levels of nested sub-tabs. The core idiom is the **seasonal overlay**: x = day of year, one line per year, with the current year highlighted. |
| **Operate** | Windows Task Scheduler on a trader's PC: `update.bat` 08:00 Mon–Fri, kills the process on port 8055, then restarts the app with `pythonw.exe`. |

### Functional inventory (legacy tabs)

| Tab | Data source | Rebuild release |
|---|---|---|
| Calendar Spreads (Historical / Forward Curve / YTD) | LSEG | **R1** (YTD in R2) |
| Product Spreads (Seasonal / Forward Curve, contract picker c1–c7) | LSEG | **R1** |
| Basis/Hedge Ratios → Prices | LSEG | **R1** |
| Basis/Hedge Ratios → Hedge Ratios, Tradable Prem | CashBookHedging project, private Google Sheet | R2 |
| Crush Spread, Leading Signals (Oilshare) | LSEG | **R1** |
| Outright Prices (Flat / Forward Curve, $/TN ↔ Local toggle) | LSEG | **R1** |
| Parity → Malindo | LSEG | **R1** |
| Parity → Ind/China | public Google Sheets | R2 |
| OI/Volume (cumulative tables, arrows, OI/Vol % flags) | LSEG | **R1** |
| Technical Analysis (K-Means S/R levels, SMAs) | LSEG | **R1** |
| Argus (11 sub-tabs, MIP arb, blend margins, tickets) | private Google Sheet + LSEG | R2 |
| SND | UOB Excel + Google Sheet | R2 |
| CFTC | CFTC report pages saved by hand | R2 |
| AI narrative | Gemini | R3 (Claude API) |
| News / document intelligence | PDFs + Claude | R3 |
| Calendar (release dates) | Claude Code CLI + web search | R3 (Claude API) |
| Margins, HVO/SAF Atlas | embedded HTML artefacts | R3 |

---

## 2. Critique of `CLAUDE.md` as a document

### 2.1 Strengths

1. **Unusually honest domain knowledge.** Formulas, unit conversions, sign conventions (for example "the China
   UCOME arb sign is inverted vs `_arb_status()`") and their verification ("verified by hand for 2026-10-07:
   +€31.6/t") are written down. That is exactly what a rebuild needs to stay numerically faithful.
2. **Gotchas are recorded with root causes.** The DataScope field-name trap (`Open Price`/`Close` rejected), the
   1-day EOD lag, the large-pull `ReadTimeout`, the `dcc.Graph` flex-resize loop, and the FCPO OI jump that is
   real vendor data rather than a bug.
3. **Clear operating conventions.** "Pipeline before UI", "fail soft for non-critical sources", "always normalise
   to USD/MT", "don't break the seasonal idiom".
4. **A single-source-of-truth mindset** (`PRODUCT_SPREAD_DEFS`, `TICKET_DEFS`, `SPREAD_LOCAL_UNITS`).

### 2.2 Weaknesses

| # | Issue | Evidence | Impact |
|---|---|---|---|
| D1 | **It is a changelog, not a guide.** §7 ("Dashboard Structure") is ~730 of the 1,128 lines and is mostly dated narrative ("2026-09-28, 4th same-day follow-up…"). The footer's "Last updated" block is another ~120 lines of history. | Lines 139–878, 1007–1127 | Defeats its own stated goal ("minimise re-exploration and token spend"). Current behaviour is hard to tell apart from superseded behaviour. |
| D2 | **Internal contradictions and stale statements.** For example: §7 says `oi_cumulative.py` computes avg price "converted to USD/MT inline", and later says that conversion "was removed outright". The Argus intro says "Five sub-tabs" while the code has 11. The doc says "Rough Margin" where the code says "European Margin" (`ar-margin`). Several entries still say "dashboard restart still pending". | Lines 257–263 vs 211–216; 393 vs `dashboard.py:2710-2720` | Readers, human or AI, act on wrong information. |
| D3 | **Requirements are implicit.** "Trader's explicit ask" appears 15+ times, but there is no list of user-facing requirements, acceptance criteria or priorities. | Throughout §7 | No basis for testing, scoping or prioritising. |
| D4 | **Formulas live in prose, not in tests.** Verified numbers ("€1,309.4 landed") are never turned into regression tests. | §7 Argus / Arb | A refactor can silently change numbers. |
| D5 | **Machine-specific paths.** `C:\Users\Algo.Trader\Desktop\…`, `C:\Program Files\Python310`, sibling folders `Biofuels/`, `CashBookHedging/`. | §2, §11, `*.bat` | Cannot be deployed anywhere else as-is. |
| D6 | **Security is reduced to "git-ignored".** Credentials section says only "never commit". | §9 | No threat model, no access model, no secret rotation. |
| D7 | **No non-functional statements at all.** Nothing about availability, performance, users, data licensing, backup or monitoring. | — | Not a foundation for production. |

**Recommendation for the new docs (applied in this repository):** split by purpose — PRD (what and why), TD (how),
Plan (sequence), Domain formulas (in code, with tests), and a short root `CLAUDE.md` that points to all of them.
History belongs in git and a CHANGELOG.

---

## 3. Critique of the application (production readiness)

Severity: 🔴 blocker for production · 🟠 high · 🟡 medium.

### 3.1 Security & compliance

| # | Sev | Finding | Evidence |
|---|---|---|---|
| S1 | 🔴 | **No authentication or authorisation.** The app binds to `0.0.0.0:8055`, so anyone who can reach the host can see every tab. | `dashboard.py:5635` |
| S2 | 🔴 | **Licensed market data is exposed to anyone on the network.** LSEG and Argus content is licensed per user or site. Serving it to unauthenticated users is a contractual risk. | S1 + §8 sources |
| S3 | 🔴 | **Plaintext secrets on disk.** `credentials.ini` (LSEG password, Gemini key), `token.json` (LSEG bearer token valid ~23 h), Google service-account JSON with **write** scope. | §9, `auth.py` |
| S4 | 🟠 | **No audit trail.** Nobody can tell who viewed or exported data, or when the pipeline changed data. | — |
| S5 | 🟠 | **Embedded third-party HTML with an injected shim that overrides globals** (`dl()`, `postMessage` bridge), served from Flask routes. This widens the XSS attack surface. | §7 Margins |
| S6 | 🟠 | **Production job runs through a personal Claude Code subscription** (`claude -p …` using the user's OAuth login) to avoid an exhausted API key. That is not an acceptable production credential. | §8 Calendar |
| S7 | 🟡 | **Runtime CDN dependencies** (cdnjs for the HVO atlas). These are unreliable from Alibaba Cloud regions in mainland China and are an extra supply-chain risk. | §7 HVO |

### 3.2 Architecture & maintainability

| # | Sev | Finding | Evidence |
|---|---|---|---|
| A1 | 🔴 | **CSV and Parquet files used as the database.** There is no schema, no transactions or concurrent-write safety, and no history. The dashboard must be **restarted** to see new data. | §3, §13 |
| A2 | 🟠 | **5,634-line monolith** with global state computed at import time. UI, data loading, formulas and layout are mixed together. | `dashboard.py` |
| A3 | 🟠 | **Slow, repeated loading.** `load_prices()` iterates the ~1 M-row CSV with `iterrows()` (a pure-Python loop), and several loaders re-read the same CSV. This was patched with `lru_cache` after a 2-minute startup regression. | `process.py:28`, §7 perf note |
| A4 | 🟠 | **Cross-project coupling.** Hedge ratios read `../CashBookHedging/cache/prices.parquet`; CFTC, Margins and the Atlas read `../Biofuels/…`. | §8 |
| A5 | 🟡 | **Magic business constants in code** (freight $100, duty 5.9 %, POME net costs 175, opex $150/$250/$300). Each change needs a developer and a redeploy, and leaves no audit trail. | §7 Arb / Margin |
| A6 | 🟡 | **Static assets never load.** Dash resolves `assets/` to `scripts/assets/`, so `/assets/*.js` returns 404 in production. | §7 Assets caveat |
| A7 | 🟡 | **About 25 ad-hoc research scripts** are mixed with production code in `scripts/`. | §12 |

### 3.3 Operations & reliability

| # | Sev | Finding | Evidence |
|---|---|---|---|
| O1 | 🔴 | **A trader's desktop PC is the production server.** There is no redundancy; logoff, reboot or a Windows update stops the service. | `setup_scheduler.bat` |
| O2 | 🟠 | **`update.bat` contains `pause` in its failure branch**, which leaves a zombie `cmd.exe` and a scheduled task that never exits. | §13 |
| O3 | 🟠 | **No retry or backoff** on LSEG extraction, and the whole 345-RIC request is sent in one call (it has already hit `ReadTimeout`). | `fetch.py`, §7 |
| O4 | 🟠 | **No monitoring or alerting.** Failures go only to `logs/update_log.txt`. | `update.py` |
| O5 | 🟡 | **No automated tests and no CI.** The `test_*.py` files are probe scripts, not tests. | `scripts/test_*.py` |
| O6 | 🟡 | **No data-quality gates.** Vendor anomalies such as the FCPO OI +41 % jump are spotted by eye. | §7 OI |
| O7 | 🟡 | **Restart-by-killing-the-port** (`taskkill` whatever listens on 8055) can kill an unrelated process. | `update.bat` |

### 3.4 Correctness observations worth keeping (or fixing) in the rebuild

- **Forward-filling across holidays before computing spreads is intentional.** Keep it, and record
  `is_filled` or data freshness so that users can see stale legs.
- **Cash M1–M2 spreads** are taken from the sibling CashBookHedging price frame. The rebuild computes them from
  this application's own converted prices, which removes the coupling. The values should be identical, because
  the inputs are the same LSEG RICs.
- **`CPO-MYSTH-M2` is converted MYR→USD in Outright Prices**, but the other local-currency cash RICs are not. Keep
  this as an explicit `native_unit` per instrument instead of a special-case set.
- **OI/Vol % thresholds (10 % notable, >100 % anomaly) and per-product arrow thresholds** are good domain logic.
  The rebuild makes them configurable.

---

## 4. Verdict

The **domain logic is valuable and well verified**. The **platform is a single-user desktop prototype**: it has
no authentication, its secrets are in plaintext, files serve as the database, it runs on one PC, and it has no
tests. It **cannot be made production-ready by incremental patching**. The rebuild:

1. **keeps** the formulas, unit conventions, RIC universe and seasonal-overlay idiom, ported faithfully and
   pinned by unit tests;
2. **replaces** the platform: a separate frontend (SPA on server 1), backend API and pipeline worker (server 2),
   and PostgreSQL 18 (ApsaraDB RDS, server 3), with authentication, RBAC, audit, DWS Hub SSO, Hub SDK and Sentinel;
3. **phases** the third-party-sourced tabs (Argus, SND, CFTC, Sheets, AI, News) into Releases 2–3. Each one
   needs credentials or files that do not exist on a build machine and must be verified against live sources.

See [02-PRD.md](02-PRD.md), [03-TECHNICAL-DESIGN.md](03-TECHNICAL-DESIGN.md) and
[04-REBUILD-PLAN.md](04-REBUILD-PLAN.md).
