from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from clipoteca.models import Clip

ROOT = Path(__file__).parents[1]


def schema(name: str) -> dict:
    return json.loads((ROOT / "schemas" / name).read_text())


def valid_clip() -> dict:
    return {
        "id": "clip-000125",
        "title": {"es": "Lluvia", "en": "Rain"},
        "description": {"es": "Calle mojada", "en": "Wet street"},
        "tags": {"es": ["lluvia"], "en": ["rain"]},
        "source": {
            "provider": "rutube",
            "provider_id": "source-id",
            "source_language": "ru",
            "source_title": "Дождь",
            "source_url": "https://example.org/123",
        },
        "media": {
            "duration_seconds": 18.2,
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "aspect_ratio": "9:16",
            "orientation": "vertical",
            "codec": "h264",
            "container": "mp4",
            "has_audio": False,
            "size_bytes": 123,
            "sha256": "a" * 64,
            "perceptual_hash": "b" * 16,
        },
        "storage": {
            "provider": "github_releases",
            "asset_url": "https://example.org/clip.mp4",
            "thumbnail_url": "https://example.org/thumb.webp",
        },
        "status": "published",
        "created_at": "2026-11-20T00:00:00Z",
    }


def test_clip_valid_and_model_fields_match() -> None:
    Draft202012Validator(schema("clip.schema.json")).validate(valid_clip())
    assert {field.name for field in fields(Clip)} == set(valid_clip())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda clip: clip["title"].pop("en"),
        lambda clip: clip["media"].update(aspect_ratio="4:3"),
        lambda clip: clip.update(status="processing"),
    ],
)
def test_clip_rejects_invalid_examples(mutation) -> None:
    clip = valid_clip()
    mutation(clip)
    with pytest.raises(ValidationError):
        Draft202012Validator(schema("clip.schema.json")).validate(clip)


def test_search_and_job_examples() -> None:
    Draft202012Validator(schema("search.schema.json")).validate(
        {
            "query": "lluvia",
            "filters": {"aspect_ratio": "9:16", "limit": 10, "import_if_missing": True},
        }
    )
    Draft202012Validator(schema("job.schema.json")).validate(
        {
            "request_id": "req-abc",
            "status": "searching",
            "language_counts": {"es": 1},
            "provider_counts": {},
            "source_errors": {},
        }
    )
