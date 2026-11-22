# M24 - matriz de proveedores e idiomas

**Evidencia live:** reporte de smoke de main 2026-11-22T02:41:01.046489+00:00. **GitHub:** ref=refs/heads/main, SHA=fd17e46aee3fb10a957425e5841cbe43ef572ed9, run_id=44977121143.
Los estados declarativos no acreditan descarga ni publicacion de videos.
Un reporte de busqueda live tampoco acredita MP4 publicado.

| Proveedor | Estado | Search | Preview | Download | es live | en live | zh live | ru live |
|---|---|---|---|---|---|---|---|---|
| bilibili | SUPPORTED_SEARCH_ONLY | best_effort | best_effort | unavailable | UNAVAILABLE (n=0) | UNAVAILABLE (n=0) | UNAVAILABLE (n=0) | UNAVAILABLE (n=0) |
| coverr | AUTH_REQUIRED | best_effort | best_effort | best_effort | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| douyin | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | SESSION_REQUIRED (n=0) | SESSION_REQUIRED (n=0) | SESSION_REQUIRED (n=0) | SESSION_REQUIRED (n=0) |
| dpla | AUTH_REQUIRED | best_effort | unavailable | unavailable | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| dzen | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | best_effort | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) |
| europeana | AUTH_REQUIRED | best_effort | best_effort | best_effort | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| instagram | AUTH_REQUIRED | best_effort | best_effort | unavailable | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| internet_archive | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) |
| library_of_congress | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | best_effort | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | RATE_LIMITED (n=0) | RATE_LIMITED (n=0) |
| nara | AUTH_REQUIRED | best_effort | best_effort | best_effort | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| nasa | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | sin resultados (n=0) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | sin resultados (n=0) | sin resultados (n=0) |
| odysee | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=2) |
| ok_ru | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) |
| peertube | SUPPORTED_SEARCH_DOWNLOAD | supported | supported | supported | sin resultados (n=0) | SUPPORTED_SEARCH_DOWNLOAD (n=2) | sin resultados (n=0) | sin resultados (n=0) |
| pexels | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) |
| pixabay | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) |
| rutube | SUPPORTED_SEARCH_DOWNLOAD | supported | supported | best_effort | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) |
| smithsonian | AUTH_REQUIRED | best_effort | unavailable | unavailable | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| snapchat | AUTH_REQUIRED | best_effort | best_effort | unavailable | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |
| tiktok | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | UNAVAILABLE (n=0) | SESSION_REQUIRED (n=0) | SESSION_REQUIRED (n=0) | SESSION_REQUIRED (n=0) |
| vk_video | SUPPORTED_SEARCH_DOWNLOAD | best_effort | best_effort | best_effort | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) |
| wikimedia_commons | SUPPORTED_SEARCH_DOWNLOAD | supported | best_effort | supported | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) | SUPPORTED_SEARCH_DOWNLOAD (n=3) |
| youtube | AUTH_REQUIRED | supported | best_effort | unavailable | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) | AUTH_REQUIRED (n=0) |

## Comparacion anterior y posterior (solo busqueda)

El estado declarativo no implica descarga verificada; solo los conteos mayores que 0
confirman busqueda en Actions. Ningun resultado de estas sondas comprueba video descargado.

| Proveedor | Antes: run 44972077685 | Despues: run 44977121143 | Variacion |
| --- | --- | --- | --- |
| bilibili | 0 resultados; SUPPORTED_SEARCH_ONLY:4 | 0 resultados; UNAVAILABLE:4 | +0 |
| coverr | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| douyin | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; SESSION_REQUIRED:4 | +0 |
| dpla | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| dzen | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| europeana | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| instagram | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| internet_archive | 0 resultados; UNAVAILABLE:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +12 |
| library_of_congress | 0 resultados; SUPPORTED_SEARCH_DOWNLOAD:1, TIMEOUT:3 | 6 resultados; RATE_LIMITED:2, SUPPORTED_SEARCH_DOWNLOAD:2 | +6 |
| nara | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| nasa | 3 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 3 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| odysee | 11 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 11 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| ok_ru | 0 resultados; UNAVAILABLE:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +12 |
| peertube | 2 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 2 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| pexels | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| pixabay | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| rutube | 0 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +12 |
| smithsonian | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| snapchat | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| tiktok | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; SESSION_REQUIRED:3, UNAVAILABLE:1 | +0 |
| vk_video | 0 resultados; AUTH_REQUIRED:3, UNAVAILABLE:1 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +12 |
| wikimedia_commons | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| youtube | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |

Proveniencia: ref refs/heads/main, SHA fd17e46aee3fb10a957425e5841cbe43ef572ed9, run_id 44977121143.
El numero de sondas candidatas difiere del numero de resultados principales.
Los diagnosticos de candidato pueden no coincidir con la busqueda principal.

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
