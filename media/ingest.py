"""
Download the films and cut them into HLS streams at 240p/360p/720p.

    python -m media.ingest                   # download and transcode every film
    python -m media.ingest --placeholder     # no download: generated test-pattern clips
    python -m media.ingest --only tears_of_steel --keep-source
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from media.library import FILMS, LADDER, load_manifest, media_dir

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NetClone-media-ingest/1.0"
SEGMENT_SECONDS = 4
VIDEO_EXT = (".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm")


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
    except ImportError:
        sys.exit("ffmpeg not found. Install it, or run: pip install imageio-ffmpeg")
    return imageio_ffmpeg.get_ffmpeg_exe()


def download(url: str, dest: Path, log=print) -> Path:
    if dest.exists():
        log(f"  already downloaded: {dest.name}")
        return dest
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done, next_report = 0, 0.0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total and done / total >= next_report:
                log(f"  {dest.name}: {done / 1e6:6.1f} / {total / 1e6:.1f} MB")
                next_report += 0.25
    part.replace(dest)
    return dest


def unpack(path: Path) -> Path:
    """Blender ships some films zipped; pull out the video file."""
    if path.suffix.lower() != ".zip":
        return path
    with zipfile.ZipFile(path) as z:
        videos = [m for m in z.infolist() if m.filename.lower().endswith(VIDEO_EXT)]
        biggest = max(videos, key=lambda m: m.file_size)
        target = path.parent / Path(biggest.filename).name
        if not target.exists():
            with z.open(biggest) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    return target


def probe(ffmpeg: str, path: Path) -> dict:
    # `ffmpeg -i` with no output exits non-zero, but prints the stream info we need
    err = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err).groups()
    w, ht = re.search(r"Video: [^\n]*?, (\d{2,5})x(\d{2,5})", err).groups()
    return {"duration": int(h) * 3600 + int(m) * 60 + float(s),
            "width": int(w), "height": int(ht), "audio": "Audio:" in err}


def make_placeholder(ffmpeg: str, dest: Path, seconds: int):
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=24:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(dest)], check=True)


def transcode_hls(ffmpeg: str, src: Path, out_dir: Path, info: dict) -> list[dict]:
    rungs = [r for r in LADDER if r[1] <= info["width"]] or LADDER[:1]
    if out_dir.exists():
        shutil.rmtree(out_dir)
    for name, _, _ in rungs:
        (out_dir / name).mkdir(parents=True)

    n = len(rungs)
    graph = f"[0:v]split={n}" + "".join(f"[s{i}]" for i in range(n)) + ";" + \
        ";".join(f"[s{i}]scale={w}:-2[v{i}]" for i, (_, w, _) in enumerate(rungs))
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-stats",
           "-i", str(src.resolve()), "-filter_complex", graph]
    stream_map = []
    for i, (name, _, kbps) in enumerate(rungs):
        cmd += ["-map", f"[v{i}]", f"-c:v:{i}", "libx264", f"-b:v:{i}", f"{kbps}k",
                f"-maxrate:v:{i}", f"{int(kbps * 1.1)}k", f"-bufsize:v:{i}", f"{kbps * 2}k"]
        entry = f"v:{i}"
        if info["audio"]:
            cmd += ["-map", "0:a:0"]
            entry += f",a:{i}"
        stream_map.append(f"{entry},name:{name}")
    if info["audio"]:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]
    cmd += ["-preset", "veryfast", "-pix_fmt", "yuv420p",
            # a keyframe at every segment boundary, so the player can switch renditions cleanly
            "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})", "-sc_threshold", "0",
            "-f", "hls", "-hls_time", str(SEGMENT_SECONDS), "-hls_playlist_type", "vod",
            "-hls_segment_filename", "%v/seg_%03d.ts", "-master_pl_name", "master.m3u8",
            "-var_stream_map", " ".join(stream_map), "%v/index.m3u8"]
    # relative output paths keep the master playlist in out_dir and its URIs relative
    subprocess.run(cmd, check=True, cwd=out_dir)
    if not (out_dir / "master.m3u8").exists():
        raise RuntimeError(f"ffmpeg did not write {out_dir / 'master.m3u8'}")
    return [{"name": name, "width": w, "kbps": kbps} for name, w, kbps in rungs]


def run(films=None, placeholder=False, keep_source=False, root=None, seconds=90, log=print) -> dict:
    ffmpeg = find_ffmpeg()
    base = media_dir(root)
    src_dir, hls_dir = base / "source", base / "hls"
    src_dir.mkdir(parents=True, exist_ok=True)
    hls_dir.mkdir(parents=True, exist_ok=True)
    assets = {a["id"]: a for a in load_manifest(base)["assets"]}

    for film in films or FILMS:
        log(f"{film['title']}:")
        used_placeholder = placeholder
        leftovers = []
        if not placeholder:
            try:
                downloaded = download(film["url"], src_dir / film["url"].rsplit("/", 1)[-1], log)
                src = unpack(downloaded)
                leftovers = [downloaded, src]
            except (OSError, urllib.error.URLError, zipfile.BadZipFile) as e:
                log(f"  download failed ({e}); generating a placeholder clip instead")
                used_placeholder = True
        if used_placeholder:
            src = src_dir / f"{film['id']}_placeholder.mp4"
            make_placeholder(ffmpeg, src, seconds)
            leftovers = [src]

        info = probe(ffmpeg, src)
        log(f"  {info['width']}x{info['height']}, {info['duration'] / 60:.1f} min -> HLS")
        renditions = transcode_hls(ffmpeg, src, hls_dir / film["id"], info)
        assets[film["id"]] = {
            "id": film["id"],
            "title": "Test pattern" if used_placeholder else film["title"],
            "credit": "Placeholder clip generated with ffmpeg" if used_placeholder else film["credit"],
            "duration": round(info["duration"], 2),
            "renditions": renditions,
            "path": f"{film['id']}/master.m3u8",
            "placeholder": used_placeholder,
        }
        if not keep_source:
            for f in set(leftovers):
                f.unlink(missing_ok=True)

        # keep the film list order so titles map to the same film on every run
        order = {f["id"]: i for i, f in enumerate(FILMS)}
        manifest = {"assets": sorted(assets.values(), key=lambda a: order.get(a["id"], 99))}
        (base / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log(f"  done: {', '.join(r['name'] for r in renditions)}")
    return manifest


def main():
    ap = argparse.ArgumentParser(description="Download the films and build HLS streams.")
    ap.add_argument("--placeholder", action="store_true",
                    help="skip downloads and generate test-pattern clips")
    ap.add_argument("--only", nargs="+", choices=[f["id"] for f in FILMS], help="only these films")
    ap.add_argument("--keep-source", action="store_true", help="keep the downloaded originals")
    ap.add_argument("--seconds", type=int, default=90, help="placeholder clip length")
    args = ap.parse_args()
    films = [f for f in FILMS if not args.only or f["id"] in args.only]
    manifest = run(films, args.placeholder, args.keep_source, seconds=args.seconds)
    print(f"\n{len(manifest['assets'])} stream(s) ready in {media_dir() / 'hls'}")


if __name__ == "__main__":
    main()
