from __future__ import annotations

import json
from hashlib import sha256

import httpx
import pytest
from clipoteca.catalog import (
    Catalog,
    GitHubCatalogWriter,
    IdentityCollision,
    DuplicateClip,
    canonical_tokens,
)
from clipoteca.storage.github import GitHubReleaseStorage, StorageError, ReleaseAsset, ReleaseBatch


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
    assert canonical_tokens("ＶÍＤＥＯ") == {"video"}
    catalog = Catalog([sample_clip()])
    assert catalog.find_duplicate(sample_clip("clip-000999"))["id"] == "clip-000001"
    assert "rain" in catalog.indexes()["en"]


def test_unverified_asset_never_indexes():
    catalog = Catalog()
    with pytest.raises(ValueError):
        catalog.add_verified(sample_clip(), {}, asset_verified=False)
    assert catalog.clips == []


def test_asset_verification_preserves_incomplete_without_deletion(tmp_path):
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
    assert not any(method == "DELETE" for method, _ in calls)


def verified_pair(clip: dict) -> dict:
    identifier = clip["id"]
    return {
        identifier: (
            ReleaseAsset(
                1, f"clip_{identifier}.mp4", 1024, "uploaded", clip["storage"]["asset_url"]
            ),
            ReleaseAsset(
                2, f"thumb_{identifier}.webp", 512, "uploaded", clip["storage"]["thumbnail_url"]
            ),
        )
    }


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
                200, json={"sha": f"s{gets}", "content": base64.b64encode(body).decode()}
            )
        puts += 1
        if puts == 1:
            return httpx.Response(409)
        return httpx.Response(200, json={"commit": {"sha": "done"}})

    incoming = sample_clip("clip-000002")
    incoming["source"]["provider_id"] = "distinct-video"
    incoming["source"]["source_url"] = "https://example.com/source/distinct-video"
    incoming["media"]["sha256"] = "b" * 64
    incoming["media"]["perceptual_hash"] = "distinct-fingerprint"
    writer = GitHubCatalogWriter(
        "o/r", "secret", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert writer.update([incoming], verified_assets=verified_pair(incoming)) == "done"
    assert gets == 2 and puts == 2


def test_concurrent_identity_collisions_never_replace_existing():
    import base64

    old = sample_clip()
    incoming = sample_clip()
    incoming["source"]["provider_id"] = "new"
    incoming["source"]["source_url"] = "https://example.com/source/new"
    incoming["media"]["sha256"] = "b" * 64
    incoming["media"]["perceptual_hash"] = "distinct-fingerprint"
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(
            200,
            json={"sha": "s1", "content": base64.b64encode(json.dumps([old]).encode()).decode()},
        )

    writer = GitHubCatalogWriter(
        "o/r", "secret", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(IdentityCollision):
        writer.update([incoming], verified_assets=verified_pair(incoming))
    assert calls == ["GET"]


def test_duplicate_remote_after_conflict_is_not_overwritten():
    import base64

    remote = sample_clip()
    incoming = sample_clip("clip-000002")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(
            200,
            json={"sha": "s", "content": base64.b64encode(json.dumps([remote]).encode()).decode()},
        )

    writer = GitHubCatalogWriter(
        "o/r", "secret", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(DuplicateClip) as err:
        writer.update([incoming], verified_assets=verified_pair(incoming))
    assert err.value.clip_id == "clip-000001"
    assert calls == ["GET"]


def test_writer_rejects_unverified_assets_before_network():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("No network call allowed for unverified assets")

    clip = sample_clip()
    writer = GitHubCatalogWriter(
        "o/r", "secret", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ValueError, match="verified"):
        writer.update([clip])
    with pytest.raises(ValueError, match="match"):
        pair = verified_pair(clip)
        pair[clip["id"]] = (
            ReleaseAsset(1, "other.mp4", 1024, "uploaded", clip["storage"]["asset_url"]),
            pair[clip["id"]][1],
        )
        writer.update([clip], verified_assets=pair)


def test_raw_catalog_read_when_contents_api_encoding_is_none():
    clip = sample_clip()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("accept"))
        if len(seen) == 1:
            return httpx.Response(200, json={"sha": "s", "encoding": "none", "content": ""})
        return httpx.Response(200, content=json.dumps([clip]).encode())

    writer = GitHubCatalogWriter(
        "o/r", "secret", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    catalog, sha = writer.read_catalog()
    assert sha == "s"
    assert catalog.clips == [clip]
    assert seen[-1] == "application/vnd.github.raw"


def test_empty_remote_catalog_refuses_overwrite():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sha": "s", "encoding": "none", "content": ""})

    writer = GitHubCatalogWriter(
        "o/r", "secret", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ValueError):
        writer.read_catalog()


def test_indexes_clean_stale_files_and_shorten_long_tokens(tmp_path):
    clip = sample_clip()
    clip["tags"]["es"] = ["天" * 200, "mañana"]
    catalog = Catalog([clip])
    old = tmp_path / "es" / "obsolete.json"
    old.parent.mkdir(parents=True)
    old.write_text("[]")
    catalog.write_indexes(tmp_path)
    assert not old.exists()
    names = [p.name for p in (tmp_path / "es").glob("*.json")]
    assert all(len(name.encode("utf-8")) < 255 for name in names)
    assert "manana.json" in names
    assert "天天.json" in names


def test_release_name_collision_must_not_delete_existing_asset(tmp_path):
    deleted = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/assets"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 9,
                        "name": "clip_clip-000001.mp4",
                        "size": 3,
                        "state": "uploaded",
                        "browser_download_url": "https://download.test/video",
                    }
                ],
            )
        if request.method == "GET":
            return httpx.Response(200, content=b"old")
        if request.method == "DELETE":
            deleted.append(True)
            return httpx.Response(204)
        raise AssertionError(request.url)

    storage = GitHubReleaseStorage(
        "o/r", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(StorageError, match="collision"):
        storage.find_existing_asset(
            ReleaseBatch(3, "batch", "https://upload.test"),
            "clip_clip-000001.mp4",
            3,
            sha256(b"new").hexdigest(),
        )
    assert not deleted


def test_new_batch_release_is_never_marked_latest():
    from datetime import date

    created = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/releases"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and request.url.path.endswith("/releases"):
            body = json.loads(request.content)
            created.append(body)
            return httpx.Response(
                201,
                json={
                    "id": 7,
                    "tag_name": body["tag_name"],
                    "upload_url": "https://uploads.example/assets{?name,label}",
                    "assets": [],
                },
            )
        raise AssertionError(f"unexpected {request.method} {request.url}")

    storage = GitHubReleaseStorage(
        "o/r", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    release = storage.get_or_create_batch_release(date(2026, 11, 23))
    assert release.tag == "batch-2026-11-23-001"
    assert created == [
        {
            "tag_name": "batch-2026-11-23-001",
            "name": "batch-2026-11-23-001",
            "draft": False,
            "prerelease": False,
            "make_latest": "false",
        }
    ]


def test_retired_clip_is_not_indexed_and_source_stays_blocked():
    retired = sample_clip()
    retired["status"] = "retired"
    retired["retirement"] = {"reason": "wrong subject", "date": "2026-11-24"}
    catalog = Catalog([retired])
    assert catalog.indexes() == {"es": {}, "en": {}}
    assert catalog.is_retired_source(retired["source"]["source_url"])
    assert catalog.find_duplicate(sample_clip("clip-000002")) is None
    assert catalog.next_id() == "clip-000002"
