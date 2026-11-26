"""Servidor MCP local de solo lectura; nunca consulta servicios externos."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

PRODUCTION = "https://clipoteca-worker.clipoteca.workers.dev/mcp"
INSTRUCTIONS = (
    "Clipoteca local de solo lectura. Consulta el catalogo incluido sin red ni credenciales. "
    "Para videos, aportes y render usa el MCP completo: " + PRODUCTION
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
mcp = FastMCP("Clipoteca Comunitaria (local)", instructions=INSTRUCTIONS)


def catalog_path() -> Path:
    if override := os.environ.get("CLIPOTECA_CATALOG"):
        return Path(override).expanduser()
    repo = Path(__file__).resolve().parents[2] / "data" / "clips.json"
    if repo.is_file():
        return repo
    return Path(__file__).resolve().parent / "_data" / "clips.json"


@lru_cache(maxsize=1)
def _load(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("El catalogo debe ser una lista de clips")
    return [clip for clip in data if clip.get("status") == "published"]


def _clips() -> list[dict[str, Any]]:
    return _load(catalog_path().resolve())


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        c for c in unicodedata.normalize("NFKD", value) if unicodedata.category(c) != "Mn"
    )


def _tokens(value: str) -> list[str]:
    return re.findall(r"[^\W_]+", _normalize(value))


def _text(clip: dict[str, Any]) -> str:
    parts = []
    for field in ("title", "description", "tags"):
        for value in clip.get(field, {}).values():
            parts.extend(value if isinstance(value, list) else [value])
    return _normalize(" ".join(parts))


def _matches(query: list[str], text: str) -> bool:
    words = set(_tokens(text))
    # Chinese has no required word separators; match Han terms as substrings.
    return all(
        token in words or (any("\u3400" <= c <= "\u9fff" for c in token) and token in text)
        for token in query
    )


@mcp.tool(annotations=READ_ONLY)
def search_clips(
    query: Annotated[str, Field(min_length=1)],
    aspect_ratio: Literal["9:16", "16:9", "1:1", "other"] | None = None,
    max_duration: Annotated[float, Field(gt=0)] | None = None,
    source: str | None = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Busca clips locales por palabras en es/en/zh/ru y filtros. Solo lectura."""
    terms = _tokens(query)
    results = []
    if terms:
        for clip in _clips():
            media = clip["media"]
            if aspect_ratio is not None and media["aspect_ratio"] != aspect_ratio:
                continue
            if max_duration is not None and media["duration_seconds"] > max_duration:
                continue
            if source is not None and clip["source"]["provider"] != source:
                continue
            if _matches(terms, _text(clip)):
                results.append(clip)
                if len(results) == limit:
                    break
    return {"clips": results, "next_cursor": None}


@mcp.tool(annotations=READ_ONLY)
def get_clip(clip_id: str) -> dict[str, Any]:
    """Consulta un clip publicado por ID, incluida su URL publica MP4. Solo lectura."""
    for clip in _clips():
        if clip["id"] == clip_id:
            return {"clip": clip}
    raise ValueError("Clip publicado no encontrado")


@mcp.tool(annotations=READ_ONLY)
def list_sources() -> dict[str, Any]:
    """Lista las fuentes del catalogo local y su conteo, no su disponibilidad en red."""
    counts = Counter(clip["source"]["provider"] for clip in _clips())
    return {
        "sources": [
            {"provider": provider, "count": count} for provider, count in sorted(counts.items())
        ],
        "kind": "local_catalog",
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
