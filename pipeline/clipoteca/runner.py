"""Declarative source partition for GitHub-hosted and self-hosted runners."""

from __future__ import annotations

import json
from pathlib import Path

from .sources import SOURCES

RUNNERS = frozenset({"actions", "self-hosted"})
DEFAULT_CONFIG = Path("data/runner-routing.json")


def selected_providers(
    runner: str,
    selected: set[str],
    *,
    config_path: Path = DEFAULT_CONFIG,
) -> set[str]:
    """Partition known sources without changing request filters or job IDs."""
    if runner not in RUNNERS:
        raise ValueError("invalid runner")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"self_hosted_providers"}:
        raise ValueError("invalid runner routing configuration")
    names = data["self_hosted_providers"]
    available = {source.name for source in SOURCES}
    if (
        not isinstance(names, list)
        or any(not isinstance(name, str) or name not in available for name in names)
        or len(names) != len(set(names))
    ):
        raise ValueError("invalid self-hosted provider selection")
    own = set(names) if runner == "self-hosted" else available - set(names)
    return (selected or available) & own
