# Contrato MCP v1 (Streamable HTTP)

Endpoint /mcp con SDK oficial TypeScript. Expone **exactamente diez tools** con outputSchema tipado por herramienta; no usa envoltorio generico {result} ni un envoltorio generico. Las respuestas estructuradas contienen metadatos y enlaces, nunca bytes de video.

| Tool | Entrada | Salida estructurada |
|---|---|---|
| search_clips | {query, aspect_ratio?, max_duration?, min_width?, fps?, source?, language?, has_audio?, limit?, cursor?, import_if_missing?} | {clips, next_cursor, request_id?, status?, semantic?, semantic_reason?, scores?} con paginacion real y RRF M22 |
| resolve_script | {scenes:[{id,narration,query,orientation,min_duration}]} (1..50) | {script_id,scenes:[{status:matched/queued/missing,media,reason?,request_id?,job?}],summary}; video-only + shared quota |
| get_clip | {clip_id} | {clip} conforme a clip.schema.json, con source_start/source_end y storage.storyboard_urls opcionales |
| get_storyboard | {clip_id} | {clip_id, frames:[{url,index,timestamp_seconds}]} con previews WebP para inspeccion previa a la descarga; clips antiguos entregan lista vacia |
| download_clip | {clip_id} | {clip_id?, url} de GitHub Releases |
| search_and_import | {query, aspect_ratio?, max_results?} | {request_id, status}, credencial necesaria |
| get_job_status | {request_id} | Registro Job conforme a job.schema.json, o error MCP si 404 |
| render_video | {script, index_clips?} (3–12 escenas, ≤60 s, 9:16/16:9/1:1) | {id,status,status_url}; Bearer de escritura |
| get_video_status | {id} | {id,status,stages?,qa?,clips_indexed?,file_url?} |
| list_sources | {} | {sources:[{provider,status}], kind?}, snapshot de GitHub Actions |

Las solicitudes de importacion reales exigen el token configurado; las busquedas de catalogo son publicas, con cuota acotada para import_if_missing. Los valores de credenciales nunca se devuelven.

## Seguridad y host de despliegue

Cuando MCP_ALLOWED_HOSTS no esta configurado, el Worker acepta el Host que coincide exactamente con el hostname de la URL propia de la request (dominio workers.dev o dominio personalizado). No acepta un Host falsificado. MCP_ALLOWED_ORIGINS puede especificarse para clientes de navegador; las solicitudes sin Origin se aceptan si superan la validacion de Host.

El endpoint /v1/health comprueba acceso real al catalogo e inicializacion del servidor MCP; responde degraded/503 ante fallas. No implica que las 15 APIs externas esten disponibles.

## Estados e informacion de fuentes

list_sources usa data/sources.json generado en Actions en cada ingesta y por cron diario. Se publica unicamente el booleano credentials_present junto con estados y capacidades, sin valores secretos. Los estados posibles incluyen SUPPORTED_SEARCH_DOWNLOAD, SUPPORTED_SEARCH_ONLY, AUTH_REQUIRED, RATE_LIMITED, UNAVAILABLE y TIMEOUT.

La publicacion real, descarga y E2E de terceros deben validarse por separado. Para desplegar ver [DESPLIEGUE.md](DESPLIEGUE.md).

## M22 - Fusion semantica

`search_clips` retorna `semantic: ready | pending | unavailable`. En `ready`, `scores` es un mapa de IDs a puntaje RRF (`1/(60+rank_lexical) + 1/(60+rank_semantico)` para rankings presentes, contando desde 1). En `pending` responde busqueda literal y encola el vector cuando el dispatch esta habilitado. En `unavailable` conserva la busqueda literal. El esquema de cada Clip y las otras tools no cambian. Vease [M22_BUSQUEDA_SEMANTICA.md](M22_BUSQUEDA_SEMANTICA.md).

M22 r2: el campo opcional `semantic_reason` indica la causa del fallback (semantic_disabled, manifest_too_large, shard_corrupt, cpu_budget_exceeded). La busqueda semantica esta apagada de fabrica y pending solo aparece con el modo de consulta liviana de Actions expresamente activado; la busqueda por termino nunca dispara este modo.

## M23 script scenes

`resolve_script` calls the same validated REST resolver as `POST /v1/script`. Each media includes a verified video URL, thumbnail, storyboard, duration and factual provider/source. Missing scenes never return a disguised image. Imports reuse existing import_if_missing quotas, request IDs and job statuses. See [M23_GUION.md](M23_GUION.md).

## M33: entrega de video

El guion completo sigue schemas/video.schema.json. Llama render_video con el
Bearer de escritura; comparte cuota con importaciones. Consulta get_video_status
con el id aleatorio de 128 bits hasta done, failed o expired. file_url sirve
el MP4 aprobado durante siete días. El id es una credencial privada; los
nombres de Actions usan su SHA-256. El Worker necesita actions: read y write
en CLIPOTECA_DISPATCH_TOKEN. La publicación del MP4 no existe en el contrato
nuevo: solo se indexan los clips válidos importados. Pendiente de promoción
y despliegue; ver [M32_RENDER.md](M32_RENDER.md).
