"""Meta Instagram Platform and Snapchat Public Profile official response fixtures."""

import httpx
import pytest

from clipoteca.providers.base import (
    AuthRequired,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    SearchLimits,
)
from clipoteca.providers.instagram import Instagram
from clipoteca.providers.snapchat import Snapchat

IG_USER = "17841405309211844"
IG_MEDIA = "17918920912340654"
SNAP_PROFILE = "76da494b-76bc-4bbb-bb27-c5a66fb0d1ab"
SNAP_ID = "W7_EDlXWTBiXAEEniNoMPwAAYaWd5d21yeG92AX_7pp00AX_7ppwFAAAAAA"


def ig_client(rows, status=200):
    def respond(req):
        assert req.url.path == f"/v27.0/{IG_USER}/media"
        assert req.url.params["access_token"] == "test"
        return httpx.Response(status, json={"data": rows})

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


def snap_client(rows, status=200):
    def respond(req):
        assert req.url.path == f"/v1/public_profiles/{SNAP_PROFILE}/spotlights"
        assert req.headers["authorization"] == "Bearer test"
        return httpx.Response(
            status,
            json={"request_status": "SUCCESS", "spotlights": rows},
            headers={"Retry-After": "0"},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["es", "en", "zh", "ru"])
async def test_instagram_video_only_owner_scope(lang):
    adapter = Instagram(
        token="test",
        user_id=IG_USER,
        client=ig_client(
            [
                {
                    "id": IG_MEDIA,
                    "media_type": "VIDEO",
                    "caption": "Rain in streets",
                    "permalink": "https://www.instagram.com/reel/test123/",
                    "media_url": "https://scontent.cdninstagram.com/video.mp4",
                },
                {"id": "17918920912340655", "media_type": "IMAGE", "caption": "Rain"},
            ]
        ),
    )
    found = await adapter.search("Rain", lang, SearchLimits())
    assert len(found) == 1
    assert found[0].source_url.startswith("https://www.instagram.com/reel/")
    assert found[0].source_language == "en"
    assert found[0].language == lang
    assert await adapter.direct_media(found[0]) is None


@pytest.mark.asyncio
async def test_instagram_missing_token_or_invalid_professional_user():
    with pytest.raises(AuthRequired):
        await Instagram(token="", user_id=IG_USER).search("rain", "en", SearchLimits())
    with pytest.raises(AuthRequired):
        await Instagram(token="test", user_id="notvalid").search("rain", "es", SearchLimits())


@pytest.mark.asyncio
async def test_instagram_empty_and_auth_error_payload():
    assert (
        await Instagram(token="test", user_id=IG_USER, client=ig_client([])).search(
            "rain", "zh", SearchLimits()
        )
        == []
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"error": {"code": 190, "message": "token"}})
        )
    )
    with pytest.raises(AuthRequired):
        await Instagram(token="test", user_id=IG_USER, client=client).search(
            "rain", "es", SearchLimits()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status,exc", [(429, RateLimited), (500, ProviderError)])
async def test_instagram_http_error(status, exc):
    with pytest.raises(exc):
        await Instagram(token="test", user_id=IG_USER, client=ig_client([], status)).search(
            "Rain", "en", SearchLimits()
        )


@pytest.mark.asyncio
async def test_instagram_timeout_typed():
    def timeout(_):
        raise httpx.ReadTimeout("timeout")

    with pytest.raises(ProviderTimeout):
        await Instagram(
            token="test",
            user_id=IG_USER,
            client=httpx.AsyncClient(transport=httpx.MockTransport(timeout)),
        ).search("rain", "en", SearchLimits())


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["es", "en", "zh", "ru"])
async def test_snapchat_authorized_profile_spotlights(lang):
    adapter = Snapchat(
        token="test",
        profile_id=SNAP_PROFILE,
        client=snap_client(
            [
                {
                    "sub_request_status": "SUCCESS",
                    "spotlight": {
                        "id": SNAP_ID,
                        "profile_id": SNAP_PROFILE,
                        "description": "Rain in city",
                        "status": "LIVE",
                        "media_url": "https://cf-st.sc-cdn.net/d/resource-id",
                    },
                },
                {"sub_request_status": "ERROR", "spotlight": {"id": "bad"}},
            ]
        ),
    )
    found = await adapter.search("Rain", lang, SearchLimits())
    assert len(found) == 1 and found[0].provider_id == SNAP_ID
    assert found[0].source_language == "en" and found[0].language == lang
    assert found[0].source_url == "https://www.snapchat.com/spotlight"
    assert found[0].metadata["scope"] == "authorized_profile_only"
    assert await adapter.direct_media(found[0]) is None


@pytest.mark.asyncio
async def test_snapchat_missing_authorization_and_empty():
    with pytest.raises(AuthRequired):
        await Snapchat(token="", profile_id=SNAP_PROFILE).search("Rain", "en", SearchLimits())
    with pytest.raises(AuthRequired):
        await Snapchat(token="token", profile_id="bad").search("Rain", "en", SearchLimits())
    assert (
        await Snapchat(token="test", profile_id=SNAP_PROFILE, client=snap_client([])).search(
            "Rain", "es", SearchLimits()
        )
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("code,exc", [(429, RateLimited), (500, ProviderError)])
async def test_snapchat_http_error(code, exc):
    with pytest.raises(exc):
        await Snapchat(token="test", profile_id=SNAP_PROFILE, client=snap_client([], code)).search(
            "Rain", "en", SearchLimits()
        )


@pytest.mark.asyncio
async def test_snapchat_timeout():
    def timeout(_):
        raise httpx.ReadTimeout("timeout")

    with pytest.raises(ProviderTimeout):
        await Snapchat(
            token="test",
            profile_id=SNAP_PROFILE,
            client=httpx.AsyncClient(transport=httpx.MockTransport(timeout)),
        ).search("Rain", "en", SearchLimits())
