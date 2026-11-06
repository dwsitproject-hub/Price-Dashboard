#!/usr/bin/env bash
# Build and (re)start one AASTAR staging tier from the SIT branch, then smoke-test it.
#
#   sudo bash deploy/staging/deploy.sh backend     # on Server 2, 172.28.92.57 — deploy this first
#   sudo bash deploy/staging/deploy.sh frontend    # on Server 1, 172.28.92.56
#
# Environment options:
#   BRANCH=SIT          branch to deploy (default SIT)
#   SKIP_GIT=1          build the checkout as it is (no fetch / fast-forward); GIT_SHA=<tag> to name the image
#   ROLLBACK_TO=<sha>   no git, no build: restart the tier on an image built earlier (see deployments.log)
#   KEEP_IMAGES=5       image tags kept per tier; older ones are removed
set -euo pipefail

ROLE=${1:-}
case "$ROLE" in
  backend)  IMAGE=aastar-backend;  CONTAINER=aastar-api-staging ;;
  frontend) IMAGE=aastar-frontend; CONTAINER=aastar-web-staging ;;
  *) echo "usage: $0 backend|frontend" >&2; exit 2 ;;
esac

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
BRANCH=${BRANCH:-SIT}
ENV_FILE="$HERE/.env.$ROLE"
COMPOSE="$HERE/docker-compose.$ROLE.yml"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
val()  { { grep -E "^$1=" "$ENV_FILE" || true; } | tail -1 | cut -d= -f2-; }
dc()   { docker compose -f "$COMPOSE" --env-file "$ENV_FILE" "$@"; }

[ -f "$ENV_FILE" ] || die "missing $ENV_FILE — copy $HERE/$ROLE.env.example to it and fill it in"
grep -nE '^[^#]*<[^>]*>' "$ENV_FILE" && die "$ENV_FILE still has <placeholders> (lines above)"
if [ "$ROLE" = backend ] && [ "$(stat -c %a "$ENV_FILE" 2>/dev/null || echo 600)" != 600 ]; then
  echo "WARNING: $ENV_FILE holds secrets — run: chmod 600 $ENV_FILE"
fi

# ── Source revision ──────────────────────────────────────────────────────────
if [ -n "${ROLLBACK_TO:-}" ]; then
  GIT_SHA=$ROLLBACK_TO
  docker image inspect "$IMAGE:$GIT_SHA" >/dev/null 2>&1 || die "no local image $IMAGE:$GIT_SHA to roll back to"
  say "Rolling back $ROLE to $GIT_SHA (no build)"
else
  if [ "${SKIP_GIT:-0}" != 1 ]; then
    say "Updating $REPO to origin/$BRANCH"
    git -C "$REPO" diff --quiet && git -C "$REPO" diff --cached --quiet \
      || die "the checkout has local changes — commit them upstream or stash them first"
    git -C "$REPO" fetch --quiet origin "$BRANCH"
    git -C "$REPO" checkout --quiet "$BRANCH"
    git -C "$REPO" merge --ff-only --quiet "origin/$BRANCH"
    GIT_SHA=$(git -C "$REPO" rev-parse --short HEAD)
  else
    GIT_SHA=${GIT_SHA:-$(git -C "$REPO" rev-parse --short HEAD)}   # SKIP_GIT may name its own tag
  fi
fi
export GIT_SHA
echo "release: $GIT_SHA ($(git -C "$REPO" log -1 --format=%s "$GIT_SHA" 2>/dev/null || echo '?'))"

# ── Port check: the host port must be free, or already ours ──────────────────
if [ "$ROLE" = backend ]; then BIND=$(val API_BIND_IP); PORT=$(val API_PORT); else BIND=$(val WEB_BIND_IP); PORT=$(val WEB_PORT); fi
BIND=${BIND:-0.0.0.0}; [ "$BIND" = 0.0.0.0 ] && CHECK_HOST=127.0.0.1 || CHECK_HOST=$BIND
OWNER=$(docker ps --filter "publish=$PORT" --format '{{.Names}}' | head -1)
if [ -n "$OWNER" ] && [ "$OWNER" != "$CONTAINER" ]; then
  die "port $PORT is already published by container '$OWNER' — pick another port in $ENV_FILE"
fi
if [ -z "$OWNER" ] && command -v ss >/dev/null && ss -Hltn "( sport = :$PORT )" | grep -q .; then
  die "port $PORT is in use by a host process: $(ss -Hltnp "( sport = :$PORT )" | head -1)"
fi

# ── Build and start ──────────────────────────────────────────────────────────
if [ -z "${ROLLBACK_TO:-}" ]; then
  say "Building $IMAGE:$GIT_SHA"
  dc build --pull
fi
say "Starting $ROLE"
fail() { dc ps -a; dc logs --tail 60; die "$*"; }
# `up` exits non-zero when migrate fails; carry on so the checks below can print the reason.
# (not `up --wait`: it reports failure whenever the one-shot migrate job exits, even with status 0)
dc up -d --remove-orphans || true
if [ "$ROLE" = backend ]; then
  if [ "$(docker inspect -f '{{.State.ExitCode}}' aastar-migrate-staging 2>/dev/null)" != 0 ]; then
    printf '\n---- aastar-migrate-staging log ----\n'; docker logs --tail 80 aastar-migrate-staging 2>&1
    die "migrate failed (alembic upgrade or bootstrap) — the reason is in the log above; see guide §14"
  fi
fi
for _ in $(seq 1 60); do
  STATE=$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo missing)
  [ "$STATE" = healthy ] && break
  [ "$STATE" = unhealthy ] && fail "$CONTAINER is unhealthy"
  sleep 5
done
[ "$STATE" = healthy ] || fail "$CONTAINER not healthy after 5 minutes (state: $STATE)"
dc ps

# ── Smoke test ───────────────────────────────────────────────────────────────
say "Smoke test"
if [ "$ROLE" = backend ]; then
  curl -fsS "http://$CHECK_HOST:$PORT/api/health/ready" \
    || die "API not ready: $(curl -s "http://$CHECK_HOST:$PORT/api/health/ready") — check DATABASE_URL, RDS whitelist, migrations"
  echo
else
  curl -fsS "http://$CHECK_HOST:$PORT/healthz" || die "Nginx not answering on port $PORT"
  curl -fsS "http://$CHECK_HOST:$PORT/api/health/ready" \
    || die "the frontend cannot reach the API at $(val API_UPSTREAM) — deploy the backend first, check the security group"
  echo "  (frontend → backend proxy OK)"
  curl -fsSI "http://$CHECK_HOST:$PORT/" | grep -i '^content-security-policy' || die "CSP header missing"
fi

# ── Record and tidy ──────────────────────────────────────────────────────────
printf '%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$ROLE" "$GIT_SHA" "${SUDO_USER:-$(id -un)}" >> "$HERE/deployments.log"
{ docker images "$IMAGE" --format '{{.Tag}}' | grep -vx "$GIT_SHA" || true; } | tail -n +"${KEEP_IMAGES:-5}" \
  | while read -r tag; do docker rmi "$IMAGE:$tag" >/dev/null 2>&1 || true; done
say "Done: $ROLE $GIT_SHA is live (log: $HERE/deployments.log)"
