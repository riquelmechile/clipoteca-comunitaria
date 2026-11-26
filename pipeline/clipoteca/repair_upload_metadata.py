"""Repair missing English metadata in the local upload index, without publishing assets."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from .media_ingest import enrich_metadata


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


def repair_index(path: Path, *, write: bool = False) -> tuple[int, int]:
    original = path.read_bytes()
    rows = json.loads(original)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("expected an array of upload records")
    repaired, changed, pending = repair_rows(rows)
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
    args = parser.parse_args()
    changed, pending = repair_index(args.index, write=args.write)
    print(f"changed={changed} pending={pending} write={args.write}")
    return 1 if pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
