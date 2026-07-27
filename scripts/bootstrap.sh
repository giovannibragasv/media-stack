#!/usr/bin/env bash
#
# bootstrap.sh — first-run setup for media-stack.
#
# Idempotent: safe to run as many times as you like. It:
#   1. creates the config/ and data/ folder layout the compose file expects
#   2. seeds .env from .env.example (only if .env doesn't exist yet)
#   3. prints what to do next
#
# Usage:  ./scripts/bootstrap.sh   (run from anywhere — it finds the repo root)

set -euo pipefail

# Resolve the repo root (parent of this script's dir) so it works from anywhere.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo "▶ media-stack bootstrap"
echo "  repo: $REPO_ROOT"

# 1. Storage layout ----------------------------------------------------------
#    Honour CONFIG_ROOT / DATA_ROOT from .env if already set there; else default.
CONFIG_ROOT="$(grep -E '^CONFIG_ROOT=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
DATA_ROOT="$(grep -E '^DATA_ROOT=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
CONFIG_ROOT="${CONFIG_ROOT:-./config}"
DATA_ROOT="${DATA_ROOT:-./data}"

echo "▶ creating folders (config: $CONFIG_ROOT, data: $DATA_ROOT)"
mkdir -p \
  "$CONFIG_ROOT" \
  "$DATA_ROOT/downloads/incomplete" \
  "$DATA_ROOT/movies" \
  "$DATA_ROOT/tv" \
  "$DATA_ROOT/music"

# 2. Environment file --------------------------------------------------------
if [ -f .env ]; then
  echo "▶ .env already exists — leaving it untouched"
else
  cp .env.example .env
  echo "▶ created .env from .env.example"
  echo "  ⚠  EDIT .env before starting: set PUID/PGID (id -u / id -g), TZ, RADARR_API_KEY"
fi

# 3. Next steps --------------------------------------------------------------
cat <<'EOF'

✔ Bootstrap complete.

Next:
  1. Edit .env         (PUID/PGID, TZ, ports, optional CONFIG_ROOT/DATA_ROOT)
  2. docker compose up -d
  3. docker compose ps
  4. First qBittorrent password:  docker compose logs qbittorrent | grep -i password

EOF
