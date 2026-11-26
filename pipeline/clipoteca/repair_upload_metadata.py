"""Repair missing English metadata in the local upload index, without publishing assets."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from .media_ingest import clean_metadata, enrich_metadata


def repair_rows(rows: list[dict]) -> tuple[list[dict], int, int]:
    repaired = []
    changed = pending = 0
    for row in rows:
        description = row.get("description", {})
        tags = row.get("tags", {})
        if (
            row.get("origin") == "upload"
            and row.get("status") == "registered"
            and description.get("es")
            and (not description.get("en") or not tags.get("en"))
        ):
            updated = enrich_metadata(row)
            changed += updated != row
            pending += not (updated["description"].get("en") and updated["tags"].get("en"))
            repaired.append(updated)
        else:
            repaired.append(row)
    return repaired, changed, pending


def repair_verified_rows(rows: list[dict], overrides: dict) -> tuple[list[dict], int, int]:
    """Apply only operator-reviewed English metadata to matching source records."""
    if not isinstance(overrides, dict) or not overrides:
        raise ValueError("expected nonempty overrides keyed by media_id")
    repaired = []
    seen = set()
    changed = 0
    for row in rows:
        media_id = row.get("media_id")
        if media_id not in overrides:
            repaired.append(row)
            continue
        if media_id in seen or row.get("origin") != "upload" or row.get("status") != "registered":
            raise ValueError("override must match one registered upload")
        seen.add(media_id)
        spec = overrides[media_id]
        description = dict(row.get("description", {}))
        tags = dict(row.get("tags", {}))
        automatic = list(row.get("metadata_auto", []))
        if description.get("es") != spec["description_es"] or tags.get("es") != spec["tags_es"]:
            raise ValueError("source metadata changed; review the override again")
        english = spec["description_en"]
        english_tags = spec["tags_en"]
        if (
            not clean_metadata(english, 400)
            or not isinstance(english_tags, list)
            or not 1 <= len(english_tags) <= 8
            or not all(clean_metadata(tag, 40) for tag in english_tags)
        ):
            raise ValueError("invalid reviewed English metadata")
        if description.get("en") and description["en"] != english:
            raise ValueError("existing English description differs; review manually")
        previous_tags = tags.get("en", [])
        if previous_tags and previous_tags != english_tags and (
            "tags.en" not in automatic or not set(previous_tags) <= set(english_tags)
        ):
            raise ValueError("existing English tags differ; review manually")
        if not description.get("en"):
            description["en"] = english
            automatic = [field for field in automatic if field != "description.en"]
        if previous_tags != english_tags:
            tags["en"] = english_tags
            automatic = [field for field in automatic if field != "tags.en"]
        updated = dict(row, description=description, tags=tags, metadata_auto=automatic)
        changed += updated != row
        repaired.append(updated)
    if seen != set(overrides):
        raise ValueError("override references a missing upload")
    return repaired, changed, 0


def repair_index(
    path: Path, *, write: bool = False, overrides: dict | None = None
) -> tuple[int, int]:
    original = path.read_bytes()
    rows = json.loads(original)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("expected an array of upload records")
    if overrides is None:
        repaired, changed, pending = repair_rows(rows)
    else:
        repaired, changed, pending = repair_verified_rows(rows, overrides)
    if write and changed:
        # Do not truncate the index if the process fails during serialization.
        content = json.dumps(repaired, ensure_ascii=True, separators=(",", ":")) + "\n"
        fd, name = tempfile.mkstemp(prefix=".aportes-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(path.stat().st_mode & 0o777)
            if path.read_bytes() != original:
                raise RuntimeError("index changed during repair; retry on a fresh checkout")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return changed, pending


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=Path("data/aportes.json"))
    parser.add_argument("--write", action="store_true", help="update the local index atomically")
    parser.add_argument(
        "--reviewed-overrides", type=Path, help="apply only explicitly reviewed matching records"
    )
    args = parser.parse_args()
    overrides = (
        json.loads(args.reviewed_overrides.read_text(encoding="utf-8"))
        if args.reviewed_overrides is not None
        else None
    )
    changed, pending = repair_index(args.index, write=args.write, overrides=overrides)
    print(f"changed={changed} pending={pending} write={args.write}")
    return 1 if pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
