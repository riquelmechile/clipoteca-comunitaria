"""Durable JSON job status with deterministic request keys and schema validation."""

from __future__ import annotations

import base64
import hashlib
import re
import json
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator, FormatChecker

from .catalog import normalize_text

SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "job.schema.json"
STATUSES = ("queued", "searching", "fetching", "processing", "publishing", "published")
TERMINAL = ("no_results", "duplicate", "failed", "rate_limited", "unavailable", "dead_letter")
RETRYABLE = ("failed", "rate_limited", "unavailable")
DEAD_LETTER_TTL = timedelta(hours=24)


def expired_dead_letter(job: dict[str, Any], *, now: datetime | None = None) -> bool:
    if job.get("status") != "dead_letter" or not isinstance(job.get("updated_at"), str):
        return False
    try:
        timestamp = datetime.fromisoformat(job["updated_at"].replace("Z", "+00:00"))
        return (now or datetime.now(UTC)) - timestamp >= DEAD_LETTER_TTL
    except (TypeError, ValueError):
        return False


MAX_ATTEMPTS = 3


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


class GitHubJobWriter:
    """Persist every job transition via Contents API with optimistic SHA checks.

    Publication serializes this alongside catalogue mutations using the workflow
    concurrency group; concurrent stale attempts must not silently clobber jobs.
    """

    def __init__(self, repository: str, token: str, *, client: httpx.Client | None = None) -> None:
        self.repository = repository
        self.client = client or httpx.Client(
            headers={"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"},
            timeout=30,
        )

    def url(self, identifier: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise ValueError("invalid request_id")
        return (
            f"https://api.github.com/repos/{self.repository}/contents/data/jobs/{identifier}.json"
        )

    def read(self, identifier: str) -> tuple[dict[str, Any] | None, str | None]:
        response = self.client.get(self.url(identifier))
        if response.status_code == 404:
            return None, None
        response.raise_for_status()
        payload = response.json()
        if payload.get("encoding") != "base64" or not payload.get("content"):
            raise ValueError("remote job must be base64 JSON")
        raw = base64.b64decode(re.sub(r"\s+", "", payload["content"]), validate=True)
        return json.loads(raw), str(payload["sha"])

    def save(self, job: dict[str, Any], *, retries: int = 3) -> None:
        identifier = job["request_id"]
        for attempt in range(retries + 1):
            existing, sha = self.read(identifier)
            if existing and existing != job:
                if existing["status"] in (*TERMINAL, "published"):
                    retry = (
                        existing["status"] in RETRYABLE
                        and job["status"] == "queued"
                        and job.get("attempt", 1) == existing.get("attempt", 1) + 1
                        and job.get("attempt", 1) <= MAX_ATTEMPTS
                    )
                    dead_letter = (
                        existing["status"] in RETRYABLE
                        and job["status"] == "dead_letter"
                        and existing.get("attempt", 1) >= MAX_ATTEMPTS
                        and job.get("attempt", 1) == existing.get("attempt", 1)
                    )
                    reset_expired = (
                        existing["status"] == "dead_letter"
                        and job["status"] == "queued"
                        and job.get("attempt") == 1
                        and expired_dead_letter(existing)
                    )
                    if not (retry or dead_letter or reset_expired):
                        raise ValueError("remote terminal job cannot transition")
            payload = {
                "message": f"chore(jobs): record {job['status']} ({identifier[:12]})",
                "content": base64.b64encode(
                    (json.dumps(job, ensure_ascii=False, indent=2) + "\n").encode()
                ).decode(),
            }
            if sha:
                payload["sha"] = sha
            response = self.client.put(self.url(identifier), json=payload)
            if response.status_code in (409, 422) and attempt < retries:
                time.sleep(min(0.2 * (2**attempt), 2.0))
                continue
            response.raise_for_status()
            return
        raise RuntimeError("remote job update conflict after retries")


class JobStore:
    def __init__(
        self,
        root: Path,
        schema_path: Path = SCHEMA,
        remote: GitHubJobWriter | None = None,
    ) -> None:
        if not schema_path.is_file():
            raise FileNotFoundError(schema_path)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self.root = root
        self.remote = remote

    def load(self, identifier: str) -> dict[str, Any] | None:
        if not (len(identifier) == 64 and all(x in "0123456789abcdef" for x in identifier)):
            raise ValueError("invalid request_id")
        path = self.root / f"{identifier}.json"
        if self.remote is not None:
            obj, _ = self.remote.read(identifier)
            if obj is not None:
                self.validator.validate(obj)
                return obj
        if not path.exists():
            return None
        obj = json.loads(path.read_text(encoding="utf-8"))
        self.validator.validate(obj)
        return obj

    def save(self, job: dict[str, Any]) -> Path:
        job["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
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
                    retry = (
                        prev in RETRYABLE
                        and next_state == "queued"
                        and job.get("attempt", 1) == old.get("attempt", 1) + 1
                        and job.get("attempt", 1) <= MAX_ATTEMPTS
                    )
                    dead_letter = (
                        prev in RETRYABLE
                        and next_state == "dead_letter"
                        and old.get("attempt", 1) >= MAX_ATTEMPTS
                        and job.get("attempt", 1) == old.get("attempt", 1)
                    )
                    reset_expired = (
                        prev == "dead_letter"
                        and next_state == "queued"
                        and job.get("attempt") == 1
                        and expired_dead_letter(old)
                    )
                    if not (retry or dead_letter or reset_expired):
                        raise ValueError("terminal job cannot transition")
                if (
                    prev in STATUSES
                    and next_state not in TERMINAL
                    and STATUSES.index(next_state) < STATUSES.index(prev)
                ):
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
        if self.remote is not None:
            self.remote.save(job)
        return target


def new_job(identifier: str) -> dict[str, Any]:
    return {
        "request_id": identifier,
        "status": "queued",
        "attempt": 1,
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "language_counts": {},
        "provider_counts": {},
        "source_errors": {},
        "terms": {},
    }
