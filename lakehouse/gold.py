"""
Gold: the business-level tables, one Parquet file each, built with DuckDB SQL
over silver.

    daily_engagement    active profiles, watch hours, film vs episode plays per day
    title_performance   viewers, views (in film-sized units), hours, completion per title
    genre_weekly        watch hours per genre per week, and its share of that week
    binge_sessions      viewing sessions per profile and series (a new session
                        starts after a 30-minute gap)
    binge_by_series     episodes per session, and how often a session is a binge (3+)
    cohort_retention    profiles grouped by the month they first watched: the share
                        still watching N months later
    device_monthly      watch hours per device per month
"""
from __future__ import annotations

from pathlib import Path

SESSION_GAP_MINUTES = 30
BINGE_EPISODES = 3

TABLES = {
    "daily_engagement": """
        SELECT event_date,
               COUNT(DISTINCT profile_id) AS active_profiles,
               ROUND(SUM(watch_seconds) / 3600.0, 1) AS watch_hours,
               COUNT(*) FILTER (WHERE episode_id IS NULL) AS film_plays,
               COUNT(*) FILTER (WHERE episode_id IS NOT NULL) AS episode_plays
        FROM watch GROUP BY event_date ORDER BY event_date""",

    "title_performance": """
        SELECT title_id, any_value(title) AS title, any_value(content_type) AS content_type,
               COUNT(DISTINCT profile_id) AS viewers,
               ROUND(SUM(film_units), 1) AS views,
               ROUND(SUM(watch_seconds) / 3600.0, 1) AS watch_hours,
               ROUND(AVG(completion), 3) AS avg_completion,
               MAX(event_ts) AS last_watched
        FROM watch GROUP BY title_id ORDER BY views DESC""",

    "genre_weekly": """
        WITH tagged AS (
            SELECT CAST(date_trunc('week', w.event_ts) AS DATE) AS week, g.genre, w.watch_seconds
            FROM watch w JOIN title_genres g ON w.title_id = g.title_id
        )
        SELECT week, genre, ROUND(SUM(watch_seconds) / 3600.0, 1) AS watch_hours,
               ROUND(SUM(watch_seconds) / SUM(SUM(watch_seconds)) OVER (PARTITION BY week), 4) AS share
        FROM tagged GROUP BY week, genre ORDER BY week, watch_hours DESC""",

    # sessionise: a new session starts when an episode begins more than the gap
    # after the previous one ended
    "binge_sessions": f"""
        WITH ordered AS (
            SELECT profile_id, title_id, title, event_ts, watch_seconds,
                   LAG(event_ts + to_seconds(watch_seconds))
                       OVER (PARTITION BY profile_id, title_id ORDER BY event_ts) AS prev_end
            FROM watch WHERE content_type = 'series'
        ),
        numbered AS (
            SELECT *, SUM(CASE WHEN prev_end IS NULL
                                 OR event_ts > prev_end + INTERVAL {SESSION_GAP_MINUTES} MINUTE
                               THEN 1 ELSE 0 END)
                      OVER (PARTITION BY profile_id, title_id ORDER BY event_ts) AS session_no
            FROM ordered
        )
        SELECT profile_id, title_id, any_value(title) AS title, session_no,
               MIN(event_ts) AS started, COUNT(*) AS episodes,
               ROUND(SUM(watch_seconds) / 60.0) AS minutes
        FROM numbered GROUP BY profile_id, title_id, session_no""",

    "binge_by_series": f"""
        SELECT title_id, any_value(title) AS title, COUNT(*) AS sessions,
               ROUND(AVG(episodes), 2) AS episodes_per_session,
               ROUND(AVG(CASE WHEN episodes >= {BINGE_EPISODES} THEN 1 ELSE 0 END), 3) AS binge_rate
        FROM binge_sessions GROUP BY title_id ORDER BY episodes_per_session DESC""",

    "cohort_retention": """
        WITH firsts AS (
            SELECT profile_id, MIN(event_month) AS cohort FROM watch GROUP BY profile_id
        ),
        sizes AS (SELECT cohort, COUNT(*) AS size FROM firsts GROUP BY cohort),
        active AS (SELECT DISTINCT profile_id, event_month FROM watch)
        SELECT f.cohort, date_diff('month', f.cohort, a.event_month) AS month_number,
               COUNT(*) AS active_profiles, ANY_VALUE(s.size) AS cohort_size,
               ROUND(COUNT(*) / ANY_VALUE(s.size), 3) AS retention
        FROM firsts f JOIN active a ON f.profile_id = a.profile_id JOIN sizes s ON f.cohort = s.cohort
        GROUP BY f.cohort, month_number ORDER BY f.cohort, month_number""",

    "device_monthly": """
        SELECT event_month, device, ROUND(SUM(watch_seconds) / 3600.0, 1) AS watch_hours
        FROM watch GROUP BY event_month, device ORDER BY event_month, watch_hours DESC""",
}


def build(con, lake: Path, log=print) -> dict:
    silver, gold = lake / "silver", lake / "gold"
    gold.mkdir(parents=True, exist_ok=True)
    con.execute(f"CREATE OR REPLACE VIEW watch AS SELECT * FROM "
                f"read_parquet('{silver.as_posix()}/watch_events/*/*.parquet', hive_partitioning = true)")
    con.execute(f"CREATE OR REPLACE VIEW title_genres AS SELECT * FROM "
                f"read_parquet('{silver.as_posix()}/title_genres.parquet')")
    rows = {}
    for name, sql in TABLES.items():  # in order: binge_by_series reads binge_sessions
        path = gold / f"{name}.parquet"
        con.execute(f"COPY ({sql}) TO '{path.as_posix()}' (FORMAT parquet)")
        con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path.as_posix()}')")
        rows[name] = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    log(f"  gold: {len(rows)} tables ({', '.join(rows)})")
    return rows
