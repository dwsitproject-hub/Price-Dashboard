# AASTAR Price Dashboard: system overview (as reviewed)

Review date 2026-11-18, branch `SIT` at `f127d70`, plus the uncommitted edits to `backend/app/integrations/claude.py`,
`gsheets.py` and `tests/test_sheets.py`. This page describes the system as the code, configs and QA stack show it.
Where the docs say something different, the code is what is described here.

## 1. Components

| Component | Technology | Where | Responsibility |
|---|---|---|---|
| SPA | React 18 + TS + Vite, TanStack Query, plotly.js-basic (lazy chunk), react-router | `frontend/src` | Seasonal charts, admin console, intel pages. `api/client.ts` sends same-origin cookies and an `X-CSRF-Token` header (double submit). Hub SDK tag is injected at build time |
| Web tier | Nginx 1.27 container (`frontend/Dockerfile`) | FE host :3170 | Serves `dist/`, sends CSP and security headers, proxies `/api/` to the API, `client_max_body_size 32m` on `/api/` |
| API | FastAPI 0.115 on gunicorn + UvicornWorker (`WEB_CONCURRENCY` workers) | BE host :4170 → 8000 | Routers `auth`, `market`, `intel`, `sheets`, `admin`, `health`. Server-side sessions, RBAC (role → permissions + modules, user overrides), CSRF + Origin checks, an in-process LRU cache keyed by `data_version`, an in-process rate limiter |
| Worker | `python -m app.worker`, APScheduler BlockingScheduler | BE host, same image | Daily LSEG pipeline cron (08:00 WIB Mon–Fri), drains `ops.pipeline_runs` every 5 s (advisory lock 42001), drains `ops.jobs` every 5 s (advisory lock 42002 + `SKIP LOCKED`), crons for CFTC, calendar and sheets, housekeeping at 03:15 |
| Migrate job | `alembic upgrade head && python -m app.cli bootstrap` | BE host, one-shot | SQL-first migrations 0001–0007, idempotent reference data (roles, modules, 347 instruments, 436 series), first admin |
| Database | PostgreSQL 18 (ApsaraDB RDS). Schemas `iam`, `market`, `ops`, `intel` | shared RDS | Roles `aastar_owner` (DDL), `aastar_app` (runtime, no UPDATE/DELETE on `ops.audit_logs`, `statement_timeout 120s`, `CONNECTION LIMIT 40`), `aastar_ro` (read-only) |
| External | LSEG DataScope Select (REST, async extractions), Anthropic Claude API (`integrations/claude.py`, streaming, web search), CFTC history zips, Google Sheets (public CSV + Sheets API with a service account), DWS Hub (OIDC code + PKCE, SDK, Sentinel) | internet / Hub | Market prices, AI narratives, document extraction, event calendar, sheet-sourced series, SSO |

### Backend layering

```
api/routes  ──>  api/deps (principal, module/permission, CSRF+Origin, rate limit)
     │
     ├──> services/   auth, sessions, sso, rbac, users, audit, settings, market (queries + LRU cache)
     ├──> intel/      snd, cftc, ai, news, calendar, atlas, jobs (ops.jobs queue)
     ├──> sheets/     sources, argus, tradable, india, china, service, job (sheet_refresh)
     └──> pipeline/   runner (runs + steps + publish), store (COPY + change-only upsert), quality
                         │
                         └──> engine/  spreads, conversions, seasonal, oi, ta  (pure functions; golden-tested vs legacy/)
catalog/  instruments + series registry (code is the source of truth, synced into market.* by bootstrap)
integrations/  lseg/client, claude, gsheets
core/  config (pydantic-settings), db (SQLAlchemy engine/pool), crypto (Fernet), security (argon2id), logging (JSON), errors, ratelimit
```

## 2. Data flows

1. **Price pipeline** (worker, lock 42001): cron or admin enqueues a run in `ops.pipeline_runs`. The worker takes the
   advisory lock, `lseg_auth` (token cached encrypted in `ops.integration_tokens`), `lseg_fetch` (year × 100-RIC
   chunks, async extraction polling), `store_prices` (COPY into a temp table, then `INSERT … ON CONFLICT … WHERE IS
   DISTINCT FROM`), `compute_series` (loads all of `price_daily` into pandas, `engine.compute_all`, `write_series`
   scoped to the LSEG keys), `compute_oi`, `quality_checks`, `publish` (`nextval('ops.data_version_seq')`). Without
   LSEG credentials every fetching run generates **synthetic demo prices** instead. A successful run enqueues
   `sheet_refresh`.
2. **Sheet sources** (job `sheet_refresh`, lock 42002): public CSV export, then the Sheets API (service-account key
   encrypted in `ops.integration_secrets`), then an admin-uploaded .xlsx as fallback. Parsed grids are joined with
   LSEG legs into `market.series_points`, written with the source's own key scope. Snapshots go to
   `intel.sheet_snapshots`. Publishing bumps `intel.sheet_sources.data_version`.
3. **Read path**: browser → Nginx → API. `current_principal` resolves the session (SHA-256 of the cookie token,
   idle/absolute expiry, sliding every 60 s), checks CSRF on unsafe methods and loads the effective modules. Market
   routes check the series' module, then serve from the per-process LRU keyed by `(query…, data_version)` or query
   `series_points` / `price_daily` and shape the seasonal traces (x = day-of-year on 2000-MM-DD).
4. **Intel jobs**: uploads (news documents stored as `bytea`, SND grids as JSONB, atlas JSON) and AI report requests
   enqueue `ops.jobs`. The worker calls Claude (structured JSON output, web search for the calendar) and writes
   `intel.*`. CFTC zips are fetched weekly.
5. **SSO**: `/api/auth/sso/login` → Hub `/authorize` (state + nonce + PKCE S256, verifier encrypted in
   `iam.oidc_login_states`) → `/api/auth/sso/callback` → token exchange → JWKS-verified ID token → bind the user by
   `hub_sub` or a verified email → server-side session cookie.
6. **Audit**: every admin action, login, export and upload adds a row to `ops.audit_logs` in the same transaction.
   A trigger keeps the table append-only even for the owner.

## 3. Deployment topology

### Staging (built and running)

```
 Users (office/VPN, plain HTTP)
        │  http://172.28.92.56:3170
        ▼
 ┌──────────────── FE host 172.28.92.56 (shared Docker host) ────────────────┐
 │  aastar-web-staging  (nginx:1.27-alpine, SPA + /api proxy, CSP)           │
 │  [other apps on the same IP: ERP :3150, ECR :3160, DWS Hub :3010 …]       │
 └───────────────┬───────────────────────────────────────────────────────────┘
                 │ http://172.28.92.57:4170  (private IP, X-Forwarded-For trusted from .56 only)
                 ▼
 ┌──────────────── BE host 172.28.92.57 (shared Docker host) ────────────────┐
 │  aastar-migrate-staging (one-shot)   aastar-api-staging (gunicorn ×2)     │
 │  aastar-worker-staging (APScheduler; no healthcheck)                      │
 │  [other apps: ERP backend :4150, ECR API :4160 …]                         │
 └───────────────┬───────────────────────────┬───────────────────────────────┘
                 │ psycopg, sslmode=prefer    │ HTTPS egress: LSEG, api.anthropic.com,
                 ▼                            │ cftc.gov, Google Sheets/OAuth, Hub
 ┌──────── ApsaraDB RDS PG18 (shared instance) ──┐
 │  db "aastar": iam / market / ops / intel      │
 │  roles aastar_owner / aastar_app / aastar_ro  │  ← roles are instance-wide
 └───────────────────────────────────────────────┘
```

### Production (planned, per the review brief)

Same container layout, built with the same `deploy/staging` kit pattern: FE `172.28.80.50:3170`, BE `172.28.80.51:4170`,
a **shared** ApsaraDB instance, plain HTTP on the private IP first, and the production database **started from a copy
of the staging database**. Note that `docs/05-DEPLOYMENT-RUNBOOK.md` §2 still describes a different plan (dedicated
ECS, ALB with HTTPS, systemd units, KMS). `deploy/nginx` and `deploy/systemd` belong to that older plan.

### Local / QA

`docker-compose.yml` runs the full stack (db, migrate, api, worker, web on :5190). The QA stack used for this review:
API on uvicorn at `127.0.0.1:8710` (APP_ENV=prod, demo data, LSEG/Claude/SSO off), Vite dev server at `:5181` (so the
Nginx/CSP path is **not** exercised in QA), PostgreSQL 18.0 at `:5546`.

## 4. Key numbers

| Item | QA (measured) | Production estimate |
|---|---|---|
| `market.price_daily` | 260,869 rows, 44 MB (347 RICs, 2024-01-02 to 2026-11-18, all `source='demo'`) | ~1.05 M rows, ~180 MB (347 RICs × ~3,050 trading days since 2015) |
| `market.series_points` | 210,630 rows, 24 MB (436 series, avg 483 points) | ~1.1–1.3 M rows, ~110–150 MB |
| Growth per year | | ~90 k price rows + ~110 k series points (small) |
| Series per visible group (max) | 15 | 15 |
| Frontend initial JS | index 158.7 kB + react 172.3 kB (≈104 kB gzip) | plotly 1.12 MB (387 kB gzip) loads lazily on the first chart |
| DB connections (staging env) | — | 2 API workers × (4 + 5) + worker (4 + 5) = 27 of the role's 40 |
