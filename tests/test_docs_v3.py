"""Keep the documented v3 surface tied to checked-in contracts, without media or network."""

import ast
import importlib.util
import json
import re
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from clipoteca.studio.script import parse_video_script

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
STUDIO = ROOT / "pipeline" / "clipoteca" / "studio"
COMMAND = re.compile(r"python -m clipoteca\.studio\.(\w+)(?:\s+([\w-]+))?([^\n]*)")
OPTION = re.compile(r"--[a-z][a-z0-9-]*")


def _text(path):
    return path.read_text(encoding="utf-8")


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _parser_options(module):
    # AST avoids importing optional media libraries or running a real stage.
    path = STUDIO / f"{module}.py"
    assert importlib.util.find_spec(f"clipoteca.studio.{module}") is not None
    tree = ast.parse(_text(path))
    main = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"),
        None,
    )
    assert main is not None, module
    parsers = {}
    groups = {"": set()}
    subcommands = {}
    for node in main.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if not isinstance(call.func, ast.Attribute):
                continue
            if not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
            if call.func.attr == "ArgumentParser":
                parsers[name] = ""
            elif call.func.attr == "add_parser":
                command = _literal(call.args[0])
                assert command
                subcommands[command] = name
                parsers[name] = command
                groups[command] = set()
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "add_argument"
                and isinstance(call.func.value, ast.Name)
            ):
                group = parsers[call.func.value.id]
                groups[group].update(
                    value
                    for arg in call.args
                    if (value := _literal(arg)) and value.startswith("--")
                )
    return groups, subcommands


def test_documented_cli_matches_argparse():
    seen = set()
    for path in (ROOT / "README.md", DOCS / "STUDIO.md"):
        for match in COMMAND.finditer(_text(path)):
            module, command, tail = match.groups()
            groups, subcommands = _parser_options(module)
            if command and not command.startswith("--"):
                assert command in subcommands, (path, module, command)
                group = command
            else:
                group = ""
            # The regex may consume the first option as the optional command.
            quoted = tail.split(chr(96))[0]
            options = set(OPTION.findall(quoted))
            if command and command.startswith("--"):
                options.add(command)
            allowed = groups[""] | groups[group]
            assert options <= allowed, (path, module, options - allowed)
            seen.add(module)
    assert seen >= {
        "plan",
        "voice",
        "timeline",
        "music",
        "clipscene",
        "importer",
        "fit",
        "mix",
        "compose",
        "qa",
        "index",
        "selection",
        "delivery",
    }


def test_example_is_valid_video_script():
    example = json.loads(_text(DOCS / "ejemplos" / "guion-minimo.json"))
    script = parse_video_script(example)
    assert len(script.scenes) == 3
    assert any(re.search(r"\d", scene.say) for scene in script.scenes)


def _check_links(path, content):
    targets = re.findall(r"\]\(([^)\s]+)\)", content)
    targets.extend(re.findall(r'(?:href|src)="([^"]+)"', content))
    for target in targets:
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        assert (path.parent / unquote(parsed.path)).exists(), (path, target)


def test_relative_links_exist():
    for path in (ROOT / "README.md", DOCS / "README.md", DOCS / "STUDIO.md"):
        _check_links(path, _text(path))
    path = DOCS / "ESTADO.md"
    section = _text(path).split("## Motor de video (v3), en main sin publicar versión", 1)[1]
    _check_links(path, section.split("\n## ", 1)[0])


def test_studio_svgs_are_safe_standalone_xml():
    namespace = "{http://www.w3.org/2000/svg}"
    for name, limit in (("clipoteca-studio.svg", 60_000), ("clipoteca-studio-qa.svg", 40_000)):
        path = ROOT / "assets" / name
        assert path.stat().st_size < limit
        root = ElementTree.parse(path).getroot()
        assert root.tag == namespace + "svg"
        assert root.get("viewBox") and root.get("role") == "img"
        for tag in ("title", "desc"):
            child = root.find(namespace + tag)
            assert child is not None and child.text and child.text.strip()
        for element in root.iter():
            assert element.tag.rsplit("}", 1)[-1] not in {"script", "foreignObject"}
            for name, value in element.attrib.items():
                assert not name.lower().startswith("on")
                assert not re.search(r"https?://|//[^/]|data:", value)
            assert not re.search(r"https?://|@import", element.text or "")
        assert "<!DOCTYPE" not in _text(path)


def test_documented_repository_version_matches_package():
    version = tomllib.loads(_text(ROOT / "pyproject.toml"))["project"]["version"]
    readme = _text(ROOT / "README.md")
    status = _text(DOCS / "ESTADO.md")
    readme_version = re.search(r"versión del repositorio es \*\*([\d.]+)\*\*", readme)
    status_version = re.search(r"Versión del repositorio ([\d.]+)", status)
    assert readme_version and readme_version.group(1) == version
    assert status_version and status_version.group(1) == version


def test_video_api_is_documented_as_deployed():
    import yaml

    openapi = yaml.safe_load(_text(DOCS / "openapi.yaml"))
    assert {"/v1/videos", "/v1/videos/{id}", "/v1/videos/{id}/file"} <= set(openapi["paths"])
    mcp = _text(ROOT / "worker" / "src" / "mcp.ts")
    for name in ("render_video", "get_video_status"):
        assert f'registerTool("{name}"' in mcp
        assert name in _text(DOCS / "mcp-tools.md")
    for path in (ROOT / "README.md", DOCS / "STUDIO.md", DOCS / "ESTADO.md"):
        content = _text(path).lower()
        # 3.0.0 is released: the docs state the deployment and no longer defer it.
        assert "desplegad" in content
        assert "pendiente de promoción" not in content
        assert "pendiente de corrida real" not in content
    assert "nunca se publica" in _text(ROOT / "README.md").lower()


def test_three_release_demo_scripts():
    expected = {
        "chile-vertical.json": "9:16",
        "sistema-solar-horizontal.json": "16:9",
        "planta-cuadrado.json": "1:1",
    }
    for name, aspect in expected.items():
        script = parse_video_script(json.loads(_text(DOCS / "ejemplos" / name)))
        assert script.aspect == aspect
        assert 3 <= len(script.scenes) <= 12


def test_documented_model_downloads_are_real_models():
    manifest = json.loads(_text(STUDIO / "models.json"))["models"]
    guide = _text(DOCS / "STUDIO.md")
    names = re.findall(r"ensure_model\('([^']+)'\)", guide)
    assert names and set(names) <= set(manifest), names
    # clip-scene never downloads its model: the walkthrough must fetch it before the first scene.
    scorer = _text(STUDIO / "clipscene.py")
    if "ensure_model" not in scorer:
        walkthrough = guide.split("set -eu", 1)[1]
        fetch = walkthrough.index("ensure_model('clip-vit-b32')")
        assert fetch < walkthrough.index("clipoteca.studio.selection")


def test_documented_workflows_exist():
    for path in (
        ROOT / "README.md",
        DOCS / "STUDIO.md",
        DOCS / "ESTADO.md",
        DOCS / "ARQUITECTURA.md",
    ):
        for name in re.findall(r"`([a-z0-9-]+\.yml)`", _text(path)):
            assert (ROOT / ".github" / "workflows" / name).is_file(), (path, name)
