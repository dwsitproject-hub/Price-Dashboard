#!/usr/bin/env bash
# Production release on ONE server (docs/08-PRODUCTION-DEPLOYMENT.md §7, §8, §12).
# Production runs a release TAG on main - a commit that passed staging - never "whatever main is".
#
#   /opt/aastar-price/src/deploy/production/release.sh be v1.0.0     # BE 172.28.80.51 first
#   /opt/aastar-price/src/deploy/production/release.sh fe v1.0.0     # then FE 172.28.80.50
#   ... be v1.0.0 --skip-backup    no pg_dump before the migrations (ApsaraDB's own backup still applies)
#   ... be v1.0.0 --skip-migrate   leave the schema alone (a release with no new migration)
#
# Guarded: refuses unfilled settings, local edits in the checkout and unknown tags; keeps the running image
# as :prev (instant rollback, guide §12); builds on this server; (BE) takes a pg_dump, applies the migrations
# and the reference-data bootstrap; recreates the containers (--force-recreate: Compose compares image NAMES,
# so a plain `up -d` after a rebuild keeps the old code) and checks them.
set -euo pipefail
ROLE="${1:-}"; TAG="${2:-}"
case "$ROLE" in be|fe) ;; *) echo "usage: release.sh be|fe <release tag> [--skip-backup] [--skip-migrate]" >&2; exit 2 ;; esac
[ -n "$TAG" ] && [ "${TAG#--}" = "$TAG" ] || { echo "usage: release.sh $ROLE <release tag, e.g. v1.0.0> [options]" >&2; exit 2; }
shift 2
MIGRATE=1; BACKUP=1
for arg in "$@"; do
  case "$arg" in
    --skip-migrate) MIGRATE=0 ;;
    --skip-backup) BACKUP=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

RUN="${AASTAR_PRICE_RUN_DIR:-/opt/aastar-price}"
SRC="$RUN/src"
HERE="$SRC/deploy/production"
stop() { echo "STOP - $*" >&2; exit 1; }

# ── the filled-in runtime files must exist and be complete ──
need_filled() {
  [ -f "$RUN/$1" ] || stop "$RUN/$1 is missing (guide: copy it from $HERE)"
  if grep -nE '^[^#]*FILL_' "$RUN/$1"; then stop "fill in the FILL_ values above in $RUN/$1"; fi
}
need_filled .env
if [ "$ROLE" = be ]; then
  need_filled production.env
  need_filled secrets.production.env
  [ "$(stat -c %a "$RUN/secrets.production.env")" = 600 ] || stop "chmod 600 $RUN/secrets.production.env"
  [ ! -f "$RUN/copy-db.env" ] || echo "WARNING: $RUN/copy-db.env still exists - delete it once the staging copy is done (guide §6)"
else
  need_filled fe-build.env
fi

# ── code: the release tag, in a checkout without local edits ──
[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ] || { git -C "$SRC" status -sb; stop "the code in $SRC has local changes"; }
git -C "$SRC" fetch --tags --force origin
git -C "$SRC" rev-parse -q --verify "refs/tags/$TAG" >/dev/null || stop "release tag $TAG does not exist on GitHub (guide §12 step 2)"
git -C "$SRC" -c advice.detachedHead=false checkout -q "$TAG"
SHA=$(git -C "$SRC" rev-parse --short HEAD)
echo "Releasing $TAG ($SHA: $(git -C "$SRC" log -1 --format=%s | cut -c1-70))"
export AASTAR_PRICE_RELEASE="$TAG"
DC="$HERE/dc.sh $ROLE"

if [ "$ROLE" = be ]; then
  IMAGE=aastar-price-backend; CHECK=aastar-price-api
  docker image inspect "$IMAGE:production" >/dev/null 2>&1 && docker tag "$IMAGE:production" "$IMAGE:prev"
  docker build -t "$IMAGE:production" "$SRC/backend"
  if [ "$MIGRATE" = 1 ]; then
    if [ "$BACKUP" = 1 ]; then
      echo "Backing up the database before the migrations..."
      bash "$HERE/backup.sh" "pre-$TAG"
    fi
    echo "Applying the schema migrations and the reference data..."
    $DC run --rm migrate
  fi
  $DC up -d --force-recreate api worker
else
  IMAGE=aastar-price-frontend; CHECK=aastar-price-web
  BUILD_ARGS=()
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    BUILD_ARGS+=(--build-arg "$line")
  done < "$RUN/fe-build.env"
  docker image inspect "$IMAGE:production" >/dev/null 2>&1 && docker tag "$IMAGE:production" "$IMAGE:prev"
  # The release reported to DWS Hub / Sentinel is the tag's commit: Sentinel reads exactly that code.
  docker build "${BUILD_ARGS[@]}" --build-arg "VITE_GIT_SHA=$SHA" -t "$IMAGE:production" "$SRC/frontend"
  $DC up -d --force-recreate web
fi

# ── check ──
status=starting
for _ in $(seq 1 45); do
  status=$(docker inspect -f '{{.State.Health.Status}}' "$CHECK" 2>/dev/null || echo missing)
  [ "$status" = healthy ] && break
  sleep 2
done
echo "Container $CHECK: $status"
if [ "$ROLE" = be ]; then
  echo "Container aastar-price-worker: $(docker inspect -f '{{.State.Status}}' aastar-price-worker 2>/dev/null || echo missing)"
  BIND=$(grep -sE '^AASTAR_PRICE_API_BIND=' "$RUN/.env" | cut -d= -f2); BIND="${BIND:-172.28.80.51:4170}"
  curl -fsS "http://$BIND/api/health" && echo
  curl -sS "http://$BIND/api/health/ready" && echo
else
  BIND=$(grep -sE '^AASTAR_PRICE_WEB_BIND=' "$RUN/.env" | cut -d= -f2); BIND="${BIND:-172.28.80.50:3170}"
  curl -fsS "http://$BIND/healthz"
  curl -sS "http://$BIND/api/health/ready" && echo
  curl -s "http://$BIND/" | grep -o '<script[^>]*hub-sdk.js[^>]*>' || echo "(no Hub SDK tag in the page - VITE_HUB_SDK_KEY empty?)"
fi
[ "$status" = healthy ] || { docker logs --tail 40 "$CHECK" >&2; stop "$CHECK is not healthy (rollback: guide §12)"; }
echo "$CHECK is running $TAG. Instant rollback: docker tag $IMAGE:prev $IMAGE:production && $DC up -d --force-recreate"
