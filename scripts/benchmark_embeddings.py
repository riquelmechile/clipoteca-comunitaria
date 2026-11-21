#!/usr/bin/env python3
"""M21 actual CPU/GPU seconds per source-video minute. Never fabricate timings.

Usage:
  uv sync --frozen --extra embeddings --extra dev
  uv run --extra embeddings python scripts/benchmark_embeddings.py --video /tmp/safe.mp4
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from clipoteca.media.embeddings import (
    EmbeddingUnavailable,
    cosine_scores,
    extract_frame_embeddings,
    load_encoder,
)
from clipoteca.media.probe import probe_media
from clipoteca.media.scenes import MAX_WINDOW_SECONDS


def run_benchmark(video: Path, query: str, device: str) -> dict:
    """Run real weights, a real pre-downloaded local video, and an actual clock."""
    output = {"device": device, "status": "not_run"}
    try:
        encoder = load_encoder(device=device)
        duration = min(probe_media(video).duration_seconds, MAX_WINDOW_SECONDS)
        started = time.perf_counter()
        samples = extract_frame_embeddings(video, encoder)
        _ = cosine_scores(samples, encoder, query)
        elapsed = time.perf_counter() - started
        output.update(
            status="measured",
            frame_count=int(len(samples.times)),
            video_seconds=float(duration),
            elapsed_seconds=round(elapsed, 4),
            seconds_per_video_minute=round(elapsed / (duration / 60), 4),
        )
    except EmbeddingUnavailable:
        output["status"] = "model_or_device_unavailable"
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path, help="Local, previously validated MP4")
    parser.add_argument("--query", default="lluvia")
    parser.add_argument("--device", choices=("cpu", "cuda", "both"), default="both")
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error("--video must be an existing local video file")
    devices = ("cpu", "cuda") if args.device == "both" else (args.device,)
    rows = [run_benchmark(args.video, args.query, device) for device in devices]
    print(json.dumps({"benchmark": "M21", "results": rows}, indent=2, ensure_ascii=False))
    return 0 if any(row["status"] == "measured" for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
