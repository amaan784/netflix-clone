"""The open-licensed films that stand in for catalog titles, and where their HLS streams live."""
from __future__ import annotations

import json
from pathlib import Path

from paths import DATA_DIR

MEDIA_DIR = DATA_DIR / "media"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NetClone-media-ingest/1.0"

TOS_SUBS = "https://download.blender.org/demo/movies/ToS/subtitles/"

# The catalog titles are copyrighted, so every title plays one of these instead.
# intro: the opening titles, in seconds (the player offers Skip Intro);
# credits: when the end credits start (a series offers Next Episode from there).
# Both were read off frame grabs of the films.
FILMS = [
    {
        "id": "big_buck_bunny",
        "title": "Big Buck Bunny",
        "url": "https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_640x360.m4v.zip",
        "credit": "Big Buck Bunny (c) 2008 Blender Foundation, bigbuckbunny.org. CC BY 3.0",
        "intro": [1, 31], "credits": 488,
    },
    {
        "id": "elephants_dream",
        "title": "Elephants Dream",
        "url": "https://download.blender.org/ED/elephantsdream-720-h264-st-aac.mov",
        "credit": "Elephants Dream (c) 2006 Blender Foundation / Netherlands Media Art "
                  "Institute, elephantsdream.org. CC BY 2.5",
        "intro": [1, 25], "credits": 552,
    },
    {
        "id": "tears_of_steel",
        "title": "Tears of Steel",
        "url": "https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov",
        "credit": "Tears of Steel (c) 2012 Blender Foundation, mango.blender.org. CC BY 3.0",
        "intro": [1, 22], "credits": 586,
        # the film's official subtitle files
        "subtitles": {
            "en": ("English", TOS_SUBS + "TOS-en.srt"),
            "de": ("Deutsch", TOS_SUBS + "TOS-de.srt"),
            "es": ("Español", TOS_SUBS + "TOS-es.srt"),
            "fr": ("Français", TOS_SUBS + "TOS-fr-orig.srt"),
            "it": ("Italiano", TOS_SUBS + "TOS-it.srt"),
            "nl": ("Nederlands", TOS_SUBS + "TOS-nl.srt"),
            "pt-BR": ("Português (Brasil)", TOS_SUBS + "TOS-PT-BR.srt"),
            "ru": ("Русский", TOS_SUBS + "TOS-ru.srt"),
            "ja": ("日本語", TOS_SUBS + "TOS-JP.srt"),
        },
    },
]
FILM_BY_ID = {f["id"]: f for f in FILMS}

# (name, width, video kbps). A film only gets the rungs that fit its own width,
# so a 640-wide source isn't upscaled to 720p.
LADDER = [("240p", 426, 400), ("360p", 640, 800), ("720p", 1280, 2500)]


def media_dir(root=None) -> Path:
    return Path(root) if root else MEDIA_DIR


def load_manifest(root=None) -> dict:
    path = media_dir(root) / "manifest.json"
    if not path.exists():
        return {"assets": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(manifest: dict, root=None):
    path = media_dir(root) / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def asset_for_title(title_id: int, manifest: dict | None = None, episode_id: int | None = None):
    """Spread titles (and a series' episodes) evenly over whatever films have been ingested."""
    assets = (manifest if manifest is not None else load_manifest())["assets"]
    if not assets:
        return None
    return assets[int(episode_id if episode_id is not None else title_id) % len(assets)]


def still_for_episode(title_id: int, episode_id: int, root=None) -> Path | None:
    """A frame from the film an episode plays, so its thumbnail matches what you'll see."""
    manifest = load_manifest(root)
    asset = asset_for_title(title_id, manifest, episode_id)
    stills = asset.get("stills") if asset else None
    if not stills:
        return None
    # episodes that share a film get different frames from it
    path = media_dir(root) / "hls" / stills[(episode_id // len(manifest["assets"])) % len(stills)]
    return path if path.exists() else None
