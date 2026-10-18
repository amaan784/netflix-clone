# NetClone

A Netflix-style streaming app that runs entirely on your own machine, with a
full data and machine-learning stack behind it: an ETL warehouse, a lakehouse,
event streaming, a trained recommender and adaptive video streaming.

## Features

- **Streaming app**: accounts and profiles, personalised home rails, series
  with seasons and episodes, and a player with adaptive HLS video, subtitles,
  seek-bar previews, Skip Intro and Next Episode.
- **Data engineering**: ETL into a SQLite star schema with 21 data-quality
  checks, plus a DuckDB lakehouse (bronze, silver and gold Parquet layers).
- **Event streaming**: a Kafka-style log with partitions and consumer groups
  that feeds the warehouse, the lakehouse and live trending (or real Kafka).
- **Machine learning**: a hybrid recommender (content-based, collaborative,
  matrix factorisation) with a trained pairwise ranker, semantic search, an AI
  assistant, and an offline evaluation harness.
- **Analytics**: in-app dashboards for the warehouse, the stream, the lakehouse
  and the model metrics.

Stack: Python, FastAPI, pandas, scikit-learn, SQLite, DuckDB, ffmpeg, hls.js,
vanilla JavaScript.

## Quick start

```powershell
pip install -r requirements.txt
python run.py
```

The first launch builds the data warehouse. Then open http://127.0.0.1:8000
and sign in with the demo account, or sign up:

| Demo account | |
|--------------|---|
| Email | `demo@netflixclone.local` |
| Password | `netclone-demo` |

It comes with four profiles (Aman, Movie Night, Chill, Kids) that already have
some viewing history.

### Optional extras

**Real video.** The catalog titles are copyrighted, so every title plays one of
three openly licensed Blender films. Download and convert them once (about
645 MB); until then the player simulates playback:

```powershell
python -m media.ingest
```

**Real poster art.** With a free [TMDB](https://www.themoviedb.org) API key,
titles get their real posters, banners and logos instead of generated ones:

```powershell
copy .env.example .env      # then paste your key into .env
python -m data_engineering.tmdb
```

## Tests

```powershell
pip install pytest httpx
python -m pytest tests -q
```

## Documentation

- [Technical guide](docs/TECHNICAL_GUIDE.md): how each part works, every
  command, the API reference, the evaluation results and the project layout.
- [ARCHITECTURE.html](ARCHITECTURE.html): the architecture with diagrams. It's
  also served at `/architecture.html` while the app runs ("Docs" in the navbar).

## Credits

The films are by the Blender Foundation (Big Buck Bunny, Elephants Dream, Tears
of Steel; CC BY). hls.js is Apache-2.0. This product uses the TMDB API but is
not endorsed or certified by TMDB.

## License

[MIT](LICENSE)
