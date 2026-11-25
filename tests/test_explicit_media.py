"""Contract, reservation and explicit video window regressions."""

import json
from pathlib import Path

import pytest

from clipoteca.studio.explicit_media import ExplicitMediaError, window_bounds
from clipoteca.studio.plan import write_plan
from clipoteca.studio.script import ScriptError, parse_video_script, script_id

FIXTURES = Path(__file__).parent / "fixtures"


def test_mixed_script_and_reserved_clip(tmp_path):
    data = json.loads((FIXTURES / "m28/guion.json").read_text())
    catalogue = FIXTURES / "m28/catalogo.json"
    original = script_id(parse_video_script(data))
    for scene, ref in zip(data["scenes"][:2], ("u77", "clip-100001"), strict=True):
        scene.pop("query")
        scene.pop("prompt")
        scene["media"] = {"ref": ref}
    script = parse_video_script(data)
    assert script_id(script) != original
    plan = write_plan(script, tmp_path, catalogue)
    assert plan["scenes"][0]["source_kind"] == "contribution"
    assert plan["scenes"][1]["source_kind"] == "catalog_pinned"
    assignments = json.loads((tmp_path / "assignments.json").read_text())["scenes"]
    assert assignments[script.scenes[0].id]["status"] == "contribution"
    assert assignments[script.scenes[1].id]["primary"]["clip_id"] == "clip-100001"
    for scene in script.scenes[2:]:
        row = assignments[scene.id]
        assert all(
            candidate["clip_id"] != "clip-100001"
            for candidate in [row["primary"], *row["backups"]]
            if candidate is not None
        )


@pytest.mark.parametrize("media", [{"ref": "../x"}, {"ref": "u77", "start": 4, "end": 3}])
def test_invalid_reference_or_window(media):
    data = json.loads((FIXTURES / "m25/valid_min.json").read_text())
    data["scenes"][0]["media"] = media
    with pytest.raises(ScriptError):
        parse_video_script(data)


def test_search_requires_fallback_queries():
    data = json.loads((FIXTURES / "m25/valid_min.json").read_text())
    scene = data["scenes"][0]
    scene["media"] = {"ref": "u77"}
    scene["on_error"] = "search"
    scene.pop("query")
    with pytest.raises(ScriptError):
        parse_video_script(data)


def test_explicit_window_and_short_video():
    assert window_bounds({"start": 1, "end": 5}, 7, 5) == (1, 5)
    assert window_bounds({}, 7, 5) == (0, 7)
    with pytest.raises(ExplicitMediaError, match="demasiado corto"):
        window_bounds({"start": 1, "end": 4}, 7, 5)
    with pytest.raises(ExplicitMediaError, match="fuera"):
        window_bounds({"end": 8}, 7, 5)
