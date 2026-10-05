"""Video: placeholder ingest -> HLS on disk -> the play endpoint."""
import pytest

from backend import video
from media import ingest, library


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


def test_play_endpoint_without_media(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "MEDIA_DIR", tmp_path)
    info = video.play(1, profile_id=None)
    assert info["available"] is False and "media.ingest" in info["hint"]


def test_play_unknown_title_is_404():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as err:
        video.play(99999, profile_id=None)
    assert err.value.status_code == 404
