# media-stack

A self-hosted Docker media stack (qBittorrent, Prowlarr, Radarr, Sonarr, Lidarr, Jellyfin) built around one shared `/data` mount so imports are hardlinks.

This project follows the Dev Stack pipeline in the global CLAUDE.md: shape → build → verify → review → learn.

- Conventions (branches, commits, PRs, code, tests, checks): `CONTRIBUTING.md`.
- Plans, backlog and design rationale: `planning/` (gitignored, local only). Start with `planning/tasks.md`.
- This repo is **public**. Never commit `.env`, `config/`, `data/`, API keys, LAN addresses, usernames or watchlists.
