"""M24: release metadata is synchronized before any 2.0.0 tag/deploy."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

from clipoteca import __version__
from clipoteca.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_every_release_surface_has_one_version(capsys):
    version = "2.0.3"
    assert __version__ == version
    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"] == version
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert next(p["version"] for p in lock["package"] if p["name"] == "clipoteca") == version
    assert json.loads((ROOT / "worker/package.json").read_text())["version"] == version
    npm = json.loads((ROOT / "worker/package-lock.json").read_text())
    assert npm["version"] == version
    assert npm["packages"][""]["version"] == version
    worker = (ROOT / "worker/src/version.ts").read_text()
    assert 'APP_VERSION = "' + version + '"' in worker
    for source in ("worker/src/index.ts", "worker/src/mcp.ts"):
        text = (ROOT / source).read_text()
        assert "APP_VERSION" in text
        assert '"1.0.0"' not in text
    assert yaml.safe_load((ROOT / "docs/openapi.yaml").read_text())["info"]["version"] == version
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "clipoteca " + version
