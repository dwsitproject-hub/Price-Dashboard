#!/usr/bin/env bash
# Docker Compose for this app's production containers. The compose files stay in the checkout; everything
# filled in on the server is read from /opt/aastar-price (.env, production.env, secrets.production.env).
#
#   /opt/aastar-price/src/deploy/production/dc.sh be ps            # on 172.28.80.51
#   /opt/aastar-price/src/deploy/production/dc.sh be logs -f api
#   /opt/aastar-price/src/deploy/production/dc.sh fe logs -f       # on 172.28.80.50
set -euo pipefail
ROLE="${1:-}"
case "$ROLE" in be|fe) shift ;; *) echo "usage: dc.sh be|fe <docker compose command...>" >&2; exit 2 ;; esac
RUN="${AASTAR_PRICE_RUN_DIR:-/opt/aastar-price}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# The release shown in the app: the tag the checkout is on (release.sh checks out a tag).
AASTAR_PRICE_RELEASE="${AASTAR_PRICE_RELEASE:-$(git -C "$HERE" describe --tags --always 2>/dev/null || echo production)}"
export AASTAR_PRICE_RELEASE
exec docker compose --project-directory "$RUN" -f "$HERE/$ROLE.compose.yml" "$@"
