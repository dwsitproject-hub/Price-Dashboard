# 05 — Deployment & Operations Runbook

| | |
|---|---|
| **Date** | 2026-11-04 |
| **Scope** | Local laptop (verified) · SIT / UAT / PROD on Alibaba Cloud (3 servers) |
| **Design** | [03-TECHNICAL-DESIGN.md §10](03-TECHNICAL-DESIGN.md#10-deployment--alibaba-cloud-3-servers) |

---

## 1. Local development (this laptop)

Ports were chosen because they were free here. Ports already in use on this machine: 3100, 3306, 4000
(local DWS Hub), 4100, 4242, 5001, 5050, 5173, 5432–5434, 5443, 8095.

| Component | Address | Start |
|---|---|---|
| PostgreSQL 18.6 | `127.0.0.1:5544` | `docker start aastar-pg18` (or `docker compose -f deploy/docker-compose.local.yml up -d`) |
| Backend API | `http://127.0.0.1:8710` | see §1.2 |
| Pipeline worker | — | see §1.2 |
| Frontend (Vite) | **http://localhost:5181** | see §1.3 |

### 1.1 First-time setup (already done on this laptop)

```bash
docker volume create aastar-pg18-data
```

```bash
docker run -d --name aastar-pg18 --restart unless-stopped -e POSTGRES_USER=aastar -e POSTGRES_PASSWORD=aastar_local_dev -e POSTGRES_DB=aastar -p 127.0.0.1:5544:5432 -v aastar-pg18-data:/var/lib/postgresql postgres:18
```

Then, from `backend/`:

1. Create the venv: `py -3.12 -m venv .venv`, then `.venv/Scripts/python -m pip install -r requirements.txt`.
2. Copy `.env.example` to `.env` and fill in keys (`python -m app.cli gen-keys`), the DB URLs and the bootstrap admin.
3. Create the least-privilege runtime role `aastar_app` (see `deploy/rds/bootstrap_roles.sql`).
4. Run `.venv/Scripts/python -m alembic upgrade head`.
5. Run `.venv/Scripts/python -m app.cli bootstrap` (roles, modules, 347 instruments, 171 series, first admin).
6. Run `.venv/Scripts/python -m app.cli run --kind demo_seed` (about 30 s: 509 k synthetic price rows → 171 series).

### 1.2 Run the backend (two terminals, from `backend/`)

```bash
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8710 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

```bash
.venv/Scripts/python -m app.worker
```

### 1.3 Run the frontend (from `frontend/`)

```bash
npm install
```

```bash
npm run dev
```

Open http://localhost:5181 and sign in as `admin@aastar.local`. The current password is in `backend/.env`
(`LOCAL_ADMIN_CURRENT_PASSWORD`). A demo viewer account (`viewer.demo@aastar-demo.com`) was created during
verification; its password is also in `backend/.env`.

### 1.4 Tests

```bash
cd backend && .venv/Scripts/python -m pytest -q
```

32 tests: the golden comparison against legacy `process.py`/`oi_cumulative.py`, conversions, password policy,
OIDC validation (bad aud/iss/exp/nonce/HS256), CSRF, lockout, RBAC/IDOR, last-admin guard, pipeline
idempotency, and security headers. They use a separate database, `aastar_test`, which is created and dropped
automatically.

### 1.5 Enable DWS Hub SSO locally (local Hub on :4000)

1. Hub Admin → Applications → **New application**: name *AASTAR Price Dashboard (local)*, `sso_mode = oidc`,
   `oauth_client_id = aastar-local`, `oidc_redirect_uris = http://localhost:5181/api/auth/sso/callback`,
   Target URL `http://localhost:5181/api/auth/sso/login`.
2. In `backend/.env`, set `SSO_ENABLED=true` and restart the API. The login page then shows **Sign in with Downstream Hub**.
3. Register the SSO user first (Admin → Users & access → sign-in method *Hub SSO* or *Password + SSO*, same
   email as in the Hub), or set `SSO_AUTO_PROVISION=true`.
4. Sanity checks: `curl -i http://localhost:4000/api/sso/jwks` → 200; `/.well-known/openid-configuration` → 200.

### 1.6 Enable the Hub SDK locally

Hub Admin → Applications → AASTAR → **SDK**. Copy the key, then add `http://localhost:5181` under **Allowed
origins**. Create `frontend/.env.local`:

```
VITE_HUB_SDK_URL=http://localhost:4000/api/sdk/v1/hub-sdk.js
VITE_HUB_SDK_KEY=dwsk_…
```

Restart `npm run dev`. Within a minute, Hub → Applications → SDK should show **Receiving data**.

### 1.7 Full stack in Docker (mirrors the 3-server layout)

`docker-compose.yml` runs `web` (Nginx + SPA) → `api` + `worker` → `db` (PostgreSQL 18). A one-shot `migrate`
job runs Alembic and the bootstrap before the API starts. It is independent of the dev servers above: it has
its own database volume and its own ports.

| Service | Address | Notes |
|---|---|---|
| web | **http://localhost:5190** | the only published app port |
| db | `127.0.0.1:5545` | owner role `aastar_owner`; the app connects as least-privilege `aastar_app` |
| api / worker | internal `api:8000` | not published; reached only through `web` |

1. Copy `deploy/docker/.env.example` to `deploy/docker/.env` (git-ignored) and fill in the passwords, keys and the
   bootstrap admin. On this laptop the file has already been generated.
2. Build and start:

   ```bash
   docker compose --env-file deploy/docker/.env up -d --build
   ```

3. On first start the worker detects an empty database and seeds demo data (about 1 minute). Sign in as
   `admin@aastar.local` with `BOOTSTRAP_ADMIN_PASSWORD` from `deploy/docker/.env`; you must change it at first
   sign-in.
4. Logs: `docker compose --env-file deploy/docker/.env logs -f api worker`. Stop with `docker compose --env-file deploy/docker/.env down`
   (add `-v` to also delete the database volume).

---

## 2. Alibaba Cloud — per environment (SIT, UAT, PROD)

### 2.1 Provision (IT Infra)

| # | Resource | Setting |
|---|---|---|
| 1 | VPC + vSwitches | `10.20.0.0/16`; `public-a 10.20.1.0/24`; `private-a 10.20.10.0/24` |
| 2 | NAT Gateway + EIP | SNAT for `private-a` (egress to LSEG, the Hub and OS mirrors) |
| 3 | **Server 3** ApsaraDB RDS for PostgreSQL **18** | PROD: High-availability, 2 vCPU/8 GiB, 100 GiB ESSD; SSL on; whitelist = backend private IP only; backups 7 d (PROD 30 d) + log backup; SQL audit on (PROD) |
| 4 | **Server 2** ECS backend | `ecs.g7.xlarge`, Alibaba Cloud Linux 3, **no EIP**, vSwitch `private-a`, SG `sg-be` (8000 from `sg-fe`) |
| 5 | **Server 1** ECS frontend | `ecs.c7.large`, vSwitch `public-a`, SG `sg-fe` (80 from ALB) |
| 6 | ALB | HTTPS 443 with a managed certificate, HTTP→HTTPS redirect, health check `GET /healthz` |
| 7 | KMS Secrets Manager | `aastar/<env>/backend`: DB URLs, `APP_SECRET_KEY`, `APP_ENCRYPTION_KEY`, LSEG credentials, bootstrap admin |
| 8 | Log Service (SLS) | Logtail on both ECS: journald units `aastar-api`, `aastar-worker`, and Nginx access/error logs |
| 9 | CloudMonitor | Alarms: CPU > 80 % for 10 min, disk > 80 %, RDS connections > 80 %, ALB unhealthy host, `GET /api/health/ready` ≠ 200 |

### 2.2 Database (once)

```bash
psql "host=<rds-endpoint> dbname=postgres user=<privileged> sslmode=require" -v owner_pw="'…'" -v app_pw="'…'" -v ro_pw="'…'" -f deploy/rds/bootstrap_roles.sql
```

### 2.3 Backend server (Server 2)

1. `useradd --system --home /opt/aastar aastar` and install Python 3.12.
2. Release layout: `/opt/aastar/backend/releases/<sha>`, with `current` as a symlink and the venv at `/opt/aastar/backend/venv`.
3. Write `/etc/aastar/backend.env` (`0600 root:aastar`) from KMS. Set `APP_ENV=<env>`,
   `APP_BASE_URL=https://<host>`, `APP_ALLOWED_ORIGINS=https://<host>`, `HUB_ISSUER=https://dwshub.kpndomain.com`,
   `HUB_CLIENT_ID=aastar-<env>`, `HUB_REDIRECT_URI=https://<host>/api/auth/sso/callback` and `SSO_ENABLED=true`.
4. Deploy: `alembic upgrade head` (with `MIGRATION_DATABASE_URL`) → `python -m app.cli bootstrap` → install
   `deploy/systemd/*.service` → `systemctl enable --now aastar-api aastar-worker`.
5. Verify: `curl -s localhost:8000/api/health/ready` returns `{"status":"ready"}`.
6. First data: Admin → Data pipeline → **Full backfill** (off-hours; chunked by year × 100 RICs), or
   `python -m app.cli run --kind full`.

### 2.4 Frontend server (Server 1)

1. Install Nginx 1.26 and copy `deploy/nginx/aastar.conf`. Replace the host, the backend IP and the Hub origin.
2. CI builds with `VITE_HUB_SDK_URL`, `VITE_HUB_SDK_KEY` (that environment's key) and `VITE_GIT_SHA`. Upload
   `dist/` to `/opt/aastar/frontend/releases/<sha>`, switch the `current` symlink, then run `nginx -s reload`.
3. Verify: `https://<host>/healthz` → `ok`; the login page loads; response headers include the CSP.

### 2.5 DWS Hub registration (Hub Admin, per environment)

| Field | Value |
|---|---|
| `sso_mode` | `oidc` |
| `oauth_client_id` | `aastar-sit` / `aastar-uat` / `aastar-prod` |
| `oidc_redirect_uris` | `https://<host>/api/auth/sso/callback` |
| Target URL | `https://<host>/api/auth/sso/login` |
| SDK → Allowed origins | `https://<host>` |
| Sentinel → repository / branch / folder | `dwsitproject-hub/Price-Dashboard` · `SIT` / `UAT` / `main` · `frontend/` |
| Sentinel → notes | the text in TD §11.3 |
| Sentinel → GitHub token | fine-grained, this repository only, *Contents: Read* + *Metadata: Read* |

---

## 3. Operations

| Task | How |
|---|---|
| Register a user | Admin → Users & access → New user (temporary password shown once) |
| User locked out | Admin → Users → open user → **Unlock** |
| Force sign-out | Admin → Users → **Revoke sessions** (or disable the user) |
| Data looks stale | Admin → Data pipeline: check the last run's steps and DQ findings → **Run incremental** |
| LSEG token issues | Automatic: refreshed 1 h before expiry and once on 401. A failing `lseg_auth` step means credentials are wrong |
| Hub SSO down | Admins sign in with a password (break-glass; keep `AUTH_LOCAL_ENABLED=true` for admins) |
| Rotate secrets | Update KMS → rewrite `/etc/aastar/backend.env` → `systemctl restart aastar-api aastar-worker`. Changing `APP_ENCRYPTION_KEY` invalidates the stored LSEG token (it is re-fetched automatically) |
| Restore drill (quarterly) | RDS console → restore to a new instance at a point in time → point a scratch backend at it → `/api/health/ready` |
| Audit archive (monthly) | `COPY (SELECT … FROM ops.audit_logs WHERE occurred_at < date_trunc('month', now())) TO STDOUT` → gzip → OSS `aastar-audit-<env>/YYYY/MM.jsonl.gz` |
