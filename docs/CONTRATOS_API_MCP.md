# Contratos - REST, MCP y catalogo

## Endpoints REST

| Metodo | Ruta | Funcion | Autorizacion |
|---|---|---|---|
| GET | `/v1/health` | estado de API/MCP | publica |
| GET | `/v1/sources` | matriz declarativa y estado de cada fuente, sin credenciales | publica |
| GET | `/v1/clips?q=lluvia&aspect_ratio=9%3A16&max_duration=20` | catalogo local | publica |
| GET | `/v1/clips/{id}` | ficha, meta, miniatura | publica |
| GET | `/v1/clips/{id}/download` | redireccion a asset GitHub Releases | publica |
| POST | `/v1/script` | 1..50 scenes: matched video, queued import or explicitly missing | shared import quota |
| POST | `/v1/search` | busca local y opcionalmente solicita nuevo trabajo externo | limitada por cuota; escritura protegida |
| GET | `/v1/jobs/{request_id}` | progreso de descubrimiento/importacion | publica para estado no sensible |
| POST | `/v1/import` | solicita ingesta externa | credencial/permiso especifico |
| POST | `/v1/contributions` | registra Issue/URL de contribucion | autenticacion GitHub o control antiabuso |

M22: POST /v1/search y MCP search_clips incluyen semantic
(ready, pending, unavailable). En ready, scores mapea IDs a puntaje
combinado RRF con k=60; en pending indica vector aun no disponible y
un dispatch de calculo solicitado; unavailable conserva el ranking lexico.
El objeto Clip no adquiere campos obligatorios, y GET /v1/clips mantiene
su contrato previo.

`GET /v1/clips` jamas dispara trabajo costoso. `POST /v1/search` puede devolver `200` con coincidencias o `202` con coincidencias y `request_id` de busqueda ampliada. El sistema devuelve enlaces de descarga cuando el archivo ya fue publicado; antes de eso solo hay `processing`.

## Herramientas MCP remotas (SDK oficial TS v2, Streamable HTTP)

- `search_clips(query, aspect_ratio?, max_duration?, min_width?, source?, limit?, import_if_missing?)`: primero catalogo; opcionalmente solicita ronda externa.
- `resolve_script(scenes)`: per-scene matched/queued/missing, media, factual source, storyboard and deterministic script_id; shared import quota.
- `get_clip(clip_id)`: ficha verificable de un video.
- `download_clip(clip_id)`: enlace a MP4 en GitHub Releases.
- `search_and_import(query, aspect_ratio?, max_results?)`: solicita nueva busqueda + persistencia asincrona; operacion con permisos.
- `get_job_status(request_id)`: estado de trabajo.
- `list_sources()`: devuelve `{sources: [{provider, status, capabilities: {search, preview, download}}], kind}` con `kind: "actions_credential_presence_snapshot"`. Los niveles `supported`, `best_effort`, `unavailable` son declaraciones sobre las capacidades, **no una garantia de exito**. Se deriva de `data/sources.json` en `main` y coincide con `GET /v1/sources`. Si falta cualquier `capabilities` o cualquiera de sus tres campos, falla explicitamente; nunca se inventa `unavailable` por omision.

Ninguna respuesta MCP incluye bytes de video embebidos. Debe retornar objetos estructurados estables y URLs directas al almacenamiento.

## Metadatos publicados, esquema v1

```json
{
  "id": "clip-000125",
  "title": {"es": "Lluvia en las calles", "en": "Rain on the streets"},
  "description": {"es": "Calle mojada de noche", "en": "Wet street at night"},
  "tags": {"es": ["lluvia", "calle"], "en": ["rain", "street"]},
  "source": {
    "provider": "rutube",
    "provider_id": "source-id",
    "source_language": "ru",
    "source_title": "----- -- -----",
    "source_url": "https://example.org/original/123"
  },
  "media": {
    "duration_seconds": 18.2,
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "aspect_ratio": "9:16",
    "orientation": "vertical",
    "codec": "h264",
    "container": "mp4",
    "has_audio": false,
    "size_bytes": 14300000,
    "sha256": "<sha256-real-al-publicar>",
    "perceptual_hash": "<hash-real-al-publicar>"
  },
  "storage": {
    "provider": "github_releases",
    "asset_url": "<url-real-al-publicar>",
    "thumbnail_url": "<url-real-al-publicar>"
  },
  "status": "published",
  "created_at": "2026-11-20T00:00:00Z"
}
```

**Los ejemplos no son registros reales.** No mostrar URLs inventadas como si un archivo estuviera publicado.

## Clasificacion precisa de orientacion

Calcular con width/height del stream de video, incorporando rotacion de metadatos:
- Igualdad/aspecto proximo a 9:16 - `9:16`, `vertical`.
- Proximo a 16:9 - `16:9`, `horizontal`.
- Proximo a 1:1 - `1:1`, `square`.
- Otro - `other` con razon reducida (`4:3`, `21:9`, etc.).

Usar tolerancia definida y testeada (p. ej. `abs(w/h - target) < 0.01`); no recortar para convertir orientacion. Guardar width/height reales de salida, no deducirlos del nombre de archivo.

## Semantica de busqueda

- Orden: resultados existentes, relevantes y accesibles; `import_if_missing` inicia nueva ronda sin bloquear.
- Indexar cadenas Unicode normalizadas y ambas lenguas de metadatos; guardar tambien keywords del idioma original para dedup y trazabilidad.
- Filtros: `aspect_ratio`, `max_duration`, `min_width`, `fps`, `source`, `language`, `has_audio`.
- Paginacion con cursor estable cuando el catalogo supere 1000 clips.

M22 r2: `semantic_reason` es opcional y solo expone la categoria de degradacion, jamas errores upstream. La comparacion semantica exige `SEMANTIC_SEARCH=1`, default absoluto de coseno 0,20 con margen relativo 0,05 y presupuesto de clips. El estado `pending` exige `CLIPOTECA_QUERY_DISPATCH_MODE=1` y el YAML de modo liviano; de lo contrario retorna `unavailable` sin iniciar ingest solo para calcular el vector.

## M23: script-to-video compatibility

POST /v1/script and MCP resolve_script share strict input validation, maximum 50 scenes, 1500 characters of narration and 200 of query. Orientation maps to aspect_ratio; min_duration filters results. Video URLs require explicit video extensions, never image assets. Imports use submitImport, existing rate limits, deterministic job identity and status checks. Mixed matched, queued and missing scenes do not fail the script. script_id is the SHA-256 of the canonical validated script. See [M23_GUION.md](M23_GUION.md).
