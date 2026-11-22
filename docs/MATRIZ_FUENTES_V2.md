# M24 - matriz de proveedores e idiomas

**Evidencia live:** reporte de smoke de main 2026-11-22T01:34:44.130831+00:00. **GitHub:** ref=refs/heads/main, SHA=bf0454baa76c7aa8e2528b51f38302b30581cf0d, run_id=44972077685.
Los estados declarativos no acreditan descarga ni publicacion de videos.
Un reporte de busqueda live tampoco acredita MP4 publicado.

| Proveedor | Estado | Search | Preview | Download | es live | en live | zh live | ru live |
|---|---|---|---|---|---|---|---|---|
| bilibili | SUPPORTED_SEARCH_ONLY | best_effort | best_effort | unavailable | sin resultados (n=0) | sin resultados (n=0) | sin resultados (n=0) | sin resultados (n=0) |
| coverr | AUTH_REQUIRED | best_effort | best_effort | best_effort | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| douyin | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| dpla | AUTH_REQUIRED | best_effort | unavailable | unavailable | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| dzen | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | best_effort | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) |
| europeana | AUTH_REQUIRED | best_effort | best_effort | best_effort | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| instagram | AUTH_REQUIRED | best_effort | best_effort | unavailable | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| internet_archive | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | UNAVAILABLE (n=0) | UNAVAILABLE (n=0) | UNAVAILABLE (n=0) | UNAVAILABLE (n=0) |
| library_of_congress | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | best_effort | TIMEOUT (n=0) | TIMEOUT (n=0) | sin resultados (n=0) | TIMEOUT (n=0) |
| nara | AUTH_REQUIRED | best_effort | best_effort | best_effort | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| nasa | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | sin resultados (n=0) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | sin resultados (n=0) | sin resultados (n=0) |
| odysee | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=2) |
| ok_ru | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | UNAVAILABLE (n=0) | UNAVAILABLE (n=0) | UNAVAILABLE (n=0) | UNAVAILABLE (n=0) |
| peertube | SUPPORTED_SEARCH_DOWNLOAD | supported | supported | supported | sin resultados (n=0) | SUPPORTED_SEARCH_DOWNLOAD (n=2) | sin resultados (n=0) | sin resultados (n=0) |
| pexels | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) |
| pixabay | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) |
| rutube | SUPPORTED_SEARCH_DOWNLOAD | supported | supported | best_effort | sin resultados (n=0) | sin resultados (n=0) | sin resultados (n=0) | sin resultados (n=0) |
| smithsonian | AUTH_REQUIRED | best_effort | unavailable | unavailable | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| snapchat | AUTH_REQUIRED | best_effort | best_effort | unavailable | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| tiktok | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| vk_video | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | AUTH_REQUIRED (n=0) | UNAVAILABLE (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| wikimedia_commons | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) |
| youtube | AUTH_REQUIRED | supported | best_effort | unavailable | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |

## Regeneracion

```sh
uv run python -m clipoteca export-sources --output data/sources.json
uv run python scripts/render_matrix_v2.py --output docs/MATRIZ_FUENTES_V2.md
# Solo tras ejecutar smoke real en main:
uv run python scripts/smoke_sources.py --mode providers --output data/reports/smoke.json
uv run python scripts/render_matrix_v2.py --smoke-report data/reports/smoke.json --output docs/MATRIZ_FUENTES_V2.md
```

Nunca escribir credenciales, URLs firmadas ni resultados supuestos.
Sin --smoke-report se preserva el estado de evidencia pendiente.
La evidencia live anota ref, SHA y run_id si el smoke los entrega.
