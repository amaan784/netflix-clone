"""
Player extras for the converted films: seek-bar preview thumbnails, a set of
stills (episode thumbnails when TMDB has none), and subtitles.

    python -m media.extras             # add whatever is missing to every film
    python -m media.extras --refresh   # rebuild all of it

media.ingest does this for each film it converts; run it by hand for films
converted before the extras existed.
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from media.library import FILM_BY_ID, UA, load_manifest, media_dir, save_manifest

THUMB_EVERY = 4          # seconds; also the keyframe interval, so each thumbnail is a keyframe
THUMB_W, THUMB_H = 160, 90
TILE = 10                # a sprite sheet is TILE x TILE thumbnails
STILLS = 24

TIMING = re.compile(r"^\s*(\d+:\d\d:\d\d[,.]\d+)\s*-+>\s*(\d+:\d\d:\d\d[,.]\d+)")


def _vtt_time(seconds: float) -> str:
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def _playlist(asset_dir: Path, asset: dict, best: bool) -> Path:
    names = [r["name"] for r in asset["renditions"]]  # smallest first
    return asset_dir / (names[-1] if best else names[0]) / "index.m3u8"


def make_thumbnails(ffmpeg: str, asset_dir: Path, asset: dict) -> int:
    """Sprite sheets of small frames, one every THUMB_EVERY seconds, and the
    WebVTT file that says which part of which sheet covers each moment.
    Decoding keyframes only (-skip_frame nokey) makes this take seconds."""
    out = asset_dir / "thumbs"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.jpg"):
        old.unlink()
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-skip_frame", "nokey", "-i", str(_playlist(asset_dir, asset, best=False)),
                    "-vf", f"fps=1/{THUMB_EVERY},scale={THUMB_W}:{THUMB_H},tile={TILE}x{TILE}",
                    "-q:v", "5", str(out / "sheet_%02d.jpg")], check=True)

    count = math.ceil(asset["duration"] / THUMB_EVERY)
    lines = ["WEBVTT", ""]
    for i in range(count):
        sheet, pos = divmod(i, TILE * TILE)
        x, y = (pos % TILE) * THUMB_W, (pos // TILE) * THUMB_H
        start, end = i * THUMB_EVERY, min((i + 1) * THUMB_EVERY, asset["duration"])
        lines += [f"{_vtt_time(start)} --> {_vtt_time(end)}",
                  f"thumbs/sheet_{sheet + 1:02d}.jpg#xywh={x},{y},{THUMB_W},{THUMB_H}", ""]
    (asset_dir / "thumbs.vtt").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return count


def make_stills(ffmpeg: str, asset_dir: Path, asset: dict, count: int = STILLS) -> int:
    """Frames spread across the film, skipping the opening titles and the credits."""
    out = asset_dir / "stills"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.jpg"):
        old.unlink()
    start, length = asset["duration"] * 0.06, asset["duration"] * 0.74
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-skip_frame", "nokey", "-ss", f"{start:.2f}", "-t", f"{length:.2f}",
                    "-i", str(_playlist(asset_dir, asset, best=True)),
                    "-vf", f"fps=1/{length / count:.3f},scale=480:-2", "-frames:v", str(count),
                    "-q:v", "4", str(out / "%02d.jpg")], check=True)
    _drop_dark(ffmpeg, out)
    return len(list(out.glob("*.jpg")))


def _drop_dark(ffmpeg: str, folder: Path, floor: float = 24):
    """Fades between scenes leave a few near-black frames; delete any whose
    average brightness (ffmpeg signalstats YAVG, 0-255) is under `floor`."""
    files = sorted(folder.glob("*.jpg"))
    err = subprocess.run([ffmpeg, "-hide_banner", "-i", str(folder / "%02d.jpg"),
                          "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                          "-f", "null", "-"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stderr
    brightness = [float(v) for v in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", err)]
    if len(brightness) == len(files):  # anything else and we'd be guessing which is which
        for f, y in zip(files, brightness):
            if y < floor:
                f.unlink()


def srt_to_vtt(srt: str) -> str:
    """SubRip to WebVTT: a header, and timings with dots for milliseconds. Also
    repairs timing lines written with a short arrow ("->"), which one of the
    Tears of Steel files has."""
    out = ["WEBVTT", ""]
    for line in srt.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = TIMING.match(line)
        if m:
            line = f"{m.group(1).replace(',', '.')} --> {m.group(2).replace(',', '.')}"
        out.append(line)
    return "\n".join(out).strip() + "\n"


def fetch_subtitles(film: dict, asset_dir: Path, refresh: bool, log=print) -> list[dict]:
    tracks = []
    for lang, (label, url) in film.get("subtitles", {}).items():
        dest = asset_dir / "subs" / f"{lang}.vtt"
        if refresh or not dest.exists():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    srt = resp.read().decode("utf-8-sig", errors="replace")
            except (OSError, urllib.error.URLError) as e:
                log(f"  {label} subtitles unavailable ({e})")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(srt_to_vtt(srt), encoding="utf-8", newline="\n")
        tracks.append({"lang": lang, "label": label, "path": f"{asset_dir.name}/subs/{lang}.vtt"})
    return tracks


def add_extras(ffmpeg: str, asset_dir: Path, asset: dict, refresh: bool = False, log=print):
    """Fill in an asset's thumbnails, stills, subtitles and intro/credits marks."""
    if refresh or not (asset_dir / "thumbs.vtt").exists():
        make_thumbnails(ffmpeg, asset_dir, asset)
    asset["thumbnails"] = f"{asset['id']}/thumbs.vtt"
    if refresh or not any((asset_dir / "stills").glob("*.jpg")):
        make_stills(ffmpeg, asset_dir, asset)
    asset["stills"] = sorted(f"{asset['id']}/stills/{p.name}" for p in (asset_dir / "stills").glob("*.jpg"))
    # placeholder clips have no dialogue and no titles to skip
    film = {} if asset.get("placeholder") else FILM_BY_ID.get(asset["id"], {})
    asset["subtitles"] = fetch_subtitles(film, asset_dir, refresh, log)
    asset["intro"], asset["credits"] = film.get("intro"), film.get("credits")
    log(f"  extras: preview thumbnails, {len(asset['stills'])} stills, "
        f"{len(asset['subtitles'])} subtitle tracks")


def run(root=None, refresh=False, log=print) -> dict:
    from media.ingest import find_ffmpeg  # here, not at the top: ingest imports this module
    ffmpeg = find_ffmpeg()
    base = media_dir(root)
    manifest = load_manifest(base)
    for asset in manifest["assets"]:
        log(f"{asset['title']}:")
        add_extras(ffmpeg, base / "hls" / asset["id"], asset, refresh, log)
    save_manifest(manifest, base)
    return manifest


def main():
    ap = argparse.ArgumentParser(description="Add preview thumbnails, stills and subtitles.")
    ap.add_argument("--refresh", action="store_true", help="rebuild everything")
    args = ap.parse_args()
    manifest = run(refresh=args.refresh)
    if not manifest["assets"]:
        print("No films converted yet. Run: python -m media.ingest")


if __name__ == "__main__":
    main()
