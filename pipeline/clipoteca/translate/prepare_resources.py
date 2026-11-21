"""Install verified Argos packages and pre-warm sentence segmentation in Actions.

Keep this setup shared by ingestion, relocalization, and community contributions.
The application must not discover Stanza/SpaCy resources during its first import.
"""

from __future__ import annotations

import os
from pathlib import Path

from .download_models import verify_or_download


def main() -> None:
    pins = Path(os.environ.get("ARGOS_MODEL_PINS_JSON", "data/argos-pins.json"))
    cache = Path(os.environ.get("ARGOS_MODEL_CACHE_DIR", ".cache/argos"))
    verified = verify_or_download(pins, cache)

    # Argos 1.10 may use SpaCy if the packaged Stanza segmenter cannot load.
    # Preload both mechanisms so the first real translation has no cold fetch.
    from argostranslate.networking import cache_spacy  # type: ignore[import-untyped]

    if cache_spacy() is None:
        raise RuntimeError("spacy_sentence_resources_unavailable")

    import stanza  # type: ignore[import-untyped]
    from argostranslate.sbd import get_stanza_processors  # type: ignore[import-untyped]

    for language in ("es", "en", "zh", "ru"):
        # Download the resources manifest before mirroring Argos's MWT selection.
        stanza.download(language, processors="tokenize", logging_level="ERROR")
        resources = stanza.resources.common.load_resources_json()
        processors = get_stanza_processors(language, resources)
        if processors != "tokenize":
            stanza.download(language, processors=processors, logging_level="ERROR")
        # Fail at setup, not at the first user contribution.
        stanza.Pipeline(
            lang=language,
            processors=processors,
            use_gpu=False,
            logging_level="ERROR",
            download_method=stanza.DownloadMethod.REUSE_RESOURCES,
        )
    print(f"Prepared {len(verified)} pinned Argos packages and sentence resources")


if __name__ == "__main__":
    main()
