"""Remove tags lacking evidence in the recorded source metadata."""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

from clipoteca.catalog import Catalog


def words(value: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", value.lower())
    folded = "".join(char for char in folded if unicodedata.category(char) != "Mn")
    return set(re.findall(r"[^\W_]+", folded))


def clean_tags(clip: dict) -> bool:
    source = clip["source"]
    fragments = [
        *clip["title"].values(),
        *clip["description"].values(),
        source.get("source_title", ""),
        *source.get("keywords", []),
    ]
    evidence = set().union(*(words(value) for value in fragments if isinstance(value, str)))
    changed = False
    for language, tags in clip["tags"].items():
        cleaned = [tag for tag in tags if words(tag) and words(tag) <= evidence]
        if cleaned != tags:
            clip["tags"][language] = cleaned
            changed = True
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("data/clips.json"))
    parser.add_argument("--index-root", type=Path, default=Path("data/index"))
    args = parser.parse_args()
    catalog = Catalog.load(args.catalog)
    changed = [clip["id"] for clip in catalog.clips if clean_tags(clip)]
    catalog.save(args.catalog)
    catalog.write_indexes(args.index_root)
    print("\n".join(changed))


if __name__ == "__main__":
    main()
