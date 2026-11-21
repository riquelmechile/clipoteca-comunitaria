"""Bounded non-forced main push after rebase, for Actions catalogue updates.

A concurrent Contents API update to data/sources.json is preserved by
rebasing the local relocalization commit before attempting to push.
"""

from __future__ import annotations

import subprocess
import time


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments], capture_output=True, text=True, timeout=45, check=False
    )


def push_catalog_with_retry(max_attempts: int = 4) -> int:
    if not 1 <= max_attempts <= 6:
        raise ValueError("invalid retry limit")
    for attempt in range(1, max_attempts + 1):
        # Fail closed on genuine rebase conflicts, retry only push races.
        if _git("pull", "--rebase", "origin", "main").returncode != 0:
            raise RuntimeError("catalog_rebase_conflict")
        if _git("push", "origin", "HEAD:main").returncode == 0:
            return attempt
        if attempt < max_attempts:
            time.sleep(0.3 * attempt)
    raise RuntimeError("catalog_push_retry_exhausted")


if __name__ == "__main__":
    print("catalog_push_attempts:" + str(push_catalog_with_retry()))
