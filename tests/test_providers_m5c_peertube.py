"""PeerTube federated search and actual /videos UUID response fixtures (2026-11-20)."""

import hashlib
import subprocess
from pathlib import Path

import httpx
import pytest

from clipoteca.catalog import Catalog
from clipoteca.ingest import ingest
from clipoteca.providers.base import ProviderError, ProviderTimeout, RateLimited, SearchLimits
from clipoteca.providers.peertube import PeerTube
from clipoteca.storage.github import ReleaseAsset, ReleaseBatch


UUID = "84ed4b77-a3e5-411f-8723-026106481b6e"
HOST = "pt.na4.eu"
WATCH = "https://" + HOST + "/videos/watch/" + UUID
FILE = "https://" + HOST + "/static/streaming-playlists/hls/rain-480-fragmented.mp4"


def make_client(*, files=True, status=200, other_host=False):
    def serve(req):
        if req.url.path == "/api/v1/search/videos":
            if status != 200:
                return httpx.Response(status, json={"error": "bad"}, headers={"Retry-After": "0"})
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "data": [
                        {
                            "uuid": UUID,
                            "name": "Rain in the forest",
                            "duration": 301,
                            "url": ("https://127.0.0.1" if other_host else "https://" + HOST)
                            + "/videos/watch/"
                            + UUID,
                            "language": {"id": "en", "label": "English"},
                        }
                    ],
                },
            )
        if req.url.path == "/api/v1/videos/" + UUID:
            return httpx.Response(
                200,
                json={
                    "uuid": UUID,
                    "duration": 301,
                    "name": "Rain in the forest",
                    "files": [],
                    "streamingPlaylists": [
                        {
                            "playlistUrl": "https://" + HOST + "/static/foo.m3u8",
                            "files": (
                                [
                                    {
                                        "fileUrl": FILE,
                                        "size": 12000000,
                                        "resolution": {"id": 480},
                                        "hasVideo": True,
                                        "width": 854,
                                        "height": 480,
                                    }
                                ]
                                if files
                                else []
                            ),
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"error": "unknown"})

    return httpx.AsyncClient(transport=httpx.MockTransport(serve))


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ("es", "en", "zh", "ru"))
async def test_peertube_sepias_federated_search_four_languages(lang):
    adapter = PeerTube(instances=(HOST,), client=make_client())
    clips = await adapter.search("lluvia", lang, SearchLimits())
    assert len(clips) == 1
    assert clips[0].source_url == WATCH
    assert clips[0].source_language == "en"
    assert clips[0].language == lang
    ref = await adapter.direct_media(clips[0])
    assert ref and ref.url == FILE and ref.mime == "video/mp4"


@pytest.mark.asyncio
async def test_peertube_missing_mp4_is_search_only_not_a_fake_download():
    adapter = PeerTube(instances=(HOST,), client=make_client(files=False))
    c = (await adapter.search("rain", "en", SearchLimits()))[0]
    assert await adapter.direct_media(c) is None


@pytest.mark.asyncio
async def test_peer_federation_cannot_trigger_private_or_unconfigured_instance_fetch():
    adapter = PeerTube(instances=(HOST,), client=make_client(other_host=True))
    assert await adapter.search("rain", "en", SearchLimits()) == []
    with pytest.raises(ValueError):
        PeerTube(instances=("127.0.0.1",), search_endpoint="http://127.0.0.1/private")


@pytest.mark.asyncio
@pytest.mark.parametrize("status,exc", [(429, RateLimited), (500, ProviderError)])
async def test_peertube_error_status(status, exc):
    adapter = PeerTube(instances=(HOST,), client=make_client(status=status))
    with pytest.raises(exc):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_peertube_timeout_is_typed():
    def timeout(_request):
        raise httpx.ReadTimeout("timeout")

    adapter = PeerTube(
        instances=(HOST,), client=httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    )
    with pytest.raises(ProviderTimeout):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_peertube_direct_mp4_through_real_ffmpeg_pipeline_and_fake_release(tmp_path):
    class Writer:
        def __init__(self):
            self.clips = []

        def read_catalog(self):
            return Catalog(self.clips), "sha"

        def update(self, clips, **kwargs):
            self.clips.extend(clips)
            return "sha"

    class Storage:
        def __init__(self):
            self.assets = {}

        def get_or_create_batch_release(self, day):
            return ReleaseBatch(42, "batch-2026-11-20-001", "https://uploads.github.com")

        def upload_asset(self, release, path, name):
            item = ReleaseAsset(
                len(self.assets) + 1,
                name,
                Path(path).stat().st_size,
                "uploaded",
                "https://github.com/org/repo/releases/download/batch/" + name,
            )
            self.assets[name] = item
            return item

        def verify_asset(self, name, size, digest, release_id):
            assert hashlib.sha256(Path(self.files[name]).read_bytes()).hexdigest() == digest
            return self.assets[name]

    class VerifiedStorage(Storage):
        def upload_asset(self, release, path, name):
            if not hasattr(self, "files"):
                self.files = {}
            self.files[name] = str(path)
            return super().upload_asset(release, path, name)

    async def fetch(_url, destination):
        video = destination / "video.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=12:duration=0.5",
                "-c:v",
                "libx264",
                str(video),
            ],
            check=True,
            timeout=20,
        )
        return video

    writer = Writer()
    adapter = PeerTube(instances=(HOST,), client=make_client())
    result = await ingest(
        "lluvia",
        [adapter],
        repository="fake/repo",
        token="fake",
        job_root=tmp_path / "jobs",
        writer=writer,
        storage=VerifiedStorage(),
        downloader=fetch,
        max_results=1,
        translator=lambda text, src, dst: {
            "es": "lluvia",
            "en": "rain",
            "zh": "下雨",
            "ru": "дождь",
        }[dst],
    )
    assert result.status == "published", result.errors
    assert result.published_ids == ("clip-000001",)
    assert writer.clips[0]["source"]["provider"] == "peertube"
    assert writer.clips[0]["media"]["duration_seconds"] <= 20
