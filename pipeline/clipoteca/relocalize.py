"""Rebuild only factual ES/EN text on existing verified catalogue entries.

This does not download media, create release assets or allocate new clip IDs.
All translations must succeed before any catalogue/index file is replaced.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Callable

from .catalog import Catalog
from .metadata import localize_candidate
from .providers.base import Candidate


def relocalize_catalog(
    catalog_path: Path,
    identifiers: list[str],
    *,
    index_root: Path,
    language_overrides: dict[str, str] | None = None,
    translator: Callable[[str, str, str], str] | None = None,
) -> list[str]:
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise ValueError("expected distinct nonempty clip IDs")
    if any(not re.fullmatch(r"clip-\d{6}", item) for item in identifiers):
        raise ValueError("invalid clip id")
    overrides = language_overrides or {}
    if set(overrides) - set(identifiers):
        raise ValueError("language override must match requested id")
    if any(language not in {"es", "en", "zh", "ru"} for language in overrides.values()):
        raise ValueError("unsupported source language override")

    original = Catalog.load(catalog_path)
    available = {item["id"]: item for item in original.clips}
    if set(identifiers) - set(available):
        raise ValueError("unknown clip id")
    updated = copy.deepcopy(original.clips)
    for clip in updated:
        if clip["id"] not in identifiers:
            continue
        source = clip["source"]
        source_language = overrides.get(clip["id"], source["source_language"])
        candidate = Candidate(
            provider=source["provider"],
            provider_id=source["provider_id"],
            term=source["source_title"],
            language=source_language,
            source_url=source["source_url"],
            source_title=source["source_title"],
            source_language=source_language,
        )
        localized = localize_candidate(candidate, translator=translator)
        clip["title"] = localized.title
        clip["description"] = localized.description
        # Replace stale machine-generated tags, including corrupted Argos
        # output, rather than merging those values back into the new index.
        clip["tags"] = localized.tags
        clip["source"]["source_language"] = source_language

    # Store atomically only after all Argos translations returned valid text.
    result = Catalog(updated)
    result.save(catalog_path)
    result.write_indexes(index_root)
    return identifiers
