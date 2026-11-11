# shellcheck shell=bash
# Sourced by the scripts so they read .env exactly as docker compose does.
# The caller sets REPO_ROOT.

# Before bootstrap seeds .env, compose reads .env.example so it sees the documented defaults.
compose() {
  local env_file="$REPO_ROOT/.env"
  [ -f "$env_file" ] || env_file="$REPO_ROOT/.env.example"
  docker compose -f "$REPO_ROOT/docker-compose.yml" -p "${COMPOSE_PROJECT_NAME:-media-stack}" \
    --env-file "$env_file" "$@"
}

# env_value NAME DEFAULT prints NAME as compose interpolates ${NAME:-DEFAULT}:
# shell env first, then the env file, with compose's own quoting and comment rules.
env_value() {
  local env line
  env="$(compose config --environment)" || return
  line="$(grep -m1 "^$1=" <<<"$env")" || true
  line="${line#*=}"
  echo "${line:-$2}"
}
