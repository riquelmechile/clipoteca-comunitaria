# Evidencia de fuentes candidatas v2 · M12

**Estado:** Pendiente: se completa tras ejecutar el workflow smoke en main.

El modo `candidates` mide RuTube, OK.ru, VK Video, Library of Congress, Douyin, TikTok y Bilibili con `lluvia`, `rain`, `下雨` y `дождь`. Para cada llamada registra HTTP, código de plataforma, duración, número de resultados y presencia de stream sin guardar credenciales ni URLs firmadas.

## Ejecución

```bash
uv run python scripts/smoke_sources.py --mode candidates --output data/reports/candidates.json
uv run python scripts/render_candidates_report.py --input data/reports/candidates.json --output docs/EVIDENCIA_FUENTES_V2.md
```

**Pendientes:** ejecutar la medición en GitHub Actions desde `main`, publicar la tabla fechada y comparar bloqueos de IP con runner propio (M20). El workflow actual no invoca automáticamente `--mode candidates`; es necesario configurarlo o ejecutarlo explícitamente en un entorno autorizado. La solicitud de nuevo YAML debe revisarse separadamente, sin modificar `.github/` en M12.
