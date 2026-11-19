# Architecture review: scenario, criteria and results

**System:** AASTAR Price Dashboard, branch `SIT` @ `f127d70` plus the uncommitted edits (claude.py, gsheets.py,
test_sheets.py). **Date:** 2026-11-18. **Target:** first production release on shared Docker hosts (FE 172.28.80.50:3170,
BE 172.28.80.51:4170), a shared ApsaraDB PG18 instance, plain HTTP on the private IP, with the production DB started from a
copy of staging.
**Scope:** code, configs, docs, and the QA stack read-only (API :8710, Vite :5181, PostgreSQL 18 :5546 db `aastar`).
**Rules kept:** no application, config or data changes. Only `SELECT`/`EXPLAIN ANALYZE` was run, in sessions set
`READ ONLY`. No logins or failed logins. The worker was not started. Credentials were read from `backend/.env.qa`
inside the script and never printed.

## How to read this

- Each viewpoint lists criteria with an ID (`V<n>.<m>`), how it was checked, a result (**Pass / Partial / Fail**) and
  evidence (file:line, command output). Findings are in `findings.md` as `AR-nn`.
- Overall result per viewpoint: **Pass** means every criterion passed or only Low notes remain. **Partial** means at
  least one criterion is Partial or Fail, with no go-live blocker. **Fail** means a criterion blocks or materially risks
  go-live.

## Method and tools

| Step | What was done |
|---|---|
| Static review | Read all of `backend/app` (≈11 k lines), the migrations, Dockerfiles, compose files, `deploy/`, the frontend entry, the API client, the routes and the polling pages, plus docs 02/03/05/06 |
| QA database | `qa/…` not used. A read-only psycopg script (scratchpad) ran inventory queries (`pg_stat_user_tables`, `pg_indexes`, FK-without-index, role settings, privileges) and 15 `EXPLAIN (ANALYZE, BUFFERS)` on the hot queries. Write paths were simulated with equivalent `SELECT`s (CTE stand-in for the temp table) |
| QA API | Unauthenticated `curl` only: `/api/health`, `/api/health/ready`, `/api/auth/config`, `/api/openapi.json`, `/api/docs`, `/api/market/catalog`, cross-origin `POST /api/auth/logout` |
| Build | `cd frontend && npm run build` (exit 0, tsc + vite) |
| Tests | `pytest --collect-only` (64 tests). The suite was **not run**: its fixture drops and recreates `aastar_test` on the dev server (:5544), which could disrupt another engineer |
| CI | Looked for `.github/`, `.gitlab-ci.yml`, `Jenkinsfile`, `azure-pipelines.yml`, lint configs (none found) |

### Volume assumptions for scaling estimates

QA holds 3 years of demo data (`price_daily` 260,869 rows / 347 RICs / 752 days; `series_points` 210,630 rows /
436 series). Production: 347 RICs × ~3,050 trading days since 2015 ≈ **1.05 M** price rows (×4), and about **1.1–1.3 M**
series points (×5–6). The estimates below scale the QA timings linearly. That is pessimistic for index scans and
about right for full scans.

---

## V1. Structure & modularity — **Pass**

| ID | Criterion | How checked | Result | Evidence |
|---|---|---|---|---|
| V1.1 | Clear layering: routes → deps → services/domain → persistence; formulas isolated and pure | Read the package tree and imports | Pass | `engine/*` are pure pandas functions. `pipeline/store.py` owns bulk SQL. Routes are thin. Overview diagram in `system-overview.md` |
| V1.2 | One owner per cross-cutting concern (auth, CSRF, RBAC, audit, Claude access, settings) | grep for alternative paths | Pass | Claude is only imported in `integrations/claude.py` (`grep -rln "import anthropic" app` → 1 file). RBAC is in `api/deps.py:76-92` and `services/rbac.py`. Audit goes through `services/audit.record` |
| V1.3 | Reference data defined once (code is the source of truth) and synced idempotently | `seed/bootstrap.py`, `catalog/registry.py` | Pass | `bootstrap.run` merges modules/roles/instruments/series. Series removed from code are deactivated (`bootstrap.py:96`) |
| V1.4 | Series writers cannot clobber each other | `store.write_series(scope)` | Pass | `store.py:143-146` deletes only within `scope`. Test `test_lseg_recompute_keeps_sheet_series` |
| V1.5 | Domain code free of HTTP concerns | grep `ApiError` in `intel/` | Partial (Low) | `intel/news.py` raises `ApiError` (e.g. `news.py:197-213`). Acceptable, noted only |
| V1.6 | Dead or stale definitions | grep | Partial (Low) | `rbac.PERMISSIONS` lacks `documents.upload` (`rbac.py:9` vs `bootstrap.py:25`). `APP_SECRET_KEY` is required (`config.py:24`) but never read → AR-24 |

## V2. Data architecture — **Partial**

| ID | Criterion | How checked | Result | Evidence |
|---|---|---|---|---|
| V2.1 | Natural PKs match the access paths of the hot queries | `pg_indexes` + EXPLAIN | Pass | `series_points_pkey (series_key, obs_date)` serves the per-series reads (E1: Bitmap Index Scan, 0.06 ms). `price_daily_pkey (ric, trade_date)` serves the forward curves (E6: 52,640 rows in 18.7 ms for 3 years). `oi_cumulative_pkey` gives a backward index scan with LIMIT (E7: 0.15 ms) |
| V2.2 | Integrity: FKs, CHECKs, UNIQUE where the code relies on them | Read `0001`, `0006`, `0007` | Pass | CHECKs on statuses and kinds. One-running-run partial unique index `ux_pipeline_one_running`. `ux_users_email (lower(email))` |
| V2.3 | FK columns used by deletes/updates are indexed | FK-without-leading-index query | Partial | 13 FKs without an index. Only one matters: `price_daily.run_id → pipeline_runs ON DELETE SET NULL` (`0001_initial.py:265`) together with the 400-day run purge (`worker.py:43`). On QA all 260,869 rows reference the single demo run → AR-18 |
| V2.4 | Bulk write path is set-based and change-only | `store.py`, simulations E9/E10 | Pass | COPY → temp table → `ON CONFLICT … WHERE IS DISTINCT FROM`. Simulated compare of all points: 234 ms (QA) → ~1.4 s at production scale. Orphan anti-join: 168 ms → ~1 s. Both are far below `statement_timeout 120s` |
| V2.5 | Migrations: SQL-first, ordered, idempotent where re-run, safe for the deploy pattern | Read 0001–0007, `env.py`, compose | Partial | Linear chain, `IF EXISTS` guards. But the grants are bound to hard-coded role names (`0001_initial.py:322`, `0006`, `0007`), the DB name is hard-coded in `bootstrap_roles.sql:17`, 0003/0005 are destructive although the docs call migrations additive (`06-STAGING-DEPLOYMENT.md:505`), and there is no backup step before `alembic upgrade head` runs on every deploy → AR-02, AR-14 |
| V2.6 | Growth and retention defined and implemented per table | Code vs TD §7.5 | Partial | Implemented: sessions (+7 d), OIDC states (+1 d), pipeline_runs (400 d). Not implemented: `ops.jobs`, `intel.documents` (`bytea`, up to 30 MB each, `0006_intel.py:109`), `intel.ai_reports`, `ops.audit_logs` (the immutable trigger `0001_initial.py:155` blocks any purge by the app). Market tables grow about 0.2 M rows a year, which is negligible → AR-17 |
| V2.7 | Time zones: timestamptz everywhere, DB TZ UTC, business dates in a defined zone | Schema, `pg_db_role_setting`, grep `date.today()` | Partial | DB `TimeZone=UTC` (role-settings query). All timestamps are `timestamptz`. But "today" is computed in container UTC while the business zone is Asia/Jakarta (`market.py:173`, `routes/market.py:114`, `routes/sheets.py:56,66`, `calendar.py:115`, `news.py:209`), and `news.py:355` writes a naive `datetime.now()` → AR-22 |
| V2.8 | Least privilege at the DB level | `has_table_privilege` | Partial | App: no CREATE on any schema, no UPDATE/DELETE on `ops.audit_logs` (verified). RO: can SELECT `ops.audit_logs` and `ops.integration_tokens` through the schema-wide grant (`0001_initial.py:332`) → AR-26 |
| V2.9 | Environments isolated at the DB level on a shared instance | Roles are instance-wide | **Fail** | `bootstrap_roles.sql:9-11` creates fixed role names. A second environment on the same instance must either share `aastar_app` (same password, one `CONNECTION LIMIT 40`) or rename the roles, in which case the migrations silently skip the grants → AR-02 |
| V2.10 | Copy-of-staging start is safe | worker bootstrap logic, data inspection | **Fail** | QA/staging-style data is 100 % `source='demo'` (inventory query). `bootstrap_data_if_empty` (`worker.py:50-58`) never backfills a non-empty `price_daily`, and `runner.py:201` flips `data.mode` to `live` after the first LSEG run → AR-01 |

**EXPLAIN summary (QA → production estimate)**

| # | Query (caller) | QA time | Plan | Prod estimate | Cached? |
|---|---|---|---|---|---|
| E1 | series points, one key, 3 y (`market._points`) | 0.06 ms | PK bitmap scan | <1 ms | yes, per data_version |
| E2 | `DISTINCT extract(year…)` (`available_years`, `market.py:82`) | 67 ms | parallel seq scan | ~0.4 s | yes |
| E3 | `max(obs_date)` on series_points (`latest_trade_date`) | 72 ms | parallel seq scan (no obs_date index) | ~0.4 s | yes |
| E4 | overview counts (`count(DISTINCT series_key)`) | 23 ms | index-only full scan | ~0.14 s | yes |
| E5 | `data_version()` | 0.15 ms | index-only backward | same | **no**: runs once per series per request |
| E6 | forward curves, 70 RICs, 3 y | 18.7 ms | PK bitmap scan | ~0.1 s for 15 y | yes |
| E7 | OI per product LIMIT 120 | 0.15 ms | PK backward | same | yes |
| E8 | admin pipeline status (4 aggregates, `admin.py:252`) | 180 ms | 2 full scans + 2 index-only | ~1 s | **no**: polled every 3 s during runs |
| E9 | write_series upsert compare (simulated) | 234 ms | hash joins, temp spill | ~1.4 s + writes | n/a |
| E10 | write_series orphan delete (simulated) | 168 ms | hash anti-join | ~1 s | n/a |
| E11 | `load_prices_long` (whole table) | 69 ms | seq scan | ~0.3 s | n/a |
| E12 | `DISTINCT trade_date` (`sheets/job.py:156`) | 118 ms | parallel index-only | ~0.5 s | n/a |
| E15 | session lookup by token hash | 0.013 ms | unique index | same | n/a |

## V3. API design — **Partial**

| ID | Criterion | How checked | Result | Evidence |
|---|---|---|---|---|
| V3.1 | Consistent resource naming and prefixes | Route files | Pass | `/api/{auth,market,intel,sheets,admin,health}`. Writes are `POST/PUT/PATCH/DELETE`. Async actions return 202 + `job_id` |
| V3.2 | One error shape for every error | curl | Partial | `ApiError`/validation → `{"error":{code,message,request_id}}`. But unknown routes return `{"detail":"Not Found"}` (curl `/api/openapi.json`, `/api/docs` → 404 with `detail`), because only FastAPI's `HTTPException` subclass is handled (`errors.py:32`) → AR-21 |
| V3.3 | Input validation with bounds | Schemas, query params | Pass | years ≤15 between 2000 and 2100 (`routes/market.py:31`), unit/tenor/kind whitelists, `days 5..120`, pagination caps (users ≤100, audit ≤200), upload size caps per route, `max_length` on strings. `group` on `/sections` is unbounded but only matched against the registry |
| V3.4 | Limits / pagination on list endpoints | Read | Pass | users and audit paged. Documents 100, AI reports 15, runs ≤200, audit export ≤50,000 |
| V3.5 | Payload size bounded | Code | Pass (Low note) | Largest visible group has 15 series (registry count). Without `group`, `/sections/waste_bio` would return 204 series × up to 15 years. The SPA always sends `group` (`ModulePage.tsx:91`) |
| V3.6 | No N+1 or per-item round trips on hot paths | Code + E5 | Partial | `section_payload` calls `series_payload` per key (`market.py:135`), and each call runs `data_version()` again (`market.py:26`, `118`). The product-spread curves query per family×contract (`market.py:270`). That is ~30 round trips per section request even on cache hits → AR-19 |
| V3.7 | Typed contracts (response models / OpenAPI) | Routes | Partial (Low) | Responses are plain dicts. OpenAPI has no response schemas, and `frontend/src/api/types.ts` is hand-maintained → AR-21 |
| V3.8 | Long work is asynchronous | Routes | Pass | Pipeline, CFTC, calendar, AI, document extraction and sheet refresh go through queues. But the upload handlers parse files synchronously inside `async def` → see V6.3 |

## V4. Security architecture — **Partial**

| ID | Criterion | How checked | Result | Evidence |
|---|---|---|---|---|
| V4.1 | Authentication: strong hashing, lockout, enumeration resistance | `security.py`, `auth.py` | Pass | Argon2id (t=3, m=64 MiB). Dummy-hash timing equaliser (`auth.py:31`). Lockout after 5 failures for 15 min. Generic errors |
| V4.2 | Sessions: opaque, hashed at rest, idle + absolute expiry, revocation | `sessions.py` | Pass | 256-bit token stored as SHA-256. Idle 30 min, absolute 12 h. Revoke on logout and password change. Users can be disabled |
| V4.3 | CSRF: token + Origin on unsafe methods | `deps.py:60-64`, curl | Pass | Double-submit header == cookie == stored hash, plus an Origin allow-list. A cross-origin POST without a session returned 401 before any action |
| V4.4 | Authorization server-side per module and permission | Routes | Pass | Every market route checks the series module (`routes/market.py:42-47`) and every intel/sheets route `require_module`. Admin needs `admin`. Tests cover a viewer without modules (`test_viewer_cannot_use_intel_without_modules`) |
| V4.5 | SSO design (BFF, PKCE S256, state single-use, JWKS, alg allow-list, verified-email binding) | `sso.py` | Pass (Low notes) | State is consumed atomically (`UPDATE … RETURNING`). HS* is rejected (test). `email_verified` is required for binding. Nonce is only checked when present (`sso.py:147`). No RP-initiated logout → AR-27 |
| V4.6 | Transport security and cookie scoping for the planned deployment | Config + plan | **Fail** | Prod is planned on plain HTTP on shared IPs. `secure_cookies` is false on http (`config.py:102`). Cookies are host-only on an IP shared by other apps (browsers ignore ports for cookies), so `aastar_session` reaches every co-hosted app → AR-04 |
| V4.7 | Secrets: not in git, encrypted at rest where stored, not exposed via the runtime | `.gitignore`, compose, `crypto.py` | Partial | `.env*` are ignored (`git check-ignore`). Fernet for the LSEG token, PKCE verifier and Google SA key. But `env_file` puts every secret into `docker inspect` output on a shared host (`docker-compose.backend.yml:10`). KMS (docs) is not used → AR-11 |
| V4.8 | DB connection encrypted and least privilege | env example, roles | Partial | `sslmode=prefer` (`backend.env.example:22-23`) silently falls back to cleartext → AR-12. Roles verified in V2.8 |
| V4.9 | Containers least privilege and network exposure | Dockerfiles, compose | Partial | Backend runs as uid 10001 (`Dockerfile:20-21`). API bound to the private IP. Image defaults `FORWARDED_ALLOW_IPS=*` (`Dockerfile:9`). No `cap_drop`/`read_only`. Nginx master runs as root → AR-23 |
| V4.10 | Browser hardening: CSP without CDNs, frame/nosniff, no unsafe HTML | nginx templates, grep | Pass | CSP `default-src 'self'` plus only the Hub origin. No `dangerouslySetInnerHTML`. react-markdown without raw HTML. Source maps are hidden and denied (`location ~* \.map$`) |
| V4.11 | Upload hardening | Routes | Partial (Low) | Size limits are per route, but openpyxl/python-docx unzip without decompression caps → AR-28 |

## V5. Reliability & resilience — **Partial**

| ID | Criterion | How checked | Result | Evidence |
|---|---|---|---|---|
| V5.1 | External calls have timeouts and bounded retries | Clients | Pass (Low notes) | LSEG: timeout 300 s / connect 30 s, 5 retries with backoff, poll ≤30 min (`lseg/client.py:39,77-91,125`). 429 is not retried. Sheets: 3 retries. CFTC 120 s. Claude SDK `max_retries=3` (`claude.py:41`) with the default 10-min timeout |
| V5.2 | Single-runner guarantees for scheduled/queued work | Code | Pass | Session advisory locks 42001 (pipeline) and 42002 (jobs). `FOR UPDATE SKIP LOCKED` claim (`jobs.py:60`). Unique "one running run" index. Locks are per database, so other DBs on the shared instance do not collide |
| V5.3 | Idempotent enqueue, no duplicate work | `jobs.enqueue` | Pass | Dedupe on `(kind, params)` while queued/running. The admin run is refused while busy (`admin.py:292-295`) |
| V5.4 | Recovery after a crash leaves no stuck user-visible state | `recover_interrupted` | **Fail** | Runs and jobs are reset, but `intel.documents.status='processing'` and `intel.ai_reports.status='pending'` are not (`jobs.py:110-115`). `reprocess` then refuses (`news.py:252`), and the AI page polls forever → AR-07 |
| V5.5 | Bounded execution time per job, no head-of-line blocking | `jobs.drain` | Partial | Serial drain of up to 50 jobs with no per-job timeout. A slow Claude call delays `sheet_refresh` after the pipeline → AR-16 |
| V5.6 | Pool sizing within the DB connection budget | config, Dockerfile, roles | Partial | Defaults: pool 10 + overflow 5 per process (`db.py:23-24`, `config.py:33`). Image `WEB_CONCURRENCY=3` → 3×15 + 15 = 60 > role limit 40. Only the staging env overrides (2×9 + 9 = 27) → AR-05 |
| V5.7 | Graceful shutdown | worker, gunicorn | Partial (Low) | Worker `SIGTERM` → `shutdown(wait=False)` (`worker.py:111`). Docker stops after 10 s, a running pipeline is killed and marked failed on the next start. Acceptable, because the next run redoes the work |
| V5.8 | Health checks reflect what matters | `/api/health*`, compose | Partial | API liveness/readiness (DB + expected revision) verified by curl. Worker healthcheck disabled (`docker-compose.backend.yml:39`). No data-freshness signal → AR-06 |
| V5.9 | Fail-closed behaviour in production for missing integrations | `runner.py:183` | **Fail** | Missing LSEG credentials produce synthetic prices in any environment. QA runs `APP_ENV=prod` with demo data → AR-09 |
| V5.10 | Housekeeping safe at scale | `worker.py:40-47` | Partial | Session purge and run purge share one transaction. The run purge cascades `SET NULL` over an unindexed FK (V2.3) under `statement_timeout 120s` → AR-18 |

## V6. Performance & scalability — **Partial**

| ID | Criterion | How checked | Result | Evidence |
|---|---|---|---|---|
| V6.1 | Hot read queries ≤100 ms at production volume | EXPLAIN table | Pass | All per-request reads are index scans ≤20 ms. Full-scan aggregates (E2–E4) are cached per data_version |
| V6.2 | Cache behaviour under multiple workers is understood and bounded | `market.py:21-43` | Pass (Low note) | 512 entries per process, invalidated by data_version. Each gunicorn worker warms its own cache, so the first-hit cost and the memory are ×N. Rate limits are also per process (documented in `ratelimit.py`) → AR-19 |
| V6.3 | The event loop is never blocked by CPU or sync I/O | `async def` routes | **Fail** | `snd_upload`, `news_upload` (`intel.py:77,215`) and sheets `upload` (`sheets.py:136`) are `async def` and call openpyxl parsing, pandas and sync DB calls directly (`intel.py:82`, `sheets.py:150-151`). One 20–31 MB workbook stalls every request on that worker → AR-08 |
| V6.4 | Polling endpoints are cheap | `PipelinePage.tsx:19,64,69` vs E8 | Partial | Status polled every 3 s while a run is active. It runs 2 full scans (~1 s at production volume) → AR-20 |
| V6.5 | Frontend bundle: small initial load, heavy libs lazy | `npm run build` | Pass | Initial: index 158.7 kB + react 172.3 kB (≈104 kB gzip). plotly 1,122 kB (387 kB gzip) is a lazy chunk (`Plot.tsx:6`, not in `index.html` modulepreload). Intel pages are lazy. Source maps are `hidden` and stripped from the image |
| V6.6 | Pipeline memory/time fits the host | Code + E9–E11 | Pass (watch) | Compute loads ~1 M price rows into pandas (~100–200 MB). There are no container memory limits on a shared host → AR-13 |

## V7. Observability & operations — **Partial**

| ID | Criterion | How checked | Result | Evidence |
|---|---|---|---|---|
| V7.1 | Structured logs with correlation IDs, no secrets | `core/logging.py`, middleware | Pass | JSON lines with `request_id` and `user_id`, key-based redaction. `X-Request-ID` is propagated from Nginx and returned (curl). Docker json-file rotation 5×20 MB |
| V7.2 | Complete, tamper-resistant audit trail | `audit.py`, trigger, grants | Pass | Admin actions, logins, exports, uploads and refreshes are audited in the same transaction. App role cannot UPDATE/DELETE (verified). Trigger blocks it even for the owner |
| V7.3 | Alerting for failed pipeline / stale data / down worker (PRD DATA-10, OBS-04) | grep `alert|notify` | **Fail** | No alerting code, no CloudMonitor/SLS on the shared Docker hosts, worker has no healthcheck. Failures show only in Admin → Data pipeline → AR-06 |
| V7.4 | Metrics (latency, job durations, cache hit rate) | grep | Partial | Per-request `ms` in logs and step metrics in `pipeline_run_steps`. No metrics endpoint |
| V7.5 | Runbooks match the code and the target topology | docs vs code | Partial | Staging guide is detailed and accurate. `05-DEPLOYMENT-RUNBOOK.md:126-199` describes a different production topology. Key-rotation guidance is incomplete. Connection count is wrong → AR-15 |

## V8. Deployability & configuration — **Partial**

| ID | Criterion | How checked | Result | Evidence |
|---|---|---|---|---|
| V8.1 | 12-factor config, validated at start | `config.py` | Pass | pydantic-settings with an `APP_ENV` allow-list and required keys (min length). Prod `APP_ENV` disables `/api/docs` (curl 404) |
| V8.2 | Images: non-root, healthcheck, minimal, reproducible | Dockerfiles | Partial | Non-root API and HEALTHCHECKs present. But the runtime image ships pytest (`requirements.txt:27`), base images are unpinned and rebuilt with `--pull` (`deploy.sh:79`), and the estimated image size is ~600 MB (scipy/sklearn/pandas) → AR-23 |
| V8.3 | Build once, promote the same artefact through environments | `deploy.sh` | **Fail** | Each host builds from the branch head. The production image would differ from the staging-tested one → AR-10 |
| V8.4 | CI with tests, lint, dependency scan (PRD DEP-05, SEC-07) | Repo search | **Fail** | No CI config of any kind → AR-10 |
| V8.5 | Migrations in the deploy flow, gated | compose | Pass (with AR-14) | The `migrate` one-shot must exit 0 before api/worker start (`depends_on: service_completed_successfully`), and `deploy.sh:87-90` stops on failure. No backup step first → AR-14 |
| V8.6 | Safe on shared hosts: port guard, pinned network, resource limits | deploy kit | Partial | The port guard is good (`deploy.sh:65-74`). No `networks:` with a pinned subnet: Docker's default pool includes 172.28.0.0/16, the hosts' own LAN → AR-03. No `mem_limit`/`cpus` → AR-13 |
| V8.7 | Rollback path | `deploy.sh ROLLBACK_TO` | Partial | Image rollback without a rebuild, last 5 images kept. Not safe across the destructive migrations 0003/0005 → AR-14 |
| V8.8 | Backups / restore tested | docs | Partial | RDS automated backups. The "quarterly restore drill" is documented but has no evidence of being run. `intel.documents` bytea inflates backups → AR-17 |

## V9. Maintainability & testability — **Partial**

| ID | Criterion | How checked | Result | Evidence |
|---|---|---|---|---|
| V9.1 | Critical formulas protected by tests | `test_engine_golden.py`, `test_sheets.py` | Pass | Golden tests against `legacy/scripts/process.py` and `oi_cumulative.py`, and parity tests for Argus/Tradable/India/China. 64 tests in total (api 15, golden 4, intel 11, security 22, sheets 12) |
| V9.2 | Security-critical paths tested | test names | Pass | CSRF, Origin, lockout, last-admin protection, SSO state/verifier/email_verified, safe_next, JWT alg, cookies |
| V9.3 | Worker / recovery / integration-client logic tested | test names | Fail (Low risk) | No tests for `recover_interrupted`, the advisory-lock contention, the LSEG retry/poll, or migration downgrade → AR-25 |
| V9.4 | Static analysis: types and lint | configs | Partial | Frontend `tsc --strict` runs in the build (pass), with 20 `any` in `charts.tsx`. Backend has no ruff/mypy config. Frontend has no ESLint and no tests → AR-25 |
| V9.5 | Docs accurate for operators and developers | spot checks | Partial | CLAUDE.md is accurate. Drift is listed in AR-15 |

## V10. Production-readiness (shared hosts, shared RDS, copy of staging) — **Fail**

| ID | Criterion | Result | Finding |
|---|---|---|---|
| V10.1 | Production data starts clean and trustworthy (no synthetic history, no staging sessions/secrets/IdP bindings) | **Fail** | AR-01 |
| V10.2 | Production DB isolated from staging (credentials, connection budget) on the shared instance | **Fail** | AR-02, AR-05 |
| V10.3 | Containers cannot disturb host networking or neighbours | **Fail** | AR-03, AR-13 |
| V10.4 | Session confidentiality on the planned HTTP / shared-IP topology | **Fail** (or formally accepted risk) | AR-04 |
| V10.5 | Production fails closed on a missing LSEG configuration | Fail | AR-09 |
| V10.6 | Someone is told when the daily pipeline fails | Fail | AR-06 |
| V10.7 | Release artefact traceable to tested code; backup before migration | Fail / Partial | AR-10, AR-14 |
| V10.8 | Operator docs for the real production topology | Partial | AR-15 |

## Scorecard

| # | Viewpoint | Result |
|---|---|---|
| 1 | Structure & modularity | **Pass** |
| 2 | Data architecture | **Partial** |
| 3 | API design | **Partial** |
| 4 | Security architecture | **Partial** |
| 5 | Reliability & resilience | **Partial** |
| 6 | Performance & scalability | **Partial** |
| 7 | Observability & operations | **Partial** |
| 8 | Deployability & configuration | **Partial** |
| 9 | Maintainability & testability | **Partial** |
| 10 | Production readiness | **Fail** |

## Re-test checklist (for the engineer fixing the findings)

1. AR-01: on a restored copy, `SELECT source, count(*) FROM market.price_daily GROUP BY 1` returns only `lseg` after the promotion procedure. `iam.sessions`, `iam.oidc_login_states` and `ops.integration_tokens` are empty. `iam.users.hub_sub` is reset (or proven equal across Hubs). `data.mode` is set by a real run.
2. AR-02/AR-05: `SELECT rolname, rolconnlimit FROM pg_roles` on the shared instance shows separate prod roles. The sum of (workers × (pool+overflow)) + worker is below the limit. Migrations grant to the configured role names.
3. AR-03: `docker network inspect <project>_default` shows the agreed subnet, and `ip route` on each host shows no 172.28.0.0/16 bridge.
4. AR-07: kill the worker during a document extraction, restart it, and check that the document shows `failed` and can be reprocessed.
5. AR-08: upload a 25 MB workbook while polling `/api/health` every 100 ms. No gap > 200 ms.
6. AR-09: with `APP_ENV=prod` and no LSEG credentials, an incremental run fails with a clear error and writes no `demo` rows.
7. AR-21: `curl /api/nope` returns `{"error":{"code":"not_found",…}}`.
