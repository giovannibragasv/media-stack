import csv
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import import_letterboxd_to_radarr as importer

MONTH_TAG = f"wl-{date.today():%Y-%m}"


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise importer.requests.HTTPError(self.text)


class FakeRadarr:
    """In-memory Radarr behind a requests.Session look-alike."""

    def __init__(self, tags=(), movies=(), lookups=None):
        self.tags = [dict(t) for t in tags]
        self.movies = [dict(m) for m in movies]
        self.lookups = lookups or {}
        self.writes = []
        self.headers = {}

    def __call__(self):
        return self

    def _path(self, url):
        return url.split("/api/v3", 1)[1]

    def get(self, url, params=None, timeout=None):
        path = self._path(url)
        if path == "/system/status":
            return FakeResponse(200, {"version": "test"})
        if path == "/movie":
            return FakeResponse(200, self.movies)
        if path == "/tag":
            return FakeResponse(200, self.tags)
        if path == "/movie/lookup":
            return FakeResponse(200, self.lookups.get(params["term"], []))
        raise AssertionError(f"unexpected GET {path}")

    def post(self, url, json=None, timeout=None):
        path = self._path(url)
        self.writes.append(("POST", path, json))
        if path == "/tag":
            tag = {"id": len(self.tags) + 1, "label": json["label"]}
            self.tags.append(tag)
            return FakeResponse(201, tag)
        if path == "/movie":
            movie = {**json, "id": 100 + len(self.movies)}
            self.movies.append(movie)
            return FakeResponse(201, movie)
        raise AssertionError(f"unexpected POST {path}")

    def put(self, url, json=None, timeout=None):
        path = self._path(url)
        self.writes.append(("PUT", path, json))
        if path == "/movie/editor":
            for m in self.movies:
                if m["id"] in json["movieIds"]:
                    assert json["applyTags"] == "add"
                    m["tags"] = sorted(set(m.get("tags", [])) | set(json["tags"]))
            return FakeResponse(202, [])
        raise AssertionError(f"unexpected PUT {path}")

    def tag_id(self, label):
        return next(t["id"] for t in self.tags if t["label"] == label)

    def movie(self, tmdb_id):
        return next(m for m in self.movies if m["tmdbId"] == tmdb_id)


def lookup_result(tmdb_id, title, year):
    return [{"tmdbId": tmdb_id, "title": title, "year": year, "titleSlug": title.lower()}]


class ImporterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_importer(self, fake, rows, dry_run=False, backfill=False):
        csv_path = Path(self.tmp.name) / "watchlist.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["Date", "Name", "Year", "Letterboxd URI"])
            w.writeheader()
            for name, year in rows:
                w.writerow({"Date": "", "Name": name, "Year": year, "Letterboxd URI": ""})
        cfg = importer.Config(
            radarr_url="http://radarr.test",
            api_key="test",
            root_folder="/data/movies",
            quality_profile_id=1,
            search_on_add=False,
            sleep=0,
            dry_run=dry_run,
            limit=None,
            backfill_tags=backfill,
        )
        with mock.patch.object(importer.requests, "Session", fake), self.assertLogs(importer.log):
            code = importer.run(cfg, str(csv_path), str(Path(self.tmp.name) / "unmatched.csv"))
        self.assertEqual(code, 0)


class EnsureTagsTest(ImporterTest):
    def test_creates_missing_tags_once_across_runs(self):
        fake = FakeRadarr(tags=[{"id": 7, "label": "src-letterboxd"}])
        client = importer.Radarr("http://radarr.test", "test", sleep=0)
        client.session = fake

        first = client.ensure_tags(["src-letterboxd", MONTH_TAG])
        second = client.ensure_tags(["src-letterboxd", MONTH_TAG])

        self.assertEqual(first, second)
        self.assertEqual(first["src-letterboxd"], 7)
        self.assertEqual([t["label"] for t in fake.tags], ["src-letterboxd", MONTH_TAG])
        self.assertEqual(len([w for w in fake.writes if w[1] == "/tag"]), 1)

    def test_labels_fit_radarr_tag_rules(self):
        for label in importer.provenance_labels(date(2026, 7, 3)):
            self.assertRegex(label, r"^[a-z0-9-]+$")
        self.assertEqual(
            importer.provenance_labels(date(2026, 7, 3)), ["src-letterboxd", "wl-2026-07"]
        )


class AddTest(ImporterTest):
    def test_added_movie_carries_both_provenance_tags(self):
        fake = FakeRadarr(lookups={"Heat 1995": lookup_result(949, "Heat", 1995)})

        self.run_importer(fake, [("Heat", "1995")])

        tags = fake.movie(949)["tags"]
        self.assertCountEqual(tags, [fake.tag_id("src-letterboxd"), fake.tag_id(MONTH_TAG)])


class BackfillTest(ImporterTest):
    def setUp(self):
        super().setUp()
        self.fake = FakeRadarr(
            tags=[{"id": 3, "label": "keep-me"}],
            movies=[
                {"id": 1, "tmdbId": 949, "title": "Heat", "tags": [3]},
                {"id": 2, "tmdbId": 680, "title": "Pulp Fiction", "tags": []},
            ],
            lookups={"Heat 1995": lookup_result(949, "Heat", 1995)},
        )

    def test_adds_source_tag_only_to_matched_movies_and_keeps_existing_tags(self):
        self.run_importer(self.fake, [("Heat", "1995")], backfill=True)

        src = self.fake.tag_id("src-letterboxd")
        self.assertCountEqual(self.fake.movie(949)["tags"], [3, src])
        self.assertEqual(self.fake.movie(680)["tags"], [])

    def test_second_run_makes_no_editor_call(self):
        self.run_importer(self.fake, [("Heat", "1995")], backfill=True)
        self.fake.writes.clear()

        self.run_importer(self.fake, [("Heat", "1995")], backfill=True)

        self.assertEqual(self.fake.writes, [])

    def test_dry_run_writes_nothing(self):
        self.fake.lookups["Ronin 1998"] = lookup_result(8195, "Ronin", 1998)

        self.run_importer(self.fake, [("Heat", "1995"), ("Ronin", "1998")], True, True)

        self.assertEqual(self.fake.writes, [])
        self.assertEqual([t["label"] for t in self.fake.tags], ["keep-me"])


if __name__ == "__main__":
    unittest.main()
