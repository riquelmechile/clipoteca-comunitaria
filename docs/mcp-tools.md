# Contrato MCP v1 (Streamable HTTP)

Endpoint /mcp con SDK oficial TypeScript. Expone **exactamente siete tools** con outputSchema tipado por herramienta; no usa envoltorio genérico {result} ni esquemas z.unknown(). Las respuestas estructuradas contienen metadatos y enlaces, nunca bytes de video.

| Tool | Entrada | Salida estructurada |
|---|---|---|
| search_clips | {query, aspect_ratio?, max_duration?, min_width?, fps?, source?, language?, has_audio?, limit?, cursor?, import_if_missing?} | {clips, next_cursor, request_id?, status?, semantic?, semantic_reason?, scores?} con paginación real y RRF M22 |
| get_clip | {clip_id} | {clip} conforme a clip.schema.json, con source_start/source_end y storage.storyboard_urls opcionales |
| get_storyboard | {clip_id} | {clip_id, frames:[{url,index,timestamp_seconds}]} con previews WebP para inspección previa a la descarga; clips antiguos entregan lista vacía |
| download_clip | {clip_id} | {clip_id?, url} de GitHub Releases |
| search_and_import | {query, aspect_ratio?, max_results?} | {request_id, status}, credencial necesaria |
| get_job_status | {request_id} | Registro Job conforme a job.schema.json, o error MCP si 404 |
| list_sources | {} | {sources:[{provider,status}], kind?}, snapshot de GitHub Actions |

Las solicitudes de importación reales exigen el token configurado; las búsquedas de catálogo son públicas, con cuota acotada para import_if_missing. Los valores de credenciales nunca se devuelven.

## Seguridad y host de despliegue

Cuando MCP_ALLOWED_HOSTS no está configurado, el Worker acepta el Host que coincide exactamente con el hostname de la URL propia de la request (dominio workers.dev o dominio personalizado). No acepta un Host falsificado. MCP_ALLOWED_ORIGINS puede especificarse para clientes de navegador; las solicitudes sin Origin se aceptan si superan la validación de Host.

El endpoint /v1/health comprueba acceso real al catálogo e inicialización del servidor MCP; responde degraded/503 ante fallas. No implica que las 15 APIs externas estén disponibles.

## Estados e información de fuentes

list_sources usa data/sources.json generado en Actions en cada ingesta y por cron diario. Se publica únicamente el booleano credentials_present junto con estados y capacidades, sin valores secretos. Los estados posibles incluyen SUPPORTED_SEARCH_DOWNLOAD, SUPPORTED_SEARCH_ONLY, AUTH_REQUIRED, RATE_LIMITED, UNAVAILABLE y TIMEOUT.

La publicación real, descarga y E2E de terceros deben validarse por separado. Para desplegar ver [DESPLIEGUE.md](DESPLIEGUE.md).

## M22 · Fusión semántica

`search_clips` retorna `semantic: ready | pending | unavailable`. En `ready`, `scores` es un mapa de IDs a puntaje RRF (`1/(60+rank_lexical) + 1/(60+rank_semántico)` para rankings presentes, contando desde 1). En `pending` responde búsqueda literal y encola el vector cuando el dispatch está habilitado. En `unavailable` conserva la búsqueda literal. El esquema de cada Clip y las otras tools no cambian. Véase [M22_BUSQUEDA_SEMANTICA.md](M22_BUSQUEDA_SEMANTICA.md).

M22 r2: el campo opcional `semantic_reason` indica la causa del fallback (semantic_disabled, manifest_too_large, shard_corrupt, cpu_budget_exceeded). La búsqueda semántica está apagada de fábrica y pending solo aparece con el modo de consulta liviana de Actions expresamente activado; la búsqueda por término nunca dispara este modo.
