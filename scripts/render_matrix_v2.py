"""Render M24 provider x language matrix from declarations and optional real smoke report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

LANGUAGES = ("es", "en", "zh", "ru")
PENDING = "pendiente del smoke en main"


def cell(value: object) -> str:
    return str(value).replace("|", "/").replace("\n", " ").replace("\r", " ")[:120]


def render(sources: list[dict], report: dict | None = None) -> str:
    live = {}
    timestamp = None
    provenance = ""
    names = {source["name"] for source in sources}
    if len(names) != len(sources):
        raise ValueError("duplicate source declaration")
    if report is not None:
        if report.get("mode") not in ("live_network_probes", "live_network_and_candidate_probes"):
            raise ValueError("only a documented live smoke report is accepted")
        timestamp = report.get("timestamp_utc")
        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError("smoke report timestamp missing")
        for row in report.get("provider_languages", []):
            if not isinstance(row, dict):
                raise ValueError("invalid smoke row")
            name, language = row.get("provider"), row.get("language")
            if not isinstance(name, str) or language not in LANGUAGES:
                raise ValueError("invalid smoke identity")
            if name not in names:
                raise ValueError("unknown provider in smoke report: " + name)
            if (
                not isinstance(row.get("status"), str)
                or isinstance(row.get("count"), bool)
                or not isinstance(row.get("count"), int)
                or row["count"] < 0
            ):
                raise ValueError("invalid smoke outcome")
            key = (name, language)
            if key in live:
                raise ValueError("duplicate smoke row")
            live[key] = row
        github = report.get("github")
        if isinstance(github, dict):
            verified = []
            for label, field in (("ref", "ref"), ("SHA", "sha"), ("run_id", "run_id")):
                value = github.get(field)
                if isinstance(value, str) and value:
                    verified.append(label + "=" + cell(value))
            if verified:
                provenance = " **GitHub:** " + ", ".join(verified) + "."
    lines = [
        "# M24 - matriz de proveedores e idiomas",
        "",
        "**Evidencia live:** "
        + (f"reporte de smoke de main {timestamp}" if timestamp else PENDING)
        + "."
        + provenance,
        "Los estados declarativos no acreditan descarga ni publicacion de videos.",
        "Un reporte de busqueda live tampoco acredita MP4 publicado.",
        "",
        "| Proveedor | Estado | Search | Preview | Download | es live | en live | zh live | ru live |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in sorted(sources, key=lambda r: r["name"]):
        capabilities = row.get("capabilities", {})
        accepts = set(row.get("accepts_text_languages", []))
        observed = []
        for lang in LANGUAGES:
            if lang not in accepts:
                observed.append("no declarada")
                continue
            result = live.get((row["name"], lang))
            if result is None:
                observed.append(PENDING)
            else:
                status = cell(result["status"])
                if result["count"] == 0 and status in {
                    "SUPPORTED_SEARCH_DOWNLOAD",
                    "SUPPORTED_SEARCH_ONLY",
                    "OK",
                    "SUCCESS",
                }:
                    observed.append("sin resultados (n=0)")
                else:
                    observed.append(status + f" (n={result['count']})")
        columns = [
            row["name"],
            row.get("status", ""),
            capabilities.get("search", ""),
            capabilities.get("preview", ""),
            capabilities.get("download", ""),
            *observed,
        ]
        lines.append("| " + " | ".join(cell(x) for x in columns) + " |")
    bt = chr(96) * 3
    lines += [
        "",
        "## Regeneracion",
        "",
        bt + "sh",
        "uv run python -m clipoteca export-sources --output data/sources.json",
        "uv run python scripts/render_matrix_v2.py --output docs/MATRIZ_FUENTES_V2.md",
        "# Solo tras ejecutar smoke real en main:",
        "uv run python scripts/smoke_sources.py --mode providers --output data/reports/smoke.json",
        "uv run python scripts/render_matrix_v2.py --smoke-report data/reports/smoke.json --output docs/MATRIZ_FUENTES_V2.md",
        bt,
        "",
        "Nunca escribir credenciales, URLs firmadas ni resultados supuestos.",
        "Sin --smoke-report se preserva el estado de evidencia pendiente.",
        "La evidencia live anota ref, SHA y run_id si el smoke los entrega.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="data/sources.json")
    parser.add_argument("--smoke-report")
    parser.add_argument("--output", default="docs/MATRIZ_FUENTES_V2.md")
    args = parser.parse_args(argv)
    sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))
    if not isinstance(sources, list) or any(not isinstance(x, dict) for x in sources):
        raise ValueError("invalid sources snapshot")
    report = (
        json.loads(Path(args.smoke_report).read_text(encoding="utf-8"))
        if args.smoke_report
        else None
    )
    output = render(sources, report)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(output, encoding="ascii")
    print(
        json.dumps(
            {
                "providers": len(sources),
                "live_rows": len(report["provider_languages"]) if report else 0,
                "output": str(path),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
