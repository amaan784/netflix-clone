"""
Bronze: events landed as Parquet exactly as they arrived.

Two sources. The warehouse's generated history is copied in once, and again
only if it changes. Live events come straight off the event stream through the
lake's own consumer group (lake-sink), so the warehouse and the lake read the
same topics independently. Stream records are kept raw (the JSON payload plus
topic/partition/offset) and only parsed in silver.

    bronze/history/watch_events.parquet, ratings.parquet
    bronze/stream/<topic>/ingest_date=YYYY-MM-DD/batch-000001.parquet
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from paths import DB_PATH

HISTORY_SQL = {
    "watch_events": """SELECT event_id, profile_id, title_id, episode_id, watch_seconds,
                              completion, device, event_ts
                       FROM fact_watch_event WHERE origin = 'generated'""",
    "ratings": """SELECT rating_id, profile_id, title_id, rating, rated_ts
                  FROM fact_rating WHERE origin = 'generated'""",
}


def _history_fingerprint(conn) -> list:
    """Cheap summary of the generated history; if it changes, bronze copies it again."""
    return [list(conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(watch_seconds), 0), MAX(event_ts) "
        "FROM fact_watch_event WHERE origin = 'generated'").fetchone()),
            list(conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(rating), 0), MAX(rated_ts) "
        "FROM fact_rating WHERE origin = 'generated'").fetchone())]


def _write(con, df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    con.register("_bronze_df", df)
    try:
        con.execute(f"COPY _bronze_df TO '{tmp.as_posix()}' (FORMAT parquet)")
    finally:
        con.unregister("_bronze_df")
    tmp.replace(path)  # readers never see a half-written file


def land_history(con, lake: Path, state: dict, db_path=DB_PATH, log=print) -> int:
    conn = sqlite3.connect(db_path)
    try:
        fingerprint = _history_fingerprint(conn)
        if state.get("history_fingerprint") == fingerprint and (lake / "bronze/history").exists():
            return 0
        written = 0
        for name, sql in HISTORY_SQL.items():
            df = pd.read_sql_query(sql, conn)
            _write(con, df, lake / "bronze" / "history" / f"{name}.parquet")
            written += len(df)
    finally:
        conn.close()
    state["history_fingerprint"] = fingerprint
    log(f"  bronze: copied {written:,} rows of generated history from the warehouse")
    return written


def drain_stream(con, lake: Path, consumer, state: dict, log=print) -> dict:
    """Read everything the lake's consumer group hasn't seen yet and land it as
    one batch file per topic. Offsets are committed only after the files are
    written, so a crash means re-reading a batch (silver drops the duplicates),
    never losing one."""
    if consumer is None:
        return {}
    records, empty_polls = [], 0
    while empty_polls < 3:  # Kafka can return nothing while it assigns partitions
        batch = consumer.poll(500)
        empty_polls = 0 if batch else empty_polls + 1
        records += batch
    if not records:
        return {}

    state["batch"] = state.get("batch", 0) + 1
    now = datetime.now()
    counts = {}
    for topic in sorted({r["topic"] for r in records}):
        rows = [{"topic": topic, "partition": r["partition"], "offset": r["offset"],
                 "key": r.get("key"), "produced_at": r.get("ts"),
                 "payload": json.dumps(r["value"]), "ingested_at": now.isoformat(timespec="seconds")}
                for r in records if r["topic"] == topic]
        path = (lake / "bronze" / "stream" / topic / f"ingest_date={now.date().isoformat()}"
                / f"batch-{state['batch']:06d}.parquet")
        _write(con, pd.DataFrame(rows), path)
        counts[topic] = len(rows)
    consumer.commit()
    log(f"  bronze: landed {', '.join(f'{n} {t}' for t, n in counts.items())} from the stream")
    return counts
