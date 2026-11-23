"""M25: offline validation, canonical identity and duration contract."""

from __future__ import annotations

import copy
import json
from dataclasses import fields
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from clipoteca.studio.script import (
    MAX_SCRIPT_BYTES,
    MusicSpec,
    SceneSpec,
    ScriptError,
    VideoScript,
    VoiceSpec,
    estimate_durations,
    parse_video_script,
    script_id,
)

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "m25"


def read_fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["valid_min.json", "valid_full.json"])
def test_valid_fixtures(name: str) -> None:
    data = read_fixture(name)
    schema = json.loads((ROOT / "schemas" / "video.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(data)
    script = parse_video_script(data)
    assert isinstance(script, VideoScript)
    assert all(isinstance(scene, SceneSpec) for scene in script.scenes)
    assert script.language == "es"
    assert len(script_id(script)) == 64


def test_defaults_and_overrides() -> None:
    minimal = parse_video_script(read_fixture("valid_min.json"))
    assert minimal.music == MusicSpec(mood="upbeat", bpm=120)
    assert minimal.voice == VoiceSpec(voice="F1", speed=1.2, lexicon={})
    assert minimal.scenes[0].caption == minimal.scenes[0].say
    assert minimal.scenes[0].sub == ""
    assert minimal.scenes[0].avoid == ()

    full = parse_video_script(read_fixture("valid_full.json"))
    assert full.music == MusicSpec(mood="epic", bpm=100)
    assert full.voice.voice == "M3"
    assert full.voice.speed == 1.0
    assert full.voice.lexicon == {"H2O": "agua", "CLIP": "clip"}
    assert full.scenes[0].caption == "El ciclo del agua"
    assert "\n" in full.scenes[0].say


@pytest.mark.parametrize("case", read_fixture("invalid_cases.json"), ids=lambda x: x["name"])
def test_invalid_fixtures(case: dict) -> None:
    with pytest.raises(ScriptError) as error:
        parse_video_script(case["script"])
    assert case["error_contains"] in str(error.value)
    assert len(str(error.value)) < 160  # no echoed script contents


def test_size_limit_and_non_json_input() -> None:
    data = read_fixture("valid_min.json")
    data["scenes"][0]["say"] = "z" * (MAX_SCRIPT_BYTES + 1)
    with pytest.raises(ScriptError, match="65536"):
        parse_video_script(data)
    with pytest.raises(ScriptError, match="script"):
        parse_video_script({"title": object()})


def test_control_characters_in_nested_text_and_keys() -> None:
    for path in ("say", "caption", "prompt"):
        data = read_fixture("valid_min.json")
        data["scenes"][0][path] = "un\ttexto"
        with pytest.raises(ScriptError, match=path):
            parse_video_script(data)
    data = read_fixture("valid_min.json")
    data["voice"] = {"lexicon": {"x\u0007": "z"}}
    with pytest.raises(ScriptError, match="lexicon"):
        parse_video_script(data)


def test_duration_exact_minimum_and_speed() -> None:
    data = read_fixture("valid_min.json")
    for scene, count in zip(data["scenes"], [12, 50, 100], strict=True):
        scene["say"] = "x" * count
    script = parse_video_script(data)
    assert estimate_durations(script) == {
        "scenes": {"fact-1": 1.5, "fact-2": 2.71, "fact-3": 5.42},
        "total": 9.63,
    }
    data["voice"] = {"speed": 1.0}
    assert estimate_durations(parse_video_script(data)) == {
        "scenes": {"fact-1": 1.5, "fact-2": 3.25, "fact-3": 6.5},
        "total": 11.25,
    }


def test_canonical_id_and_default_equivalence() -> None:
    data = read_fixture("valid_min.json")
    original = script_id(parse_video_script(data))
    reversed_keys = json.loads(json.dumps(data, sort_keys=True))
    assert script_id(parse_video_script(reversed_keys)) == original
    with_defaults = copy.deepcopy(data)
    with_defaults["language"] = "es"
    with_defaults["music"] = {"mood": "upbeat", "bpm": 120}
    with_defaults["voice"] = {"voice": "F1", "speed": 1.2, "lexicon": {}}
    assert script_id(parse_video_script(with_defaults)) == original
    data["scenes"][0]["say"] = "Lluvia fuerte."
    assert script_id(parse_video_script(data)) != original


def test_python_dataclass_fields_match_schema() -> None:
    schema = json.loads((ROOT / "schemas" / "video.schema.json").read_text())
    scene = schema["$defs"]["scene"]["properties"]
    assert set(schema["properties"]) == {field.name for field in fields(VideoScript)}
    assert set(schema["properties"]["music"]["properties"]) == {
        field.name for field in fields(MusicSpec)
    }
    assert set(schema["properties"]["voice"]["properties"]) == {
        field.name for field in fields(VoiceSpec)
    }
    assert set(scene) == {field.name for field in fields(SceneSpec)}


def test_schema_objects_are_closed() -> None:
    schema = json.loads((ROOT / "schemas" / "video.schema.json").read_text())

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(schema)


def test_unpaired_surrogates_raise_field_specific_script_error() -> None:
    for field, path in [
        ("title", "script.title"),
        ("say", "script.scenes[0].say"),
        ("caption", "script.scenes[0].caption"),
    ]:
        data = read_fixture("valid_min.json")
        if field == "title":
            data["title"] = "broken\\ud800".encode("utf-8").decode("unicode_escape")
        else:
            data["scenes"][0][field] = "broken\\udfff".encode("utf-8").decode("unicode_escape")
        with pytest.raises(ScriptError) as exc:
            parse_video_script(data)
        assert path in str(exc.value)


def test_nonstring_keys_are_rejected_at_every_level() -> None:
    cases = [
        (lambda d: d.update({99: "invalid"}), "script.key"),
        (lambda d: d["scenes"][0].update({99: "invalid"}), "script.scenes[0].key"),
        (lambda d: d["scenes"][0]["query"].update({99: ["x"]}), "script.scenes[0].query.key"),
        (lambda d: d.setdefault("music", {}).update({99: 120}), "script.music.key"),
    ]
    for mutate, path in cases:
        data = read_fixture("valid_min.json")
        mutate(data)
        with pytest.raises(ScriptError) as exc:
            parse_video_script(data)
        assert path in str(exc.value)


def test_integral_float_bpm_matches_int_and_fraction_is_invalid() -> None:
    raw = read_fixture("valid_min.json")
    int_script = parse_video_script(raw)
    raw["music"] = {"bpm": 120.0}
    float_script = parse_video_script(raw)
    assert float_script.music.bpm == 120
    assert isinstance(float_script.music.bpm, int)
    assert script_id(float_script) == script_id(int_script)
    raw["music"]["bpm"] = 120.5
    with pytest.raises(ScriptError, match="music.bpm"):
        parse_video_script(raw)


def test_parsed_collections_immutable_and_ids_backward_compatible() -> None:
    expected = {
        "valid_full.json": "52f43627b0ec989f9de45e84cd8fc9be50e84cc0deff760298a2f9b16e1d4af5",
        "valid_min.json": "041fde1b30a75bd9b63fdc57447034f480102482f9eb76b60882835fd611f6d7",
    }
    for name, old_sha in expected.items():
        raw = read_fixture(name)
        parsed = parse_video_script(raw)
        assert script_id(parsed) == old_sha
        before = script_id(parsed)
        assert isinstance(dict(parsed.voice.lexicon), dict)
        assert isinstance(parsed.scenes[0].query["es"], tuple)
        with pytest.raises(TypeError):
            parsed.voice.lexicon["X"] = "otra"  # type: ignore[index]
        with pytest.raises(TypeError):
            parsed.scenes[0].query["es"] = ("otra",)  # type: ignore[index]
        raw["scenes"][0]["query"]["es"][0] = "otra"
        assert script_id(parsed) == before
