"""Aggregation queries for the analytics dashboard."""
from __future__ import annotations

import sqlite3


def _rows(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def kpis(conn: sqlite3.Connection) -> dict:
    """Headline numbers for the top of the dashboard."""
    total_hours = conn.execute(
        "SELECT COALESCE(SUM(watch_seconds),0)/3600.0 FROM fact_watch_event").fetchone()[0]
    return {
        "total_titles": conn.execute("SELECT COUNT(*) FROM dim_title").fetchone()[0],
        "total_profiles": conn.execute("SELECT COUNT(*) FROM dim_profile").fetchone()[0],
        "total_watch_events": conn.execute("SELECT COUNT(*) FROM fact_watch_event").fetchone()[0],
        "total_watch_hours": round(total_hours, 1),
        "total_ratings": conn.execute("SELECT COUNT(*) FROM fact_rating").fetchone()[0],
        "avg_rating": round(conn.execute(
            "SELECT COALESCE(AVG(rating),0) FROM fact_rating").fetchone()[0], 2),
        "avg_completion_pct": round(conn.execute(
            "SELECT COALESCE(AVG(completion),0)*100 FROM fact_watch_event").fetchone()[0], 1),
    }


def top_titles_by_watch_hours(conn, limit=10):
    return _rows(conn, """
        SELECT t.title_id, t.title, t.content_type,
               ROUND(SUM(f.watch_seconds)/3600.0, 1) AS watch_hours,
               COUNT(*) AS views,
               COUNT(DISTINCT f.profile_id) AS unique_viewers
        FROM fact_watch_event f JOIN dim_title t ON f.title_id = t.title_id
        GROUP BY t.title_id ORDER BY watch_hours DESC LIMIT ?""", (limit,))


def watch_hours_by_genre(conn):
    return _rows(conn, """
        SELECT g.genre_name,
               ROUND(SUM(f.watch_seconds)/3600.0, 1) AS watch_hours,
               COUNT(*) AS views
        FROM fact_watch_event f
        JOIN bridge_title_genre b ON f.title_id = b.title_id
        JOIN dim_genre g ON b.genre_id = g.genre_id
        GROUP BY g.genre_name ORDER BY watch_hours DESC""")


def completion_rate_by_genre(conn):
    return _rows(conn, """
        SELECT g.genre_name,
               ROUND(AVG(f.completion)*100, 1) AS avg_completion_pct,
               ROUND(AVG(f.completed)*100, 1) AS finish_rate_pct
        FROM fact_watch_event f
        JOIN bridge_title_genre b ON f.title_id = b.title_id
        JOIN dim_genre g ON b.genre_id = g.genre_id
        GROUP BY g.genre_name ORDER BY avg_completion_pct DESC""")


def watch_hours_by_device(conn):
    return _rows(conn, """
        SELECT device,
               ROUND(SUM(watch_seconds)/3600.0, 1) AS watch_hours,
               COUNT(*) AS views
        FROM fact_watch_event GROUP BY device ORDER BY watch_hours DESC""")


def daily_engagement(conn, limit=30):
    """Recent daily active profiles + watch hours (uses the date dimension)."""
    rows = _rows(conn, """
        SELECT d.cal_date,
               COUNT(DISTINCT f.profile_id) AS active_profiles,
               ROUND(SUM(f.watch_seconds)/3600.0, 1) AS watch_hours
        FROM fact_watch_event f JOIN dim_date d ON f.date_id = d.date_id
        GROUP BY d.cal_date ORDER BY d.cal_date DESC LIMIT ?""", (limit,))
    return list(reversed(rows))


def rating_distribution(conn):
    return _rows(conn, """
        SELECT rating, COUNT(*) AS n FROM fact_rating
        GROUP BY rating ORDER BY rating""")


def content_type_split(conn):
    return _rows(conn, """
        SELECT t.content_type, COUNT(DISTINCT t.title_id) AS titles,
               COUNT(f.event_id) AS views
        FROM dim_title t LEFT JOIN fact_watch_event f ON t.title_id = f.title_id
        GROUP BY t.content_type""")


def top_in_country(conn, country, limit=10):
    """Most-watched titles among viewers in a given country."""
    return _rows(conn, """
        SELECT t.title_id, t.title, COUNT(*) AS views
        FROM fact_watch_event f
        JOIN dim_profile p ON f.profile_id = p.profile_id
        JOIN dim_user u ON p.user_id = u.user_id
        JOIN dim_title t ON f.title_id = t.title_id
        WHERE u.country = ?
        GROUP BY t.title_id ORDER BY views DESC LIMIT ?""", (country, limit))


def dashboard(conn: sqlite3.Connection) -> dict:
    """Bundle everything the analytics page needs in one payload."""
    return {
        "kpis": kpis(conn),
        "top_titles": top_titles_by_watch_hours(conn),
        "watch_hours_by_genre": watch_hours_by_genre(conn),
        "completion_by_genre": completion_rate_by_genre(conn),
        "watch_hours_by_device": watch_hours_by_device(conn),
        "daily_engagement": daily_engagement(conn),
        "rating_distribution": rating_distribution(conn),
        "content_type_split": content_type_split(conn),
    }
