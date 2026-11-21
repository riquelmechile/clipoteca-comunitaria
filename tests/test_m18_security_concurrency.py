"""M18 archive SSRF, permission, credential and concurrent catalog regressions."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from clipoteca.catalog import DuplicateClip, GitHubCatalogWriter, safe_source_url
from clipoteca.providers.base import Candidate, ProviderError, SearchLimits
from clipoteca.providers.coverr import Coverr
from clipoteca.providers.dpla import DPLA
from clipoteca.providers.europeana import Europeana
from clipoteca.providers.library_of_congress import LibraryOfCongress
from clipoteca.providers.nara import NARA
from clipoteca.providers.smithsonian import Smithsonian
from clipoteca.security.download import DownloadError, download_media
from clipoteca.security.ssrf import UnsafeMediaURL, validate_public_url
from clipoteca.storage.github import ReleaseAsset


def candidate(provider, url: str) -> Candidate:
    meta = (
        {"resources": [{"video": [url]}]}
        if provider == "library_of_congress"
        else {"urls": {"mp4_download": url}}
        if provider == "coverr"
        else {"direct_video_url": url, "direct_video_mime": "video/mp4"}
    )
    return Candidate(
        provider,
        "record-1",
        "rain",
        "en",
        "https://archives.example/item/1",
        "Rain archive film",
        "en",
        metadata=meta,
    )


def adapter_for(provider: str):
    types = {
        "library_of_congress": LibraryOfCongress,
        "coverr": Coverr,
        "europeana": Europeana,
        "nara": NARA,
        "dpla": DPLA,
        "smithsonian": Smithsonian,
    }
    kind = types[provider]
    return kind() if provider == "library_of_congress" else kind(api_key="fixture")


PROVIDERS = (
    "library_of_congress", "coverr", "europeana",
    "nara", "dpla", "smithsonian",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_archive_private_ip_download_ref_blocked_by_security(provider, tmp_path):
    url = "https://127.0.0.1/secret.mp4"
    ref = await adapter_for(provider).direct_media(candidate(provider, url))
    assert ref is not None
    requests = []

    def handler(request):
        requests.append(request)
        pytest.fail("SSRF target must be rejected before sending any request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadError, match="UnsafeMediaURL"):
            await download_media(
                ref.url, tmp_path, client=client, resolver=lambda _host: ["8.8.8.8"]
            )
    assert not requests


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_archive_private_dns_and_http_scheme_rejected(provider):
    public = "https://media.example.org/stream.mp4"
    ref = await adapter_for(provider).direct_media(candidate(provider, public))
    assert ref is not None
    with pytest.raises(UnsafeMediaURL):
        await validate_public_url(ref.url, resolver=lambda _host: ["10.1.2.3"])
    with pytest.raises(UnsafeMediaURL):
        await validate_public_url(
            "http://media.example.org/stream.mp4", resolver=lambda _host: ["8.8.8.8"]
        )
    assert (
        await adapter_for(provider).direct_media(
            candidate(provider, "http://media.example.org/stream.mp4")
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [Europeana, NARA])
async def test_archive_explicit_download_restriction_overrides_video_url(kind):
    adapter = kind(api_key="fixture")
    raw = {
        "direct_video_url": "https://media.example.org/record.mp4",
        "direct_video_mime": "video/mp4",
        "download_restricted": "true",
    }
    restricted = Candidate(
        kind.name, "1", "rain", "en", "https://archive.example/item/1",
        "Rain", "en", metadata=raw,
    )
    assert await adapter.direct_media(restricted) is None
    nested = dict(raw)
    nested.pop("download_restricted")
    nested["resources"] = [{"downloadRestricted": True}]
    nested_candidate = Candidate(
        kind.name, "2", "rain", "en", "https://archive.example/item/2",
        "Rain", "en", metadata=nested,
    )
    assert await adapter.direct_media(nested_candidate) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [Coverr, Europeana, NARA, DPLA, Smithsonian])
async def test_archive_provider_errors_never_expose_secret_keys(kind):
    secret = "SECRET_SHOULD_NEVER_APPEAR"
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(503, json={"detail": secret}, headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = kind(api_key=secret, client=client)
        with pytest.raises(ProviderError) as raised:
            await adapter.search("rain", "en", SearchLimits(max_pages=1))
    assert seen
    assert secret not in str(raised.value)
    assert secret not in json.dumps({"status": type(raised.value).__name__})
    if kind in (DPLA, Smithsonian):
        assert any("api_key" in request.url.params for request in seen)
    clean = safe_source_url(
        f"https://archive.example/item/1?api_key={secret}&feature=abc"
    )
    assert secret not in clean and "api_key" not in clean and "feature=abc" in clean


def test_github_catalog_writer_detects_concurrent_duplicate_before_second_put():
    existing = {
        "id": "clip-000042",
        "source": {
            "provider": "library_of_congress",
            "provider_id": "movie-a",
            "source_title": "Newsreel 1945",
            "source_url": "https://loc.gov/item/A",
        },
        "media": {"sha256": "a" * 64, "perceptual_hash": "a"},
    }
    incoming = {
        "id": "clip-000043",
        "source": {
            "provider": "europeana",
            "provider_id": "movie-b",
            "source_title": "Different film title",
            "source_url": "https://europeana.eu/item/B",
        },
        "media": {"sha256": "a" * 64, "perceptual_hash": "a"},
        "storage": {
            "asset_url": "https://github.com/test/video",
            "thumbnail_url": "https://github.com/test/thumb",
        },
    }
    calls = {"get": 0, "put": 0}

    def github(request):
        if request.method == "GET":
            calls["get"] += 1
            rows = [] if calls["get"] == 1 else [existing]
            content = base64.b64encode(json.dumps(rows).encode()).decode()
            return httpx.Response(200, json={
                "sha": str(calls["get"]), "encoding": "base64", "content": content
            })
        calls["put"] += 1
        return httpx.Response(409, json={"message": "concurrent update"})

    client = httpx.Client(transport=httpx.MockTransport(github))
    writer = GitHubCatalogWriter(
        "owner/repo", "fixture-token", client=client, schema={"type": "object"}
    )
    video = ReleaseAsset(
        1, "clip_clip-000043.mp4", 100, "uploaded", incoming["storage"]["asset_url"]
    )
    thumbnail = ReleaseAsset(
        2, "thumb_clip-000043.webp", 10, "uploaded",
        incoming["storage"]["thumbnail_url"],
    )
    with pytest.raises(DuplicateClip) as error:
        writer.update(
            [incoming], verified_assets={incoming["id"]: (video, thumbnail)},
            phash_similar=lambda x, y: x == y,
        )
    assert error.value.clip_id == existing["id"]
    assert calls == {"get": 2, "put": 1}
