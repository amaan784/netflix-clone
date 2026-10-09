# NetClone

A local Netflix clone that runs entirely offline. There is no cloud, no
external API, and no internet requirement. It includes a data-engineering
pipeline (ETL into a star-schema warehouse), an event-streaming pipeline
(Kafka-style), an AI/ML layer (hybrid recommender, semantic search, and a
natural-language assistant), and adaptive HLS video streaming, served behind a
Netflix-style web app.

Stack: FastAPI, uvicorn, pandas, numpy, scikit-learn, SQLite, ffmpeg, hls.js.

## Documentation

A full architecture write-up with diagrams (system layers, star schema, the
hybrid recommender, and the streaming flow) is in
[ARCHITECTURE.html](ARCHITECTURE.html). Open the file directly in a browser, or
visit `http://127.0.0.1:8000/architecture.html` while the app is running (also
linked as "Docs" in the app navbar).

## Quick start

```powershell
# optional: install dependencies
pip install -r requirements.txt

# builds the data warehouse on first launch, then starts the server
python run.py
```

Open http://127.0.0.1:8000 and pick a profile.

To play real video, fetch and convert the films once (about 645 MB of downloads,
see [Video streaming](#video-streaming-media)):

```powershell
python -m media.ingest
```

For real poster art, add a free TMDB key (see [Artwork](#artwork-tmdb)):

```powershell
copy .env.example .env      # then paste your key into .env
python -m data_engineering.tmdb
```

Other commands:

```powershell
python run.py --rebuild   # force-rebuild the warehouse, then serve
python run.py --etl       # run the ETL pipeline only (no web server)
python -m data_engineering.pipeline   # same ETL, standalone
python -m lakehouse.build             # run the lakehouse (bronze -> silver -> gold)
```

Everything runs on localhost.

## Data engineering (`data_engineering/`)

A dimensional model built by a logged, idempotent ETL.

| Stage | What happens |
|-------|--------------|
| Extract | Curated catalog (`catalog_seed.py`, 60+ titles) plus a generated population of ~280 viewers with latent genre preferences who produce ~11k watch events and ~4k ratings (`generate_data.py`). |
| Transform | Surrogate keys, derived attributes (decade, weekend flag), genre normalisation into a bridge table, and a calendar date dimension. |
| Load | Bulk insert into the SQLite star schema (`schema.sql`). |
| Validate | 13 data-quality checks: referential integrity, value ranges, null/blank, uniqueness, and "every title has a genre" (`data_quality.py`). The run fails if any check fails. |

Star schema:

- Dimensions: `dim_title`, `dim_genre`, `dim_user`, `dim_profile`, `dim_date`
- Bridge: `bridge_title_genre` (title to genre many-to-many)
- Facts: `fact_watch_event` (additive: `watch_seconds`, `completion`), `fact_rating`
- Operational: `my_list`

Analytics (`analytics.py`) provides the aggregations behind the in-app
dashboard: top titles by watch hours, watch hours and completion by genre, by
device, daily active profiles over the date dimension, rating distribution, and
movie/series split.

The app writes live user actions (`backend/store.py`) back into the same
warehouse, so a pipeline refresh or model retrain picks up real usage.

## Lakehouse (`lakehouse/`)

Beside the SQLite warehouse sits a lakehouse: Parquet files in three layers,
built and queried with DuckDB.

| Layer | What's in it |
|-------|--------------|
| Bronze | Raw events as they arrived. The warehouse's generated history is copied once (and again only if it changes); live events come straight off the event stream through the lake's own consumer group (`lake-sink`), kept raw (JSON payload plus topic, partition and offset), one batch file per run, partitioned by ingest date. |
| Silver | Bronze parsed, history and stream unioned, duplicates dropped (a re-delivered batch lands twice in bronze but once here), and every row checked against the dimensions. Rows that fail go to a quarantine table with the reason (for example `series event without an episode`). Good rows get the dimension columns joined on; watch events are partitioned by month. |
| Gold | Business tables built with SQL: daily engagement, title performance (views in film-sized units), weekly genre share, binge sessions (sessionised with window functions: a new session after a 30-minute gap), episodes per session by series, retention by first-watch month, and device mix. |

The app runs the lakehouse on startup and from the Analytics page ("Run
pipeline"); `python -m lakehouse.build` runs it from the command line. The
Analytics page shows each layer's rows, files and size, the most-binged series,
the most-viewed titles, a retention heatmap and the quarantine. Because bronze
keeps every streamed event, the lake still has live activity after the
warehouse has been rebuilt.

## AI / ML (`ai/`)

A recommendation service (`recommender.py`) that trains on startup and exposes
the rails the home page uses.

1. Content-based filtering (`content_based.py`). TF-IDF over each title's
   metadata blob (genres x3, keywords x2, cast, director, description, decade),
   cosine similarity for "More Like This" and a per-profile taste vector.

2. Collaborative filtering (`collaborative.py`). From the implicit-feedback
   interaction matrix (profiles x titles, valued by an engineered affinity
   score):
   - Matrix factorization (`TruncatedSVD`) for latent taste factors and
     predicted affinity.
   - Item-item CF for "people who watched A also watched B" (reacts to a
     profile's current watch set with no retrain).

3. Hybrid ranker (`recommend_for_profile`):

   ```
   final = 0.40*content + 0.30*item-item + 0.20*matrix-factorization + 0.10*popularity
   ```

   with min-max normalisation per signal, cold-start fallback to trending, and
   kids/maturity filtering per profile.

4. Semantic search (`semantic_search.py`). TF-IDF vector space plus a light NL
   parser that extracts structured intent (decade, movie/series, genre, mood
   synonyms such as scary to Horror) and applies it as a soft boost. Handles
   queries like "scary supernatural shows set in a small town".

5. Assistant (`assistant.py`). A rule-based NL assistant that classifies intent
   (recommend / similar / info / mood / search) and routes to the recommender
   or search engine, then composes a reply citing real titles.

Watching a title or rating it (`POST /api/watch`, `/api/rate`) updates the
in-memory profile state immediately, so "Top Picks" and "Because you watched"
refresh without a full retrain. `POST /api/admin/refresh` retrains everything
from the warehouse.

Top Picks are diversity re-ranked with MMR (maximal marginal relevance) so the
list is not dominated by a single genre. The home page also has a Continue
Watching rail (started-but-unfinished titles, with a resume bar) and a
"Top 10 in your country" rail.

### Offline evaluation (`ai/evaluation.py`)

There is an offline eval for the recommender. A leakage-free per-profile
holdout retrains every model on the train split only, then ranks the held-out
items and scores each model with Precision@K, Recall@K, NDCG@K, MAP@K,
hit-rate and catalog coverage. Popularity, content-based, item-item, SVD, ALS
and the hybrid are compared on the same split; the hybrid wins on NDCG@10. A
grid search can tune the blend weights.

```powershell
python -m ai.evaluation            # comparison table at K=10
python -m ai.evaluation --grid     # also tune the hybrid weights
```

Also exposed at `GET /api/eval` (add `?grid=true` for the weight search).

## Event streaming (`streaming/`)

Watch and rating actions are not written to the database directly. The API
publishes them to an append-only log, and a background consumer reads the log,
maintains a time-windowed live trending tally, and sinks each event into the
warehouse. This is a local, pure-Python stand-in for Kafka (no broker to
install) that models the same concepts:

- `broker.py` - topics split into partitions; each partition is an ordered log
  persisted to a `.log` file so it survives restarts and can be replayed.
  Records are keyed (by profile) so one viewer's events stay ordered in a
  partition. Consumer groups commit offsets independently.
- `producer.py` - publishes keyed records to a topic.
- `consumer.py` - reads from assigned partitions and commits offsets per group.
- `pipeline.py` - the consumer app: sinks events to the warehouse, updates the
  live recommender state, and tracks real-time trending.

Topics: `watch-events` (3 partitions), `ratings` (2 partitions). The Analytics
page shows topic/partition message counts, consumer lag, throughput, and live
trending. `GET /api/streaming/stats` and `/api/streaming/trending` expose this.

Run the streaming layer on its own (produces sample events into a throwaway
broker and consumes them):

```powershell
python -m streaming.pipeline
```

### Using the real Apache Kafka broker

The same pipeline can run against a real Kafka broker instead of the in-process
log. A single-node Kafka (KRaft mode, no Zookeeper) is provided via Docker, and
runs entirely on localhost.

```powershell
docker compose up -d        # start Kafka on localhost:9092
python run.py --kafka       # run the app against it (sets KAFKA_BOOTSTRAP)
docker compose down         # stop Kafka when done
```

`--kafka` sets `KAFKA_BOOTSTRAP=localhost:9092`; you can also set that variable
yourself. On startup the app probes the broker: if it is reachable it uses
Kafka (creating the topics if needed), otherwise it falls back to the
in-process log broker, so the app always runs. The backend in use is shown on
the Analytics page and in `GET /api/streaming/stats`.

`backends.py` selects the backend and exposes a common interface (producer,
consumer, stats); `kafka_backend.py` is the kafka-python implementation. The
consumer, sink, and trending logic are identical for both.

## Video streaming (`media/`)

Playback is adaptive HLS, the same approach real streaming services use. Each
film is cut into 4-second segments at up to three qualities (240p, 360p, 720p),
and the player switches between them as bandwidth changes. You can also pick a
quality by hand.

The catalog titles are copyrighted, so every title plays one of three
openly licensed Blender films instead, and the player credits whichever one is
playing:

| Film | Source file (download.blender.org) | Size | License |
|------|------------------------------------|------|---------|
| Big Buck Bunny (2008) | `BigBuckBunny_640x360.m4v.zip` | 121 MB | CC BY 3.0 |
| Elephants Dream (2006) | `elephantsdream-720-h264-st-aac.mov` | 152 MB | CC BY 2.5 |
| Tears of Steel (2012) | `tears_of_steel_720p.mov` | 372 MB | CC BY 3.0 |

```powershell
python -m media.ingest                  # download, transcode, delete the originals
python -m media.ingest --placeholder    # no download: generated test-pattern clips
python -m media.ingest --only tears_of_steel --keep-source
```

- `media/library.py` - the film list, the quality ladder, and which film a
  title plays.
- `media/ingest.py` - downloads each film, then has ffmpeg encode every quality
  in one pass with a keyframe at each segment boundary, so switching quality
  never glitches. Writes `data/media/hls/<film>/master.m3u8` and a
  `manifest.json`. If a download fails it generates a placeholder clip instead.
- `backend/video.py` - `GET /api/play/{id}` returns the stream URL, duration
  and a resume point; `/media/*` serves the playlists and segments.
- `frontend/js/player.js` - plays the stream with hls.js (Safari plays HLS
  natively). It resumes where you left off and reports the seconds you
  actually watched, which feeds the recommender and Continue Watching. Without
  any ingested video it falls back to the old simulated player.

ffmpeg comes from the `imageio-ffmpeg` package unless one is already on your
PATH. hls.js 1.7.3 is vendored in `frontend/vendor/` (Apache-2.0), so the
player works offline.

## Artwork (TMDB)

By default every title gets a generated poster (`backend/poster.py`). With a
free TMDB API key, `python -m data_engineering.tmdb` matches each title by name,
year and type, and downloads its poster, banner and title logo, plus episode
names, synopses and stills for the series, into `data/tmdb/`. The app serves
them from there, so after one run everything works offline again; titles
without a match keep the generated poster.

1. Create a free account at https://www.themoviedb.org and request an API key
   (Settings, then API).
2. Copy `.env.example` to `.env` and paste either the "API Read Access Token" or
   the "API Key" after `TMDB_API_KEY=`. `.env` stays on your machine.
3. Run `python -m data_engineering.tmdb`. Reload the app.

Image URLs carry the art's version, so browsers pick up new art straight away.
This product uses the TMDB API but is not endorsed or certified by TMDB.

## Frontend features (`frontend/`)

- Profiles: a "Who's watching?" gate; recommendations are per-profile, with a
  Kids profile that filters out mature content.
- Home: hero plus personalised rails (Top Picks, Trending, Critically
  Acclaimed, New Releases, Because you watched X, genre rows).
- Detail modal: synopsis, cast, warehouse stats (viewers / watch hours /
  completion), rating, add to My List, and "More Like This".
- Player: adaptive HLS video with a quality picker, resume, and real
  watch-time reporting; falls back to simulated playback if no video is set up.
- Search: live semantic search as you type.
- Assistant: chat panel with poster results.
- Analytics: KPI cards and bar charts from the warehouse.
- Posters: each title's key art is a procedurally generated SVG
  (`backend/poster.py`).

## API reference

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/profiles` | selectable demo profiles |
| GET | `/api/home?profile_id=` | personalised home rails |
| GET | `/api/title/{id}?profile_id=` | detail, stats, more-like-this |
| GET | `/api/catalog?type=movie|series` | full catalog (popularity-ranked) |
| GET | `/api/genre/{name}` | titles in a genre |
| GET | `/api/recommendations?profile_id=` | hybrid personalised list |
| GET | `/api/search?q=` | semantic search |
| POST | `/api/assistant` | assistant (`{profile_id, query}`) |
| POST | `/api/watch` | record a watch event |
| POST | `/api/rate` | rate a title (1-5) |
| GET/POST | `/api/mylist`, `/api/mylist/add`, `/api/mylist/remove` | My List |
| GET | `/api/analytics` | dashboard payload |
| GET | `/api/eval` | offline recommender metrics (add `?grid=true` to tune weights) |
| GET | `/api/streaming/stats` | broker/topic/consumer metrics |
| GET | `/api/streaming/trending` | live trending from the event stream |
| GET | `/api/poster/{id}` | SVG poster |
| GET | `/api/play/{id}?profile_id=` | stream URL, duration and resume point |
| GET | `/media/{film}/master.m3u8` | HLS playlists and segments |
| POST | `/api/admin/refresh` | retrain models from the warehouse |

## Project layout

```
netflix clone/
├── run.py                     # launcher (build DB + serve)
├── paths.py                   # shared path config
├── requirements.txt
├── data_engineering/
│   ├── catalog_seed.py        # curated 60+ title catalog (source extract)
│   ├── generate_data.py       # synthetic viewers + interaction events
│   ├── schema.sql             # star-schema DDL
│   ├── pipeline.py            # ETL orchestrator (extract, transform, load, validate)
│   ├── data_quality.py        # data-quality checks
│   └── analytics.py           # KPI queries
├── ai/
│   ├── data_access.py         # warehouse to feature frames
│   ├── content_based.py       # TF-IDF content model
│   ├── collaborative.py       # SVD + item-item CF
│   ├── semantic_search.py     # NL semantic search
│   ├── recommender.py         # hybrid recommender service (singleton) + MMR
│   ├── evaluation.py          # offline ranking-metrics harness + weight tuning
│   └── assistant.py           # assistant
├── streaming/
│   ├── broker.py              # append-only log broker (topics/partitions/offsets)
│   ├── producer.py            # event producer
│   ├── consumer.py            # consumer with per-group offset commits
│   ├── kafka_backend.py       # real Kafka backend (kafka-python)
│   ├── backends.py            # backend selection: Kafka or in-process log
│   ├── topics.py              # topic config
│   └── pipeline.py            # consumer app: sink + live trending
├── docker-compose.yml         # single-node Kafka (KRaft) for the real backend
├── media/
│   ├── library.py             # film list, quality ladder, title -> film mapping
│   └── ingest.py              # download + ffmpeg HLS transcode
├── backend/
│   ├── main.py                # FastAPI app + routes
│   ├── store.py               # operational writes
│   ├── video.py               # /api/play + HLS file serving
│   └── poster.py              # SVG posters
├── frontend/
│   ├── index.html
│   ├── css/styles.css
│   ├── js/{api.js, player.js, app.js}
│   └── vendor/hls.min.js      # hls.js 1.7.3 (Apache-2.0)
└── data/
    ├── netflix.db             # generated SQLite warehouse
    ├── media/                 # generated HLS streams + manifest.json
    └── logs/etl.log           # pipeline logs
```

## Tests

```powershell
pip install pytest
python -m pytest tests -q
```

Covers data-quality checks, recommender behaviour (personalization, kids filter,
cold start, live updates), the ALS model, the evaluation harness (metrics in
range, hybrid beats the popularity baseline), the video pipeline (placeholder
ingest into multi-bitrate HLS, the play endpoint), and an API smoke test.

## Reproducibility

Synthetic data generation is seeded (`SEED = 42`), so every `--rebuild` produces
the same warehouse. Delete `data/netflix.db` and relaunch to regenerate from
scratch.
