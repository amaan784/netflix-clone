"""Lakehouse: bronze -> silver -> gold on a scratch lake, fed by a private log broker."""
import sqlite3

import duckdb
import pytest

from paths import DB_PATH
from backend import episodes, main
from lakehouse import build
from streaming.broker import LogBroker
from streaming.consumer import Consumer
from streaming.producer import Producer
from streaming.topics import LAKE_GROUP, RATINGS, WATCH_EVENTS

QUIET = {"log": lambda *a: None}


@pytest.fixture
def stream(tmp_path):
    broker = LogBroker(tmp_path / "stream")
    return Producer(broker), Consumer(broker, LAKE_GROUP, [WATCH_EVENTS, RATINGS])


def _ids():
    titles = main.svc().all_titles()
    film = next(t["title_id"] for t in titles if t["content_type"] == "movie")
    series = next(t["title_id"] for t in titles if t["content_type"] == "series")
    return film, series, episodes.for_title(series)[0]["episode_id"]


def _generated_counts():
    conn = sqlite3.connect(DB_PATH)
    try:
        return [conn.execute(f"SELECT COUNT(*) FROM {t} WHERE origin = 'generated'").fetchone()[0]
                for t in ("fact_watch_event", "fact_rating")]
    finally:
        conn.close()


def _sql(lake, query):
    return duckdb.sql(query.replace("LAKE", lake.as_posix())).fetchall()


def test_events_flow_through_every_layer(tmp_path, stream):
    producer, consumer = stream
    film, series, episode = _ids()
    good = [{"profile_id": 1, "title_id": film, "episode_id": None, "watch_seconds": 600,
             "completion": 0.5, "device": "Web"},
            {"profile_id": 1, "title_id": series, "episode_id": episode, "watch_seconds": 3000,
             "completion": 1.0, "device": "TV"}]
    bad = [{"profile_id": 1, "title_id": series, "watch_seconds": 60, "completion": 0.1},  # no episode
           {"profile_id": 1, "title_id": film, "watch_seconds": 60, "completion": 7.0}]   # impossible
    for value in good + bad:
        producer.send(WATCH_EVENTS, value, key=1)
    producer.send(RATINGS, {"profile_id": 1, "title_id": film, "rating": 5}, key=1)

    lake = tmp_path / "lake"
    summary = build.run(consumer, root=lake, **QUIET)
    n_watch, n_ratings = _generated_counts()

    assert summary["history_rows_copied"] == n_watch + n_ratings
    assert summary["stream_records_landed"] == {"watch-events": 4, "ratings": 1}
    assert summary["layers"]["silver"]["rows"] == n_watch + 2 + n_ratings + 1
    problems = sorted(r[0] for r in _sql(lake, "SELECT problem FROM read_parquet('LAKE/silver/quarantine.parquet')"))
    assert problems == ["completion out of range", "series event without an episode"]

    # gold agrees with silver
    silver_views, gold_views = _sql(lake, """
        SELECT (SELECT ROUND(SUM(film_units), 1) FROM read_parquet('LAKE/silver/watch_events/*/*.parquet')),
               (SELECT ROUND(SUM(views), 1) FROM read_parquet('LAKE/gold/title_performance.parquet'))""")[0]
    assert silver_views == pytest.approx(gold_views, abs=1.0)
    first_month = _sql(lake, "SELECT DISTINCT retention FROM read_parquet('LAKE/gold/cohort_retention.parquet') "
                             "WHERE month_number = 0")
    assert first_month == [(1.0,)]

    # a second run has nothing new: no history copy, no stream batch
    again = build.run(consumer, root=lake, **QUIET)
    assert again["history_rows_copied"] == 0 and again["stream_records_landed"] == {}


def test_redelivered_batch_is_deduplicated(tmp_path, stream, monkeypatch):
    producer, consumer = stream
    film, _, _ = _ids()
    producer.send(WATCH_EVENTS, {"profile_id": 2, "title_id": film, "watch_seconds": 120,
                                 "completion": 0.2, "device": "Mobile"}, key=2)
    lake = tmp_path / "lake"
    # first run lands the batch but "crashes" before committing the offset...
    monkeypatch.setattr(consumer, "commit", lambda: None)
    first = build.run(consumer, root=lake, **QUIET)
    monkeypatch.undo()
    # ...so a fresh consumer in the same group reads it again
    second = build.run(Consumer(consumer.broker, LAKE_GROUP, [WATCH_EVENTS, RATINGS]), root=lake, **QUIET)

    assert second["stream_records_landed"] == {"watch-events": 1}
    assert second["layers"]["bronze"]["rows"] == first["layers"]["bronze"]["rows"] + 1  # twice in bronze
    assert second["layers"]["silver"]["rows"] == first["layers"]["silver"]["rows"]      # once in silver


def test_dashboard_reads_gold(tmp_path, stream):
    _, consumer = stream
    lake = tmp_path / "lake"
    assert build.dashboard(lake) == {"last_run": None}
    build.run(consumer, root=lake, **QUIET)
    d = build.dashboard(lake)
    assert d["binge_by_series"] and d["top_views"] and d["cohort_retention"]
    assert all(row["episodes_per_session"] >= 1 for row in d["binge_by_series"])
