# 04 — Rebuild Plan

| | |
|---|---|
| **Date** | 2026-11-04 |
| **Implements** | [PRD](02-PRD.md) · [TD](03-TECHNICAL-DESIGN.md) |
| **Target structure** | `backend/` (server 2) · `frontend/` (server 1) · `docs/` · `deploy/` · `legacy/` (reference only) |

## 0. Principles

1. **Port the formulas, replace the platform.** Domain code is translated function by function from
   `legacy/scripts/process.py`, `oi_cumulative.py` and `dashboard.py`, and pinned by tests.
2. **Legacy stays runnable** under `legacy/` until R2 parity is signed off, so numbers can be compared side by
   side.
3. **Every phase ends in something runnable and verified**, never a half-migrated state.
4. **Local first.** Everything runs on the laptop with free ports (DB 5544, API 8710, UI 5181) and demo data
   before any cloud work.

## 1. Phase overview

| Phase | Name | Output | Release |
|---|---|---|---|
| P0 | Restructure the repository | `legacy/`, `docs/`, `backend/`, `frontend/`, `deploy/`; new root `CLAUDE.md` | R1 |
| P1 | Database & backend foundation | PG18 schema (Alembic 0001), config, logging, security core, bootstrap seed | R1 |
| P2 | Identity & access | Local login, sessions, CSRF, lockout, RBAC, users/roles admin API, audit | R1 |
| P3 | DWS Hub SSO | OIDC BFF (discovery, PKCE, token, JWKS validation, user linking) | R1 |
| P4 | Market data engine & pipeline | Catalog registry, conversions, spreads, OI, TA, LSEG client, runner, worker, demo generator | R1 |
| P5 | Market API | Catalog, sections, series, forward curves, OI, TA, overview, CSV export | R1 |
| P6 | Frontend | Design system, logo, shell, login and SSO, modules, admin console, Hub SDK | R1 |
| P7 | Local run & verification | Everything running on free ports; tests green; browser walkthrough | R1 |
| P8 | Deployment assets | Nginx config, systemd units, RDS bootstrap SQL, env templates, runbook | R1 |
| P9 | SIT → UAT → PROD on Alibaba Cloud | Infrastructure provisioned, Hub registration per environment, pen-test, go-live | R1 |
| P10 | Legacy parity sources | Argus, SND, CFTC (upload), Tradable Prem, Hedge Ratios, India, Ind/China, YTD, Sheet pushes, model-assumption editor | R2 |
| P11 | AI & documents | Claude API narrative, document intelligence/News, Calendar, Margins/Atlas native | R3 |

Phases P0–P8 are carried out in this build. P9–P11 follow, with the dependencies listed in §4.

## 2. Phase detail (R1)

### P0 — Restructure
- [x] Move `scripts/`, `config.py`, `update.py`, `*.bat`, `assets/`, `hvo_saf_data.json`, `requirements.txt` →
  `legacy/` (`git mv`, history preserved).
- [x] Move the legacy `CLAUDE.md` → `docs/legacy/CLAUDE-legacy.md`; write a short root `CLAUDE.md` index.
- [x] `.gitignore` for `backend/.venv`, `frontend/node_modules`, `frontend/dist`, `.env`.

**Done when:** the tree matches TD §3 and the legacy app's files are intact under `legacy/`.

### P1 — Database & backend foundation
- [x] `backend/requirements.txt` with pinned versions, `app/core/config.py` (pydantic-settings), `.env.example`.
- [x] `migrations/versions/0001_initial.py`: the schemas `iam`, `market`, `ops`, all 21 tables in TD §7.3, the
  indexes, the `updated_at` trigger, the audit append-only trigger, `ops.data_version_seq`, and grants to
  `aastar_app` when that role exists.
- [x] `app/seed/bootstrap.py`: roles, modules, role-module defaults, products, instruments (347 RICs), series
  registry sync, settings, and a bootstrap admin from the environment.

**Done when:** `alembic upgrade head` on an empty PG18 database succeeds and the bootstrap is idempotent.

### P2 — Identity & access
- [x] Argon2id hashing, password policy, temporary-password generator, password history.
- [x] Session service (create / resolve / slide / revoke), cookies, CSRF dependency, Origin check.
- [x] Lockout and the login rate limiter.
- [x] RBAC: effective modules, `require_module`, `require_permission`, `require_admin`.
- [x] Admin APIs: users (list, create, get, patch, reset password, unlock, revoke sessions), roles matrix,
  modules, audit (list and export), settings.
- [x] Guard rails: no self-demotion or self-disable, and at least one active admin is always kept.

**Done when:** the pytest security suite passes (401/403/CSRF/lockout/policy/last admin).

### P3 — DWS Hub SSO
- [x] Discovery cache, PKCE S256, state and nonce stored hashed and encrypted, single use, 10-minute TTL.
- [x] Token exchange (JSON body), JWKS cache with refresh on unknown `kid`, strict validation.
- [x] User resolution: `hub_sub` → email link → optional auto-provision → reject. Audit for every outcome.
- [x] `/api/auth/sso/login` doubles as the Hub launch Target URL.

**Done when:** tests with a locally generated RSA key and a mocked Hub cover the happy path, bad `aud`, bad `iss`,
expiry, nonce mismatch, `alg=HS256`, and an unregistered user. The local Hub's discovery is reachable from the
backend.

### P4 — Engine & pipeline
- [x] `catalog/registry.py`: TITLES, SUBSECTIONS, units and local units, ported exactly (legacy keys).
- [x] `engine/conversions.py`, `engine/spreads.py` (all sections of `process.py`, plus cash M1–M2 and synthetic
  FOB), `engine/oi.py`, `engine/ta.py`, `engine/seasonal.py`.
- [x] `integrations/lseg/client.py`: token cache in the DB, extraction with 202 polling, retry and backoff,
  chunking.
- [x] `pipeline/runner.py` (steps, advisory lock, data version), `pipeline/quality.py`, `worker.py` (APScheduler
  cron plus a poller for queued manual runs), CLI.
- [x] `seed/demo_data.py`: deterministic synthetic prices for every RIC (2017→today; OI-extended contracts for
  the last 2 years).

**Done when:** engine unit tests pass, a demo seed followed by a compute run fills `series_points`, `oi_*` and a
data version, and a rerun with no changes rewrites zero rows.

### P5 — Market API
- [x] Catalog filtered by the user's modules; years; overview tiles; section payloads shaped as seasonal traces;
  single series; CSV export (audited); forward curves (outright and product spread); OI tables with thresholds;
  TA (price, SMA 50/100/200, K-Means levels).
- [x] LRU cache keyed by `data_version`.

**Done when:** every R1 module endpoint returns data on the demo dataset, inaccessible modules return 403, and
p95 is below 300 ms warm on the laptop.

### P6 — Frontend
- [x] Vite + React + TS project; design tokens (light/dark); logo, mark and favicon (SVG).
- [x] API client (CSRF, request ID logging), AuthProvider, guards, Hub SDK wrapper, build-time SDK tag plugin,
  `keepNames`.
- [x] Pages: Login (local + "Sign in with Downstream Hub", error codes), Change password, Overview, generic
  Module page (group tabs, year picker, unit toggle, tenor picker, chart grid, CSV export), Forward Curves,
  OI/Volume, Technical Analysis.
- [x] Admin: Users (list, filters, create drawer, one-time password reveal, edit, reset, unlock, revoke, disable),
  Roles & module matrix, Audit log, Data pipeline (status, run buttons, history, steps, DQ findings),
  Settings & integrations.
- [x] Demo-data banner, empty states, 403 and 404 pages, responsive layout.

**Done when:** `tsc` is clean, `vite build` succeeds, and every page works against the local API.

### P7 — Local run & verification
- [x] Start `aastar-pg18` on 5544, migrate, bootstrap, seed demo data, compute.
- [x] Start the API on 8710, the worker, and the UI on 5181.
- [x] Browser walkthrough: login, forced password change, each module, an admin creating a viewer, the viewer's
  restricted navigation, a pipeline run, the audit trail.
- [x] Report what could not be verified locally (live LSEG and the Hub SSO round trip need a registered client and
  an SDK key).

### P8 — Deployment assets
- [x] `deploy/nginx/aastar.conf`, `deploy/systemd/aastar-api.service`, `aastar-worker.service`,
  `deploy/rds/bootstrap_roles.sql`, `backend/.env.example`, `frontend/.env.example`, `deploy/docker-compose.local.yml`.
- [x] `docs/05-DEPLOYMENT-RUNBOOK.md`.

## 3. Work breakdown for P9 (cloud go-live)

| # | Task | Owner | Depends on |
|---|---|---|---|
| 9.1 | Confirm the region, hostnames and LSEG licence (PRD §10) | Business + IT | — |
| 9.2 | Provision VPC, vSwitches, NAT, ALB and certificate per environment | IT Infra | 9.1 |
| 9.3 | Provision RDS PG 18 (SIT/UAT basic, PROD HA), whitelist, roles (`deploy/rds/bootstrap_roles.sql`) | IT Infra | 9.2 |
| 9.4 | ECS FE and BE, hardening (CIS baseline), Cloud Assistant, SLS agent | IT Infra | 9.2 |
| 9.5 | Store secrets in KMS; CI deploy jobs per branch | DevOps | 9.3, 9.4 |
| 9.6 | Hub registration per environment (client ID, redirect URIs, SDK key, allowed origins, Sentinel repository and token) | Hub Admin | 9.2 |
| 9.7 | LSEG production credentials; first **full backfill** (chunked, off-hours) | Data owner | 9.5 |
| 9.8 | UAT: trader validates numbers against legacy (golden tests plus spot checks) | Trader | 9.7 |
| 9.9 | Pen test and remediation; restore drill | Security | 9.8 |
| 9.10 | PROD go-live; legacy desktop task disabled after a two-week parallel run | IT | 9.9 |

## 4. R2/R3 dependencies

| Item | Needs |
|---|---|
| Argus, Tradable Prem | Google service-account key (read) via KMS; sheet shared with it |
| Sheet pushes | Service-account key with write scope; target sheet shared as Editor |
| SND | UOB Excel via admin upload (stored in OSS) |
| CFTC | Admin upload of weekly report pages (replaces the shared-folder convention) or a direct fetch from cftc.gov |
| Hedge ratios | CashBookHedging beta logic ported into `engine/` |
| AI, Calendar, News | Anthropic API key on the backend (Claude API; replaces the Gemini and Claude-CLI usage) |

## 5. Definition of done (R1)

- PRD acceptance criteria §8.1–8.7 are met in SIT. §8.8 (restore drill) is met before PROD.
- Docs updated: PRD, TD and runbook match the deployed system.
- No secrets in git. `pip-audit` and `npm audit` show no high or critical findings.
