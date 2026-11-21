"""Publish provider capability snapshot with credential presence, never secret values."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
from pathlib import Path

import httpx

from .sources import list_sources

REPOSITORY = "riquelmechile/clipoteca-comunitaria"


def snapshot() -> list[dict]:
    """Keep public capabilities, status and credential presence booleans only."""
    rows = list_sources(env=os.environ)
    public = []
    for row in rows:
        public.append(
            {
                "name": row["name"],
                "category": row["category"],
                "status": row["status"],
                "support_status": row["support_status"],
                "accepts_text_languages": row["accepts_text_languages"],
                "languages_semantically_verified": False,
                "scope": row["scope"],
                "download_supported": row["download_supported"],
                "credentials_present": not bool(row["missing_credential_names"]),
                "notes": row["notes"],
            }
        )
    return public


def publish_snapshot(repository: str, token: str, *, client: httpx.Client | None = None) -> str:
    if repository != REPOSITORY or not token:
        raise ValueError("authorized GitHub repository and token required")
    data = json.dumps(snapshot(), ensure_ascii=False, indent=2) + "\n"
    encoded = base64.b64encode(data.encode("utf-8")).decode("ascii")
    url = f"https://api.github.com/repos/{repository}/contents/data/sources.json"
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "clipoteca-source-snapshot",
    }
    own_client = client is None
    connection = client or httpx.Client(timeout=30, follow_redirects=False)
    try:
        for attempt in range(4):
            current = connection.get(url + "?ref=main", headers=headers)
            sha = None
            if current.status_code == 200:
                payload = current.json()
                sha = payload.get("sha")
                if payload.get("encoding") == "base64":
                    old = base64.b64decode(re.sub(r"\s+", "", payload["content"]))
                    if old == data.encode("utf-8"):
                        return "unchanged"
            elif current.status_code != 404:
                current.raise_for_status()
            body = {
                "message": "chore(sources): update Actions credential-presence snapshot",
                "content": encoded,
                "branch": "main",
                **({"sha": sha} if sha else {}),
            }
            response = connection.put(url, json=body, headers=headers)
            if response.status_code in (409, 422) and attempt < 3:
                time.sleep(0.2 * (attempt + 1))
                continue
            response.raise_for_status()
            return "published"
        raise RuntimeError("source snapshot update conflict")
    finally:
        if own_client:
            connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish booleans-only public source snapshot")
    parser.add_argument("--output", default="data/sources.json")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    payload = json.dumps(snapshot(), ensure_ascii=False, indent=2) + "\n"
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    if args.publish:
        result = publish_snapshot(
            os.environ.get("GITHUB_REPOSITORY", ""),
            os.environ.get("GITHUB_TOKEN", ""),
        )
        print("sources_snapshot:" + result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
