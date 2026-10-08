"""FastAPI app serving the frontend and JSON API.

Run:  python -m backend.main      (or)      python run.py
"""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from paths import DB_PATH, FRONTEND_DIR, ROOT
from backend import artwork, episodes, store, video
from streaming.topics import WATCH_EVENTS, RATINGS


# lazily-initialised singletons
_svc = None
_concierge = None
_producer = None
_processor = None
_backend = None


def svc():
    global _svc, _concierge
    if _svc is None:
        from data_engineering.pipeline import ensure_warehouse
        from ai.recommender import get_service
        from ai.assistant import AIConcierge
        ensure_warehouse()
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
                               v.get("device", "Web"), v.get("episode_id"))
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
    episode_id: int | None = None  # required for a series, absent for a film
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
    svc()  # makes sure the warehouse exists
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
    top_extra = []

    # Continue Watching: started-but-unfinished titles, with resume % (and the episode, for a series)
    cw_items = []
    for e in store.get_continue_watching(profile_id):
        rec = s.get_title(e["title_id"])
        if rec:
            cw_items.append({**rec, **e})
    if cw_items:
        top_extra.append({"title": "Continue Watching", "kind": "continue", "items": cw_items})

    my_list = store.get_list(profile_id)
    if my_list:
        items = [s.get_title(t) for t in my_list if s.get_title(t)]
        if items:
            top_extra.append({"title": "My List", "kind": "mylist", "items": items})

    rows = top_extra + rows

    # Top 10 in the profile's country
    country = store.get_profile_country(profile_id)
    if country:
        allow = s._allowed_for_profile(profile_id)
        c_items = []
        for r in store.get_top_in_country(country, 10):
            rec = s.get_title(r["title_id"])
            if rec and allow(rec):
                c_items.append(dict(rec))
        if c_items:
            rows.append({"title": f"Top 10 in {country} Today", "kind": "country",
                         "items": c_items})

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
    if rec["content_type"] == "series":
        out["seasons"] = episodes.seasons(title_id, profile_id)
        nxt = episodes.up_next(profile_id, title_id)
        last = episodes.last_watched(profile_id, title_id) if profile_id else None
        out["up_next"] = {**nxt, "label": episodes.label(nxt),
                          "resume": bool(last and last["episode_id"] == nxt["episode_id"])}
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
    # checked here, not in the consumer: a bad event would otherwise sit in the log
    rec = _title_or_404(body.title_id)
    if rec["content_type"] == "series":
        ep = episodes.get(body.episode_id) if body.episode_id else None
        if not ep or ep["title_id"] != body.title_id:
            raise HTTPException(400, "A series needs the episode_id of one of its episodes")
    elif body.episode_id is not None:
        raise HTTPException(400, "A film has no episodes")
    producer, _ = streaming()
    ack = producer.send(WATCH_EVENTS, {
        "profile_id": body.profile_id, "title_id": body.title_id,
        "episode_id": body.episode_id,
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


# offline recommender evaluation
@app.get("/api/eval")
def eval_models(k: int = 10, strategy: str = "last_n", include_kids: bool = True,
                grid: bool = False):
    from ai import evaluation
    svc()  # ensure the warehouse exists
    res = evaluation.run_evaluation(ks=(5, k, 20), strategy=strategy,
                                    include_kids=include_kids)
    if grid:
        res["grid_search"] = evaluation.grid_search_hybrid(
            metric=f"ndcg@{k}", strategy=strategy, include_kids=include_kids)
    return res


# admin / ops
@app.post("/api/admin/refresh")
def refresh():
    """Retrain the models from the current database state."""
    svc().refresh()
    return {"ok": True, "message": "Models retrained from warehouse."}


# artwork: TMDB images once data_engineering.tmdb has run, generated SVGs until then
def _title_or_404(title_id: int) -> dict:
    rec = svc().get_title(title_id)
    if not rec:
        raise HTTPException(404, "Title not found")
    return rec


@app.get("/api/poster/{title_id}")
def poster(title_id: int, w: int = 300, h: int = 450):
    return artwork.image(_title_or_404(title_id), "poster", w, h)


@app.get("/api/backdrop/{title_id}")
def backdrop(title_id: int, w: int = 1280, h: int = 720):
    return artwork.image(_title_or_404(title_id), "backdrop", w, h)


@app.get("/api/logo/{title_id}")
def logo(title_id: int):
    return artwork.image(_title_or_404(title_id), "logo", 0, 0)


@app.get("/api/still/{episode_id}")
def still(episode_id: int, w: int = 400, h: int = 225):
    ep = episodes.get(episode_id)
    if not ep:
        raise HTTPException(404, "Episode not found")
    return artwork.still(_title_or_404(ep["title_id"]), ep, w, h)


@app.get("/api/artwork")
def artwork_status():
    return artwork.status(svc().get_title)


# architecture doc (lives at the repo root, served here so the app can link to it)
@app.get("/architecture.html")
def architecture():
    return FileResponse(ROOT / "ARCHITECTURE.html")


# frontend, mounted last so /api/* wins
@app.get("/")
def index():
    # no-cache = revalidate every time; otherwise a browser can keep an old page
    # (and its old ?v= script links) for hours after an update
    return FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health():
    return {"status": "ok", "models_ready": _svc is not None and _svc.ready}


class FrontendFiles(StaticFiles):
    """The frontend's files with no-cache, so browsers check for a newer copy
    (a cheap 304 when nothing changed) instead of guessing how long to keep one."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


video.install(app)  # /api/play and /media/*, ahead of the catch-all frontend mount
app.mount("/", FrontendFiles(directory=str(FRONTEND_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
