"""FastAPI JSON API. Interactive docs at /docs.

Run:  python -m backend.main      (or)      python run.py
"""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from paths import DB_PATH
from backend import store
from backend.poster import poster_svg


# lazily-initialised singletons
_svc = None
_concierge = None


def _ensure_db():
    """Build the database via the pipeline if it doesn't exist yet."""
    need = True
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(DB_PATH)
            n = conn.execute("SELECT COUNT(*) FROM dim_title").fetchone()[0]
            conn.close()
            need = n == 0
        except sqlite3.Error:
            need = True
    if need:
        from data_engineering.pipeline import run_pipeline
        run_pipeline()


def svc():
    global _svc, _concierge
    if _svc is None:
        _ensure_db()
        from ai.recommender import get_service
        from ai.assistant import AIConcierge
        _svc = get_service()
        _concierge = AIConcierge(_svc)
    return _svc


def concierge():
    svc()
    return _concierge


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    svc()  # warm the models so the first request is fast
    yield


app = FastAPI(title="Netflix Clone API", version="1.0.0", lifespan=_lifespan)


# request models
class WatchBody(BaseModel):
    profile_id: int
    title_id: int
    watch_seconds: int = 0
    completion: float = 0.0
    device: str = "Web"


class RateBody(BaseModel):
    profile_id: int
    title_id: int
    rating: int


class ListBody(BaseModel):
    profile_id: int
    title_id: int


class AssistantBody(BaseModel):
    profile_id: int
    query: str


# catalog / profiles
@app.get("/api/profiles")
def get_profiles():
    s = svc()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT p.profile_id, p.profile_name, p.is_kids, p.avatar_hue, p.age_group
            FROM dim_profile p JOIN dim_user u ON p.user_id = u.user_id
            WHERE u.is_synthetic = 0 ORDER BY p.profile_id""").fetchall()
    finally:
        conn.close()
    return [{"profile_id": r[0], "name": r[1], "is_kids": bool(r[2]),
             "avatar_hue": r[3], "age_group": r[4]} for r in rows]


@app.get("/api/genres")
def get_genres():
    return svc().genres_list()


@app.get("/api/home")
def get_home(profile_id: int = Query(...)):
    s = svc()
    rows = s.home_rows(profile_id)
    my_list = store.get_list(profile_id)
    if my_list:
        items = [s.get_title(t) for t in my_list if s.get_title(t)]
        rows.insert(1, {"title": "My List", "kind": "mylist", "items": items})
    return {"profile_id": profile_id, "rows": rows}


@app.get("/api/title/{title_id}")
def get_title(title_id: int, profile_id: int = Query(None)):
    s = svc()
    rec = s.get_title(title_id)
    if not rec:
        raise HTTPException(404, "Title not found")
    out = dict(rec)
    out["stats"] = s.title_stats(title_id)
    out["more_like_this"] = s.more_like_this(title_id, k=12)
    out["in_list"] = title_id in store.get_list(profile_id) if profile_id else False
    out["my_rating"] = _my_rating(profile_id, title_id) if profile_id else None
    return out


def _my_rating(profile_id, title_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT rating FROM fact_rating WHERE profile_id=? AND title_id=?",
                           (profile_id, title_id)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


@app.get("/api/genre/{name}")
def get_genre(name: str, profile_id: int = Query(None)):
    s = svc()
    allow = s._allowed_for_profile(profile_id) if profile_id else None
    return {"genre": name, "items": s.by_genre(name, k=40, allow=allow)}


@app.get("/api/catalog")
def catalog(type: str = Query(None), profile_id: int = Query(None)):
    """All titles (optionally filtered by movie/series), popularity-ranked."""
    s = svc()
    allow = s._allowed_for_profile(profile_id) if profile_id else (lambda r: True)
    items = [i for i in s.all_titles()
             if (type is None or i["content_type"] == type) and allow(i)]
    items.sort(key=lambda r: s.popularity.get(r["title_id"], 0), reverse=True)
    return {"type": type, "items": items}


# search + assistant
@app.get("/api/search")
def search(q: str = Query(""), profile_id: int = Query(None)):
    return {"query": q, "items": svc().search(q, k=30)}


@app.post("/api/assistant")
def assistant(body: AssistantBody):
    return concierge().ask(body.profile_id, body.query)


# recommendations
@app.get("/api/recommendations")
def recommendations(profile_id: int = Query(...), k: int = 24):
    return {"items": svc().recommend_for_profile(profile_id, k=k)}


# writes
@app.post("/api/watch")
def watch(body: WatchBody):
    res = store.record_watch(body.profile_id, body.title_id,
                             body.watch_seconds, body.completion, body.device)
    svc().add_interaction(body.profile_id, body.title_id, completion=res["completion"])
    return {"ok": True, **res}


@app.post("/api/rate")
def rate(body: RateBody):
    res = store.record_rating(body.profile_id, body.title_id, body.rating)
    svc().add_interaction(body.profile_id, body.title_id, rating=res["rating"])
    return {"ok": True, **res}


@app.get("/api/mylist")
def mylist(profile_id: int = Query(...)):
    s = svc()
    items = [s.get_title(t) for t in store.get_list(profile_id) if s.get_title(t)]
    return {"items": items}


@app.post("/api/mylist/add")
def mylist_add(body: ListBody):
    store.add_to_list(body.profile_id, body.title_id)
    return {"ok": True, "in_list": True}


@app.post("/api/mylist/remove")
def mylist_remove(body: ListBody):
    store.remove_from_list(body.profile_id, body.title_id)
    return {"ok": True, "in_list": False}


# analytics dashboard
@app.get("/api/analytics")
def analytics():
    return store.get_analytics()


# admin / ops
@app.post("/api/admin/refresh")
def refresh():
    """Retrain the models from the current database state."""
    svc().refresh()
    return {"ok": True, "message": "Models retrained from warehouse."}


# posters
@app.get("/api/poster/{title_id}")
def poster(title_id: int, w: int = 300, h: int = 450):
    rec = svc().get_title(title_id)
    if not rec:
        raise HTTPException(404, "Title not found")
    svg = poster_svg(rec, w=w, h=h)
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/health")
def health():
    return {"status": "ok", "models_ready": _svc is not None and _svc.ready}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
