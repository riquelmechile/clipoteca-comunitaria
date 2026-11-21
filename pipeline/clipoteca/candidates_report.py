"""Convert sanitized M12 candidate probe output into a dated Markdown evidence table."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def render(report: dict) -> str:
    if report.get("mode") != "candidates":
        raise ValueError("candidates report required")
    rows = report.get("provider_languages")
    if not isinstance(rows, list):
        raise ValueError("provider_languages list required")
    output = [
        "# Evidencia de fuentes candidatas v2 · M12",
        "",
        "**Estado:** medición de endpoints candidatos. Un HTTP 200 no garantiza búsqueda ni descarga funcional.",
        "",
        f"**Fecha UTC de generación:** {datetime.now(UTC).strftime('%Y-%m-%d')}.",
        "",
        "| Proveedor | Idioma | Operación | HTTP | Código plataforma | ms | Resultados | Stream/archivo |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Values are only metrics; never accept arbitrary provider strings/URLs as output.
        provider = str(row.get("provider", ""))[:40].replace("|", "")
        language = str(row.get("language", ""))[:3].replace("|", "")
        operation = str(row.get("operation", ""))[:24].replace("|", "")
        status = row.get("status_code")
        code = row.get("platform_code")
        elapsed = row.get("elapsed_ms")
        count = row.get("count", 0)
        output.append(
            f"| {provider} | {language} | {operation} | "
            f"{status if isinstance(status, int) else '—'} | "
            f"{code if isinstance(code, int) else '—'} | "
            f"{elapsed if isinstance(elapsed, (int, float)) else '—'} | "
            f"{count if isinstance(count, int) else 0} | "
            f"{'sí' if row.get('has_stream') is True else 'no'} |"
        )
    output += [
        "",
        "## Ejecución y límites",
        "",
        "- No se guardaron tokens, cookies, payloads completos ni URLs firmadas.",
        "- Los estados 2483, 403 y 429 evidencian restricciones; no implican soporte.",
        "- Un resultado positivo debe verificarse con procesamiento real en el hito de la fuente.",
        "- **Runner propio frente a Actions:** pendiente de medición desde ambos entornos (M20).",
        "- **Pendiente:** publicar una ejecución del workflow en main usando el modo candidates.",
        "",
    ]
    return "\n".join(output)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="docs/EVIDENCIA_FUENTES_V2.md")
    args = parser.parse_args(argv)
    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render(report), encoding="utf-8")


if __name__ == "__main__":
    main()
