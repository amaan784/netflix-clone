"""
Data-quality checks run after each load.

Verifies foreign keys, measure ranges, null columns, that fact tables contain
data, and that series, episodes and watch events fit together.
"""
from __future__ import annotations

import sqlite3


def _scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def run_all_checks(conn: sqlite3.Connection) -> dict:
    checks = []

    def check(name, passed, detail=""):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    # non-empty fact tables
    n_watch = _scalar(conn, "SELECT COUNT(*) FROM fact_watch_event")
    check("fact_watch_event is populated", n_watch > 0, f"rows={n_watch}")
    n_rating = _scalar(conn, "SELECT COUNT(*) FROM fact_rating")
    check("fact_rating is populated", n_rating > 0, f"rows={n_rating}")
    n_titles = _scalar(conn, "SELECT COUNT(*) FROM dim_title")
    check("dim_title is populated", n_titles > 0, f"rows={n_titles}")

    # referential integrity (no orphan facts)
    orphan_watch = _scalar(conn, """
        SELECT COUNT(*) FROM fact_watch_event f
        LEFT JOIN dim_title t ON f.title_id = t.title_id
        WHERE t.title_id IS NULL""")
    check("no orphan watch events -> dim_title", orphan_watch == 0, f"orphans={orphan_watch}")

    orphan_prof = _scalar(conn, """
        SELECT COUNT(*) FROM fact_watch_event f
        LEFT JOIN dim_profile p ON f.profile_id = p.profile_id
        WHERE p.profile_id IS NULL""")
    check("no orphan watch events -> dim_profile", orphan_prof == 0, f"orphans={orphan_prof}")

    orphan_date = _scalar(conn, """
        SELECT COUNT(*) FROM fact_watch_event f
        LEFT JOIN dim_date d ON f.date_id = d.date_id
        WHERE d.date_id IS NULL""")
    check("no orphan watch events -> dim_date", orphan_date == 0, f"orphans={orphan_date}")

    orphan_btg = _scalar(conn, """
        SELECT COUNT(*) FROM bridge_title_genre b
        LEFT JOIN dim_genre g ON b.genre_id = g.genre_id
        WHERE g.genre_id IS NULL""")
    check("no orphan genre bridges", orphan_btg == 0, f"orphans={orphan_btg}")

    # measure ranges
    bad_completion = _scalar(conn,
        "SELECT COUNT(*) FROM fact_watch_event WHERE completion < 0 OR completion > 1")
    check("completion within [0,1]", bad_completion == 0, f"violations={bad_completion}")

    bad_seconds = _scalar(conn, "SELECT COUNT(*) FROM fact_watch_event WHERE watch_seconds < 0")
    check("watch_seconds non-negative", bad_seconds == 0, f"violations={bad_seconds}")

    bad_rating = _scalar(conn, "SELECT COUNT(*) FROM fact_rating WHERE rating < 1 OR rating > 5")
    check("rating within [1,5]", bad_rating == 0, f"violations={bad_rating}")

    # critical-column null checks
    null_titles = _scalar(conn,
        "SELECT COUNT(*) FROM dim_title WHERE title IS NULL OR title = ''")
    check("no null/blank titles", null_titles == 0, f"violations={null_titles}")

    # every title has at least one genre
    no_genre = _scalar(conn, """
        SELECT COUNT(*) FROM dim_title t
        LEFT JOIN bridge_title_genre b ON t.title_id = b.title_id
        WHERE b.title_id IS NULL""")
    check("every title has >= 1 genre", no_genre == 0, f"titles_without_genre={no_genre}")

    # uniqueness
    dupe_titles = _scalar(conn, """
        SELECT COUNT(*) FROM (
            SELECT title, release_year, COUNT(*) c
            FROM dim_title GROUP BY title, release_year HAVING c > 1)""")
    check("no duplicate (title, year)", dupe_titles == 0, f"duplicates={dupe_titles}")

    # episodes
    no_eps = _scalar(conn, """
        SELECT COUNT(*) FROM dim_title t
        WHERE t.content_type = 'series'
          AND NOT EXISTS (SELECT 1 FROM dim_episode e WHERE e.title_id = t.title_id)""")
    check("every series has episodes", no_eps == 0, f"series_without_episodes={no_eps}")

    movie_eps = _scalar(conn, """
        SELECT COUNT(*) FROM dim_episode e JOIN dim_title t ON e.title_id = t.title_id
        WHERE t.content_type = 'movie'""")
    check("no episodes on movies", movie_eps == 0, f"violations={movie_eps}")

    gaps = _scalar(conn, """
        SELECT COUNT(*) FROM (
            SELECT title_id, season_number FROM dim_episode
            GROUP BY title_id, season_number
            HAVING MIN(episode_number) != 1 OR MAX(episode_number) != COUNT(*))""")
    check("episodes numbered 1..n in each season", gaps == 0, f"seasons_with_gaps={gaps}")

    orphan_ep = _scalar(conn, """
        SELECT COUNT(*) FROM fact_watch_event f
        LEFT JOIN dim_episode e ON f.episode_id = e.episode_id
        WHERE f.episode_id IS NOT NULL AND e.episode_id IS NULL""")
    check("no orphan watch events -> dim_episode", orphan_ep == 0, f"orphans={orphan_ep}")

    wrong_kind = _scalar(conn, """
        SELECT COUNT(*) FROM fact_watch_event f JOIN dim_title t ON f.title_id = t.title_id
        WHERE (t.content_type = 'series') != (f.episode_id IS NOT NULL)""")
    check("episode set exactly on series events", wrong_kind == 0, f"violations={wrong_kind}")

    wrong_title = _scalar(conn, """
        SELECT COUNT(*) FROM fact_watch_event f JOIN dim_episode e ON f.episode_id = e.episode_id
        WHERE e.title_id != f.title_id""")
    check("an event's episode belongs to its title", wrong_title == 0, f"violations={wrong_title}")

    passed = sum(1 for c in checks if c["passed"])
    return {"checks": checks, "passed": passed, "total": len(checks),
            "all_passed": passed == len(checks)}
