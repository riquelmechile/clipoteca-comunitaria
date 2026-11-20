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


def _probe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,format_name:stream=width,height,avg_frame_rate,codec_name,codec_type",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


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

    probe = _probe(path)
    video_stream = next(item for item in probe["streams"] if item["codec_type"] == "video")
    numerator, denominator = map(int, video_stream["avg_frame_rate"].split("/"))
    width, height = int(video_stream["width"]), int(video_stream["height"])
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
            "duration_seconds": float(probe["format"]["duration"]),
            "width": width,
            "height": height,
            "fps": numerator / denominator,
            "aspect_ratio": "16:9" if abs(width / height - 16 / 9) < 0.01 else "other",
            "orientation": "horizontal",
            "codec": video_stream["codec_name"],
            "container": "mp4",
            "has_audio": any(item["codec_type"] == "audio" for item in probe["streams"]),
            "size_bytes": path.stat().st_size,
            "sha256": digest,
            "perceptual_hash": "pending-m2",
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
