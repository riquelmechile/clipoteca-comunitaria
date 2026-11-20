"""JSON metrics and GitHub Actions summary without credentials or URLs."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from .engine import SearchResult


def discovery_metrics(result: SearchResult) -> dict:
    by_lang = Counter(entry.language for entry in result.executions)
    by_provider = Counter(entry.provider for entry in result.executions)
    timeouts = sum(entry.status == "TIMEOUT" for entry in result.executions)
    return {
        "search_requests_total": 1,
        "search_requests_by_language": dict(by_lang),
        "provider_search_attempts": dict(by_provider),
        "provider_timeout_rate": timeouts / max(1, len(result.executions)),
        "new_candidates": len(result.candidates),
        "deduplicated_clips": result.duplicates,
        "mean_discovery_duration": result.elapsed_seconds,
        "downloaded_clips": 0,
        "published_clips": 0,
        "failed_imports": 0,
    }


def write_metrics(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as file:
            file.write("## Clipoteca search metrics\n\n")
            for key, value in payload.items():
                file.write(f"- {key}: {json.dumps(value, ensure_ascii=False)}\n")
