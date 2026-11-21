"""M23 additive REST, JSON Schema and strict OpenAPI contracts (offline)."""

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from test_m22_r3_contracts import StrictLoader

ROOT = Path(__file__).resolve().parents[1]


def test_script_schema_validates_five_scenes_and_rejects_extra_fields():
    schema = json.loads((ROOT / "schemas/script.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    valid = {
        "scenes": [
            {
                "id": f"scene-{index}",
                "narration": "A real scene",
                "query": "rain",
                "orientation": "landscape",
                "min_duration": 8,
            }
            for index in range(5)
        ]
    }
    validator.validate(valid)
    with pytest.raises(Exception):
        validator.validate({"scenes": valid["scenes"] * 11})
    with pytest.raises(Exception):
        validator.validate({"scenes": [{**valid["scenes"][0], "forbidden": True}]})


def test_resolved_script_media_and_missing_schema():
    schema = json.loads((ROOT / "schemas/script.schema.json").read_text())
    validator = Draft202012Validator(schema)
    scene = {
        "id": "rain",
        "narration": "Storm",
        "query": "rain",
        "orientation": "16:9",
        "min_duration": 8,
        "status": "matched",
        "media": {
            "type": "video",
            "clip_id": "clip-000101",
            "url": "https://example.com/video.mp4",
            "thumbnail_url": None,
            "storyboard_urls": [],
            "duration_seconds": 12,
            "source": {"provider": "pexels", "source_language": "es"},
        },
    }
    resolved = {
        "script_id": "a" * 64,
        "scenes": [
            scene,
            {**scene, "id": "missing", "status": "missing", "media": None, "reason": "not_found"},
        ],
        "summary": {"matched": 1, "queued": 0, "missing": 1},
    }
    validator.validate(resolved)
    with pytest.raises(Exception):
        validator.validate(
            {**resolved, "scenes": [{**scene, "media": {**scene["media"], "type": "image"}}]}
        )


def test_openapi_additive_script_and_reason_enum_consistency():
    spec = yaml.load((ROOT / "docs/openapi.yaml").read_text(), Loader=StrictLoader)
    assert "resolveScript" == spec["paths"]["/v1/script"]["post"]["operationId"]
    assert spec["components"]["schemas"]["ScriptRequest"]["properties"]["scenes"]["maxItems"] == 50
    assert spec["components"]["schemas"]["ScriptResponse"]["properties"]["scenes"]["maxItems"] == 50
    search_schema = json.loads((ROOT / "schemas/search.schema.json").read_text())
    expected = search_schema["oneOf"][1]["properties"]["semantic_reason"]["enum"]
    assert (
        spec["components"]["schemas"]["SearchResponse"]["properties"]["semantic_reason"]["enum"]
        == expected
    )
