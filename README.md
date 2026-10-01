# NetClone v6: Event streaming

Watch and rating actions no longer write to the database directly. The API
publishes them to an append-only log, and a background consumer writes them to
the warehouse, updates the recommender and keeps a live trending count.

The log is a small in-process broker modelled on Kafka: topics split into
partitions, records keyed by profile, offsets committed per consumer group,
and each partition saved to a `.log` file so it survives restarts.

## New in this stage (`streaming/`)

- `broker.py` - the log itself.
- `producer.py`, `consumer.py` - publish; poll and commit.
- `topics.py` - `watch-events` (3 partitions) and `ratings` (2).
- `pipeline.py` - the consumer loop, the warehouse sink and live trending.
- `backends.py` - gives the app a producer and consumers.

The Analytics page gets a streaming panel (message counts, consumer lag,
throughput, live trending), backed by `/api/streaming/stats` and
`/api/streaming/trending`.

## Run

```powershell
pip install -r requirements.txt
python run.py
```

To watch the broker on its own: `python -m streaming.pipeline`.
