# scripts/

Helper scripts for the media stack.

## `bootstrap.sh`

First-run setup. Idempotent — run it any time:

```bash
./scripts/bootstrap.sh
```

Seeds `.env` from `.env.example` if you don't have one yet, then creates the
`config/` + `data/` folder layout the compose file expects. It resolves
`CONFIG_ROOT` / `DATA_ROOT` through `docker compose config` (via `lib/env.sh`),
so it makes the folders exactly where compose will mount them, shell overrides
included.

## `backup_config.sh`

Timestamped, consistent backup of `config/` while the stack runs. See
[Backups and restore](../README.md#backups-and-restore) for options, the restore
steps and the optional launchd schedule.

## `import_letterboxd_to_radarr.py`

Bulk-import a [Letterboxd](https://letterboxd.com) watchlist into Radarr.

### 1. Export your watchlist
Letterboxd ▸ Settings ▸ **Import & Export** ▸ *Export your data*. Unzip it; the
file you want is `watchlist.csv` (columns: `Date,Name,Year,Letterboxd URI`).

Put it under `scripts/watchlists/` — that folder is gitignored, so your personal
lists never get committed:

```bash
mkdir -p scripts/watchlists
cp ~/Downloads/letterboxd-export/watchlist.csv scripts/watchlists/watchlist.csv
```

### 2. Set up Python
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r scripts/requirements.txt
```

### 3. Give it your Radarr API key
Radarr ▸ Settings ▸ General ▸ Security ▸ **API Key**. Then either export it or
add it to `.env` and source it:

```bash
export RADARR_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# or:  set -a; source .env; set +a
```

### 4. Dry-run first, then import
```bash
# See exactly what WOULD happen — adds nothing:
python scripts/import_letterboxd_to_radarr.py --csv scripts/watchlists/watchlist.csv --dry-run

# Do it for real:
python scripts/import_letterboxd_to_radarr.py --csv scripts/watchlists/watchlist.csv
```

### Options
| Flag | Default | Meaning |
|------|---------|---------|
| `--csv` | *(required)* | Path to the Letterboxd `watchlist.csv` |
| `--url` | `http://localhost:7878` | Radarr base URL (or env `RADARR_URL`) |
| `--root-folder` | `/data/movies` | Radarr root folder (or env `RADARR_ROOT_FOLDER`) |
| `--quality-profile-id` | `7` | Quality profile id (or env `RADARR_QUALITY_PROFILE_ID`) |
| `--search` | off | Start searching each movie right after adding |
| `--sleep` | `0.5` | Seconds between API calls (be nice to Radarr) |
| `--limit` | all | Only process the first N rows (handy for testing) |
| `--dry-run` | off | Report actions, change nothing |
| `-v/--verbose` | off | Debug logging |

### What "unmatched" means
Anything Radarr can't confidently match (no result, or no TMDB id) is written to
`<csv>.unmatched.csv` instead of being force-added. These are usually
miniseries/TV that belong in **Sonarr**, or titles whose year/spelling differs
from TMDB. Review that file by hand.

### Finding your quality profile id
The default `7` is this setup's *Movies 1080p* profile. Yours may differ:
```bash
curl -s -H "X-Api-Key: $RADARR_API_KEY" http://localhost:7878/api/v3/qualityprofile \
  | python3 -c "import sys,json; [print(p['id'], p['name']) for p in json.load(sys.stdin)]"
```

> Note: `--root-folder` uses the in-container path (`/data/movies`), because
> that's how Radarr sees the filesystem — not the host path.
