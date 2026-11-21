# M22 · Búsqueda semántica en API y MCP

## Arquitectura sin modelos dentro del Worker

El pipeline Python usa exactamente el encoder de texto multilingüe de M21
(`sentence-transformers/clip-ViT-B-32-multilingual-v1`) y el encoder de
imagen CLIP ViT-B/32 en el mismo espacio de **512 dimensiones**. El Worker de
Cloudflare no ejecuta Torch ni descarga un modelo de 135 millones de parámetros.

La consulta se normaliza aplicando Unicode NFKC, minúsculas y colapsando
espacios, por ejemplo `"  AGUACERO  "` → `"aguacero"`. La caché queda en
`data/embeddings/queries/<sha256(normalized_query)>.json` con un vector
unitario codificado como base64 de 1024 bytes float16 little-endian, sin texto
de consulta, secretos ni URLs firmadas. El pipeline lo calcula al iniciar
ingest, aun si no aparecen videos. El comando liviano disponible es:

```bash
uv sync --frozen --extra embeddings
GITHUB_REPOSITORY=owner/repository GITHUB_TOKEN=... \
  uv run --frozen --extra embeddings python -m clipoteca query-embedding --query aguacero
```

El comando sólo calcula y persiste el vector, sin buscar ni publicar clips.

## Shards y migración M21 → M22

Los archivos `data/embeddings/clip-000001.f16` y `index.json` de M21
continúan disponibles durante la transición. Los nuevos vectores se guardan
**también** en shards y el Worker prefiere el manifiesto de M22:

```json
{
  "version": 2,
  "dimension": 512,
  "dtype": "float16-le",
  "shard_capacity": 4096,
  "shards": [
    {
      "path": "shards/shard-000000-<sha256>.f16",
      "sha256": "<sha256>",
      "blob_sha": "<SHA-1 Git blob>",
      "count": 2,
      "ids": ["clip-000101", "clip-000102"]
    }
  ]
}
```

Cada shard contiene los vectores **concatenados**, en el mismo orden que
`ids`: 512 × 2 = **1024 bytes por clip**. Su capacidad máxima es **4096
vectores (4.194.304 bytes = 4 MiB)**. La escritura concatena al último shard
si hay espacio, conserva cada versión como un archivo inmutable con checksum
en el nombre y actualiza `manifest.json` mediante optimistic CAS por SHA
de GitHub. Si alguien publica simultáneamente, vuelve a leer el manifiesto y
reintenta. El backend lee los shards grandes desde **Git Data API
`/git/blobs/{sha}`**, nunca usando el JSON de Contents API para un archivo
superior a 1 MiB. El Worker descarga directamente los blobs públicos de
`raw.githubusercontent.com`, con máximo de bytes, checksum SHA-256 y caché
Cache API + memoria del isolate. El manifiesto se valida por campo, orden,
longitud y unicidad de IDs.

Migrar los registros M21 ya existentes, sin borrar sidecars antiguos:

```bash
uv run python -m clipoteca migrate-embeddings --root data/embeddings
# Con checkout que contiene sidecars existentes y permisos de publicación:
GITHUB_REPOSITORY=owner/repository GITHUB_TOKEN=... \
  uv run python -m clipoteca migrate-embeddings --root data/embeddings --publish
```

El lector Python resuelve tanto shards como sidecars M21. El Worker también
ofrece compatibilidad M21 cuando no existe manifiesto, limitada a **35
vectores por request** para no agotar los subrequests de Cloudflare: antes de
exceder ese umbral debe ejecutarse la migración a shards. La migración es
idempotente; no altera `clips.json` ni el formato float16.

## Puntuaciones, filtros y resultados

Para cada consulta con vector:

1. La búsqueda por término existente conserva sus tokens, Unicode, filtros
   (duración/orientación/fuente/idioma/audio) y orden original.
2. El Worker decodifica cada float16 de 16 bits *manualmente* (sin
   `Float16Array`), valida finitud y norma, renormaliza en float32 y
   calcula coseno con el vector de consulta. Se ordena todo el catálogo con
   vector por similitud descendente, incluso si no contiene el término.
3. Fusiona rankings por **Reciprocal Rank Fusion, k=60**:

   \[
   S(c)=\frac{\mathbf{1}_{c\in L}}{60+r_L(c)}
       +\frac{\mathbf{1}_{c\in V}}{60+r_V(c)}
   \]

   Los ranks empiezan en 1. Los clips presentes sólo en un ranking también
   participan. El desempate es determinista por posición y luego ID.
4. Se aplica el cursor y límite **después de fusionar**. `scores` es un
   diccionario de IDs seleccionados a `S(c)`, no se agrega un campo al objeto
   `Clip` que pudiera romper clientes anteriores.

Los contratos `POST /v1/search`, tool `search_clips`,
`schemas/search.schema.json`, `docs/openapi.yaml` y el esquema de salida
MCP añaden `semantic: "ready" | "pending" | "unavailable"`; en estado
`ready` incluyen `scores`. Si faltan vectores/shards o falla un endpoint,
continúa exactamente la búsqueda literal sin lanzar un error de búsqueda.
El endpoint `GET /v1/clips` y las otras herramientas MCP no cambian.

## Consultas nuevas: dispatch y respuesta inmediata

- **Caché presente:** respuesta inmediata `semantic:"ready"` usando el
  vector calculado en el pipeline y el manifiesto.
- **Sin vector pero con corpus y token de dispatch:** respuesta literal con
  `semantic:"pending"` y enqueue con throttling de 30 segundos por query.
  Sin el patch YAML, se reutiliza `dispatchIngest` con las entradas actuales,
  evitando enviar un input desconocido o bloquear a los clientes. El resultado
  permanece pendiente mientras el runner no instale el extra de modelos.
- **Con el YAML actualizado:** configurar
  `CLIPOTECA_QUERY_DISPATCH_MODE=1` en el Worker; `ingest.yml` recibe
  `mode=query_embedding`, ejecuta `query-embedding` sin búsqueda ni MP4,
  y la **siguiente consulta** puede responder `ready` después del commit
  de la caché (respetando TTL de 10 segundos).
- **Sin token, corpus o manifest, o con error:** `semantic:"unavailable"`
  y ranking literal sin alterar.
- **Opcional** `QUERY_ENCODER_URL`: endpoint HTTPS administrado por el
  operador, sin redirecciones, credenciales en URL, puertos arbitrarios,
  hosts internos, localhost ni IP literales. Recibe POST
  `{"query":"aguacero"}` y devuelve
  `{"vector":[512 números finitos]}`. Se comprueba longitud, finitud y
  normalización antes de comparar. No se instala ningún servicio de pago.

### Instrucciones YAML para el mantenedor (NO aplicado en este PR)

Agregar **bajo** `on.workflow_dispatch.inputs` en
`.github/workflows/ingest.yml` exactamente:

```yaml
      mode:
        description: Execution mode
        required: false
        type: choice
        default: ingest
        options:
          - ingest
          - query_embedding
```

Añadir `if: inputs.mode != 'query_embedding'` en los pasos
`Prepare verified translation resources`,
`Publish credential-presence source snapshot` e `Ingest` existentes.
Añadir un paso al mismo job `ingest` (después de setup-uv):

```yaml
      - name: Cache semantic query vector (M22)
        if: inputs.mode == 'query_embedding'
        env:
          GITHUB_REPOSITORY: ${{ github.repository }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          CLIPOTECA_QUERY: ${{ inputs.query }}
        run: |
          uv sync --frozen --extra embeddings
          uv run --frozen --extra embeddings python -m clipoteca query-embedding --query "$CLIPOTECA_QUERY"
```

Las ramas estándar de ingest siguen ejecutándose con su configuración
existente; el modo nuevo sólo añade cálculo liviano y persistencia. **No
se cambió ningún archivo de `.github/`**, ni se activó
`CLIPOTECA_QUERY_DISPATCH_MODE=1` desde este PR. Hasta que el mantenedor
aplique este bloque, la búsqueda literal y el modo pending funcionan sin
fallar, y la caché remota se completa automáticamente sólo en runners donde
el extra esté instalado.

## Pruebas reproducibles

```bash
uv sync --frozen --extra dev
uv run pytest -q tests/test_shards_m22.py tests/test_ingest_m22.py tests/test_providers_m19_r3.py
uv run ruff check .
uv run ruff format --check .
(cd worker && npm ci && npm run typecheck && npm test)
```

`tests/fixtures/m22/semantic_vectors.json` contiene vectores float16
calculados con encoder **falso determinista**. La consulta **aguacero** no
coincide léxicamente con el clip **lluvia**, pero su coseno lo coloca primero;
se comprueba el mismo comportamiento para lluvia, rain, 下雨 y дождь, sin
red ni pesos neuronales. Los tests Worker cubren media precisión, coseno,
RRF, fallbacks, HTTP de cache, dispatch y legado M21. Los tests Python
cubren manifiesto, 409 CAS/reintento, migración, y cliente de embedding
reutilizado/cerrado en ingest persistente.

**Limitación de pruebas:** el encoder de lenguaje real y la latencia de
hardware están fuera del CI habitual y conservan sus pruebas opcionales
M21. Un test con encoder falso demuestra el contrato y no sustituye
medición live de recuperación semántica.
