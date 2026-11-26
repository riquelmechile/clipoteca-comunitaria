"""Shared test vectors for the Worker interpreter and Python's reference validator."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/video.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
CASES = json.loads((ROOT / "tests/fixtures/video-schema-parity.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_shared_video_schema_case(case):
    assert VALIDATOR.is_valid(case["value"]) == case["valid"]


@pytest.mark.parametrize("path", sorted((ROOT / "docs/ejemplos").glob("*.json")))
def test_documented_example(path):
    assert VALIDATOR.is_valid(json.loads(path.read_text(encoding="utf-8")))
