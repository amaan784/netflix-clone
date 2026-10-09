"""
Silver: bronze cleaned up. Stream payloads are parsed, history and stream are
unioned, duplicates dropped, and every row checked against the dimensions.
Rows that fail a check go to a quarantine table with the reason instead of
disappearing. Good rows get the dimension columns joined on, so gold queries
don't need joins.

    silver/watch_events/event_month=YYYY-MM-01/*.parquet   (partitioned by month)
    silver/ratings.parquet, silver/quarantine.parquet
    silver/titles.parquet, profiles.parquet, episodes.parquet, title_genres.parquet
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from paths import DB_PATH
from ai.data_access import EPISODES_PER_FILM

DIMENSIONS = {
    "titles": "SELECT title_id, title, content_type, release_year, maturity_rating FROM dim_title",
    "profiles": """SELECT p.profile_id, p.user_id, p.is_kids, u.country, u.is_synthetic
                   FROM dim_profile p JOIN dim_user u ON p.user_id = u.user_id""",
    "episodes": "SELECT episode_id, title_id, season_number, episode_number FROM dim_episode",
    "title_genres": """SELECT b.title_id, g.genre_name AS genre
                       FROM bridge_title_genre b JOIN dim_genre g ON b.genre_id = g.genre_id""",
}


def _files(lake: Path, pattern: str) -> str | None:
    """A read_parquet() glob, or None when nothing has landed there yet."""
    return f"{lake.as_posix()}/{pattern}" if any(lake.glob(pattern)) else None


def count_rows(con, lake: Path, pattern: str) -> int:
    files = _files(lake, pattern)
    return con.execute(f"SELECT COUNT(*) FROM read_parquet('{files}')").fetchone()[0] if files else 0


def _copy(con, sql: str, path: Path, partition_by: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    opts = f"FORMAT parquet, PARTITION_BY ({partition_by}), OVERWRITE" if partition_by else "FORMAT parquet"
    con.execute(f"COPY ({sql}) TO '{path.as_posix()}' ({opts})")


def snapshot_dimensions(con, lake: Path, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        for name, sql in DIMENSIONS.items():
            con.register(f"_{name}", pd.read_sql_query(sql, conn))
            _copy(con, f"SELECT * FROM _{name}", lake / "silver" / f"{name}.parquet")
            con.unregister(f"_{name}")
            con.execute(f"CREATE OR REPLACE VIEW {name} AS "
                        f"SELECT * FROM read_parquet('{(lake / 'silver' / name).as_posix()}.parquet')")
    finally:
        conn.close()


def _raw_watch_events(lake: Path) -> str:
    parts = []
    history = _files(lake, "bronze/history/watch_events.parquet")
    if history:
        parts.append(f"""
            SELECT 'history:' || event_id AS event_key, profile_id, title_id,
                   CAST(episode_id AS INTEGER) AS episode_id, watch_seconds, completion, device,
                   CAST(event_ts AS TIMESTAMP) AS event_ts, 'generated' AS origin
            FROM read_parquet('{history}')""")
    stream = _files(lake, "bronze/stream/watch-events/*/*.parquet")
    if stream:
        # TRY_CAST: a malformed payload becomes NULLs, which the checks below catch
        parts.append(f"""
            SELECT 'stream:' || "partition" || ':' || "offset" AS event_key,
                   TRY_CAST(payload->>'profile_id' AS INTEGER), TRY_CAST(payload->>'title_id' AS INTEGER),
                   TRY_CAST(payload->>'episode_id' AS INTEGER), TRY_CAST(payload->>'watch_seconds' AS INTEGER),
                   TRY_CAST(payload->>'completion' AS DOUBLE), payload->>'device',
                   TRY_CAST(produced_at AS TIMESTAMP), 'app'
            FROM read_parquet('{stream}', hive_partitioning = true)""")
    if not parts:
        return """SELECT NULL::VARCHAR AS event_key, NULL::INTEGER AS profile_id, NULL::INTEGER AS title_id,
                  NULL::INTEGER AS episode_id, NULL::INTEGER AS watch_seconds, NULL::DOUBLE AS completion,
                  NULL::VARCHAR AS device, NULL::TIMESTAMP AS event_ts, NULL::VARCHAR AS origin WHERE false"""
    return " UNION ALL ".join(parts)


def _raw_ratings(lake: Path) -> str:
    parts = []
    history = _files(lake, "bronze/history/ratings.parquet")
    if history:
        parts.append(f"""
            SELECT 'history:' || rating_id AS rating_key, profile_id, title_id, rating,
                   CAST(rated_ts AS TIMESTAMP) AS rated_ts, 'generated' AS origin
            FROM read_parquet('{history}')""")
    stream = _files(lake, "bronze/stream/ratings/*/*.parquet")
    if stream:
        parts.append(f"""
            SELECT 'stream:' || "partition" || ':' || "offset",
                   TRY_CAST(payload->>'profile_id' AS INTEGER), TRY_CAST(payload->>'title_id' AS INTEGER),
                   TRY_CAST(payload->>'rating' AS INTEGER), TRY_CAST(produced_at AS TIMESTAMP), 'app'
            FROM read_parquet('{stream}', hive_partitioning = true)""")
    if not parts:
        return """SELECT NULL::VARCHAR AS rating_key, NULL::INTEGER AS profile_id, NULL::INTEGER AS title_id,
                  NULL::INTEGER AS rating, NULL::TIMESTAMP AS rated_ts, NULL::VARCHAR AS origin WHERE false"""
    return " UNION ALL ".join(parts)


def build(con, lake: Path, db_path=DB_PATH, log=print) -> dict:
    snapshot_dimensions(con, lake, db_path)

    # watch events: dedupe (a re-read stream batch lands twice in bronze), then check
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE watch_checked AS
        WITH raw AS ({_raw_watch_events(lake)}),
        deduped AS (
            SELECT * FROM raw
            QUALIFY ROW_NUMBER() OVER (PARTITION BY event_key ORDER BY event_ts) = 1
        )
        SELECT d.*, t.title, t.content_type, p.user_id, p.country, p.is_kids,
               e.season_number, e.episode_number,
               CASE
                   WHEN p.profile_id IS NULL THEN 'unknown profile'
                   WHEN t.title_id IS NULL THEN 'unknown title'
                   WHEN t.content_type = 'series' AND d.episode_id IS NULL THEN 'series event without an episode'
                   WHEN d.episode_id IS NOT NULL AND e.episode_id IS NULL THEN 'unknown episode'
                   WHEN d.episode_id IS NOT NULL AND e.title_id != d.title_id THEN 'episode of another title'
                   WHEN d.completion IS NULL OR d.completion NOT BETWEEN 0 AND 1 THEN 'completion out of range'
                   WHEN d.watch_seconds IS NULL OR d.watch_seconds < 0 THEN 'bad watch time'
                   WHEN d.event_ts IS NULL THEN 'no timestamp'
               END AS problem
        FROM deduped d
        LEFT JOIN titles t ON d.title_id = t.title_id
        LEFT JOIN profiles p ON d.profile_id = p.profile_id
        LEFT JOIN episodes e ON d.episode_id = e.episode_id""")

    _copy(con, f"""
        SELECT event_key, profile_id, user_id, country, is_kids, title_id, title, content_type,
               episode_id, season_number, episode_number, event_ts, CAST(event_ts AS DATE) AS event_date,
               CAST(date_trunc('month', event_ts) AS DATE) AS event_month,
               watch_seconds, completion, completion >= 0.9 AS completed,
               CASE WHEN episode_id IS NULL THEN completion ELSE completion / {EPISODES_PER_FILM} END
                   AS film_units,
               device, origin
        FROM watch_checked WHERE problem IS NULL""",
          lake / "silver" / "watch_events", partition_by="event_month")

    # ratings: dedupe, keep each profile's latest rating of a title, check
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE ratings_checked AS
        WITH raw AS ({_raw_ratings(lake)}),
        deduped AS (
            SELECT * FROM raw
            QUALIFY ROW_NUMBER() OVER (PARTITION BY rating_key ORDER BY rated_ts) = 1
        )
        SELECT d.*,
               CASE
                   WHEN p.profile_id IS NULL THEN 'unknown profile'
                   WHEN t.title_id IS NULL THEN 'unknown title'
                   WHEN d.rating IS NULL OR d.rating NOT BETWEEN 1 AND 5 THEN 'rating out of range'
                   WHEN d.rated_ts IS NULL THEN 'no timestamp'
               END AS problem
        FROM deduped d
        LEFT JOIN titles t ON d.title_id = t.title_id
        LEFT JOIN profiles p ON d.profile_id = p.profile_id""")
    _copy(con, """
        SELECT * EXCLUDE (problem) FROM ratings_checked WHERE problem IS NULL
        QUALIFY ROW_NUMBER() OVER (PARTITION BY profile_id, title_id ORDER BY rated_ts DESC) = 1""",
          lake / "silver" / "ratings.parquet")

    _copy(con, """
        SELECT 'watch_events' AS source, event_key AS record_key, problem,
               CAST(profile_id AS VARCHAR) AS profile_id, CAST(title_id AS VARCHAR) AS title_id, origin
        FROM watch_checked WHERE problem IS NOT NULL
        UNION ALL
        SELECT 'ratings', rating_key, problem, CAST(profile_id AS VARCHAR), CAST(title_id AS VARCHAR), origin
        FROM ratings_checked WHERE problem IS NOT NULL""",
          lake / "silver" / "quarantine.parquet")

    counts = {"watch_events": count_rows(con, lake, "silver/watch_events/*/*.parquet"),
              "ratings": count_rows(con, lake, "silver/ratings.parquet"),
              "quarantined": count_rows(con, lake, "silver/quarantine.parquet")}
    log(f"  silver: {counts['watch_events']:,} watch events, {counts['ratings']:,} ratings, "
        f"{counts['quarantined']} quarantined")
    return counts
