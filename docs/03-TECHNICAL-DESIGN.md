# 03 — Technical Design (TD)

| | |
|---|---|
| **Product** | AASTAR Price Dashboard 2.0 |
| **Date** | 2026-11-04 · rev. 1.0 |
| **Implements** | [02-PRD.md](02-PRD.md) (R1, designed for R2/R3) |
| **Database** | PostgreSQL **18** (ApsaraDB RDS for PostgreSQL 18 in cloud; `postgres:18` container locally) |

---

## 1. Architecture overview

```mermaid
flowchart LR
    subgraph Internet
      U[User browser]
      HUB[DWS Downstream Hub<br/>OIDC IdP · SDK · Sentinel]
      LSEG[LSEG DataScope Select]
    end
    subgraph AliCloud["Alibaba Cloud VPC (per environment)"]
      ALB[ALB / SLB<br/>TLS 443, WAF optional]
      subgraph S1["Server 1 · Frontend ECS (public vSwitch)"]
        NGX[Nginx<br/>SPA static + /api reverse proxy]
      end
      subgraph S2["Server 2 · Backend ECS (private vSwitch)"]
        API[FastAPI · Gunicorn+Uvicorn<br/>:8000]
        WRK[Pipeline worker<br/>APScheduler]
      end
      subgraph S3["Server 3 · ApsaraDB RDS PostgreSQL 18"]
        DB[(aastar DB<br/>iam · market · ops)]
      end
      NAT[NAT Gateway]
      KMS[KMS Secrets Manager]
      SLS[Log Service SLS]
    end
    U -- HTTPS --> ALB --> NGX
    NGX -- "/api/* (private IP)" --> API
    API --> DB
    WRK --> DB
    API -- "token, JWKS (egress)" --> NAT --> HUB
    WRK -- "extractions (egress)" --> NAT --> LSEG
    U -- "SDK script + events" --> HUB
    U -- "OIDC redirect" --> HUB
    API -.-> SLS
    WRK -.-> SLS
    API -.-> KMS
```

**Key decisions (ADR summary)**

| # | Decision | Rationale |
|---|---|---|
| ADR-1 | **Python FastAPI backend.** | Reuses the legacy pandas formulas and tests verbatim. FastAPI has async I/O, Pydantic validation and an OpenAPI schema. |
| ADR-2 | **React + TypeScript + Vite SPA**, served as static files by Nginx. | Meets the frontend/backend server separation. A hashed static bundle allows a strict CSP and is cache-friendly. |
| ADR-3 | **Same-origin through the Nginx reverse proxy** (`/api` → backend). | First-party `HttpOnly` cookies, no CORS, and a simple CSP. The backend has no public IP. |
| ADR-4 | **Backend-for-Frontend (BFF) OIDC client.** | PKCE verifier and ID token never reach the browser. The session is an opaque server-side token. |
| ADR-5 | **PostgreSQL 18 as the single source of truth** for prices, derived series, users and audit. | Replaces CSV and Parquet. Transactions, history, and no restart needed for new data. Uses `uuidv7()` and virtual generated columns. |
| ADR-6 | **Pipeline in a separate worker process** (same codebase). | API workers stay responsive. A Postgres advisory lock guarantees one run at a time, even with several worker processes. |
| ADR-7 | **Plotly.js (basic bundle) for charts.** | Keeps the legacy look, hover-unified behaviour and seasonal idiom that traders know. Bundled locally, no CDN. |
| ADR-8 | **Synthetic demo-data mode.** | Builds and demos run without licensed data. This is a deterministic, seeded generator. |

---

## 2. Technology stack

| Layer | Technology | Version (pinned in lockfiles) |
|---|---|---|
| Frontend | React, TypeScript, Vite, React Router, TanStack Query, plotly.js-basic-dist-min | React 18.3, TS 5.6, Vite 5.4, Router 6.28, Query 5.x, Plotly 2.35 |
| Backend | Python, FastAPI, Uvicorn, Gunicorn, SQLAlchemy 2 (Core + ORM), psycopg 3, Alembic, Pydantic v2 / pydantic-settings | Python 3.12, FastAPI 0.115, SQLAlchemy 2.0, psycopg 3.2, Alembic 1.14 |
| Security | argon2-cffi (Argon2id), PyJWT[crypto] (JWKS, RS/ES/PS/EdDSA), cryptography (Fernet AES-128-CBC+HMAC for secrets at rest) | — |
| Analytics | pandas, numpy, scikit-learn (K-Means), kneed | pandas 2.2, numpy 2.x, sklearn 1.5 |
| Scheduling | APScheduler (cron trigger, worker process) + `pg_try_advisory_lock` | APScheduler 3.10 |
| Database | PostgreSQL 18 | ApsaraDB RDS PG 18 / `postgres:18` |
| Web server | Nginx | 1.26 |
| OS | Alibaba Cloud Linux 3 | — |

---

## 3. Repository structure

```
Price-Dashboard/
├── backend/                      ← server 2
│   ├── app/
│   │   ├── main.py               FastAPI app factory, middleware, routers
│   │   ├── worker.py             scheduler + pipeline worker entrypoint
│   │   ├── core/                 config, db, security, logging, ratelimit, crypto, errors
│   │   ├── models/               SQLAlchemy ORM (iam, ops, market)
│   │   ├── schemas/              Pydantic request/response models
│   │   ├── api/                  deps.py (auth, RBAC, CSRF) + routes/*.py
│   │   ├── services/             auth, sso (OIDC), users, audit, settings, market queries
│   │   ├── catalog/              series registry: titles, sections, groups, units (port of TITLES/SUBSECTIONS)
│   │   ├── engine/               conversions, spreads (port of process.py), oi, ta, seasonal
│   │   ├── integrations/lseg/    DataScope client (auth, extraction, retry, chunking)
│   │   ├── pipeline/             runner (steps), scheduler, data-quality checks
│   │   └── seed/                 bootstrap (roles, modules, instruments, admin), demo data generator
│   ├── migrations/               Alembic (SQL-first migrations)
│   ├── tests/                    pytest: formulas, conversions, auth, RBAC, OIDC, API
│   ├── requirements.txt · alembic.ini · .env.example
├── frontend/                     ← server 1
│   ├── index.html                Hub SDK tag injected at build when a key is configured
│   ├── src/
│   │   ├── api/                  typed client (CSRF, request id, errors)
│   │   ├── auth/                 AuthProvider, guards, SDK identify/reset
│   │   ├── components/           AppShell, Logo, charts, controls, tables, admin widgets
│   │   ├── pages/                Login, ChangePassword, modules, admin/*
│   │   ├── styles/               tokens.css (design tokens, light/dark), app.css
│   │   └── lib/                  hubSdk, format, storage
│   ├── public/                   logo.svg, favicon.svg
│   └── vite.config.ts            dev proxy, keepNames, release injection
├── deploy/                       nginx, systemd units, docker-compose (local), RDS bootstrap SQL
├── docs/                         PRD, TD, plan, runbooks, brand, legacy/
├── legacy/                       untouched legacy Dash app (reference until R2 parity)
└── CLAUDE.md                     short index for AI/humans → docs/
```

---

## 4. Backend design

### 4.1 Layers

`routes` (HTTP, validation) → `services` (business rules, transactions, audit) → `models`/SQL (persistence).
`engine` is pure functions on pandas frames with no I/O, so it is fully unit-testable. `pipeline` composes the
LSEG client, persistence and the engine.

### 4.2 Request pipeline (middleware order)

1. **RequestContext**: generates or propagates `X-Request-ID` (UUIDv7) and starts a latency timer.
2. **SecurityHeaders**: CSP, nosniff, referrer policy, permissions policy, COOP, and HSTS when `APP_ENV != local`.
   Nginx sets the same headers, so they also cover static files.
3. **Body size limit** of 1 MB.
4. **Session resolution** (dependency, not middleware): cookie `aastar_session` → SHA-256 → `iam.sessions` row,
   checked for not revoked, not idle-expired and not expired. Updates `last_seen_at` and slides the idle
   expiry, throttled to once a minute.
5. **CSRF** on `POST/PUT/PATCH/DELETE`: header `X-CSRF-Token` must equal the cookie `aastar_csrf`, its SHA-256 must
   equal `sessions.csrf_hash`, and `Origin` (if present) must be in `APP_ALLOWED_ORIGINS`. Login is exempt but
   still checks `Origin`.
6. **RBAC** dependencies: `require_user`, `require_module("calendar_spreads")`, `require_permission("data.export")`
   and `require_admin`.
7. **Rate limiting**: in-process token buckets (one backend server, documented). Login is 10/min per IP; the
   authenticated API is 600/min per session; pipeline triggers are 6/hour per user.
8. **Errors**: one JSON shape `{ "error": { "code", "message", "request_id" } }`. Stack traces go to logs only.

### 4.3 Authentication flows

#### Local login

```mermaid
sequenceDiagram
  participant B as Browser (SPA)
  participant N as Nginx
  participant A as API
  participant D as DB
  B->>N: POST /api/auth/login {email,password}
  N->>A: proxy
  A->>A: rate-limit(ip) · Origin check
  A->>D: SELECT user by lower(email)
  alt locked / disabled / bad password
    A->>D: failed_login_count++ (lock at 5 → locked_until=now()+15m)
    A->>D: INSERT audit LOGIN_FAILED
    A-->>B: 401 invalid_credentials (generic)
  else ok
    A->>A: argon2id.verify (+ rehash if params changed)
    A->>D: reset counters · INSERT session(token_hash, csrf_hash, expiries)
    A->>D: INSERT audit LOGIN_SUCCESS
    A-->>B: 200 user · Set-Cookie aastar_session (HttpOnly) + aastar_csrf
  end
  B->>B: DwsHub.identify({id: hub_sub?, email})
```

#### DWS Hub SSO (OIDC Authorization Code + PKCE, BFF)

```mermaid
sequenceDiagram
  participant B as Browser
  participant A as API (BFF)
  participant D as DB
  participant H as DWS Hub
  B->>A: GET /api/auth/sso/login?next=/calendar
  A->>H: GET /api/sso/.well-known/openid-configuration (cached 1h)
  A->>A: state, nonce, code_verifier (random 32B) · challenge=S256(verifier)
  A->>D: INSERT oidc_login_states(state_hash, enc(verifier), nonce, next, exp=+10m)
  A-->>B: 302 → H/authorize?response_type=code&client_id&redirect_uri&code_challenge&S256&state&nonce&scope
  B->>H: authorize (Hub session / login)
  H-->>B: 302 → /api/auth/sso/callback?code&state
  B->>A: GET /api/auth/sso/callback
  A->>D: consume state (UPDATE … SET consumed_at WHERE consumed_at IS NULL AND exp>now() RETURNING …)
  A->>H: POST /api/sso/token {grant_type, code, redirect_uri, client_id, code_verifier}
  H-->>A: {id_token, expires_in:60}
  A->>H: GET /api/sso/jwks (cached 10m; refetch on unknown kid)
  A->>A: verify sig(kid) · iss · aud=client_id · exp(+60s leeway) · nonce · sub
  A->>D: user by hub_sub → else by email (link sub) → else auto-provision? → else reject
  A->>D: INSERT session · audit SSO_LOGIN
  A-->>B: 302 → next (relative only) + Set-Cookie
```

- **Hub launch:** the Hub's Target URL is `https://<host>/api/auth/sso/login`, so a launch from the Hub signs the
  user straight in.
- **Errors** redirect to `/login?error=<code>`. The codes are `sso_state_invalid`, `sso_token_failed`,
  `sso_token_invalid`, `sso_not_registered`, `sso_disabled` and `sso_unavailable`.
- **Signature algorithms allowed:** `RS256, RS384, RS512, PS256, ES256, ES384, EdDSA`. `HS*` and `none` are
  rejected outright.

### 4.4 Sessions, cookies and CSRF

| Cookie | Flags | Content |
|---|---|---|
| `aastar_session` | `HttpOnly; Secure; SameSite=Lax; Path=/` (`Secure` is off only when `APP_ENV=local` over http) | 43-character URL-safe random token (256 bits) |
| `aastar_csrf` | `Secure; SameSite=Strict; Path=/` (readable by JavaScript) | 43-character random token; its SHA-256 is stored on the session |

Idle timeout is `SESSION_IDLE_MINUTES` (30) and absolute lifetime is `SESSION_ABSOLUTE_HOURS` (12). Both can be
overridden at runtime through `ops.app_settings`. A password change, a disable or an admin revoke sets
`revoked_at` on every session of that user.

### 4.5 Password handling

Argon2id (`time_cost=3, memory_cost=65536 KiB, parallelism=2, hash_len=32, salt_len=16`). The hash is upgraded at
login if the parameters change. Temporary passwords are 16 characters from `secrets.choice` across all 4
character classes. There is no password-history rule (removed 2026-11; migration `0003` dropped
`iam.password_history`); a new password must only differ from the current one.

### 4.6 RBAC model

- A **role** has `permissions text[]` (`admin`, `data.export`, `pipeline.run`) and a set of **modules**.
- Effective modules = (role modules ∪ user `grant` overrides) − user `deny` overrides. `admin` implies all modules.
- Every market endpoint maps a series key to its module through the catalog and checks access, so a user cannot
  fetch a series from a module they cannot see by guessing its key.

### 4.7 API surface (R1)

All paths are prefixed with `/api`. JSON in and out. An OpenAPI document is available at `/api/docs` in non-prod
environments only.

| Method & path | Auth | Description |
|---|---|---|
| `GET /health` · `GET /health/ready` | none | Liveness · DB + migration check |
| `GET /auth/config` | none | `{local_enabled, sso_enabled, sso_label, app_version, data_mode}` |
| `POST /auth/login` | none (Origin, rate-limited) | Local login → session cookies |
| `POST /auth/logout` | session | Revoke session |
| `GET /auth/me` | session | User, role, permissions, modules |
| `POST /auth/change-password` | session | Current + new password (policy) → rotates the session |
| `GET /auth/sso/login?next=` | none | Starts OIDC (302) |
| `GET /auth/sso/callback?code&state` | none | Completes OIDC (302) |
| `GET /market/catalog` | session | Modules → groups → series visible to the user |
| `GET /market/years` | session | Years that have data |
| `GET /market/overview` | module `overview` | Latest tiles + freshness |
| `GET /market/sections/{module}?years=&unit=&tenor=` | module | All series of a module, already shaped as seasonal traces |
| `GET /market/series/{key}?years=&unit=` | module of the key | One seasonal series |
| `GET /market/series/{key}/export.csv?unit=` | `data.export` | CSV download (audited) |
| `GET /market/forward-curves?kind=outright\|product_spread&years=&snapshot=MM-DD&unit=` | `forward_curves` | Forward-curve snapshots |
| `GET /market/ytd-spreads?year=&unit=` | `calendar_spreads` | Calendar Spreads → YTD: C1−C2…C1−C7 and the C1 price per futures product for one year (legacy `make_ytd_spread_chart`) |
| `GET /market/oi-cumulative?days=30` | `oi_volume` | Tables + thresholds + native units |
| `GET /market/ta/instruments` · `GET /market/ta/{instrument}?start_year=&unit=` | `technical_analysis` | Price, SMAs, K-Means levels |
| `GET /admin/users?q=&role=&status=&page=` · `POST /admin/users` | admin | List / create (temporary password returned once) |
| `GET/PATCH /admin/users/{id}` | admin | Detail / update |
| `POST /admin/users/{id}/reset-password` · `/unlock` · `/revoke-sessions` | admin | Account actions |
| `GET /admin/roles` · `PUT /admin/roles/{code}/modules` | admin | Role × module matrix |
| `GET /admin/modules` | admin | Module list |
| `GET /admin/audit?action=&actor=&from=&to=&page=` · `GET /admin/audit/export.csv` | admin | Audit log |
| `GET /admin/pipeline/status` · `GET /admin/pipeline/runs` · `GET /admin/pipeline/runs/{id}` | admin or `pipeline.run` | Pipeline state and history |
| `POST /admin/pipeline/run {kind}` | `pipeline.run` (`full` = admin) | Queue a run (the worker picks it up) |
| `GET /admin/settings` · `PUT /admin/settings` | admin | Runtime settings and integration status |

### 4.8 Configuration (environment)

| Variable | Example | Notes |
|---|---|---|
| `APP_ENV` | `local` / `sit` / `uat` / `prod` | Enables HSTS and secure cookies outside `local` |
| `APP_BASE_URL` | `https://aastar.kpndomain.com` | Public origin used for redirects |
| `APP_ALLOWED_ORIGINS` | `https://aastar.kpndomain.com` | CSRF Origin allow-list (comma-separated) |
| `DATABASE_URL` | `postgresql+psycopg://aastar_app:***@pgm-xxx.pg.rds.aliyuncs.com:5432/aastar?sslmode=require` | Runtime role |
| `MIGRATION_DATABASE_URL` | `…aastar_owner…` | Used by Alembic only |
| `APP_SECRET_KEY` | 64 random bytes, base64 | HMAC and derivation |
| `APP_ENCRYPTION_KEY` | Fernet key | Encrypts LSEG token and PKCE verifiers at rest |
| `AUTH_LOCAL_ENABLED` / `SSO_ENABLED` | `true` / `true` | AUTH-03 |
| `HUB_ISSUER` | `https://dwshub.kpndomain.com` | Discovery at `{issuer}/api/sso/.well-known/openid-configuration` |
| `HUB_CLIENT_ID` | `aastar-price-dashboard` | Registered in Hub Admin |
| `HUB_REDIRECT_URI` | `https://<host>/api/auth/sso/callback` | Exact match in Hub |
| `SSO_AUTO_PROVISION` / `SSO_DEFAULT_ROLE` | `false` / `viewer` | USR-10 |
| `LSEG_USERNAME` / `LSEG_PASSWORD` | — | Empty means demo mode |
| `PIPELINE_CRON` / `PIPELINE_TZ` | `0 8 * * mon-fri` / `Asia/Jakarta` | DATA-07 |
| `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` | — | First admin, created once if no admin exists |
| Frontend build: `VITE_HUB_SDK_URL`, `VITE_HUB_SDK_KEY`, `VITE_GIT_SHA` | — | SDK-01 |

---

## 5. Frontend design

- **Routing:** `/login`, `/change-password`, `/` (Overview), `/m/:module` (generic seasonal module page),
  `/m/:code/forward` (Forward Curve sub-view of Calendar Spreads, Outright Prices and Product Spreads; `/forward-curves`
  redirects), `/m/calendar_spreads/ytd`, `/oi-volume`, `/technical-analysis`, `/admin/users`, `/admin/users/:id`, `/admin/roles`,
  `/admin/audit`, `/admin/pipeline`, `/admin/settings`, `/403`, and `*` (404).
- **Guards:** `RequireAuth` loads `/api/auth/me`. It redirects to `/login?next=…` on 401.
  `RequireModule` and `RequireAdmin` render a 403 page.
- **Data:** TanStack Query caches by `(endpoint, params, data_version)`. Requests are cancelled on navigation.
- **API client:** `credentials: 'same-origin'`, sends `X-CSRF-Token` from the cookie, and parses the error shape.
  On error it logs `console.error('[api]', status, path, request_id)` so the Hub SDK records the request ID as a
  breadcrumb (SEN-04).
- **Charts:** `SeasonalChart` (Plotly scatter, x = `2000-MM-DD`, `tickformat "%b"`, unified hover, legacy year
  styling), `ForwardCurveChart` (category x-axis), `TaChart` (date x, levels as dashed lines). The Plotly bundle is
  lazy-loaded with `React.lazy`.
- **State:** years and unit are stored in `localStorage` (try/catch). The theme follows `prefers-color-scheme`
  plus a manual override.
- **Hub SDK** (`lib/hubSdk.ts`): a queue-safe wrapper (`window.DwsHub = window.DwsHub || []`).
  `identify({id, email})` is called after `/auth/me`, `reset()` on logout, and `track(name, props)` at the
  business events in PRD SDK-05.

### 5.1 Build-time SDK injection (`vite.config.ts`)

A small `transformIndexHtml` plugin inserts the following tag only when `VITE_HUB_SDK_KEY` is set:

```html
<script src="${VITE_HUB_SDK_URL}" data-app-key="${VITE_HUB_SDK_KEY}" data-release="${VITE_GIT_SHA}"
        data-bug-button="true" data-bug-button-position="bottom-right" defer></script>
```

`esbuild: { keepNames: true }` and `build.sourcemap: 'hidden'` (source maps are uploaded as release artefacts,
never served).

---

## 6. Data pipeline design

### 6.1 Run lifecycle

```mermaid
stateDiagram-v2
  [*] --> queued: schedule / manual / CLI
  queued --> running: worker acquires pg_try_advisory_lock(42_001)
  running --> succeeded: all critical steps ok
  running --> partial: critical ok, warnings / non-critical failed
  running --> failed: critical step error
  succeeded --> [*]
  partial --> [*]
  failed --> [*]
```

**Steps** (each is recorded in `ops.pipeline_run_steps`):

| # | Step | Critical | Description |
|---|---|---|---|
| 1 | `lseg_auth` | yes | Use the cached encrypted token or request a new one (`ops.integration_tokens`) |
| 2 | `lseg_fetch` | yes | ElektronTimeseries extraction. Incremental window = 7 days; full = 2015-01-01 onward, chunked by **year × ≤100 RICs**; 202 + `Location` polled every 10 s; retry with exponential backoff (1, 2, 4, 8, 16 s, 5 attempts) |
| 3 | `store_prices` | yes | `COPY` into a temp table, then `INSERT … ON CONFLICT (ric, trade_date) DO UPDATE … WHERE (…) IS DISTINCT FROM (…)` |
| 4 | `compute_series` | yes | Load `price_daily` → wide frames → convert → ffill → every spread → stage → upsert `series_points` (changed rows only) → delete orphans |
| 5 | `compute_oi` | yes | OI cumulative per product + arrow thresholds |
| 6 | `quality_checks` | no | DATA-09 checks → `ops.dq_findings` |
| 7 | `publish` | yes | Assign `data_version = nextval('ops.data_version_seq')` to the run. API caches key on it. |

The **demo mode** replaces steps 1–2 with `demo_generate` (deterministic synthetic prices, seed `20261104`).

### 6.2 LSEG client

A port of `scripts/auth.py` and `scripts/fetch.py` with these changes: credentials come from the environment or
KMS; the token is stored encrypted in the database; `httpx` uses timeouts of `(connect=30, read=300)`; there is
retry and backoff, RIC chunking and year chunking, and structured logs. The field list is fixed:
`["RIC","Trade Date","Last","Settlement Price","Bid","Ask","Volume","Open Interest"]` (never `Open Price`/`Close`).

### 6.3 Unit conversion rules

| Instrument set | Native unit | → USD/MT |
|---|---|---|
| FCPO c1–c12+, `CPO-MYSTH-M1/M2` | MYR/MT | ÷ `MYR=` |
| DCP, DBY, `JCI-SOA-DALN` | CNY/MT | ÷ `CNY=` |
| BO | cents/lb | × 22.0462 |
| S | cents/bushel | × 0.3674 |
| SM | USD/short ton | × 1.1023 |
| HO | USD/gallon | × 264.172 |
| CL, LCO | USD/barrel | × 6.2898 |
| LGO, other cash | USD/MT | none |
| FX (`MYR=`, `CNY=`, `GBP=`, `EUR=`) | rate | price = mid (Bid+Ask)/2, forward-filled |

Order of operations (identical to legacy): resolve price → pivot wide → ffill FX → convert → ffill everything →
compute. Crush and Oilshare use the **raw** (unconverted, ffilled) frame. Calendar-spread local values are
computed the legacy way (USD value × same-date FX, or ÷ factor). Outright local values are the raw native quote.

### 6.4 Derived series (port of `process.py` + dashboard injections)

The registry in `app/catalog/registry.py` defines each series key exactly as legacy did, so golden tests can
compare file by file: calendar (`{P}_c1c3/c3c5/c5c7`), cash calendar (`{stem}_M1M2`), product spreads
(`{LABEL}_{c1,c2,c3,c5,c7}`), `MAL_PREM_*`, `IDO_PREM_*`, `MALINDO_*`, `MAL/IDO_PROD_*`, `CRUSH_c1/c3`,
`SA_CRUSH`, `US_BASIS_SOY_OIL`, `SA_PREM_BO`, `SA_BASIS_SOY/SM`, `SA_ARGSBO_FCPO`, `CN_BASIS_SOY_OIL`,
cross-geography (`SA-CN_BO`, `SA-SUN_BO`, `CPO-SUN`, `SA-CPO_BO`, `CPOFOB-SUN`, `SA-CPOFOB_BO`), synthetic FOB
CPO (`CPO-MYFOB-M1/M2`, `CPO-MYFOB_M1M2`), `OIL_SHARE_c1`, `SA_OIL_SHARE`, and outrights (RIC as key). Each
definition declares its module, group, title, unit and sort order. Titles use the legacy wording.

### 6.5 Caching

The API holds an in-process LRU of 512 entries, keyed by `(query, data_version)`. A new `data_version` makes every
entry obsolete, with no explicit invalidation. Nginx adds `Cache-Control: no-store` for `/api/*` and long-lived
immutable caching for `/assets/*` (hashed file names).

---

## 7. Database design (PostgreSQL 18)

### 7.1 Conventions

- Three schemas: **`iam`** (identity and access), **`market`** (instruments, prices, derived data) and **`ops`**
  (audit, settings, pipeline, tokens).
- Surrogate keys use the PG18 built-in **`uuidv7()`**, which is time-ordered, index-friendly and has no extension
  dependency. Append-heavy logs use `bigint GENERATED ALWAYS AS IDENTITY`.
- Every timestamp is `timestamptz` in UTC. Business dates are `date`.
- `price_daily.mid` is a PG18 **virtual generated column** (`(bid+ask)/2`), computed on read and never stored.
- Enumerations are `varchar` + `CHECK` (easier to migrate than `ENUM` types).
- Names are snake_case. Foreign keys are indexed. Every table has `created_at` where meaningful.

### 7.2 Entity-relationship diagram

```mermaid
erDiagram
  ROLES ||--o{ USERS : "has"
  ROLES ||--o{ ROLE_MODULES : "grants"
  MODULES ||--o{ ROLE_MODULES : "in"
  USERS ||--o{ USER_MODULE_OVERRIDES : "overrides"
  MODULES ||--o{ USER_MODULE_OVERRIDES : "for"
  USERS ||--o{ SESSIONS : "owns"
  USERS ||--o{ AUDIT_LOGS : "acts in"
  USERS ||--o{ PIPELINE_RUNS : "triggers"
  MODULES ||--o{ SERIES : "contains"
  PRODUCTS ||--o{ INSTRUMENTS : "has contracts"
  INSTRUMENTS ||--o{ PRICE_DAILY : "priced on"
  PIPELINE_RUNS ||--o{ PRICE_DAILY : "loaded by"
  PIPELINE_RUNS ||--o{ PIPELINE_RUN_STEPS : "consists of"
  PIPELINE_RUNS ||--o{ DQ_FINDINGS : "raises"
  SERIES ||--o{ SERIES_POINTS : "has"
  PRODUCTS ||--o{ OI_CUMULATIVE : "aggregated"
  PRODUCTS ||--|| OI_ARROW_THRESHOLDS : "calibrated"

  ROLES {
    smallint id PK
    varchar code UK
    varchar name
    text_arr permissions
    bool is_system
  }
  MODULES {
    varchar code PK
    varchar name
    varchar category
    varchar release
    smallint sort_order
    bool is_enabled
  }
  ROLE_MODULES {
    smallint role_id PK,FK
    varchar module_code PK,FK
  }
  USERS {
    uuid id PK "uuidv7()"
    varchar email UK "lower(email)"
    varchar display_name
    smallint role_id FK
    varchar auth_provider "local|sso|both"
    text password_hash "argon2id, nullable"
    varchar hub_sub UK "OIDC sub"
    varchar status "active|disabled"
    smallint failed_login_count
    timestamptz locked_until
    timestamptz last_login_at
  }
  USER_MODULE_OVERRIDES {
    uuid user_id PK,FK
    varchar module_code PK,FK
    varchar effect "grant|deny"
  }
  SESSIONS {
    uuid id PK
    uuid user_id FK
    bytea token_hash UK
    bytea csrf_hash
    varchar auth_method
    timestamptz idle_expires_at
    timestamptz expires_at
    timestamptz revoked_at
  }
  OIDC_LOGIN_STATES {
    bytea state_hash PK
    text code_verifier_enc
    varchar nonce
    varchar next_path
    timestamptz expires_at
    timestamptz consumed_at
  }
  AUDIT_LOGS {
    bigint id PK
    timestamptz occurred_at
    uuid actor_user_id FK
    varchar action
    varchar target_type
    varchar target_id
    varchar outcome
    jsonb details
  }
  APP_SETTINGS {
    varchar key PK
    jsonb value
  }
  INTEGRATION_TOKENS {
    varchar provider PK
    text token_enc
    timestamptz expires_at
  }
  PIPELINE_RUNS {
    uuid id PK
    varchar kind
    varchar trigger
    varchar status
    uuid triggered_by FK
    bigint data_version UK
  }
  PIPELINE_RUN_STEPS {
    bigint id PK
    uuid run_id FK
    varchar step
    varchar status
  }
  DQ_FINDINGS {
    bigint id PK
    uuid run_id FK
    varchar check_code
    varchar ric
    varchar severity
  }
  PRODUCTS {
    varchar code PK
    varchar display_name
    varchar native_unit
    varchar conversion_type
    numeric conversion_factor
  }
  INSTRUMENTS {
    varchar ric PK
    varchar product_code FK
    varchar category
    smallint contract_no
    varchar native_unit
    varchar price_basis "last|mid"
  }
  PRICE_DAILY {
    varchar ric PK,FK
    date trade_date PK
    numeric last_price
    numeric settlement_price
    numeric bid
    numeric ask
    numeric mid "VIRTUAL generated"
    numeric price "resolved native"
    bigint volume
    bigint open_interest
  }
  SERIES {
    varchar key PK
    varchar module_code FK
    varchar group_name
    varchar title
    varchar unit
    varchar local_unit
    jsonb definition
  }
  SERIES_POINTS {
    varchar series_key PK,FK
    date obs_date PK
    float8 value "USD/MT or series unit"
    float8 value_local "native, nullable"
  }
  OI_CUMULATIVE {
    varchar product_code PK,FK
    date obs_date PK
    bigint total_oi
    bigint oi_chg
    bigint total_volume
    float8 oi_vol_pct
    float8 avg_price
    float8 price_chg_pct
  }
  OI_ARROW_THRESHOLDS {
    varchar product_code PK,FK
    float8 median_pct
    float8 p75_pct
    int n_obs
  }
```

### 7.3 Table dictionary

#### `iam.roles`: system and custom roles

| Column | Type | Null | Default | Constraints / notes |
|---|---|---|---|---|
| id | smallint | no | identity | PK |
| code | varchar(32) | no | | UNIQUE; `admin`, `analyst`, `viewer` |
| name | varchar(64) | no | | Display name |
| description | text | yes | | |
| permissions | text[] | no | `'{}'` | Subset of `{admin, data.export, pipeline.run}` |
| is_system | boolean | no | false | System roles cannot be deleted or renamed |
| created_at | timestamptz | no | now() | |

#### `iam.modules`: navigable analytics areas

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| code | varchar(48) | no | | PK, e.g. `calendar_spreads` |
| name | varchar(80) | no | | |
| description | text | yes | | |
| category | varchar(24) | no | `'analytics'` | |
| release | varchar(4) | no | `'R1'` | Release that ships the module |
| sort_order | smallint | no | 0 | Navigation order |
| icon | varchar(32) | yes | | Frontend icon name |
| is_enabled | boolean | no | true | Feature flag |

#### `iam.role_modules`: role × module matrix

| Column | Type | Null | Notes |
|---|---|---|---|
| role_id | smallint | no | PK, FK → roles(id) ON DELETE CASCADE |
| module_code | varchar(48) | no | PK, FK → modules(code) ON DELETE CASCADE |

#### `iam.users`

| Column | Type | Null | Default | Constraints / notes |
|---|---|---|---|---|
| id | uuid | no | uuidv7() | PK |
| email | varchar(254) | no | | `UNIQUE INDEX ux_users_email ON (lower(email))` |
| display_name | varchar(120) | no | | |
| role_id | smallint | no | | FK → roles(id); index |
| auth_provider | varchar(8) | no | `'local'` | CHECK in (`local`, `sso`, `both`) |
| password_hash | text | yes | | Argon2id PHC string. CHECK: required unless `auth_provider='sso'` |
| hub_sub | varchar(128) | yes | | UNIQUE. OIDC `sub` from the DWS Hub |
| status | varchar(12) | no | `'active'` | CHECK in (`active`, `disabled`) |
| failed_login_count | smallint | no | 0 | |
| locked_until | timestamptz | yes | | |
| last_login_at | timestamptz | yes | | |
| last_login_method | varchar(8) | yes | | `local` / `sso` |
| password_changed_at | timestamptz | yes | | |
| created_at / updated_at | timestamptz | no | now() | `updated_at` maintained by trigger |
| created_by / updated_by | uuid | yes | | FK → users(id) |

#### `iam.user_module_overrides`

| Column | Type | Null | Notes |
|---|---|---|---|
| user_id | uuid | no | PK, FK → users ON DELETE CASCADE |
| module_code | varchar(48) | no | PK, FK → modules ON DELETE CASCADE |
| effect | varchar(5) | no | CHECK in (`grant`, `deny`) |
| created_at | timestamptz | no | now() |
| created_by | uuid | yes | FK → users |

#### `iam.sessions`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | uuid | no | uuidv7() | PK |
| user_id | uuid | no | | FK → users ON DELETE CASCADE; partial index `WHERE revoked_at IS NULL` |
| token_hash | bytea | no | | UNIQUE. SHA-256 of the cookie token |
| csrf_hash | bytea | no | | SHA-256 of the CSRF token |
| auth_method | varchar(8) | no | | `local` / `sso` |
| created_at | timestamptz | no | now() | |
| last_seen_at | timestamptz | no | now() | |
| idle_expires_at | timestamptz | no | | Slides on activity |
| expires_at | timestamptz | no | | Absolute lifetime |
| revoked_at | timestamptz | yes | | |
| revoked_reason | varchar(32) | yes | | `logout`, `password_changed`, `user_disabled`, `admin_revoked` |
| ip | inet | yes | | |
| user_agent | varchar(512) | yes | | |

Expired rows are purged daily by the worker (`DELETE … WHERE expires_at < now() - interval '7 days'`).

#### `iam.oidc_login_states`

| Column | Type | Null | Notes |
|---|---|---|---|
| state_hash | bytea | no | PK. SHA-256 of `state` |
| code_verifier_enc | text | no | PKCE verifier, Fernet-encrypted |
| nonce | varchar(128) | no | |
| next_path | varchar(512) | yes | Validated relative path |
| ip | inet | yes | |
| created_at | timestamptz | no | now() |
| expires_at | timestamptz | no | created_at + 10 min |
| consumed_at | timestamptz | yes | Single use |

#### `ops.audit_logs` (append-only)

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | bigint | no | identity | PK |
| occurred_at | timestamptz | no | now() | BRIN index; btree `(action, occurred_at DESC)`, `(actor_user_id, occurred_at DESC)` |
| actor_user_id | uuid | yes | | FK → users ON DELETE SET NULL |
| actor_email | varchar(254) | yes | | Snapshot at the time of the event |
| action | varchar(64) | no | | e.g. `LOGIN_SUCCESS`, `USER_CREATE` |
| target_type | varchar(32) | yes | | `user`, `role`, `setting`, `pipeline_run`, `series` |
| target_id | varchar(128) | yes | | |
| outcome | varchar(8) | no | `'success'` | CHECK in (`success`, `failure`) |
| ip | inet | yes | | |
| user_agent | varchar(512) | yes | | |
| request_id | varchar(64) | yes | | |
| details | jsonb | no | `'{}'` | Before and after values, reason. Secrets removed |

`aastar_app` gets `INSERT, SELECT` only. A trigger also raises on `UPDATE` and `DELETE` (defence in depth).

#### `ops.app_settings`

| Column | Type | Null | Notes |
|---|---|---|---|
| key | varchar(64) | no | PK, e.g. `security.session_idle_minutes`, `sso.auto_provision`, `sso.default_role`, `data.mode` |
| value | jsonb | no | |
| description | text | yes | |
| updated_at | timestamptz | no | now() |
| updated_by | uuid | yes | FK → users |

#### `ops.integration_tokens`

| Column | Type | Null | Notes |
|---|---|---|---|
| provider | varchar(32) | no | PK (`lseg`) |
| token_enc | text | no | Fernet-encrypted bearer token |
| obtained_at | timestamptz | no | |
| expires_at | timestamptz | no | Refreshed 1 h before expiry |

#### `ops.pipeline_runs`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | uuid | no | uuidv7() | PK |
| kind | varchar(16) | no | | CHECK in (`incremental`, `full`, `recompute`, `demo_seed`) |
| trigger | varchar(12) | no | | CHECK in (`schedule`, `manual`, `cli`, `startup`) |
| status | varchar(12) | no | `'queued'` | CHECK in (`queued`, `running`, `succeeded`, `partial`, `failed`). Partial unique index: only one `running` |
| triggered_by | uuid | yes | | FK → users |
| queued_at | timestamptz | no | now() | |
| started_at / finished_at | timestamptz | yes | | |
| rows_fetched / rows_upserted / series_computed | integer | no | 0 | |
| data_version | bigint | yes | | UNIQUE. Set on publish from `ops.data_version_seq` |
| warnings_count | integer | no | 0 | |
| error | text | yes | | Sanitised message |
| params | jsonb | no | `'{}'` | e.g. `{"start_date": "2015-01-01"}` |

#### `ops.pipeline_run_steps`

| Column | Type | Null | Notes |
|---|---|---|---|
| id | bigint | no | identity PK |
| run_id | uuid | no | FK → pipeline_runs ON DELETE CASCADE; index |
| seq | smallint | no | Order |
| step | varchar(32) | no | |
| status | varchar(12) | no | `running` / `succeeded` / `failed` / `skipped` |
| started_at / finished_at | timestamptz | yes | |
| message | text | yes | |
| metrics | jsonb | no | `'{}'` |

#### `ops.dq_findings`

| Column | Type | Null | Notes |
|---|---|---|---|
| id | bigint | no | identity PK |
| run_id | uuid | no | FK → pipeline_runs ON DELETE CASCADE; index |
| check_code | varchar(32) | no | `stale_ric`, `non_positive_price`, `oi_vol_anomaly`, `price_jump` |
| severity | varchar(8) | no | `info` / `warn` / `error` |
| ric | varchar(32) | yes | |
| obs_date | date | yes | |
| detail | jsonb | no | `'{}'` |

#### `market.products`

| Column | Type | Null | Notes |
|---|---|---|---|
| code | varchar(8) | no | PK: `FCPO`, `BO`, `LGO`, `DCP`, `DBY`, `S`, `SM`, `HO`, `CL`, `LCO` |
| display_name | varchar(80) | no | e.g. "MDEX Palm Oil" |
| oi_display_name | varchar(80) | yes | e.g. "FCPO Palm Oil" |
| exchange | varchar(32) | yes | |
| native_unit | varchar(24) | no | |
| conversion_type | varchar(8) | no | CHECK in (`none`, `fx_div`, `factor`) |
| conversion_factor | numeric(12,6) | yes | For `factor` |
| conversion_fx_ric | varchar(32) | yes | For `fx_div` |
| oi_contracts | smallint | no | Cumulative OI range (FCPO 17, DCP 12, BO 18, SM 18, S 17, LGO 40, HO 28, CL 87, LCO 74, DBY 8) |
| oi_display_order | smallint | yes | 1–10 |
| sort_order | smallint | no | |

#### `market.instruments`

| Column | Type | Null | Notes |
|---|---|---|---|
| ric | varchar(32) | no | PK |
| product_code | varchar(8) | yes | FK → products; null for cash and FX |
| category | varchar(24) | no | CHECK in (`futures`, `fx`, `fx_usd`, `malaysia_cash`, `indonesia_cash`, `us_cash`, `sa_cash`, `china_cash`, `blacksea_cash`) |
| contract_no | smallint | yes | 1…87 for futures |
| display_name | varchar(120) | no | |
| native_unit | varchar(24) | no | |
| conversion_type | varchar(8) | no | As for products (cash `CPO-MYSTH-*` = `fx_div` `MYR=`) |
| conversion_factor | numeric(12,6) | yes | |
| conversion_fx_ric | varchar(32) | yes | |
| price_basis | varchar(8) | no | `last` (Last → Settlement) or `mid` (FX) |
| universe | varchar(12) | no | `core` (c1–c7/c12, cash, FX) or `oi_extended` |
| is_active | boolean | no | true |
| created_at | timestamptz | no | now() |

#### `market.price_daily` (about 1.1 M rows)

| Column | Type | Null | Notes |
|---|---|---|---|
| ric | varchar(32) | no | PK part, FK → instruments |
| trade_date | date | no | PK part. Secondary index `(trade_date)` |
| last_price | numeric(18,6) | yes | |
| settlement_price | numeric(18,6) | yes | |
| bid | numeric(18,6) | yes | |
| ask | numeric(18,6) | yes | |
| mid | numeric | — | **`GENERATED ALWAYS AS ((bid + ask) / 2) VIRTUAL`** (PG18) |
| price | numeric(18,6) | yes | Resolved native price (mid for FX, else last → settlement) |
| volume | bigint | yes | |
| open_interest | bigint | yes | |
| source | varchar(8) | no | `lseg` / `demo` |
| run_id | uuid | yes | FK → pipeline_runs ON DELETE SET NULL |
| updated_at | timestamptz | no | now() |

Fill factor is 90 (incremental upserts touch the last 7 days). Partition by year when the table passes 10 M rows
(not needed in R1).

#### `market.series`: derived series registry (synchronised from the code catalog on deploy)

| Column | Type | Null | Notes |
|---|---|---|---|
| key | varchar(64) | no | PK, legacy key, e.g. `BOPO_c3` |
| module_code | varchar(48) | no | FK → iam.modules |
| group_name | varchar(64) | no | e.g. "Futures", "Cash - Malaysia" |
| title | varchar(160) | no | Legacy title |
| kind | varchar(16) | no | `calendar`, `product`, `premium`, `malindo`, `cash_product`, `crush`, `basis`, `cross`, `oilshare`, `outright`, `synthetic` |
| family | varchar(32) | yes | Product-spread label for the tenor picker (e.g. `BOPO`) |
| tenor | varchar(4) | yes | `c1` … `c7`, `M1M2` |
| unit | varchar(24) | no | `USD/MT`, `$/bushel`, `%` |
| local_unit | varchar(24) | yes | Native unit when a local value exists |
| definition | jsonb | no | `{"op": "diff", "a": "FCPOc1", "b": "FCPOc3"}` (documentation and audit) |
| sort_order | integer | no | |
| is_active | boolean | no | true |
| updated_at | timestamptz | no | now() |

#### `market.series_points` (about 400 k rows)

| Column | Type | Null | Notes |
|---|---|---|---|
| series_key | varchar(64) | no | PK part, FK → series ON DELETE CASCADE |
| obs_date | date | no | PK part |
| value | double precision | no | USD/MT (or the series unit) |
| value_local | double precision | yes | Native-unit value (calendar spreads, outrights) |

The access pattern is `WHERE series_key = $1 AND obs_date >= $2` (served by the PK).

#### `market.oi_cumulative`

| Column | Type | Null | Notes |
|---|---|---|---|
| product_code | varchar(8) | no | PK part, FK → products |
| obs_date | date | no | PK part |
| total_oi | bigint | no | Σ OI over `oi_contracts` (clip ≥ 0, `min_count=1`) |
| oi_chg | bigint | yes | Day-on-day |
| total_volume | bigint | yes | |
| volume_chg | bigint | yes | |
| oi_vol_pct | double precision | yes | `oi_chg / total_volume × 100` where volume > 0 |
| avg_price | double precision | yes | Mean of c1–c5, native unit |
| price_chg_pct | double precision | yes | |
| contracts_reporting | smallint | yes | Contracts with non-null OI |

#### `market.oi_arrow_thresholds`

| Column | Type | Null | Notes |
|---|---|---|---|
| product_code | varchar(8) | no | PK, FK → products |
| median_pct | double precision | no | Median absolute % move since `window_start` |
| p75_pct | double precision | no | 75th percentile |
| n_obs | integer | no | |
| window_start | date | no | `2023-01-01` |
| computed_at | timestamptz | no | now() |

### 7.4 Database roles & grants

```sql
CREATE ROLE aastar_owner LOGIN PASSWORD '…';          -- owns schemas, runs Alembic
CREATE ROLE aastar_app   LOGIN PASSWORD '…';          -- runtime (API + worker)
CREATE ROLE aastar_ro    LOGIN PASSWORD '…';          -- reporting / support (optional)
GRANT USAGE ON SCHEMA iam, market, ops TO aastar_app, aastar_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA iam, market TO aastar_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ops.app_settings, ops.integration_tokens, ops.pipeline_runs,
      ops.pipeline_run_steps, ops.dq_findings TO aastar_app;
GRANT SELECT, INSERT ON ops.audit_logs TO aastar_app;     -- append-only
GRANT USAGE ON ALL SEQUENCES IN SCHEMA iam, market, ops TO aastar_app;
GRANT SELECT ON ALL TABLES IN SCHEMA market, ops TO aastar_ro;
```

### 7.5 Retention

| Data | Retention |
|---|---|
| price_daily, series_points, oi_* | Indefinite (market history) |
| sessions | Purged 7 days after expiry |
| oidc_login_states | Purged 1 day after expiry |
| pipeline_runs (+ steps, dq_findings) | 400 days |
| audit_logs | 1 year online; monthly export to OSS (`oss://aastar-audit-<env>/YYYY/MM.jsonl.gz`) kept 5 years |

---

## 8. Frontend UX & design system

### 8.1 Layout

- **App shell:** a left sidebar (logo, module navigation grouped by *Markets*, *Spreads*, *Positioning & TA*,
  *Admin*; collapsible), a top bar (page title, global controls, data as-of chip, theme toggle, user menu) and the
  content area.
- **Module page:** group tabs (for example Futures · Soybean · Cash), then a responsive chart grid
  (`repeat(auto-fill, minmax(420px, 1fr))`). Each card holds exactly one chart, which avoids the legacy
  flex-resize loop by design.
- **Admin pages:** a table + side drawer pattern. Destructive actions need confirmation.

### 8.2 Design system

| Token | Light | Dark | Use |
|---|---|---|---|
| `--brand-700` | `#0B4F45` | `#0B4F45` | Primary (palm green) |
| `--brand-500` | `#12806E` | `#2BB39C` | Interactive |
| `--accent-500` | `#E3A21A` | `#F2B84B` | Current year, highlights (palm-oil gold) |
| `--ink` | `#0F1B21` | `#E7EEF0` | Body text |
| `--muted` | `#5A6B71` | `#93A4AA` | Secondary text |
| `--bg` | `#F4F7F6` | `#0A1114` | Page |
| `--surface` | `#FFFFFF` | `#111B1F` | Cards |
| `--line` | `#E1E8E6` | `#1F2D32` | Borders, gridlines |
| `--pos` / `--neg` | `#1A7F37` / `#C0392B` | `#3FB66B` / `#EF6A5B` | Up / down |
| `--warn-bg` | `#FFF6D9` | `#3A2F12` | OI/Vol notable row |

- **Typography:** system UI stack (`Inter`, `Segoe UI`, `Roboto`, …), tabular numerals in tables. No web-font
  download (SEC-08).
- **Year colours** (seasonal): oldest → newest go slate-blue `#5B7DB1` → teal → gold. The **current year** uses
  `--accent-500` at width 2.75, the previous year width 1.6, older years width 1.1 with opacity
  `max(0.3, 1−0.07·age)`. This mirrors the legacy encoding with the brand palette.
- **Logo:** a rounded-square badge in a palm-green gradient holding a gold oil droplet, with a rising price line
  that forms an "A" peak, crowned by a four-point star (the "STAR" in AASTAR). Wordmark **AASTAR**, tagline
  *Price Intelligence*. Files: `docs/brand/` (logo lockups, app icon and variants — see `docs/brand/README.md`), `frontend/public/favicon.svg|.ico`, `frontend/public/icons/`, `frontend/public/manifest.webmanifest`.

---

## 9. Security design

### 9.1 Threat model (STRIDE summary)

| Threat | Vector | Control |
|---|---|---|
| Spoofing | Credential stuffing | Argon2id, lockout, IP rate limit, generic errors, SSO preferred |
| Spoofing | Forged ID token | JWKS signature, iss/aud/exp/nonce, asymmetric algorithms only, PKCE |
| Tampering | CSRF | SameSite cookies + double-submit token bound to the session + Origin check |
| Tampering | SQL injection | Parameterised SQL; allow-listed identifiers |
| Repudiation | Denying an action | Append-only audit with request ID |
| Information disclosure | Data exfiltration | RBAC per module, export permission and audit, no public backend or DB |
| Information disclosure | XSS | React escaping, strict CSP (no inline scripts, Hub origin only), no `dangerouslySetInnerHTML` |
| Information disclosure | Secrets leakage | KMS / `0600` env, Fernet at rest, log scrubbing, nothing secret in the frontend bundle |
| Denial of service | Request floods | ALB + rate limits, body-size limit, cached heavy endpoints, worker separate from the API |
| Elevation of privilege | Insecure direct object reference on series keys | Server-side module check per key; admin endpoints need the `admin` permission |
| Elevation of privilege | Self-escalation | No self role edits; at least one active admin is kept |

### 9.2 Content-Security-Policy (production)

```
default-src 'self';
script-src 'self' https://dwshub.kpndomain.com;
connect-src 'self' https://dwshub.kpndomain.com;
img-src 'self' data:;
style-src 'self' 'unsafe-inline';
font-src 'self';
object-src 'none'; base-uri 'self'; form-action 'self' https://dwshub.kpndomain.com;
frame-ancestors 'none'; upgrade-insecure-requests
```

(`style-src 'unsafe-inline'` is needed by Plotly's runtime styles. Scripts stay strict.)

---

## 10. Deployment — Alibaba Cloud (3 servers)

### 10.1 Network

| Resource | Setting |
|---|---|
| VPC | `10.20.0.0/16` per environment |
| vSwitch `public-a` | `10.20.1.0/24`: ALB, FE ECS |
| vSwitch `private-a` | `10.20.10.0/24`: BE ECS (no EIP), RDS |
| NAT Gateway + EIP | SNAT for `private-a` → LSEG, Hub, OS mirrors |
| ALB | HTTPS 443 (certificate from Certificate Management Service), HTTP→HTTPS redirect, health check `/healthz` on FE |

### 10.2 Security groups

| SG | Inbound | Outbound |
|---|---|---|
| `sg-fe` | 80/443 from the ALB; 22 from the bastion SG | 8000 to `sg-be`; 443 to mirrors |
| `sg-be` | 8000 from `sg-fe`; 22 from the bastion SG | 5432 to RDS; 443 to LSEG, the Hub and mirrors (via NAT) |
| RDS whitelist | BE private IP only (`10.20.10.x/32`) | — |

### 10.3 Server roles

**Server 1 — Frontend** (`/etc/nginx/conf.d/aastar.conf`, see `deploy/nginx/aastar.conf`):
- `root /opt/aastar/frontend/dist;` with SPA fallback `try_files $uri /index.html;`
- `location /api/ { proxy_pass http://10.20.10.11:8000; proxy_set_header X-Forwarded-For …; X-Forwarded-Proto https; }`
- Security headers and CSP (§9.2), gzip, `/assets/` long-lived cache, `/index.html` `no-cache`.

**Server 2 — Backend** (systemd units in `deploy/systemd/`):
- `aastar-api.service`: `gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000`
  (`--forwarded-allow-ips` = the FE private IP).
- `aastar-worker.service`: `python -m app.worker` (scheduler + run queue).
- Environment from `/etc/aastar/backend.env` (`0600 root`), filled from KMS Secrets Manager at deploy time.

**Server 3 — ApsaraDB RDS PG 18:** HA edition (PROD), SSL on, database `aastar`, roles from §7.4,
`timezone=UTC`, `shared_preload_libraries` default, automated backups (§AVL-02), and SQL audit (DAS) enabled for
PROD.

### 10.4 Deployment pipeline

1. CI (GitHub Actions or Alibaba Cloud Flow): lint → unit tests (backend `pytest`, frontend `vitest`/`tsc`) →
   `pip-audit` / `npm audit` → build the frontend with `VITE_GIT_SHA` → artefact.
2. Deploy BE: copy the release → `alembic upgrade head` (owner role) → `systemctl restart aastar-api aastar-worker`
   → probe `/api/health/ready`.
3. Deploy FE: upload `dist/` → atomic symlink switch → `nginx -s reload`.
4. Branch → environment: `SIT`, `UAT`, `main`→PROD (PROD needs manual approval).

### 10.5 Local development (this laptop)

| Component | Port | How |
|---|---|---|
| PostgreSQL 18 | `127.0.0.1:5544` | Docker container `aastar-pg18` (`postgres:18`, volume `aastar-pg18-data`) |
| Backend API | `127.0.0.1:8710` | `uvicorn app.main:app --port 8710` |
| Worker | — | `python -m app.worker` (or `python -m app.cli run --kind demo_seed`) |
| Frontend | `http://localhost:5181` | `npm run dev` (Vite; proxies `/api` → 8710) |
| DWS Hub (local) | `http://localhost:4000` | Existing local Hub; register client `aastar-local`, redirect `http://localhost:5181/api/auth/sso/callback` |

---

## 11. Integration specifications

### 11.1 DWS Hub SSO: registration values

| Field (Hub Admin → Applications) | Local | SIT/UAT/PROD |
|---|---|---|
| Name | AASTAR Price Dashboard (local) | AASTAR Price Dashboard |
| `sso_mode` | `oidc` | `oidc` |
| `oauth_client_id` | `aastar-local` | `aastar-<env>` |
| `oidc_redirect_uris` | `http://localhost:5181/api/auth/sso/callback` | `https://<host>/api/auth/sso/callback` |
| Target URL | `http://localhost:5181/api/auth/sso/login` | `https://<host>/api/auth/sso/login` |

Sanity checks: `GET {issuer}/api/sso/jwks` → 200, `/.well-known/openid-configuration` → 200, `/api/sso/bridge` → 410.

### 11.2 Hub SDK

- Snippet from Hub → Applications → AASTAR → **SDK**. Set `VITE_HUB_SDK_URL=https://dwshub.kpndomain.com/api/sdk/v1/hub-sdk.js`
  and `VITE_HUB_SDK_KEY=dwsk_…`, then build with `VITE_GIT_SHA=$(git rev-parse --short HEAD)`.
- Allowed origins: `https://<host>` per environment, plus `http://localhost:5181` on the staging Hub for development.
- Identify: `DwsHub.identify({ id: me.hub_sub ?? undefined, email: me.email })`. Reset on logout.

### 11.3 Sentinel

Hub → Analytic → AASTAR → Sentinel settings:

| Field | Value |
|---|---|
| GitHub repository | `dwsitproject-hub/Price-Dashboard` |
| Branch | `SIT` / `UAT` / `main` per environment (overridden by `data-release`) |
| Application folder | *(empty)* — monorepo: the SPA is in `frontend/` and the API in `backend/`; Sentinel needs both to trace a failing API call |
| Analyse automatically | ✓ |
| **Notes for Sentinel** | *"AASTAR Price Dashboard: React 18 + TypeScript + Vite SPA (frontend/), served by Nginx; API is FastAPI at /api (backend/app, routes in backend/app/api/routes). Auth: server session cookie aastar_session + CSRF header X-CSRF-Token; 401 → redirect to /login, 403 → module not granted (expected for viewers). Data: LSEG DataScope prices pulled 08:00 WIB Mon–Fri by a separate worker into PostgreSQL 18 (market.price_daily → market.series_points). Charts are Plotly seasonal overlays (x = 2000-MM-DD, one trace per year). 'No data' cards are normal for series with no history in the selected years. API errors log '[api] <status> <path> <request_id>' — use request_id to correlate with backend logs. Demo mode (synthetic data) shows a banner and is normal in local/SIT."* |

---

## 11A. Intelligence sections (legacy parity: SND, CFTC, AI, News, Calendar, HVO/SAF Atlas)

Code: `backend/app/intel/` (one module per section), routes `backend/app/api/routes/intel.py` under
`/api/intel/*`, Claude access `backend/app/integrations/claude.py`, migration `0006_intel`.

| Section | Module | Source | Storage | Refresh |
|---|---|---|---|---|
| SND | `snd` | Uploaded UOB Excel + sheet CSV/XLSX (role + year) | `intel.snd_sources` (raw cell grids; parsed on read with the legacy `snd_data.py` rules) | on upload |
| CFTC | `cftc` | `cftc.gov/files/dea/history/com_disagg_txt_<year>.zip` (12 tracked contracts) | `intel.cftc_positions` | job `cftc_refresh`, Sat 07:00 + manual |
| AI analysis | `ai_analysis` | `market.price_daily` / `market.series_points` | `intel.ai_reports` (sections, raw stats, narrative) | on request; job `ai_narrative` |
| News | `news` | Uploaded PDF/DOCX/XLSX | `intel.documents` (bytes), `intel.doc_prices`, `intel.doc_sentiment` | job `document_extract` per upload |
| Calendar | `calendar` | Claude API + `web_search_20260209` | `intel.calendar_events` (replaced per successful run) | job `calendar_refresh`, weekdays 07:45 + manual |
| HVO/SAF Atlas | `hvo_atlas` | Seed `app/intel/data/atlas_plants.json` (94 plants) | `intel.atlas_plants` | admin JSON replace |

**Jobs.** `ops.jobs` (kind, status, params, result, error) is drained every 5 s by the worker in its own thread
(advisory lock 42002, separate from the price pipeline's), so a long Claude call never delays prices. Identical
queued jobs are de-duplicated; interrupted jobs are marked failed on worker start.

**Claude.** `claude-opus-5-5` by default (`CLAUDE_MODEL`), streamed, adaptive thinking with explicit effort
(`low` for classification, `medium` otherwise), server-side refusal fallback (`fallbacks: "default"`,
beta `server-side-fallback-2026-07-01`), `pause_turn` resumes for web search. News extraction uses structured
outputs (JSON schema with the legacy product/geography enums); the Calendar keeps the legacy JSON-in-text parsing
because web-search answers carry citations. Prompts are the legacy ones. SDK `anthropic` 2.x (API version
`2026-10-21`: no sampling parameters).

**Permissions.** Module access per section; SND uploads, Atlas replace and document delete need `admin`;
CFTC/Calendar refresh need `pipeline.run`; document upload/reprocess need `documents.upload` (Analyst).
Every change is audited (`SND_UPLOAD`, `CFTC_REFRESH`, `CALENDAR_REFRESH`, `ATLAS_REPLACE`, `DOCUMENT_UPLOAD`,
`DOCUMENT_REPROCESS`, `DOCUMENT_DELETE`, `AI_REPORT`). AI reports are limited to 20 per user per hour. Upload
bodies: documents ≤ `DOCUMENT_MAX_MB` (30), SND sheets ≤ 20 MB, Atlas ≤ 5 MB; everything else ≤ 1 MB.

**Fixes vs the legacy** (kept deliberately): AI percentages divide by the absolute base (negative spreads), 0.0
is not "missing", k-means never asks for more clusters than points, and only real series are offered (the
legacy listed any processed CSV and crashed); News validates enums and records failures instead of retrying
them on every run; Calendar validates regions; CFTC history is no longer limited to hand-saved weeks.

## 12. Testing strategy

| Level | Tooling | Coverage |
|---|---|---|
| Unit (engine) | pytest | Every conversion factor; each spread family against hand-computed values; OI cumulative (min_count, clip, OI/Vol where volume is 0); K-Means levels deterministic (`random_state=0`) |
| Golden | pytest + legacy `process.py` | The same raw input run through legacy `calc_spreads` and through the new engine gives identical values per key |
| Security | pytest + httpx TestClient | 401 without a session, 403 without a module, CSRF required, lockout after 5 failures, password policy, open-redirect rejection, OIDC validation (bad aud/iss/exp/nonce/alg) with a locally generated RSA JWKS |
| API | pytest | Users CRUD, roles matrix, audit written, pipeline queueing |
| Frontend | `tsc --noEmit`, vitest | Type safety, API client CSRF, seasonal trace shaping |
| E2E (SIT) | Playwright (R2) | Login → module → export; SSO round trip against the staging Hub |
