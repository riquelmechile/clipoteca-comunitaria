"""Untrusted public GitHub Issue -> guarded automatic community ingestion.

An Issue contains data, not commands. There is no manual approval gate.
Source validation and the regular downloader's DNS/SSRF, size and MIME gates
still apply. GitHub write credentials never reach FFmpeg subprocesses.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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

MAX_ISSUE_BODY = 20000
MEDIA_EXTENSIONS = {".mp4": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg"}
ISSUE_PREFIX = "[Aporte pendiente] "
OWNER_REPOSITORY = "riquelmechile/clipoteca-comunitaria"


@dataclass(frozen=True)
class CommunityIssue:
    number: int
    title: str
    source_url: str
    author: str
    issue_url: str


def parse_community_issue(
    payload: object, issue_number: int, repository: str = OWNER_REPOSITORY
) -> CommunityIssue:
    if not isinstance(payload, dict):
        raise ValueError("GitHub Issue must be an object")
    if (
        payload.get("number") != issue_number
        or payload.get("state") != "open"
        or "pull_request" in payload
    ):
        raise ValueError("wrong, closed or unavailable GitHub Issue")
    raw_title, body = payload.get("title"), payload.get("body")
    if (
        not isinstance(raw_title, str)
        or not raw_title.startswith(ISSUE_PREFIX)
        or not isinstance(body, str)
    ):
        raise ValueError("not an Aportar escena Issue")
    title = raw_title.removeprefix(ISSUE_PREFIX).strip()
    if len(title) < 3 or len(title) > 120 or re.search(r"[\x00-\x1f]", title):
        raise ValueError("invalid Issue title")
    if len(body) > MAX_ISSUE_BODY:
        raise ValueError("Issue body too long")
    match = re.search(r"(?m)^### URL pública del vídeo\s*\n\s*(https://[^\s]+)\s*$", body)
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
        or host.endswith((".localhost", ".local", ".internal", ".invalid", ".test", ".onion"))
        or not re.fullmatch(r"[a-z0-9.-]+", host)
        or host.replace(".", "").isdigit()
    ):
        raise ValueError("nonpublic media URL")
    if Path(address.path).suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError("contribution must link a direct MP4, WebM or OGV")
    user = payload.get("user")
    author = user.get("login", "") if isinstance(user, dict) else ""
    if not isinstance(author, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", author):
        raise ValueError("invalid Issue author")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("invalid repository")
    return CommunityIssue(
        issue_number, title, url, author, f"https://github.com/{repository}/issues/{issue_number}"
    )


def load_community_issue(
    repository: str, token: str, issue_number: int, *, client: httpx.Client | None = None
) -> CommunityIssue:
    if repository != OWNER_REPOSITORY or issue_number <= 0 or not token:
        raise ValueError("owner repository, positive Issue ID and GitHub token required")
    own_client = client is None
    connection = client or httpx.Client(timeout=20, follow_redirects=False)
    try:
        response = connection.get(
            f"https://api.github.com/repos/{repository}/issues/{issue_number}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "clipoteca-community",
            },
        )
        response.raise_for_status()
        return parse_community_issue(response.json(), issue_number, repository)
    finally:
        if own_client:
            connection.close()


class CommunityContribution(Adapter):
    name = "community"
    category = "community_video"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    accepts_text_languages = ("es",)

    def __init__(self, issue: CommunityIssue):
        self.issue = issue

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        if lang != "es" or limits.max_candidates < 1:
            return []
        issue = self.issue
        digest = hashlib.sha256(issue.source_url.encode("utf-8")).hexdigest()[:12]
        return [
            Candidate(
                provider=self.name,
                provider_id=f"issue-{issue.number}-{digest}",
                term=term,
                language=lang,
                source_url=issue.source_url,
                source_title=issue.title,
                source_language=detect_source_language(issue.title),
                metadata={
                    "issue_number": issue.number,
                    "issue_author": issue.author,
                    "issue_url": issue.issue_url,
                },
            )
        ]

    async def metadata(self, candidate: Candidate) -> dict:
        return candidate.metadata or {}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        suffix = Path(urlsplit(self.issue.source_url).path).suffix.lower()
        mime = MEDIA_EXTENSIONS.get(suffix)
        return MediaRef(self.issue.source_url, mime) if mime else None


async def run_community_issue(issue: CommunityIssue, repository: str, token: str):
    return await ingest(
        issue.title,
        [CommunityContribution(issue)],
        repository=repository,
        token=token,
        job_root=Path("data/jobs"),
        max_results=1,
        filters={"max_duration": 20},
        horizon=f"community-issue-{issue.number}",
    )


def send_feedback(
    repository: str, token: str, number: int, status: str, *, client: httpx.Client | None = None
) -> None:
    """Fixed GitHub paths and bounded, sanitized status text; no untrusted body or URLs."""
    if repository != OWNER_REPOSITORY or number < 1 or not token:
        raise ValueError("invalid feedback destination")
    base = f"https://api.github.com/repos/{repository}/issues/{number}"
    own_client = client is None
    connection = client or httpx.Client(timeout=20, follow_redirects=False)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "clipoteca-community",
    }
    success = status in {"published", "duplicate"}
    try:
        response = connection.post(
            base + "/comments",
            headers=headers,
            json={
                "body": (
                    "Clipoteca: aporte catalogado correctamente (" + status + ")."
                    if success
                    else "Clipoteca: el procesamiento no se completó (" + status + "). "
                    "Corrige el enlace o vuelve a editar el Issue para reintentar."
                )
            },
        )
        response.raise_for_status()
        response = connection.post(
            base + "/labels",
            headers=headers,
            json={"labels": ["clipoteca-ingested" if success else "clipoteca-ingest-failed"]},
        )
        response.raise_for_status()
        if success:
            response = connection.patch(base, headers=headers, json={"state": "closed"})
            response.raise_for_status()
    finally:
        if own_client:
            connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Automatic GitHub Issue -> secured Clipoteca ingest"
    )
    parser.add_argument("--issue-number", type=int, required=True)
    args = parser.parse_args(argv)
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    status = "failed"
    try:
        issue = load_community_issue(repository, token, args.issue_number)
        report = asyncio.run(run_community_issue(issue, repository, token))
        status = report.status
    except Exception as exc:
        # Do not echo untrusted Issue input, upstream messages or credentials.
        status = "failed_" + type(exc).__name__
    try:
        send_feedback(repository, token, args.issue_number, status)
    except Exception as exc:
        print("issue_feedback_failed:" + type(exc).__name__)
        return 1
    print(json.dumps({"issue_number": args.issue_number, "status": status}, ensure_ascii=False))
    return 0 if status in {"published", "duplicate"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
