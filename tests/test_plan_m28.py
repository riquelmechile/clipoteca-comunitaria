"""M28 deterministic plan generation and M25 compatibility tests."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from clipoteca.studio.plan import NEG, build_plan, estimate_scenes, main, scene_job, write_plan
from clipoteca.studio.script import ScriptError, parse_video_script, script_id


FIXTURE_DIR = Path(__file__).parent / "fixtures/m28"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _script():
    return parse_video_script(_fixture("guion.json"))


def test_build_plan_matches_exact_five_scene_fixture() -> None:
    actual = build_plan(_script())
    expected = _fixture("plan_esperado.json")
    assert actual == expected
    assert list(actual) == [
        "version",
        "script_id",
        "title",
        "aspect",
        "language",
        "music",
        "voice",
        "bpm",
        "beat",
        "total_est",
        "scenes",
    ]
    assert len(actual["scenes"]) == 5
    assert actual["script_id"] == script_id(_script())
    assert actual["total_est"] == 21.0
    assert [x["len_est"] for x in actual["scenes"]] == [3, 4, 4.5, 4.5, 5]
    assert [x["start_est"] for x in actual["scenes"]] == [0, 3, 7, 11.5, 16]


def test_beat_quantization_and_fetch_margin() -> None:
    script = _script()
    rows = estimate_scenes(script)
    beat = 60 / script.music.bpm
    for item in rows:
        assert math.isclose(item["len_est"] / beat, round(item["len_est"] / beat))
        assert item["len_fetch"] == round(item["len_est"] + max(1.0, 0.12 * item["len_est"]), 2)
    assert rows[-1]["len_est"] >= 3.5
    assert all(row["len_est"] >= 4 * beat for row in rows[:-1])


def test_bpm_96_and_intro_minimum() -> None:
    data = _fixture("guion.json")
    data["music"]["bpm"] = 96
    script = parse_video_script(data)
    plan = build_plan(script)
    assert plan["beat"] == 0.625
    assert plan["bpm"] == 96.0
    assert all(
        abs(item["len_est"] / plan["beat"] - round(item["len_est"] / plan["beat"])) < 0.002
        for item in plan["scenes"]
    )
    assert plan["scenes"][0]["len_est"] >= 2.5


def test_scene_job_exact_contract_and_negative_prompts() -> None:
    script = _script()
    for scene, est in zip(script.scenes, estimate_scenes(script), strict=True):
        job = scene_job(script, scene, est)
        assert list(job) == [
            "version",
            "script_id",
            "id",
            "type",
            "title",
            "sub",
            "say",
            "caption",
            "aspect",
            "len_est",
            "len_fetch",
            "start_est",
            "queries",
            "prompt",
            "avoid",
            "negatives",
            "min_sim",
            "min_prob",
            "min_score",
            "catalog_min_sim",
            "top_n",
            "min_short_side",
        ]
        assert job["id"] == scene.id
        assert job["queries"] == {lang: list(scene.query[lang]) for lang in ("es", "en")}
        assert job["negatives"] == NEG
        assert job["min_sim"] == 0.22
        assert job["min_prob"] == 0.5
        assert job["min_score"] == 0.0
        assert job["catalog_min_sim"] == 0.27
        assert job["top_n"] == 4
        assert job["min_short_side"] == 720


def test_write_plan_atomic_deterministic_and_cli_one_line(tmp_path: Path, capsys) -> None:
    script = _script()
    result = write_plan(script, tmp_path)
    files = [tmp_path / "plan.json"] + [
        tmp_path / "scenes" / scene.id / "job.json" for scene in script.scenes
    ]
    before = [file.read_bytes() for file in files]
    assert json.loads(before[0]) == result
    write_plan(script, tmp_path)
    assert [file.read_bytes() for file in files] == before
    assert main(["--script", str(FIXTURE_DIR / "guion.json"), "--out", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "script_id": result["script_id"],
        "scenes": [scene.id for scene in script.scenes],
        "total_est": 21.0,
    }


@pytest.mark.parametrize("invalid_id", ["../escape", "escape/path", "a\\b", "..", ""])
def test_write_plan_rejects_unsafe_scene_id_before_writing(
    invalid_id: str,
    tmp_path: Path,
) -> None:
    script = _script()
    first = replace(script.scenes[0], id=invalid_id)
    unsafe = replace(script, scenes=(first, *script.scenes[1:]))
    with pytest.raises(ScriptError, match="scenes.id"):
        write_plan(unsafe, tmp_path)
    assert not list(tmp_path.iterdir())


def test_optional_catalog_writes_exact_deterministic_scene_assignments(
    tmp_path: Path,
    capsys,
) -> None:
    catalog = FIXTURE_DIR / "catalogo.json"
    expected = _fixture("asignacion_esperada.json")
    result = write_plan(_script(), tmp_path, catalog)
    assert result == _fixture("plan_esperado.json")
    output = tmp_path / "assignments.json"
    assert json.loads(output.read_text(encoding="utf-8")) == expected
    assert list(expected) == ["version", "script_id", "scenes"]
    saved_bytes = output.read_bytes()
    for scene in _script().scenes:
        local = json.loads((tmp_path / "scenes" / scene.id / "assignment.json").read_text())
        assert local == expected["scenes"][scene.id]
    write_plan(_script(), tmp_path, catalog)
    assert output.read_bytes() == saved_bytes
    assert (
        main(
            [
                "--script",
                str(FIXTURE_DIR / "guion.json"),
                "--out",
                str(tmp_path),
                "--catalog",
                str(catalog),
            ]
        )
        == 0
    )
    assert len(capsys.readouterr().out.splitlines()) == 1
    assert json.loads(output.read_text()) == expected


def test_without_catalog_has_no_assignment_side_effect(tmp_path: Path) -> None:
    write_plan(_script(), tmp_path)
    assert not (tmp_path / "assignments.json").exists()
    assert not list(tmp_path.glob("scenes/*/assignment.json"))
