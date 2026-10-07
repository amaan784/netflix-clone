"""TMDB artwork: title matching, the download job (run against a fake TMDB), and serving."""
import json
import urllib.parse
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend import main
from data_engineering import tmdb

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
PNG = b"\x89PNG\r\n\x1a\nfake-png"
QUIET = {"log": lambda *a: None}


class FakeTMDB:
    """Stands in for the HTTP layer. Every search finds an exact match for the
    requested name and year, except the names listed in `unknown`."""

    def __init__(self, unknown=()):
        self.unknown = set(unknown)
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        parsed = urllib.parse.urlparse(url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        if parsed.netloc == "image.tmdb.org":
            return PNG if url.endswith(".png") else JPEG
        if parsed.path.endswith("/configuration"):
            return b"{}"
        if "/search/" in parsed.path:
            year = q.get("year") or q.get("first_air_date_year")
            if q["query"] in self.unknown or not year:
                return b'{"results": []}'
            date_field = "release_date" if parsed.path.endswith("movie") else "first_air_date"
            hit = {"id": 100, "title": q["query"], "name": q["query"], date_field: f"{year}-06-01",
                   "poster_path": "/poster.jpg", "backdrop_path": "/backdrop.jpg"}
            return json.dumps({"results": [hit]}).encode()
        if parsed.path.endswith("/images"):
            return json.dumps({"logos": [{"file_path": "/logo.png", "iso_639_1": "en"}]}).encode()
        raise AssertionError(f"unexpected request: {url}")


TITLES = [{"title_id": 1, "title": "Stranger Things", "content_type": "series", "year": 2016},
          {"title_id": 2, "title": "Not On TMDB", "content_type": "movie", "year": 2022}]


def test_best_match_wants_same_name_and_year():
    results = [
        {"id": 1, "name": "Dark Matter", "first_air_date": "2017-02-01", "poster_path": "/a.jpg"},
        {"id": 2, "name": "Dark", "first_air_date": "2017-12-01", "poster_path": "/b.jpg"},
        {"id": 3, "name": "Dark", "first_air_date": "2005-01-01", "poster_path": "/c.jpg"},
    ]
    assert tmdb.best_match(results, "Dark", 2017)["id"] == 2


def test_best_match_accepts_longer_official_name():
    results = [{"id": 7, "title": "Glass Onion: A Knives Out Mystery", "release_date": "2022-09-10"}]
    assert tmdb.best_match(results, "Glass Onion", 2022)["id"] == 7


def test_best_match_rejects_other_years():
    # a made-up catalog title must not borrow an older film's poster
    results = [{"id": 9, "title": "Wednesday's Child", "release_date": "1934-11-02"}]
    assert tmdb.best_match(results, "Wednesday's Child", 2022) is None


def test_pick_logo_prefers_english():
    logos = [{"file_path": "/ko.png", "iso_639_1": "ko", "vote_average": 9},
             {"file_path": "/en.png", "iso_639_1": "en", "vote_average": 5},
             {"file_path": "/plain.svg", "iso_639_1": None, "vote_average": 8}]
    assert tmdb.pick_logo(logos) == "/en.png"
    assert tmdb.pick_logo(logos[:1]) is None


def test_fetch_downloads_art_and_writes_manifest(tmp_path):
    fake = FakeTMDB(unknown={"Not On TMDB"})
    manifest = tmdb.run(client=tmdb.TMDB("eyJfake.token", fake), root=tmp_path, titles=TITLES, **QUIET)

    entry = manifest["titles"]["1"]
    assert entry["tmdb_id"] == 100
    assert (tmp_path / entry["poster"]).read_bytes() == JPEG
    assert (tmp_path / entry["backdrop"]).read_bytes() == JPEG
    assert entry["logo"] == "logo/1.png" and (tmp_path / "logo" / "1.png").read_bytes() == PNG
    assert manifest["unmatched"] == ["Not On TMDB"]
    # a read access token travels in the Authorization header, never in the URL
    api_calls = [(u, h) for u, h in fake.calls if "api.themoviedb.org" in u]
    assert api_calls and all(h["Authorization"] == "Bearer eyJfake.token" and "api_key" not in u
                             for u, h in api_calls)


def test_second_fetch_reuses_what_it_has(tmp_path):
    tmdb.run(client=tmdb.TMDB("key", FakeTMDB()), root=tmp_path, titles=TITLES[:1], **QUIET)
    fake = FakeTMDB()
    tmdb.run(client=tmdb.TMDB("key", fake), root=tmp_path, titles=TITLES[:1], **QUIET)
    # just the key check: no searches, no downloads
    assert [urllib.parse.urlparse(u).path for u, _ in fake.calls] == ["/3/configuration"]


def test_v3_key_goes_in_the_query_string():
    fake = FakeTMDB()
    tmdb.TMDB("abc123", fake).get("/configuration")
    url, headers = fake.calls[0]
    assert "api_key=abc123" in url and "Authorization" not in headers


def test_api_key_read_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.setattr(tmdb, "ROOT", tmp_path)
    assert tmdb.api_key() is None
    (tmp_path / ".env").write_text('# my keys\nTMDB_API_KEY="abc123"\n', encoding="utf-8")
    assert tmdb.api_key() == "abc123"


def test_app_serves_downloaded_art(tmp_path, monkeypatch):
    rec = main.svc().get_title(1)
    title = {"title_id": 1, "title": rec["title"], "content_type": rec["content_type"],
             "year": rec["release_year"]}
    tmdb.run(client=tmdb.TMDB("key", FakeTMDB()), root=tmp_path, titles=[title], **QUIET)
    monkeypatch.setattr(tmdb, "TMDB_DIR", tmp_path)

    poster = main.poster(1)
    assert poster.media_type == "image/jpeg" and Path(poster.path).read_bytes() == JPEG
    assert main.backdrop(1).media_type == "image/jpeg"
    assert main.logo(1).media_type == "image/png"
    status = main.artwork_status()
    assert status["source"] == "TMDB" and status["logos"] == [1] and status["attribution"]


def test_without_art_everything_falls_back_to_svg(tmp_path, monkeypatch):
    monkeypatch.setattr(tmdb, "TMDB_DIR", tmp_path)
    assert main.poster(1).body.startswith(b"<svg")
    assert main.backdrop(1).body.startswith(b"<svg")
    with pytest.raises(HTTPException) as err:
        main.logo(1)
    assert err.value.status_code == 404
    assert main.artwork_status() == {"source": None, "posters": 0, "backdrops": 0, "logos": [],
                                     "version": "none", "attribution": None}


def test_banner_fallback_has_no_text():
    # the page draws the title over the banner, so the SVG mustn't draw it too
    assert b"<text" not in main.backdrop(1).body
    assert b"<text" in main.poster(1).body
