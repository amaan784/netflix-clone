"""Seasons and episodes: listing them, and working out which one plays next."""
from __future__ import annotations

import sqlite3

from paths import DB_PATH

COLS = "episode_id, title_id, season_number, episode_number, episode_title, runtime_minutes, synopsis"


def _rows(sql: str, params=()) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def label(ep: dict) -> str:
    return f"S{ep['season_number']}:E{ep['episode_number']}"


def get(episode_id: int) -> dict | None:
    rows = _rows(f"SELECT {COLS} FROM dim_episode WHERE episode_id = ?", (episode_id,))
    return rows[0] if rows else None


def for_title(title_id: int) -> list[dict]:
    return _rows(f"SELECT {COLS} FROM dim_episode WHERE title_id = ? "
                 "ORDER BY season_number, episode_number", (title_id,))


def next_after(ep: dict) -> dict | None:
    rows = _rows(f"""SELECT {COLS} FROM dim_episode
        WHERE title_id = ? AND (season_number, episode_number) > (?, ?)
        ORDER BY season_number, episode_number LIMIT 1""",
        (ep["title_id"], ep["season_number"], ep["episode_number"]))
    return rows[0] if rows else None


def last_watched(profile_id: int, title_id: int) -> dict | None:
    """The profile's most recent episode event for a series: episode_id + completion."""
    rows = _rows("""SELECT episode_id, completion FROM fact_watch_event
        WHERE profile_id = ? AND title_id = ? AND episode_id IS NOT NULL
        ORDER BY event_ts DESC, event_id DESC LIMIT 1""", (profile_id, title_id))
    return rows[0] if rows else None


def up_next(profile_id: int | None, title_id: int) -> dict | None:
    """The episode Play starts: the one stopped partway, else the one after the
    last finished, else (nothing watched yet, or all of it) the first."""
    eps = for_title(title_id)
    if not eps:
        return None
    last = last_watched(profile_id, title_id) if profile_id else None
    if last:
        i = next(k for k, e in enumerate(eps) if e["episode_id"] == last["episode_id"])
        if last["completion"] < 0.9:
            return eps[i]
        if i + 1 < len(eps):
            return eps[i + 1]
    return eps[0]


def seasons(title_id: int, profile_id: int | None = None) -> list[dict]:
    """Episodes grouped by season, each with the profile's progress (latest watch, in %)."""
    done = {}
    if profile_id:
        for r in _rows("""SELECT episode_id, completion FROM fact_watch_event
                WHERE profile_id = ? AND title_id = ? AND episode_id IS NOT NULL
                ORDER BY event_ts, event_id""", (profile_id, title_id)):
            done[r["episode_id"]] = r["completion"]  # later watches overwrite earlier ones
    grouped = {}
    for ep in for_title(title_id):
        ep["label"] = label(ep)
        ep["progress"] = round(done[ep["episode_id"]] * 100) if ep["episode_id"] in done else None
        grouped.setdefault(ep["season_number"], []).append(ep)
    return [{"season": n, "episodes": eps} for n, eps in grouped.items()]
