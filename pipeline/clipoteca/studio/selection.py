"""Scene policy: strong catalogue match, otherwise compare imported and catalogue clips."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .clipscene import ClipSceneError, _atomic_json, clip_scene


def choose_result(catalog, imported=None):
    """Prefer strong catalogue, then valid imports, then weak catalogue."""
    if catalog.get("status") == "ok" and catalog.get("catalog_strong"):
        return "catalog", catalog
    if imported is not None and imported.get("status") == "ok":
        if catalog.get("status") == "ok":
            cat_degraded = bool(catalog.get("degraded_reason"))
            imp_degraded = bool(imported.get("degraded_reason"))
            if cat_degraded != imp_degraded:
                return ("imported", imported) if cat_degraded else ("catalog", catalog)
            if cat_degraded and (catalog["clip"]["sim"], catalog["clip"]["score"]) > (
                imported["clip"]["sim"],
                imported["clip"]["score"],
            ):
                return "catalog", catalog
        return "imported", imported
    if catalog.get("status") == "ok":
        return "catalog", catalog
    return "imported" if imported is not None else "catalog", imported or catalog


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def select_scene(root, phase, *, exclude=(), runner=clip_scene):
    root = Path(root)
    job = _read(root / "job.json")
    job.setdefault("catalog_min_sim", 0.27)
    # Keep the best rendered degraded candidate while normal import/retry still runs.
    job["_allow_fallback"] = True
    folders = {"catalog": root / "selection-catalog", "imported": root / "selection-imported"}
    (root / "result.json").unlink(missing_ok=True)
    (root / "clip.mp4").unlink(missing_ok=True)
    if phase in ("catalog", "rerun"):
        catalog = runner(job, _read(root / "assignment.json"), folders["catalog"], exclude=exclude)
    else:
        catalog = _read(folders["catalog"] / "result.json")
    imported = None
    imported_folder = None
    tried = list(catalog.get("tried", []))
    if phase != "catalog" and not catalog.get("catalog_strong"):
        for name in ("import", "import-retry"):
            assignment = root / name / "assignment.json"
            if not assignment.is_file():
                continue
            folder = folders["imported"] / name
            candidate = runner(
                job,
                _read(assignment),
                folder,
                exclude=exclude,
                local_root=root / name,
            )
            tried.extend(candidate.get("tried", []))
            if imported is None or (
                candidate.get("status") == "ok"
                and (
                    imported.get("status") != "ok"
                    or (
                        (
                            not candidate.get("degraded_reason"),
                            not candidate.get("degraded_reason")
                            and candidate["clip"].get("name_match", False),
                            candidate["clip"]["sim"],
                            candidate["clip"]["score"],
                            not candidate["clip"].get("related_match", False),
                        )
                        > (
                            not imported.get("degraded_reason"),
                            not imported.get("degraded_reason")
                            and imported["clip"].get("name_match", False),
                            imported["clip"]["sim"],
                            imported["clip"]["score"],
                            not imported["clip"].get("related_match", False),
                        )
                    )
                )
            ):
                imported = candidate
                imported_folder = folder
    origin, result = choose_result(catalog, imported)
    result = dict(result)
    result["tried"] = tried
    result["needs_retry"] = (
        not catalog.get("catalog_strong")
        and (
            imported is None
            or imported.get("status") != "ok"
            or bool(imported.get("degraded_reason"))
        )
        and not (root / "import-retry" / "imported.json").exists()
    )
    if result.get("status") == "ok":
        result["source"] = origin
        result["fallback"] = bool(result.get("degraded_reason")) or (
            origin == "catalog" and not catalog.get("catalog_strong", False)
        )
        if result["fallback"]:
            result.setdefault("degraded_reason", "weak_catalog_match")
        if origin == "imported":
            result["import_round"] = imported_folder.name
            source = imported_folder / "clip.mp4"
        else:
            source = folders["catalog"] / "clip.mp4"
        shutil.copyfile(source, root / "clip.mp4")
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
    except ClipSceneError as exc:
        parser.exit(2, f"selection: {exc}\n")
    except (OSError, ValueError, KeyError):
        parser.exit(2, "selection: cannot evaluate scene or invalid input\n")
    return 0 if result["status"] == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
