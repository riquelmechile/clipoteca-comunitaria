import json

from clipoteca.repair_upload_metadata import repair_index


def test_repair_index_preserves_assets_and_is_idempotent(tmp_path, monkeypatch):
    def translate(value, src, dst):
        assert (src, dst) == ("es", "en")
        return {"Lluvia en el campo": "Rain in the field", "lluvia": "rain"}[value]

    monkeypatch.setattr("clipoteca.media_ingest.translate_text", translate)
    row = {
        "media_id": "original",
        "origin": "upload",
        "status": "registered",
        "url": "https://example.org/original.media",
        "sha256": "a" * 64,
        "description": {"es": "Lluvia en el campo"},
        "tags": {"es": ["lluvia"]},
        "metadata_auto": ["tags.es"],
    }
    other = dict(row, media_id="other", origin="other")
    path = tmp_path / "aportes.json"
    path.write_text(json.dumps([row, other]), encoding="utf-8")
    original = path.read_bytes()
    assert repair_index(path) == (1, 0)
    assert path.read_bytes() == original
    assert repair_index(path, write=True) == (1, 0)
    result = json.loads(path.read_bytes())
    assert result == [
        dict(
            row,
            description={"es": "Lluvia en el campo", "en": "Rain in the field"},
            tags={"es": ["lluvia"], "en": ["rain"]},
            metadata_auto=["tags.es", "description.en", "tags.en"],
        ),
        other,
    ]
    saved = path.read_bytes()
    assert repair_index(path, write=True) == (0, 0)
    assert path.read_bytes() == saved


def test_repair_failure_does_not_remove_existing_metadata(tmp_path, monkeypatch):
    from clipoteca.translate.argos import TranslationFailed

    def translate(value, src, dst):
        raise TranslationFailed(src, dst, "package_missing")

    monkeypatch.setattr("clipoteca.media_ingest.translate_text", translate)
    row = {
        "media_id": "original",
        "origin": "upload",
        "status": "registered",
        "description": {"es": "Lluvia", "en": "Rain"},
        "tags": {"es": ["lluvia"]},
        "metadata_auto": ["description.en", "tags.es"],
    }
    path = tmp_path / "aportes.json"
    path.write_text(json.dumps([row]), encoding="utf-8")
    original = path.read_bytes()
    assert repair_index(path, write=True) == (0, 1)
    assert path.read_bytes() == original


def test_repair_explicit_empty_english_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("clipoteca.media_ingest.translate_text", lambda *args: "Rain")
    row = {
        "media_id": "original",
        "origin": "upload",
        "status": "registered",
        "description": {"es": "Lluvia", "en": ""},
        "tags": {"es": ["lluvia"], "en": []},
    }
    path = tmp_path / "aportes.json"
    path.write_text(json.dumps([row]), encoding="utf-8")
    assert repair_index(path, write=True) == (1, 0)
    result = json.loads(path.read_bytes())[0]
    assert result["description"] == {"es": "Lluvia", "en": "Rain"}
    assert result["tags"] == {"es": ["lluvia"], "en": ["Rain"]}
