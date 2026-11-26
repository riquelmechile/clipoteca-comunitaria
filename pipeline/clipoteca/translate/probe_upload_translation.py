"""Report the real upload translation environment without logging user data or secrets."""

from __future__ import annotations

import json
import os
from importlib.metadata import version
from pathlib import Path

from .argos import _package_spec, _validate_translation, _verify_package


def main() -> int:
    source = "Un perro corriendo en el pasto"
    keys = (
        "HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "STANZA_RESOURCES_DIR",
        "ARGOS_DEVICE_TYPE",
        "ARGOS_CHUNK_TYPE",
        "ARGOS_PACKAGES_DIR",
        "ARGOS_MODEL_CACHE_DIR",
        "ARGOS_MODEL_PINS_JSON",
        "SPACY_DATA",
        "OMP_NUM_THREADS",
        "CT2_INTER_THREADS",
        "CT2_INTRA_THREADS",
    )
    print(json.dumps({"cwd": str(Path.cwd()), "env": {key: os.getenv(key) for key in keys}}))
    distributions = ("argostranslate", "ctranslate2", "sentencepiece", "stanza", "spacy")
    print(json.dumps({"versions": {name: version(name) for name in distributions}}))

    from argostranslate import package, settings, translate
    from stanza.resources import common

    names = ("package_data_dir", "package_dirs", "cache_dir", "data_dir", "device", "chunk_type")
    print(
        json.dumps(
            {
                "argos_settings": {name: str(getattr(settings, name, None)) for name in names},
                "stanza_default_dir": str(common.DEFAULT_MODEL_DIR),
                "spacy_cache": str(Path.home() / ".cache" / "spacy"),
            }
        )
    )
    path, checksum = _package_spec("es", "en")
    _verify_package(path, checksum, "es", "en")
    installed = package.get_installed_packages()
    if not any(p.from_code == "es" and p.to_code == "en" for p in installed):
        package.install_from_path(path)
    print(
        json.dumps(
            {
                "es_en_packages": [
                    {"path": str(p.package_path), "version": str(p.package_version)}
                    for p in package.get_installed_packages()
                    if p.from_code == "es" and p.to_code == "en"
                ],
                "verified_archive": str(path.resolve()),
            }
        )
    )
    raw = translate.translate(source, "es", "en")
    # JSON escapes line breaks and control characters; only a fixed synthetic
    # sentence is translated, never the input manifest or process environment.
    print(json.dumps({"raw_length": len(raw), "raw_preview": raw[:200]}, ensure_ascii=True))
    _validate_translation(source, raw, "es", "en")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
