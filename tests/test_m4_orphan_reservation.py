"""Orphaned assets reserve identifiers across retries and later ingestions."""

import httpx

from clipoteca.catalog import Catalog
from clipoteca.storage.github import GitHubReleaseStorage, ReleaseBatch


def test_next_id_skips_any_reserved_orphan_from_release():
    catalog = Catalog([{"id": "clip-000001"}])
    assert catalog.next_id(reserved_ids={"clip-000002", "clip-000003"}) == "clip-000004"


def test_storage_existing_thumbnail_or_video_reserves_id():
    def github(request):
        assert request.method == "GET"
        return httpx.Response(
            200,
            json=[
                {"id": 1, "name": "clip_clip-000004.mp4", "state": "uploaded"},
                {"id": 2, "name": "thumb_clip-000005.webp", "state": "uploaded"},
                {"id": 3, "name": "other.mp4", "state": "uploaded"},
            ],
        )

    storage = GitHubReleaseStorage(
        "r/test", client=httpx.Client(transport=httpx.MockTransport(github))
    )
    release = ReleaseBatch(1, "batch-2026-11-20-001", "https://uploads.github.com/")
    assert storage.reserved_ids(release) == {"clip-000004", "clip-000005"}
    assert Catalog().next_id(reserved_ids=storage.reserved_ids(release)) == "clip-000001"
