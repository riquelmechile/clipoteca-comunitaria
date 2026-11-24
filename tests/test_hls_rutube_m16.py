"""M16 secure HLS fixtures and RuTube search/stream resolution, no network."""

from __future__ import annotations

import subprocess

import httpx
import pytest

from clipoteca.providers.base import Candidate, ProviderError, RateLimited, SearchLimits
from clipoteca.providers.rutube import Rutube, RUTUBE_HLS_ALLOWED_HOSTS
from clipoteca.security.download import DownloadError
from clipoteca.security.hls import (
    HLS_MIME_TYPES,
    choose_variant,
    download_hls,
    ffmpeg_hls_extension_capable,
    _fetch,
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


@pytest.mark.skipif(
    not ffmpeg_hls_extension_capable(), reason="FFmpeg lacks HLS allowed_segment_extensions"
)
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
        with pytest.raises(UnsafeMediaURL, match="private or special-use"):
            await download_hls(
                PUBLIC + "/list.m3u8",
                tmp_path,
                client=client,
                allowed_hosts=("127.0.0.1",),
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


@pytest.mark.asyncio
async def test_hls_500_segments_downloads_only_cut_window(monkeypatch, tmp_path):
    monkeypatch.setattr("clipoteca.security.hls.ffmpeg_hls_extension_capable", lambda: True)
    """500 segments of a VOD must not exhaust limits for a 20-second cut."""
    seen = []
    commands = []
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:4"]
    for index in range(500):
        lines.extend(("#EXTINF:4.0,", f"piece-{index:04d}.ts"))
    playlist = "\n".join(lines) + "\n"

    def handler(request):
        seen.append((request.url.path, request.url.host, request.headers.get("host")))
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text=playlist)
        assert request.url.path.startswith("/piece-")
        return httpx.Response(200, content=b"TS_BYTES")

    def fake_run(command, **_kwargs):
        commands.append(command)
        from pathlib import Path

        Path(command[-1]).write_bytes(b"synthetic_mp4_for_bounded_test")
        return subprocess.CompletedProcess(command, 0)

    async def fake_remux(command):
        fake_run(command)

    monkeypatch.setattr("clipoteca.security.hls._run_remux", fake_remux)
    async with async_client(handler) as client:
        result = await download_hls(
            PUBLIC + "/vod.m3u8",
            tmp_path,
            max_duration=20,
            max_bytes=500000,
            client=client,
            resolver=public_resolver,
        )
    assert result.exists()
    assert len(parse_media(playlist)) == 500
    assert [p for p, _ip, _host in seen] == [
        "/vod.m3u8",
        *[f"/piece-{index:04d}.ts" for index in range(5)],
    ]
    assert all(ip == "8.8.8.8" and host == "video.example.com" for _, ip, host in seen)
    assert commands and commands[0][commands[0].index("-t") + 1] == "20"


@pytest.mark.asyncio
async def test_hls_30_second_single_segment_is_fetched_then_trimmed(monkeypatch, tmp_path):
    monkeypatch.setattr("clipoteca.security.hls.ffmpeg_hls_extension_capable", lambda: True)
    commands = []
    responses = []

    def handler(request):
        responses.append(request.url.path)
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text="#EXTM3U\n#EXTINF:30.0,\nlong.ts\n")
        return httpx.Response(200, content=b"TS_BYTES")

    def fake_run(command, **_kwargs):
        commands.append(command)
        from pathlib import Path

        Path(command[-1]).write_bytes(b"bounded test output")
        return subprocess.CompletedProcess(command, 0)

    async def fake_remux(command):
        fake_run(command)

    monkeypatch.setattr("clipoteca.security.hls._run_remux", fake_remux)
    async with async_client(handler) as client:
        await download_hls(
            PUBLIC + "/one.m3u8",
            tmp_path,
            client=client,
            resolver=public_resolver,
            max_duration=20,
        )
    assert responses == ["/one.m3u8", "/long.ts"]
    cmd = commands[0]
    assert cmd[cmd.index("-t") + 1] == "20"
    assert cmd[cmd.index("-allowed_segment_extensions") + 1] == "ts"
    assert "-allowed_extensions" not in cmd
    assert "ALL" not in cmd


@pytest.mark.asyncio
async def test_hls_start_offset_selects_intersecting_segments(monkeypatch, tmp_path):
    monkeypatch.setattr("clipoteca.security.hls.ffmpeg_hls_extension_capable", lambda: True)
    paths = []
    commands = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(
                200, text=("#EXTM3U\n#EXTINF:10,\na.ts\n#EXTINF:10,\nb.ts\n#EXTINF:10,\nc.ts\n")
            )
        return httpx.Response(200, content=b"TS")

    def fake_run(command, **_kwargs):
        commands.append(command)
        from pathlib import Path

        Path(command[-1]).write_bytes(b"trimmed MP4")
        return subprocess.CompletedProcess(command, 0)

    async def fake_remux(command):
        fake_run(command)

    monkeypatch.setattr("clipoteca.security.hls._run_remux", fake_remux)
    async with async_client(handler) as client:
        await download_hls(
            PUBLIC + "/offset.m3u8",
            tmp_path,
            start=18,
            max_duration=4,
            client=client,
            resolver=public_resolver,
        )
    assert paths == ["/offset.m3u8", "/b.ts", "/c.ts"]
    cmd = commands[0]
    assert cmd[cmd.index("-ss") + 1] == "8.0"
    assert cmd[cmd.index("-t") + 1] == "4"


@pytest.mark.asyncio
async def test_hls_peer_mismatch_rejected_even_with_injected_client(tmp_path):
    class Peer:
        def get_extra_info(self, name):
            assert name == "server_addr"
            return ("127.0.0.1", 443)

    def handler(request):
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "video.example.com"
        assert request.extensions["sni_hostname"] == "video.example.com"
        return httpx.Response(
            200, text="#EXTM3U\n#EXTINF:1,\nx.ts\n", extensions={"network_stream": Peer()}
        )

    async with async_client(handler) as client:
        with pytest.raises(DownloadError, match="peer mismatch"):
            await download_hls(
                PUBLIC + "/list.m3u8", tmp_path, client=client, resolver=public_resolver
            )


@pytest.mark.asyncio
async def test_hls_dns_rebinding_denied_before_get(tmp_path):
    calls = []
    count = 0

    def resolver(_host):
        nonlocal count
        count += 1
        return ["8.8.8.8"] if count == 1 else ["10.0.0.1"]

    def handler(request):
        calls.append(request.url.path)
        pytest.fail("HLS request must not reach HTTP transport after DNS rebinding")

    async with async_client(handler) as client:
        with pytest.raises(UnsafeMediaURL, match="private"):
            await download_hls(PUBLIC + "/list.m3u8", tmp_path, client=client, resolver=resolver)
    assert calls == []


@pytest.mark.parametrize(
    "tag",
    [
        '#EXT-X-KEY:METHOD=AES-128,URI="key.key"',
        '#EXT-X-MAP:URI="init.mp4"',
        "#EXT-X-BYTERANGE:1024@0",
    ],
)
@pytest.mark.asyncio
async def test_hls_rejects_key_map_and_byterange_before_media_get(tmp_path, tag):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, text=f"#EXTM3U\n{tag}\n#EXTINF:2.0,\ns.ts\n")

    async with async_client(handler) as client:
        with pytest.raises(DownloadError, match="key or init-map|byte-range"):
            await download_hls(
                PUBLIC + "/list.m3u8", tmp_path, client=client, resolver=public_resolver
            )
    assert seen == ["/list.m3u8"]


@pytest.mark.asyncio
async def test_rutube_http_error_json_detail_classified_as_limited():
    for status, detail in [
        (403, "Access forbidden"),
        (429, "Too many requests"),
        (451, "Geo restricted in this country"),
    ]:
        async with async_client(
            lambda _req: httpx.Response(status, json={"detail": detail})
        ) as client:
            with pytest.raises(RateLimited):
                await Rutube(client=client).search("rain", "en", SearchLimits())
    # Unrelated providers retain their generic HTTP errors.
    from clipoteca.providers.http import fetch_json

    async with async_client(
        lambda _req: httpx.Response(403, json={"detail": "forbidden"})
    ) as client:
        with pytest.raises(ProviderError, match="HTTP 403"):
            await fetch_json(client, "https://rutube.ru/api/other")


@pytest.mark.asyncio
async def test_rutube_invalid_port_and_scoped_cdn_domains():
    candidate = Candidate(
        "rutube", VIDEO, "rain", "en", "https://rutube.ru/video/" + VIDEO + "/", "Rain", "en"
    )
    links = [
        ("https://cdn.rtbcdn.ru:invalid/master.m3u8", None),
        ("https://cdn.rtbcdn.ru/master.m3u8", "application/vnd.apple.mpegurl"),
        ("https://assets.rutube.ru/master.m3u8", "application/vnd.apple.mpegurl"),
        ("https://rutube.ru.evil.example.com/master.m3u8", None),
        ("https://evil.com/master.m3u8", None),
    ]
    for link, mime in links:
        async with async_client(
            lambda _req: httpx.Response(200, json={"video_balancer": {"hls": link}})
        ) as client:
            ref = await Rutube(client=client).direct_media(candidate)
            assert (ref.mime if ref else None) == mime
            if ref:
                assert "rtbcdn.ru" in RUTUBE_HLS_ALLOWED_HOSTS
                assert ".rtbcdn.ru" in ref.allowed_hosts


@pytest.mark.asyncio
async def test_ingest_hls_mime_dispatches_to_bounded_hls_downloader(tmp_path, monkeypatch):
    """Exercise the real ingest dispatch branch, without external services."""
    import importlib
    from types import SimpleNamespace

    from clipoteca.catalog import Catalog
    from clipoteca.providers.base import Adapter, MediaRef, SupportStatus

    module = importlib.import_module("clipoteca.ingest")
    candidate = Candidate(
        "rutube",
        VIDEO,
        "rain",
        "en",
        "https://rutube.ru/video/" + VIDEO + "/",
        "Rain",
        "en",
    )

    class AdapterFixture(Adapter):
        name = "rutube"
        support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

        async def search(self, term, lang, limits):
            return [candidate]

        async def metadata(self, row):
            return {}

        async def direct_media(self, row):
            return MediaRef(
                "https://cdn.rtbcdn.ru/video/master.m3u8",
                "application/vnd.apple.mpegurl",
                referer="https://rutube.ru/video/" + VIDEO + "/",
                allowed_hosts=RUTUBE_HLS_ALLOWED_HOSTS,
            )

    class EngineFixture:
        async def search(self, *args, **kwargs):
            return SimpleNamespace(candidates=[candidate], executions=[], duplicates=False)

    monkeypatch.setattr(module, "SearchEngine", lambda *_args, **_kw: EngineFixture())
    monkeypatch.setattr(
        module,
        "expand_query",
        lambda *_args, **_kw: SimpleNamespace(
            complete=True, errors={}, terms={"en": "rain", "es": "lluvia"}
        ),
    )
    monkeypatch.setattr(module, "discovery_metrics", lambda _search: {})
    monkeypatch.setattr(module, "write_metrics", lambda *_args: None)
    monkeypatch.setattr(
        module,
        "localize_candidate",
        lambda *_args, **_kw: SimpleNamespace(
            title={"en": "Rain", "es": "Lluvia"},
            description={"en": "Rain", "es": "Lluvia"},
            tags={"en": ["rain"], "es": ["lluvia"]},
        ),
    )
    observed = []

    async def hls_stub(url, target_dir, **kwargs):
        observed.append((url, kwargs))
        raise DownloadError("expected HLS path")

    monkeypatch.setattr(module, "download_hls", hls_stub)
    report = await module.ingest(
        "rain",
        [AdapterFixture()],
        repository="riquelmechile/clipoteca-comunitaria",
        token="test-only",
        job_root=tmp_path / "jobs",
        writer=SimpleNamespace(read_catalog=lambda: (Catalog(), None)),
        storage=SimpleNamespace(),
        translator=lambda *_args: "rain",
        filters={"max_duration": 8},
    )
    assert report.status == "failed"
    assert len(observed) == 1
    url, options = observed[0]
    assert url == "https://cdn.rtbcdn.ru/video/master.m3u8"
    assert options["max_duration"] == 8
    assert options["referer"].startswith("https://rutube.ru/video/")
    assert options["allowed_hosts"] == RUTUBE_HLS_ALLOWED_HOSTS


@pytest.mark.asyncio
async def test_hls_retries_next_validated_ip_after_transport_failure():
    attempts = []

    def handler(request):
        attempts.append(request.url.host)
        if request.url.host == "8.8.8.8":
            raise httpx.ConnectError("first validated address unavailable", request=request)
        return httpx.Response(200, content=b"media")

    async with async_client(handler) as client:
        data = await _fetch(
            client,
            PUBLIC + "/media.ts",
            referer=None,
            resolver=lambda _host: ["8.8.8.8", "1.1.1.1"],
            own_client=False,
            max_bytes=100,
        )
    assert data == b"media"
    assert attempts == ["8.8.8.8", "1.1.1.1"]


@pytest.mark.asyncio
async def test_hls_equivalent_ipv6_peer_not_rejected():
    class Peer:
        def get_extra_info(self, name):
            assert name == "server_addr"
            return ("2001:4860:4860:0:0:0:0:8888", 443)

    async with async_client(
        lambda _req: httpx.Response(200, content=b"ok", extensions={"network_stream": Peer()})
    ) as client:
        data = await _fetch(
            client,
            PUBLIC + "/media.ts",
            referer=None,
            resolver=lambda _host: ["2001:4860:4860::8888"],
            own_client=False,
            max_bytes=20,
        )
    assert data == b"ok"


@pytest.mark.asyncio
async def test_rutube_status_classification_without_keyword_detail():
    for code in (403, 451):
        async with async_client(
            lambda _req: httpx.Response(code, json={"detail": "unavailable"})
        ) as client:
            with pytest.raises(RateLimited):
                await Rutube(client=client).search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_rutube_429_retries_retry_after_even_with_json_detail(monkeypatch):
    from clipoteca.providers.http import fetch_json

    pauses = []
    calls = []

    async def fake_sleep(delay):
        pauses.append(delay)

    monkeypatch.setattr("clipoteca.providers.http.asyncio.sleep", fake_sleep)

    def handler(_req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                429, json={"detail": "unavailable"}, headers={"Retry-After": "10"}
            )
        return httpx.Response(200, json={"results": []})

    async with async_client(handler) as client:
        result = await fetch_json(
            client,
            "https://rutube.ru/api/search/video/",
            detail_statuses=frozenset({403, 429, 451}),
            max_retry_after_seconds=0.2,
        )
    assert result == {"results": []}
    assert len(calls) == 2 and pauses == [0.2]
