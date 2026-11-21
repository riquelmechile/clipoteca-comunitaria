"""Pinned Argos models are not installed or accepted without verified bytes."""

import hashlib
import json

import pytest
from clipoteca.translate.download_models import verify_or_download


def test_existing_pinned_model_is_verified_without_network(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    name = "translate-es_en-1_9.argosmodel"
    (cache / name).write_bytes(b"model")
    pins = {}
    for pair, version in (
        ("es_en", "1.9"),
        ("en_es", "1.0"),
        ("en_zh", "1.9"),
        ("zh_en", "1.9"),
        ("en_ru", "1.9"),
        ("ru_en", "1.9"),
    ):
        model = "translate-" + pair
        file = model + "-" + version.replace(".", "_") + ".argosmodel"
        (cache / file).write_bytes(b"model")
        pins[pair] = {
            "name": model,
            "version": version,
            "url": "https://argos-net.com/v1/" + file,
            "sha256": hashlib.sha256(b"model").hexdigest(),
        }
    manifest = tmp_path / "pins.json"
    manifest.write_text(json.dumps(pins), encoding="utf-8")

    def no_network(*args, **kwargs):
        raise AssertionError("verified models must not hit network")

    monkeypatch.setattr("urllib.request.urlopen", no_network)
    assert len(verify_or_download(manifest, cache)) == 6


def test_invalid_model_url_is_rejected_before_download(tmp_path):
    manifest = tmp_path / "pins.json"
    manifest.write_text(
        json.dumps(
            {
                "es_en": {
                    "name": "translate-es_en",
                    "version": "1.9",
                    "url": "https://evil.test/fake",
                    "sha256": "0" * 64,
                }
            }
        )
    )
    with pytest.raises(ValueError, match="invalid pinned"):
        verify_or_download(manifest, tmp_path / "cache")
