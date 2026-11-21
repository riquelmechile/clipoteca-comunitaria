"""JSON metrics and GitHub Actions summary without credentials or URLs."""

from __future__ import annotations

import json
import os
import re
import math
from collections import Counter
from pathlib import Path

from .engine import SearchResult


def discovery_metrics(result: SearchResult) -> dict:
    by_lang = Counter(entry.language for entry in result.executions)
    by_provider = Counter(entry.provider for entry in result.executions)
    timeouts = sum(entry.status == "TIMEOUT" for entry in result.executions)
    completed = sum(
        entry.status in {"SUPPORTED_SEARCH_DOWNLOAD", "SUPPORTED_SEARCH_ONLY"}
        for entry in result.executions
    )
    rate_limited = sum(entry.status == "RATE_LIMITED" for entry in result.executions)
    return {
        "search_requests_total": 1,
        "search_requests_by_language": dict(by_lang),
        "provider_search_attempts": dict(by_provider),
        "provider_timeout_rate": timeouts / max(1, len(result.executions)),
        "provider_success_rate": completed / max(1, len(result.executions)),
        "provider_rate_limit_rate": rate_limited / max(1, len(result.executions)),
        "new_candidates": len(result.candidates),
        "deduplicated_clips": result.duplicates,
        "mean_discovery_duration": result.elapsed_seconds,
        "downloaded_clips": 0,
        "published_clips": 0,
        "failed_imports": 0,
        "assets_storage_bytes": 0,
        "mean_publish_duration_seconds": 0.0,
        "runner_elapsed_minutes_estimate": 0.0,
    }


_ALLOWED_METRICS = {
    "search_requests_total", "search_requests_by_language",
    "provider_search_attempts", "provider_timeout_rate", "provider_success_rate",
    "provider_rate_limit_rate", "new_candidates", "deduplicated_clips",
    "mean_discovery_duration", "downloaded_clips", "published_clips",
    "failed_imports", "assets_storage_bytes", "mean_publish_duration_seconds",
    "runner_elapsed_minutes_estimate",
}


def _validated_metrics(payload: dict) -> dict:
    """Allowlist all metrics and labels before writing Actions-visible summaries."""
    if set(payload) - _ALLOWED_METRICS:
        raise ValueError("unexpected metric name")
    safe = {}
    for name, value in payload.items():
        if isinstance(value, dict):
            if any(not re.fullmatch(r"[a-z0-9_]{1,48}", str(key)) for key in value):
                raise ValueError("unsafe metric label")
            if any(
                isinstance(count, bool)
                or not isinstance(count, (int, float))
                or not math.isfinite(count)
                or count < 0
                for count in value.values()
            ):
                raise ValueError("invalid metric count")
        elif (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("invalid metric value")
        safe[name] = value
    return safe


def write_metrics(payload: dict, path: Path) -> None:
    payload = _validated_metrics(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as file:
            file.write("## Clipoteca search metrics\n\n")
            for key, value in payload.items():
                file.write(f"- {key}: {json.dumps(value, ensure_ascii=False)}\n")
