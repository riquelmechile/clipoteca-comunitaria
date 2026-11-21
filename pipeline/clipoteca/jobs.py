"""Durable JSON job status with deterministic request keys and schema validation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .catalog import normalize_text

SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "job.schema.json"
STATUSES = ("queued", "searching", "fetching", "processing", "publishing", "published")
TERMINAL = ("no_results", "duplicate", "failed", "rate_limited", "unavailable")


def request_id(query: str, filters: dict[str, Any], horizon: str) -> str:
    normalized_filters = dict(filters)
    if normalized_filters.get("aspect_ratio") == "":
        normalized_filters["aspect_ratio"] = None
    if "max_duration" in normalized_filters and normalized_filters["max_duration"] not in (
        None,
        "",
    ):
        duration = float(normalized_filters["max_duration"])
        normalized_filters["max_duration"] = int(duration) if duration.is_integer() else duration
    canonical = json.dumps(
        {
            "query": " ".join(normalize_text(query).split()),
            "filters": normalized_filters,
            "horizon": horizon,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class JobStore:
    def __init__(self, root: Path, schema_path: Path = SCHEMA) -> None:
        if not schema_path.is_file():
            raise FileNotFoundError(schema_path)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self.root = root

    def load(self, identifier: str) -> dict[str, Any] | None:
        if not (len(identifier) == 64 and all(x in "0123456789abcdef" for x in identifier)):
            raise ValueError("invalid request_id")
        path = self.root / f"{identifier}.json"
        if not path.exists():
            return None
        obj = json.loads(path.read_text(encoding="utf-8"))
        self.validator.validate(obj)
        return obj

    def save(self, job: dict[str, Any]) -> Path:
        self.validator.validate(job)
        identifier = job["request_id"]
        if not (len(identifier) == 64 and all(x in "0123456789abcdef" for x in identifier)):
            raise ValueError("invalid request_id")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{identifier}.json"
        old = self.load(identifier)
        if old:
            prev, next_state = old["status"], job["status"]
            if prev != next_state:
                if prev in (*TERMINAL, "published"):
                    raise ValueError("terminal job cannot transition")
                if next_state not in TERMINAL and STATUSES.index(next_state) < STATUSES.index(prev):
                    raise ValueError("job state cannot regress")
        fd, temporary = tempfile.mkstemp(dir=self.root, prefix=".job-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(job, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return target


def new_job(identifier: str) -> dict[str, Any]:
    return {
        "request_id": identifier,
        "status": "queued",
        "language_counts": {},
        "provider_counts": {},
        "source_errors": {},
        "terms": {},
    }
