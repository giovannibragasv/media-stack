# Contributing

These are the conventions for every change to media-stack, whether a person or an agent makes it. `/code-review` checks changes against this file.

## Workflow

1. Work starts from a GitHub issue.
2. Each issue is built on its own branch, in its own git worktree, off `main`.
3. Changes land through a pull request the owner reviews with `/code-review` and `/thermo-nuclear-code-quality-review`.
4. Land it with `land-pr <number>` from the main checkout. It rebase-merges, so every commit survives on `main`, and cleans up the branch and worktree only after GitHub confirms the merge.

## Branches

`<type>/<short-slug>`, using the commit types below, for example `feat/config-backup`.

## Commits

[Conventional Commits](https://www.conventionalcommits.org): `type(scope): subject`, imperative, no trailing period.

- **Types:** `feat`, `fix`, `refactor`, `test`, `docs`, `build`, `ci`, `chore`, `perf`.
- **Scopes:** `compose` (the stack definition), `scripts`, `importer` (the Letterboxd importer), or none for repo-wide changes.
- **One logical change per commit.** Tests go in the commit of the feature they test.

## Pull requests

- **Title:** a Conventional Commit subject.
- **Body:** the template: why, what changed, scope, tradeoffs, blast radius, verification.
- **Closing issues:** `Closes #N`.

## Code

- **Public repo.** Nothing personal or secret is committed: no `.env`, `config/`, `data/`, API keys, LAN addresses, usernames or watchlist exports. `.gitignore` covers the folders; you cover the content.
- **One `/data` mount.** Every media service mounts `${DATA_ROOT}` at `/data`. Never split downloads and libraries into separate mounts, or hardlinks silently become copies.
- **Storage is configured in `.env` only.** Paths go through `CONFIG_ROOT` and `DATA_ROOT`; scripts read them the same way `scripts/bootstrap.sh` does.
- **Scripts that change live data** (Radarr, qBittorrent, files under `data/`) default to safe behaviour and offer `--dry-run`.
- **Scripts are idempotent.** Running one twice converges to the same state.
- **Comments only explain a non-obvious _why_.** A deliberate shortcut gets `# shortcut: <the limit>, <when to upgrade>`.
- **Python:** 3.11+, standard library plus `requests` (`scripts/requirements.txt`). No new dependency for a few lines.
- **Shell:** `bash` with `set -euo pipefail`, clean under `shellcheck`.

## Tests

- Python logic gets `unittest` tests in `tests/`, testing behaviour through the script's functions, with HTTP calls faked at the client boundary.
- Anything that touches the running stack is proven with `scripts/verify.sh` (once it exists) or a recorded run in the PR's Verification section.

## Checks

These must pass before a PR, and CI runs the same ones:

```sh
uvx ruff check
uvx ruff format --check
python -m unittest discover -s tests -t .      # once tests/ exists
git ls-files '*.sh' '.githooks/*' | xargs shellcheck
docker compose --env-file .env.example config -q
```

No local `shellcheck`? `docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable <files>`.

### Git hooks

Turn them on once per clone:

```sh
git config core.hooksPath .githooks
```

- `pre-commit` runs ruff when Python is staged, and shellcheck when shell scripts are staged and `shellcheck` is installed.
- `commit-msg` rejects anything that isn't a Conventional Commit.

### CI

`.github/workflows/ci.yml` runs the checks above and the Conventional Commits check on every PR and push to `main`, on Linux runners only, cancelling superseded runs. Docs-only changes skip the code checks. The jobs are inlined rather than calling the shared private `ci-workflows` repo, because a public repo can't call workflows from a private one.
