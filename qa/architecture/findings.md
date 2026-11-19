# Architecture review findings

AASTAR Price Dashboard, branch `SIT` @ `f127d70` plus the uncommitted edits, reviewed 2026-11-18 for the first production release (shared Docker hosts FE 172.28.80.50 / BE 172.28.80.51, shared ApsaraDB PG18, HTTP on the private IP, production DB started from a copy of staging). Criteria and evidence are in `review-scenario.md`. Topology is in `system-overview.md`.

Severity: **High** blocks or materially risks go-live. **Medium** should be fixed before or soon after go-live. **Low** is hygiene or latent. Effort: **S** ≤ ½ day, **M** 1–3 days, **L** > 3 days. *Quick fix* = a small, low-risk change that can be made now without a design decision.

## Summary

| ID | Sev | Viewpoint | Title | Effort | Quick fix |
|---|---|---|---|---|---|
| AR-01 | High | Prod readiness / Data | A copy of staging brings synthetic prices, sessions, staging IdP bindings and secrets into production | M | no |
| AR-02 | High | Data / Security | Role and database names are hard-coded, so on a shared RDS instance prod and staging share `aastar_app` | M | no |
| AR-03 | High | Deployability | Docker networks are not pinned; the default pool includes 172.28.0.0/16, the hosts' own LAN | S | yes* |
| AR-04 | High | Security | Plain HTTP on a shared IP: the session cookie goes to every co-hosted app and travels in cleartext | M | no |
| AR-05 | Medium | Reliability | Default pool sizing (60–75 connections) exceeds the role limit of 40; no connection budget on the shared instance | S | yes |
| AR-06 | Medium | Observability | No alerting, no worker liveness, no data-freshness signal (PRD DATA-10 / OBS-04 unmet) | M | no |
| AR-07 | Medium | Reliability | A worker crash leaves documents "processing" and AI reports "pending" forever | S | yes |
| AR-08 | Medium | Performance | Upload handlers are `async def` but parse workbooks synchronously, which blocks the event loop | S | yes |
| AR-09 | Medium | Reliability | Missing LSEG credentials silently produce synthetic prices, also with `APP_ENV=prod` | S | yes |
| AR-10 | Medium | Deployability | No CI, no test gate, no build-once-promote (prod image ≠ staging-tested image) | M | no |
| AR-11 | Medium | Security | Secrets in `env_file` are readable with `docker inspect` by anyone with Docker access on the shared host | M | no |
| AR-12 | Medium | Security | DB TLS not enforced (`sslmode=prefer`); SSO issuer over HTTP | S | yes |
| AR-13 | Medium | Deployability | No container memory/CPU limits on shared hosts | S | yes |
| AR-14 | Medium | Deployability / Data | Migrations auto-apply on every deploy with no backup step; "additive migrations" claim is false | S | yes |
| AR-15 | Medium | Observability / Docs | Production runbook describes a different topology; rotation and connection docs are wrong | S | yes |
| AR-16 | Medium | Reliability | Serial job queue with no per-job timeout (head-of-line blocking) | S | no |
| AR-17 | Low | Data | Retention gaps: jobs, documents (`bytea`), AI reports, audit archive | M | no |
| AR-18 | Low | Data / Reliability | 400-day run purge will `SET NULL` ~1 M `price_daily` rows through an unindexed FK, inside the session-purge transaction | S | yes |
| AR-19 | Low | API / Performance | Per-request fan-out: `data_version()` per series, N+1 point queries, caches and limits per process | S | yes |
| AR-20 | Low | Performance | Admin pipeline status runs full-table counts and is polled every 3 s | S | yes |
| AR-21 | Low | API | Routing 404/405 bypass the error envelope; responses untyped | S | yes |
| AR-22 | Low | Data | "Today" is computed in UTC while the business zone is Asia/Jakarta; a naive `datetime.now()` is used | S | yes |
| AR-23 | Low | Security / Deployability | Image defaults: `FORWARDED_ALLOW_IPS=*`, pytest in the runtime, unpinned bases, root Nginx | S | yes |
| AR-24 | Low | Maintainability | Stale and unused definitions: `rbac.PERMISSIONS`, `APP_SECRET_KEY`, hard-coded `EXPECTED_REVISION` | S | yes |
| AR-25 | Low | Maintainability | No backend lint/type check, no frontend tests/lint, no tests for worker recovery or the LSEG client | M | no |
| AR-26 | Low | Security | `aastar_ro` can read `ops.audit_logs` and `ops.integration_tokens` | S | yes |
| AR-27 | Low | Security | SSO: nonce optional, auth code in Nginx access logs, no Hub logout | S | partly |
| AR-28 | Low | Security | Office/zip parsing without decompression limits | S | no |
| AR-29 | Low | Reliability | LSEG client keeps a DB transaction open for the whole extraction; HTTP 429 not retried | S | yes |

\* AR-03 needs the free subnet confirmed with infra first; after that it is a one-line compose change.

## High

### AR-01: Production started from a copy of staging inherits synthetic data, sessions, IdP bindings and staging-encrypted secrets
- **Evidence:**
  - Staging-style data is synthetic: on QA, `SELECT source, count(*) FROM market.price_daily` → `demo | 260869` (100 %). The staging env example leaves the LSEG credentials empty ("leave empty for demo mode").
  - The worker backfills only an **empty** price table: `bootstrap_data_if_empty` (`backend/app/worker.py:50-58`). With a copy, only `incremental` runs happen (7-day lookback), so the synthetic history stays.
  - After the first successful LSEG run, `runner.py:201` sets `data.mode=live`, so the demo banner (`AppShell.tsx:216`) disappears while years of synthetic prices remain in the charts.
  - Other state carried over: valid `iam.sessions` (token hashes keep working until expiry), `iam.oidc_login_states`, `ops.integration_tokens` / `ops.integration_secrets` (encrypted with the **staging** `APP_ENCRYPTION_KEY`; with a new prod key the Google SA key silently stops decrypting, `sheets/sources.py:74`), `iam.users.hub_sub` bound to the **staging Hub's** subjects (if the prod Hub issues other `sub` values, every SSO user is rejected with "linked to a different Hub account", `sso.py:202,213`), `ops.audit_logs` (immutable for the app), `ops.jobs`, sheet uploads, AI reports and documents.
- **Impact:** Synthetic history shown as live data. Possible SSO lockout. Staging session tokens valid in prod. Audit trail mixes environments.
- **Recommendation:** Write and rehearse a promotion script:
  - Delete `price_daily WHERE source='demo'` and all `series_points`/`oi_*`, then run a `full` LSEG backfill and verify it.
  - `TRUNCATE iam.sessions, iam.oidc_login_states, ops.integration_tokens, ops.jobs`.
  - Reset `hub_sub` to NULL so users re-bind by verified email, or prove the prod Hub keeps the same subjects.
  - Keep the encryption key deliberately, or re-enter the SA key.
  - Decide on the audit log (owner disables the trigger once, or archives it).
  - Clear `data.mode`.

  Alternative: start from an empty DB (migrate + bootstrap) and copy only users, roles, overrides and the wanted intel uploads.
- **Effort:** M · **Quick fix:** no

### AR-02: Role and database names are hard-coded, so environments on a shared RDS instance cannot be isolated
- **Evidence:** `deploy/rds/bootstrap_roles.sql:9-11` creates fixed `aastar_owner/app/ro`, and `:17` creates `aastar`. Roles are instance-wide. All migrations grant only to those literal names behind `IF EXISTS (… rolname='aastar_app')` (`0001_initial.py:322`, also 0002, 0004, 0006, 0007). `CONNECTION LIMIT 40` is per role (`:10`).
- **Impact:** Prod on the staging instance must either reuse `aastar_app` (same password, one shared limit of 40: 27 + 27 > 40 means refused connections) or rename the roles, in which case the migrations silently skip every grant and the app gets "permission denied". `CREATE DATABASE aastar` also clashes.
- **Recommendation:** Parameterise role and DB names (psql variables plus an Alembic `-x`/env setting used by every grant block), or grant to group roles that each environment's login role joins. Separate passwords and connection limits per environment.
- **Effort:** M · **Quick fix:** no

### AR-03: Docker networks not pinned; the default pool covers 172.28.0.0/16
- **Evidence:** The staging compose files declare no `networks:` (grep for networks/mem_limit/cpus finds nothing). Docker's default pools are 172.17–172.31/16. The hosts are 172.28.92.x and 172.28.80.x.
- **Impact:** If the project network gets 172.28.0.0/16, the host routes the corporate range into the bridge. FE↔BE, the NAS and the RDS path break for every app on the host.
- **Recommendation:** Pin an agreed free /24 per tier (`networks.default.ipam.config.subnet`) or set `default-address-pools` in `daemon.json`. Add an overlap pre-check to `deploy.sh`.
- **Effort:** S · **Quick fix:** yes, once the subnet is confirmed

### AR-04: Plain HTTP on a shared IP exposes the session cookie
- **Evidence:** `Secure` is set only for https (`config.py:102-104`, `routes/auth.py:29-32`). Prod is planned on `http://172.28.80.50:3170`. Cookies are scoped by host, not port, so `aastar_session` is sent to every other app on that IP, and `aastar_csrf` is readable by their JavaScript. SameSite does not separate them. Staging already shares its IP with the ERP, ECR and the Hub. The guide's assumption "stays strict on HTTPS production" (`06-STAGING-DEPLOYMENT.md:565`) no longer holds. What still protects: CSRF double-submit plus the Origin allow-list.
- **Impact:** Co-hosted apps (their logs, operators, or an XSS in them) receive valid sessions, including admin sessions. Credentials cross the network in cleartext.
- **Recommendation:** Own hostname + TLS before go-live (`APP_BASE_URL=https://…` turns on Secure and HSTS), consider `__Host-` cookies. Otherwise record a formal risk acceptance, shorten the absolute session lifetime, and limit which apps share the IP.
- **Effort:** M · **Quick fix:** no

## Medium

### AR-05: Default pool sizing exceeds the connection limit
- **Evidence:** `db.py:23-24` (pool from `DB_POOL_SIZE`, default 10 at `config.py:33`; `max_overflow=5` hard-coded). `Dockerfile:8` `WEB_CONCURRENCY=3` gives 3×15 + worker 15 = 60. `systemd/aastar-api.service:16` `-w 4` gives 75. Limit is 40. Only `staging/backend.env.example:18-19` overrides it (27 connections; the guide says 36, `06:223`). The shared instance's `max_connections` budget is undocumented.
- **Recommendation:** Safe defaults (pool 4, configurable overflow 2–3, 2 workers), a smaller pool for the worker, a documented budget per environment, and a startup warning when the total exceeds it.
- **Effort:** S · **Quick fix:** yes

### AR-06: No alerting, no worker liveness, no freshness signal
- **Evidence:** No alert or notify code. PRD DATA-10 (`02-PRD.md:155`) and OBS-04 are unmet. Worker `healthcheck: {disable: true}` (`docker-compose.backend.yml:39`). Readiness checks only the DB and the revision (`health.py:21-30`). The CloudMonitor/SLS from the runbook do not exist on the shared Docker hosts.
- **Recommendation:**
  - A worker heartbeat table and a worker healthcheck that reads it.
  - A `/api/health/data` endpoint with last-success age, heartbeat age and failed jobs in 24 h.
  - A webhook (DingTalk/email) on a failed scheduled run, two warning runs in a row, or failing sheet sources.
  - Point the host monitor at those endpoints.
- **Effort:** M · **Quick fix:** no

### AR-07: A worker crash leaves documents and AI reports stuck
- **Evidence:** `jobs.py:110-115` resets only `ops.jobs`. `news.py:304` sets the document to `processing`, and `reprocess` accepts only `failed|succeeded` (`news.py:252`). AI reports stay `pending` (`ai.py:342`) and the page polls every 3 s forever (`AiPage.tsx:85`).
- **Recommendation:** In `recover_interrupted`, mark orphaned `processing`/`queued` documents and `pending` reports as `failed` ("Interrupted"). Add a test.
- **Effort:** S · **Quick fix:** yes

### AR-08: Upload endpoints block the event loop
- **Evidence:** `routes/intel.py:77,215` and `routes/sheets.py:136` are `async def` but call openpyxl, pandas and sync DB work directly (`intel.py:82`, `sheets.py:150-151`). Files can be 20–31 MB.
- **Impact:** Every request on that worker stalls, health checks included. Half the capacity with 2 workers. Gunicorn kills the worker after 120 s.
- **Recommendation:** Make them plain `def` handlers (FastAPI runs those in the threadpool) or use `run_in_threadpool`.
- **Effort:** S · **Quick fix:** yes

### AR-09: Synthetic fallback active in production
- **Evidence:** `runner.py:183` has no environment check. QA runs `APP_ENV=prod` with `data_mode: demo` (`/api/auth/config`). `worker.py:56` queues `demo_seed` when LSEG is not configured.
- **Recommendation:** In prod, a missing LSEG configuration makes fetching runs fail and refuses `demo_seed`.
- **Effort:** S · **Quick fix:** yes

### AR-10: No CI, no gate, no build-once-promote
- **Evidence:** No CI config. `deploy.sh:79` builds on each host with `build --pull`, without pytest. PRD DEP-05 (`02-PRD.md:302`) and SEC-07 (`:242`) are unmet.
- **Recommendation:** A pipeline with pytest (PG18 service), the FE build, `pip-audit` and `npm audit`. Build SHA-tagged images, push them to ACR, and have `deploy.sh` pull the tested tag. Pin base images by digest.
- **Effort:** M · **Quick fix:** no

### AR-11: Secrets exposed via `docker inspect` on shared hosts
- **Evidence:** `docker-compose.backend.yml:10` `env_file` holds the DB passwords, the encryption key, the LSEG and Anthropic keys and the bootstrap admin password. KMS is in the docs but not used.
- **Recommendation:** Restrict Docker access on the hosts. Use mounted secret files with `*_FILE` settings, or KMS at start. Remove the bootstrap password after first use. Rotate the staging keys before the copy.
- **Effort:** M · **Quick fix:** no

### AR-12: DB and IdP transport not enforced encrypted
- **Evidence:** `sslmode=prefer` (`backend.env.example:22-23`). `HUB_ISSUER=http://…` (`:37`).
- **Recommendation:** RDS SSL with `verify-full` (at least `require`), the HTTPS Hub in prod, and reject `http://` issuers when `APP_ENV=prod`.
- **Effort:** S · **Quick fix:** yes

### AR-13: No container resource limits
- **Evidence:** No `mem_limit`/`cpus` in the compose files. The worker loads ~1 M price rows into pandas (`store.py:88-113`) and parses 30 MB files.
- **Recommendation:** `mem_limit` and `cpus` per service, and `stop_grace_period: 60s` for the worker. Measure peak memory during a full run.
- **Effort:** S · **Quick fix:** yes

### AR-14: Auto-migrations without a backup; rollback claim is false
- **Evidence:** `alembic upgrade head` runs on every deploy (`docker-compose.backend.yml:23`) with no backup step. The guide (`06:505`) claims migrations are additive, but 0003 drops a table and 0005 drops a column. Downgrades are untested.
- **Recommendation:** Back up (RDS manual backup or `pg_dump -Fc`) before migrate when not at head. Use expand/contract for destructive changes. Fix the guide.
- **Effort:** S · **Quick fix:** yes

### AR-15: Operator documentation drift
- **Evidence:**
  - `05-DEPLOYMENT-RUNBOOK.md:126-199` describes ECS/ALB/HTTPS/systemd/KMS, not the shared-Docker HTTP plan.
  - `deploy/nginx/aastar.conf:40` hard-codes `X-Forwarded-Proto https`; `deploy/systemd/aastar-api.service:16` uses `-w 4`.
  - The rotation advice (`05:197`) ignores that a new encryption key also kills the Google SA key and in-flight OIDC states.
  - `03-TECHNICAL-DESIGN.md:275` describes `APP_SECRET_KEY`, which is unused.
  - `06:223` says 36 connections; the actual figure is 27.
- **Recommendation:** A production guide modelled on doc 06 (subnet, roles, connection budget, the AR-01 promotion, backups). Retire `deploy/nginx` and `deploy/systemd`. Fix the rotation and key tables.
- **Effort:** S · **Quick fix:** yes

### AR-16: Serial job queue without timeouts
- **Evidence:** `jobs.py:89-101` runs jobs one at a time with no handler timeout. Claude uses the SDK default timeout (10 min) with `max_retries=3` (`claude.py:41`) and up to 5 `pause_turn` rounds (`claude.py:77-85`).
- **Impact:** One slow calendar or document job delays the post-pipeline `sheet_refresh`.
- **Recommendation:** Timeouts per job kind, and a separate lane (or priority) for `sheet_refresh`.
- **Effort:** S · **Quick fix:** no

## Low

### AR-17: Retention gaps
- **Evidence:** Housekeeping (`worker.py:40-47`) purges only sessions, runs and OIDC states. Unbounded: `ops.jobs`, `intel.documents` (bytea ≤30 MB each, `0006_intel.py:109`, kept in every backup), `ai_reports`, and `audit_logs` (the trigger at `0001:155` blocks purges; the archive is manual).
- **Recommendation:** Purge old jobs, set a document policy (or move blobs to OSS), and script the audit archive.
- **Effort:** M · **Quick fix:** no

### AR-18: The run purge will rewrite ~1 M price rows through an unindexed FK
- **Evidence:** `price_daily.run_id ON DELETE SET NULL` (`0001:265`) has no index. On QA all 260,869 rows reference one run. The purge (`worker.py:42-43`) shares a transaction with the session purge under the 120 s timeout.
- **Impact:** About 400 days after go-live, ~1 M rows are updated in one go; it may time out every night and take the session purge with it.
- **Recommendation:** Drop the FK or index it and skip referenced runs. Separate transactions.
- **Effort:** S · **Quick fix:** yes

### AR-19: Request fan-out and per-process state
- **Evidence:** `data_version()` runs inside every cached call (`market.py:77,85,118,207`): 15 or more queries per section request even on cache hits. One points query per series (`:135`) and per family × contract (`:270`). `deps.py:68` loads effective modules on every request. Caches and limits are per worker.
- **Recommendation:** Resolve the version once per request, fetch a section's points with one `ANY(:keys)` query, and optionally cache module access briefly.
- **Effort:** S · **Quick fix:** yes

### AR-20: Polled admin status runs full counts
- **Evidence:** `admin.py:251-253`. E8: 180 ms on QA, ~1 s at prod volume. Polled every 3 s during runs, 20 s otherwise (`PipelinePage.tsx:64,69`).
- **Recommendation:** Use `reltuples` estimates or store the counts at publish time.
- **Effort:** S · **Quick fix:** yes

### AR-21: Error envelope gaps and untyped responses
- **Evidence:** `curl /api/openapi.json` returns `404 {"detail":"Not Found"}`. Only FastAPI's `HTTPException` is handled (`errors.py:32`). No `response_model` anywhere; the TS types are maintained by hand.
- **Recommendation:** Handle `starlette.exceptions.HTTPException`. Add response models gradually.
- **Effort:** S · **Quick fix:** yes

### AR-22: Business dates in UTC
- **Evidence:** `date.today()` in `routes/market.py:114`, `routes/sheets.py:56,66`, `services/market.py:173`, `calendar.py:115,140`, `news.py:209`. Naive `datetime.now()` in `news.py:355`. The business zone is Asia/Jakarta.
- **Impact:** Off by one day between 00:00 and 07:00 WIB.
- **Recommendation:** A `business_today()` helper using ZoneInfo, and `datetime.now(UTC)` everywhere.
- **Effort:** S · **Quick fix:** yes

### AR-23: Image defaults
- **Evidence:** `Dockerfile:9` `FORWARDED_ALLOW_IPS=*` allows spoofed client IPs if the override is forgotten (login rate-limit bypass, false audit IPs). pytest is in the runtime image (`requirements.txt:27`). Bases are not digest-pinned. The Nginx master runs as root. No `cap_drop`/`read_only`.
- **Recommendation:** Default to `127.0.0.1`, split dev requirements, use an unprivileged Nginx image, add `cap_drop: [ALL]`, `read_only` and `no-new-privileges`.
- **Effort:** S · **Quick fix:** yes

### AR-24: Stale and unused definitions
- **Evidence:** `rbac.py:9` `PERMISSIONS` lacks `documents.upload` (`bootstrap.py:25`). `APP_SECRET_KEY` is required (`config.py:24`) but unused. `EXPECTED_REVISION` is hard-coded (`health.py:12`).
- **Recommendation:** Update or remove the constant, drop the key or give it a use, and read the Alembic head at start.
- **Effort:** S · **Quick fix:** yes

### AR-25: Tooling and test gaps
- **Evidence:** 64 tests, strong on formulas and security. None cover recovery, lock contention, LSEG retry/poll, housekeeping or downgrade. No ruff/mypy, no frontend tests or ESLint, 20 `any` in `charts.tsx`. The fixture drops a shared `aastar_test`.
- **Recommendation:** Linters in CI, recovery tests, LSEG `MockTransport` tests, and a test DB name per run.
- **Effort:** M · **Quick fix:** no

### AR-26: Read-only role sees audit data and tokens
- **Evidence:** `0001:332` grants SELECT on all `ops` tables. Verified with `has_table_privilege`: true for `ops.audit_logs` and `ops.integration_tokens`.
- **Recommendation:** Revoke in a new migration.
- **Effort:** S · **Quick fix:** yes

### AR-27: SSO details
- **Evidence:** The nonce is checked only when present (`sso.py:147`). The callback `code`/`state` land in Nginx access logs. No Hub end-session on logout.
- **Recommendation:** Require the nonce, use a log format without query strings for the callback, consider RP-initiated logout.
- **Effort:** S · **Quick fix:** partly

### AR-28: Unbounded decompression of uploads
- **Evidence:** `openpyxl` (`sources.py:125`, `snd.py:65`, `news.py:267`) and `python-docx` have no limit on uncompressed size. Uploads need admin or `documents.upload`.
- **Recommendation:** Check the total uncompressed size and a row cap before parsing.
- **Effort:** S · **Quick fix:** no

### AR-29: LSEG client DB transaction and 429 handling
- **Evidence:** `lseg/client.py:46` reads the token through a session kept for the whole run, so the connection stays "idle in transaction" through polls of up to 30 min (`:125`). `:82` returns any status below 500, so a 429 fails the run.
- **Recommendation:** A short-lived session for the token, and retry 429 honouring `Retry-After`.
- **Effort:** S · **Quick fix:** yes

## Strengths worth keeping
- **Formulas protected by tests:** golden tests against legacy `process.py`/`oi_cumulative.py`, plus parity tests for every sheet source.
- **Clean layering:** pure `engine/`, bulk SQL in `store.py`, thin routes, a single Claude gateway, and the catalog as code synced by an idempotent bootstrap.
- **Efficient writes:** COPY into temp tables with change-only upserts, and scoped orphan deletes so writers never delete each other's points. Hot reads are PK index scans (≤20 ms on QA; about 0.1–1.4 s for the bulk paths at prod scale).
- **Sound concurrency:** advisory locks per database (safe on a shared instance), `SKIP LOCKED`, a one-running-run unique index, job de-duplication, start-up recovery.
- **Simple cache invalidation:** `data_version` replaces explicit invalidation, and LSEG and sheet publishes share one sequence.
- **Security fundamentals:** Argon2id with timing equalisation and lockout; hashed server-side sessions with idle and absolute expiry; CSRF double-submit plus Origin allow-list; module checks per series; BFF OIDC with PKCE S256, single-use state, JWKS, an algorithm allow-list and `email_verified`; strict CSP with no CDNs; hidden source maps.
- **Audit trail:** written in the same transaction as the change; the app role cannot update or delete it and a trigger backs that up; secrets redacted. Plus least-privilege DB roles and SQL-first Alembic migrations.
- **Staging kit:** port guard, migrate gate, health wait, smoke tests including the CSP, image rollback, deployment log, and the rehearsal lessons written into the guide.
- **Frontend:** ≈104 kB gzip initial load, plotly and intel pages lazy, sensible query retries, polling only while work is active, and a request-id error model.
- **Logging:** JSON logs with request and user ids, and `X-Request-ID` carried from Nginx through the API to the client.
