"""
ETL pipeline orchestrator: extract, transform, load, validate.

Run standalone with: python -m data_engineering.pipeline
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import zlib
from datetime import date, datetime

from paths import DB_PATH, SCHEMA_PATH, LOG_DIR
from data_engineering.catalog_seed import CATALOG
from data_engineering import generate_data
from data_engineering import accounts, data_quality, tmdb

# Stored in the database (PRAGMA user_version). Bump it whenever schema.sql
# changes, and existing warehouses get rebuilt on the next start.
SCHEMA_VERSION = 4

# log to console and file
logger = logging.getLogger("etl")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh = logging.FileHandler(LOG_DIR / "etl.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)


def _decade(year: int) -> str:
    return f"{(year // 10) * 10}s"


# extract
def extract():
    logger.info("[EXTRACT] Reading curated catalog (%d titles)", len(CATALOG))
    users, profiles, affinities = generate_data.generate_population()
    logger.info("[EXTRACT] Generated %d synthetic users / %d profiles", len(users), len(profiles))
    return users, profiles, affinities


# transform
def transform(users, profiles, affinities):
    logger.info("[TRANSFORM] Assigning surrogate keys & deriving attributes")

    # titles + genre normalisation
    titles = []
    genre_set = {}
    bridge = []
    title_index = {}  # catalog position -> title_id
    added_base = date(2018, 1, 1)

    for i, t in enumerate(CATALOG):
        title_id = i + 1
        title_index[i] = title_id
        titles.append({
            "title_id": title_id,
            "title": t["title"],
            "content_type": t["type"],
            "release_year": t["year"],
            "maturity_rating": t["maturity"],
            "runtime_minutes": t["runtime"],
            "description": t["description"].strip(),
            "director": t["director"],
            "cast_list": json.dumps(t["cast"]),
            "keywords": json.dumps(t["keywords"]),
            "country": t["country"],
            "language": t["language"],
            "imdb_score": t["imdb"],
            "added_date": (added_base.replace(year=2018 + (i % 6))).isoformat(),
            # crc32, not hash(): str hashes change between runs, and the colour shouldn't
            "poster_hue": zlib.crc32(t["title"].encode("utf-8")) % 360,
            "decade": _decade(t["year"]),
        })
        for g in t["genres"]:
            if g not in genre_set:
                genre_set[g] = len(genre_set) + 1
            bridge.append((title_id, genre_set[g]))

    genres = [{"genre_id": gid, "genre_name": name} for name, gid in genre_set.items()]

    # episodes, then events (series events point at the episode watched)
    episodes = generate_data.generate_episodes(title_index)
    watch_events, ratings = generate_data.generate_events(profiles, affinities, title_index,
                                                          episodes)
    logger.info("[TRANSFORM] %d episodes, %d watch events, %d ratings",
                len(episodes), len(watch_events), len(ratings))

    # date dimension, built from every event/rating date
    date_keys = set()
    for e in watch_events:
        date_keys.add(e["event_ts"][:10])
    for r in ratings:
        date_keys.add(r["rated_ts"][:10])
    dim_dates = []
    for ds in sorted(date_keys):
        d = datetime.fromisoformat(ds).date()
        date_id = int(d.strftime("%Y%m%d"))
        dim_dates.append({
            "date_id": date_id,
            "cal_date": d.isoformat(),
            "year": d.year, "month": d.month, "day": d.day,
            "weekday": d.strftime("%A"),
            "is_weekend": 1 if d.weekday() >= 5 else 0,
        })

    # attach date_id foreign keys to facts
    for e in watch_events:
        e["date_id"] = int(e["event_ts"][:10].replace("-", ""))
    for r in ratings:
        r["date_id"] = int(r["rated_ts"][:10].replace("-", ""))

    return {
        "titles": titles, "genres": genres, "bridge": bridge, "episodes": episodes,
        "users": users, "profiles": profiles,
        "watch_events": watch_events, "ratings": ratings, "dim_dates": dim_dates,
    }


# load
def load(data: dict, conn: sqlite3.Connection):
    logger.info("[LOAD] Rebuilding schema at %s", DB_PATH)
    cur = conn.cursor()
    # people who signed up in the app aren't generated data: keep them
    kept = accounts.app_people(conn)

    # rebuild analytical tables
    for tbl in ["fact_rating", "fact_watch_event", "bridge_title_genre", "dim_episode",
                "dim_profile", "dim_user", "dim_genre", "dim_date", "dim_title"]:
        cur.execute(f"DROP TABLE IF EXISTS {tbl}")
    # my_list, app_account and app_session are operational data and survive reloads
    conn.commit()

    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)

    cur.executemany(
        """INSERT INTO dim_title (title_id,title,content_type,release_year,maturity_rating,
            runtime_minutes,description,director,cast_list,keywords,country,language,
            imdb_score,added_date,poster_hue,decade)
           VALUES (:title_id,:title,:content_type,:release_year,:maturity_rating,
            :runtime_minutes,:description,:director,:cast_list,:keywords,:country,:language,
            :imdb_score,:added_date,:poster_hue,:decade)""", data["titles"])

    cur.executemany("INSERT INTO dim_genre (genre_id,genre_name) VALUES (:genre_id,:genre_name)",
                    data["genres"])
    cur.executemany("INSERT INTO bridge_title_genre (title_id,genre_id) VALUES (?,?)",
                    data["bridge"])
    cur.executemany(
        """INSERT INTO dim_episode (episode_id,title_id,season_number,episode_number,
            episode_title,runtime_minutes,synopsis)
           VALUES (:episode_id,:title_id,:season_number,:episode_number,
            :episode_title,:runtime_minutes,:synopsis)""", data["episodes"])
    cur.executemany(
        """INSERT INTO dim_user (user_id,full_name,email,country,plan,signup_date,is_synthetic)
           VALUES (:user_id,:full_name,:email,:country,:plan,:signup_date,:is_synthetic)""",
        data["users"])
    cur.executemany(
        """INSERT INTO dim_profile (profile_id,user_id,profile_name,is_kids,avatar_hue,age_group)
           VALUES (:profile_id,:user_id,:profile_name,:is_kids,:avatar_hue,:age_group)""",
        data["profiles"])
    cur.executemany(
        """INSERT INTO dim_date (date_id,cal_date,year,month,day,weekday,is_weekend)
           VALUES (:date_id,:cal_date,:year,:month,:day,:weekday,:is_weekend)""",
        data["dim_dates"])
    cur.executemany(
        """INSERT INTO fact_watch_event (event_id,profile_id,title_id,episode_id,date_id,
            watch_seconds,completion,completed,device,event_ts)
           VALUES (:event_id,:profile_id,:title_id,:episode_id,:date_id,
            :watch_seconds,:completion,:completed,:device,:event_ts)""", data["watch_events"])
    cur.executemany(
        """INSERT INTO fact_rating (rating_id,profile_id,title_id,date_id,rating,rated_ts)
           VALUES (:rating_id,:profile_id,:title_id,:date_id,:rating,:rated_ts)""",
        data["ratings"])

    restored = accounts.restore_app_people(conn, kept)
    if restored:
        logger.info("[LOAD] Kept %d users/profiles created in the app", restored)
    demo = next(u for u in data["users"] if u["email"] == generate_data.DEMO_EMAIL)
    accounts.ensure_login(conn, generate_data.DEMO_EMAIL, generate_data.DEMO_PASSWORD, demo["user_id"])

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    logger.info("[LOAD] Inserted %d titles, %d episodes, %d genres, %d users, %d profiles, "
                "%d watch events, %d ratings",
                len(data["titles"]), len(data["episodes"]), len(data["genres"]),
                len(data["users"]), len(data["profiles"]), len(data["watch_events"]),
                len(data["ratings"]))


# orchestration
def run_pipeline(verbose: bool = True) -> dict:
    t0 = time.time()
    logger.info("=" * 70)
    logger.info("ETL PIPELINE START")
    logger.info("=" * 70)

    users, profiles, affinities = extract()
    data = transform(users, profiles, affinities)

    conn = sqlite3.connect(DB_PATH)
    try:
        load(data, conn)
        named = tmdb.enrich_warehouse(conn)  # no-op until data_engineering.tmdb has run
        if named:
            logger.info("[ENRICH] %d episode names and synopses from the TMDB cache", named)
        logger.info("[VALIDATE] Running data-quality checks")
        report = data_quality.run_all_checks(conn)
        for check in report["checks"]:
            status = "PASS" if check["passed"] else "FAIL"
            logger.info("[VALIDATE]   %-40s %s", check["name"], status)
            if not check["passed"]:
                logger.warning("[VALIDATE]     -> %s", check["detail"])
    finally:
        conn.close()

    elapsed = time.time() - t0
    logger.info("ETL PIPELINE COMPLETE in %.2fs (%d/%d checks passed)",
                elapsed, report["passed"], report["total"])
    return {"elapsed_s": round(elapsed, 2), "dq": report,
            "counts": {k: len(v) for k, v in data.items()}}


def warehouse_ready() -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            return (version == SCHEMA_VERSION
                    and conn.execute("SELECT COUNT(*) FROM dim_title").fetchone()[0] > 0)
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def ensure_warehouse():
    """Build the warehouse unless it's already there and current: first run, a
    deleted data/ folder, or a database from before the latest schema change."""
    if not warehouse_ready():
        run_pipeline()


if __name__ == "__main__":
    run_pipeline()
