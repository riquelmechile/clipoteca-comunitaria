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
| register_media_url | {url,kind,title,author,source,license_note?} | Registro público, Bearer de escritura |
| create_media_upload | {kind,title,author,source,license_note?} | Ticket de 15 minutos; público con KV + Actions; sin PAT personal |
| get_media_status | {media_id} | Estado público: uploading, processing, registered, rejected (reason) |
| crear_subida | {} | Sesión de 24 h hasta 12 archivos |
| ticket_subida | {session_id,kind,title?,author?,source?} | PUT binario para agentes |
| subir_archivo | {session_id,file,kind?} | Archivo temporal del host MCP |
| subir_imagen_base64 | {session_id,image_base64} | Imagen hasta 40 KiB |
| estado_subida | {session_id} | Estado del lote |
| list_sources | {} | {sources:[{provider,status}], kind?}, snapshot de GitHub Actions |

Las solicitudes de importacion reales exigen el token configurado; las busquedas de catalogo son publicas, con cuota acotada para import_if_missing. Los valores de credenciales nunca se devuelven.

## Subir desde el celular

Primero intenta `subir_archivo` con el archivo del host; después `ticket_subida` con shell/red o `subir_imagen_base64` para imágenes pequeñas. Solo pide abrir un enlace si el host no puede subir directamente. Ver [SUBIDAS.md](SUBIDAS.md).

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
Bearer de escritura; cuota propia de video: 1 cada 5 minutos y 50 por día UTC por IP
(best-effort por Cache API). Consulta get_video_status
con el id aleatorio de 128 bits hasta done, failed o expired. file_url sirve
el MP4 aprobado durante siete días, con Range. Si aún no aparece la corrida,
reintenta ante 404 transitorio; warnings indica evidencia no disponible.
La entrega no espera el índice; clips_indexed se actualiza al terminar este. El id protege el enlace de entrega, no la privacidad del contenido; la corrida de Actions es pública; los
nombres de Actions usan su SHA-256. El Worker necesita actions: read y write
en CLIPOTECA_DISPATCH_TOKEN. La publicación del MP4 no existe en el contrato
nuevo: solo se indexan los clips válidos importados. Ver
[M32_RENDER.md](M32_RENDER.md).

Todo lo que subas (archivos, guion y video final) queda público.


### Aportes de video y clips fijados (etapa 2)

Todo lo que subas (archivos, guion y video final) queda público. `media.ref` acepta un aporte registrado de video (`u...` o `a...`) o un clip publicado del catálogo (`clip-NNNNNN`). `start` y `end` limitan la ventana en segundos; sin ellos se busca una ventana de buena calidad en el material indicado. No se filtra el material elegido por CLIP/OCR, pero se verifica resolución, decodificación, negro y congelamiento. El ajuste permite recorte o ralentización de hasta 1,25 veces, no pingpong. Por defecto `on_error: "fail"`; `on_error: "search"` permite buscar al fallar la selección inicial, y exige `query` y `prompt`. Las imágenes llegan en la etapa 3. La entrega distingue `contribution`, `catalog_pinned`, `catalog` e `imported`; el aporte solo se indexa tras QA aprobada y el clip fijado no se vuelve a indexar.

Ejemplo mixto (sustituye los identificadores por los registrados en tu catálogo):

```json
{
  "title": "Agua",
  "aspect": "9:16",
  "scenes": [
    {
      "id": "intro",
      "type": "intro",
      "title": "Mar",
      "say": "El mar.",
      "media": {
        "ref": "u77",
        "start": 0,
        "end": 5
      }
    },
    {
      "id": "fact",
      "type": "fact",
      "title": "Agua",
      "say": "El agua fluye.",
      "media": {
        "ref": "clip-000001"
      }
    },
    {
      "id": "outro",
      "type": "outro",
      "title": "Lluvia",
      "say": "La lluvia cae.",
      "query": {
        "es": [
          "lluvia"
        ],
        "en": [
          "rain"
        ]
      },
      "prompt": "falling rain"
    }
  ]
}
```


### Escenas con imágenes y SVG propios

`media.ref` admite fotos registradas JPEG, PNG o WebP (hasta 10 MiB y 24 MP, lado largo mínimo del 50 % del formato final) y SVG del directorio `assets/` mediante `asset:<nombre>`. No admite SVG de usuarios ni rutas libres. Las opciones `motion` (auto, zoom_in, zoom_out, pan_left, pan_right) y `framing` (cover, contain) son exclusivas de imágenes; start/end solo sirven para video. Por defecto las fotos usan cover y los SVG contain. El movimiento se genera otra vez a la duración de la voz real. Los orígenes contribution_image y repo_asset no se indexan. Todo aporte es público. Para el ejemplo, sustituir u78 por el ID de una foto registrada de resolución suficiente.

```json
{
  "title": "Clipoteca",
  "aspect": "16:9",
  "scenes": [
    {
      "id": "intro",
      "type": "intro",
      "title": "Foto",
      "say": "Una foto aportada.",
      "media": {
        "ref": "u78",
        "motion": "zoom_in",
        "framing": "cover"
      }
    },
    {
      "id": "fact",
      "type": "fact",
      "title": "Flujo",
      "say": "Así funciona Clipoteca.",
      "media": {
        "ref": "asset:clipoteca-pipeline",
        "motion": "auto"
      }
    },
    {
      "id": "outro",
      "type": "outro",
      "title": "Mar",
      "say": "El mar.",
      "query": {
        "es": [
          "mar"
        ],
        "en": [
          "sea"
        ]
      },
      "prompt": "ocean waves"
    }
  ]
}
```
