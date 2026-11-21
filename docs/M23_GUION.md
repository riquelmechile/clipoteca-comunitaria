# M23 - script to verified video clips

M23 adds a REST endpoint (`POST /v1/script`) and an MCP tool (`resolve_script`) that turn a storyboard script into one selected published video clip per scene, or an explicit queued/missing state. It uses the same Clipoteca search corpus, video filters, import queue and rate limits as `POST /v1/search` with `import_if_missing`. It does not create a separate GitHub Actions dispatch route.

## Input contract

The request is exactly `{"scenes":[...]}`. Each scene requires five keys:

- `id`: unique ASCII identifier matching `[A-Za-z0-9_-]{1,64}`.
- `narration`: 1..1500 characters, with controls disallowed.
- `query`: 1..200 characters, with controls disallowed.
- `orientation`: `9:16`, `16:9`, `1:1`, `other`, or aliases `portrait`/`vertical`, `landscape`/`horizontal`, `square`.
- `min_duration`: positive number, at most 20 seconds, in the same unit as published clips.

Limits: **1..50 scenes** per request, whole HTTP JSON body **64 KiB**, no unknown keys, no repeated scene IDs. MCP imposes the same scene limits and additionally validates the input with Zod. A 400 `invalid_script` response reports invalid inputs without dispatching jobs.

## Resolution algorithm

1. Load the verified catalog once for the whole script.
2. For each scene, query the existing lexical plus optional semantic search with the mapped `aspect_ratio` and `min_duration`. Do not dispatch an embedding-only job for this pass. Only HTTPS video assets with a known video suffix (`.mp4`, `.webm`, `.mov`, `.m4v`, `.m3u8`) may be selected; image assets are never silently presented as videos.
3. Use maximum bipartite matching to avoid using the same clip twice when alternatives exist. If all available candidates are already claimed, reuse a clip only when unavoidable, marked by `reason: reuse_unavoidable`.
4. When no suitable clip exists, call the existing `submitImport` path, reusing its deterministic `request_id`, prior-job state checks, IP quota, 30-second dispatch cache and GitHub Actions throttling. Return `queued` if the job is pending and a valid request ID exists; otherwise return `missing` and an explicit reason (`rate_limited`, `dispatch_unavailable`, `no_results`, `non_video_material`, `search_unavailable`, etc.). A failed scene does not fail other scenes.
5. The deterministic `script_id` is SHA-256 of the canonical JSON representation of the validated script, so repeating exactly the same normalized script yields the same identifier. The scene result may change when a previously queued job completes and the catalog gains new media.

The response includes a `summary` of `matched`/`queued`/`missing` counts and a `scenes` array in original order. Each scene retains its id/narration/query/orientation/min_duration. `matched` scenes have:

```json
{
  "status": "matched",
  "media": {
    "type": "video",
    "clip_id": "clip-000101",
    "url": "<existing HTTPS MP4 from catalog>",
    "thumbnail_url": "<existing HTTPS WebP or null>",
    "storyboard_urls": ["<existing HTTPS WebP frame>"],
    "duration_seconds": 12,
    "source": {
      "provider": "pexels",
      "source_language": "es",
      "source_title": "<original title when supplied>",
      "source_url": "<original URL when supplied>"
    }
  }
}
```

A `queued` scene has `media:null`, `request_id` and `job:{request_id,status}`. A `missing` scene has `media:null` and a machine-readable `reason`. These are compatible with scene arrays in `scenes.with_media.json` style; the output contains factual source data only, not invented attribution.

## End-to-end example from an MCP agent (five scenes, offline fixture)

Connect an MCP client to the Worker `https://<your-worker-domain>/mcp`, list tools, then call `resolve_script` with these arguments:

```json
{
  "scenes": [
    {"id":"intro","narration":"Drops on the river.","query":"agua","orientation":"landscape","min_duration":8},
    {"id":"rain","narration":"Heavy rain begins.","query":"lluvia","orientation":"16:9","min_duration":8},
    {"id":"forest","narration":"The camera enters the forest.","query":"bosque","orientation":"16:9","min_duration":8},
    {"id":"city","narration":"Traffic appears.","query":"ciudad","orientation":"16:9","min_duration":8},
    {"id":"coast","narration":"The sea closes the film.","query":"olas","orientation":"16:9","min_duration":8}
  ]
}
```

The deterministic fake catalog in `worker/test/script.test.ts` contains five suitable published video records, each with storyboard and thumbnail URLs. The MCP test verifies that all five scenes return `matched`, no clip ID repeats, and the first two scenes are optimally reassigned when they initially compete for the same video.

Example of the *tested shape* (clip IDs from the offline fixture, not an assertion of live availability):

```json
{
  "script_id": "<sha256 of canonical request>",
  "summary": {"matched":5,"queued":0,"missing":0},
  "scenes": [
    {"id":"intro","status":"matched","media":{"clip_id":"clip-000002","type":"video"}},
    {"id":"rain","status":"matched","media":{"clip_id":"clip-000001","type":"video"}},
    {"id":"forest","status":"matched","media":{"clip_id":"clip-000003","type":"video"}},
    {"id":"city","status":"matched","media":{"clip_id":"clip-000004","type":"video"}},
    {"id":"coast","status":"matched","media":{"clip_id":"clip-000005","type":"video"}}
  ]
}
```

This abbreviated example illustrates only the fields needed for an agent to identify matches. The real response carries the complete original scene fields, media URL, preview, source and duration defined by `schemas/script.schema.json` and `docs/openapi.yaml`.

A REST client sends the identical JSON to `POST /v1/script` with `Content-Type: application/json`. It receives HTTP 200 for a valid script even if some scenes are pending/missing, and HTTP 400 for invalid structure. To check pending imports, poll `GET /v1/jobs/{request_id}` and rerun the same script after the catalog is updated. API and MCP responses do not include video bytes.

## Verification status

**Offline tests:** five-scene REST and MCP resolution, no duplicate clips when alternatives exist, mixed matched/queued/missing, shared quotas, deterministic IDs, orientation and minimum duration, image rejection, invalid input and oversized body. M22 r4 regression tests cover empty manifest no-dispatch and short negative-cache TTL.

**Not executed here:** a live MCP invocation against an already deployed production Worker. A real end-to-end run requires a deployed Worker, its live catalog and suitable source videos. Do not present offline fixture IDs or placeholder URLs as published production assets.

Run `cd worker && npm ci && npm run typecheck && npm test`, and `uv run pytest -q`. The three CI jobs (python, worker and real-argos) must be verified on the exact PR head before delivery.


## Correcciones M23 r2

- Un guion con 50 escenas sin material no ejecuta 50 lecturas anonimas a GitHub. El resolver deduplica consultas por el mismo request_id, comprueba primero la cuota de import, limita a una comprobacion efectiva de job por guion (cuota de import por IP: 15 s); el tope defensivo de tres permanece, pero habitualmente no se alcanza y, tras el primer rate_limited, devuelve missing/rate_limited a las escenas restantes. La operacion comparte la cuota import (no crea otra ruta de dispatch).
- Cada clip se tokeniza una vez por guion; la busqueda solo-video se aplica antes de paginar. Medicion original antes de optimizar: 1000 clips aproximadamente 340 ms por guion de 50 escenas; 5000 clips aproximadamente 1,5 s. Workers Free (10 ms CPU) no es un objetivo realista para catalogos de ese tamano: usar Workers Paid y medir CPU real, o reducir escenas. Estos tiempos son de la revision anterior.
- Narration permite tab, nueva linea y retorno de carro (escapes JSON: \t, \n, \r); rechaza otros controles. Los maximos se aplican a Unicode code points, no unidades UTF-16.
- Errores de validacion del request: HTTP 400 invalid_script. Fallos internos despues de validar: HTTP 500 internal_error.
- La importacion actual no transporta min_duration a la pipeline Python: el clip importado puede tener duracion menor. Un nuevo guion filtrara ese clip hasta que exista uno suficientemente largo.
- El resumen incluye matched, queued, missing y reused; reused cuenta matched con reason=reuse_unavoidable.
- Los alias de orientacion se convierten a 9:16, 16:9 o 1:1 al validar, por lo que portrait y 9:16 producen el mismo script_id. Reordenar escenas cambia el hash: su orden es parte del guion.

### M24: coste y estado por escena

Con semantica activa, un guion consulta a lo sumo 3 vectores de consultas normalizadas distintos en GitHub; las otras escenas se resuelven lexicalmente y las queries repetidas reutilizan el resultado de la busqueda. 50 consultas distintas: <=8 fetches GitHub para semantica en el fixture offline. Los motivos non_video_material solo se devuelven cuando la busqueda completa realmente encuentra imagenes pero ningun video. Import por IP tiene ventana de 15 segundos: como maximo un lookup/dispatch efectivo por guion en la configuracion actual. Enviar el mismo guion otra vez pasados 15 segundos permite que otra escena pendiente utilice la cuota. Una medicion nueva en Node (no workerd) dio aproximadamente 33 ms con 1000 clips y 140 ms con 5000 clips; no equivale a latencia Cloudflare. Narration rechaza U+0085, U+2028 y U+2029 ademas de los otros controles prohibidos.
