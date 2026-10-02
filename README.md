# NetClone v7: Kafka backend

Adds a real Apache Kafka backend as an alternative to the in-process log. The
app code doesn't change: `backends.py` uses Kafka when `KAFKA_BOOTSTRAP`
points at a reachable broker and falls back to the log broker otherwise, so
the app always starts.

## New in this stage

- `streaming/kafka_backend.py` - producer, consumer and stats on kafka-python,
  with the same interface as the log broker. Creates the topics if needed.
- `streaming/backends.py` - picks the backend.
- `docker-compose.yml` - single-node Kafka (KRaft, no Zookeeper) on
  localhost:9092.
- `run.py --kafka` - sets `KAFKA_BOOTSTRAP=localhost:9092`.

## Run

Without Kafka, same as before:

```powershell
pip install -r requirements.txt
python run.py
```

With Kafka (needs Docker):

```powershell
docker compose up -d
python run.py --kafka
```

The Analytics page shows which backend is in use.
