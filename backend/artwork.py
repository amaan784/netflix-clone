"""Title images: TMDB art when it has been downloaded, the generated SVG otherwise."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from backend.poster import poster_svg
from data_engineering import tmdb
from media import library

# Downloaded art never changes under a given ?v= (the frontend adds the manifest's
# timestamp), so it can be cached for a day. Generated SVGs get a short cache so
# they're replaced soon after art arrives.
CACHE = {"Cache-Control": "public, max-age=86400"}
SVG_CACHE = {"Cache-Control": "public, max-age=300"}

_cache = {"key": None, "manifest": {"titles": {}}}


def manifest() -> dict:
    """The TMDB manifest, re-read when the file changes, so art fetched while
    the app is running shows up without a restart."""
    path = tmdb.TMDB_DIR / "manifest.json"
    key = (str(path), path.stat().st_mtime if path.exists() else None)
    if key != _cache["key"]:
        _cache.update(key=key, manifest=tmdb.load_manifest(tmdb.TMDB_DIR))
    return _cache["manifest"]


def _entry(rec: dict) -> dict | None:
    entry = manifest()["titles"].get(str(rec["title_id"]))
    # the name check guards against a catalog that changed since the fetch
    return entry if entry and entry.get("title") == rec["title"] else None


def _existing(local: str | None) -> Path | None:
    path = tmdb.TMDB_DIR / local if local else None
    return path if path and path.exists() else None


def _file(path: Path) -> FileResponse:
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
                        headers=CACHE)


def art_path(rec: dict, kind: str) -> Path | None:
    entry = _entry(rec)
    return _existing(entry.get(kind)) if entry else None


def image(rec: dict, kind: str, w: int, h: int):
    path = art_path(rec, kind)
    if path:
        return _file(path)
    if kind == "logo":
        raise HTTPException(404, "No logo for this title")
    # banners get a plain background: the page draws the title over them
    svg = poster_svg(rec, w=w, h=h, labels=kind == "poster")
    return Response(content=svg, media_type="image/svg+xml", headers=SVG_CACHE)


def still(rec: dict, ep: dict, w: int, h: int):
    """An episode's thumbnail: its TMDB still, else a frame from the film the
    episode plays (media.extras), else the show's banner."""
    entry = _entry(rec) or {}
    key = f"{ep['season_number']}x{ep['episode_number']}"
    path = (_existing(entry.get("episodes", {}).get(key, {}).get("still"))
            or library.still_for_episode(ep["title_id"], ep["episode_id"]))
    return _file(path) if path else image(rec, "backdrop", w, h)


def status(get_title) -> dict:
    have = {"poster": [], "backdrop": [], "logo": []}
    for tid in manifest()["titles"]:
        rec = get_title(int(tid))
        for kind, ids in have.items():
            if rec and art_path(rec, kind):
                ids.append(int(tid))
    n = len(have["poster"])
    return {"source": "TMDB" if n else None, "posters": n, "backdrops": len(have["backdrop"]),
            "logos": sorted(have["logo"]),  # the frontend shows these instead of the title text
            "version": manifest().get("updated", "none"),  # goes on image URLs as ?v=
            "attribution": tmdb.ATTRIBUTION if n else None}
