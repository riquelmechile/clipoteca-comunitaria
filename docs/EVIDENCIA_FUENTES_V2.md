# Evidencia de fuentes candidatas v2 · M12

**Estado:** Pendiente: se completa tras ejecutar el workflow smoke en main.

El modo `candidates` mide RuTube, OK.ru, VK Video, Dzen, Library of Congress, Douyin, TikTok y Bilibili con `lluvia`, `rain`, `下雨` y `дождь`. Para cada llamada registra HTTP, código de plataforma, duración, número de resultados y presencia de stream sin guardar credenciales ni URLs firmadas.

## Ejecución

```bash
uv run python scripts/smoke_sources.py --mode candidates --output data/reports/candidates.json
uv run python scripts/render_candidates_report.py --input data/reports/candidates.json --output docs/EVIDENCIA_FUENTES_V2.md
```

**M17 (sin evidencia live):** OK.ru, VK Video anónimo y Dzen quedan declarados `best_effort`; los fixtures offline no se consideran confirmación de disponibilidad ni de CDN.

**Clip real pendiente:** la consulta `дождь` y la descarga de un clip real de M17 deben ejecutarse desde `main`; este PR no presenta los fixtures offline como evidencia live.

**Pendientes:** ejecutar la medición en GitHub Actions desde `main`, publicar la tabla fechada y comparar bloqueos de IP con runner propio (M20). El workflow actual no invoca automáticamente `--mode candidates`; es necesario configurarlo o ejecutarlo explícitamente en un entorno autorizado. La solicitud de nuevo YAML debe revisarse separadamente, sin modificar `.github/` en M12.

**M19 (sin evidencia live):** las publicaciones de clips reales de la consulta
下雨 (Douyin) y rain (TikTok) quedan expresamente **pendientes** del smoke en
main. Los fixtures offline y el CI verde no acreditan resultados de
plataformas reales ni disponibilidad desde GitHub Actions. La sonda M12
firmada informa status_code 2483 sin filtrar cookies ni URLs de firma.
