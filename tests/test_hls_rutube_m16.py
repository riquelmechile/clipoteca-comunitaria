"""M16 secure HLS fixtures and RuTube search/stream resolution, no network."""

from __future__ import annotations

import subprocess

import httpx
import pytest

from clipoteca.providers.base import Candidate, ProviderError, RateLimited, SearchLimits
from clipoteca.providers.rutube import Rutube
from clipoteca.security.download import DownloadError
from clipoteca.security.hls import (
    HLS_MIME_TYPES,
    choose_variant,
    download_hls,
    parse_master,
    parse_media,
)
from clipoteca.security.ssrf import UnsafeMediaURL


VIDEO = "8411dde9e6521796b6805e5966ad4297"
PUBLIC = "https://video.example.com"


def async_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def public_resolver(_host):
    return ["8.8.8.8"]


def test_master_selects_cheapest_720p_or_best_available():
    body = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=500000,RESOLUTION=640x360
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1600000,RESOLUTION=1280x720
medium.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080
high.m3u8
"""
    variants = parse_master(body)
    assert choose_variant(variants) == "medium.m3u8"
    assert choose_variant([variants[0], variants[2]]) == "high.m3u8"
    assert parse_media("#EXTM3U\n#EXTINF:2.0,\nsegment.ts\n") == [(2.0, "segment.ts")]
    with pytest.raises(DownloadError, match="key"):
        parse_media('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.key"\n')
    with pytest.raises(DownloadError, match="byte-range"):
        parse_media("#EXTM3U\n#EXT-X-BYTERANGE:100@0\n#EXTINF:1,\na.ts\n")


@pytest.mark.asyncio
async def test_hls_m3u8_to_mp4_with_mock_http_and_real_ffmpeg(tmp_path):
    source = tmp_path / "segment.ts"
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
            "color=c=blue:s=160x90:r=12:d=2",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-f",
            "mpegts",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert request.headers["referer"] == "https://rutube.ru/video/demo/"
        if request.url.path == "/master.m3u8":
            return httpx.Response(
                200,
                text="""#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=500000,RESOLUTION=640x360
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1600000,RESOLUTION=1280x720
medium.m3u8
""",
            )
        if request.url.path == "/medium.m3u8":
            return httpx.Response(
                200,
                text="#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2.0,\nsegment.ts\n",
            )
        if request.url.path == "/segment.ts":
            return httpx.Response(200, content=source.read_bytes())
        raise AssertionError(f"unwanted HTTP path: {request.url.path}")

    async with async_client(handler) as client:
        output = await download_hls(
            PUBLIC + "/master.m3u8",
            tmp_path,
            referer="https://rutube.ru/video/demo/",
            allowed_hosts=("video.example.com",),
            max_duration=2,
            max_bytes=5 * 1024 * 1024,
            client=client,
            resolver=public_resolver,
        )
    assert output.exists() and output.suffix == ".mp4"
    assert output.stat().st_size > 100
    assert "/low.m3u8" not in seen
    assert "/medium.m3u8" in seen
    assert len(seen) == 3
    assert "application/vnd.apple.mpegurl" in HLS_MIME_TYPES


@pytest.mark.asyncio
async def test_hls_rejects_private_ip_segment_before_fetch(tmp_path):
    def handler(request: httpx.Request):
        assert request.url.path == "/list.m3u8"
        return httpx.Response(
            200,
            text="#EXTM3U\n#EXTINF:3.0,\nhttps://127.0.0.1/secret.ts\n",
        )

    async with async_client(handler) as client:
        with pytest.raises(UnsafeMediaURL, match="host"):
            await download_hls(
                PUBLIC + "/list.m3u8",
                tmp_path,
                client=client,
                resolver=public_resolver,
            )


@pytest.mark.asyncio
async def test_hls_rejects_redirect_to_other_host(tmp_path):
    def handler(request: httpx.Request):
        return httpx.Response(302, headers={"location": "https://evil.example.invalid/secret.m3u8"})

    async with async_client(handler) as client:
        with pytest.raises(DownloadError, match="blocked or redirected"):
            await download_hls(
                PUBLIC + "/list.m3u8",
                tmp_path,
                client=client,
                resolver=public_resolver,
            )


@pytest.mark.asyncio
async def test_hls_aggregate_byte_cap_and_scheme_gate(tmp_path):
    def handler(request: httpx.Request):
        if request.url.path == "/list.m3u8":
            return httpx.Response(200, text="#EXTM3U\n#EXTINF:2.0,\nsegment.ts\n")
        return httpx.Response(200, content=b"0" * 200)

    async with async_client(handler) as client:
        with pytest.raises(DownloadError, match="cap"):
            await download_hls(
                PUBLIC + "/list.m3u8",
                tmp_path,
                client=client,
                resolver=public_resolver,
                max_bytes=100,
            )
        with pytest.raises(UnsafeMediaURL):
            await download_hls(
                "file:///etc/passwd",
                tmp_path,
                client=client,
                resolver=public_resolver,
            )


@pytest.mark.asyncio
async def test_rutube_pagination_same_origin_only():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.params.get("page") == "1":
            assert request.url.params["limit"] == "30"
            return httpx.Response(
                200,
                json={
                    "results": [{"id": VIDEO, "title": "Дождь"}],
                    "next": "/api/search/video/?query=rain&page=2&limit=30",
                },
            )
        return httpx.Response(
            200,
            json={"results": [{"id": "11111111111111111111111111111111", "title": "rain"}]},
        )

    async with async_client(handler) as client:
        found = await Rutube(client=client).search("rain", "ru", SearchLimits(max_pages=2))
    assert len(found) == 2
    assert paths == ["/api/search/video/", "/api/search/video/"]


@pytest.mark.asyncio
async def test_rutube_detail_blocking_and_missing_video_balancer():
    def blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"detail": "Access blocked"})

    async with async_client(blocked) as client:
        with pytest.raises(RateLimited):
            await Rutube(client=client).search("дождь", "ru", SearchLimits())

    def missing(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mq"] == "all"
        assert request.headers["referer"].startswith("https://rutube.ru/video/")
        return httpx.Response(200, json={"video_balancer": {}})

    async with async_client(missing) as client:
        candidate = Candidate(
            "rutube",
            VIDEO,
            "rain",
            "en",
            "https://rutube.ru/video/" + VIDEO + "/",
            "Rain",
            "en",
        )
        assert await Rutube(client=client).direct_media(candidate) is None


@pytest.mark.asyncio
async def test_rutube_refuses_cross_origin_next():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [], "next": "https://evil.example.invalid/capture"},
        )

    async with async_client(handler) as client:
        with pytest.raises(ProviderError, match="pagination origin"):
            await Rutube(client=client).search("rain", "en", SearchLimits(max_pages=2))
