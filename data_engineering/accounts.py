"""
Accounts: password hashing, signing up, profiles, and keeping app-made people
through a warehouse rebuild.

Passwords are hashed with scrypt (a deliberately slow, memory-hard function in
the standard library) with a random salt per password. People who sign up get
user and profile ids from APP_ID_START, clear of the generated population, so
the ETL can drop and rebuild the generated part and put them back afterwards.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import date, datetime

APP_ID_START = 100_000
MAX_PROFILES = 5
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


class AccountError(ValueError):
    """Something about the request can't be done; the message is safe to show."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), dklen=32,
                             n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


# checked against when an email isn't found, so a wrong email takes as long as a wrong password
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def find_account(conn, email: str) -> dict | None:
    row = conn.execute("""SELECT a.account_id, a.email, a.password_hash, a.user_id, u.full_name
                          FROM app_account a LEFT JOIN dim_user u ON u.user_id = a.user_id
                          WHERE a.email = ?""", (email.strip(),)).fetchone()
    if not row:
        return None
    return {"account_id": row[0], "email": row[1], "password_hash": row[2], "user_id": row[3],
            "name": row[4]}


def check_login(conn, email: str, password: str) -> dict | None:
    account = find_account(conn, email)
    ok = verify_password(password, account["password_hash"] if account else _DUMMY_HASH)
    return account if account and ok else None


def _next_id(conn, table: str, column: str) -> int:
    top = conn.execute(f"SELECT MAX({column}) FROM {table}").fetchone()[0] or 0
    return max(APP_ID_START, top + 1)


def create_account(conn, name: str, email: str, password: str, country: str = "USA") -> dict:
    name, email = (name or "").strip(), (email or "").strip().lower()
    if not 1 <= len(name) <= 40:
        raise AccountError("Please enter your name (up to 40 characters).")
    if not EMAIL.match(email):
        raise AccountError("Please enter a valid email address.")
    if len(password or "") < 8:
        raise AccountError("Your password needs at least 8 characters.")
    if find_account(conn, email):
        raise AccountError("There's already an account with that email. Sign in instead.")
    user_id = _next_id(conn, "dim_user", "user_id")
    conn.execute("""INSERT INTO dim_user (user_id, full_name, email, country, plan, signup_date, is_synthetic)
                    VALUES (?, ?, ?, ?, 'Standard', ?, 0)""",
                 (user_id, name, email, country, date.today().isoformat()))
    cur = conn.execute("INSERT INTO app_account (email, password_hash, user_id, created_ts) VALUES (?,?,?,?)",
                       (email, hash_password(password), user_id, datetime.now().isoformat(timespec="seconds")))
    add_profile(conn, user_id, name.split()[0])
    conn.commit()
    return {"account_id": cur.lastrowid, "email": email, "user_id": user_id, "name": name}


def profiles_of(conn, user_id: int) -> list[dict]:
    rows = conn.execute("""SELECT profile_id, profile_name, is_kids, avatar_hue, age_group
                           FROM dim_profile WHERE user_id = ? ORDER BY profile_id""", (user_id,)).fetchall()
    return [{"profile_id": r[0], "name": r[1], "is_kids": bool(r[2]), "avatar_hue": r[3],
             "age_group": r[4]} for r in rows]


def add_profile(conn, user_id: int, name: str, is_kids: bool = False) -> dict:
    name = (name or "").strip()
    if not 1 <= len(name) <= 20:
        raise AccountError("A profile name needs 1 to 20 characters.")
    if len(profiles_of(conn, user_id)) >= MAX_PROFILES:
        raise AccountError(f"An account can have up to {MAX_PROFILES} profiles.")
    profile_id = _next_id(conn, "dim_profile", "profile_id")
    hue = secrets.randbelow(360)
    conn.execute("""INSERT INTO dim_profile (profile_id, user_id, profile_name, is_kids, avatar_hue, age_group)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                 (profile_id, user_id, name, int(is_kids), hue, "kids" if is_kids else "adult"))
    conn.commit()
    return {"profile_id": profile_id, "name": name, "is_kids": bool(is_kids), "avatar_hue": hue,
            "age_group": "kids" if is_kids else "adult"}


# --- kept through a rebuild -------------------------------------------------
def app_people(conn: sqlite3.Connection) -> dict:
    """The users and profiles made in the app (ids from APP_ID_START), read
    before the ETL drops the dimension tables."""
    def rows(table, key):
        try:
            cur = conn.execute(f"SELECT * FROM {table} WHERE {key} >= ?", (APP_ID_START,))
        except sqlite3.OperationalError:  # first build: nothing there yet
            return []
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    return {"dim_user": rows("dim_user", "user_id"), "dim_profile": rows("dim_profile", "profile_id")}


def restore_app_people(conn: sqlite3.Connection, people: dict) -> int:
    for table, rows in people.items():
        for row in rows:
            cols = ", ".join(row)
            conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(row))})",
                         list(row.values()))
    return sum(len(r) for r in people.values())


def ensure_login(conn: sqlite3.Connection, email: str, password: str, user_id: int):
    """Give a (generated) user a login, unless the email already has one."""
    if not find_account(conn, email):
        conn.execute("INSERT INTO app_account (email, password_hash, user_id, created_ts) VALUES (?,?,?,?)",
                     (email.lower(), hash_password(password), user_id,
                      datetime.now().isoformat(timespec="seconds")))
