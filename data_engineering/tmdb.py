"""
Real poster, banner and title-logo art for the catalog, from TMDB.

TMDB (themoviedb.org) has a free API. Put your key in TMDB_API_KEY, or in a .env
file at the project root (see .env.example). Every catalog title is matched by
name, year and type, and its images are saved under data/tmdb/. The app serves
them from there, so one run is enough and everything works offline afterwards.
Titles without a match keep their generated SVG poster.

    python -m data_engineering.tmdb             # fetch whatever is missing
    python -m data_engineering.tmdb --refresh   # match and download everything again

This product uses the TMDB API but is not endorsed or certified by TMDB.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from paths import DB_PATH, ROOT, TMDB_DIR

API = "https://api.themoviedb.org/3"
IMAGES = "https://image.tmdb.org/t/p"
SIZES = {"poster": "w500", "backdrop": "w1280", "logo": "w500"}
ATTRIBUTION = "This product uses the TMDB API but is not endorsed or certified by TMDB."
UA = "NetClone/1.0 (local demo)"

NO_KEY = ("No TMDB API key found. Get a free one at https://www.themoviedb.org/settings/api,\n"
          "then put TMDB_API_KEY=<key> in a .env file at the project root (see .env.example)\n"
          "or set it as an environment variable.")


class TMDBError(RuntimeError):
    pass


def api_key() -> str | None:
    """TMDB_API_KEY from the environment, else from the project's .env file."""
    key = os.environ.get("TMDB_API_KEY", "").strip()
    env_file = ROOT / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "TMDB_API_KEY":
                key = value.strip().strip("'\"")
    return key or None


def _http_get(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(float(e.headers.get("Retry-After") or 1 + attempt))  # rate limited
        except urllib.error.URLError:
            if attempt == 3:
                raise
            time.sleep(1 + attempt)


class TMDB:
    def __init__(self, key: str, http_get=_http_get):
        self.key = key
        self.http_get = http_get

    def get(self, path: str, **params) -> dict:
        headers = {"Accept": "application/json"}
        if self.key.startswith("eyJ"):
            # the long "API Read Access Token" is a JWT and goes in a header;
            # the short v3 "API Key" goes in the query string
            headers["Authorization"] = f"Bearer {self.key}"
        else:
            params["api_key"] = self.key
        try:
            return json.loads(self.http_get(f"{API}{path}?{urllib.parse.urlencode(params)}", headers))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise TMDBError("TMDB rejected the API key (HTTP 401). Check TMDB_API_KEY.") from e
            raise

    def image(self, file_path: str, size: str) -> bytes:
        return self.http_get(f"{IMAGES}/{size}{file_path}", {})

    def find(self, title: str, year: int, content_type: str) -> dict | None:
        if content_type == "movie":
            path, year_filter = "/search/movie", {"year": year}
        else:
            path, year_filter = "/search/tv", {"first_air_date_year": year}
        results = self.get(path, query=title, include_adult="false", **year_filter)["results"]
        if not results:  # the year filter can be too strict (festival vs. streaming release)
            results = self.get(path, query=title, include_adult="false")["results"]
        return best_match(results, title, year)


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower().replace("&", "and"))


def best_match(results: list[dict], title: str, year: int) -> dict | None:
    """The search result that really is this title: same name (or one name
    containing the other, like "Glass Onion: A Knives Out Mystery"), released
    within a year of the catalog's year. None if nothing qualifies, so a
    made-up title doesn't borrow some other film's poster."""
    want = _norm(title)

    def rank(r):
        names = {_norm(r.get(k)) for k in ("title", "name", "original_title", "original_name")} - {""}
        date = r.get("release_date") or r.get("first_air_date") or ""
        if not date[:4].isdigit() or abs(int(date[:4]) - year) > 1:
            return None
        if want in names:
            name_score = 2
        elif any(min(len(n), len(want)) >= 5 and (want in n or n in want) for n in names):
            name_score = 1
        else:
            return None
        return name_score, int(date[:4]) == year, bool(r.get("poster_path")), float(r.get("popularity") or 0)

    scored = [(rank(r), r) for r in results]
    scored = [(s, r) for s, r in scored if s is not None]
    return max(scored, key=lambda x: x[0])[1] if scored else None


def pick_logo(logos: list[dict]) -> str | None:
    """An English (or text-free) title logo, the best-voted one."""
    usable = [lg for lg in logos if lg.get("iso_639_1") in ("en", None)]
    if not usable:
        return None
    best = max(usable, key=lambda lg: (lg.get("iso_639_1") == "en", lg.get("vote_average") or 0,
                                       lg.get("width") or 0))
    return best["file_path"]


def _save(dest: Path, data: bytes):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(data)
    part.replace(dest)


def fetch_title(client: TMDB, t: dict, out: Path, cached: dict | None, refresh: bool) -> dict | None:
    entry = None if refresh else cached
    if entry is None or entry.get("title") != t["title"]:
        hit = client.find(t["title"], t["year"], t["content_type"])
        if hit is None:
            return None
        kind = "movie" if t["content_type"] == "movie" else "tv"
        try:
            images = client.get(f"/{kind}/{hit['id']}/images", include_image_language="en,null")
        except urllib.error.HTTPError:
            images = {}
        entry = {"title": t["title"], "year": t["year"], "tmdb_id": hit["id"], "tmdb_type": kind,
                 "tmdb_name": hit.get("title") or hit.get("name"),
                 "remote": {"poster": hit.get("poster_path"), "backdrop": hit.get("backdrop_path"),
                            "logo": pick_logo(images.get("logos", []))}}
    for kind, remote in entry["remote"].items():
        if not remote:
            continue
        local = f"{kind}/{t['title_id']}{Path(remote).suffix or '.jpg'}"
        if refresh or not (out / local).exists():
            try:
                _save(out / local, client.image(remote, SIZES[kind]))
            except urllib.error.HTTPError:
                continue  # an image TMDB has since removed; the SVG fallback covers it
        entry[kind] = local
    return entry


def catalog_titles() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("SELECT title_id, title, content_type, release_year "
                            "FROM dim_title ORDER BY title_id").fetchall()
    finally:
        conn.close()
    return [{"title_id": r[0], "title": r[1], "content_type": r[2], "year": r[3]} for r in rows]


def load_manifest(root=None) -> dict:
    path = Path(root or TMDB_DIR) / "manifest.json"
    if not path.exists():
        return {"titles": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def run(refresh=False, client=None, root=None, titles=None, log=print) -> dict:
    if client is None:
        key = api_key()
        if not key:
            raise TMDBError(NO_KEY)
        client = TMDB(key)
    client.get("/configuration")  # fails fast on a bad key, before 60 searches do
    out = Path(root or TMDB_DIR)
    if titles is None:
        from data_engineering.pipeline import ensure_warehouse
        ensure_warehouse()
        titles = catalog_titles()

    cached = load_manifest(out)["titles"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = [(t, pool.submit(fetch_title, client, t, out, cached.get(str(t["title_id"])), refresh))
                for t in titles]
    found, missing = {}, []
    for t, job in jobs:
        entry = job.result()
        if entry is None:
            missing.append(t["title"])
            log(f"  {t['title']} ({t['year']}): no match, keeps the generated poster")
            continue
        found[str(t["title_id"])] = entry
        got = [k for k in ("poster", "backdrop", "logo") if entry.get(k)]
        log(f"  {t['title']} ({t['year']}): {', '.join(got) or 'no images'}")

    manifest = {"source": "TMDB", "attribution": ATTRIBUTION,
                "updated": datetime.now().isoformat(timespec="seconds"),
                "titles": found, "unmatched": missing}
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    ap = argparse.ArgumentParser(description="Download poster, banner and logo art from TMDB.")
    ap.add_argument("--refresh", action="store_true", help="match and download everything again")
    args = ap.parse_args()
    try:
        manifest = run(refresh=args.refresh)
    except TMDBError as e:
        sys.exit(str(e))
    except urllib.error.URLError as e:
        sys.exit(f"Couldn't reach TMDB ({e.reason}). Check your internet connection.")
    titles = manifest["titles"].values()
    print(f"\n{len(manifest['titles'])} titles matched, {len(manifest['unmatched'])} without a match. "
          f"Posters: {sum(1 for t in titles if t.get('poster'))}, "
          f"banners: {sum(1 for t in titles if t.get('backdrop'))}, "
          f"logos: {sum(1 for t in titles if t.get('logo'))}.")
    print(ATTRIBUTION)


if __name__ == "__main__":
    main()
