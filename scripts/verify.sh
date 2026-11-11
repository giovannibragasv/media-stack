#!/usr/bin/env bash
#
# verify.sh — prove the running media-stack is healthy.
#
# Checks, one PASS/FAIL line each:
#   1. every compose service is running and answers HTTP on its published port
#   2. /data/downloads and /data/movies are one filesystem inside Radarr
#      (a real hardlink probe as the app user, removed afterwards)
#   3. no stale qBittorrent lockfile while qBittorrent is down
#
# Read-only apart from the two probe files. Exits non-zero if any check fails.
# Ports resolve as docker compose resolves them (shell env, then .env), e.g.
#   RADARR_PORT=1 ./scripts/verify.sh   # expect a FAIL for radarr
#
# Usage:  ./scripts/verify.sh   (run from anywhere — it finds the repo root)

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=scripts/lib/env.sh
source "$SCRIPT_DIR/lib/env.sh"

command -v jq >/dev/null || { echo "FAIL  jq is required (brew install jq / apt install jq)"; exit 1; }

failures=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; failures=$((failures + 1)); }

config="$(compose config --format json)"
running="$(compose ps --status running --services)"
is_running() { grep -qx "$1" <<<"$running"; }

# 1. Services up and answering ------------------------------------------------
while read -r service port; do
  if ! is_running "$service"; then
    fail "$service is not running"
    continue
  fi
  if [ -z "$port" ]; then
    pass "$service is running (no published port)"
    continue
  fi
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://localhost:$port/" || true)"
  case "$code" in
    2??|3??|401) pass "$service answers HTTP $code on port $port" ;;
    *) fail "$service gave HTTP ${code:-000} on port $port" ;;
  esac
done < <(jq -r '.services | to_entries[] | "\(.key) \(.value.ports[0].published // "")"' <<<"$config")

# 2. Hardlink probe inside Radarr ---------------------------------------------
probe=".verify-hardlink-probe-$$"
radarr="$(compose ps -q radarr)"
cleanup() {
  if [ -n "$radarr" ]; then
    docker exec "$radarr" rm -f "/data/downloads/$probe" "/data/movies/$probe" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if ! is_running radarr; then
  fail "hardlink probe skipped: radarr is not running"
elif inodes="$(docker exec -u abc "$radarr" sh -c \
    'touch "/data/downloads/$1" && ln "/data/downloads/$1" "/data/movies/$1" && stat -c %i "/data/downloads/$1" "/data/movies/$1"' \
    sh "$probe" 2>&1)" \
  && [ "$(sort -u <<<"$inodes" | wc -l)" -eq 1 ]; then
  pass "hardlink /data/downloads -> /data/movies shares one inode (same filesystem)"
else
  fail "hardlink /data/downloads -> /data/movies failed: $(tr '\n' ' ' <<<"$inodes")"
fi

# 3. Stale qBittorrent lockfile -----------------------------------------------
# The live /config mount wins over the compose config, so a run from another checkout still sees the real file.
qbit="$(compose ps -a -q qbittorrent 2>/dev/null || true)"
qbit_config=""
if [ -n "$qbit" ]; then
  qbit_config="$(docker inspect "$qbit" --format '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"
fi
qbit_config="${qbit_config:-$(jq -r '.services.qbittorrent.volumes[] | select(.target == "/config") | .source' <<<"$config")}"
lockfile="$qbit_config/qBittorrent/lockfile"
if is_running qbittorrent; then
  pass "qbittorrent is running, so its lockfile is live"
elif [ -e "$lockfile" ]; then
  fail "stale qBittorrent lockfile at $lockfile: delete it before starting qbittorrent"
else
  pass "no stale qBittorrent lockfile"
fi

if [ "$failures" -gt 0 ]; then
  echo "$failures check(s) failed"
  exit 1
fi
echo "all checks passed"
