# NetClone v5: Web frontend

Adds the Netflix-style frontend and has the API serve it. This is the first
version you can click through.

## New in this stage

- `frontend/` - a single-page app in plain JavaScript with no build step:
  profile picker, hero banner, content rows, title detail modal, a mock player
  that records watch progress, live search, the assistant panel, and an
  analytics page.
- `backend/main.py` now serves `index.html` and the static files.

## Run

```powershell
pip install -r requirements.txt
python run.py
```

Open http://127.0.0.1:8000 and pick a profile.
