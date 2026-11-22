# M24 - demo E2E v2 reproducible (pendiente de ejecucion real)

**Estado:** guion ejecutable; no se ha ejecutado aqui contra un Worker desplegado ni un proveedor live. Los fixtures offline y el CI verde NO sustituyen la demostracion. Ejecutar desde `main` en un entorno autorizado con recursos publicados y guardar SHA, fecha UTC, request_id, clip IDs, manifests SHA y enlace al run de Actions. No inventar capturas, clips, costes ni tiempos.

## 0. Preparacion

```sh
git rev-parse HEAD
uv sync --frozen --extra dev
uv run python -m clipoteca version
cd worker && npm ci && npm run typecheck && npm test && cd ..
uv run pytest -q tests/test_scenes_m13.py tests/test_m14_scenes_storyboard.py tests/test_embeddings_m21.py tests/test_m23_contracts.py
```

Para REST/MCP, el operador debe proporcionar el URL realmente desplegado, no un hostname supuesto:
```sh
export CLIPOTECA_WORKER_URL="https://REEMPLAZAR-POR-WORKER-REAL"
curl --fail-with-body -sS "$CLIPOTECA_WORKER_URL/v1/health" | jq '{status,version,checks}'
curl --fail-with-body -sS "$CLIPOTECA_WORKER_URL/v1/sources" | jq '.'
```

Se espera `version: 2.0.0` solo DESPUES de un despliegue autorizado independiente. No desplegar al preparar M24.

## 1. Busqueda real de fuentes en cuatro idiomas

El smoke siguiente hace peticiones de busqueda, NO publica media. Ejecutar en main con secrets opcionales bajo la configuracion auditada, conservando timestamps:
```sh
uv run python scripts/smoke_sources.py --mode all --output data/reports/smoke-v2.json
uv run python scripts/render_matrix_v2.py --smoke-report data/reports/smoke-v2.json --output docs/MATRIZ_FUENTES_V2.md
```

Terminos por idioma usados por `smoke_sources.py`: `es=lluvia`, `en=rain`, `zh=\u4e0b\u96e8`, `ru=\u0434\u043e\u0436\u0434\u044c`. Conservar el reporte real por proveedor/lengua, estados `AUTH_REQUIRED`, `TIMEOUT` o `RATE_LIMITED` cuando correspondan. El renderizador no infiere descargas de una busqueda.

Prueba local de expansion (no verifica busqueda live):
```sh
uv run python -m clipoteca search lluvia --dry-run
uv run python -m clipoteca search rain --dry-run
uv run python -m clipoteca search "$(printf '%b' '\u4e0b\u96e8')" --dry-run
uv run python -m clipoteca search "$(printf '%b' '\u0434\u043e\u0436\u0434\u044c')" --dry-run
```

## 2. Importacion desde fuente sin clave

Seleccionar `library_of_congress` u otra fuente sin clave declarada, primero con una busqueda en el smoke. Si hay video apto y se autoriza publicar, ejecutar con token temporal solo en ambiente controlado:
```sh
export GITHUB_REPOSITORY="riquelmechile/clipoteca-comunitaria"
# GITHUB_TOKEN debe recibirse como secret efimero, nunca pegarlo en el comando.
CLIPOTECA_PERSIST_JOBS=1 uv run --no-sync python -m clipoteca ingest \
  --query "rain" --providers library_of_congress \
  --max-results 1 --max-duration 20 --runner actions
```

Si la respuesta es `no_results`, `search_only`, `AUTH_REQUIRED` o `ip_blocked`, guardar la evidencia y NO afirmar que hubo importacion exitosa. Consultar `data/jobs/{request_id}.json` sin exponer URLs firmadas ni secretos.

## 3. Corte por escena, thumbnail y storyboard

Con material descargado por el pipeline y un `clip_id` real del reporte:
```sh
uv run pytest -q tests/test_scenes_m13.py tests/test_m14_scenes_storyboard.py
```

Verificar en el catalogo real que la duracion esta dentro del limite, que `source_start` y `source_end` coinciden con el segmento elegido, que el thumbnail apunta al punto medio de la escena y que `storage.storyboard_urls` contiene fotogramas WebP publicados. Ejemplo de lectura, usando un ID realmente devuelto por la API:
```sh
export CLIP_ID="clip-REEMPLAZAR-ID-REAL"
curl --fail-with-body -sS "$CLIPOTECA_WORKER_URL/v1/clips/$CLIP_ID" \
  | jq '{id,media,source,storyboard_urls:.storage.storyboard_urls,thumbnail_url:.storage.thumbnail_url}'
```

No pasar una URL externa sin validar directamente a FFmpeg. La normalizacion y el corte se realizan mediante los componentes de seguridad del pipeline y archivos temporales locales.

## 4. Busqueda semantica "aguacero"

Antes de esta etapa, confirmar manifiesto M22 valido, YAML del modo ligero aplicado, modelo texto fijado y flags `SEMANTIC_SEARCH=1`, `CLIPOTECA_QUERY_DISPATCH_MODE=1` solo tras habilitacion segura:
```sh
curl --fail-with-body -sS "$CLIPOTECA_WORKER_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"aguacero","filters":{"limit":10}}' \
  | jq '{semantic,semantic_reason,clips:[.clips[] | {id,title}],scores}'
```

Registrar si `semantic` es `ready`, `pending` o `unavailable`. No afirmar exito antes de ver que una consulta semantica sobre "aguacero" recupera material relevante de "lluvia". No se asume disponibilidad live en los cuatro idiomas.

## 5. Guion de cinco escenas por MCP

El siguiente request JSON-RPC usa la herramienta realmente registrada `resolve_script`; el mismo body de escenas puede enviarse a `POST /v1/script`:
```sh
cat > /tmp/clipoteca-scenas-v2.json <<'JSON'
{"scenes":[
  {"id":"inicio","narration":"Empieza la lluvia.","query":"rain","orientation":"16:9","min_duration":8},
  {"id":"bosque","narration":"Se mueve la camara por el bosque.","query":"forest","orientation":"16:9","min_duration":8},
  {"id":"ciudad","narration":"Aparece la ciudad.","query":"city","orientation":"16:9","min_duration":8},
  {"id":"costa","narration":"Rompen las olas.","query":"ocean waves","orientation":"16:9","min_duration":8},
  {"id":"cierre","narration":"El cielo se abre.","query":"sunrise","orientation":"16:9","min_duration":8}
]}
JSON
jq -n --slurpfile payload /tmp/clipoteca-scenas-v2.json \
  '{jsonrpc:"2.0",method:"tools/call",id:1,params:{name:"resolve_script",arguments:$payload[0]}}' \
  > /tmp/clipoteca-mcp-request.json
curl --fail-with-body -sS "$CLIPOTECA_WORKER_URL/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  --data-binary @/tmp/clipoteca-mcp-request.json | jq '.'
```

La respuesta real debe incluir 5 escenas, `status` por escena (`matched`, `queued` o `missing`), `media.type=video` para matched, URL de video, thumbnail, storyboard, duracion, origen factual y `summary.reused`. Como la cuota import por IP es de 15 segundos, reenviar el mismo guion despues de 15 segundos si hay escenas `rate_limited`. Si `queued`, consultar `GET /v1/jobs/{request_id}` con el ID devuelto. Guardar solo datos no sensibles.

## 6. Evidencia para considerar M24 terminado

- [ ] SHA de main y ejecucion de CI python/worker/real-argos en ese SHA.
- [ ] Reporte smoke real por proveedor e idioma (fecha UTC y estado real).
- [ ] Un clip real de fuente sin clave y su escena, thumbnail y storyboard.
- [ ] Consulta semantica de "aguacero" con vector real y respuesta comprobada.
- [ ] Cinco escenas MCP reales con videos o pendientes/missing declarados sin disfrazarlos.
- [ ] Version 2.0.0 desplegada SOLO tras aprobacion explicita posterior, tag/release si el propietario lo dispone.

El documento es una receta de validacion futura. Esta preparacion no ejecuta smokes, no publica clips y no despliega el Worker.
