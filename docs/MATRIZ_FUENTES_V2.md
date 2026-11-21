# M24 - matriz de proveedores e idiomas

**Evidencia live:** pendiente del smoke en main.
Los estados declarativos no acreditan descarga ni publicacion de videos.
Un reporte de busqueda live tampoco acredita MP4 publicado.

| Proveedor | Estado | Search | Preview | Download | es live | en live | zh live | ru live |
|---|---|---|---|---|---|---|---|---|
| bilibili | SUPPORTED_SEARCH_ONLY | best_effort | best_effort | unavailable | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| coverr | AUTH_REQUIRED | best_effort | best_effort | best_effort | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| douyin | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| dpla | AUTH_REQUIRED | best_effort | unavailable | unavailable | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| dzen | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| europeana | AUTH_REQUIRED | best_effort | best_effort | best_effort | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| instagram | AUTH_REQUIRED | best_effort | best_effort | unavailable | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| internet_archive | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| library_of_congress | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| nara | AUTH_REQUIRED | best_effort | best_effort | best_effort | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| nasa | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| odysee | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| ok_ru | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| peertube | SUPPORTED_SEARCH_DOWNLOAD | supported | supported | supported | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| pexels | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| pixabay | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| rutube | SUPPORTED_SEARCH_DOWNLOAD | supported | supported | best_effort | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| smithsonian | AUTH_REQUIRED | best_effort | unavailable | unavailable | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| snapchat | AUTH_REQUIRED | best_effort | best_effort | unavailable | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| tiktok | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| vk_video | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| wikimedia_commons | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |
| youtube | AUTH_REQUIRED | supported | best_effort | unavailable | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main | pendiente del smoke en main |

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
