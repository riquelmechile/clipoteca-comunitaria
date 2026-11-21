"""Human-approved community uploads through the normal secured ingest pipeline.

An Issue is *not* approval. A maintainer must add the "clipoteca-approved"
label and explicitly run the protected workflow; the issue URL is never fetched
during triage or loading. The existing download_media enforces DNS/SSRF checks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .ingest import ingest
from .providers.base import (
    Adapter,
    Candidate,
    MediaRef,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)

APPROVED_LABEL = "clipoteca-approved"
MAX_ISSUE_BODY = 20000
MEDIA_EXTENSIONS = {".mp4": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg"}


@dataclass(frozen=True)
class ApprovedIssue:
    number: int
    title: str
    source_url: str


def parse_approved_issue(payload: object, issue_number: int) -> ApprovedIssue:
    if not isinstance(payload, dict):
        raise ValueError("GitHub Issue must be an object")
    if payload.get("number") != issue_number or payload.get("state") != "open":
        raise ValueError("wrong, closed or unavailable GitHub Issue")
    labels = payload.get("labels") or []
    if not isinstance(labels, list) or not any(
        isinstance(item, dict) and item.get("name") == APPROVED_LABEL for item in labels
    ):
        raise ValueError("reviewer has not applied clipoteca-approved label")
    title = payload.get("title")
    body = payload.get("body")
    if not isinstance(title, str) or not isinstance(body, str):
        raise ValueError("Issue title/body missing")
    title = title.removeprefix("[Aporte pendiente] ").strip()
    if len(title) < 3 or len(title) > 120 or re.search(r"[\x00-\x1f]", title):
        raise ValueError("invalid Issue title")
    if len(body) > MAX_ISSUE_BODY or "### Derechos de uso" not in body:
        raise ValueError("license attestation missing")
    if not (
        "Declaración de autorización del remitente" in body
        or re.search(r"- \[[xX]\] Confirmo que cuento con autorización", body)
    ):
        raise ValueError("contributor did not attest license rights")
    match = re.search(
        r"(?m)^### URL pública del vídeo\s*\n\s*(https://[^\s]+)\s*$", body
    )
    if not match:
        raise ValueError("public video URL missing")
    url = match.group(1)
    if len(url) > 2048 or re.search(r"[\x00-\x20]", url):
        raise ValueError("unsafe URL")
    try:
        address = urlsplit(url)
        port = address.port
    except ValueError as exc:
        raise ValueError("invalid video URL") from exc
    host = (address.hostname or "").lower()
    if (
        address.scheme != "https"
        or not host
        or address.username is not None
        or address.password is not None
        or port not in (None, 443)
        or address.query
        or address.fragment
        or host in {"localhost", "metadata.google.internal"}
        or host.endswith((".localhost", ".local", ".internal", ".test", ".onion"))
        or not re.fullmatch(r"[a-z0-9.-]+", host)
        or host.replace(".", "").isdigit()
    ):
        raise ValueError("nonpublic media URL")
    suffix = Path(address.path).suffix.lower()
    if suffix not in MEDIA_EXTENSIONS:
        raise ValueError("approved contribution must be direct MP4, WebM or OGV")
    return ApprovedIssue(issue_number, title, url)


def load_approved_issue(
    repository: str,
    token: str,
    issue_number: int,
    *,
    client: httpx.Client | None = None,
) -> ApprovedIssue:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("invalid repository")
    if issue_number <= 0 or not token:
        raise ValueError("positive Issue ID and GitHub token required")
    owned = client is None
    connection = client or httpx.Client(timeout=20, follow_redirects=False)
    try:
        response = connection.get(
            f"https://api.github.com/repos/{repository}/issues/{issue_number}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "clipoteca-community-approved",
            },
        )
        response.raise_for_status()
        return parse_approved_issue(response.json(), issue_number)
    finally:
        if owned:
            connection.close()


class ApprovedContribution(Adapter):
    name = "community_contribution"
    category = "reviewed_video"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    accepts_text_languages = ("es",)

    def __init__(self, approved: ApprovedIssue):
        self.approved = approved

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        if lang != "es" or limits.max_candidates < 1:
            return []
        issue = self.approved
        return [
            Candidate(
                provider=self.name,
                provider_id=f"issue-{issue.number}",
                term=term,
                language=lang,
                source_url=issue.source_url,
                source_title=issue.title,
                source_language=detect_source_language(issue.title),
            )
        ]

    async def metadata(self, candidate: Candidate) -> dict:
        return {"reviewed_issue_number": self.approved.number}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        suffix = Path(urlsplit(self.approved.source_url).path).suffix.lower()
        mime = MEDIA_EXTENSIONS.get(suffix)
        return MediaRef(self.approved.source_url, mime) if mime else None


async def run_approved_issue(issue: ApprovedIssue, repository: str, token: str):
    # Isolate idempotency from regular text queries, but use exactly the same
    # download, FFmpeg, dedup, release and job persistence path as providers.
    return await ingest(
        issue.title,
        [ApprovedContribution(issue)],
        repository=repository,
        token=token,
        job_root=Path("data/jobs"),
        max_results=1,
        filters={"max_duration": 20},
        horizon=f"approved-issue-{issue.number}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Operator-reviewed GitHub Issue -> secured Clipoteca ingest"
    )
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--rights-confirmed", choices=["yes"], required=True)
    arguments = parser.parse_args(argv)
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    issue = load_approved_issue(repository, token, arguments.issue_number)
    report = asyncio.run(run_approved_issue(issue, repository, token))
    print(
        json.dumps(
            {
                "issue_number": issue.number,
                "request_id": report.request_id,
                "status": report.status,
                "published_ids": list(report.published_ids),
                "error_types": [entry.rsplit(":", 1)[-1] for entry in report.errors],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.status in {"published", "duplicate"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
