# NetClone v4: REST API

Puts the models behind a FastAPI app. There's no UI yet; use the generated docs
page to try the endpoints.

## New in this stage (`backend/`)

- `main.py` - profiles, home rows, title detail, catalog, genres, search,
  recommendations, assistant, watch/rate, My List, analytics, posters.
- `store.py` - writes watch events, ratings and My List entries to the
  warehouse.
- `poster.py` - draws an SVG poster for each title, so no image files are
  needed.

Watch and rate calls write straight to the database and update the
recommender's in-memory state, so recommendations react right away.

## Run

```powershell
pip install -r requirements.txt
python run.py
```

Then open http://127.0.0.1:8000/docs.
