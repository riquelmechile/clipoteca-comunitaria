import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from clipoteca import mcp_local


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    clips = [
        {
            "id": "clip-1",
            "title": {
                "es": "R\u00edo",
                "en": "River",
                "zh": "\u6cb3\u6d41\u98ce\u666f",
                "ru": "\u0440\u0435\u043a\u0430",
            },
            "description": {"en": "water"},
            "tags": {"es": ["agua"]},
            "source": {"provider": "pexels"},
            "media": {"aspect_ratio": "16:9", "duration_seconds": 5},
            "storage": {"asset_url": "https://github.com/example/repo/releases/download/v1/c.mp4"},
            "status": "published",
        },
        {
            "id": "clip-2",
            "title": {"en": "River"},
            "description": {},
            "tags": {},
            "source": {"provider": "commons"},
            "media": {"aspect_ratio": "9:16", "duration_seconds": 15},
            "status": "published",
        },
        {"id": "retired", "status": "retired"},
    ]
    path = tmp_path / "clips.json"
    path.write_text(json.dumps(clips), encoding="utf-8")
    monkeypatch.setenv("CLIPOTECA_CATALOG", str(path))
    return path, clips


def test_search_and_filters(catalog):
    _, clips = catalog
    for query in ("RIO", "\u6cb3\u6d41", "\u0440\u0435\u043a\u0430", "water", "agua"):
        assert mcp_local.search_clips(query)["clips"] == [clips[0]]
    assert mcp_local.search_clips("river", limit=1)["clips"] == [clips[0]]
    assert mcp_local.search_clips("river", aspect_ratio="9:16")["clips"] == [clips[1]]
    assert mcp_local.search_clips("river", max_duration=5)["clips"] == [clips[0]]
    assert mcp_local.search_clips("river", source="commons")["clips"] == [clips[1]]
    assert mcp_local.search_clips("missing") == {"clips": [], "next_cursor": None}


def test_get_and_sources(catalog):
    _, clips = catalog
    assert mcp_local.get_clip("clip-1") == {"clip": clips[0]}
    with pytest.raises(ValueError):
        mcp_local.get_clip("retired")
    assert mcp_local.list_sources()["sources"] == [
        {"provider": "commons", "count": 1},
        {"provider": "pexels", "count": 1},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,args",
    [
        (sys.executable, ["-m", "clipoteca.mcp_local"]),
        ("uv", ["run", "--offline", "--no-sync", "clipoteca-mcp"]),
    ],
)
async def test_stdio(catalog, command, args):
    path, clips = catalog
    parameters = StdioServerParameters(
        command=command,
        args=args,
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**os.environ, "CLIPOTECA_CATALOG": str(path)},
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            result = await session.initialize()
            assert "solo lectura" in result.instructions
            tools = (await session.list_tools()).tools
            assert {tool.name for tool in tools} == {"search_clips", "get_clip", "list_sources"}
            assert all(tool.annotations.readOnlyHint for tool in tools)
            found = await session.call_tool("search_clips", {"query": "river", "limit": 1})
            assert found.structuredContent["clips"] == [clips[0]]
            got = await session.call_tool("get_clip", {"clip_id": "clip-1"})
            assert got.structuredContent["clip"] == clips[0]
            sources = await session.call_tool("list_sources")
            assert len(sources.structuredContent["sources"]) == 2
            missing = await session.call_tool("get_clip", {"clip_id": "missing"})
            assert missing.isError
