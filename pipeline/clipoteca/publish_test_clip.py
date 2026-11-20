"""Workflow-only publisher for the deterministic M1 smoke clip."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path

from .catalog import DuplicateClip, GitHubCatalogWriter
from .storage.github import GitHubReleaseStorage
from .media.probe import probe_media
from .media.hashing import perceptual_hash


def main() -> int:
    path = Path(sys.argv[sys.argv.index("--path") + 1])
    expected = sys.argv[sys.argv.index("--expect") + 1] if "--expect" in sys.argv else None
    repository = os.environ.get("GITHUB_REPOSITORY", "riquelmechile/clipoteca-comunitaria")
    token = os.environ["GITHUB_TOKEN"]
    writer = GitHubCatalogWriter(repository, token)
    catalog, _ = writer.read_catalog()
    digest = sha256(path.read_bytes()).hexdigest()
    existing = catalog.find_duplicate(
        {
            "source": {"provider": "generated_test", "provider_id": digest},
            "media": {"sha256": digest},
        }
    )
    if existing:
        if expected != "duplicate":
            print(json.dumps({"status": "duplicate", "id": existing["id"]}))
        return 0
    if expected == "duplicate":
        raise RuntimeError("expected a duplicate, but catalogue does not contain the test clip")

    identifier = catalog.next_id()
    thumbnail = path.with_suffix(".webp")
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-y",
            str(thumbnail),
        ],
        check=True,
        timeout=30,
    )
    video_name = f"clip_{identifier}.mp4"
    thumb_name = f"thumb_{identifier}.webp"
    with GitHubReleaseStorage(repository, token=token) as storage:
        release = storage.get_or_create_batch_release(date.today())
        video = storage.upload_asset(release, path, video_name)
        video = storage.verify_asset(video_name, path.stat().st_size, digest, release.id)
        thumb_digest = sha256(thumbnail.read_bytes()).hexdigest()
        thumb = storage.upload_asset(release, thumbnail, thumb_name)
        thumb = storage.verify_asset(thumb_name, thumbnail.stat().st_size, thumb_digest, release.id)

    media = probe_media(path)
    clip = {
        "id": identifier,
        "title": {"es": "Clip de prueba", "en": "Test clip"},
        "description": {"es": "Fixture determinista", "en": "Deterministic fixture"},
        "tags": {"es": ["test"], "en": ["test"]},
        "source": {
            "provider": "generated_test",
            "provider_id": digest,
            "source_language": "en",
            "source_title": "testsrc2",
            "source_url": "https://github.com/riquelmechile/clipoteca-comunitaria/actions",
        },
        "media": {
            "duration_seconds": media.duration_seconds,
            "width": media.width,
            "height": media.height,
            "fps": media.fps,
            "aspect_ratio": media.aspect_ratio,
            "orientation": media.orientation,
            "codec": media.codec,
            "container": "mp4",
            "has_audio": media.has_audio,
            "size_bytes": path.stat().st_size,
            "sha256": digest,
            "perceptual_hash": perceptual_hash(path),
        },
        "storage": {
            "provider": "github_releases",
            "asset_url": video.browser_download_url,
            "thumbnail_url": thumb.browser_download_url,
        },
        "status": "published",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    try:
        commit = writer.update([clip], verified_assets={identifier: (video, thumb)})
    except DuplicateClip as duplicate:
        print(json.dumps({"status": "duplicate", "id": duplicate.clip_id}))
        return 0
    print(json.dumps({"status": "published", "id": identifier, "commit": commit}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
