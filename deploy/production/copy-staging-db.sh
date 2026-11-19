#!/usr/bin/env bash
# ONE-TIME: copy the staging database into the EMPTY production database `aastar` (guide §6).
#
#   bash /opt/aastar-price/src/deploy/production/copy-staging-db.sh                 # BE 172.28.80.51: dump + restore
#   bash .../copy-staging-db.sh --dump-only                                        # staging BE 172.28.92.57: dump only
#   bash .../copy-staging-db.sh --restore /opt/aastar-price/backups/FILE.dump      # BE 172.28.80.51: restore that file
#
# The default needs the production BE server on the STAGING RDS whitelist for the copy. Where that is not
# allowed, dump on the staging BE server (--dump-only), scp the file over, and --restore it here.
#
# Reads $RUN/copy-db.env (chmod 600, filled with nano, deleted right after the copy):
#   SOURCE_URL  staging,    aastar_owner @ staging RDS / aastar      (not needed for --restore)
#   TARGET_URL  production, aastar_owner @ production RDS / aastar   (not needed for --dump-only)
#
# What it does, in order, stopping at the first failure:
#   1. signs in to both databases; production must be PostgreSQL 18 and must have no tables yet;
#   2. pg_dump of staging (custom format, WITH grants) into $RUN/backups, kept;
#   3. pg_restore as aastar_owner, so it owns every object, and the grants to aastar_app / aastar_ro are the
#      ones the migrations gave on staging; then checks those grants;
#   4. compares the row counts of the main tables (staging / production);
#   5. removes what must not cross environments, in one transaction:
#        sessions and pending Hub sign-ins (staging cookies); every user's link to the STAGING Hub (hub_sub:
#        the production Hub has its own subjects, each user is linked again by e-mail on first sign-in);
#        the LSEG token and the Google key (encrypted with staging's key, unreadable here - enter the Google
#        key again, guide §9); queued jobs; synthetic (demo) prices, if staging ever had any; staging's audit
#        log (it stays in the dump file) - replaced by one entry recording this copy.
#      Password users keep their staging passwords: the guide (§9) has them reviewed and changed.
# The schema version comes with the copy; release.sh be then migrates it to the release (guide §7).
set -euo pipefail
RUN="${AASTAR_PRICE_RUN_DIR:-/opt/aastar-price}"
ENVF="${COPY_DB_ENV:-$RUN/copy-db.env}"
# PostgreSQL 18 client: production runs 18, and a client reads any server up to its own version.
CLIENT="${PG_CLIENT_IMAGE:-postgres:18-alpine}"
stop() { echo "STOP - $*" >&2; exit 1; }

MODE=copy; FILE=
case "${1:-}" in
  "") ;;
  --dump-only) MODE=dump ;;
  --restore) MODE=restore; FILE="${2:-}"; [ -n "$FILE" ] || stop "usage: copy-staging-db.sh --restore /opt/aastar-price/backups/FILE.dump" ;;
  *) stop "usage: copy-staging-db.sh [--dump-only | --restore FILE.dump]" ;;
esac

[ -f "$ENVF" ] || stop "$ENVF is missing (guide §6: copy copy-db.env.example, fill it with nano)"
[ "$(stat -c %a "$ENVF")" = 600 ] || stop "chmod 600 $ENVF"
if grep -nE '^(SOURCE|TARGET)_URL=.*FILL_' "$ENVF" | grep -q .; then
  case "$MODE" in
    copy) stop "fill in the FILL_ values in $ENVF" ;;
    dump) grep -qE '^SOURCE_URL=.*FILL_' "$ENVF" && stop "fill in SOURCE_URL in $ENVF" ;;
    restore) grep -qE '^TARGET_URL=.*FILL_' "$ENVF" && stop "fill in TARGET_URL in $ENVF" ;;
  esac
fi
mkdir -p "$RUN/backups" && chmod 700 "$RUN/backups"
if [ "$MODE" = restore ]; then
  [ -f "$FILE" ] || stop "$FILE does not exist"
  case "$(cd "$(dirname "$FILE")" && pwd)" in "$RUN/backups") ;; *) stop "move the dump into $RUN/backups first";; esac
  NAME=$(basename "$FILE")
else
  NAME="staging-copy-$(date +%Y%m%d-%H%M%S).dump"
fi

docker run --rm -i --env-file "$ENVF" -e MODE="$MODE" -e NAME="$NAME" -e RUN="$RUN" -v "$RUN/backups:/backups" "$CLIENT" sh -eu <<'INNER'
TABLES="iam.roles iam.modules iam.users market.products market.instruments market.series market.price_daily market.series_points market.oi_cumulative ops.pipeline_runs intel.sheet_sources intel.sheet_snapshots intel.snd_sources intel.cftc_positions intel.atlas_plants intel.documents intel.ai_reports intel.calendar_events"
q() { psql "$1" -X -q -v ON_ERROR_STOP=1 -Atc "$2"; }

# ── 1. sign in, versions, empty target ──
if [ "$MODE" != restore ]; then
  SV=$(q "$SOURCE_URL" 'show server_version') || { echo "STOP - cannot sign in to the STAGING database (SOURCE_URL): user, password, host, whitelist"; exit 1; }
  echo "staging server:    PostgreSQL $SV ($(q "$SOURCE_URL" 'select version_num from public.alembic_version'))"
fi
if [ "$MODE" != dump ]; then
  PV=$(q "$TARGET_URL" 'show server_version') || { echo "STOP - cannot sign in to the PRODUCTION database (TARGET_URL): user, password, host, whitelist (guide §3)"; exit 1; }
  echo "production server: PostgreSQL $PV as $(q "$TARGET_URL" 'select current_user') on database $(q "$TARGET_URL" 'select current_database()')"
  case "$PV" in 18*|19*|2*) ;; *) echo "STOP - production is PostgreSQL $PV; the schema needs 18 (uuidv7, virtual generated columns)"; exit 1;; esac
  [ "$(q "$TARGET_URL" 'select current_user')" = aastar_owner ] || { echo "STOP - TARGET_URL must sign in as aastar_owner, which then owns every object"; exit 1; }
  n=$(q "$TARGET_URL" "select count(*) from pg_tables where schemaname not in ('pg_catalog','information_schema')")
  [ "$n" = 0 ] || { echo "STOP - the production database already has $n tables. This copy is for the first install only (guide §6)."; exit 1; }
fi

# ── 2. dump ──
if [ "$MODE" != restore ]; then
  echo "Dumping staging..."
  pg_dump --format=custom --file="/backups/$NAME" "$SOURCE_URL"
  echo "dump: $RUN/backups/$NAME ($(du -h "/backups/$NAME" | cut -f1))"
  if [ "$MODE" = dump ]; then
    echo "Dump only. Move $RUN/backups/$NAME to /opt/aastar-price/backups/ on the production BE 172.28.80.51"
    echo "(WinSCP via the workstation, or scp), then there: copy-staging-db.sh --restore /opt/aastar-price/backups/$NAME"
    exit 0
  fi
fi

# ── 3. restore, as aastar_owner, keeping the grants ──
echo "Restoring into production..."
if ! pg_restore --no-owner --exit-on-error --dbname="$TARGET_URL" "/backups/$NAME"; then
  echo "STOP - pg_restore failed (above). The production database is half-filled: empty it as in guide §15 ('pg_restore failed'), then run this again."
  exit 1
fi
g=$(q "$TARGET_URL" "select has_table_privilege('aastar_app','market.series_points','SELECT') and has_table_privilege('aastar_app','ops.audit_logs','INSERT') and not has_table_privilege('aastar_app','ops.audit_logs','UPDATE') and has_schema_privilege('aastar_app','market','USAGE')")
[ "$g" = t ] && echo "grants: aastar_app OK" || { echo "STOP - aastar_app is missing its grants after the restore (guide §15)"; exit 1; }
echo "schema version copied: $(q "$TARGET_URL" 'select version_num from public.alembic_version') (release.sh be migrates it to the release)"

# ── 4. row counts ──
bad=0
if [ "$MODE" = copy ]; then
  echo "Row counts (staging / production):"
  for t in $TABLES; do
    s=$(q "$SOURCE_URL" "select count(*) from $t" 2>/dev/null || echo "-")
    p=$(q "$TARGET_URL" "select count(*) from $t" 2>/dev/null || echo "-")
    # Staging may keep writing (its worker) while this runs: staging slightly AHEAD is expected.
    if [ "$p" = "-" ] || { [ "$s" != "-" ] && [ "$p" -gt "$s" ]; }; then mark=FAIL; bad=1
    elif [ "$s" = "$p" ]; then mark=OK
    else mark="WARN staging wrote $((s - p)) more after the dump"; fi
    printf '  %-26s %12s %12s  %s\n' "$t" "$s" "$p" "$mark"
  done
else
  echo "Row counts (production; compare with staging if in doubt):"
  for t in $TABLES; do printf '  %-26s %12s\n' "$t" "$(q "$TARGET_URL" "select count(*) from $t" 2>/dev/null || echo MISSING)"; done
  for t in $TABLES; do q "$TARGET_URL" "select 1 from $t limit 1" >/dev/null 2>&1 || bad=1; done
fi
[ "$bad" = 0 ] || { echo "STOP - a table is missing or larger in production: the restore did not complete (guide §15)"; exit 1; }

# ── 5. what must not cross environments ──
demo=$(q "$TARGET_URL" "select count(*) from market.price_daily where source = 'demo'")
echo "Removing staging-only data..."
psql "$TARGET_URL" -X -q -At -v ON_ERROR_STOP=1 -v dump="$NAME" <<'SQL'
\set QUIET on
BEGIN;
CREATE TEMP TABLE copy_report (item text, n bigint);
WITH d AS (DELETE FROM iam.sessions RETURNING 1) INSERT INTO copy_report SELECT 'staging sessions removed', count(*) FROM d;
WITH d AS (DELETE FROM iam.oidc_login_states RETURNING 1) INSERT INTO copy_report SELECT 'pending Hub sign-ins removed', count(*) FROM d;
WITH u AS (UPDATE iam.users SET hub_sub = NULL WHERE hub_sub IS NOT NULL RETURNING 1) INSERT INTO copy_report SELECT 'users unlinked from the staging Hub', count(*) FROM u;
WITH d AS (DELETE FROM ops.integration_tokens RETURNING 1) INSERT INTO copy_report SELECT 'LSEG tokens removed', count(*) FROM d;
WITH d AS (DELETE FROM ops.integration_secrets RETURNING 1) INSERT INTO copy_report SELECT 'stored keys removed (Google: enter again)', count(*) FROM d;
WITH d AS (DELETE FROM ops.jobs RETURNING 1) INSERT INTO copy_report SELECT 'staging jobs removed', count(*) FROM d;
WITH d AS (DELETE FROM ops.app_settings WHERE key = 'data.mode' RETURNING 1) INSERT INTO copy_report SELECT 'data.mode setting removed', count(*) FROM d;
WITH d AS (DELETE FROM market.price_daily WHERE source = 'demo' RETURNING 1) INSERT INTO copy_report SELECT 'synthetic (demo) prices removed', count(*) FROM d;
INSERT INTO copy_report SELECT 'staging audit entries cleared (kept in the dump)', count(*) FROM ops.audit_logs;
TRUNCATE ops.audit_logs RESTART IDENTITY;
INSERT INTO ops.audit_logs (action, target_type, target_id, details)
  VALUES ('DATABASE_COPIED_FROM_STAGING', 'database', 'aastar',
          jsonb_build_object('dump', :'dump', 'report', (SELECT jsonb_object_agg(item, n) FROM copy_report)));
COMMIT;
SELECT format('  %-50s %s', item, n) FROM copy_report;
SQL
users=$(q "$TARGET_URL" "select string_agg(r.code || ' ' || n, ', ') from (select role_id, count(*) n from iam.users where status = 'active' group by role_id) x join iam.roles r on r.id = x.role_id")
echo "active users by role: ${users:-none}"
echo "users who can sign in with a (staging) password: $(q "$TARGET_URL" "select count(*) from iam.users where status = 'active' and password_hash is not null") - review them (guide §9 step 2)"
[ "$demo" = 0 ] || echo "NOTE - staging had synthetic prices: the series built from them stay until the full LSEG load (guide §9 step 3)"
echo "Copy complete."
INNER
case "$MODE" in
  dump) echo "Done. Delete $ENVF on this server (rm $ENVF)." ;;
  *) echo "Done. Now: rm $ENVF, then the backend release (guide §7)." ;;
esac
