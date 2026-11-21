"""Pass video tools only noncredential environment variables."""

from __future__ import annotations

import os


def safe_media_env() -> dict[str, str]:
    banned_suffixes = ("_TOKEN", "_API_KEY", "_SECRET", "_PASSWORD", "_COOKIE", "_KEY")
    banned = {"GITHUB_TOKEN", "GITHUB_AUTH", "AUTHORIZATION"}
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in banned and not key.upper().endswith(banned_suffixes)
    }
