"""Video playback: which stream a title (or episode) plays, where to resume, and the HLS file server."""
from __future__ import annotations

import mimetypes
import sqlite3

from fastapi import APIRouter, HTTPException
from fastapi.staticfiles import StaticFiles

from paths import DB_PATH
from backend import episodes
from media import library

router = APIRouter()

# Windows maps .ts to TypeScript in the registry; HLS needs the MPEG-TS type
mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")
mimetypes.add_type("video/mp2t", ".ts")


def _with_label(ep: dict | None) -> dict | None:
    return {**ep, "label": episodes.label(ep)} if ep else None


@router.get("/api/play/{title_id}")
def play(title_id: int, profile_id: int | None = None, episode_id: int | None = None):
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT content_type FROM dim_title WHERE title_id=?", (title_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Title not found")

    # a series plays one episode: the one asked for, else whichever is up next
    ep = None
    if row[0] == "series":
        ep = episodes.get(episode_id) if episode_id else episodes.up_next(profile_id, title_id)
        if not ep or ep["title_id"] != title_id:
            raise HTTPException(404, "Episode not found")

    last = None
    if profile_id:
        conn = sqlite3.connect(DB_PATH)
        try:
            last = conn.execute(
                "SELECT completion FROM fact_watch_event WHERE profile_id=? AND title_id=? "
                "AND episode_id IS ? ORDER BY event_ts DESC, event_id DESC LIMIT 1",
                (profile_id, title_id, ep["episode_id"] if ep else None)).fetchone()
        finally:
            conn.close()

    asset = library.asset_for_title(title_id, episode_id=ep["episode_id"] if ep else None)
    if asset is None:
        return {"available": False, "hint": "No video set up yet. Run: python -m media.ingest",
                "episode": _with_label(ep), "next_episode": _with_label(ep and episodes.next_after(ep))}

    # resume only for started-but-unfinished, same rule as Continue Watching
    completion = last[0] if last and 0.05 < last[0] < 0.9 else 0.0
    return {
        "available": True,
        "asset": asset["id"],
        "film": asset["title"],
        "credit": asset["credit"],
        "src": f"/media/{asset['path']}",
        "duration": asset["duration"],
        "resume_at": round(completion * asset["duration"], 1),
        "renditions": [r["name"] for r in asset["renditions"]],
        "placeholder": asset.get("placeholder", False),
        # player extras (media.extras): seek-bar previews, subtitles, skip-intro / credits marks
        "thumbnails": f"/media/{asset['thumbnails']}" if asset.get("thumbnails") else None,
        "subtitles": [{"lang": s["lang"], "label": s["label"], "src": f"/media/{s['path']}"}
                      for s in asset.get("subtitles", [])],
        "intro": asset.get("intro"),
        "credits": asset.get("credits"),
        "episode": _with_label(ep),
        "next_episode": _with_label(ep and episodes.next_after(ep)),
    }


def install(app):
    app.include_router(router)
    app.mount("/media", StaticFiles(directory=str(library.media_dir() / "hls"), check_dir=False),
              name="media")
