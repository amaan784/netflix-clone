# NetClone technical guide

How each part of NetClone works, in more detail than the [README](../README.md).
For diagrams, see [ARCHITECTURE.html](../ARCHITECTURE.html).

- [Commands](#commands)
- [Accounts and sign-in](#accounts-and-sign-in-backendauthpy-data_engineeringaccountspy)
- [Data engineering](#data-engineering-data_engineering)
- [Lakehouse](#lakehouse-lakehouse)
- [AI / ML](#ai--ml-ai)
- [Event streaming](#event-streaming-streaming)
- [Video streaming](#video-streaming-media)
- [Artwork](#artwork-tmdb)
- [Frontend features](#frontend-features-frontend)
- [API reference](#api-reference)
- [Project layout](#project-layout)
- [Tests](#tests)
- [Reproducibility](#reproducibility)

## Commands

```powershell
python run.py --rebuild   # force-rebuild the warehouse, then serve
python run.py --etl       # run the ETL pipeline only (no web server)
python -m data_engineering.pipeline   # same ETL, standalone
python -m lakehouse.build             # run the lakehouse (bronze -> silver -> gold)
python -m ai.evaluation               # offline recommender metrics
python -m media.extras                # preview thumbnails, stills, subtitles for converted films
```

## Accounts and sign-in (`backend/auth.py`, `data_engineering/accounts.py`)

- Sign up with a name, email and password; you get a first profile named after
  you and can add up to five (kids profiles never see mature titles).
- Passwords are hashed with scrypt and a random salt. Sessions are a random
  token in an HttpOnly, SameSite=Lax cookie; only a SHA-256 of it is stored.
- A guard in front of the API requires a session for every `/api` call (apart
  from signing in and the images) and checks that any `profile_id` in the query
  string or JSON body belongs to the signed-in account. Five wrong passwords for
  an email in ten minutes and further attempts wait.
- Accounts survive a warehouse rebuild. People who sign up get user and profile
  ids from 100000, clear of the generated population, so `--rebuild` can
  regenerate everything else and put them back. Their watch history is reset by
  a rebuild, like everyone's; My List and sign-ins are kept.
- The demo login is made by the ETL (`DEMO_EMAIL` and `DEMO_PASSWORD` in
  `data_engineering/generate_data.py`).

## Data engineering (`data_engineering/`)

A dimensional model built by a logged, idempotent ETL.

| Stage | What happens |
|-------|--------------|
| Extract | Curated catalog (`catalog_seed.py`: 61 titles, with real season and episode counts for the 32 series) plus a generated population of ~280 viewers with latent genre preferences (`generate_data.py`). A film session is one watch event; a series session is a binge of consecutive episodes, picking up where the viewer left off. ~40k watch events, ~4k ratings. |
| Transform | Surrogate keys, 855 episodes, derived attributes (decade, weekend flag), genre normalisation into a bridge table, and a calendar date dimension. |
| Load | Bulk insert into the SQLite star schema (`schema.sql`). App accounts and the profiles people made are kept; the demo login is created if missing. |
| Enrich | If TMDB data has been fetched, real episode names and synopses replace "Episode N". |
| Validate | 21 data-quality checks: referential integrity (including events to episodes), value ranges, null/blank, uniqueness, every series has episodes numbered 1..n per season, an event's episode belongs to its title, lineage values, and every login belongs to a user (`data_quality.py`). |

Star schema:

- Dimensions: `dim_title`, `dim_episode`, `dim_genre`, `dim_user`, `dim_profile`, `dim_date`
- Bridge: `bridge_title_genre` (title to genre many-to-many)
- Facts: `fact_watch_event` (additive: `watch_seconds`, `completion`; `episode_id`
  for series; `origin` = generated history or live app event), `fact_rating`
- Operational: `my_list`, `app_account`, `app_session`

The database carries a schema version (`PRAGMA user_version`). After a schema
change, an older warehouse is rebuilt automatically on the next start.

Analytics (`analytics.py`) provides the aggregations behind the in-app
dashboard: top titles by watch hours, watch hours and completion by genre, by
device, daily active profiles over the date dimension, rating distribution, and
movie/series split. The app writes live user actions (`backend/store.py`) back
into the same warehouse.

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
   - Item-item CF for "people who watched A also watched B".
   - Implicit ALS (Hu, Koren and Volinsky), used as a ranking feature.

   Episodes count in film-sized units (six watched episodes weigh like one film
   watched to the end), so a long binge doesn't drown out the films a profile
   chose.

3. Two-stage ranking for Top Picks (`ranker.py`). A hybrid blend shortlists 40
   candidates:

   ```
   hybrid = 0.40*content + 0.30*item-item + 0.20*matrix-factorization + 0.10*popularity
   ```

   and a trained ranker orders them. The ranker is pairwise (the RankNet idea
   with a linear scorer): each profile's latest titles are held out, the base
   models are refitted on the history before them, and the ranker learns
   weights that score the titles that were watched above the ones that
   weren't. Its features are the model scores plus a few about the profile and
   title (how the profile's genre taste compares with the catalog and with
   other viewers, films-vs-series habit, title age, IMDb score). The Analytics
   page shows the weights it learned. Gradient-boosted trees were tried first
   and overfit on this data. `USE_RANKER` in `recommender.py` switches Top
   Picks back to the hybrid alone. Picks are then diversity re-ranked with MMR
   (maximal marginal relevance).

4. Popularity counts viewers and "views" (watch time in film-sized units, the
   way Netflix's own Top 10 divides hours by runtime), so long shows don't win
   on raw hours alone.

5. Semantic search (`semantic_search.py`). TF-IDF vector space plus a light NL
   parser that extracts structured intent (decade, movie/series, genre, mood
   synonyms such as scary to Horror) and applies it as a soft boost.

6. Assistant (`assistant.py`). A rule-based NL assistant that classifies intent
   (recommend / similar / info / mood / search) and routes to the recommender
   or search engine, then composes a reply citing real titles.

Watching a title or rating it updates the in-memory profile state immediately,
so "Top Picks" and "Because you watched" refresh without a full retrain.
`POST /api/admin/refresh` retrains everything from the warehouse. The home page
also has a Continue Watching rail (the episode or film you stopped partway
through, or the next episode) and a "Top 10 in your country" rail.

### Offline evaluation (`ai/evaluation.py`)

A leakage-free per-profile holdout retrains every model on the train split
only, then ranks the held-out titles and scores each model with Precision@K,
Recall@K, NDCG@K, MAP@K, hit rate and catalog coverage. On a freshly built
warehouse, at K = 10:

| Model | NDCG | Precision | Recall | Hit rate | Coverage |
|-------|------|-----------|--------|----------|----------|
| hybrid | 0.263 | 0.120 | 0.363 | 0.686 | 0.934 |
| ranker | 0.258 | 0.116 | 0.350 | 0.676 | 0.984 |
| item-item | 0.243 | 0.111 | 0.334 | 0.649 | 0.869 |
| SVD | 0.220 | 0.098 | 0.298 | 0.604 | 1.000 |
| content | 0.216 | 0.103 | 0.306 | 0.652 | 0.951 |
| popularity | 0.211 | 0.100 | 0.300 | 0.642 | 0.410 |
| ALS | 0.196 | 0.094 | 0.274 | 0.572 | 1.000 |

The ranker comes out level with the hand-designed hybrid: comparing the two on
the same 558 profiles, the difference in NDCG@10 is -0.005 with a 95% interval
of -0.017 to +0.007 (paired bootstrap), and it ranks better for 30% of
profiles, worse for 35%. It gets there by learning its blend from data, and it
reaches more of the catalog. Both clearly beat every single model.

```powershell
python -m ai.evaluation            # comparison table at K=10
python -m ai.evaluation --grid     # also tune the hybrid weights
```

Also exposed at `GET /api/eval` and on the Analytics page ("Run evaluation").

## Event streaming (`streaming/`)

Watch and rating actions are not written to the database directly. The API
publishes them to an append-only log, and background consumers read it: one
maintains a time-windowed live trending tally and sinks each event into the
warehouse; the lakehouse reads the same topics with its own consumer group.
This is a local, pure-Python stand-in for Kafka (no broker to install) that
models the same concepts:

- `broker.py` - topics split into partitions; each partition is an ordered log
  persisted to a `.log` file so it survives restarts and can be replayed.
  Records are keyed (by profile) so one viewer's events stay ordered in a
  partition. Consumer groups commit offsets independently (and two processes
  sharing the offsets file never move each other's offsets backwards).
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

## Video streaming (`media/`)

Playback is adaptive HLS, the same approach real streaming services use. Each
film is cut into 4-second segments at up to three qualities (240p, 360p, 720p),
and the player switches between them as bandwidth changes. You can also pick a
quality by hand.

The catalog titles are copyrighted, so every title (and every episode of a
series) plays one of three openly licensed Blender films instead, and the
player credits whichever one is playing:

| Film | Source file (download.blender.org) | Size | License |
|------|------------------------------------|------|---------|
| Big Buck Bunny (2008) | `BigBuckBunny_640x360.m4v.zip` | 121 MB | CC BY 3.0 |
| Elephants Dream (2006) | `elephantsdream-720-h264-st-aac.mov` | 152 MB | CC BY 2.5 |
| Tears of Steel (2012) | `tears_of_steel_720p.mov` | 372 MB | CC BY 3.0 |

```powershell
python -m media.ingest                  # download, transcode, delete the originals
python -m media.ingest --placeholder    # no download: generated test-pattern clips
python -m media.ingest --only tears_of_steel --keep-source
python -m media.extras                  # extras for films converted before they existed
```

- `media/library.py` - the film list (with their opening-titles and end-credits
  times, and Tears of Steel's official subtitle files in nine languages), the
  quality ladder, and which film a title or episode plays.
- `media/ingest.py` - downloads each film, then has ffmpeg encode every quality
  in one pass with a keyframe at each segment boundary, so switching quality
  never glitches. Writes `data/media/hls/<film>/master.m3u8` and a
  `manifest.json`.
- `media/extras.py` - seek-bar preview thumbnails (sprite sheets of keyframes
  plus a WebVTT index, made in seconds by decoding keyframes only), a set of
  stills (episode thumbnails when TMDB has none; near-black frames are dropped),
  and subtitles converted from SRT to WebVTT.
- `backend/video.py` - `GET /api/play/{id}` returns the stream URL, duration, a
  resume point, subtitles, preview thumbnails, the intro and credits marks, and
  for a series the episode and the next one; `/media/*` serves the files.
- `frontend/js/player.js` - the player, with custom controls: seek bar with
  preview frames, back/forward 10 s, volume, subtitles menu, quality, full
  screen and keyboard shortcuts (Space, arrows, F, M, C, N). For episodes it
  offers Skip Intro during the opening titles and, from the end credits, a Next
  Episode countdown that plays the next one by itself. Reaching the credits
  counts as finishing. It resumes where you left off and reports the seconds
  you actually watched. Without any converted video, the same controls drive a
  fast-forward simulation.

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

- Sign-in and sign-up screens, a "Who's watching?" gate with Add Profile, and an
  account menu (switch profile, sign out).
- Home: a banner with the title's art and logo that starts a muted preview of
  the video after a few seconds, plus personalised rails (Continue Watching,
  My List, Top Picks, Trending, Critically Acclaimed, New Releases, Because you
  watched X, genre rows, Top 10 in your country).
- Detail view: synopsis, cast, warehouse stats, rating, My List, More Like
  This, and for series a season picker and episode list with thumbnails and
  progress. Play starts (or resumes) the right episode.
- Player: see [Video streaming](#video-streaming-media).
- Search: live semantic search as you type.
- Assistant: chat panel with poster results.
- Analytics: warehouse KPIs and charts, the streaming panel, the lakehouse, and
  the recommender evaluation with the ranker's learned weights.

## API reference

All `/api` calls need a signed-in session except the sign-in ones and the
images; calls that name a `profile_id` must name one of your profiles.

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/auth/signup`, `/api/auth/login`, `/api/auth/logout` | accounts and sessions |
| GET | `/api/auth/me` | who's signed in, and their profiles |
| GET/POST | `/api/profiles` | list or add profiles |
| GET | `/api/home?profile_id=` | personalised home rails |
| GET | `/api/title/{id}?profile_id=` | detail, stats, more-like-this; seasons and up-next for a series |
| GET | `/api/catalog?type=movie\|series` | full catalog (popularity-ranked) |
| GET | `/api/genre/{name}` | titles in a genre |
| GET | `/api/recommendations?profile_id=` | personalised list (hybrid + ranker) |
| GET | `/api/search?q=` | semantic search |
| POST | `/api/assistant` | assistant (`{profile_id, query}`) |
| POST | `/api/watch` | record a watch event (`episode_id` for a series) |
| POST | `/api/rate` | rate a title (1-5) |
| GET/POST | `/api/mylist`, `/api/mylist/add`, `/api/mylist/remove` | My List |
| GET | `/api/play/{id}?profile_id=&episode_id=` | stream, resume point, subtitles, thumbnails, intro/credits, next episode |
| GET | `/api/analytics` | dashboard payload |
| GET | `/api/streaming/stats`, `/api/streaming/trending` | broker metrics, live trending |
| GET | `/api/lakehouse`, POST `/api/lakehouse/run` | lakehouse layers and gold tables; run it now |
| GET | `/api/eval` | offline metrics (`?grid=true` tunes the hybrid; `?cached=true` returns the last run) |
| GET | `/api/poster/{id}`, `/api/backdrop/{id}`, `/api/logo/{id}`, `/api/still/{episode}` | images (TMDB art or generated) |
| GET | `/api/artwork` | which art is available |
| GET | `/media/{film}/master.m3u8` | HLS playlists, segments, thumbnails, subtitles |
| POST | `/api/admin/refresh` | retrain models from the warehouse |

## Project layout

```
netflix clone/
├── run.py                     # launcher (build DB + serve)
├── paths.py                   # shared path config
├── requirements.txt
├── .env.example               # TMDB key template (copy to .env)
├── ARCHITECTURE.html          # architecture write-up with diagrams
├── docs/TECHNICAL_GUIDE.md    # this guide
├── data_engineering/
│   ├── catalog_seed.py        # curated 61-title catalog with season sizes (source extract)
│   ├── generate_data.py       # synthetic viewers, episodes, interaction events, demo account
│   ├── schema.sql             # star-schema DDL + operational tables
│   ├── pipeline.py            # ETL orchestrator (extract, transform, load, enrich, validate)
│   ├── data_quality.py        # 21 data-quality checks
│   ├── accounts.py            # password hashing, sign-up, profiles, kept through rebuilds
│   ├── tmdb.py                # TMDB art and episode data (optional)
│   └── analytics.py           # KPI queries
├── lakehouse/
│   ├── bronze.py              # raw landing: warehouse history + stream batches
│   ├── silver.py              # parse, dedupe, check, quarantine, conform
│   ├── gold.py                # business tables in DuckDB SQL
│   └── build.py               # orchestration, layer stats, dashboard queries, CLI
├── ai/
│   ├── data_access.py         # warehouse to feature frames, holdout split, popularity
│   ├── content_based.py       # TF-IDF content model
│   ├── collaborative.py       # SVD + item-item CF + ALS
│   ├── ranker.py              # learned pairwise ranker (two-stage ranking)
│   ├── semantic_search.py     # NL semantic search
│   ├── recommender.py         # recommender service (singleton) + MMR
│   ├── evaluation.py          # offline ranking-metrics harness + weight tuning
│   └── assistant.py           # assistant
├── streaming/
│   ├── broker.py              # append-only log broker (topics/partitions/offsets)
│   ├── producer.py            # event producer
│   ├── consumer.py            # consumer with per-group offset commits
│   ├── kafka_backend.py       # real Kafka backend (kafka-python)
│   ├── backends.py            # backend selection: Kafka or in-process log
│   ├── topics.py              # topic config and consumer groups
│   └── pipeline.py            # consumer app: sink + live trending
├── docker-compose.yml         # single-node Kafka (KRaft) for the real backend
├── media/
│   ├── library.py             # films, intro/credits marks, subtitles, title -> film mapping
│   ├── ingest.py              # download + ffmpeg HLS transcode
│   └── extras.py              # preview thumbnails, stills, subtitles
├── backend/
│   ├── main.py                # FastAPI app + routes
│   ├── auth.py                # sessions, sign-in routes, the API guard
│   ├── episodes.py            # seasons, up-next, next episode
│   ├── store.py               # operational writes, Continue Watching
│   ├── video.py               # /api/play + HLS file serving
│   ├── artwork.py             # TMDB art or generated SVG
│   └── poster.py              # SVG posters
├── frontend/
│   ├── index.html
│   ├── css/styles.css
│   ├── js/{api.js, player.js, app.js}
│   └── vendor/hls.min.js      # hls.js 1.7.3 (Apache-2.0)
└── data/                      # all generated, safe to delete
    ├── netflix.db             # SQLite warehouse
    ├── lake/                  # bronze / silver / gold Parquet
    ├── media/                 # HLS streams, thumbnails, stills, subtitles + manifest.json
    ├── tmdb/                  # downloaded art (if you set up a key)
    ├── streaming/             # event log partitions + offsets
    └── logs/etl.log           # pipeline logs
```

## Tests

```powershell
pip install pytest httpx
python -m pytest tests -q
```

61 tests: data-quality checks, recommender behaviour (personalization, kids
filter, cold start, live updates), the ALS model, the ranker (training,
candidates, kids filter, its place in the evaluation), the evaluation harness,
episodes (generator, up-next and Continue Watching rules, watch validation,
TMDB episode names), the video pipeline (placeholder ingest into multi-bitrate
HLS, preview thumbnails, stills, SRT to WebVTT, the play endpoint), the TMDB
artwork job (run against a fake TMDB), the lakehouse (every layer, quarantine,
deduplication of a re-delivered batch), accounts over HTTP (sign-up, the guard,
profile privacy, rate limiting, rebuilds keeping accounts), and an API smoke
test.

## Reproducibility

Synthetic data generation is seeded (`SEED = 42`), so every `--rebuild` produces
the same warehouse. Delete `data/netflix.db` and relaunch to regenerate from
scratch (accounts made in the app are only kept by `--rebuild`, not by deleting
the database).
