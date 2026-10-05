"""The open-licensed films that stand in for catalog titles, and where their HLS streams live."""
from __future__ import annotations

import json
from pathlib import Path

from paths import DATA_DIR

MEDIA_DIR = DATA_DIR / "media"

# The catalog titles are copyrighted, so every title plays one of these instead.
FILMS = [
    {
        "id": "big_buck_bunny",
        "title": "Big Buck Bunny",
        "url": "https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_640x360.m4v.zip",
        "credit": "Big Buck Bunny (c) 2008 Blender Foundation, bigbuckbunny.org. CC BY 3.0",
    },
    {
        "id": "elephants_dream",
        "title": "Elephants Dream",
        "url": "https://download.blender.org/ED/elephantsdream-720-h264-st-aac.mov",
        "credit": "Elephants Dream (c) 2006 Blender Foundation / Netherlands Media Art "
                  "Institute, elephantsdream.org. CC BY 2.5",
    },
    {
        "id": "tears_of_steel",
        "title": "Tears of Steel",
        "url": "https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov",
        "credit": "Tears of Steel (c) 2012 Blender Foundation, mango.blender.org. CC BY 3.0",
    },
]

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


def asset_for_title(title_id: int, manifest: dict | None = None):
    """Spread titles evenly over whatever films have been ingested."""
    assets = (manifest if manifest is not None else load_manifest())["assets"]
    if not assets:
        return None
    return assets[int(title_id) % len(assets)]
