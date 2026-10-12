"""Smoke-test the API route functions directly (no HTTP server needed; signing in
and the guard in front of the API are tested over HTTP in test_auth.py)."""
from backend import main


def test_home(demo_pid):
    home = main.get_home(profile_id=demo_pid)
    assert home["rows"], "home returned no rows"
    kinds = {r["kind"] for r in home["rows"]}
    assert "personalized" in kinds


def test_recommendations_and_search(demo_pid):
    assert main.recommendations(profile_id=demo_pid)["items"]
    assert "items" in main.search(q="heist movie", profile_id=demo_pid)


def test_title_detail_and_poster(demo_pid, tmp_path, monkeypatch):
    from data_engineering import tmdb
    monkeypatch.setattr(tmdb, "TMDB_DIR", tmp_path)  # no downloaded art: generated posters
    pid = demo_pid
    catalog = main.catalog(type=None, profile_id=pid)["items"]
    tid = catalog[0]["title_id"]
    detail = main.get_title(tid, profile_id=pid)
    assert detail["stats"] is not None
    assert len(detail["more_like_this"]) >= 1
    resp = main.poster(tid)
    assert resp.body.startswith(b"<svg")


def test_eval_endpoint():
    res = main.eval_models(k=10, include_kids=True)
    assert "hybrid" in res["models"]
