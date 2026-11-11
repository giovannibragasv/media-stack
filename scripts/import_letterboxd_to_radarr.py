#!/usr/bin/env python3
"""
Import a Letterboxd watchlist export into Radarr.

Reads a Letterboxd CSV (columns: Date,Name,Year,Letterboxd URI), looks each
title up against the Radarr API, and adds the matched movie to Radarr with a
chosen quality profile and root folder. Movies already present are skipped.

Every movie it adds is tagged with its provenance: `src-letterboxd` and
`wl-YYYY-MM` (the month of the run). `--backfill-tags` adds `src-letterboxd` to
movies that are already in Radarr and appear in the CSV.

Anything Radarr can't confidently match is written to an "unmatched" CSV so you
can review it by hand (these are often miniseries/TV that belong in Sonarr, or
titles whose year/spelling differs from TMDB).

Usage:
    export RADARR_API_KEY=...            # or put it in ../.env and `source` it
    python import_letterboxd_to_radarr.py --csv data/watchlist.csv --dry-run
    python import_letterboxd_to_radarr.py --csv data/watchlist.csv

See scripts/README.md for details.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import date

import requests

log = logging.getLogger("letterboxd2radarr")

SOURCE_TAG = "src-letterboxd"


def provenance_labels(today: date) -> list[str]:
    # Radarr tag labels only allow [a-z0-9-], so `src:letterboxd` becomes `src-letterboxd`.
    return [SOURCE_TAG, f"wl-{today:%Y-%m}"]


@dataclass
class Config:
    radarr_url: str
    api_key: str
    root_folder: str
    quality_profile_id: int
    search_on_add: bool
    sleep: float
    dry_run: bool
    limit: int | None
    backfill_tags: bool = False


class Radarr:
    """Thin Radarr v3 API client."""

    def __init__(self, base_url: str, api_key: str, sleep: float = 0.5):
        self.base = base_url.rstrip("/") + "/api/v3"
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Key": api_key})
        self.sleep = sleep

    def _throttle(self):
        if self.sleep:
            time.sleep(self.sleep)

    def get(self, path: str, **params):
        r = self.session.get(f"{self.base}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, payload: dict):
        r = self.session.post(f"{self.base}{path}", json=payload, timeout=30)
        if r.status_code == 400:
            # Radarr returns 400 for "already exists" and validation errors.
            log.debug("400 from Radarr: %s", r.text[:300])
            return None
        r.raise_for_status()
        return r.json()

    def put(self, path: str, payload: dict):
        r = self.session.put(f"{self.base}{path}", json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def ping(self) -> str:
        """Return Radarr version; raises if unreachable/unauthorized."""
        return self.get("/system/status").get("version", "unknown")

    def movies_by_tmdb_id(self) -> dict[int, dict]:
        """All movies currently in Radarr keyed by tmdbId — fetched ONCE (not per row)."""
        return {m["tmdbId"]: m for m in self.get("/movie") if m.get("tmdbId")}

    def tag_ids(self) -> dict[str, int]:
        return {t["label"]: t["id"] for t in self.get("/tag")}

    def ensure_tags(self, labels: list[str]) -> dict[str, int]:
        """Label -> id for each label, creating only the ones Radarr doesn't have yet."""
        ids = self.tag_ids()
        for label in labels:
            if label not in ids:
                created = self.post("/tag", {"label": label})
                if not created:
                    raise RuntimeError(f"Radarr rejected tag label {label!r}")
                ids[label] = created["id"]
        return {label: ids[label] for label in labels}

    def add_tags(self, movie_ids: list[int], tag_ids: list[int]) -> None:
        """Union tag_ids into each movie's tags in one bulk call; never removes a tag."""
        self.put(
            "/movie/editor",
            {"movieIds": movie_ids, "tags": tag_ids, "applyTags": "add"},
        )

    def lookup(self, term: str) -> list[dict]:
        self._throttle()
        return self.get("/movie/lookup", term=term)

    def add_movie(self, movie: dict, cfg: Config, tag_ids: list[int]) -> dict | None:
        payload = {
            "title": movie["title"],
            "year": movie["year"],
            "tmdbId": movie["tmdbId"],
            "titleSlug": movie["titleSlug"],
            "images": movie.get("images", []),
            "monitored": True,
            "rootFolderPath": cfg.root_folder,
            "qualityProfileId": cfg.quality_profile_id,
            "minimumAvailability": "released",
            "tags": tag_ids,
            "addOptions": {"searchForMovie": cfg.search_on_add},
        }
        self._throttle()
        return self.post("/movie", payload)


def pick_best_match(results: list[dict], want_year: int | None) -> dict | None:
    """Prefer an exact-year match; otherwise the first result with a tmdbId."""
    candidates = [m for m in results if m.get("tmdbId")]
    if not candidates:
        return None
    if want_year is not None:
        for m in candidates:
            if m.get("year") == want_year:
                return m
        # Allow ±1 year (release-year vs. festival-year drift).
        for m in candidates:
            if m.get("year") and abs(m["year"] - want_year) <= 1:
                return m
    return candidates[0]


def parse_year(raw: str) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def read_watchlist(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run(cfg: Config, csv_path: str, unmatched_path: str) -> int:
    radarr = Radarr(cfg.radarr_url, cfg.api_key, sleep=cfg.sleep)

    try:
        version = radarr.ping()
    except requests.RequestException as e:
        log.error(
            "Cannot reach Radarr at %s (%s). Is it up? Is RADARR_API_KEY correct?",
            cfg.radarr_url,
            e,
        )
        return 2
    log.info("Connected to Radarr %s at %s", version, cfg.radarr_url)

    rows = read_watchlist(csv_path)
    if cfg.limit:
        rows = rows[: cfg.limit]
    log.info("Loaded %d rows from %s", len(rows), csv_path)

    in_radarr = radarr.movies_by_tmdb_id()
    existing = set(in_radarr)
    log.info("Radarr already has %d movies", len(existing))

    labels = provenance_labels(date.today())
    if cfg.dry_run:
        tags = {label: i for label, i in radarr.tag_ids().items() if label in labels}
        missing = [label for label in labels if label not in tags]
        if missing:
            log.info("Would create tags: %s", ", ".join(missing))
    else:
        tags = radarr.ensure_tags(labels)

    added = skipped = present = unmatched = 0
    unmatched_rows: list[dict] = []
    matched_in_radarr: dict[int, dict] = {}

    for i, row in enumerate(rows, 1):
        title = (row.get("Name") or "").strip().strip('"')
        year = parse_year(row.get("Year", ""))
        if not title:
            continue
        term = f"{title} {year}" if year else title
        prefix = f"[{i}/{len(rows)}] {term}"

        try:
            results = radarr.lookup(term)
        except requests.RequestException as e:
            log.warning("%s -> lookup failed: %s", prefix, e)
            unmatched += 1
            unmatched_rows.append({**row, "reason": "lookup_error"})
            continue

        movie = pick_best_match(results, year)
        if not movie:
            log.info("%s -> no confident match (maybe TV/miniseries?)", prefix)
            unmatched += 1
            unmatched_rows.append({**row, "reason": "no_match"})
            continue

        if movie["tmdbId"] in existing:
            log.info("%s -> already in Radarr", prefix)
            present += 1
            if movie["tmdbId"] in in_radarr:
                found = in_radarr[movie["tmdbId"]]
                matched_in_radarr[found["id"]] = found
            continue

        if cfg.dry_run:
            log.info(
                "%s -> WOULD ADD: %s (%s) tmdb=%s",
                prefix,
                movie["title"],
                movie.get("year"),
                movie["tmdbId"],
            )
            added += 1
            existing.add(movie["tmdbId"])  # avoid dup-counting within the run
            continue

        result = radarr.add_movie(movie, cfg, list(tags.values()))
        if result:
            log.info("%s -> ADDED: %s (%s)", prefix, result.get("title"), result.get("year"))
            added += 1
            existing.add(movie["tmdbId"])
        else:
            log.info("%s -> skipped (already exists / rejected)", prefix)
            skipped += 1

    if unmatched_rows:
        fieldnames = list(unmatched_rows[0].keys())
        with open(unmatched_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(unmatched_rows)
        log.info(
            "Wrote %d unmatched rows to %s (review by hand)", len(unmatched_rows), unmatched_path
        )

    if cfg.backfill_tags:
        backfill(radarr, matched_in_radarr, tags.get(SOURCE_TAG), cfg.dry_run)

    verb = "would add" if cfg.dry_run else "added"
    log.info(
        "Done. %s=%d, already_present=%d, skipped=%d, unmatched=%d",
        verb,
        added,
        present,
        skipped,
        unmatched,
    )
    return 0


def backfill(radarr: Radarr, matched: dict[int, dict], tag_id: int | None, dry_run: bool) -> int:
    """Add the source tag to matched movies that lack it. Returns how many it tags."""
    untagged = [mid for mid, m in matched.items() if tag_id not in m.get("tags", [])]
    verb = "would tag" if dry_run else "tagged"
    log.info(
        "Backfill: %d movies in Radarr match the CSV, %d already have %s, %s %d",
        len(matched),
        len(matched) - len(untagged),
        SOURCE_TAG,
        verb,
        len(untagged),
    )
    if untagged and not dry_run:
        radarr.add_tags(untagged, [tag_id])
    return len(untagged)


def main() -> int:
    p = argparse.ArgumentParser(description="Import a Letterboxd watchlist into Radarr.")
    p.add_argument("--csv", required=True, help="Path to Letterboxd watchlist CSV")
    p.add_argument(
        "--url",
        default=os.getenv("RADARR_URL", "http://localhost:7878"),
        help="Radarr base URL (default: env RADARR_URL or http://localhost:7878)",
    )
    p.add_argument(
        "--root-folder",
        default=os.getenv("RADARR_ROOT_FOLDER", "/data/movies"),
        help="Radarr root folder path (default: /data/movies)",
    )
    p.add_argument(
        "--quality-profile-id",
        type=int,
        default=int(os.getenv("RADARR_QUALITY_PROFILE_ID", "7")),
        help="Radarr quality profile id (default: 7 = 'Movies 1080p')",
    )
    p.add_argument(
        "--search",
        action="store_true",
        help="Tell Radarr to search for each movie right after adding",
    )
    p.add_argument("--sleep", type=float, default=0.5, help="Seconds between API calls")
    p.add_argument("--limit", type=int, default=None, help="Only process the first N rows")
    p.add_argument("--dry-run", action="store_true", help="Report actions without adding anything")
    p.add_argument(
        "--backfill-tags",
        action="store_true",
        help=f"Add the {SOURCE_TAG} tag to movies already in Radarr that appear in the CSV",
    )
    p.add_argument(
        "--unmatched-csv",
        default=None,
        help="Where to write unmatched rows (default: <csv>.unmatched.csv)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    api_key = os.getenv("RADARR_API_KEY")
    if not api_key:
        log.error("RADARR_API_KEY is not set. Export it or add it to ../.env and `source` it.")
        return 2

    cfg = Config(
        radarr_url=args.url,
        api_key=api_key,
        root_folder=args.root_folder,
        quality_profile_id=args.quality_profile_id,
        search_on_add=args.search,
        sleep=args.sleep,
        dry_run=args.dry_run,
        limit=args.limit,
        backfill_tags=args.backfill_tags,
    )
    unmatched_path = args.unmatched_csv or (args.csv.rsplit(".", 1)[0] + ".unmatched.csv")
    return run(cfg, args.csv, unmatched_path)


if __name__ == "__main__":
    sys.exit(main())
