#!/usr/bin/env bash
#
# backup_config.sh: snapshot ${CONFIG_ROOT} into a timestamped .tar.gz.
#
# SQLite databases are copied with sqlite3's online backup, so the stack can
# keep running. Regenerable bulk (caches, logs, artwork, transcodes, the apps'
# own backup zips) is skipped. Only the newest N archives are kept.
#
# Usage:  ./scripts/backup_config.sh [--dest DIR] [--keep N]
#   --dest DIR  where archives go (env BACKUP_DIR, default <repo>/backups)
#   --keep N    archives to keep  (env BACKUP_KEEP, default 14)

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

DEST="${BACKUP_DIR:-$REPO_ROOT/backups}"
KEEP="${BACKUP_KEEP:-14}"
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --keep) KEEP="$2"; shift 2 ;;
    -h|--help) sed -n '3,11p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$KEEP" in ''|*[!0-9]*|0) echo "--keep must be a positive integer" >&2; exit 2 ;; esac

# shellcheck source=scripts/lib/env.sh
source "$SCRIPT_DIR/lib/env.sh"
CONFIG_ROOT="$(env_value CONFIG_ROOT ./config)"
case "$CONFIG_ROOT" in /*) ;; *) CONFIG_ROOT="$REPO_ROOT/$CONFIG_ROOT" ;; esac
[ -d "$CONFIG_ROOT" ] || { echo "config folder not found: $CONFIG_ROOT" >&2; exit 1; }

# Paths relative to CONFIG_ROOT, matched with find -path (a * also spans /).
EXCLUDES=(
  # logs, crash reports, artwork re-fetched on demand, the arrs' own weekly zips
  {radarr,sonarr,lidarr,prowlarr}/{logs,'logs.db*',Sentry,MediaCover,Backups}
  'qbittorrent/qBittorrent/logs' 'jellyfin/log'
  '*/.cache' 'jellyfin/cache'                # caches
  'jellyfin/data/metadata'                   # artwork, re-fetched on demand
  'jellyfin/data/transcodes'                 # Jellyfin transcode scratch
  'jackett'                                  # legacy, not in the compose file
  '*.pid'                                    # stale on restore
  '*.db-wal' '*.db-shm' '*.db-journal'       # folded into the .backup copy
)

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$DEST"
DEST="$(cd -- "$DEST" && pwd)" # absolute, since we cd into CONFIG_ROOT below

prune=()
for p in "${EXCLUDES[@]}"; do prune+=(-path "./$p" -o); done
config_files() { find . \( "${prune[@]}" -false \) -prune -o -type f "$@" -print0; }

cd "$CONFIG_ROOT"
config_files ! -name '*.db' | tar -cf - --null -T - | tar -xpf - -C "$STAGE"

dbs=0
while IFS= read -r -d '' db; do
  mkdir -p "$STAGE/$(dirname "$db")"
  # -readonly: never checkpoint or delete a live WAL, since host and container
  # may not share file locks. Read-only can't create a missing -wal/-shm; with
  # no -wal the main file holds every commit, so read it as immutable instead.
  src="$db"
  [ -e "$db-wal" ] || src="file:$db?immutable=1"
  sqlite3 -readonly -cmd '.timeout 30000' "$src" ".backup '$STAGE/$db'"
  # immutable=1 reads the copy without leaving -wal/-shm files in the archive.
  result="$(sqlite3 "file:$STAGE/$db?immutable=1" 'PRAGMA integrity_check;')"
  [ "$result" = ok ] || { echo "integrity_check failed for $db: $result" >&2; exit 1; }
  dbs=$((dbs + 1))
done < <(config_files -name '*.db')

name="config-$(date +%Y%m%d-%H%M%S).tar.gz"
# Archive the entries, not ".", so extracting never resets CONFIG_ROOT to mktemp's 0700.
(cd "$STAGE" && shopt -s dotglob && umask 077 && tar -czf "$DEST/.$name.partial" -- *)
mv -f "$DEST/.$name.partial" "$DEST/$name"
echo "wrote $DEST/$name ($(du -h "$DEST/$name" | cut -f1), $dbs databases checked ok)"

shopt -s nullglob
archives=("$DEST"/config-*.tar.gz) # glob order is oldest first, by timestamped name
for ((i = 0; i < ${#archives[@]} - KEEP; i++)); do
  rm -f -- "${archives[$i]}"
  echo "pruned ${archives[$i]}"
done
