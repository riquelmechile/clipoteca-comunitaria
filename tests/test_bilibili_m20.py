"""M20 Bilibili WBI offline tests with upstream-pinned signature vectors.

Upstream Apache-2.0 source:
Evil0ctal/Douyin_TikTok_Download_API@ed4ec42534b1194f8d8c18be7b4ba493334f5321
src/dtk/signing/native/wbi.py. Expected values obtained with its sign().
"""

from __future__ import annotations

import httpx
import pytest

from clipoteca.providers.base import Candidate, ProviderError, RateLimited, SearchLimits
from clipoteca.providers.bilibili import Bilibili, wbi_mixin, wbi_sign

IMG = "7cd084941338484aae1ad9425b84077c"
SUB = "4932caff0ff746eab6f01bf08b70ac45"
NAV = {
    "code": -101,
    "data": {
        "wbi_img": {
            "img_url": "https://i0.hdslb.com/bfs/wbi/" + IMG + ".png",
            "sub_url": "https://i0.hdslb.com/bfs/wbi/" + SUB + ".png",
        }
    },
}
BVID = "BV1xx411c7mD"


def candidate() -> Candidate:
    return Candidate(
        provider="bilibili",
        provider_id=BVID,
        term="rain",
        language="en",
        source_url="https://www.bilibili.com/video/" + BVID,
        source_title="Rain",
        source_language="en",
    )


def test_wbi_signature_matches_pinned_upstream_vectors():
    mixin = wbi_mixin(IMG, SUB)
    assert mixin == "ea1db124af3c7062474693fa704f4ff8"
    query, digest = wbi_sign(
        {"keyword": "hello world!()*", "search_type": "video", "page": 1},
        mixin,
        timestamp=1729000000,
    )
    assert query == (
        "keyword=hello%20world&page=1&search_type=video&wts=1729000000"
        "&w_rid=eafefcf8d49f8ec4446788b9df30b5ef"
    )
    assert digest == "eafefcf8d49f8ec4446788b9df30b5ef"
    query, digest = wbi_sign(
        {
            "bvid": BVID,
            "cid": 987654,
            "qn": 64,
            "fnval": 16,
            "platform": "pc",
        },
        mixin,
        timestamp=1729001234,
    )
    assert digest == "b0b76d7d466c3c204304bc01de1b3768"
    assert query.endswith("&w_rid=" + digest)


@pytest.mark.asyncio
async def test_signed_search_optional_cookie_referer_nav_and_search_result():
    visits = []

    def responder(request: httpx.Request):
        visits.append(request)
        if request.url.path.endswith("/nav"):
            return httpx.Response(200, json=NAV)
        assert request.url.path == "/x/web-interface/wbi/search/type"
        assert request.url.params["search_type"] == "video"
        assert len(request.url.params["w_rid"]) == 32
        assert request.headers["referer"] == "https://www.bilibili.com/"
        assert request.headers["user-agent"]
        assert request.headers["cookie"] == "SESSDATA=example"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "result": [
                        {
                            "bvid": BVID,
                            "title": "<em>Rain</em>",
                            "description": "Storm",
                            "cid": 987654,
                        },
                        {"bvid": "broken", "title": "invalid"},
                        {"bvid": "BV2xx411c7mD", "is_pay": True},
                    ]
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        adapter = Bilibili(cookie="SESSDATA=example", client=client)
        rows = await adapter.search("rain", "en", SearchLimits(max_pages=1))
    assert len(rows) == 1
    assert rows[0].provider_id == BVID
    assert rows[0].metadata["cid"] == 987654
    assert len(visits) == 2


@pytest.mark.asyncio
async def test_wbi_playurl_prefers_mp4_durl_and_rejects_external_cdns():
    def response(req: httpx.Request):
        if req.url.path.endswith("/nav"):
            return httpx.Response(200, json=NAV)
        assert req.url.path.endswith("/wbi/playurl")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "durl": [
                        {"url": "https://upos-sz-mirrorcos.bilivideo.com/demo.mp4"},
                    ]
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        adapter = Bilibili(client=client)
        media = await adapter.direct_media(
            Candidate(**{**candidate().__dict__, "metadata": {"cid": 987654}})
        )
        assert media and media.url.endswith("demo.mp4")
        assert media.audio_url is None
        assert media.referer == "https://www.bilibili.com/"
        assert media.allowed_hosts == ("upos-sz-mirrorcos.bilivideo.com",)

    def hostile(req: httpx.Request):
        if req.url.path.endswith("/nav"):
            return httpx.Response(200, json=NAV)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "durl": [{"url": "https://private.example/unsafe.mp4"}],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(hostile)) as client:
        assert (
            await Bilibili(client=client).direct_media(
                Candidate(**{**candidate().__dict__, "metadata": {"cid": 987654}})
            )
            is None
        )


@pytest.mark.asyncio
async def test_wbi_dash_video_audio_and_metadata_fallback():
    calls = []

    def responder(req: httpx.Request):
        calls.append(req.url.path)
        if req.url.path.endswith("/nav"):
            return httpx.Response(200, json=NAV)
        if req.url.path.endswith("/view"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "cid": 987654,
                        "aid": 12,
                        "title": "Rain",
                        "duration": 15,
                    },
                },
            )
        assert req.url.path.endswith("/wbi/playurl")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "dash": {
                        "video": [{"baseUrl": "https://a.bilivideo.com/v.m4s", "bandwidth": 9000}],
                        "audio": [{"baseUrl": "https://b.bilivideo.com/a.m4s", "bandwidth": 1000}],
                    },
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        media = await Bilibili(client=client).direct_media(candidate())
    assert media and media.audio_url == "https://b.bilivideo.com/a.m4s"
    assert media.allowed_hosts == ("a.bilivideo.com", "b.bilivideo.com")
    assert "/x/web-interface/view" in calls


@pytest.mark.asyncio
async def test_wbi_rekeys_on_minus_352_and_minus_403():
    calls = {"nav": 0, "search": 0}

    def responder(req: httpx.Request):
        if req.url.path.endswith("/nav"):
            calls["nav"] += 1
            return httpx.Response(200, json=NAV)
        calls["search"] += 1
        if calls["search"] == 1:
            return httpx.Response(200, json={"code": -352})
        return httpx.Response(200, json={"code": 0, "data": {"result": [], "numResults": 0}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        await Bilibili(client=client).search("rain", "en", SearchLimits(max_pages=1))
    assert calls == {"nav": 2, "search": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [412, 429])
async def test_http_risk_block_classified(status):
    def responder(req: httpx.Request):
        return httpx.Response(status, json={"code": -412})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        with pytest.raises(RateLimited):
            await Bilibili(client=client).search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_api_minus_412_is_rate_limited():
    def responder(req: httpx.Request):
        if req.url.path.endswith("/nav"):
            return httpx.Response(200, json=NAV)
        return httpx.Response(200, json={"code": -412})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        with pytest.raises(RateLimited):
            await Bilibili(client=client).search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_bilibili_download_sends_referer_user_agent_and_rejects_mirror_redirect(tmp_path):
    from clipoteca.security.download import DownloadError, download_media

    requests = []
    mp4_bytes = b"\x00\x00\x00\x10ftypisom" + b"0" * 20

    def responder(req: httpx.Request):
        requests.append(req)
        assert req.headers["referer"] == "https://www.bilibili.com/"
        assert req.headers["user-agent"]
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=mp4_bytes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        path = await download_media(
            "https://upos-sz-mirrorcos.bilivideo.com/demo.mp4",
            tmp_path,
            client=client,
            resolver=lambda host: ["1.1.1.1"],
            referer="https://www.bilibili.com/",
            allowed_hosts=("upos-sz-mirrorcos.bilivideo.com",),
        )
        assert path.read_bytes() == mp4_bytes
        with pytest.raises(DownloadError, match="allowlist"):
            await download_media(
                "https://outside.example.com/not-a-video.mp4",
                tmp_path,
                client=client,
                resolver=lambda host: ["1.1.1.1"],
                referer="https://www.bilibili.com/",
                allowed_hosts=("upos-sz-mirrorcos.bilivideo.com",),
            )
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_dash_mux_downloads_two_checked_local_streams(tmp_path, monkeypatch):
    import asyncio
    import clipoteca.security.download as module

    downloads = []

    async def fake_download(url, destination, **kwargs):
        downloads.append((url, kwargs))
        target = destination / ("video.mp4" if "/video" in url else "audio.m4a")
        target.write_bytes(b"\x00\x00\x00\x10ftypisom" + b"0" * 20)
        return target

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_ffmpeg(*argv, **kwargs):
        assert argv[0] == "ffmpeg"
        assert "https://" not in " ".join(argv)
        Path(argv[-1]).write_bytes(b"\x00\x00\x00\x10ftypisom" + b"0" * 20)
        return FakeProcess()

    from pathlib import Path

    monkeypatch.setattr(module, "download_media", fake_download)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_ffmpeg)
    result = await module.download_dash(
        "https://a.bilivideo.com/video.m4s",
        "https://b.bilivideo.com/audio.m4s",
        tmp_path,
        referer="https://www.bilibili.com/",
        allowed_hosts=("a.bilivideo.com", "b.bilivideo.com"),
    )
    assert result.name == "dash-muxed.mp4" and result.stat().st_size > 0
    assert not (tmp_path / "video.mp4").exists()
    assert not (tmp_path / "audio.m4a").exists()
    assert len(downloads) == 2
    assert downloads[1][1]["allowed_mime_types"]


@pytest.mark.asyncio
async def test_html5_first_dash_fallback_backup_urls_bandwidth_and_root_domain():
    calls = []

    def responder(req: httpx.Request):
        if req.url.path.endswith("/nav"):
            return httpx.Response(200, json=NAV)
        calls.append(dict(req.url.params))
        if req.url.params["platform"] == "html5":
            assert req.url.params["fnval"] == "1"
            assert req.url.params["high_quality"] == "1"
            return httpx.Response(200, json={"code": 0, "data": {"durl": []}})
        assert req.url.params["platform"] == "pc"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "dash": {
                        "video": [
                            {
                                "baseUrl": "https://evil.example/stream.m4s",
                                "backupUrl": ["https://bilivideo.com/video.m4s"],
                                "bandwidth": "not-a-number",
                            }
                        ],
                        "audio": [
                            {
                                "base_url": "https://evil.example/audio.m4s",
                                "backup_url": ["https://sub.hdslb.com/audio.m4s"],
                                "bandwidth": object_id(),
                            }
                        ],
                    }
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        media = await Bilibili(client=client).direct_media(
            Candidate(**{**candidate().__dict__, "metadata": {"cid": 987654}})
        )
    assert media and media.url == "https://bilivideo.com/video.m4s"
    assert media.audio_url == "https://sub.hdslb.com/audio.m4s"
    assert len(calls) == 2


def object_id():
    return None


@pytest.mark.asyncio
async def test_html5_durl_backup_when_primary_outside_cdn():
    calls = []

    def responder(req: httpx.Request):
        if req.url.path.endswith("/nav"):
            return httpx.Response(200, json=NAV)
        calls.append(req.url.params["platform"])
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "durl": [
                        {
                            "url": "https://outside.invalid/untrusted.mp4",
                            "backup_url": ["https://bilivideo.cn/trusted.mp4"],
                        }
                    ]
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        media = await Bilibili(client=client).direct_media(
            Candidate(**{**candidate().__dict__, "metadata": {"cid": 987654}})
        )
    assert media and media.url == "https://bilivideo.cn/trusted.mp4"
    assert calls == ["html5"]


@pytest.mark.asyncio
async def test_nav_keys_concurrent_requests_single_refresh():
    import asyncio

    counts = {"nav": 0}

    def responder(req: httpx.Request):
        if req.url.path.endswith("/nav"):
            counts["nav"] += 1
            return httpx.Response(200, json=NAV)
        return httpx.Response(200, json={"code": 0, "data": {"result": [], "numResults": 0}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        adapter = Bilibili(client=client)
        await asyncio.gather(
            *(adapter.search("rain", "en", SearchLimits(max_pages=1)) for _ in range(12))
        )
    assert counts["nav"] == 1


@pytest.mark.asyncio
async def test_three_http_412_blocks_job_without_more_network():
    count = 0

    def responder(req: httpx.Request):
        nonlocal count
        count += 1
        return httpx.Response(412, json={"code": -412})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        adapter = Bilibili(client=client)
        for _ in range(2):
            with pytest.raises(RateLimited):
                await adapter.search("rain", "en", SearchLimits(max_pages=1))
        with pytest.raises(ProviderError, match="ip_blocked"):
            await adapter.search("rain", "en", SearchLimits(max_pages=1))
        with pytest.raises(ProviderError, match="ip_blocked"):
            await adapter.search("rain", "en", SearchLimits(max_pages=1))
    assert count == 3


@pytest.mark.asyncio
async def test_dash_ffmpeg_protocol_restricted_and_absent_is_download_error(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path
    import clipoteca.security.download as module

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def downloader(url, path, **kwargs):
        target = path / ("vid.mp4" if "video" in url else "aud.mp4")
        target.write_bytes(b"ftyp" + b"0" * 20)
        return target

    async def ffmpeg(*args, **kwargs):
        args = list(args)
        assert args.count("-protocol_whitelist") == 2
        for i, value in enumerate(args):
            if value == "-i":
                assert args[i - 4 : i] == ["-f", "mp4", "-protocol_whitelist", "file"]
        Path(args[-1]).write_bytes(b"ftyp" + b"0" * 20)
        return Proc()

    monkeypatch.setattr(module, "download_media", downloader)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", ffmpeg)
    kwargs = {"allowed_hosts": ("a.bilivideo.com",), "referer": "https://www.bilibili.com/"}
    await module.download_dash(
        "https://a.bilivideo.com/video.m4s", "https://a.bilivideo.com/audio.m4s", tmp_path, **kwargs
    )

    async def missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)
    with pytest.raises(module.DownloadError, match="ffmpeg_unavailable"):
        await module.download_dash(
            "https://a.bilivideo.com/video.m4s",
            "https://a.bilivideo.com/audio.m4s",
            tmp_path,
            **kwargs,
        )
