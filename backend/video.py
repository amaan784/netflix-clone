"""Video playback: which stream a title plays, where to resume, and the HLS file server."""
from __future__ import annotations

import mimetypes
import sqlite3

from fastapi import APIRouter, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from paths import DB_PATH
from media import library

router = APIRouter()

# Windows maps .ts to TypeScript in the registry; HLS needs the MPEG-TS type
mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")
mimetypes.add_type("video/mp2t", ".ts")


@router.get("/api/play/{title_id}")
def play(title_id: int, profile_id: int = Query(None)):
    conn = sqlite3.connect(DB_PATH)
    try:
        if not conn.execute("SELECT 1 FROM dim_title WHERE title_id=?", (title_id,)).fetchone():
            raise HTTPException(404, "Title not found")
        last = None
        if profile_id:
            last = conn.execute(
                "SELECT completion FROM fact_watch_event WHERE profile_id=? AND title_id=? "
                "ORDER BY event_ts DESC LIMIT 1", (profile_id, title_id)).fetchone()
    finally:
        conn.close()

    asset = library.asset_for_title(title_id)
    if asset is None:
        return {"available": False, "hint": "No video set up yet. Run: python -m media.ingest"}

    # resume only for started-but-unfinished titles, same rule as Continue Watching
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
    }


def install(app):
    app.include_router(router)
    app.mount("/media", StaticFiles(directory=str(library.media_dir() / "hls"), check_dir=False),
              name="media")
