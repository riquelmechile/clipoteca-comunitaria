"""M34 strong-match and delivery contracts with no models or network."""

import json

import pytest

from clipoteca.studio.selection import choose_result, select_scene


def row(sim, score, *, strong=False):
    return {"status": "ok", "catalog_strong": strong, "clip": {"sim": sim, "score": score}}


@pytest.mark.parametrize(
    "catalog, imported, expected",
    [
        (row(0.27, 1, strong=True), row(0.9, 9), "catalog"),
        (row(0.2424, 2.44), row(0.4, 1), "imported"),
        (row(0.26, 3), row(0.25, 9), "catalog"),
        (row(0.25, 1), None, "catalog"),
        ({"status": "fail"}, row(0.3, 2), "imported"),
        (row(0.25, 1), {"status": "fail"}, "catalog"),
    ],
)
def test_selection_policy(catalog, imported, expected):
    assert choose_result(catalog, imported)[0] == expected


def test_weak_catalogue_survives_failed_import(tmp_path):
    (tmp_path / "job.json").write_text("{}")
    (tmp_path / "assignment.json").write_text("{}")
    calls = []

    def runner(job, assignment, out, **kwargs):
        calls.append(job["catalog_min_sim"])
        out.mkdir(exist_ok=True)
        result = row(0.25, 3)
        (out / "result.json").write_text(json.dumps(result))
        (out / "clip.mp4").write_bytes(b"catalogue")
        return result

    assert select_scene(tmp_path, "catalog", runner=runner)["source"] == "catalog"
    assert select_scene(tmp_path, "finish", runner=runner)["source"] == "catalog"
    assert (tmp_path / "clip.mp4").read_bytes() == b"catalogue"
    assert calls == [0.27]
