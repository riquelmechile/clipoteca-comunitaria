"""2.0.2 download smoke and provider regression fixtures; no network or publication."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from clipoteca.providers.base import Candidate, ProviderError, SearchLimits
from clipoteca.providers.dzen import _watch_document
from clipoteca.providers.odysee import Odysee
from clipoteca.providers.ok_ru import _media
from clipoteca.providers.vk_video import VKVideo
from clipoteca.security.download import DownloadError, download_media
from clipoteca.security.hls import download_hls_probe


def public(_host):
    return ["8.8.8.8"]


def mp4():
    return bytes.fromhex("000000186674797069736f6d") + b"X" * 700


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status", [200, 206])
async def test_mp4_prefix_accepts_large_declared_length_and_stops_at_cap(tmp_path, http_status):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["range"] == "bytes=0-63"
        assert request.headers["host"] == "media.example.com"
        headers = {
            "content-type": "video/mp4",
            "content-length": str(len(mp4())),
        }
        if http_status == 206:
            headers["content-range"] = "bytes 0-63/99999999"
        return httpx.Response(http_status, headers=headers, content=mp4())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        path = await download_media(
            "https://media.example.com/file.mp4",
            tmp_path,
            client=client,
            resolver=public,
            max_bytes=64,
            probe_prefix_bytes=64,
        )
    assert path.read_bytes() == mp4()[:64]
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_mp4_prefix_rejects_nonvideo_magic(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(
                206,
                content=b"not media!" * 10,
                headers={"content-type": "video/mp4", "content-range": "bytes 0-63/9000"},
            )
        )
    ) as client:
        with pytest.raises(DownloadError, match="recognized media container"):
            await download_media(
                "https://media.example.com/a.mp4",
                tmp_path,
                client=client,
                resolver=public,
                max_bytes=64,
                probe_prefix_bytes=64,
            )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_mp4_prefix_rejects_nonzero_content_range(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(
                206,
                content=mp4(),
                headers={"content-type": "video/mp4", "content-range": "bytes 10-73/9000"},
            )
        )
    ) as client:
        with pytest.raises(DownloadError, match="Content-Range"):
            await download_media(
                "https://media.example.com/a.mp4",
                tmp_path,
                client=client,
                resolver=public,
                max_bytes=64,
                probe_prefix_bytes=64,
            )


@pytest.mark.asyncio
async def test_hls_probe_accepts_segment_larger_than_byte_cap(tmp_path):
    payload = bytearray(b"X" * 700)
    payload[0] = payload[188] = 0x47
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers["host"]))
        if request.url.path.endswith("/index.m3u8"):
            return httpx.Response(
                200,
                content=b"#EXTM3U\n#EXTINF:2.0,\nsegment.ts\n",
            )
        assert request.url.path == "/segment.ts"
        return httpx.Response(200, content=bytes(payload))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        path = await download_hls_probe(
            "https://cdn.example.com/index.m3u8",
            tmp_path,
            client=client,
            resolver=public,
            max_bytes=400,
            max_duration=20.0,
            allowed_hosts=("cdn.example.com",),
        )
    assert path.stat().st_size == 400
    assert seen == [("/index.m3u8", "cdn.example.com"), ("/segment.ts", "cdn.example.com")]


@pytest.mark.asyncio
async def test_hls_probe_blocks_external_segment_before_download(tmp_path):
    def handler(request):
        if request.url.path == "/index.m3u8":
            return httpx.Response(
                200,
                content=b"#EXTM3U\n#EXTINF:2.0,\nhttps://evil.example/part.ts\n",
            )
        pytest.fail("must not fetch untrusted segment")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Exception, match="HLS URI host or scheme not allowed"):
            await download_hls_probe(
                "https://cdn.example.com/index.m3u8",
                tmp_path,
                client=client,
                resolver=public,
                max_bytes=400,
                allowed_hosts=("cdn.example.com",),
            )
    assert list(tmp_path.iterdir()) == []


def test_ok_ru_live_shape_hls_manifest_inside_flashvars_metadata():
    fixture = {
        "flashvars": {
            "metadata": {
                "movie": {"id": "123456", "title": "Rain"},
                "hlsManifestUrl": "https://vd621.okcdn.ru/video.m3u8?token=fictional",
                "videos": [{"name": "mobile", "url": "https://vd621.okcdn.ru/?signed=fake"}],
            }
        }
    }
    assert _media([fixture]).startswith("https://vd621.okcdn.ru/video.m3u8")


def test_ok_ru_signed_extensionless_video_quality_fallback():
    fixture = {
        "flashvars": {
            "metadata": {
                "videos": [{"name": "mobile", "url": "https://vd621.okcdn.ru/?signed=fake"}],
            }
        }
    }
    assert _media([fixture]) == "https://vd621.okcdn.ru/?signed=fake"
    fixture["flashvars"]["metadata"]["videos"][0]["url"] = (
        "https://vd621.okcdn.ru.attacker.test/?signed=fake"
    )
    assert _media([fixture]) == ""


@pytest.mark.asyncio
async def test_vk_skips_region_restricted_first_result_and_allows_real_okcdn(monkeypatch):
    from clipoteca.providers import vk_video

    monkeypatch.setattr(vk_video, "_TOKEN", ("anon", 9999))
    locked = {
        "owner_id": -2000814210,
        "id": 83814210,
        "title": "Rain locked",
        "content_restricted": 2,
        "restriction": {"can_play": 0},
    }
    playable = {
        "owner_id": -7,
        "id": 42,
        "title": "Rain playable",
        "files": {"hls": "https://vkvd478.okcdn.ru/video.m3u8?key=fake"},
    }

    def handler(req):
        return httpx.Response(200, json={"response": {"items": [locked, playable]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        vk = VKVideo(client=client, clock=lambda: 0)
        rows = await vk.search("rain", "en", SearchLimits(1, 1, 5))
        assert [row.provider_id for row in rows] == ["-7_42"]
        media = await vk.direct_media(rows[0])
    assert media and media.mime == "application/vnd.apple.mpegurl"
    assert media.url.startswith("https://vkvd478.okcdn.ru/")
    assert "okcdn.ru" in media.allowed_hosts


@pytest.mark.asyncio
async def test_dzen_redirect_within_allowlist_and_ssrf_recheck():
    calls = []
    dns = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("/abc123"):
            return httpx.Response(302, headers={"location": "/video/watch/def456"})
        return httpx.Response(200, text="valid-video-page")

    def resolver(host):
        dns.append(host)
        return ["8.8.8.8"]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        doc = await _watch_document(client, "https://dzen.ru/video/watch/abc123", resolver=resolver)
    assert doc == "valid-video-page"
    assert calls == ["/video/watch/abc123", "/video/watch/def456"]
    assert dns == ["dzen.ru", "dzen.ru"]


@pytest.mark.asyncio
async def test_dzen_rejects_external_sso_and_redirect_loop():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(302, headers={"location": "https://sso.passport.yandex.ru/auth"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="host not allowed"):
            await _watch_document(client, "https://dzen.ru/video/watch/abc123")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_odysee_large_claim_still_resolves_public_stream(monkeypatch):
    seen = []

    async def rpc(_method, _params):
        seen.append(_method)
        return {"streaming_url": "https://player.odycdn.com/abc/video.mp4"}

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as client:
        adapter = Odysee(client=client)
        monkeypatch.setattr(adapter, "_rpc", rpc)
        candidate = Candidate(
            "odysee",
            "a" * 40,
            "rain",
            "en",
            "https://odysee.com/rain",
            "Rain",
            "en",
            metadata={"uri": "lbry://rain#a", "size_bytes": 400 * 1024 * 1024},
        )
        assert await adapter.direct_media(candidate) is None
        media = await adapter.smoke_direct_media(candidate)
    assert seen == ["get"]
    assert media and media.size_bytes == 400 * 1024 * 1024
    assert media.allowed_hosts == ("player.odycdn.com",)


@pytest.mark.asyncio
async def test_library_of_congress_uses_advertised_45_second_timeout():
    path = Path(__file__).parents[1] / "scripts/smoke_sources.py"
    spec = importlib.util.spec_from_file_location("smoke_202", path)
    assert spec and spec.loader
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    timeouts = []

    class Adapter:
        accepts_text_languages = ("en",)
        smoke_timeout_seconds = 45.0

        async def search(self, _term, _lang, limits):
            timeouts.append(limits.timeout_seconds)
            return []

    result = await smoke.download_probe(
        SimpleNamespace(name="library_of_congress", requires=(), adapter=Adapter)
    )
    assert result["status"] == "NO_CANDIDATE"
    assert timeouts == [45.0]
