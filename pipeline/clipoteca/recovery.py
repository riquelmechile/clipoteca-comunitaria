"""Operator-only catalogue rollback from a reviewed, validated JSON snapshot.

Never mutates Releases or unverified assets. Check the expected current SHA256 to
prevent overwriting concurrent changes; stage indexes before touching live paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from .catalog import Catalog, SCHEMA_PATH


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def restore_catalog_snapshot(
    *,
    catalog_path: Path,
    snapshot_path: Path,
    index_root: Path,
    expected_current_sha256: str,
) -> int:
    if not catalog_path.is_file() or not snapshot_path.is_file():
        raise ValueError("catalogue and snapshot must be existing files")
    if catalog_path == snapshot_path:
        raise ValueError("snapshot and live catalogue must differ")
    if len(expected_current_sha256) != 64 or any(
        digit not in "0123456789abcdef" for digit in expected_current_sha256
    ):
        raise ValueError("invalid expected SHA256")
    if file_sha256(catalog_path) != expected_current_sha256:
        raise ValueError("current catalogue changed; refusing stale rollback")

    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("snapshot must be a catalogue array")
    validator = Draft202012Validator(
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
        format_checker=FormatChecker(),
    )
    ids: set[str] = set()
    for clip in data:
        validator.validate(clip)
        if clip["id"] in ids:
            raise ValueError("duplicate ID in rollback snapshot")
        ids.add(clip["id"])

    catalog = Catalog(data)
    # Prevent accidental writes to an index directory holding uncommitted content.
    if index_root.resolve() == catalog_path.parent.resolve():
        raise ValueError("index root must differ from catalogue parent")
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    index_root.parent.mkdir(parents=True, exist_ok=True)

    # Both catalogue and indexes should share a filesystem for atomic renames.
    with tempfile.TemporaryDirectory(
        prefix=".clipoteca-rollback-", dir=catalog_path.parent
    ) as directory:
        stage = Path(directory)
        staged_catalog = stage / "clips.json"
        staged_index = stage / "index"
        backup_index = stage / "previous-index"
        catalog.save(staged_catalog)
        catalog.write_indexes(staged_index)
        old_index_moved = False
        new_index_installed = False
        try:
            if index_root.exists():
                os.replace(index_root, backup_index)
                old_index_moved = True
            os.replace(staged_index, index_root)
            new_index_installed = True
            # Recheck CAS against a concurrent writer immediately before commit.
            if file_sha256(catalog_path) != expected_current_sha256:
                raise ValueError("catalogue changed during rollback")
            os.replace(staged_catalog, catalog_path)
        except Exception:
            if new_index_installed:
                shutil.rmtree(index_root)
            if old_index_moved:
                os.replace(backup_index, index_root)
            raise
    return len(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage reviewed catalogue rollback locally; never push to GitHub"
    )
    parser.add_argument("--catalog", default="data/clips.json")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--index-root", default="data/index")
    parser.add_argument("--expect-sha256", required=True)
    args = parser.parse_args(argv)
    count = restore_catalog_snapshot(
        catalog_path=Path(args.catalog),
        snapshot_path=Path(args.snapshot),
        index_root=Path(args.index_root),
        expected_current_sha256=args.expect_sha256,
    )
    print(json.dumps({"restored_clips": count, "pushed_to_github": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
