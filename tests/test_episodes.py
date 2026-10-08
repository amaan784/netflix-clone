"""Seasons and episodes: the generator, up-next / Continue Watching rules, watch
validation, and TMDB episode names. The rule tests run on a tiny scratch warehouse."""
import json
import sqlite3
import urllib.parse

import pytest
from fastapi import HTTPException

from paths import SCHEMA_PATH
from backend import episodes, main, store
from data_engineering import generate_data, tmdb
from data_engineering.catalog_seed import CATALOG


def test_generator_follows_the_catalog_seasons():
    index = {i: i + 1 for i in range(len(CATALOG))}
    eps = generate_data.generate_episodes(index)
    assert len(eps) == sum(sum(t.get("seasons", [])) for t in CATALOG)
    assert len({e["episode_id"] for e in eps}) == len(eps)
    st = [e for e in eps if e["title_id"] == 1]  # Stranger Things
    assert [sum(1 for e in st if e["season_number"] == s) for s in (1, 2, 3, 4)] == [8, 9, 8, 9]


@pytest.fixture
def mini(tmp_path, monkeypatch):
    """A warehouse with one series (2 seasons x 2 episodes), one film and one profile."""
    db = tmp_path / "mini.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript("""
        INSERT INTO dim_title (title_id, title, content_type, release_year) VALUES
            (1, 'Show', 'series', 2020), (2, 'Film', 'movie', 2021);
        INSERT INTO dim_episode VALUES
            (11, 1, 1, 1, 'Pilot', 50, NULL), (12, 1, 1, 2, 'Second', 50, NULL),
            (21, 1, 2, 1, 'Return', 50, NULL), (22, 1, 2, 2, 'Finale', 55, NULL);
        INSERT INTO dim_user (user_id, full_name, is_synthetic) VALUES (1, 'Tester', 0);
        INSERT INTO dim_profile (profile_id, user_id, profile_name) VALUES (7, 1, 'Tester');
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(episodes, "DB_PATH", db)
    monkeypatch.setattr(store, "DB_PATH", db)

    def watch(episode_id, completion, title_id=1):
        store.record_watch(7, title_id, 600, completion, episode_id=episode_id)
    return watch


def cw():
    return [(e["title_id"], e.get("episode_label"), e["progress"])
            for e in store.get_continue_watching(7)]


def test_up_next_walks_through_the_seasons(mini):
    assert episodes.up_next(7, 1)["episode_id"] == 11            # nothing watched yet
    mini(11, 0.4)
    assert episodes.up_next(7, 1)["episode_id"] == 11            # resume the pilot
    mini(11, 1.0)
    assert episodes.up_next(7, 1)["episode_id"] == 12            # pilot done: episode 2
    mini(12, 0.95)
    assert episodes.up_next(7, 1)["episode_id"] == 21            # season 1 done: season 2
    mini(22, 1.0)
    assert episodes.up_next(7, 1)["episode_id"] == 11            # all done: start over


def test_continue_watching_for_series_and_films(mini):
    mini(11, 0.03)
    assert cw() == []                                    # only peeked at the pilot
    mini(11, 0.5)
    assert cw() == [(1, "S1:E1", 50)]                    # stopped halfway
    mini(11, 1.0)
    assert cw() == [(1, "S1:E2", 0)]                     # finished it: next one is up
    mini(None, 0.4, title_id=2)
    assert cw()[0] == (2, None, 40)                      # films work as before
    mini(22, 1.0)
    assert [t for t, _, _ in cw()] == [2]                # finished the show: it drops out


def test_seasons_carry_progress(mini):
    mini(12, 0.5)
    groups = episodes.seasons(1, profile_id=7)
    assert [g["season"] for g in groups] == [1, 2]
    s1 = {e["episode_id"]: e["progress"] for e in groups[0]["episodes"]}
    assert s1 == {11: None, 12: 50}
    assert groups[1]["episodes"][1]["label"] == "S2:E2"


def test_watch_needs_a_matching_episode():
    titles = main.svc().all_titles()
    series = next(t for t in titles if t["content_type"] == "series")
    film = next(t for t in titles if t["content_type"] == "movie")
    other = next(t for t in titles if t["content_type"] == "series" and t["title_id"] != series["title_id"])
    wrong_ep = episodes.for_title(other["title_id"])[0]["episode_id"]
    for body in ({"title_id": series["title_id"]},                            # no episode
                 {"title_id": series["title_id"], "episode_id": wrong_ep},    # someone else's
                 {"title_id": film["title_id"], "episode_id": wrong_ep}):     # films have none
        with pytest.raises(HTTPException) as err:
            main.watch(main.WatchBody(profile_id=1, completion=0.5, **body))
        assert err.value.status_code == 400


def test_title_detail_lists_seasons():
    series = next(t for t in main.svc().all_titles() if t["content_type"] == "series")
    d = main.get_title(series["title_id"], profile_id=None)
    assert d["seasons"] and d["up_next"]["label"] == "S1:E1"
    film = next(t for t in main.svc().all_titles() if t["content_type"] == "movie")
    assert "seasons" not in main.get_title(film["title_id"], profile_id=None)


def test_tmdb_episode_names_reach_the_warehouse(mini, tmp_path):
    def fake(url, headers):
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc == "image.tmdb.org":
            return b"\xff\xd8still"
        if parsed.path.endswith("/configuration"):
            return b"{}"
        if parsed.path.endswith("/search/tv"):
            return json.dumps({"results": [{"id": 5, "name": "Show", "first_air_date": "2020-01-01"}]}).encode()
        if parsed.path.endswith("/images"):
            return b'{"logos": []}'
        if parsed.path.endswith("/season/1"):
            return json.dumps({"episodes": [
                {"episode_number": 1, "name": "The Real Pilot", "overview": "It begins.",
                 "still_path": "/s1e1.jpg"},
                {"episode_number": 2, "name": "The Real Second", "overview": ""}]}).encode()
        if parsed.path.endswith("/season/2"):
            return json.dumps({"episodes": [{"episode_number": 1, "name": "Back Again"}]}).encode()
        raise AssertionError(url)

    art = tmp_path / "art"
    show = {"title_id": 1, "title": "Show", "content_type": "series", "year": 2020, "seasons": 2}
    manifest = tmdb.run(client=tmdb.TMDB("key", fake), root=art, titles=[show], log=lambda *a: None)
    assert manifest["titles"]["1"]["episodes"]["1x1"]["still"] == "still/1/1x1.jpg"
    assert (art / "still" / "1" / "1x1.jpg").exists()

    conn = sqlite3.connect(episodes.DB_PATH)
    try:
        assert tmdb.enrich_warehouse(conn, manifest) == 3
    finally:
        conn.close()
    names = {e["episode_id"]: (e["episode_title"], e["synopsis"]) for e in episodes.for_title(1)}
    assert names == {11: ("The Real Pilot", "It begins."), 12: ("The Real Second", None),
                     21: ("Back Again", None), 22: ("Finale", None)}
