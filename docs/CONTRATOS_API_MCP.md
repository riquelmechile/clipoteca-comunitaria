# Contratos — REST, MCP y catálogo

## Endpoints REST

| Método | Ruta | Función | Autorización |
|---|---|---|---|
| GET | `/v1/health` | estado de API/MCP | pública |
| GET | `/v1/sources` | matriz declarativa y estado de cada fuente, sin credenciales | pública |
| GET | `/v1/clips?q=lluvia&aspect_ratio=9%3A16&max_duration=20` | catálogo local | pública |
| GET | `/v1/clips/{id}` | ficha, meta, miniatura | pública |
| GET | `/v1/clips/{id}/download` | redirección a asset GitHub Releases | pública |
| POST | `/v1/search` | busca local y opcionalmente solicita nuevo trabajo externo | limitada por cuota; escritura protegida |
| GET | `/v1/jobs/{request_id}` | progreso de descubrimiento/importación | pública para estado no sensible |
| POST | `/v1/import` | solicita ingesta externa | credencial/permiso específico |
| POST | `/v1/contributions` | registra Issue/URL de contribución | autenticación GitHub o control antiabuso |

M22: POST /v1/search y MCP search_clips incluyen semantic
(ready, pending, unavailable). En ready, scores mapea IDs a puntaje
combinado RRF con k=60; en pending indica vector aún no disponible y
un dispatch de cálculo solicitado; unavailable conserva el ranking léxico.
El objeto Clip no adquiere campos obligatorios, y GET /v1/clips mantiene
su contrato previo.

`GET /v1/clips` jamás dispara trabajo costoso. `POST /v1/search` puede devolver `200` con coincidencias o `202` con coincidencias y `request_id` de búsqueda ampliada. El sistema devuelve enlaces de descarga cuando el archivo ya fue publicado; antes de eso solo hay `processing`.

## Herramientas MCP remotas (SDK oficial TS v2, Streamable HTTP)

- `search_clips(query, aspect_ratio?, max_duration?, min_width?, source?, limit?, import_if_missing?)`: primero catálogo; opcionalmente solicita ronda externa.
- `get_clip(clip_id)`: ficha verificable de un video.
- `download_clip(clip_id)`: enlace a MP4 en GitHub Releases.
- `search_and_import(query, aspect_ratio?, max_results?)`: solicita nueva búsqueda + persistencia asíncrona; operación con permisos.
- `get_job_status(request_id)`: estado de trabajo.
- `list_sources()`: devuelve `{sources: [{provider, status, capabilities: {search, preview, download}}], kind}` con `kind: "actions_credential_presence_snapshot"`. Los niveles `supported`, `best_effort`, `unavailable` son declaraciones sobre las capacidades, **no una garantía de éxito**. Se deriva de `data/sources.json` en `main` y coincide con `GET /v1/sources`. Si falta cualquier `capabilities` o cualquiera de sus tres campos, falla explícitamente; nunca se inventa `unavailable` por omisión.

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
    "source_title": "Дождь на улице",
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

## Clasificación precisa de orientación

Calcular con width/height del stream de video, incorporando rotación de metadatos:  
- Igualdad/aspecto próximo a 9:16 → `9:16`, `vertical`.
- Próximo a 16:9 → `16:9`, `horizontal`.
- Próximo a 1:1 → `1:1`, `square`.
- Otro → `other` con razón reducida (`4:3`, `21:9`, etc.).

Usar tolerancia definida y testeada (p. ej. `abs(w/h - target) < 0.01`); no recortar para convertir orientación. Guardar width/height reales de salida, no deducirlos del nombre de archivo.

## Semántica de búsqueda

- Orden: resultados existentes, relevantes y accesibles; `import_if_missing` inicia nueva ronda sin bloquear.
- Indexar cadenas Unicode normalizadas y ambas lenguas de metadatos; guardar también keywords del idioma original para dedup y trazabilidad.
- Filtros: `aspect_ratio`, `max_duration`, `min_width`, `fps`, `source`, `language`, `has_audio`.
- Paginación con cursor estable cuando el catálogo supere 1000 clips.
