# AASTAR Price Dashboard production deployment

How to put the AASTAR Price Dashboard into production on the shared production servers, step by step, and how to
run it afterwards. It is built the same way as staging ([06-STAGING-DEPLOYMENT.md](06-STAGING-DEPLOYMENT.md)): the
same images and checks. It runs on the production servers, a database on an existing production ApsaraDB instance
(shared with another application), and the **production** DWS Hub. Production **starts from a copy of the staging
database**: users, roles, prices, series, sheet snapshots and intelligence data.

```
Browser ──► FE server 172.28.80.50:3170   aastar-price-web (nginx: the web app; /api proxied)
            (plain HTTP on the private address - accepted risk AR-04; a name later, §11)
                      │ /api
                      ▼
            BE server 172.28.80.51:4170   aastar-price-api (FastAPI) + aastar-price-worker (scheduler, jobs)
                      │
                      ├──► ApsaraDB RDS PG18 (another app's production instance)  database aastar, roles aastar_*
                      ├──► LSEG DataScope Select   selectapi.datascope.lseg.com:443   (prices, 08:00 WIB Mon–Fri)
                      ├──► Claude API              api.anthropic.com:443              (AI narrative, News, Calendar)
                      ├──► CFTC                    www.cftc.gov:443                   (COT, Saturdays)
                      ├──► Google Sheets           docs.google.com, sheets/oauth2.googleapis.com:443
                      └──► production DWS Hub      dwshub.kpndomain.com:443 → 172.28.80.50 (OIDC token, signing keys)
Browser ──► production DWS Hub  (sign-in; Hub SDK script and analytics, Sentinel)
```

| | FE server 172.28.80.50 (ECS-App) | BE server 172.28.80.51 (ECS-DB) |
|---|---|---|
| Containers | `aastar-price-web` | `aastar-price-api`, `aastar-price-worker` (+ `aastar-price-migrate`, one-shot) |
| Port | **3170** on 172.28.80.50 only | **4170** on 172.28.80.51 only |
| Compose file | `deploy/production/fe.compose.yml` | `deploy/production/be.compose.yml` |
| Settings (filled on the server) | `/opt/aastar-price/.env`, `fe-build.env` | `/opt/aastar-price/.env`, `production.env`, `secrets.production.env` |
| Deploy | `/opt/aastar-price/src/deploy/production/release.sh fe TAG` | `/opt/aastar-price/src/deploy/production/release.sh be TAG` |
| Code | `/opt/aastar-price/src`, at a release tag | `/opt/aastar-price/src`, at the same release tag |

Do the sections in order. Sections 1–6 are one-time preparation; section 7 onwards is the deployment itself.

**Conventions.** Every command block runs on the machine named above it, as `root` in PuTTY. Passwords are typed into
`nano`, never onto a command line. A command block never contains `<…>` (bash reads `<` as a redirect): values to edit
are written `FILL_…` and changed before pressing Enter. The release tag in the examples is `v1.0.0`.

---

## 1. Ports and the Docker network

| Server | Host ports known to be taken (Nov 2026 records and screenshots; the preflight re-checks on the day) | This app |
|---|---|---|
| FE 172.28.80.50 | 2001, 3001, 3002, 3020, 3040, 3042, 3050, 3070, 3080, 3091, 3100, 3150, 3160, 8000, 8080, 8081, 8090, 8443; host nginx 80/443; host services 53, 1818, 3010, 3081, 9999 | **3170** on 172.28.80.50 |
| BE 172.28.80.51 | 3000, 3003, 3011, 4000, 4001, 4100, 4150, 4160, 5000, 5001, 5003, 5010, 5050, 5070, 5433, 5434, 8001, 8091, 13000; host services 53, 1818 | **4170** on 172.28.80.51 |

3170 / 4170 are the staging ports too. Only *published* host ports clash: `0.0.0.0:3160->80/tcp` takes 3160, while
`3000/tcp` without `->` is a port inside a container and does not count. `docker ps` cannot see host services; the
preflight (§5) checks both with `ss` and `docker ps -a` (stopped containers still reserve their ports). If it reports
a port taken, it prints the first free one: put it in `/opt/aastar-price/.env` (`AASTAR_PRICE_API_BIND`,
`AASTAR_PRICE_WEB_BIND`, `AASTAR_PRICE_API_UPSTREAM`, see `compose.env.example`) and, for the FE port, in
`APP_BASE_URL`, `APP_ALLOWED_ORIGINS`, `HUB_REDIRECT_URI` and the Hub registration.

**Why nothing here cuts other apps off from the NAS.** This app does not use the NAS and mounts nothing. Its Docker
network `aastar-price_net` gets a fixed /24 (`AASTAR_PRICE_SUBNET`, no default) that the preflight chooses outside
172.28–172.31 (the servers' LAN and the NAS, 172.30.1.94); an auto-allocated 172.30 network once cut a whole staging
host off from the NAS. Nothing in this guide restarts Docker, edits `daemon.json`, prunes, or touches another app's
network, container, mount or nginx site.

## 2. Before you start: who provides what

| Item | From | Used in |
|---|---|---|
| Production ApsaraDB instance **host** (PostgreSQL **18**), its privileged account for one step, 172.28.80.51 on its whitelist | the instance's owner / IT infra | §3 |
| Staging database owner password (`aastar_owner`, staging `.env.backend`) and, for the copy, either 172.28.80.51 on the **staging** RDS whitelist for an hour or a file transfer from 172.28.92.57 (WinSCP via the workstation) | team vault / IT infra | §6 |
| AASTAR Price Dashboard registered in the **production** Hub: client id, redirect URI, SDK key; Sentinel's GitHub token covering `Price-Dashboard` | Hub administrator, GitHub org owner | §4 |
| Read-only deploy key per server on `dwsitproject-hub/Price-Dashboard` | GitHub repository admin | §5 |
| LSEG DataScope Select production account | market data team | §7 |
| Claude API key for production (an `sk-ant-…` key with a usage limit) | AI Champion / API admin | §7 |
| Google service-account key (JSON) with read access to the private sheets (China and India parity) | Google Workspace admin | §9 |
| Firewall rules below | IT infra | §5 checks them |
| Release tag `v1.0.0` on `main` | you (§12 steps 1–2) | §5, §7, §8 |

**Decisions to make before go-live**

- **Address: http://172.28.80.50:3170, plain HTTP on the private network — accepted risk AR-04** (architecture
  review). Session cookies are not `Secure` and sign-in travels unencrypted inside the VPC; reachable only from the
  office network / VPN. A name and HTTPS later: §11 (the vhost file is ready, a small release is needed first).
- **Shared database instance.** The database `aastar` and roles `aastar_owner`, `aastar_app` (max 40 connections),
  `aastar_ro` (5) live on another application's instance. Its owner must agree to the connections (18 in normal use),
  the disk (a few GB of prices and series) and a 08:00 WIB weekday load. The instance must be PostgreSQL 18.
- **Staging copy.** Users, roles and data come from staging. Hub links are re-made by e-mail on the first production
  sign-in; password users keep their **staging** passwords until they or an admin change them (§9 step 2) — the app
  no longer forces a change. Agree a moment when nobody changes staging data.
- **AI reports are shared** between everyone with the *AI Analysis* module (PT-AC-07): confirm this is intended.

**Network rules**

| From | To | Why |
|---|---|---|
| Users (office / VPN) | 172.28.80.50:3170 | the app |
| Users | dwshub.kpndomain.com:443 | Hub sign-in page, SDK |
| 172.28.80.50 | 172.28.80.51:4170 | nginx → API |
| 172.28.80.51 | production RDS host:5432 | database |
| 172.28.80.51 | 172.28.80.50:443 (dwshub.kpndomain.com) | Hub discovery, token, signing keys |
| 172.28.80.51 | selectapi.datascope.lseg.com:443 | LSEG prices |
| 172.28.80.51 | api.anthropic.com:443, www.cftc.gov:443 | Claude, CFTC |
| 172.28.80.51 | docs.google.com, sheets.googleapis.com, oauth2.googleapis.com :443 | Google Sheet sources |
| both | github.com:22, registry-1.docker.io:443; BE pypi.org, files.pythonhosted.org; FE registry.npmjs.org | building the images |
| 172.28.80.51 | staging RDS :5432 (one hour), unless the dump is made on 172.28.92.57 | the one-time copy, §6 |

## 3. Database on ApsaraDB RDS (once)

The instance belongs to another application: this section creates **only** the `aastar_*` roles and the `aastar`
database and changes nothing else on it. Its privileged account is needed once (step 3); its owner can run that
step, or create the accounts in the console instead (staging guide §5.2, *console alternative*).

1. **Whitelist** — RDS console → the instance → *Whitelist*: add **172.28.80.51** (the instance may already list
   it for another app on this server).
2. **SSL** — RDS console → *Data Security* → *SSL*. If SSL is on, keep `sslmode=require` in the URLs (§7).
   If it is off, use `sslmode=prefer`: turning SSL on restarts the instance, which is the other application's
   decision.
3. **Roles and database. PuTTY on 172.28.80.51 (BE)**, after §5 (the checkout). First the privileged account, in a
   file deleted at the end of this step:

```bash
mkdir -p /opt/aastar-price && chmod 700 /opt/aastar-price
printf 'PGHOST=FILL_PROD_RDS_HOST\nPGPORT=5432\nPGUSER=FILL_PRIVILEGED_ACCOUNT\nPGPASSWORD=FILL\nPGSSLMODE=prefer\n' > /opt/aastar-price/rds-admin.env
chmod 600 /opt/aastar-price/rds-admin.env
nano /opt/aastar-price/rds-admin.env
```

In `nano` replace the three `FILL…` values (host, account, password), save with Ctrl+O, Enter, Ctrl+X. Check the
instance before creating anything:

```bash
docker run --rm --env-file /opt/aastar-price/rds-admin.env postgres:18-alpine psql -d postgres -XAt -c "show server_version" -c "select 'aastar roles: ' || count(*) from pg_roles where rolname like 'aastar%'" -c "select 'aastar database: ' || count(*) from pg_database where datname = 'aastar'"
```

| Line | Must be | If not |
|---|---|---|
| server version | **18.x** | **Stop.** The schema uses `uuidv7()` and virtual generated columns (PG18). Another instance is needed |
| `aastar roles: 0` | 0 | Roles from an earlier attempt: ask before dropping anything |
| `aastar database: 0` | 0 | Same |
| (no output, timeout) | — | Whitelist (step 1), firewall |

Then create the roles and the database with generated, alphanumeric passwords (no percent-encoding needed). The
checkout must exist first: `ls /opt/aastar-price/src/deploy/rds/bootstrap_roles.sql` lists the file (if not, §5 —
and if `/opt/aastar-price/src` exists without being a checkout, see §15):

```bash
umask 077
printf 'OWNER_PW=%s\nAPP_PW=%s\nRO_PW=%s\n' "$(openssl rand -hex 20)" "$(openssl rand -hex 20)" "$(openssl rand -hex 20)" > /opt/aastar-price/db-passwords.env
docker run --rm --env-file /opt/aastar-price/rds-admin.env --env-file /opt/aastar-price/db-passwords.env -v /opt/aastar-price/src/deploy/rds:/sql:ro postgres:18-alpine sh -c 'psql -d postgres -X -v owner_pw="$OWNER_PW" -v app_pw="$APP_PW" -v ro_pw="$RO_PW" -f /sql/bootstrap_roles.sql'
rm /opt/aastar-price/rds-admin.env
```

Expected: `CREATE ROLE` ×3, `GRANT ROLE`, `CREATE DATABASE`, then `You are now connected to database "aastar"`, the
grants and `ALTER ROLE` ×2. A `WARNING: setting an MD5-encrypted password` before each `CREATE ROLE` only means the
instance stores passwords as MD5 (its `password_encryption`, the owner's setting); sign-in works.
(First install on 19 Nov 2026: the production instance runs PostgreSQL 18.6.) Store the three passwords in the team vault (`cat /opt/aastar-price/db-passwords.env`
once, straight into the vault); §6 and §7 read them from this file and §7 deletes it.

4. **Backups** — the instance's automatic backups (RDS console → *Backup and Restoration*) cover `aastar` too;
   check with the owner that the retention is at least 7 days. `backup.sh` adds nightly dumps (§13).

The tables come from the staging copy (§6) and later releases' migrations. Nobody runs DDL by hand.

## 4. DWS Hub registration (once, in the **production** Hub)

The production Hub is `https://dwshub.kpndomain.com`, served by the FE server's host nginx. Check it from the BE
server the way the API container reaches it (its name pinned to 172.28.80.50):

```bash
curl -s --resolve dwshub.kpndomain.com:443:172.28.80.50 https://dwshub.kpndomain.com/api/sso/.well-known/openid-configuration
```

`issuer` must be `https://dwshub.kpndomain.com` (that exact string goes to `HUB_ISSUER`). Hub Admin →
**Applications** → add *AASTAR Price Dashboard*:

| Field | Value |
|---|---|
| `sso_mode` | `oidc` (strict: code + PKCE, JWKS) |
| `oauth_client_id` | `aastar-price` → `HUB_CLIENT_ID` |
| `oidc_redirect_uris` | `http://172.28.80.50:3170/api/auth/sso/callback` (exact: scheme, port, no trailing slash) |
| Target URL (tile) | `http://172.28.80.50:3170/api/auth/sso/login` |

A tile launch works as on staging (staging guide §8.2): the app restarts it as its own sign-in.

**Hub SDK** — the application → **SDK**: the script URL and the production key go to `fe-build.env` (§8).
**Allowed origins**: `http://172.28.80.50:3170`. Never use staging's key.

**Sentinel** — Admin → Analytic → *AASTAR Price Dashboard* → **Sentinel settings**: repository
`dwsitproject-hub/Price-Dashboard`, branch `main`, application folder **empty** (monorepo), analyse automatically ✓,
and the notes from staging guide §10 with `production (http://172.28.80.50:3170)`, `172.28.80.51:4170`, and "LSEG
data, no demo mode". The Hub's GitHub token must include `Price-Dashboard`. **Test repository access** must show
`main`.

## 5. The code on each server (once per server)

**PuTTY on each server (BE 172.28.80.51, then FE 172.28.80.50).** Each gets its own **read-only** deploy key (GitHub
→ Price-Dashboard → Settings → Deploy keys → *Add*, *Allow write access* off; GitHub refuses the same key twice),
wired to an SSH alias so other applications' `git pull` on the shared host is untouched:

```bash
ssh-keygen -t ed25519 -C "aastar-price production $(hostname)" -f /root/.ssh/id_ed25519_aastarprice -N ""
cat /root/.ssh/id_ed25519_aastarprice.pub
```

```bash
printf '\nHost github-aastarprice\n  HostName github.com\n  User git\n  IdentityFile /root/.ssh/id_ed25519_aastarprice\n  IdentitiesOnly yes\n' >> /root/.ssh/config
chmod 600 /root/.ssh/config
ssh -T git@github-aastarprice
```

Expected: *"Hi dwsitproject-hub/Price-Dashboard! You've successfully authenticated"* (answer `yes` to the host-key
question the first time).

```bash
mkdir -p /opt/aastar-price && chmod 700 /opt/aastar-price
git clone git@github-aastarprice:dwsitproject-hub/Price-Dashboard.git /opt/aastar-price/src
git -C /opt/aastar-price/src fetch --tags origin
git -C /opt/aastar-price/src checkout v1.0.0
```

Expected after the last line: `HEAD is now at …` (a "detached HEAD" note is normal for a tag). `pathspec 'v1.0.0' did
not match` means the tag is not on GitHub yet (§12 step 2); for the preparation (§3, §5, §6) the checkout may stay on
`origin/SIT` meanwhile (`git -C /opt/aastar-price/src checkout origin/SIT`): `release.sh` checks out the tag itself. Production runs a tagged release, not whatever `main` is
at. If a clone fails half-way, remove the leftover `/opt/aastar-price/src` before retrying.

**Preflight — read-only, on each server.** It changes nothing; paste the whole output back for review. Replace
`FILL_PROD_RDS_HOST` with the instance host before Enter:

```bash
RDS_HOST=FILL_PROD_RDS_HOST bash /opt/aastar-price/src/deploy/production/preflight.sh be      # on 172.28.80.51
```

```bash
bash /opt/aastar-price/src/deploy/production/preflight.sh fe                                  # on 172.28.80.50
```

| Line | Must be | If not |
|---|---|---|
| `port 4170 is free` / `port 3170 is free` | OK | Use the free port it prints (§1) |
| `suggested subnet for aastar-price_net: …` | OK — note it per server, for `AASTAR_PRICE_SUBNET` | `NONE`: ask infra for a /24 |
| `… is outside Docker's usual pool` | WARN is normal here (every 172.17–172.31 range is taken) | Confirm with infra that the office / VPN networks do not use it |
| `RDS host:5432 reachable` (BE) | OK | Whitelist (§3 step 1), firewall |
| `selectapi.datascope.lseg.com`, `api.anthropic.com`, `www.cftc.gov`, Google hosts (BE) | OK; the URL lines show any HTTP code (401/404 are fine) | Outbound 443 (§2) |
| `Hub discovery via 172.28.80.50` and `jwks -> 200` (BE) | OK | Firewall BE → FE :443 |
| `staging RDS reachable from here` (BE) | INFO either way | decides how §6 runs |
| `github.com:22`, `registry-1.docker.io`, `pypi.org` / `registry.npmjs.org` | OK | Outbound (§2) |
| `…/hub-sdk.js -> HTTP 200` (FE) | OK | The Hub's SDK route |
| `a Docker network of ANOTHER app holds a 172.30/31 range` | WARN, pre-existing (TAS, DHM) | Report to its owner; nothing to do here |
| `NAS mounts already on this host` | INFO, listed only | This app adds none |

Then, on **each** server, the Compose settings file with that server's subnet:

```bash
cp /opt/aastar-price/src/deploy/production/compose.env.example /opt/aastar-price/.env
nano /opt/aastar-price/.env
```

Set `AASTAR_PRICE_SUBNET=` to the suggested subnet the preflight printed **on that server**.

## 6. Production data: the staging copy (once, before §7)

This app keeps no files on the NAS; this slot is the one-time copy of the staging database into the empty `aastar`
database. `copy-staging-db.sh` refuses a database that already has tables, keeps the dump in
`/opt/aastar-price/backups`, checks the grants and the row counts, then removes what must not cross environments:
staging sessions and Hub links, the LSEG token and Google key (encrypted with staging's key), queued jobs, synthetic
prices, staging's audit log (one entry records the copy). Passwords are copied as they are (§9 step 2).

1. **Freeze staging** for the copy: tell its users; nobody edits users, roles or the atlas meanwhile. Staging's
   worker may keep running (the counts allow staging to be slightly ahead).
2. **PuTTY on 172.28.80.51 (BE)** — the connection file (deleted right after the copy):

```bash
cp /opt/aastar-price/src/deploy/production/copy-db.env.example /opt/aastar-price/copy-db.env
chmod 600 /opt/aastar-price/copy-db.env
set -a; . /opt/aastar-price/db-passwords.env; set +a
sed -i "s/FILL_PROD_OWNER_PASSWORD/$OWNER_PW/" /opt/aastar-price/copy-db.env
nano /opt/aastar-price/copy-db.env
```

In `nano`: `FILL_STAGING_OWNER_PASSWORD` → staging's `aastar_owner` password (vault, or staging's
`MIGRATION_DATABASE_URL`), `FILL_PROD_RDS_HOST` → the production instance host, and `require` → `prefer` if §3 step 2
found SSL off.

3. **Copy.** If the preflight said *staging RDS reachable from here*:

```bash
bash /opt/aastar-price/src/deploy/production/copy-staging-db.sh
```

When it ends with `Copy complete.`, skip the alternative below and go straight to the `rm` at the end of this section.

**Only if the staging RDS is not reachable from 172.28.80.51:** dump on the staging BE and restore here.
**PuTTY on 172.28.92.57 (staging BE)**, whose checkout must be on
the `SIT` commit that contains `deploy/production` (staging guide §6.1). A connection file with only the staging line
filled (the production line may keep its `FILL_` values in this mode):

```bash
mkdir -p /opt/aastar-price && chmod 700 /opt/aastar-price
cp /opt/aastar-price-dashboard/deploy/production/copy-db.env.example /opt/aastar-price/copy-db.env
chmod 600 /opt/aastar-price/copy-db.env
nano /opt/aastar-price/copy-db.env
```

```bash
bash /opt/aastar-price-dashboard/deploy/production/copy-staging-db.sh --dump-only
rm /opt/aastar-price/copy-db.env
```

Move the printed `/opt/aastar-price/backups/staging-copy-….dump` to `/opt/aastar-price/backups/` on 172.28.80.51
(WinSCP through the workstation, or `scp` if the two servers trust each other's keys). **PuTTY on 172.28.80.51 (BE)**,
with the file name it printed:

```bash
bash /opt/aastar-price/src/deploy/production/copy-staging-db.sh --restore /opt/aastar-price/backups/staging-copy-FILL.dump
```

Expected, in order: both server versions (production **18.x**, signed in as `aastar_owner` on `aastar`), `dump: …`,
`grants: aastar_app OK`, `schema version copied: 0008_hardening` (or older: §7 migrates it), the row counts with `OK`
(or `WARN staging wrote N more`), the list of removed items, `active users by role: …`, the number of password
users, `Copy complete.` Then:

```bash
rm /opt/aastar-price/copy-db.env
```

Delete the staging dump on 172.28.92.57 too once production runs (`/opt/aastar-price/backups/` there). Remove the
temporary staging whitelist entry if one was added.

## 7. Backend (BE server 172.28.80.51)

**PuTTY on 172.28.80.51.** Copy the templates, write the database passwords and two new keys into the secrets file:

```bash
cp /opt/aastar-price/src/deploy/production/production.env /opt/aastar-price/production.env
cp /opt/aastar-price/src/deploy/production/secrets.production.env.example /opt/aastar-price/secrets.production.env
chmod 600 /opt/aastar-price/secrets.production.env /opt/aastar-price/production.env
set -a; . /opt/aastar-price/db-passwords.env; set +a
sed -i "s/FILL_APP_DB_PASSWORD/$APP_PW/; s/FILL_OWNER_DB_PASSWORD/$OWNER_PW/" /opt/aastar-price/secrets.production.env
SK=$(python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(48)).decode())'); EK=$(python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')
sed -i "s#^APP_SECRET_KEY=.*#APP_SECRET_KEY=$SK#; s#^APP_ENCRYPTION_KEY=.*#APP_ENCRYPTION_KEY=$EK#" /opt/aastar-price/secrets.production.env
unset SK EK OWNER_PW APP_PW RO_PW; rm /opt/aastar-price/db-passwords.env
nano /opt/aastar-price/secrets.production.env
```

In `nano`: `FILL_PROD_RDS_HOST` (twice), `require` → `prefer` only if SSL is off (§3 step 2), the LSEG username and
password, the Claude API key (or `ANTHROPIC_API_KEY=` empty to keep AI, News and Calendar off). Keep a copy of the
whole file in the team vault: `APP_ENCRYPTION_KEY` protects the LSEG token and the Google key in the database.
Review `/opt/aastar-price/production.env` (it is complete for http://172.28.80.50:3170):

| Setting | Value |
|---|---|
| `APP_ENV` | `prod` (no API docs page, no synthetic prices) |
| `APP_BASE_URL`, `APP_ALLOWED_ORIGINS` | `http://172.28.80.50:3170` |
| `FORWARDED_ALLOW_IPS` | `172.28.80.50` (only the FE nginx reports visitor addresses) |
| `SSO_ENABLED`, `HUB_ISSUER`, `HUB_CLIENT_ID`, `HUB_REDIRECT_URI` | `true`, `https://dwshub.kpndomain.com`, `aastar-price`, `http://172.28.80.50:3170/api/auth/sso/callback` |
| `ALLOW_DEMO_DATA` | `false` |
| `CLAUDE_MODEL` | `claude-opus-5-5` (exact; a typo is reported as a rejected key) |
| `PIPELINE_CRON` / `SHEETS_CRON` | `0 8 * * mon-fri` / `30 8 * * mon-fri`, `PIPELINE_TZ=Asia/Jakarta` |

Deploy:

```bash
/opt/aastar-price/src/deploy/production/release.sh be v1.0.0
```

What it does, in order: refuses unfilled `FILL_` values, a secrets file that is not `chmod 600`, local edits, or an
unknown tag; checks out the tag; keeps the running image as `:prev`; builds `aastar-price-backend:production`;
`pg_dump` to `/opt/aastar-price/backups/aastar-…-pre-v1.0.0.dump`; runs `aastar-price-migrate` (`alembic upgrade
head`, then the reference-data bootstrap: `"admin_created": null` is expected, the staging administrators came with
the copy); recreates `aastar-price-api` and `aastar-price-worker`; checks them. It ends with:

```
Container aastar-price-api: healthy
Container aastar-price-worker: running
{"status":"ok","version":"…"}
{"status":"ready","db":"ok","revision":"0008_hardening"}
aastar-price-api is running v1.0.0. Instant rollback: …
```

## 8. Frontend (FE server 172.28.80.50)

**PuTTY on 172.28.80.50.** The Hub SDK values (§4) are build arguments:

```bash
cp /opt/aastar-price/src/deploy/production/fe-build.env.example /opt/aastar-price/fe-build.env
nano /opt/aastar-price/fe-build.env
```

Set `VITE_HUB_SDK_KEY` to the production `dwsk_…` key (and `VITE_HUB_SDK_URL` if the Hub shows another script URL).
`/opt/aastar-price/.env` already holds this server's subnet (§5). Deploy:

```bash
/opt/aastar-price/src/deploy/production/release.sh fe v1.0.0
```

It builds `aastar-price-frontend:production` with `VITE_GIT_SHA` = the tag's commit (Sentinel reads exactly that
code), recreates `aastar-price-web` and ends with `Container aastar-price-web: healthy`, `ok`, the API's `ready`
line through the proxy, and the `<script … hub-sdk.js … data-release="…">` tag. No host nginx change and no DNS:
users open **http://172.28.80.50:3170** (AR-04). The vhost file waits for §11.

## 9. First sign-in, administrators and the first data load

1. Open http://172.28.80.50:3170. Sign in with **Sign in with Downstream Hub** (users registered for Hub SSO are
   linked again by e-mail on their first production sign-in), or with your staging password, then change it at once
   (your name, top right → **Change password**). Check **Admin → Audit log**: `DATABASE_COPIED_FROM_STAGING`, then your sign-in.
2. **Admin → Users & access**: review the copied users and roles; disable anyone who should not use production
   (staging-only testers). Staging passwords were typed on a test system: switch everyone who has a Hub account to
   sign-in method **Hub SSO** (no password), keep **Password + SSO** only for the break-glass administrator, and reset
   the password of any remaining password user. New people: *New user*, **Hub SSO**, the same e-mail as in the Hub.
3. **Admin → Data pipeline** → *Run* **incremental**: it signs in to LSEG and loads the last days; the status must be
   *succeeded*. If the copy printed `NOTE - staging had synthetic prices`, run **full** instead (all history since
   2015; it takes a while) — until then the series built from staging's synthetic prices remain.
4. **Admin → Sheet sources**: paste the Google service-account key (*Save key*: the e-mail appears), then **Refresh
   all**; every source must show *ok* with today's time.
5. **CFTC** page → *Refresh*; **AI Analysis**: one short report (checks the Claude key and model).
6. **Admin → Settings**: review the copied values (session timeouts, *Create unknown Hub users automatically* off).

## 10. Go-live checks

| # | Check | Expected |
|---|---|---|
| 1 | `curl http://172.28.80.50:3170/api/health/ready` (any office PC) | `ready`, revision `0008_hardening` (the release's latest migration) |
| 2 | Sign in through the Hub tile and on the login page | overview, signed in; `SSO_LOGIN` (+ `SSO_LINK` once) in the audit log |
| 3 | Audit log of your sign-in | **your** PC's address, not 172.28.80.50 or a 172.16.x.1 gateway |
| 4 | Overview | today's data date, no demo banner; Admin → Data pipeline: last run *succeeded* |
| 5 | Waste/Bio, Basis & premiums, Parity pages | charts with data; Sheet sources all *ok* |
| 6 | From another server or PC: `curl -m 5 http://172.28.80.51:4170/api/health` | answers only from 172.28.80.50 if the firewall restricts it; never reachable from outside the VPC |
| 7 | `curl -sI http://172.28.80.50:3170/` | `Content-Security-Policy` with `https://dwshub.kpndomain.com`, `X-Frame-Options: DENY`, `nosniff` |
| 8 | A viewer with one module | other modules: "You don't have access to this module" |
| 9 | Hub SDK | Hub → SDK screen *Receiving data*; Analytic shows the user by name; a test bug report |
| 10 | Sentinel | *Analyse with Sentinel* on the test report cites files at the release |
| 11 | Next weekday 08:00 / 08:30 WIB | a `schedule` pipeline run, then the sheet refresh |
| 12 | The other applications | unchanged: `docker ps` on both servers shows them up as before; `findmnt -t cifs` on BE unchanged |

## 11. A name and HTTPS (later)

Day one is plain HTTP on 172.28.80.50:3170 (AR-04, accepted). When a name (e.g. `aastar-price.kpndomain.com`) and,
ideally, TLS are wanted:

1. **A release first**: the web container must trust the host nginx as a proxy (`set_real_ip_from` 172.28.80.50 and
   the `aastar-price_net` gateway in `frontend/nginx/default.conf.template`) — otherwise every user shares one
   address in the audit log and the sign-in lockout. Test it on staging.
2. DNS: the name → 172.28.80.50. Install `deploy/production/nginx/host-vhost-aastar-price.conf` as
   `/etc/nginx/conf.d/aastar-price.conf` on the FE server, then `nginx -t && systemctl reload nginx` (never reload
   without `nginx -t`: the nginx is shared). With TLS: the certificate in a `listen 443 ssl` server (check whether the
   host's existing `*.kpndomain.com` certificate covers the name), port 80 redirecting.
3. `production.env`: `APP_BASE_URL`, `APP_ALLOWED_ORIGINS` (keep the IP origin during the switch, comma-separated),
   `HUB_REDIRECT_URI` → the name; with HTTPS, cookies become `Secure` by themselves.
4. Hub: add the new redirect URI (first in the list), the Target URL and the SDK allowed origin.
5. `release.sh be` with the same tag (re-reads the settings), then everyone signs in again.

## 12. Releasing updates and rolling back

1. **Staging first**: the change is merged into `SIT`, deployed with `deploy/staging/deploy.sh` and checked.
2. **PR `SIT` → `main`**, reviewed and merged. On the workstation: `git checkout main && git pull`, then
   `git tag -a v1.0.1 -m "v1.0.1" && git push origin v1.0.1`.
3. **BE first, then FE**:

```bash
/opt/aastar-price/src/deploy/production/release.sh be v1.0.1      # PuTTY on 172.28.80.51
```

```bash
/opt/aastar-price/src/deploy/production/release.sh fe v1.0.1      # PuTTY on 172.28.80.50
```

Before a release, read what changed: new migrations (`backend/migrations/versions/`), new settings in
`deploy/production/production.env` (copy them into `/opt/aastar-price/production.env`), new build arguments, new
outbound hosts (preflight).

**Rollback.** The application: release the previous tag (`release.sh be v1.0.0 --skip-migrate`, then `fe`), or
instantly, without a build, the image kept by the last release:

```bash
docker tag aastar-price-backend:prev aastar-price-backend:production && /opt/aastar-price/src/deploy/production/dc.sh be up -d --force-recreate api worker
```

```bash
docker tag aastar-price-frontend:prev aastar-price-frontend:production && /opt/aastar-price/src/deploy/production/dc.sh fe up -d --force-recreate web
```

Migrations are usually additive, so older code runs on the newer schema. When a release's notes say a migration is
**not** (a dropped or renamed column), roll back the database too: restore the `pre-<tag>` dump (§13).

## 13. Backup and recovery

| What | Where | How |
|---|---|---|
| The whole database | ApsaraDB automatic backups (instance-wide) | RDS console; point-in-time restore goes to a **new** instance |
| `aastar` nightly | `/opt/aastar-price/backups/aastar-…-nightly.dump`, newest 7 | `backup.sh nightly` from cron |
| `aastar` before every migration | `…-pre-<tag>.dump`, newest 7 | `release.sh be` |
| The staging copy | `staging-copy-….dump` | kept from §6 |
| Settings and keys | the team vault | §3, §7 |
| Code | GitHub, the release tag | §12 |

Nightly backup, **PuTTY on 172.28.80.51**:

```bash
(crontab -l 2>/dev/null; echo '30 1 * * * /opt/aastar-price/src/deploy/production/backup.sh nightly >> /opt/aastar-price/backups/backup.log 2>&1') | crontab -
/opt/aastar-price/src/deploy/production/backup.sh manual
```

The second line must print `backup written: /opt/aastar-price/backups/aastar-…-manual.dump (…)`.

**Restore a dump** (overwrites `aastar`; stop the app first, keep the failed state as a dump):

```bash
/opt/aastar-price/src/deploy/production/dc.sh be stop api worker
/opt/aastar-price/src/deploy/production/backup.sh before-restore
docker run --rm --env-file /opt/aastar-price/secrets.production.env -v /opt/aastar-price/backups:/backups:ro postgres:18-alpine sh -c 'pg_restore --clean --if-exists --no-owner --dbname="$(printf %s "$MIGRATION_DATABASE_URL" | sed "s#+psycopg##")" /backups/FILL_DUMP_FILE'
/opt/aastar-price/src/deploy/production/dc.sh be up -d api worker
```

## 14. Running it day to day

- **Health**: `curl http://172.28.80.51:4170/api/health/ready` (BE) — `ready`. Admin → Data pipeline shows each run
  and its steps; Admin → Sheet sources each source's last refresh.
- **Containers and logs** (BE: `be`, FE: `fe`):

```bash
/opt/aastar-price/src/deploy/production/dc.sh be ps
/opt/aastar-price/src/deploy/production/dc.sh be logs --tail 200 worker
```

- **Restart** one service: `dc.sh be restart api` (settings changes need `dc.sh be up -d --force-recreate api worker`).
- **Usage and JavaScript errors**: DWS Hub → Analytic, with Sentinel's analyses.
- **Never on these shared hosts**: `systemctl restart docker`, editing `/etc/docker/daemon.json`, `docker … prune`,
  `docker compose down` on another app's files, removing another app's network / container / volume, touching
  another app's mount or nginx site, reloading nginx without `nginx -t`.

## 15. Troubleshooting

Everything in the staging guide's §14 and §8.5 applies, with production addresses. In addition:

| Symptom | Cause / fix |
|---|---|
| `docker: open /opt/aastar-price/rds-admin.env: no such file or directory` | The first block of §3 step 3 was skipped, or its last line (`rm`) already ran: create the file again |
| `git clone`: `destination path '/opt/aastar-price/src' already exists` and `not a git repository` | A `docker run -v /opt/aastar-price/src/…` ran before the clone, and Docker created the missing folders empty. `find /opt/aastar-price/src` must list folders only; then `find /opt/aastar-price/src -depth -type d -empty -delete` and clone again |
| `STOP - fill in the FILL_ values above in /opt/aastar-price/…` | Edit the file with `nano`; the line numbers are printed above |
| `set AASTAR_PRICE_SUBNET in /opt/aastar-price/.env` | §5, last step, on this server |
| `Pool overlaps with other one on this address space` | The subnet was taken since the preflight: run it again, use the new suggestion |
| `release tag … does not exist on GitHub` | §12 step 2 |
| `the code in /opt/aastar-price/src has local changes` | Someone edited the checkout: `git -C /opt/aastar-price/src status`, then `git -C /opt/aastar-price/src checkout -- .` |
| copy: `production is PostgreSQL 16/17` | The instance is not PG18: another instance is needed (§3) |
| copy: `cannot sign in to the STAGING database` | Staging owner password, or 172.28.80.51 is not on the staging whitelist: use `--dump-only` on staging (§6) |
| copy: `already has N tables` | The copy ran before; it is for the first install only |
| copy: `pg_restore failed` / a table `FAIL` / grants missing | Empty the database as `aastar_owner`, then run the copy again: `docker run --rm --env-file /opt/aastar-price/copy-db.env postgres:18-alpine sh -c 'psql "$TARGET_URL" -X -c "DROP SCHEMA IF EXISTS iam, market, ops, intel CASCADE" -c "DROP TABLE IF EXISTS public.alembic_version"'` |
| `/api/health/ready` says `migrations_pending` | The migrate step was skipped or failed: `release.sh be TAG` again, read its migrate output |
| Migrate: `permission denied` / `must be owner` | `MIGRATION_DATABASE_URL` is not `aastar_owner`, or the copy ran as another account |
| API container unhealthy, log `password authentication failed` | The password in `DATABASE_URL`, or `sslmode=require` while the instance has SSL off |
| Pipeline run *failed* at `lseg_auth`: "LSEG credentials are not configured" | `LSEG_USERNAME/PASSWORD` empty — production never falls back to synthetic data |
| Sheet sources: "key could not be read" after the copy | Expected: paste the Google key again (§9 step 4) |
| AI: "key rejected" | Check `CLAUDE_MODEL` spelling first, then the key; `dc.sh be up -d --force-recreate api worker` after the change |
| `sso_unavailable` | The API cannot reach `dwshub.kpndomain.com` via 172.28.80.50:443 (`extra_hosts`; preflight `Hub discovery` line) |
| `sso_token_failed` | `HUB_REDIRECT_URI` ≠ the registered URI, or `HUB_CLIENT_ID` |
| A staging password is refused | It was changed on staging after the copy, or the user is disabled; an admin resets it (Users & access) |
| Everyone shows as 172.28.80.50 in the audit log | Users are coming through a proxy the app does not trust (§11 step 1) |
| Too many connections on the shared instance | 18 in normal use (2 API workers × 6 + worker 6); lower `WEB_CONCURRENCY` or `DB_POOL_SIZE`, tell the instance owner |
| `docker: Error … pull access denied` / timeouts while building | Outbound to Docker Hub / PyPI / npm (§2) |
