"""Local video delivery manifest, never a remote video publication."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..catalog import Catalog
from .index import _atomic, _digest, _read, _validator
from .script import parse_video_script, script_id


def _timings(work):
    path = work / "timings.json"
    timings = _read(path) if path.is_file() else {}
    for folder in sorted((work / "scenes").glob("*")):
        for path in sorted(folder.glob("timing*.json")):
            for key, value in _read(path).items():
                timings[f"scene.{folder.name}.{key}"] = value
        for name, stage in (("import", "import"), ("import-retry", "import_retry")):
            manifest = folder / name / "imported.json"
            if not manifest.is_file():
                continue
            report = _read(manifest)
            for key in (
                "time_s",
                "searches_started",
                "searches_avoided",
                "searches_cancelled",
                "downloads_started",
                "downloads_avoided",
            ):
                if key in report:
                    timings[f"scene.{folder.name}.{stage}.{key}"] = report[key]
            reason = report.get("termination_reason")
            if reason in {"enough", "max_results", "timeout", "exhausted"}:
                timings[f"scene.{folder.name}.{stage}.termination_reason.{reason}"] = 1
    for stage in ("voz", "musica"):
        path = work / stage / "timing.json"
        if path.is_file():
            timings.update(_read(path))
    _atomic(work / "timings.json", timings)
    return timings


def build_delivery(work, catalog_path):
    work = Path(work)
    (work / "entrega.json").unlink(missing_ok=True)
    script = parse_video_script(_read(work / "guion.json"))
    video_path = work / "video.mp4"
    render_path = work / "render.json"
    qa_path = work / "qa_report.json"
    render = _read(render_path) if render_path.is_file() else {}
    qa = _read(qa_path) if qa_path.is_file() else {"pass": False, "failed": ["assembly"]}
    size, digest = None, None
    if video_path.is_file() and render_path.is_file() and qa_path.is_file():
        size, digest = _digest(video_path)
        if (
            qa["script_id"] != script_id(script)
            or qa["video"] != {"size_bytes": size, "sha256": digest}
            or render["size_bytes"] != size
        ):
            raise ValueError("delivery bytes or script mismatch")
    elif qa["pass"]:
        raise ValueError("passing QA requires video bytes")
    index_path = work / "index.json"
    indexed = _read(index_path) if index_path.exists() else {"clips": {}, "indexed": []}
    catalog = {row["id"]: row for row in Catalog.load(Path(catalog_path)).clips}
    rows = []
    for scene in script.scenes:
        folder = work / "scenes" / scene.id
        path = folder / "result.json"
        result = _read(path) if path.is_file() else {}
        if result.get("status") != "ok":
            rows.append(
                {
                    "id": scene.id,
                    "status": "fail",
                    "reason": result.get("reason", "missing_result"),
                    "tried": result.get("tried", []),
                }
            )
            continue
        clip = result["clip"]
        identifier = clip["clip_id"]
        imported = clip.get("imported", False)
        if imported:
            name = result.get("import_round", "import")
            if name not in ("import", "import-retry"):
                raise ValueError("invalid import round")
            manifest = _read(folder / name / "imported.json")
            record = next(row["record"] for row in manifest["clips"] if row["imp_id"] == identifier)
        else:
            record = catalog[identifier]
        rows.append(
            {
                "id": scene.id,
                "status": "ok",
                "fallback": result.get("fallback", False),
                "clip_id": indexed["clips"].get(identifier, identifier),
                "origin": "imported" if imported else "catalog",
                "source": record["source"]["provider"],
                "source_url": record["source"]["source_url"],
            }
        )
    if any(row["status"] == "fail" for row in rows):
        if qa["pass"]:
            raise ValueError("passing QA with missing scene")
        qa["failed"] = sorted(set(qa["failed"] + ["clips_all_scenes"]))
    result = {
        "version": 1,
        "script_id": script_id(script),
        "title": script.title,
        "aspect": script.aspect,
        "duration_s": render["duration_s"] if size is not None else None,
        "sha256": digest,
        "size_bytes": size,
        "qa": {"pass": qa["pass"], "failed": qa["failed"]},
        "scenes": rows,
        "clips_indexed": indexed["indexed"],
        "timings": _timings(work),
    }
    _validator("video-delivery.schema.json").validate(result)
    _atomic(work / "entrega.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        build_delivery(args.work, args.catalog)
    except Exception:
        parser.exit(2, "delivery: invalid or incomplete render evidence\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
