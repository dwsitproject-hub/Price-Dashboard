#!/usr/bin/env bash
# READ-ONLY: what the host nginx on the FE server answers for every name it serves (guide §11). Run it before
# and after adding the aastar-pi vhost and compare: every other app's line must be unchanged.
#
#   bash /opt/aastar-price/src/deploy/production/nginx/vhost-check.sh > /root/vhosts-before.txt
#   ... install the vhost, nginx -t, reload ...
#   bash /opt/aastar-price/src/deploy/production/nginx/vhost-check.sh > /root/vhosts-after.txt
#   diff /root/vhosts-before.txt /root/vhosts-after.txt     # only the aastar-pi line may differ
#
# Each line: name, HTTP status on port 80, HTTPS status on port 443 (000 = no answer), both asked at
# HOST_IP (172.28.80.50) the way a browser would after DNS. It changes nothing on the host.
set -u
HOST_IP="${HOST_IP:-172.28.80.50}"
names=$(nginx -T 2>/dev/null | sed -n 's/^[[:space:]]*server_name[[:space:]]\+\(.*\);.*/\1/p' | tr ' ' '\n' \
        | grep -vE '^(_|localhost|)$' | grep -v '^~' | grep -v '\*' | sort -u)
[ -n "$names" ] || { echo "no server_name found in nginx -T (run as root on the FE server)" >&2; exit 1; }
for n in $names aastar-pi.kpndomain.com; do
  h=$(curl -s -o /dev/null -m 6 -w '%{http_code}' --resolve "$n:80:$HOST_IP" "http://$n/")
  s=$(curl -sk -o /dev/null -m 6 -w '%{http_code}' --resolve "$n:443:$HOST_IP" "https://$n/")
  printf '%-45s http %s  https %s\n' "$n" "$h" "$s"
done | sort -u
