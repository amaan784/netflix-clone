"""Persists user actions (watch events, ratings, My List) to the database."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from paths import DB_PATH


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _ensure_date(conn, ts: str) -> int:
    d = datetime.fromisoformat(ts).date()
    date_id = int(d.strftime("%Y%m%d"))
    exists = conn.execute("SELECT 1 FROM dim_date WHERE date_id=?", (date_id,)).fetchone()
    if not exists:
        conn.execute(
            """INSERT INTO dim_date (date_id,cal_date,year,month,day,weekday,is_weekend)
               VALUES (?,?,?,?,?,?,?)""",
            (date_id, d.isoformat(), d.year, d.month, d.day,
             d.strftime("%A"), 1 if d.weekday() >= 5 else 0))
    return date_id


def record_watch(profile_id: int, title_id: int, watch_seconds: int,
                 completion: float, device: str = "Web") -> dict:
    ts = datetime.now().isoformat(timespec="seconds")
    completion = max(0.0, min(1.0, float(completion)))
    conn = _conn()
    try:
        date_id = _ensure_date(conn, ts)
        cur = conn.execute(
            """INSERT INTO fact_watch_event
               (profile_id,title_id,date_id,watch_seconds,completion,completed,device,event_ts)
               VALUES (?,?,?,?,?,?,?,?)""",
            (profile_id, title_id, date_id, int(watch_seconds), completion,
             1 if completion >= 0.9 else 0, device, ts))
        conn.commit()
        return {"event_id": cur.lastrowid, "completion": completion}
    finally:
        conn.close()


def record_rating(profile_id: int, title_id: int, rating: int) -> dict:
    rating = max(1, min(5, int(rating)))
    ts = datetime.now().isoformat(timespec="seconds")
    conn = _conn()
    try:
        date_id = _ensure_date(conn, ts)
        # one rating per (profile, title): replace existing
        conn.execute("DELETE FROM fact_rating WHERE profile_id=? AND title_id=?",
                     (profile_id, title_id))
        cur = conn.execute(
            """INSERT INTO fact_rating (profile_id,title_id,date_id,rating,rated_ts)
               VALUES (?,?,?,?,?)""", (profile_id, title_id, date_id, rating, ts))
        conn.commit()
        return {"rating_id": cur.lastrowid, "rating": rating}
    finally:
        conn.close()


def add_to_list(profile_id: int, title_id: int):
    ts = datetime.now().isoformat(timespec="seconds")
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO my_list (profile_id,title_id,added_ts) VALUES (?,?,?)",
            (profile_id, title_id, ts))
        conn.commit()
    finally:
        conn.close()


def remove_from_list(profile_id: int, title_id: int):
    conn = _conn()
    try:
        conn.execute("DELETE FROM my_list WHERE profile_id=? AND title_id=?",
                     (profile_id, title_id))
        conn.commit()
    finally:
        conn.close()


def get_list(profile_id: int) -> list[int]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT title_id FROM my_list WHERE profile_id=? ORDER BY added_ts DESC",
            (profile_id,)).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_continue_watching(profile_id: int, limit: int = 18) -> list[dict]:
    """Titles the profile started but didn't finish, most recent first.

    Uses the latest watch event per title; keeps it if the latest completion is
    between 5% and 90% (started, not finished). Returns title_id + resume pct.
    """
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT title_id, completion FROM fact_watch_event "
            "WHERE profile_id=? ORDER BY event_ts DESC", (profile_id,)).fetchall()
    finally:
        conn.close()
    out, seen = [], set()
    for title_id, completion in rows:
        if title_id in seen:
            continue
        seen.add(title_id)
        if 0.05 < completion < 0.9:
            out.append({"title_id": title_id, "progress": round(completion * 100)})
        if len(out) >= limit:
            break
    return out


def get_profile_country(profile_id: int) -> str | None:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT u.country FROM dim_profile p JOIN dim_user u ON p.user_id = u.user_id "
            "WHERE p.profile_id = ?", (profile_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_top_in_country(country: str, limit: int = 10) -> list[dict]:
    from data_engineering import analytics
    conn = _conn()
    try:
        return analytics.top_in_country(conn, country, limit)
    finally:
        conn.close()


def get_analytics() -> dict:
    from data_engineering import analytics
    conn = _conn()
    try:
        return analytics.dashboard(conn)
    finally:
        conn.close()
