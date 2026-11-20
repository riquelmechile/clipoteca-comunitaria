from __future__ import annotations

import json
from hashlib import sha256

import httpx
import pytest
from clipoteca.catalog import Catalog, GitHubCatalogWriter, canonical_tokens
from clipoteca.storage.github import GitHubReleaseStorage, StorageError


def sample_clip(identifier: str = "clip-000001") -> dict:
    return {
        "id": identifier,
        "title": {"es": "LLUVIA", "en": "Rain"},
        "description": {"es": "Calle", "en": "Street"},
        "tags": {"es": ["Vídeo"], "en": ["video"]},
        "source": {
            "provider": "pexels",
            "provider_id": "x",
            "source_language": "en",
            "source_title": "ＲＡＩＮ",
            "source_url": "https://example.com/source/x",
        },
        "media": {
            "duration_seconds": 2.0,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "aspect_ratio": "16:9",
            "orientation": "horizontal",
            "codec": "h264",
            "container": "mp4",
            "has_audio": False,
            "size_bytes": 1024,
            "sha256": "a" * 64,
            "perceptual_hash": "0011",
        },
        "storage": {
            "provider": "github_releases",
            "asset_url": "https://example.com/assets/clip.mp4",
            "thumbnail_url": "https://example.com/assets/thumb.webp",
        },
        "status": "published",
        "created_at": "2026-11-20T00:00:00Z",
    }


def test_nfkc_indexes_and_dedup():
    assert canonical_tokens("ＶÍＤＥＯ") == {"vídeo"}
    catalog = Catalog([sample_clip()])
    assert catalog.find_duplicate(sample_clip("clip-000999"))["id"] == "clip-000001"
    assert "rain" in catalog.indexes()["en"]


def test_unverified_asset_never_indexes():
    catalog = Catalog()
    with pytest.raises(ValueError):
        catalog.add_verified(sample_clip(), {}, asset_verified=False)
    assert catalog.clips == []


def test_asset_verification_deletes_incomplete(tmp_path):
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET" and request.url.path.endswith("/assets"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 2,
                        "name": "clip_1.mp4",
                        "size": 2,
                        "state": "new",
                        "browser_download_url": "https://download.test/a",
                    }
                ],
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(request.url)

    storage = GitHubReleaseStorage(
        "o/r", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(StorageError):
        storage.verify_asset("clip_1.mp4", 2, sha256(b"xx").hexdigest(), 1)
    assert any(method == "DELETE" for method, _ in calls)


def test_409_reloads_merges_and_retries():
    gets = 0
    puts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets, puts
        if request.method == "GET":
            gets += 1
            body = json.dumps([] if gets == 1 else [sample_clip()]).encode()
            import base64

            return httpx.Response(
                200,
                json={"sha": f"s{gets}", "content": base64.b64encode(body).decode()},
            )
        puts += 1
        if puts == 1:
            return httpx.Response(409)
        return httpx.Response(200, json={"commit": {"sha": "done"}})

    writer = GitHubCatalogWriter(
        "o/r", "secret", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert writer.update([sample_clip("clip-000002")]) == "done"
    assert gets == 2 and puts == 2
