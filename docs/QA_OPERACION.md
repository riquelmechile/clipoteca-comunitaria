# QA, protección operativa y costos

## Pruebas automáticas

| Capa | Fixtures obligatorias | Resultado esperado |
|---|---|---|
| Idiomas | `lluvia`, traducciones ES/EN/ZH/RU, caracteres Unicode | se ejecutan cuatro búsquedas y se guarda `es`/`en` |
| Proveedores | respuesta válida/vacía/429/500/timeout, HTML no media | conector aislado con estado fiel |
| Medios | 9:16, 16:9, cuadrado, 4:3, rotación, VFR, 24s, corrupto | dimensiones calculadas y MP4 <=20s o fallo explícito |
| Duplicados | misma fuente, mismo SHA, distintas resoluciones de la misma toma | ninguna entrada doble exacta |
| Releases | asset subido y validado; subida incompleta | el JSON solo indexa assets verificados |
| Paralelismo | dos búsquedas del mismo tema a la vez | estado serializado, sin commits perdidos |
| API/MCP | filtros, paginación, 202, 404, herramientas | respuestas tipadas, URL real, sin bytes MP4 |
| Seguridad | URL local/metadata endpoint/redirección, ZIP bomb, 1GB fake | bloqueado con límites claros |

## Controles técnicos esenciales

- URLs externas validadas y redirecciones revalidadas: bloquear IP internas, `localhost`, metadatos cloud, esquemas fuera de HTTPS, puertos peligrosos y cambios DNS (SSRF).
- Descargar con `Content-Length` máximo configurable, límite de bytes real, timeouts y eliminación de temporales; no permitir archivos enormes por un clip de 20 s.
- FFmpeg desde paquetes de runner, proceso con límites de CPU/tiempo/memoria/espacio, nombres seguros, sandbox del job, sin comandos de shell derivados de entradas.
- Escaneos/metadata no ejecutan scripts del clip; nunca ejecutar PR de terceros con `GITHUB_TOKEN` de escritura.
- Tokens GitHub con permisos mínimos, secreto Worker para dispatch, claves de proveedores solo en Actions Secrets, logs con redacción.
- API pública de lectura; crear trabajos/invocar importación requiere autorización o límites antiabuso explícitos (sin necesidad de cuentas propias).
- Uso de `concurrency` GitHub en publicación y escrituras del catálogo; retries de conflictos SHA.
- Salida no falsificada: si no hay URL real de media, devolver `search_only`, no simular descarga.

## Límites de plataforma verificados en documentación de noviembre 2026

- GitHub Releases: hasta **1.000 assets por Release** y **<2 GiB por archivo**; no equivale a espacio gratis ilimitado garantizado.
- GitHub Actions: runners estándar gratuitos en repositorios públicos, con políticas de uso y límites de concurrencia/duración.
- GitHub adjuntos de video: **10 MB en GitHub Free** (documentación actualizada en septiembre 2026); subir por URL para mayores.
- Cloudflare Workers Free: **100.000 solicitudes/día**, límite de CPU por invocación; solo API/metadata, no ejecutar FFmpeg allí.
- Traducción offline: modelos Argos (`zh`, `ru`, `en`, `es`) empaquetados/fijados en runner. Si el paquete exacto falla, registrar error, no inventar texto traducido.

## Indicadores (observabilidad sin base de datos)

- `search_requests_total`, `search_requests_by_language`, `provider_success_rate`, `provider_timeout_rate`.
- `new_candidates`, `downloaded_clips`, `deduplicated_clips`, `published_clips`, `failed_imports`.
- `mean_discovery_duration`, `mean_publish_duration`, `github_actions_minutes`, `assets_storage_estimate`, `cloudflare_requests`.
- Registrar por workflow en un reporte JSON o summary de Actions, y sumar en reporte de operaciones versionado periódicamente.

## Runbook mínimo

1. Si falla un proveedor: verificar su estado en `list_sources`, no detener los demás.
2. Si falla publicación: no indexar el asset; reintentar después de verificar nombre/hash.
3. Si hay duplicado: retornar el ID existente y no consumir nuevamente red/CPU.
4. Si hay conflicto de `clips.json`: releer SHA y fusionar por IDs, luego commit serializado.
5. Si se supera cuota gratuita: pausar dispatch de búsquedas externas, mantener API de lectura y descargas existente.
6. Si GitHub limita distribución multimedia: suspender nuevas subidas y cambiar el adaptador de almacenamiento sin pérdida del catálogo.

## Documentación oficial para mantener actualizada

- https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- https://docs.github.com/en/actions/concepts/billing-and-usage
- https://docs.github.com/en/rest/actions/workflows
- https://developers.cloudflare.com/workers/platform/limits/
- https://github.com/modelcontextprotocol/typescript-sdk
- https://github.blog/changelog/2026-09-01-github-cli-media-in-issues-pull-requests-and-comments/
