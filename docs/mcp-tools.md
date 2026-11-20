# Contrato MCP v1

El endpoint `/mcp` usa Streamable HTTP sin estado con el SDK oficial TypeScript v2. Las respuestas contienen objetos estructurados y URLs; nunca bytes de video.

| Tool | Input | Output |
|---|---|---|
| `search_clips` | `{query, aspect_ratio?, max_duration?, min_width?, source?, limit?, import_if_missing?}` | `{clips, next_cursor, request_id?, status?}` |
| `get_clip` | `{clip_id}` | `{clip}` conforme a `clip.schema.json` |
| `download_clip` | `{clip_id}` | `{url}` directa al MP4 publicado |
| `search_and_import` | `{query, aspect_ratio?, max_results?}` | `{request_id, status}` |
| `get_job_status` | `{request_id}` | objeto conforme a `job.schema.json` |
| `list_sources` | `{}` | `{sources:[{provider,status}]}` |

Las operaciones de escritura exigen credencial. Los estados de soporte válidos son `SUPPORTED_SEARCH_DOWNLOAD`, `SUPPORTED_SEARCH_ONLY`, `AUTH_REQUIRED`, `RATE_LIMITED`, `UNAVAILABLE` y `TIMEOUT`.

