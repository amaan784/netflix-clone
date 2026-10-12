"""
Signing in: sessions in a cookie, and the guard in front of the API.

Every /api call needs a signed-in session, apart from the ones that sign you in
and the images. A call that names a profile_id, in the query string or in the
JSON body, must name one of the signed-in account's profiles. The guard is
plain ASGI middleware, so the route functions themselves don't change.

The session cookie is HttpOnly (page scripts can't read it) and SameSite=Lax
(other sites can't make the browser send it with a POST). Only a SHA-256 of the
token is stored, so a copy of the database doesn't contain usable sessions.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from paths import DB_PATH
from data_engineering import accounts

COOKIE = "netclone_session"
SESSION_DAYS = 30
PUBLIC = {"/api/auth/login", "/api/auth/signup", "/api/auth/logout", "/api/auth/me", "/api/artwork"}
PUBLIC_PREFIXES = ("/api/poster/", "/api/backdrop/", "/api/logo/", "/api/still/")
MAX_FAILURES, FAILURE_WINDOW = 5, 600  # failed sign-ins allowed per email per 10 minutes

router = APIRouter()
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_session(account_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    conn = _conn()
    try:
        conn.execute("INSERT INTO app_session (token_hash, account_id, created_ts, expires_ts) "
                     "VALUES (?, ?, ?, ?)",
                     (_hash(token), account_id, now.isoformat(timespec="seconds"),
                      (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")))
        conn.commit()
    finally:
        conn.close()
    return token


def end_session(token: str | None):
    if not token:
        return
    conn = _conn()
    try:
        conn.execute("DELETE FROM app_session WHERE token_hash = ?", (_hash(token),))
        conn.commit()
    finally:
        conn.close()


def account_for(token: str | None) -> dict | None:
    if not token:
        return None
    conn = _conn()
    try:
        row = conn.execute("""SELECT a.account_id, a.email, a.user_id, u.full_name, s.expires_ts
                              FROM app_session s JOIN app_account a ON a.account_id = s.account_id
                              LEFT JOIN dim_user u ON u.user_id = a.user_id
                              WHERE s.token_hash = ?""", (_hash(token),)).fetchone()
    except sqlite3.OperationalError:  # the warehouse is being (re)built
        return None
    finally:
        conn.close()
    if not row or row[4] < datetime.now().isoformat(timespec="seconds"):
        return None
    return {"account_id": row[0], "email": row[1], "user_id": row[2], "name": row[3]}


def owns_profile(account: dict, profile_id) -> bool:
    try:
        profile_id = int(profile_id)
    except (TypeError, ValueError):
        return False
    conn = _conn()
    try:
        return conn.execute("SELECT 1 FROM dim_profile WHERE profile_id = ? AND user_id = ?",
                            (profile_id, account["user_id"])).fetchone() is not None
    finally:
        conn.close()


class Guard:
    """ASGI middleware: signed-in check and profile ownership for /api/*."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (scope["type"] != "http" or not path.startswith("/api/") or path in PUBLIC
                or path.startswith(PUBLIC_PREFIXES)):
            return await self.app(scope, receive, send)

        request = Request(scope)
        account = account_for(request.cookies.get(COOKIE))
        if account is None:
            return await JSONResponse({"detail": "Sign in first."}, status_code=401)(scope, receive, send)

        # read the body to look for a profile_id, then pass it on untouched
        messages, body = [], b""
        if scope["method"] in ("POST", "PUT", "PATCH", "DELETE"):
            while True:
                message = await receive()
                messages.append(message)
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
        profile_id = request.query_params.get("profile_id")
        if profile_id is None and body:
            try:
                payload = json.loads(body)
                profile_id = payload.get("profile_id") if isinstance(payload, dict) else None
            except ValueError:
                pass  # not JSON: the route will reject it
        if profile_id not in (None, "") and not owns_profile(account, profile_id):
            return await JSONResponse({"detail": "That profile isn't on your account."},
                                      status_code=403)(scope, receive, send)

        scope.setdefault("state", {})["account"] = account

        async def replay():
            return messages.pop(0) if messages else await receive()
        await self.app(scope, replay, send)


# sign-in attempts
def _too_many_failures(email: str) -> bool:
    now = time.time()
    with _failures_lock:
        recent = [t for t in _failures.get(email, []) if now - t < FAILURE_WINDOW]
        _failures[email] = recent
        return len(recent) >= MAX_FAILURES


def _note_failure(email: str):
    with _failures_lock:
        _failures.setdefault(email, []).append(time.time())


# routes
class SignUp(BaseModel):
    name: str
    email: str
    password: str


class SignIn(BaseModel):
    email: str
    password: str


class NewProfile(BaseModel):
    name: str
    is_kids: bool = False


def _me(account: dict) -> dict:
    conn = _conn()
    try:
        profiles = accounts.profiles_of(conn, account["user_id"])
    finally:
        conn.close()
    return {"account": {"email": account["email"], "name": account["name"]}, "profiles": profiles}


def _sign_in(response: Response, account: dict) -> dict:
    token = start_session(account["account_id"])
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", path="/")
    return _me(account)


@router.post("/api/auth/signup")
def signup(body: SignUp, response: Response):
    conn = _conn()
    try:
        account = accounts.create_account(conn, body.name, body.email, body.password)
    except accounts.AccountError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()
    return _sign_in(response, account)


@router.post("/api/auth/login")
def login(body: SignIn, response: Response):
    email = body.email.strip().lower()
    if _too_many_failures(email):
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    conn = _conn()
    try:
        account = accounts.check_login(conn, email, body.password)
    finally:
        conn.close()
    if not account:
        _note_failure(email)
        raise HTTPException(401, "That email and password don't match an account.")
    return _sign_in(response, account)


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    end_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
def me(request: Request):
    """Who's signed in: the account and its profiles, or {"account": null}. Public,
    so the page can ask at startup without a 401 in the console."""
    account = account_for(request.cookies.get(COOKIE))
    return _me(account) if account else {"account": None, "profiles": []}


@router.get("/api/profiles")
def get_profiles(request: Request):
    return _me(request.state.account)["profiles"]


@router.post("/api/profiles")
def create_profile(body: NewProfile, request: Request):
    conn = _conn()
    try:
        return accounts.add_profile(conn, request.state.account["user_id"], body.name, body.is_kids)
    except accounts.AccountError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()
