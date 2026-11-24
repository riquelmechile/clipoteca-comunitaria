"""Retire catalogue entries without deleting their media or reusing IDs."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from clipoteca.catalog import Catalog


def retire_clip(catalog: Catalog, identifier: str, reason: str, date: str) -> None:
    if not reason.strip():
        raise ValueError("retirement reason must not be empty")
    for clip in catalog.clips:
        if clip["id"] == identifier:
            if clip.get("status") != "published":
                raise ValueError("clip is not published")
            clip["status"] = "retired"
            clip["retirement"] = {"reason": reason.strip(), "date": date}
            return
    raise ValueError("unknown clip id")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("id")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--catalog", type=Path, default=Path("data/clips.json"))
    parser.add_argument("--index-root", type=Path, default=Path("data/index"))
    args = parser.parse_args()
    catalog = Catalog.load(args.catalog)
    retire_clip(catalog, args.id, args.reason, datetime.now(UTC).date().isoformat())
    catalog.save(args.catalog)
    catalog.write_indexes(args.index_root)
    print(args.id)


if __name__ == "__main__":
    main()
