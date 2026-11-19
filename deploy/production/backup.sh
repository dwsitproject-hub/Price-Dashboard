#!/usr/bin/env bash
# Production backend (172.28.80.51): pg_dump of the production database `aastar` (custom format), on top of
# ApsaraDB's own automatic backups (guide §13). Signs in as aastar_owner with MIGRATION_DATABASE_URL from
# /opt/aastar-price/secrets.production.env, inside a throwaway PostgreSQL 18 client container.
#
#   /opt/aastar-price/src/deploy/production/backup.sh [label]   -> /opt/aastar-price/backups/aastar-<time>-<label>.dump
#   release.sh be takes one labelled pre-<tag> before every migration.
#   cron (guide §13):  30 1 * * * /opt/aastar-price/src/deploy/production/backup.sh nightly >> /opt/aastar-price/backups/backup.log 2>&1
# Keeps the newest KEEP (default 7) dumps of each label.
set -euo pipefail
RUN="${AASTAR_PRICE_RUN_DIR:-/opt/aastar-price}"
BACKUP_DIR="$RUN/backups"
KEEP="${KEEP:-7}"
LABEL="${1:-manual}"
case "$LABEL" in *[!A-Za-z0-9._-]*) echo "label: letters, digits, . _ - only" >&2; exit 2 ;; esac
CLIENT="${PG_CLIENT_IMAGE:-postgres:18-alpine}"
NAME="aastar-$(date +%Y%m%d-%H%M%S)-$LABEL.dump"
mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"

docker run --rm --env-file "$RUN/secrets.production.env" -e NAME="$NAME" -v "$BACKUP_DIR:/backups" "$CLIENT" sh -eu -c '
  URL=$(printf %s "$MIGRATION_DATABASE_URL" | sed "s#^postgresql+psycopg://#postgresql://#")
  pg_dump --format=custom --file="/backups/$NAME" "$URL"'
chmod 600 "$BACKUP_DIR/$NAME"
echo "$(date -Is) backup written: $BACKUP_DIR/$NAME ($(du -h "$BACKUP_DIR/$NAME" | cut -f1))"

ls -1t "$BACKUP_DIR"/aastar-*-"$LABEL".dump 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
