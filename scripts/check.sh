#!/usr/bin/env bash
# The checks every PR must pass. pre-commit and CI both run this file.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

uvx ruff check
uvx ruff format --check

if [ -d tests ]; then
  python3 -m unittest discover -s tests -t .
fi

if command -v shellcheck >/dev/null; then
  git ls-files -z '*.sh' '.githooks/*' | xargs -0 shellcheck
else
  git ls-files -z '*.sh' '.githooks/*' |
    xargs -0 docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable
fi

git ls-files 'docker-compose*.yml' | while IFS= read -r f; do
  docker compose --env-file .env.example -f "$f" config -q
done
