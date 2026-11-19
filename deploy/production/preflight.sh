#!/usr/bin/env bash
# AASTAR Price Dashboard production preflight - READ-ONLY, changes nothing.
#
#   RDS_HOST=FILL_PROD_RDS_HOST bash /opt/aastar-price/src/deploy/production/preflight.sh be   # on 172.28.80.51
#   bash /opt/aastar-price/src/deploy/production/preflight.sh fe                              # on 172.28.80.50
#
# Reports what docs/08-PRODUCTION-DEPLOYMENT.md needs before anything is installed on a host other
# applications share: is the app's port free (ss also sees host services that docker ps cannot), a /24 for
# AASTAR_PRICE_SUBNET that collides with no Docker network, route, this LAN or the NAS, the NAS mounts other
# apps use (listed, never touched - this app has no NAS mount), and the outbound paths the app needs.
# Every line starts with OK / WARN / FAIL / INFO; paste the whole output back for review.
#
# Settings (environment, all optional): RDS_HOST (the production ApsaraDB host - be), PORTS, TCP_CHECKS,
# URL_CHECKS, APP_HOST, HUB_HOST_IP (172.28.80.50), STAGING_RDS_HOST (for the one-time copy, guide §6).
set -u
ROLE="${1:-}"
case "$ROLE" in be|fe) ;; *) echo "usage: preflight.sh be|fe"; exit 2 ;; esac
APP=aastar-price
NAS=no
NAS_IP="${NAS_IP:-172.30.1.94}"
APP_HOST="${APP_HOST:-aastar-price.kpndomain.com}"
HUB_HOST_IP="${HUB_HOST_IP:-172.28.80.50}"
RDS_HOST="${RDS_HOST:-}"
STAGING_RDS_HOST="${STAGING_RDS_HOST:-pgm-d9jx9o06qae8gf3h.pgsql.ap-southeast-5.rds.aliyuncs.com}"
BUILD_CHECKS="github.com:22 registry-1.docker.io:443"
if [ "$ROLE" = be ]; then
  PORTS="${PORTS:-4170}"
  TCP_CHECKS="${TCP_CHECKS:-$BUILD_CHECKS pypi.org:443 files.pythonhosted.org:443 selectapi.datascope.lseg.com:443 api.anthropic.com:443 www.cftc.gov:443 docs.google.com:443 sheets.googleapis.com:443 oauth2.googleapis.com:443}"
  URL_CHECKS="${URL_CHECKS:-https://selectapi.datascope.lseg.com/RestApi/v1/ https://api.anthropic.com/v1/models https://www.cftc.gov/ https://oauth2.googleapis.com/token}"
else
  PORTS="${PORTS:-3170}"
  TCP_CHECKS="${TCP_CHECKS:-$BUILD_CHECKS registry.npmjs.org:443}"
  URL_CHECKS="${URL_CHECKS:-https://dwshub.kpndomain.com/api/sdk/v1/hub-sdk.js https://registry.npmjs.org/}"
fi

say() { printf '%-5s %s\n' "$1" "$2"; }
have() { command -v "$1" >/dev/null 2>&1; }
tcp() { timeout 6 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }

echo "== $APP production preflight ($ROLE) on $(hostname) ($(hostname -I 2>/dev/null | awk '{print $1}')) $(date '+%F %T')"
for t in docker ss ip python3 curl findmnt git; do have "$t" && say OK "tool $t" || say FAIL "tool $t missing"; done
docker compose version >/dev/null 2>&1 && say OK "docker compose v2 $(docker compose version --short 2>/dev/null)" || say FAIL "docker compose v2 missing"
say INFO "disk free: / $(df -h / | awk 'NR==2{print $4}'), /var/lib/docker $(df -h /var/lib/docker 2>/dev/null | awk 'NR==2{print $4}'), memory free $(free -h 2>/dev/null | awk '/^Mem:/{print $7}')"

# ── ports: ss sees host services too; docker ps -a sees stopped containers' reservations ──
LISTEN=$(ss -ltnH 2>/dev/null | awk '{print $4}' | sed -E 's/.*:([0-9]+)$/\1/' | sort -n | uniq)
RESERVED=$(docker ps -a --format '{{.Ports}}' | grep -oE ':[0-9]+->' | tr -d ':->' | sort -n | uniq)
OURS=$(docker ps -a --filter "name=^aastar-price-" --format '{{.Ports}}' | grep -oE ':[0-9]+->' | tr -d ':->' | sort -n | uniq)
say INFO "listening: $(echo $LISTEN)"
say INFO "published by containers (incl. stopped): $(echo $RESERVED)"
for P in $(echo "$PORTS" | tr ',' ' '); do
  if echo "$OURS" | tr ' ' '\n' | grep -qx "$P"; then
    say OK "port $P is published by this app's own container (a re-run)"
  elif echo "$LISTEN $RESERVED" | tr ' ' '\n' | grep -qx "$P"; then
    for q in $(seq "$P" $((P + 60))); do
      echo "$LISTEN $RESERVED" | tr ' ' '\n' | grep -qx "$q" || { say FAIL "port $P is taken - first free at or above it: $q"; break; }
    done
  else
    say OK "port $P is free"
  fi
done

# ── docker networks, and a subnet for the app that collides with nothing ──
SUBNETS=$(docker network ls -q 2>/dev/null | xargs -r docker network inspect --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null)
say INFO "docker networks:"; echo "$SUBNETS" | sed 's/^/        /'
if docker network inspect "${APP}_net" >/dev/null 2>&1; then
  say INFO "${APP}_net exists: $(docker network inspect "${APP}_net" --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}') - keep that value for AASTAR_PRICE_SUBNET"
fi
SUGGEST=$(NAS_IP="$NAS_IP" SUBNETS="$SUBNETS" ROUTES="$(ip -4 route show | awk '{print $1}' | grep -E '^[0-9]')" ADDRS="$(ip -4 -o addr show | awk '{print $4}')" python3 - <<'PY'
import ipaddress, os
taken = []
for tok in (os.environ["SUBNETS"] + " " + os.environ["ROUTES"] + " " + os.environ["ADDRS"]).split():
    try:
        taken.append(ipaddress.ip_network(tok, strict=False))
    except ValueError:
        pass
fence = [ipaddress.ip_network(x) for x in ("172.28.0.0/16", "172.29.0.0/16", "172.30.0.0/16", "172.31.0.0/16")]
fence.append(ipaddress.ip_network(os.environ["NAS_IP"] + "/16", strict=False))
def candidates():
    # Docker's usual pool first (172.17-172.27), then 172.16.x, then 192.168.32.x and up. On the production
    # hosts other apps already hold every 172.17-172.31 /16 and 192.168.0-31 (Nov 2026); AASTAR Logistic
    # took 172.16.249.0/24 and PCT 172.16.250.0/24. Any of them must also be unused by the company's
    # office / VPN networks, which only infra can confirm.
    for second in range(27, 16, -1):
        for third in range(250, 0, -1):
            yield f"172.{second}.{third}.0/24"
    for third in range(250, 0, -1):
        yield f"172.16.{third}.0/24"
    for third in range(250, 31, -1):
        yield f"192.168.{third}.0/24"
for c in candidates():
    cand = ipaddress.ip_network(c)
    if not any(cand.overlaps(t) for t in taken + fence):
        print(cand)
        raise SystemExit
print("NONE")
PY
)
[ "$SUGGEST" = NONE ] && say FAIL "no free /24 in 172.16-172.27 or 192.168.32-250 - choose by hand" || say OK "suggested subnet for ${APP}_net: $SUGGEST"
case "$SUGGEST" in 172.1[7-9].*|172.2[0-7].*|NONE) ;; *) say WARN "$SUGGEST is outside Docker's usual pool - confirm with infra that the office / VPN networks do not use it";; esac
echo "$SUBNETS" | grep -Eq ' 172\.3[01]\.' && say WARN "a Docker network of ANOTHER app holds a 172.30/31 range - the NAS may be shadowed on this host (report to its owner; this app adds nothing there)"

# ── NAS: this app has none; list what exists, never touch it ──
say INFO "NAS mounts already on this host (other apps - leave them alone; this app adds none):"
findmnt -rn -t cifs,smb3,nfs,nfs4 -o TARGET,SOURCE 2>/dev/null | sed 's/^/        /'
R=$(ip route get "$NAS_IP" 2>/dev/null | head -1)
case "$R" in
  *" dev br-"*|*" dev docker"*) say WARN "route to the NAS goes into a Docker bridge (another app's network): $R" ;;
  *) say INFO "route to the NAS: ${R:-none}" ;;
esac

# ── outbound ──
for hp in $TCP_CHECKS; do
  h="${hp%:*}"; p="${hp##*:}"
  tcp "$h" "$p" && say OK "$hp reachable" || say FAIL "$hp NOT reachable (firewall / whitelist / DNS)"
done
for u in $URL_CHECKS; do
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$u"); [ "$c" != 000 ] && say OK "$u -> HTTP $c" || say FAIL "$u unreachable"
done
if [ "$ROLE" = be ]; then
  # The production database: another application's ApsaraDB instance (guide §3).
  if [ -z "$RDS_HOST" ] || [ "${RDS_HOST#FILL_}" != "$RDS_HOST" ]; then
    say WARN "RDS_HOST not given - re-run as: RDS_HOST=pgm-....rds.aliyuncs.com bash preflight.sh be"
  else
    say INFO "$RDS_HOST resolves to $(getent hosts "$RDS_HOST" | awk '{print $1}' | head -1)"
    tcp "$RDS_HOST" 5432 && say OK "$RDS_HOST:5432 reachable" || say FAIL "$RDS_HOST:5432 NOT reachable (RDS whitelist: add 172.28.80.51, guide §3)"
  fi
  # Only for the one-time copy (guide §6); otherwise the dump is made on the staging server.
  tcp "$STAGING_RDS_HOST" 5432 && say INFO "staging RDS reachable from here - the copy can run on this server" \
    || say INFO "staging RDS not reachable from here - copy with --dump-only on the staging BE (guide §6)"
  # The production Hub, the way the API container reaches it (name pinned to the FE server).
  D=$(curl -s --max-time 8 --resolve "dwshub.kpndomain.com:443:$HUB_HOST_IP" https://dwshub.kpndomain.com/api/sso/.well-known/openid-configuration)
  case "$D" in
    *'"issuer"'*) say OK "Hub discovery via $HUB_HOST_IP: $(echo "$D" | grep -oE '"issuer" *: *"[^"]*"')" ;;
    *) say FAIL "Hub discovery via $HUB_HOST_IP failed (outbound 443 to the FE server, or the Hub is down)" ;;
  esac
  J=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 --resolve "dwshub.kpndomain.com:443:$HUB_HOST_IP" https://dwshub.kpndomain.com/api/sso/jwks)
  [ "$J" = 200 ] && say OK "Hub signing keys (jwks) -> 200" || say FAIL "Hub signing keys (jwks) -> $J"
fi

# ── front proxy (meaningful on the web server) ──
if [ "$ROLE" = fe ]; then
  API="${AASTAR_PRICE_API_UPSTREAM:-172.28.80.51:4170}"
  tcp "${API%:*}" "${API##*:}" && say OK "backend $API reachable" || say INFO "backend $API not reachable yet (expected before guide §7; afterwards: firewall FE -> BE :4170)"
  OWNER=$(ss -ltnpH '( sport = :80 )' 2>/dev/null | head -1)
  case "$OWNER" in
    *nginx*) say INFO "port 80: host nginx (shared front door) - only for a later name, guide §11" ;;
    *docker-proxy*) say INFO "port 80: a container" ;;
    "") say INFO "nothing on port 80 on this server" ;;
  esac
  if have nginx; then
    nginx -T 2>/dev/null | grep -q "server_name.*$APP_HOST" && say WARN "a server block for $APP_HOST already exists" || say INFO "no server block for $APP_HOST (expected: users open http://172.28.80.50:3170)"
  fi
fi
for c in aastar-price-api aastar-price-worker aastar-price-web; do
  docker inspect "$c" >/dev/null 2>&1 && say INFO "container $c exists: $(docker inspect -f '{{.State.Status}} {{.Config.Image}}' "$c")"
done
[ -d "/opt/$APP/src/.git" ] && say INFO "checkout: $(git -C /opt/$APP/src describe --tags --always 2>/dev/null) $(git -C /opt/$APP/src log --oneline -1 2>/dev/null | cut -c1-60); $(git -C /opt/$APP/src status -sb 2>/dev/null | head -1)"
echo "== done"
