"""Manual relocalization never touches release assets or unrelated clip records."""

import json
from pathlib import Path

import pytest

from clipoteca.metadata import MetadataTranslationError
from clipoteca.relocalize import relocalize_catalog


def test_relocalize_real_catalogue_fixture_without_media_download(tmp_path):
    original = Path(__file__).resolve().parents[1] / "data/clips.json"
    path = tmp_path / "clips.json"
    before = json.loads(original.read_text(encoding="utf-8"))
    path.write_text(json.dumps(before), encoding="utf-8")
    translated = relocalize_catalog(
        path,
        ["clip-000002", "clip-000003"],
        index_root=tmp_path / "index",
        language_overrides={"clip-000002": "es"},
        translator=lambda text, src, dst: (
            "Highways"
            if src == "es" and dst == "en"
            else "Autopistas"
            if src == "es" and dst == "es"
            else "Un hombre caminando bajo la lluvia"
            if dst == "es"
            else text
        ),
    )
    after = json.loads(path.read_text(encoding="utf-8"))
    assert translated == ["clip-000002", "clip-000003"]
    assert before[0] == after[0]
    assert after[1]["title"]["es"] == "Autopistas"
    assert after[1]["title"]["en"] == "Highways"
    assert after[1]["source"]["source_language"] == "es"
    assert after[2]["title"]["es"] == "Un hombre caminando bajo la lluvia"
    assert after[2]["title"]["en"] == "A man walking down a sidewalk in the rain"
    assert all(before[i]["storage"] == after[i]["storage"] for i in (1, 2))
    assert list((tmp_path / "index" / "es").glob("*.json"))


def test_failed_translation_does_not_mutate_catalog(tmp_path):
    original = Path(__file__).resolve().parents[1] / "data/clips.json"
    path = tmp_path / "clips.json"
    path.write_bytes(original.read_bytes())
    before = path.read_bytes()

    def broken(_text, _src, _dst):
        raise RuntimeError("translator unavailable")

    with pytest.raises(MetadataTranslationError):
        relocalize_catalog(
            path,
            ["clip-000002"],
            index_root=tmp_path / "index",
            language_overrides={"clip-000002": "es"},
            translator=broken,
        )
    assert path.read_bytes() == before
    assert not (tmp_path / "index").exists()
