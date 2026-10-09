"""
Runs the lakehouse: bronze -> silver -> gold, all Parquet under data/lake/,
queried with DuckDB. The app runs it on startup and from the Analytics page;
it can also run on its own:

    python -m lakehouse.build

Every run is incremental at the bronze end (only new stream records are read)
and rebuilds silver and gold from bronze, which takes a second or two at this size.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import duckdb

from paths import DB_PATH, LAKE_DIR
from lakehouse import bronze, gold, silver

_lock = threading.Lock()  # one run at a time; dashboard reads wait for it too

LAYERS = {
    "bronze": ["history/watch_events.parquet", "history/ratings.parquet",
               "stream/watch-events/*/*.parquet", "stream/ratings/*/*.parquet"],
    "silver": ["watch_events/*/*.parquet", "ratings.parquet"],
    "gold": [f"{name}.parquet" for name in gold.TABLES],
}


def _state_path(lake: Path) -> Path:
    return lake / "_state.json"


def load_state(lake: Path) -> dict:
    path = _state_path(lake)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def layer_stats(con, lake: Path) -> dict:
    out = {}
    for layer, patterns in LAYERS.items():
        files = [f for p in patterns for f in (lake / layer).glob(p)]
        rows = sum(silver.count_rows(con, lake, f"{layer}/{p}") for p in patterns)
        out[layer] = {"files": len(files), "rows": rows,
                      "bytes": sum(f.stat().st_size for f in files)}
    return out


def run(consumer=None, root=None, db_path=DB_PATH, log=print) -> dict:
    """One lakehouse run. `consumer` is a stream consumer in the lake-sink group;
    without one, bronze only refreshes the warehouse history."""
    lake = Path(root or LAKE_DIR)
    lake.mkdir(parents=True, exist_ok=True)
    with _lock:
        started, t0 = datetime.now(), time.time()
        state = load_state(lake)
        con = duckdb.connect()
        try:
            timings = {}
            t = time.time()
            history = bronze.land_history(con, lake, state, db_path, log)
            streamed = bronze.drain_stream(con, lake, consumer, state, log)
            timings["bronze"] = round(time.time() - t, 2)
            t = time.time()
            cleaned = silver.build(con, lake, db_path, log)
            timings["silver"] = round(time.time() - t, 2)
            t = time.time()
            gold.build(con, lake, log)
            timings["gold"] = round(time.time() - t, 2)
            summary = {
                "ran_at": started.isoformat(timespec="seconds"),
                "seconds": round(time.time() - t0, 2),
                "timings": timings,
                "history_rows_copied": history,
                "stream_records_landed": streamed,
                "quarantined": cleaned["quarantined"],
                "layers": layer_stats(con, lake),
            }
        finally:
            con.close()
        state["last_run"] = summary
        _state_path(lake).write_text(json.dumps(state, indent=2), encoding="utf-8")
    log(f"lakehouse run done in {summary['seconds']}s")
    return summary


def _query(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def dashboard(root=None) -> dict:
    """What the Analytics page shows: the last run, plus a few gold tables."""
    lake = Path(root or LAKE_DIR)
    with _lock:
        state = load_state(lake)
        if "last_run" not in state:
            return {"last_run": None}
        g = (lake / "gold").as_posix()
        s = (lake / "silver").as_posix()
        con = duckdb.connect()
        try:
            out = {
                "last_run": state["last_run"],
                "binge_by_series": _query(con, f"""
                    SELECT title, episodes_per_session, binge_rate, sessions
                    FROM read_parquet('{g}/binge_by_series.parquet')
                    WHERE sessions >= 20 ORDER BY episodes_per_session DESC LIMIT 8"""),
                # views count watch time in film-sized units, so shows don't win on hours alone
                "top_views": _query(con, f"""
                    SELECT title, content_type, views, viewers, watch_hours
                    FROM read_parquet('{g}/title_performance.parquet')
                    ORDER BY views DESC LIMIT 8"""),
                "cohort_retention": _query(con, f"""
                    SELECT strftime(cohort, '%Y-%m') AS cohort, month_number, retention, cohort_size
                    FROM read_parquet('{g}/cohort_retention.parquet')
                    WHERE cohort IN (SELECT DISTINCT cohort FROM read_parquet('{g}/cohort_retention.parquet')
                                     ORDER BY cohort LIMIT 6)
                      AND month_number <= 6
                    ORDER BY cohort, month_number"""),
                "quarantine": _query(con, f"""
                    SELECT source, problem, COUNT(*) AS n FROM read_parquet('{s}/quarantine.parquet')
                    GROUP BY source, problem ORDER BY n DESC"""),
            }
        finally:
            con.close()
    return out


def main():
    import logging
    from streaming.backends import get_stream_backend
    from streaming.topics import LAKE_GROUP, RATINGS, WATCH_EVENTS
    from data_engineering.pipeline import ensure_warehouse

    logging.basicConfig(level=logging.WARNING)
    ensure_warehouse()
    consumer = get_stream_backend().make_consumer(LAKE_GROUP, [WATCH_EVENTS, RATINGS])
    summary = run(consumer)
    for layer, st in summary["layers"].items():
        print(f"  {layer:<7} {st['rows']:>8,} rows  {st['files']:>3} files  {st['bytes'] / 1e6:6.2f} MB")


if __name__ == "__main__":
    main()
