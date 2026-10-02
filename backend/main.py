"""FastAPI app serving the frontend and JSON API.

Run:  python -m backend.main      (or)      python run.py
"""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from paths import DB_PATH, FRONTEND_DIR
from backend import store
from backend.poster import poster_svg
from streaming.topics import WATCH_EVENTS, RATINGS


# lazily-initialised singletons
_svc = None
_concierge = None
_producer = None
_processor = None
_backend = None


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


def streaming():
    """Lazily start the streaming backend (Kafka if reachable, else the
    in-process log), its producer, and the consumer that sinks events into the
    warehouse and updates live recommender state."""
    global _producer, _processor, _backend
    if _processor is None:
        from streaming.backends import get_stream_backend
        from streaming.pipeline import StreamProcessor
        from streaming.topics import WATCH_EVENTS as W, RATINGS as R, SINK_GROUP
        _backend = get_stream_backend()
        _producer = _backend.producer
        consumer = _backend.make_consumer(SINK_GROUP, [W, R])
        s = svc()

        def on_watch(v):
            store.record_watch(v["profile_id"], v["title_id"],
                               v.get("watch_seconds", 0), v.get("completion", 0.0),
                               v.get("device", "Web"))
            s.add_interaction(v["profile_id"], v["title_id"],
                              completion=float(v.get("completion", 0.0)))

        def on_rating(v):
            store.record_rating(v["profile_id"], v["title_id"], v["rating"])
            s.add_interaction(v["profile_id"], v["title_id"], rating=int(v["rating"]))

        _processor = StreamProcessor(consumer, _backend.stats,
                                     on_watch=on_watch, on_rating=on_rating)
        _processor.start()
    return _producer, _processor


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    svc()        # warm the models so the first request is fast
    streaming()  # start the event-streaming consumer
    yield
    if _processor is not None:
        _processor.stop()


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


# writes: published to the event stream; the consumer sinks them to the warehouse
@app.post("/api/watch")
def watch(body: WatchBody):
    producer, _ = streaming()
    ack = producer.send(WATCH_EVENTS, {
        "profile_id": body.profile_id, "title_id": body.title_id,
        "watch_seconds": body.watch_seconds, "completion": body.completion,
        "device": body.device}, key=body.profile_id)
    return {"ok": True, "streamed": True, **ack}


@app.post("/api/rate")
def rate(body: RateBody):
    producer, _ = streaming()
    ack = producer.send(RATINGS, {
        "profile_id": body.profile_id, "title_id": body.title_id,
        "rating": body.rating}, key=body.profile_id)
    return {"ok": True, "streamed": True, **ack}


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


# event-streaming pipeline
@app.get("/api/streaming/stats")
def streaming_stats():
    _, proc = streaming()
    st = proc.stats()
    st["backend"] = _backend.kind if _backend else "log"
    return st


@app.get("/api/streaming/trending")
def streaming_trending(k: int = 10):
    s = svc()
    _, proc = streaming()
    return {"items": s.hydrate(proc.trending.top(k))}


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


# frontend, mounted last so /api/* wins
@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "models_ready": _svc is not None and _svc.ready}


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
