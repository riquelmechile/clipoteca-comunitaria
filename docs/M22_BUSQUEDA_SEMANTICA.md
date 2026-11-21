# M22 r2 · Búsqueda semántica en REST y MCP

## Activación segura y costo de Workers

El Worker **no** ejecuta Torch ni el modelo CLIP de 135 millones de
parámetros. Usa el vector de imagen CLIP 512D ya producido por M21 y los
vectores de consultas precalculados por Python con
`sentence-transformers/clip-ViT-B-32-multilingual-v1`.

- `SEMANTIC_SEARCH=1`: habilita lectura de corpus y coseno; **apagado por
  defecto**. Sin esta variable la búsqueda por término sigue funcionando
  idéntica a la anterior.
- `SEMANTIC_MAX_CLIPS`: presupuesto de vectores por petición. **256 por
  defecto**, techo implementado **4096**. Si el manifiesto excede ese
  presupuesto, el Worker no descarga ni decodifica shards: devuelve búsqueda
  por término con `semantic:"unavailable"` y
  `semantic_reason:"cpu_budget_exceeded"`.
- Cloudflare **Workers Free: 10 ms de CPU por invocación**. Mantener 256 clips
  por defecto como presupuesto conservador, pero medir en producción para
  ajustar; no constituye garantía de 10 ms. Para miles de clips, usar
  Workers Paid (CPU por petición configurable, 30 s por defecto, hasta 5
  minutos) y ajustar `SEMANTIC_MAX_CLIPS` **solo después de medir**.
  Referencias:
  https://developers.cloudflare.com/workers/platform/limits/ y
  https://developers.cloudflare.com/workers/platform/pricing/.

El corpus es un `Float32Array` contiguo con IDs por posición, cacheado en
memoria del isolate por la versión (SHA-256) del manifiesto durante 60 s.
El SHA-256 y la decodificación de cada shard se hacen únicamente al cargar
o cambiar versión; el Worker calcula el coseno sobre float32, no sobre f16
redeserializados en cada búsqueda. La Cache API conserva los blobs de
shards sellados (TTL de 1 h), pero el **tail mutable se cachea solo 10 s**,
como el manifiesto. El pequeño caché en memoria tiene límites explícitos.

## Almacenamiento sin archivos obsoletos

La versión 3 del manifiesto queda en
`data/embeddings/manifest.json` y **no almacena listas de IDs**. Cada
shard sellado tiene su binario y un archivo de IDs correspondiente; la
lista de IDs queda fuera del manifiesto:

```json
{
  "version": 3,
  "dimension": 512,
  "dtype": "float16-le",
  "shard_capacity": 4096,
  "seal_capacity": 256,
  "shards": [
    {
      "path": "shards/shard-000000-<sha256>.f16",
      "ids_path": "ids/shard-000000-<sha256>.json",
      "count": 256,
      "sha256": "<sha256 del binario>",
      "ids_sha256": "<sha256 de la lista>",
      "blob_sha": "<SHA-1 Git del binario>",
      "ids_blob_sha": "<SHA-1 Git de la lista>"
    }
  ],
  "tail": {
    "path": "shards/tail.f16",
    "ids_path": "ids/tail.json",
    "count": 1,
    "sha256": "<sha256 del binario>",
    "ids_sha256": "<sha256 de la lista>",
    "blob_sha": "<SHA-1 Git del binario>",
    "ids_blob_sha": "<SHA-1 Git de la lista>"
  }
}
```

Cada vector tiene **512 float16 little-endian, 1024 bytes**. Los bloques
sellados contienen **256 vectores** y jamás se reescriben, y el tail
mutable contiene entre 1 y 255: siempre se sobrescriben las rutas fijas
`shards/tail.f16` e `ids/tail.json`. Al reunir 256 vectores se sella
el bloque y se eliminan ambas rutas del tail **en el mismo commit**.

En remoto, `GitHubShards.publish_many` crea los blobs nuevos y un
único árbol/commit vía Git Data API, actualiza `refs/heads/main` con
fast-forward sin force; reintenta un conflicto CAS 409/422 releyendo la
rama. No utiliza Contents API para leer blobs de 256 KiB ni mayores.
En memoria local, `ShardStore.put_many` hace una escritura por lote.
La migración agrupa los M21 sidecars, con una sola actualización remota de
manifiesto por lote, no una por clip:

```bash
uv run python -m clipoteca migrate-embeddings --root data/embeddings
GITHUB_REPOSITORY=owner/repository GITHUB_TOKEN=... \
  uv run python -m clipoteca migrate-embeddings \
  --root data/embeddings --publish
```

Los lectores Python aceptan tanto el nuevo formato como los sidecars
M21, que **no se eliminan**. La escritura M21 conserva su índice y archivo
por clip aun si falla la escritura secundaria M22; deja un aviso
`m22_shard_dual_write_failed:m21_sidecar_persisted` sin deshacer el clip.

El máximo de shards sellados es **500** tanto en Python como en el Worker.
El manifiesto se lee como archivo crudo, no mediante Contents API JSON, y
tiene límite explícito de 1 MB. Los IDs se consultan por archivo de shard.
Cada shard se valida por longitud, SHA-256 y forma de IDs: un shard
corrupto queda **aislado**; si otros sirven, se responde
`semantic:"ready"` con `semantic_reason:"shard_corrupt"`. Un
manifiesto ilegible o demasiado grande devuelve `unavailable` y
`semantic_reason:"manifest_too_large"` o `"manifest_invalid"`.

Los tests de escala verifican **300 y 5000 vectores**: los archivos
presentes en HEAD y bytes del árbol crecen aproximadamente con N, no con
N². Como en cualquier repositorio Git, el historial de blobs mutables
anteriores puede conservarse hasta que Git haga mantenimiento; este
contrato limita los archivos presentes y evita shards huérfanos en HEAD.

## Consultas, fusión y resultados

El pipeline guarda el vector de cada consulta que procesa ingest en
`data/embeddings/queries/<sha256(query_normalizada)>.json` y puede hacerlo
aisladamente con:

```bash
uv sync --frozen --extra embeddings
GITHUB_REPOSITORY=owner/repository GITHUB_TOKEN=... \
  uv run --frozen --extra embeddings python -m clipoteca \
    query-embedding --query "aguacero"
```

La normalización es NFKC + Unicode casefold + espacios colapsados,
implementada de manera equivalente en Python y TS: `ΟΔΟΣ`, sigma final,
Cherokee, U+FEFF, U+001C–U+001F, ß, İ, fullwidth, tabs, idiomas
es/en/zh/ru. Se prueba el mismo SHA-256 en ambas suites.

Los candidatos semánticos **requieren coseno ≥ τ = 0,30**. No se incluyen
clips sin relación, como Sunshine para una consulta aguacero. Se aplican
los filtros del catálogo y se ordena por coseno. Luego se fusiona con los
resultados del buscador léxico mediante *Reciprocal Rank Fusion* con
`k=60`:

\[
 S(c)=\frac{\mathbf{1}_{c\in L}}{60+r_L(c)}
 +\frac{\mathbf{1}_{c\in V,\cos\ge0.30}}{60+r_V(c)}
\]

Los rankings empiezan en 1; paginación **después** de la fusión. Si no
hay coincidencia léxica ni semántica sobre el umbral,
`import_if_missing` continúa solicitando una importación normal, sin
disparar adicionalmente el workflow liviano. Sin corpus, sin encoder o
sin permisos, se conserva el resultado literal anterior.

`POST /v1/search`, JSON Schema, OpenAPI y MCP `search_clips` añaden
`semantic:"ready"|"pending"|"unavailable"`, opcional
`semantic_reason`, y en `ready` un mapa `scores[clip_id]` con RRF.
El objeto `Clip` y `GET /v1/clips` no adquieren campos nuevos.

## Control de abuse de GitHub Actions

**Jamás** se inicia un ingest completo para calcular únicamente un
vector de consulta. El Worker sólo envía
`mode=query_embedding` si **ambas** variables están habilitadas:
`SEMANTIC_SEARCH=1` y `CLIPOTECA_QUERY_DISPATCH_MODE=1` (esta última
solo después de que el operador pegue el YAML de abajo). Sin ese modo,
la consulta sin vector responde `semantic:"unavailable"`, no
`pending`, y no despacha nada.

El payload del dispatch de consulta contiene solo `query` normalizada,
`mode=query_embedding` y
`request_id=sha256(query normalizada)`. Este ID aprovecha el
`concurrency.group` ya existente en `ingest.yml`.

Controles de coste: marcador persistente Cache API por SHA de consulta
**10 minutos**, incluido si GitHub devolvió 422 u otro fallo; cuota
separada `query-embed` **por IP** como los imports; **20 intentos
por hora** en el marcador global de Cache API, contados **antes** de
enviar HTTP y también si la solicitud falla. Los Maps del isolate son
acotados. Tras 204 responde `pending`; sin 204 responde
`unavailable`. El mismo límite cubre el endpoint opcional
`QUERY_ENCODER_URL`; su vector válido se cachea 10 minutos.

**Limitación técnica:** Cloudflare Cache API no replica entre centros de
datos y carece de CAS atómico; la cuota global Cache API es
**best-effort por centro de datos**, no un límite mundial fuerte bajo
concurrencia. Un límite estricto distribuido requeriría un contador
transaccional externo (fuera del alcance y sin servicio nuevo en este
PR). Referencia: https://developers.cloudflare.com/workers/runtime-apis/cache/.

`QUERY_ENCODER_URL` es opcional y solo para un endpoint **HTTPS
configurado por el operador**, sin redirecciones, IP literales
(v4, 0.0.0.0/8, 100.64/10, v6), localhost ni hosts internos.
Lee la respuesta **por streaming con tope de 16 KiB**, aunque el
servidor omita Content-Length. Acepta únicamente
`{"vector":[512 números finitos]}`, renormaliza y cachea el resultado.

## YAML exacto que debe aplicar el operador (NO modificado aquí)

**1.** Bajo `on.workflow_dispatch.inputs` en
`.github/workflows/ingest.yml`:

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

**2.** En el job existente `ingest`, agregar el paso de caché **después
de setup-uv**. Se reutiliza el pin que ya usa el repositorio para
`actions/cache`:

```yaml
      - name: Cache query encoder dependencies and HuggingFace weights
        if: inputs.mode == 'query_embedding'
        uses: actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830 # v4
        with:
          path: |
            .cache/uv
            .cache/huggingface
          key: query-embedding-v1-${{ runner.os }}-${{ hashFiles('uv.lock') }}
```

**3.** Agregar `if: inputs.mode != 'query_embedding'` a TODOS los
pasos actuales de ingesta: el `run: sudo apt-get update ... ffmpeg`,
`Prepare verified translation resources`,
`Publish credential-presence source snapshot` e `Ingest`.
En el paso `Upload job records and metrics` reemplazar su `if:
always()` por `if: always() && inputs.mode != 'query_embedding'`.
El checkout y setup-uv se mantienen para ambos modos.

**4.** Añadir este paso después de la caché (no ejecuta búsqueda, MP4,
traducción ni ffmpeg):

```yaml
      - name: Cache semantic query vector (M22)
        if: inputs.mode == 'query_embedding'
        env:
          GITHUB_REPOSITORY: ${{ github.repository }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          CLIPOTECA_QUERY: ${{ inputs.query }}
          UV_CACHE_DIR: .cache/uv
          HF_HOME: .cache/huggingface
          HF_HUB_CACHE: .cache/huggingface/hub
        run: |
          uv sync --frozen --extra embeddings
          uv run --frozen --extra embeddings python -m clipoteca \
            query-embedding --query "$CLIPOTECA_QUERY"
```

**5.** Aplicar YAML mediante revisión independiente; comprobar el
workflow de prueba y **solo entonces** configurar
`CLIPOTECA_QUERY_DISPATCH_MODE=1` junto con
`SEMANTIC_SEARCH=1`. Hasta entonces, el Worker funciona con
búsqueda léxica sin agotar Actions.

## Tests reproducibles

```bash
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
(cd worker && npm ci && npm run typecheck && npm test)
```

Los tests offline tienen un encoder falso determinista (no pesos reales):
`aguacero` encuentra `lluvia`, `rain`, `下雨` y
`дождь` aunque no coincida el término; Sunshine queda excluido por
coseno. Cubren 300/5000 inserciones y bytes/archivos, CAS remoto,
manifest grande, shard corrupto aislado, presupuesto, caché, cuotas
por consulta/global/IP, 422, URL encoder y contratos REST/MCP.
El rendimiento del modelo real y los tiempos en hardware no se fingen;
deben medirse fuera del CI offline.
