"""Local video delivery manifest, never a remote video publication."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..catalog import Catalog
from .index import _atomic, _digest, _read, _validator
from .script import parse_video_script, script_id


def build_delivery(work, catalog_path):
    work = Path(work)
    (work / "entrega.json").unlink(missing_ok=True)
    script = parse_video_script(_read(work / "guion.json"))
    render = _read(work / "render.json")
    qa = _read(work / "qa_report.json")
    size, digest = _digest(work / "video.mp4")
    if (
        qa["script_id"] != script_id(script)
        or qa["video"] != {"size_bytes": size, "sha256": digest}
        or render["size_bytes"] != size
    ):
        raise ValueError("delivery bytes or script mismatch")
    index_path = work / "index.json"
    indexed = _read(index_path) if index_path.exists() else {"clips": {}, "indexed": []}
    catalog = {row["id"]: row for row in Catalog.load(Path(catalog_path)).clips}
    rows = []
    for scene in script.scenes:
        folder = work / "scenes" / scene.id
        result = _read(folder / "result.json")
        if result["status"] != "ok":
            raise ValueError("missing selected scene")
        clip = result["clip"]
        identifier = clip["clip_id"]
        imported = clip.get("imported", False)
        if imported:
            manifest = _read(folder / "import" / "imported.json")
            record = next(row["record"] for row in manifest["clips"] if row["imp_id"] == identifier)
        else:
            record = catalog[identifier]
        rows.append(
            {
                "id": scene.id,
                "clip_id": indexed["clips"].get(identifier, identifier),
                "origin": "imported" if imported else "catalog",
                "source": record["source"]["provider"],
                "source_url": record["source"]["source_url"],
            }
        )
    result = {
        "version": 1,
        "script_id": script_id(script),
        "title": script.title,
        "aspect": script.aspect,
        "duration_s": render["duration_s"],
        "sha256": digest,
        "size_bytes": size,
        "qa": {"pass": qa["pass"], "failed": qa["failed"]},
        "scenes": rows,
        "clips_indexed": indexed["indexed"],
        "timings": _read(work / "timings.json"),
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
