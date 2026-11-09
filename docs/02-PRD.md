# 02 — Product Requirements Document (PRD)

| | |
|---|---|
| **Product** | AASTAR Price Dashboard — *Edible-oils & biofuels price intelligence* |
| **Version** | 2.0 (rebuild) · document rev. 1.0 |
| **Date** | 2026-11-04 |
| **Owner** | IT Project — DWS (`dwsitproject-hub`) |
| **Status** | Draft for review |
| **Related** | [01 Legacy review](01-LEGACY-REVIEW.md) · [03 Technical design](03-TECHNICAL-DESIGN.md) · [04 Rebuild plan](04-REBUILD-PLAN.md) |

Requirement IDs use the format `AREA-NN`. Priority uses **MoSCoW**: **M**ust, **S**hould, **C**ould, **W**on't
(this release). The **Rel.** column gives the target release (R1 = production MVP, R2 = legacy parity,
R3 = AI and documents).

---

## 1. Background & problem

The legacy dashboard (see [01](01-LEGACY-REVIEW.md)) is an accurate and heavily used analytical tool. It runs as
a single-user desktop prototype on a trader's PC, with no login, plaintext credentials, CSV files as the database,
and no monitoring. The company wants to make it available to **more users** (trading desk, management, analysts)
**safely**, on **company infrastructure in Alibaba Cloud**, integrated with the **DWS Downstream Hub**: single
sign-on, usage analytics and AI production support.

## 2. Goals & success metrics

| Goal | Metric | Target |
|---|---|---|
| G1 Secure multi-user access | Share of access that is authenticated and authorised | 100 % of routes except `/api/health` and the login flow |
| G2 Faithful analytics | R1 series that match legacy values on the same input data | 100 % (tolerance 1e-9) for the ported formulas |
| G3 Reliable daily data | Successful scheduled pipeline runs per month | ≥ 99 % on trading days; a failure alerts admins within 15 min |
| G4 Fast UI | p95 time for a 15-chart section to become interactive (warm cache) | ≤ 2.5 s |
| G5 Adoption | Weekly active users (Hub Analytic) | ≥ 80 % of registered users by month 2 |
| G6 Supportability | JS errors and bug reports that reach Sentinel with release and user identification | 100 % |

### Non-goals

- Trade execution, order management or position keeping.
- Redistributing licensed data outside the company (exports are limited to authorised roles and audited).
- A mobile-native app. The web UI must still be usable on tablet and phone widths.
- Replacing the Downstream Hub's own user directory. The Hub remains the identity provider (IdP) for SSO users.

## 3. Personas

| Persona | Description | Key needs |
|---|---|---|
| **Trader / Analyst** | Algo or commodity trader working on palm, soy, energy and biofuel spreads | Every seasonal chart, forward curves, OI tables, unit toggle, fast navigation, CSV export |
| **Viewer** (management, sales) | Reads market context | A curated subset of modules, an overview page, nothing to configure |
| **Administrator** (IT) | Runs the application | Registers users, assigns roles and module access, monitors the pipeline, reads the audit log, rotates keys |
| **Hub Administrator** | Runs the DWS Downstream Hub | Registers the app as an OIDC client, issues the SDK key, connects the repository to Sentinel |
| **Engineer** (support) | Maintains the code | Sentinel analyses with file:line evidence, structured logs, reproducible local setup |

## 4. Scope by release

| Release | Contents |
|---|---|
| **R1 — Production MVP** (this build) | Platform (login, SSO, users, roles, module access, audit, security, settings), the LSEG pipeline (fetch → store → compute → schedule), and the LSEG-driven analytics modules: Overview, Calendar Spreads, Product Spreads, Basis & Premiums, Parity (Malindo), Crush, Leading Signals, Outright Prices, Forward Curves, OI/Volume, Technical Analysis. Hub SDK and Sentinel integration. Alibaba Cloud 3-server deployment. |
| **R2 — Legacy parity** | Argus (all 11 sub-tabs, MIP and arb waterfalls, blend margins), SND, CFTC (upload-based, no shared folder), Tradable Premium, Hedge Ratios (logic moved in-house), India Local, Parity Ind/China, Calendar-spread YTD, Google Sheet pushes (DCP / Cash). Admin-editable model assumptions. |
| **R3 — AI & documents** | AI narrative (Claude API, server-side key), document intelligence and News, event Calendar (Claude API with web search), Margins and HVO/SAF Atlas rebuilt as native pages. |

---

## 5. Functional requirements

### 5.1 Authentication (AUTH)

| ID | Requirement | Pri | Rel. |
|---|---|---|---|
| AUTH-01 | Users can sign in with **email + password** (local accounts). | M | R1 |
| AUTH-02 | Users can sign in with **DWS Hub SSO** ("Sign in with Downstream Hub") using OIDC Authorization Code + PKCE (S256). See §5.8. | M | R1 |
| AUTH-03 | Admins can disable either method per environment (`AUTH_LOCAL_ENABLED`, `SSO_ENABLED`). The login page shows only the enabled methods. | M | R1 |
| AUTH-04 | Passwords are stored only as **Argon2id** hashes. A plaintext password is never logged or returned. | M | R1 |
| AUTH-05 | **Password policy:** at least 12 and at most 128 characters; at least 3 of the 4 character classes; must not contain the email local-part; must differ from the current password (the earlier "last 5 passwords" rule was removed at the business owner's request, 2026-11). | M | R1 |
| AUTH-06 | **Account lockout:** 5 consecutive failed logins lock the account for 15 minutes. Admins can unlock it. Each lockout is audited. | M | R1 |
| AUTH-07 | **Login rate limit:** at most 10 attempts per minute per IP. Excess attempts get `429`. | M | R1 |
| AUTH-08 | Error messages do not reveal whether an account exists ("Invalid email or password"). | M | R1 |
| AUTH-09 | A user with a temporary password (newly created or reset by an admin) **must change it** at first sign-in before reaching any module. | M | R1 |
| AUTH-10 | **Sessions** are server-side, referenced by an opaque random token (≥ 256 bits) in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie. Only a SHA-256 hash of the token is stored. | M | R1 |
| AUTH-11 | Session **idle timeout of 30 minutes** and **absolute lifetime of 12 hours** (configurable). A new session ID is issued at login. | M | R1 |
| AUTH-12 | Sign-out revokes the server session, clears the cookies and calls `DwsHub.reset()`. | M | R1 |
| AUTH-13 | Changing a password, disabling a user or an admin "revoke sessions" action ends **all** of that user's sessions. | M | R1 |
| AUTH-14 | All state-changing requests require a **CSRF token** (double-submit cookie plus header, bound to the session) and a same-origin `Origin` header. | M | R1 |
| AUTH-15 | TOTP multi-factor authentication for **local** admin accounts. SSO users rely on Hub MFA. | S | R2 |
| AUTH-16 | Self-service "forgot password" by email. | C | R2 |

### 5.2 User management — the "User Access" page (USR)

| ID | Requirement | Pri | Rel. |
|---|---|---|---|
| USR-01 | Admins see a **Users** list with search (name, email), filters (role, status, sign-in method) and paging. Columns: name, email, role, sign-in method, status, last sign-in, created. | M | R1 |
| USR-02 | Admins can **register a new user**: email (unique, case-insensitive), display name, role, sign-in method (`Local`, `SSO`, `Local + SSO`) and optional per-module overrides. | M | R1 |
| USR-03 | For `Local` users the system generates a strong **temporary password**, shown **once** to the admin, with "must change at first sign-in" set. Admins may type their own password if it meets AUTH-05. | M | R1 |
| USR-04 | For `SSO` users no password is set. The account is linked to the Hub identity (`sub`) at the user's first SSO sign-in, matched by email. | M | R1 |
| USR-05 | Admins can **edit** name, role, sign-in method, status and module overrides. | M | R1 |
| USR-06 | Admins can **disable or re-enable** a user. Disabling revokes the user's sessions immediately. Users are never hard-deleted (audit integrity). | M | R1 |
| USR-07 | Admins can **reset the password** (new temporary password shown once), **unlock** a locked account, and **revoke all sessions**. | M | R1 |
| USR-08 | An admin cannot disable, demote or lock themselves out, and the system keeps **at least one active admin**. | M | R1 |
| USR-09 | Every user-management action is written to the audit log with before and after values. Hashes are never logged. | M | R1 |
| USR-10 | Optional **SSO auto-provisioning**: if enabled, an unregistered Hub user signing in for the first time is created with a default role (default `viewer`). If disabled (the default), the user sees "not registered — contact an administrator". | S | R1 |
| USR-11 | Bulk import of users from CSV. | C | R2 |

### 5.3 Roles & module access (RBAC)

| ID | Requirement | Pri | Rel. |
|---|---|---|---|
| RBAC-01 | System roles: **Administrator** (all modules + Admin console), **Analyst** (all analytics modules, CSV export, can trigger a data refresh), **Viewer** (read-only, curated modules, no export). | M | R1 |
| RBAC-02 | Every analytics area is a **module** (Overview, Calendar Spreads, Product Spreads, Basis & Premiums, Parity, Crush, Leading Signals, Outright Prices, Forward Curves, OI/Volume, Technical Analysis; more in R2/R3). | M | R1 |
| RBAC-03 | On the **Roles** page an admin defines which modules each role can see (a role × module matrix). | M | R1 |
| RBAC-04 | Per-user overrides **grant** or **deny** a module on top of the role. Deny wins. | M | R1 |
| RBAC-05 | Enforcement is **server-side** on every API call. The UI also hides inaccessible navigation, but this is not a security control. | M | R1 |
| RBAC-06 | Permission (capability) checks: `data.export` (Admin, Analyst), `pipeline.run` (Admin, Analyst), `admin.*` (Admin only). | M | R1 |
| RBAC-07 | Custom roles beyond the three system roles. | C | R2 |

### 5.4 Analytics modules (DASH)

All charts follow the legacy idiom unless stated otherwise. In a **seasonal overlay** the x-axis runs Jan→Dec
(day of year), there is one line per selected year, older years are drawn thinner and more transparent, and the
**current year is the thickest line in the accent colour**. Hover is unified on x and shows every year's value.

| ID | Requirement | Pri | Rel. |
|---|---|---|---|
| DASH-01 | **Global controls:** year multi-select (default: the last 3 years, as in legacy) and unit toggle **$/TN (USD/MT, default) ↔ Local** on the modules that support native units (Calendar Spreads, Outright Prices, Forward Curves, Technical Analysis). Both persist per user in browser storage. | M | R1 |
| DASH-02 | **Overview:** tiles with the latest value, day-on-day change and as-of date for the key futures (FCPO, BO, LGO, DCP, DBY, CL, LCO, S, SM, HO) and FX (`MYR=`, `CNY=`), plus a data-freshness indicator (last successful pipeline run, latest trade date). | M | R1 |
| DASH-03 | **Calendar Spreads**, grouped Futures / Soybean / Soybean Meal / Heating Oil / Brent / WTI / Cash: c1–c3, c3–c5, c5–c7 per product, and M1–M2 for palm cash. | M | R1 |
| DASH-04 | **Product Spreads** (BOPO, BOGO, POGO, DCP-FCPO/BO/LGO, DBY-FCPO/BO/LGO, DCP-DBY, SM-PO, BOHO, Brent-WTI, HO-LGO) with a **contract picker** (c1, c2, c3, c5, c7), plus Malaysia / Indonesia cash product spreads and cross-geography spreads. | M | R1 |
| DASH-05 | **Basis & Premiums:** Malaysia and Indonesia cash premiums vs FCPO c3 (including synthetic FOB CPO), US basis, SA premiums and basis, China basis. | M | R1 |
| DASH-06 | **Parity → Malindo:** Malaysia − Indonesia for CPO, synthetic FOB CPO, olein, palm oil and stearin (M1). | M | R1 |
| DASH-07 | **Crush:** CBOT crush c1 and c3 ($/bushel, raw units) and SA crush. | M | R1 |
| DASH-08 | **Leading Signals:** CBOT Oilshare c1 (%) and SA Oilshare (%), with a reference band at the long-run mean ± 2σ. | M | R1 |
| DASH-09 | **Outright Prices:** futures c3 and cash by region, in USD/MT or the native quote unit (MYR/MT, cents/lb, CNY/MT, USD/bbl…) via the unit toggle. | M | R1 |
| DASH-10 | **Forward Curves:** for each product, x = c1…c7, one line per selected year, each line sampled as of the same day-month (snapshot date picker, default today). Also Product-Spread forward curves (c1, c2, c3, c5, c7). | M | R1 |
| DASH-11 | **OI/Volume:** one table per instrument (FCPO, BO, LGO, DCP, DBY, LCO, HO, CL, SM, S in that order), last 30 trading days. Columns: Date, Total OI, Δ OI, Total Volume, Δ Volume, OI/Vol %, Avg Px c1–c5 (native unit), Px change with per-instrument arrows (×1 below the median, ×2 between the median and p75, ×3 above p75 of the trailing absolute % moves since 2023). Rows with OI/Vol % between 10 and 100 % are shaded amber; above 100 % the value is muted, with the real value in a tooltip. | M | R1 |
| DASH-12 | **Technical Analysis:** instrument picker (futures c3, cash, FX, key spreads), start year, unit toggle. Shows price, **K-Means support/resistance levels** (elbow-selected k, 30-day window, step 5), and 50/100/200-day SMAs. | M | R1 |
| DASH-13 | Every chart has a "Download CSV" action for users with `data.export`. Exports are audited and tracked as `DwsHub.track('chart_exported', …)`. | M | R1 |
| DASH-14 | Every chart shows a "no data" state for empty series and never fails the whole page. | M | R1 |
| DASH-15 | While the environment runs on **synthetic demo data** (no LSEG credentials), a persistent banner says so. | M | R1 |
| DASH-16 | Light and dark themes, following the OS by default, with a manual toggle. | S | R1 |
| DASH-17 | Argus, SND, CFTC, Tradable Prem, Hedge Ratios, India Local, Ind/China parity, YTD, Google Sheet pushes. | M | R2 |
| DASH-18 | AI narrative, News and document intelligence, event Calendar, Margins, HVO/SAF Atlas. | S | R3 |

### 5.5 Data pipeline (DATA)

| ID | Requirement | Pri | Rel. |
|---|---|---|---|
| DATA-01 | The pipeline fetches the configured RIC universe (347 RICs: 345 legacy + `GBP=`/`EUR=`) from **LSEG DataScope Select** with the fields `RIC, Trade Date, Last, Settlement Price, Bid, Ask, Volume, Open Interest`. | M | R1 |
| DATA-02 | **Incremental by default** (7-day lookback, upsert on `(ric, trade_date)`, newest value wins). **Full backfill** from 2015-01-01 on demand, **chunked by year and by groups of at most 100 RICs**, resumable. | M | R1 |
| DATA-03 | **Retry with exponential backoff** on network errors, 5xx and poll timeouts (up to 5 attempts). On `401` the token is refreshed once. | M | R1 |
| DATA-04 | The LSEG token is cached **encrypted at rest** in the database (never in a file). It is refreshed 1 hour before its ~24 h expiry. | M | R1 |
| DATA-05 | Price resolution: FX uses the mid `(Bid+Ask)/2`; all other instruments use `Last`, falling back to `Settlement Price`. Conversion to USD/MT follows the legacy factors exactly ([TD §6.3](03-TECHNICAL-DESIGN.md#63-unit-conversion-rules)). | M | R1 |
| DATA-06 | After each fetch, the pipeline **recomputes every derived series** and the OI cumulative and threshold tables, then publishes a new **data version**. The UI picks up new data **without a restart**. | M | R1 |
| DATA-07 | **Schedule:** 08:00 Mon–Fri in the configured time zone (default `Asia/Jakarta`), plus manual runs from Admin → Data Pipeline (`pipeline.run`). Only **one run at a time**, enforced with a database advisory lock. | M | R1 |
| DATA-08 | Each run records status, step timings, rows fetched and upserted, series computed, and errors, all visible in the Admin console. | M | R1 |
| DATA-09 | **Data-quality checks** per run: stale RICs (no new data for 5 or more trading days), negative or zero prices, OI/Vol > 100 %, and day-on-day jumps above 8σ. Results are stored as run warnings. | S | R1 |
| DATA-10 | **Alerting:** a failed scheduled run, or two consecutive runs with warnings, notifies admins (email/DingTalk webhook, configurable). | S | R2 |
| DATA-11 | **Demo mode:** with no LSEG credentials configured, a deterministic synthetic generator seeds realistic data for every RIC, so the application can be built, tested and demonstrated anywhere. | M | R1 |
| DATA-12 | Non-critical third-party sources (R2) **fail soft**: a failure logs a warning on the run and never blocks the LSEG price pull. | M | R2 |

### 5.6 Administration console (ADM)

| ID | Requirement | Pri | Rel. |
|---|---|---|---|
| ADM-01 | **Users** (§5.2) and **Roles & module access** (§5.3). | M | R1 |
| ADM-02 | **Audit log:** filter by action, actor, target and date range; paged; export to CSV. | M | R1 |
| ADM-03 | **Data pipeline:** current status, last successful run, a "Run incremental now" button and a "Recompute series" button, plus run history with step details and warnings. A full backfill is admin-only and needs confirmation. | M | R1 |
| ADM-04 | **System / integrations:** read-only status of LSEG (configured, last token refresh), DWS Hub SSO (discovery reachable, client ID), Hub SDK (key present, release), data mode (live or demo) and version. | M | R1 |
| ADM-05 | **Security settings** editable at runtime: session idle and absolute timeout, SSO auto-provisioning and its default role. Each change is audited. | S | R1 |
| ADM-06 | **Model assumptions** editor (freight, duty, opex, ticket parameters) with an effective date and an audit trail. | M | R2 |

### 5.7 Audit (AUD)

| ID | Requirement | Pri | Rel. |
|---|---|---|---|
| AUD-01 | Audited events: login success and failure, logout, lockout, password change and reset, SSO login, SSO link and SSO rejection, user create and update, enable and disable, role-module change, settings change, pipeline run start and finish, data export, session revocation. | M | R1 |
| AUD-02 | Each entry records the UTC timestamp, actor (user ID and email), action, target type and ID, outcome, IP, user agent, request ID, and a JSON details object (secrets removed). | M | R1 |
| AUD-03 | The audit log is **append-only** for the application: the database role used by the app cannot `UPDATE` or `DELETE` it. Retention is at least 1 year online, archived to OSS for 5 years. | M | R1 |

### 5.8 DWS Hub SSO integration (SSO)

Contract: *SSO Target App Integration (Strict OIDC Contract)*.

| ID | Requirement | Pri | Rel. |
|---|---|---|---|
| SSO-01 | Uses the Hub endpoints `/.well-known/openid-configuration`, `/authorize`, `/token` and `/jwks` under `{HUB_ISSUER}/api/sso`. The issuer and endpoints come from discovery; nothing is hard-coded. | M | R1 |
| SSO-02 | The authorization request includes `response_type=code`, `client_id`, an exact `redirect_uri`, `code_challenge` (S256), `code_challenge_method=S256`, `state`, `nonce` and `scope=openid profile email`. | M | R1 |
| SSO-03 | The **backend** (a confidential Backend-for-Frontend) generates and stores the PKCE verifier, state and nonce server-side (single use, 10-minute TTL) and performs the token exchange with a JSON body. The browser never sees the verifier or the ID token. | M | R1 |
| SSO-04 | The **ID token is validated** against the JWKS (asymmetric algorithms only, RS/PS/ES/EdDSA; never HS256): valid signature for an active `kid`, `iss` equal to the discovery issuer, `aud` equal to the client ID, `exp` not passed (60 s leeway), `sub` present, and `nonce` matching. The JWKS is cached and refreshed when an unknown `kid` appears. | M | R1 |
| SSO-05 | `sub` is the **canonical user key** (`users.hub_sub`). `email` and `name` are profile attributes only. Linking by email happens once, then always by `sub`. | M | R1 |
| SSO-06 | The **Hub launch** flow works: the Hub's *Target URL* for this app is `https://<app>/api/auth/sso/login`, which immediately starts the flow (no extra click). | M | R1 |
| SSO-07 | Failures (`invalid_grant`, signature, `iss`/`aud`, expired code, unregistered user, disabled user) show a clear message on the login page and are audited. Codes are never retried. | M | R1 |
| SSO-08 | Hub registration checklist (Hub Admin): `sso_mode=oidc`, a unique `oauth_client_id`, and `oidc_redirect_uris` covering every environment's `https://<host>/api/auth/sso/callback`. | M | R1 |
| SSO-09 | The `next` parameter after login only accepts same-site relative paths (prevents open redirects). | M | R1 |

### 5.9 DWS Hub SDK integration (SDK)

Contract: *Downstream Hub SDK — Integration Guide*.

| ID | Requirement | Pri | Rel. |
|---|---|---|---|
| SDK-01 | The Hub SDK script is included in `<head>` of `index.html` **before** the app bundle, with `data-app-key` (per environment) and `data-release` (git short SHA, injected at build time). The tag is omitted when no key is configured. | M | R1 |
| SDK-02 | After authentication, the app calls `DwsHub.identify({ id: hub_sub, email })` on every page load. Local-only users are identified by email only, and the Hub shows them as "Not a Hub account". | M | R1 |
| SDK-03 | On sign-out the app calls `DwsHub.reset()`. | M | R1 |
| SDK-04 | The SPA router changes are picked up automatically (page views). No query strings carry secrets; the app never puts tokens in URLs. | M | R1 |
| SDK-05 | Business events are tracked: `login_succeeded` (method), `chart_exported` (series, format), `pipeline_run_triggered` (kind), `user_created` (role), `unit_toggled`, `years_changed`. Names are at most 80 characters; properties are at most 2 KB and contain no PII. | S | R1 |
| SDK-06 | The **CSP** allows the Hub origin in `script-src` and `connect-src`, and nothing else external. | M | R1 |
| SDK-07 | Production builds keep function names (`esbuild.keepNames`) so stack traces are readable for Sentinel. | M | R1 |
| SDK-08 | The floating "🐞 Report a bug" button is enabled, in the bottom-right corner. | M | R1 |
| SDK-09 | Every environment's origin (`https://<host>`, and `http://localhost:5181` for local development) is registered under Hub → SDK → Allowed origins. | M | R1 |

### 5.10 Sentinel integration (SEN)

Contract: *Sentinel — AI Production Support for Downstream Hub*.

| ID | Requirement | Pri | Rel. |
|---|---|---|---|
| SEN-01 | The repository `dwsitproject-hub/Price-Dashboard` is connected in Hub → Analytic → AASTAR → Sentinel settings, with application folder `frontend/` and a branch per environment (`SIT`, `UAT`, `main`). | M | R1 |
| SEN-02 | The Sentinel GitHub token is fine-grained, scoped to this repository only, with *Contents: Read* and *Metadata: Read*. Its expiry goes in the IT calendar. | M | R1 |
| SEN-03 | The **Notes for Sentinel** text ([TD §11.3](03-TECHNICAL-DESIGN.md#113-sentinel)) describes the architecture, data sources and what "normal" looks like. It is kept up to date with each release. | M | R1 |
| SEN-04 | API errors seen by the frontend include the backend `X-Request-ID` in `console.error` breadcrumbs, so that a Sentinel analysis can be correlated with backend logs. | M | R1 |
| SEN-05 | User-facing error text never contains secrets or tokens (Sentinel redacts them, but the app must not emit them in the first place). | M | R1 |
| SEN-06 | "Analyse automatically" is on in SIT/UAT and on in PROD subject to the Hub's daily limit. | S | R1 |

### 5.11 Hub analytics dependencies

The Hub SDK is **analytics-grade identity**. The application **never** uses Hub analytics data to grant access.
Access control relies only on the application's own session and RBAC.

---

## 6. Non-functional requirements (NFR)

### 6.1 Security (SEC)

| ID | Requirement | Pri |
|---|---|---|
| SEC-01 | TLS 1.2+ everywhere public (ALB/SLB with a managed certificate); HSTS (`max-age=31536000; includeSubDomains`). Traffic between servers stays inside the VPC on private IPs. The DB connection uses SSL (`sslmode=require` or stricter). | M |
| SEC-02 | **Security headers:** strict CSP (`default-src 'self'`; Hub origin in script and connect; `frame-ancestors 'none'`; `object-src 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` that disables camera, microphone and geolocation, and `Cross-Origin-Opener-Policy: same-origin`. | M |
| SEC-03 | **Network isolation:** only the frontend server's 443 is reachable from the load balancer. The backend accepts 8000 only from the frontend server's security group. The database accepts 5432 only from the backend's private IP (RDS whitelist). SSH only through a bastion or Cloud Assistant, never 0.0.0.0/0. | M |
| SEC-04 | **Secrets** (DB password, LSEG credentials, session and encryption keys, Hub client settings) live in **Alibaba Cloud KMS Secrets Manager** (or a root-only `0600` `.env` in R1), are never committed, and are rotated at least every 180 days. | M |
| SEC-05 | **Input validation** on every API (Pydantic schemas, size limits, allow-lists for series keys and sort fields). Database access uses parameterised queries only. | M |
| SEC-06 | **Least privilege in the database:** separate roles for migrations (`aastar_owner`), the application (`aastar_app`: DML on its tables, INSERT-only on `audit_logs`) and read-only reporting (`aastar_ro`). | M |
| SEC-07 | Dependency scanning (`pip-audit`, `npm audit`) and SAST in CI. No known high or critical CVEs at release. | M |
| SEC-08 | No external runtime CDNs: fonts, chart libraries and icons are bundled. The only third-party origin is the DWS Hub. | M |
| SEC-09 | OWASP ASVS 4.0 Level 2 is the verification baseline. A penetration test runs before PROD go-live. | S |
| SEC-10 | Logs contain no passwords, tokens, cookies or full IDs of third-party credentials. Emails appear only in audit entries. | M |

### 6.2 Performance & capacity (PERF)

| ID | Requirement |
|---|---|
| PERF-01 | 50 named users, 20 concurrent at peak (design headroom ×3). |
| PERF-02 | API p95 below 300 ms for a single series and below 800 ms for a 15-series section (warm cache); cold below 2 s. |
| PERF-03 | Incremental pipeline (fetch + compute) under 10 minutes; full recompute of every series under 2 minutes. |
| PERF-04 | Initial JS payload below 400 KB gzip excluding the chart library (lazy-loaded). |
| PERF-05 | Data volume: about 1.1 M `price_daily` rows growing by about 90 k rows per year, about 400 k `series_points` rows. |

### 6.3 Availability, backup & DR (AVL)

| ID | Requirement |
|---|---|
| AVL-01 | Target availability of 99.5 % during business hours (Mon–Fri 07:00–20:00 WIB). |
| AVL-02 | ApsaraDB RDS: automated daily backups and PITR with 7 days of log backup (PROD: 30). Restore is tested quarterly. RPO ≤ 24 h for the data (it can be re-pulled from LSEG); RPO ≤ 5 min for users and audit (PITR). RTO ≤ 4 h. |
| AVL-03 | Backend and frontend are stateless and redeployable from git plus secrets. ECS snapshots are taken weekly. |
| AVL-04 | Health endpoints: `GET /api/health` (liveness) and `GET /api/health/ready` (database reachable, migrations current). Monitored by CloudMonitor with alerts. |

### 6.4 Observability (OBS)

| ID | Requirement |
|---|---|
| OBS-01 | Structured JSON logs (timestamp, level, request ID, user ID, route, status, latency) are shipped to **Log Service (SLS)**. |
| OBS-02 | Every response carries `X-Request-ID`, which is propagated to the logs and the audit trail. |
| OBS-03 | Frontend errors, page views and bug reports go to the DWS Hub through the SDK, and Sentinel analyses them. |
| OBS-04 | CloudMonitor alarms: CPU above 80 % for 10 minutes, disk above 80 %, RDS connections above 80 %, a failing health check, and a pipeline failure. |

### 6.5 Usability & accessibility (UX)

| ID | Requirement |
|---|---|
| UX-01 | Responsive from 360 px to 2560 px. Chart grids reflow from 1 to 3 columns. |
| UX-02 | WCAG 2.1 AA colour contrast. Every control is keyboard-operable with a visible focus ring. Charts have text titles and a CSV alternative. |
| UX-03 | Browsers: the last 2 versions of Chrome, Edge, Firefox and Safari. |
| UX-04 | Consistent branding (logo, palette and typography in [TD §8.2](03-TECHNICAL-DESIGN.md#82-design-system)). |

### 6.6 Compliance & data licensing (CMP)

| ID | Requirement |
|---|---|
| CMP-01 | **LSEG and Argus licences must cover the number of named users** who can see the data. The business owner confirms this before go-live. The user count is reported from the Users page. |
| CMP-02 | CSV export is limited to roles with `data.export` and is audited. |
| CMP-03 | Personal data is limited to name, email, IP and user agent, retained according to company policy. |

---

## 7. Deployment requirements — Alibaba Cloud, 3 servers

| ID | Requirement | Pri |
|---|---|---|
| DEP-01 | **Server 1 — Frontend (ECS):** Nginx serves the built SPA and reverse-proxies `/api/*` to the backend's private IP (single origin, so no CORS and first-party cookies). TLS ends at the ALB/SLB in front of it. Suggested size: `ecs.c7.large` (2 vCPU / 4 GiB), Alibaba Cloud Linux 3. | M |
| DEP-02 | **Server 2 — Backend (ECS, private vSwitch, no public IP):** FastAPI under Gunicorn/Uvicorn workers (systemd or Docker) plus a **separate worker process** for the scheduler and pipeline. Outbound internet through a NAT Gateway to the LSEG and DWS Hub endpoints (later also the Anthropic API for R3). Suggested size: `ecs.g7.xlarge` (4 vCPU / 16 GiB, for pandas). | M |
| DEP-03 | **Server 3 — ApsaraDB RDS for PostgreSQL 18** (High-availability edition in PROD), private endpoint only, whitelist containing only the backend's private IP, SSL enabled. Suggested size: 2 vCPU / 8 GiB, 100 GiB ESSD PL1. | M |
| DEP-04 | One VPC per environment (SIT, UAT, PROD). Each environment has its own Hub client ID, SDK key, secrets and database. | M |
| DEP-05 | Git branches map to environments: `SIT` → SIT, `UAT` → UAT, `main` → PROD. Deployments run through CI with a manual approval for PROD. | M |
| DEP-06 | Database migrations (Alembic) run as a separate deploy step with the owner role, before the backend restarts. | M |
| DEP-07 | Outbound allow-list on the backend security group: `selectapi.datascope.lseg.com:443`, the Hub origin `:443`, `api.anthropic.com:443` (R3), and the mirrors used by OS updates. | M |

---

## 8. Acceptance criteria (R1 go-live)

1. A user without a session reaching any page is redirected to `/login`. An API call without a session gets `401`,
   and one without module access gets `403` (automated tests).
2. An admin creates a local user, who signs in with the temporary password, is forced to change it, and sees only
   the modules of their role. Every step appears in the audit log.
3. A pre-registered SSO user signs in from the Hub launcher with no extra prompt. `users.hub_sub` is linked, and
   the Hub Analytic page shows the user identified within one minute.
4. A bug report submitted from the app appears in Hub → Analytic, and Sentinel produces an analysis that cites
   files under `frontend/`.
5. Every R1 module renders on demo data and on live LSEG data. Ported series match the legacy CSV output for an
   identical input file (golden test).
6. A scheduled run at 08:00 completes, and the UI shows the new as-of date without a restart.
7. Security scan: no high or critical findings. Headers are verified with an observatory-style scan. The DB is not
   reachable from the internet.
8. Restore drill: the database is restored from a backup into a scratch instance and the app is started against it.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| LSEG licence does not cover multi-user distribution | CMP-01 sign-off before go-live; per-role export restrictions |
| LSEG quota or timeouts on backfill | Chunking by year and RIC group, resumable backfill, off-hours schedule |
| The Hub is unavailable, so SSO sign-in fails | Keep local login enabled for admins (break-glass), documented runbook |
| Formula drift during the port | Golden tests against legacy outputs; domain review by the trader in UAT |
| R2 sources (Google Sheets, Argus) change layout | Schema checks per run, fail soft, admin alert |
| Mainland-China network restrictions (if a CN region is used) | No external CDNs (SEC-08); NAT and allow-listed egress only |

## 10. Open questions

1. Which Alibaba Cloud region (Jakarta `ap-southeast-5`, Singapore, or a mainland region)? This affects latency
   to LSEG and the DWS Hub, and the ICP filing requirement.
2. Final production hostname (for example `aastar.kpndomain.com`) for the TLS certificate and the Hub redirect URI.
3. Is DingTalk, email or both preferred for pipeline alerts (DATA-10)?
4. Should the Argus and Google Sheet integrations (R2) keep using a service account, or move to a direct Argus
   feed?
5. LSEG licence: named-user count and whether CSV export is permitted.
