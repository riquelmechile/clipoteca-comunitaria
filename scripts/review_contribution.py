"""Safe GitHub Issues triage. Issue text is data, never shell or executable source."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

LIMIT = 2048
HEADINGS = ("### URL pública del vídeo", "### URL pública del video")


def safe_url(value: str) -> bool:
    if len(value) > LIMIT or not value.startswith("https://"):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host or parsed.username or parsed.password or parsed.fragment:
        return False
    try:
        if parsed.port not in (None, 443):
            return False
    except ValueError:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if host.endswith((".localhost", ".local", ".internal", ".invalid", ".test", ".onion")):
            return False
        if host in {"localhost", "metadata.google.internal"}:
            return False
    else:
        return False
    return bool(re.fullmatch(r"[a-z0-9.-]+", host))


def issue_source_url(body: str) -> str | None:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if line.strip() not in HEADINGS:
            continue
        for next_line in lines[index + 1 : index + 5]:
            value = next_line.strip()
            if value and not value.startswith("###"):
                return value
    return None


def classify(body: str) -> str:
    url = issue_source_url(body)
    if url and safe_url(url):
        return "clipoteca-pending-review"
    return "clipoteca-invalid-url"


def main() -> int:
    event_path = Path(os.environ["GITHUB_EVENT_PATH"])
    if not event_path.is_file():
        raise ValueError("missing issue event")
    event = json.loads(event_path.read_text(encoding="utf-8"))
    issue = event.get("issue") or {}
    number = issue.get("number")
    if not isinstance(number, int) or number < 1:
        raise ValueError("invalid issue id")
    body = issue.get("body")
    label = classify(body if isinstance(body, str) else "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repository):
        raise ValueError("invalid repository")
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise ValueError("missing token")

    # Use validated repo/issue number, fixed GitHub host and constant label only.
    uri = f"https://api.github.com/repos/{repository}/issues/{number}/labels"
    payload = json.dumps({"labels": [label]}).encode()
    request = Request(
        uri,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "clipoteca-issues-triage",
        },
    )
    with urlopen(request, timeout=15) as response:
        if response.status not in (200, 201):
            raise RuntimeError("unable to mark Issue")
    print(f"Clipoteca contribution #{number}: {label}; no source URL downloaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
