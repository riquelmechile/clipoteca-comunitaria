"""M19 deterministic, no-network Douyin/TikTok web signing and discovery tests."""

import json
import random
from pathlib import Path

import httpx
import pytest

from clipoteca.providers.base import AuthRequired, Candidate, SearchLimits
from clipoteca.providers.douyin import Douyin
from clipoteca.providers.signing import ABogus, BROWSER_UA, XBogus, sign_tiktok, sm3_hexdigest
from clipoteca.providers.tiktok import TikTok
from clipoteca.sources import list_sources
from tests.fake_secrets import fake_secret

FIXTURES = Path(__file__).parent / "fixtures" / "m19"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def guest_response():
    return httpx.Response(200, json={}, headers={"Set-Cookie": "ttwid=guest-identity; Path=/"})


@pytest.mark.asyncio
async def test_douyin_signed_video_search_and_direct_mp4_last_bitrate_url():
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.host == "ttwid.bytedance.com":
            assert req.method == "POST"
            return guest_response()
        assert req.url.path == "/aweme/v1/web/search/item/"
        assert req.headers.get("cookie") == "ttwid=guest-identity"
        assert req.url.params["aid"] == "6383"
        assert req.url.params["device_platform"] == "webapp"
        assert req.url.params["msToken"]
        assert req.url.params["a_bogus"]
        assert req.url.params["X-Bogus"]
        return httpx.Response(200, json=fixture("douyin_video.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = Douyin(cookie="session_secret=" + fake_secret("unused-douyin"), client=client)
        candidates = await adapter.search("下雨", "zh", SearchLimits(max_pages=1))
        media = await adapter.direct_media(candidates[0])
    assert len(seen) == 2
    assert len(candidates) == 1
    assert candidates[0].source_language == "zh"
    assert candidates[0].source_url == "https://www.douyin.com/video/7381000000000000001"
    assert "DO_NOT_USE" not in repr(candidates)
    assert media is not None and media.mime == "video/mp4"
    assert media.url.endswith("/play/movie.mp4")
    assert ".douyinvod.com" in media.allowed_hosts


@pytest.mark.asyncio
async def test_douyin_general_search_fallback_after_empty_video():
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.host == "ttwid.bytedance.com":
            return guest_response()
        if req.url.path == "/aweme/v1/web/search/item/":
            return httpx.Response(200, json={"status_code": 0, "data": []})
        if req.url.path == "/aweme/v1/web/general/search/single/":
            assert req.url.params["search_channel"] == "aweme_general"
            return httpx.Response(200, json=fixture("douyin_general.json"))
        raise AssertionError("Unexpected request path")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await Douyin(client=client).search("rain", "en", SearchLimits(max_pages=1))
    assert [x.provider_id for x in rows] == ["7381000000000000002"]
    assert seen == [
        "/ttwid/union/register/",
        "/aweme/v1/web/search/item/",
        "/aweme/v1/web/general/search/single/",
    ]


@pytest.mark.asyncio
async def test_douyin_detail_uses_secsdk_signature_without_browser():
    def handler(req):
        if req.url.host == "ttwid.bytedance.com":
            return guest_response()
        if req.url.path == "/":
            return httpx.Response(200, text="<html></html>", headers={"x-tt-trace-host": "UIFID-X"})
        assert req.url.path == "/aweme/v1/web/aweme/detail/"
        assert req.url.params["aweme_id"] == "7381000000000000003"
        assert req.url.params["x-secsdk-web-signature"]
        assert req.url.params["uifid"] == "UIFID-X"
        return httpx.Response(200, json=fixture("douyin_detail.json"))

    candidate = Candidate(
        "douyin",
        "7381000000000000003",
        "下雨",
        "zh",
        "https://www.douyin.com/video/7381000000000000003",
        "雨滴",
        "zh",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Douyin(client=client).direct_media(candidate)
    assert result is not None and result.url.endswith("/play/detail.mp4")


@pytest.mark.asyncio
async def test_tiktok_search_signature_cursor_search_id_and_resolution():
    seen = []

    def handler(req):
        if req.url.host == "ttwid.bytedance.com":
            return guest_response()
        seen.append(req)
        assert req.url.path == "/api/search/item/full/"
        assert req.url.params["X-Dynosaur"]
        assert req.url.params["X-Gnarly"]
        assert req.url.params["X-Bogus"] == "1"
        assert req.url.params["aid"] == "1988"
        assert req.headers.get("cookie") == "ttwid=guest-identity"
        if req.url.params.get("offset") == "0":
            assert not req.url.params.get("search_id")
            return httpx.Response(200, json=fixture("tiktok_page1.json"))
        assert req.url.params["offset"] == "1"
        assert req.url.params["search_id"] == "CanonSearch555"
        return httpx.Response(200, json=fixture("tiktok_page2.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTok(cookie="secret=" + fake_secret("unused-tiktok"), client=client)
        rows = await adapter.search("rain", "en", SearchLimits(max_pages=2))
        ref = await adapter.direct_media(rows[0])
    assert len(seen) == 2
    assert [r.provider_id for r in rows] == ["7400000000000000001", "7400000000000000002"]
    assert ref is not None and ref.url.endswith("video-1.mp4")
    assert "UNUSED" not in repr(rows)


@pytest.mark.asyncio
async def test_tiktok_detail_fetches_without_cached_signed_media_url():
    def handler(req):
        if req.url.host == "ttwid.bytedance.com":
            return guest_response()
        assert req.url.path == "/api/item/detail/"
        assert req.url.params["itemId"] == "7400000000000000003"
        assert req.url.params["X-Gnarly"]
        return httpx.Response(200, json=fixture("tiktok_detail.json"))

    candidate = Candidate(
        "tiktok",
        "7400000000000000003",
        "rain",
        "en",
        "https://www.tiktok.com/@creator/video/7400000000000000003",
        "Rain",
        "en",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ref = await TikTok(client=client).direct_media(candidate)
    assert ref and ref.url.endswith("detail.mp4")


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
async def test_code_2483_is_session_required_not_generic(platform):
    def handler(req):
        if req.url.host == "ttwid.bytedance.com":
            return guest_response()
        return httpx.Response(200, json=fixture("session_required.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = Douyin(client=client) if platform == "douyin" else TikTok(client=client)
        with pytest.raises(AuthRequired, match="session_required"):
            await adapter.search("rain", "en", SearchLimits(max_pages=1))


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
async def test_secret_cookie_is_fallback_only_and_never_in_results(platform):
    private_cookie = fake_secret("m19-cookie")
    secret = "session=" + private_cookie
    seen = []

    def handler(req):
        if req.url.host == "ttwid.bytedance.com":
            return guest_response()
        seen.append(req.headers.get("cookie"))
        if len(seen) == 1:
            return httpx.Response(200, json=fixture("session_required.json"))
        assert req.headers.get("cookie") == secret
        return httpx.Response(
            200,
            json=fixture("douyin_video.json" if platform == "douyin" else "tiktok_page2.json"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = (
            Douyin(cookie=secret, client=client)
            if platform == "douyin"
            else TikTok(cookie=secret, client=client)
        )
        rows = await adapter.search("rain", "en", SearchLimits(max_pages=1))
    assert seen == ["ttwid=guest-identity", secret]
    assert rows
    assert private_cookie not in json.dumps([r.__dict__ for r in rows], ensure_ascii=False)


def test_signatures_exact_upstream_reference_vectors():
    """Outputs recorded by executing original reference code offline.

    Evil0ctal/Douyin_TikTok_Download_API @
    ed4ec42534b1194f8d8c18be7b4ba493334f5321:
    src/dtk/signing/native/{xbogus,abogus,tiktok_sign}.py;
    src/dtk/signing/base.py; tests/unit/test_signing_golden.py.
    Native modules loaded unchanged with isolated package stubs; rng and
    wall clock fixed, source code's browser query encoder retained.

    Youhai020616/douyin @ 6b10a49a92ea3369d633043a99f20cfebb5d857e
    src/dy_cli/engines/api_client.py _secsdk_sign executed from original
    AST with time=1700000000 and uifid=UIFID-X.
    """
    from clipoteca.providers.signing import secsdk_sign
    from clipoteca.providers.signing.tiktok_params import search_posts_params

    ref = fixture("upstream_signatures.json")
    assert ref["commit"] == "ed4ec42534b1194f8d8c18be7b4ba493334f5321"
    assert sm3_hexdigest(b"abc") == (
        "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
    )
    query = (
        "device_platform=webapp&aid=6383&channel=channel_pc_web"
        "&aweme_id=7372484719365098803&pc_client_type=1&version_code=190500"
        "&version_name=19.5.0"
    )
    assert XBogus(BROWSER_UA).sign(query, timestamp=1735689600) == ref["xbogus"]
    assert (
        ABogus(BROWSER_UA, rng=random.Random(3)).get_value(query, now_ms=1740000000000)
        == ref["abogus"]
    )
    signed, params = sign_tiktok(
        [("aid", "1988"), ("keyword", "rain")],
        BROWSER_UA,
        timestamp=1788856077,
        nonce=3,
        nonce2=4,
        rng=random.Random(3),
    )
    assert signed == ref["tiktok_signed"]
    assert params == ref["tiktok_params"]
    assert (
        secsdk_sign(
            {"aweme_id": "123", "aid": 6383},
            "UIFID-X",
            timestamp=1700000000,
        )
        == ref["secsdk"]["params"]
    )
    assert (
        search_posts_params(keyword="rain", cursor="20:SearchABC123")["search_id"] == "SearchABC123"
    )


def test_new_provider_capabilities_do_not_require_oauth_or_expose_cookies():
    private_dy = fake_secret("douyin-cookie")
    private_tt = fake_secret("tiktok-cookie")
    old_access = fake_secret("legacy-access")
    rows = {
        r["name"]: r
        for r in list_sources(
            env={
                "DOUYIN_COOKIE": private_dy,
                "TIKTOK_COOKIE": private_tt,
                "DOUYIN_ACCESS_TOKEN": old_access,
                "TIKTOK_ACCESS_TOKEN": old_access,
            }
        )
    }
    for key in ("douyin", "tiktok"):
        assert rows[key]["status"] == "SUPPORTED_SEARCH_DOWNLOAD"
        assert rows[key]["scope"] == "public_search"
        assert rows[key]["capabilities"]["download"] == "best_effort"
        assert rows[key]["requires"] == []
    assert all(x not in json.dumps(rows) for x in (private_dy, private_tt, old_access))


@pytest.mark.asyncio
async def test_tiktok_secret_mstoken_is_signed_only_after_session_required():
    seen = []
    private_cookie = fake_secret("tiktok-session")
    ms_token = fake_secret("mstoken")
    secret = f"session={private_cookie}; msToken={ms_token}"

    def handler(request):
        if request.url.host == "ttwid.bytedance.com":
            return guest_response()
        seen.append(
            {
                "cookie": request.headers.get("cookie"),
                "token": request.url.params.get("msToken"),
            }
        )
        if len(seen) == 1:
            return httpx.Response(200, json={"status_code": 2483})
        return httpx.Response(200, json=fixture("tiktok_page2.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await TikTok(cookie=secret, client=client).search(
            "rain", "en", SearchLimits(max_pages=1)
        )
    assert rows
    assert seen[0]["token"] == ""
    assert seen[0]["cookie"] == "ttwid=guest-identity"
    assert seen[1]["token"] == ms_token
    assert seen[1]["cookie"] == secret
    assert private_cookie not in repr(rows)


def test_tiktok_captured_response_shape_is_parsed_without_identity_leaks():
    from clipoteca.providers.tiktok import _items

    payload = fixture("tiktok_captured_shape.json")
    assert payload["status_code"] == 0
    assert len(_items(payload)) == 2
    assert "cookie" not in json.dumps(payload).lower()
