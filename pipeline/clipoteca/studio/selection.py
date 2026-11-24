"""Scene policy: strong catalogue match, otherwise compare imported and catalogue clips."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .clipscene import ClipSceneError, _atomic_json, clip_scene


def choose_result(catalog, imported=None):
    """Choose the most pertinent valid clip; visual score is the tie-breaker."""
    if catalog.get("status") == "ok" and catalog.get("catalog_strong"):
        return "catalog", catalog
    valid = [
        (name, row)
        for name, row in (("catalog", catalog), ("imported", imported))
        if row is not None and row.get("status") == "ok"
    ]
    if not valid:
        return "imported" if imported is not None else "catalog", imported or catalog
    return max(valid, key=lambda pair: (pair[1]["clip"]["sim"], pair[1]["clip"]["score"]))


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def select_scene(root, phase, *, exclude=(), runner=clip_scene):
    root = Path(root)
    job = _read(root / "job.json")
    job.setdefault("catalog_min_sim", 0.27)
    folders = {"catalog": root / "selection-catalog", "imported": root / "selection-imported"}
    (root / "result.json").unlink(missing_ok=True)
    (root / "clip.mp4").unlink(missing_ok=True)
    if phase in ("catalog", "rerun"):
        catalog = runner(
            job, _read(root / "assignment.json"), folders["catalog"], exclude=exclude
        )
    else:
        catalog = _read(folders["catalog"] / "result.json")
    imported = None
    assignment = root / "import" / "assignment.json"
    if (
        phase != "catalog"
        and not catalog.get("catalog_strong")
        and assignment.is_file()
    ):
        imported = runner(
            job,
            _read(assignment),
            folders["imported"],
            exclude=exclude,
            local_root=root / "import",
        )
    origin, result = choose_result(catalog, imported)
    result = dict(result)
    if result.get("status") == "ok":
        result["source"] = origin
        # Preserve the catalogue report on disk for the next phase, not its duplicate MP4.
        source = folders[origin] / "clip.mp4"
        if phase == "catalog":
            import shutil

            shutil.copyfile(source, root / "clip.mp4")
        else:
            os.replace(source, root / "clip.mp4")
    _atomic_json(root / "result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--phase", choices=("catalog", "finish", "rerun"), required=True)
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        result = select_scene(args.scene, args.phase, exclude=args.exclude)
    except (ClipSceneError, OSError, ValueError, KeyError):
        parser.exit(2, "selection: cannot evaluate scene or invalid input\n")
    return 0 if result["status"] == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
