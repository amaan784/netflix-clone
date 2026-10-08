"""Video: placeholder ingest -> HLS on disk (plus preview thumbnails and stills) -> the play endpoint."""
import pytest

from backend import episodes, video
from media import extras, ingest, library


@pytest.fixture(scope="module")
def media_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("media")
    ingest.run(library.FILMS[:2], placeholder=True, root=root, seconds=6, log=lambda *a: None)
    return root


def test_ingest_writes_multi_bitrate_hls(media_root):
    assets = library.load_manifest(media_root)["assets"]
    assert [a["id"] for a in assets] == [f["id"] for f in library.FILMS[:2]]
    for a in assets:
        master = (media_root / "hls" / a["path"]).read_text()
        assert master.count("#EXT-X-STREAM-INF") == len(a["renditions"]) == 3
        assert a["duration"] == pytest.approx(6, abs=1)
        for r in a["renditions"]:
            playlist = media_root / "hls" / a["id"] / r["name"] / "index.m3u8"
            assert "#EXT-X-ENDLIST" in playlist.read_text()


def test_sources_are_removed_after_ingest(media_root):
    assert not any((media_root / "source").iterdir())


def test_titles_spread_over_films(media_root):
    manifest = library.load_manifest(media_root)
    films = {library.asset_for_title(t, manifest)["id"] for t in range(1, 11)}
    assert len(films) == 2


def test_play_endpoint(media_root, monkeypatch):
    monkeypatch.setattr(library, "MEDIA_DIR", media_root)
    info = video.play(1, profile_id=1)
    assert info["available"] and info["src"].endswith("/master.m3u8")
    assert 0 <= info["resume_at"] < info["duration"]
    assert info["thumbnails"].endswith("/thumbs.vtt")
    # placeholder clips have no dialogue and no opening titles
    assert info["subtitles"] == [] and info["intro"] is None and info["credits"] is None


def test_play_a_series_episode(media_root, monkeypatch):
    monkeypatch.setattr(library, "MEDIA_DIR", media_root)
    eps = episodes.for_title(1)  # title 1 is a series
    info = video.play(1, profile_id=None, episode_id=eps[1]["episode_id"])
    assert info["episode"]["episode_id"] == eps[1]["episode_id"] and info["episode"]["label"] == "S1:E2"
    assert info["next_episode"]["episode_id"] == eps[2]["episode_id"]
    with pytest.raises(Exception):
        video.play(1, profile_id=None, episode_id=10**6)  # not one of its episodes


def test_preview_thumbnails_cover_the_clip(media_root):
    asset = library.load_manifest(media_root)["assets"][0]
    vtt = (media_root / "hls" / asset["thumbnails"]).read_text()
    cues = [line for line in vtt.splitlines() if "-->" in line]
    assert vtt.startswith("WEBVTT") and len(cues) == 2  # a 6 s clip, one frame every 4 s
    assert "thumbs/sheet_01.jpg#xywh=160,0,160,90" in vtt
    assert (media_root / "hls" / asset["id"] / "thumbs" / "sheet_01.jpg").stat().st_size > 0
    assert asset["stills"] and all((media_root / "hls" / s).exists() for s in asset["stills"])


def test_srt_to_vtt():
    srt = ("1\r\n00:00:23,000 --> 00:00:24,500\r\nYou're a jerk, Thom.\r\n\r\n"
           "2\r\n00:00:25,000 -> 00:00:27,000\r\nLook Celia...\r\n")
    assert extras.srt_to_vtt(srt) == ("WEBVTT\n\n1\n00:00:23.000 --> 00:00:24.500\nYou're a jerk, Thom.\n\n"
                                      "2\n00:00:25.000 --> 00:00:27.000\nLook Celia...\n")


def test_episode_stills_come_from_the_film(media_root, monkeypatch):
    monkeypatch.setattr(library, "MEDIA_DIR", media_root)
    path = library.still_for_episode(1, 7)
    assert path is not None and path.exists() and path.parent.name == "stills"


def test_play_endpoint_without_media(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "MEDIA_DIR", tmp_path)
    info = video.play(1, profile_id=None)
    assert info["available"] is False and "media.ingest" in info["hint"]


def test_play_unknown_title_is_404():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as err:
        video.play(99999, profile_id=None)
    assert err.value.status_code == 404
