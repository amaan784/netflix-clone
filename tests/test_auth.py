"""Accounts and sign-in, through the real HTTP stack (FastAPI's TestClient)
against a scratch copy of the warehouse, so tests never add accounts to yours."""
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from paths import DB_PATH
from backend import auth, main
from data_engineering import accounts, generate_data, pipeline

PASSWORD = "correct horse battery"


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    db = tmp_path / "netflix.db"
    shutil.copyfile(DB_PATH, db)
    monkeypatch.setattr(auth, "DB_PATH", db)
    monkeypatch.setattr(auth, "_failures", {})
    return db


@pytest.fixture
def client(scratch_db):
    return TestClient(main.app)  # no `with`: the app's startup jobs don't run


def signup(client, email="viewer@example.test", name="Vera Viewer"):
    return client.post("/api/auth/signup", json={"name": name, "email": email, "password": PASSWORD})


def test_passwords_are_salted_and_checked():
    a, b = accounts.hash_password(PASSWORD), accounts.hash_password(PASSWORD)
    assert a != b and a.startswith("scrypt$")
    assert accounts.verify_password(PASSWORD, a) and not accounts.verify_password("wrong", a)
    assert not accounts.verify_password(PASSWORD, "garbage")


def test_sign_up_gives_a_session_and_a_first_profile(client):
    r = signup(client)
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    me = client.get("/api/auth/me").json()
    assert me["account"]["email"] == "viewer@example.test"
    assert [p["name"] for p in me["profiles"]] == ["Vera"]
    assert me["profiles"][0]["profile_id"] >= accounts.APP_ID_START


def test_sign_up_rejects_bad_input(client):
    assert client.post("/api/auth/signup", json={"name": "X", "email": "not-an-email",
                                                  "password": PASSWORD}).status_code == 400
    assert client.post("/api/auth/signup", json={"name": "X", "email": "x@example.test",
                                                  "password": "short"}).status_code == 400
    signup(client)
    again = signup(client, email="VIEWER@example.test")  # emails are case-insensitive
    assert again.status_code == 400 and "already" in again.json()["detail"]


def test_api_needs_a_session(client):
    assert client.get("/api/genres").status_code == 401
    assert client.get("/api/auth/me").json() == {"account": None, "profiles": []}
    assert client.get("/api/poster/1").status_code == 200   # images stay public
    signup(client)
    assert client.get("/api/genres").status_code == 200


def test_profiles_are_private_to_their_account(client):
    signup(client)
    mine = client.get("/api/profiles").json()[0]["profile_id"]
    assert client.get(f"/api/mylist?profile_id={mine}").status_code == 200
    # the demo family belongs to another account: query string and JSON body are both checked
    assert client.get("/api/mylist?profile_id=1").status_code == 403
    r = client.post("/api/mylist/add", json={"profile_id": 1, "title_id": 2})
    assert r.status_code == 403


def test_demo_login_and_sign_out(client):
    r = client.post("/api/auth/login", json={"email": generate_data.DEMO_EMAIL,
                                             "password": generate_data.DEMO_PASSWORD})
    assert r.status_code == 200
    assert {"Aman", "Movie Night", "Chill", "Kids"} <= {p["name"] for p in r.json()["profiles"]}
    assert client.get("/api/genres").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/genres").status_code == 401


def test_repeated_wrong_passwords_are_slowed_down(client):
    signup(client)
    client.post("/api/auth/logout")
    for _ in range(auth.MAX_FAILURES):
        assert client.post("/api/auth/login", json={"email": "viewer@example.test",
                                                    "password": "nope"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "viewer@example.test", "password": PASSWORD})
    assert r.status_code == 429  # even the right password waits now


def test_profiles_cap_and_kids_flag(client):
    signup(client)
    kid = client.post("/api/profiles", json={"name": "Junior", "is_kids": True}).json()
    assert kid["is_kids"] and kid["age_group"] == "kids"
    for name in ("Two", "Three", "Four"):
        assert client.post("/api/profiles", json={"name": name}).status_code == 200
    full = client.post("/api/profiles", json={"name": "Six"})
    assert full.status_code == 400 and "up to 5" in full.json()["detail"]


def test_rebuild_keeps_app_accounts(scratch_db, monkeypatch):
    conn = sqlite3.connect(scratch_db)
    try:
        acct = accounts.create_account(conn, "Rita Rebuild", "rita@example.test", PASSWORD)
        kid = accounts.add_profile(conn, acct["user_id"], "Kiddo", is_kids=True)
    finally:
        conn.close()
    monkeypatch.setattr(pipeline, "DB_PATH", scratch_db)
    pipeline.run_pipeline()
    conn = sqlite3.connect(scratch_db)
    try:
        assert accounts.check_login(conn, "rita@example.test", PASSWORD)
        names = {p["name"]: p["is_kids"] for p in accounts.profiles_of(conn, acct["user_id"])}
        assert names == {"Rita": False, "Kiddo": True}
        assert kid["profile_id"] >= accounts.APP_ID_START
        assert accounts.check_login(conn, generate_data.DEMO_EMAIL, generate_data.DEMO_PASSWORD)
    finally:
        conn.close()
