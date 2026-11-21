# M22 r2 U+00B7 Busqueda semantica en REST y MCP

## Activacion segura y costo de Workers

El Worker **no** ejecuta Torch ni el modelo CLIP de 135 millones de
parametros. Usa el vector de imagen CLIP 512D ya producido por M21 y los
vectores de consultas precalculados por Python con
`sentence-transformers/clip-ViT-B-32-multilingual-v1`.

- `SEMANTIC_SEARCH=1`: habilita lectura de corpus y coseno; **apagado por
  defecto**. Sin esta variable la busqueda por termino sigue funcionando
  identica a la anterior.
- `SEMANTIC_MAX_CLIPS`: presupuesto de vectores por peticion. **256 por
  defecto**, techo implementado **4096**. Si el manifiesto excede ese
  presupuesto, el Worker no descarga ni decodifica shards: devuelve busqueda
  por termino con `semantic:"unavailable"` y
  `semantic_reason:"cpu_budget_exceeded"`.
- Cloudflare **Workers Free: 10 ms de CPU por invocacion**. Mantener 256 clips
  por defecto como presupuesto conservador, pero medir en produccion para
  ajustar; no constituye garantia de 10 ms. Para miles de clips, usar
  Workers Paid (CPU por peticion configurable, 30 s por defecto, hasta 5
  minutos) y ajustar `SEMANTIC_MAX_CLIPS` **solo despues de medir**.
  Referencias:
  https://developers.cloudflare.com/workers/platform/limits/ y
  https://developers.cloudflare.com/workers/platform/pricing/.

El corpus es un `Float32Array` contiguo con IDs por posicion, cacheado en
memoria del isolate por la version (SHA-256) del manifiesto durante 60 s.
El SHA-256 y la decodificacion de cada shard se hacen unicamente al cargar
o cambiar version; el Worker calcula el coseno sobre float32, no sobre f16
redeserializados en cada busqueda. La Cache API conserva los blobs de
shards sellados (TTL de 1 h), pero el **tail con SHA se cachea hasta 1 h**,
como el manifiesto. El pequeno cache en memoria tiene limites explicitos.

## Almacenamiento sin archivos obsoletos

La version 3 del manifiesto queda en
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
    "path": "shards/tail-<sha256>.f16",
    "ids_path": "ids/tail-<sha256>.json",
    "count": 1,
    "sha256": "<sha256 del binario>",
    "ids_sha256": "<sha256 de la lista>",
    "blob_sha": "<SHA-1 Git del binario>",
    "ids_blob_sha": "<SHA-1 Git de la lista>"
  }
}
```

Cada vector tiene **512 float16 little-endian, 1024 bytes**. Los bloques
sellados contienen **256 vectores** y jamas se reescriben, y el tail
mutable contiene entre 1 y 255: siempre se sobrescriben las rutas fijas
`shards/tail-<sha256>.f16` e `ids/tail-<sha256>.json`. Al reunir 256 vectores se sella
el bloque y se eliminan ambas rutas del tail **en el mismo commit**.

En remoto, `GitHubShards.publish_many` crea los blobs nuevos y un
unico arbol/commit via Git Data API, actualiza `refs/heads/main` con
fast-forward sin force; reintenta un conflicto CAS 409/422 releyendo la
rama. No utiliza Contents API para leer blobs de 256 KiB ni mayores.
En memoria local, `ShardStore.put_many` hace una escritura por lote.
La migracion agrupa los M21 sidecars, con una sola actualizacion remota de
manifiesto por lote, no una por clip:

```bash
uv run python -m clipoteca migrate-embeddings --root data/embeddings
GITHUB_REPOSITORY=owner/repository GITHUB_TOKEN=... \
  uv run python -m clipoteca migrate-embeddings \
  --root data/embeddings --publish
```

Los lectores Python aceptan tanto el nuevo formato como los sidecars
M21, que **no se eliminan**. La escritura M21 conserva su indice y archivo
por clip aun si falla la escritura secundaria M22; deja un aviso
`m22_shard_dual_write_failed:m21_sidecar_persisted` sin deshacer el clip.

El maximo de shards sellados es **500** tanto en Python como en el Worker.
El manifiesto se lee como archivo crudo, no mediante Contents API JSON, y
tiene limite explicito de 1 MB. Los IDs se consultan por archivo de shard.
Cada shard se valida por longitud, SHA-256 y forma de IDs: un shard
corrupto queda **aislado**; si otros sirven, se responde
`semantic:"ready"` con `semantic_reason:"shard_corrupt"`. Un
manifiesto ilegible o demasiado grande devuelve `unavailable` y
`semantic_reason:"manifest_too_large"` o `"manifest_invalid"`.

Los tests de escala verifican **300 y 5000 vectores**: los archivos
presentes en HEAD y bytes del arbol crecen aproximadamente con N, no con
N2. Como en cualquier repositorio Git, el historial de blobs mutables
anteriores puede conservarse hasta que Git haga mantenimiento; este
contrato limita los archivos presentes y evita shards huerfanos en HEAD.

## Consultas, fusion y resultados

El pipeline guarda el vector de cada consulta que procesa ingest en
`data/embeddings/queries/manifest.json` y puede hacerlo
aisladamente con:

```bash
uv sync --frozen --extra embeddings
GITHUB_REPOSITORY=owner/repository GITHUB_TOKEN=... \
  uv run --frozen --extra embeddings python -m clipoteca \
    query-embedding --query "aguacero"
```

La normalizacion es NFKC + Unicode casefold + espacios colapsados,
implementada de manera equivalente en Python y TS: `U+039FU+0394U+039FU+03A3`, sigma final,
Cherokee, U+FEFF, U+001CU+2013U+001F, U+00DF, I, fullwidth, tabs, idiomas
es/en/zh/ru. Se prueba el mismo SHA-256 en ambas suites.

Los candidatos semanticos **requieren coseno U+2265 max(SEMANTIC_MIN_COSINE, mejorU+22120,05), con default absoluto 0,20**. No se incluyen
clips sin relacion, como Sunshine para una consulta aguacero. Se aplican
los filtros del catalogo y se ordena por coseno. Luego se fusiona con los
resultados del buscador lexico mediante *Reciprocal Rank Fusion* con
`k=60`:

\[
 S(c)=\frac{\mathbf{1}_{c\in L}}{60+r_L(c)}
 +\frac{\mathbf{1}_{c\in V,\cos\ge\tau_{dinamica}}}{60+r_V(c)}
\]

Los rankings empiezan en 1; paginacion **despues** de la fusion. Si no
hay coincidencia lexica ni semantica sobre el umbral,
`import_if_missing` continua solicitando una importacion normal, sin
disparar adicionalmente el workflow liviano. Sin corpus, sin encoder o
sin permisos, se conserva el resultado literal anterior.

`POST /v1/search`, JSON Schema, OpenAPI y MCP `search_clips` anaden
`semantic:"ready"|"pending"|"unavailable"`, opcional
`semantic_reason`, y en `ready` un mapa `scores[clip_id]` con RRF.
El objeto `Clip` y `GET /v1/clips` no adquieren campos nuevos.

## Control de abuse de GitHub Actions

**Jamas** se inicia un ingest completo para calcular unicamente un
vector de consulta. El Worker solo envia
`mode=query_embedding` si **ambas** variables estan habilitadas:
`SEMANTIC_SEARCH=1` y `CLIPOTECA_QUERY_DISPATCH_MODE=1` (esta ultima
solo despues de que el operador pegue el YAML de abajo). Sin ese modo,
la consulta sin vector responde `semantic:"unavailable"`, no
`pending`, y no despacha nada.

El payload del dispatch de consulta contiene solo `query` normalizada,
`mode=query_embedding` y
`request_id=sha256(query normalizada)`. Este ID aprovecha el
`concurrency.group` ya existente en `ingest.yml`.

Controles de coste: marcador persistente Cache API por SHA de consulta
**10 minutos**, incluido si GitHub devolvio 422 u otro fallo; cuota
separada `query-embed` **por IP** como los imports; **20 intentos
por hora** en el marcador global de Cache API, contados **antes** de
enviar HTTP y tambien si la solicitud falla. Los Maps del isolate son
acotados. Tras 204 responde `pending`; sin 204 responde
`unavailable`. El mismo limite cubre el endpoint opcional
`QUERY_ENCODER_URL`; su vector valido se cachea 10 minutos.

**Limitacion tecnica:** Cloudflare Cache API no replica entre centros de
datos y carece de CAS atomico; la cuota global Cache API es
**best-effort por centro de datos**, no un limite mundial fuerte bajo
concurrencia. Un limite estricto distribuido requeriria un contador
transaccional externo (fuera del alcance y sin servicio nuevo en este
PR). Referencia: https://developers.cloudflare.com/workers/runtime-apis/cache/.

`QUERY_ENCODER_URL` es opcional y solo para un endpoint **HTTPS
configurado por el operador**, sin redirecciones, IP literales
(v4, 0.0.0.0/8, 100.64/10, v6), localhost ni hosts internos.
Lee la respuesta **por streaming con tope de 16 KiB**, aunque el
servidor omita Content-Length. Acepta unicamente
`{"vector":[512 numeros finitos]}`, renormaliza y cachea el resultado.

## YAML exacto que debe aplicar el operador (NO modificado aqui)

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

**2.** En el job existente `ingest`, agregar el paso de cache **despues
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

**4.** Anadir este paso despues de la cache (no ejecuta busqueda, MP4,
traduccion ni ffmpeg):

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

**5.** Aplicar YAML mediante revision independiente; comprobar el
workflow de prueba y **solo entonces** configurar
`CLIPOTECA_QUERY_DISPATCH_MODE=1` junto con
`SEMANTIC_SEARCH=1`. Hasta entonces, el Worker funciona con
busqueda lexica sin agotar Actions.

## Tests reproducibles

```bash
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
(cd worker && npm ci && npm run typecheck && npm test)
```

Los tests offline tienen un encoder falso determinista (no pesos reales):
`aguacero` encuentra `lluvia`, `rain`, `U+4E0BU+96E8` y
`U+0434U+043EU+0436U+0434U+044C` aunque no coincida el termino; Sunshine queda excluido por
coseno. Cubren 300/5000 inserciones y bytes/archivos, CAS remoto,
manifest grande, shard corrupto aislado, presupuesto, cache, cuotas
por consulta/global/IP, 422, URL encoder y contratos REST/MCP.
El rendimiento del modelo real y los tiempos en hardware no se fingen;
deben medirse fuera del CI offline.

## M22 r3 U+00B7 Correcciones obligatorias (prevalecen sobre r2)

**Umbral medido.** En 100 imagenes Flickr30k con clip-ViT-B-32 +
clip-ViT-B-32-multilingual-v1, pares correctos: mediana 0,308 y p10 0,256;
consultas de una palabra, maximo correcto 0,27; no relacionados:
mediana 0,17 y p90 0,225. El antiguo umbral fijo 0,30 descartaba
muchos aciertos. `SEMANTIC_MIN_COSINE` tiene valor predeterminado **0,20**.
Se conservan como maximo **50** candidatos, ordenados por coseno, que
cumplan `cos >= max(tau_abs, cos_del_mejor - 0.05)`. Si ninguno cumple,
la busqueda lexica se conserva con `semantic: unavailable` y
`semantic_reason: no_semantic_match`. Se mantiene RRF k=60.

**Despacho concurrente.** Se reserva sincronicamente el digest, la IP y una
plaza del contador por isolate **antes del primer await**. El marcador
persistente de 10 minutos solo se escribe al reservar un intento real
de HTTP; una denegacion por cuota IP o por tope horario no penaliza
a otros usuarios por la misma consulta. Los 20 intentos/hora continuan
siendo un limite best-effort por colo de Cache API, no atomico mundial.
El workflow liviano debe usar un grupo global de concurrencia y 10 min
de timeout para evitar 50 ejecuciones simultaneas entre isolates.

**Consultas con almacenamiento finito.** Los vectores ya no generan
`queries/<digest>.json` nuevos en cada publicacion. El formato r3
`queries/manifest.json` contiene bloques sellados de 64 vectores f16,
archivos separados de IDs SHA-256 y un tail SHA-inmutable.
El maximo es **512 consultas activas**, con poda FIFO de los bloques
mas antiguos (aproximacion acotada a las menos usadas).
GitHubQueries.publish_many realiza un unico Git Data API commit por job
y reintenta CAS 409/422 hasta cinco veces. El Worker lee este formato
y conserva fallback de lectura de los JSON r2 existentes durante la migracion.
Se debe retirar la cache JSON r2 heredada en una migracion supervisada
tras verificar que los nuevos shards cubren las consultas necesarias.

**Migracion M21/M22.** Mantener `CLIPOTECA_M21_SIDECARS=1` (valor
predeterminado) hasta terminar `clipoteca migrate-embeddings --publish`
y comprobar el manifiesto remoto. Despues, activar
`CLIPOTECA_M21_SIDECARS=0` en el runner para dejar de generar
archivos M21 por clip e `index.json`; el Worker consume M22.
Las escrituras local M21 y M22 estan aisladas entre si y los errores
se dejan en `source_warnings`. La publicacion remota de M22 usa una
unica llamada publish_many por job, sin un commit por clip.

**Integridad y CPU.** Todos los tails M22 usan rutas con hash
(`shards/tail-<sha256>.f16` y `ids/tail-<sha256>.json`). El antiguo
tail solo se elimina despues de cambiar el manifiesto local; remoto
se actualiza y se elimina en el mismo Git commit. No hay lecturas
desfasadas por ruta fija de raw.githubusercontent.com.
El Worker construye una tabla f16U+2192f32 de 65.536 entradas una sola vez
por isolate, busca primero el vector de la consulta y reutiliza el corpus
f32 por version del manifiesto durante **20 minutos**.
`SEMANTIC_MAX_CLIPS` sigue siendo **256** por defecto, hasta 4096
explicitos si el plan lo permite. Workers Free da 10 ms de CPU por
peticion, por lo que incluso 256 debe medirse; un Error 1102 por
exceso de CPU detiene el isolate y **no puede capturarse con try/catch**.
Para bibliotecas mayores usar Workers Paid y medir antes de subir el limite.

**Unicode y salida.** La tabla `worker/src/casefold-map.json` se genero
con `unicodedata` Unicode **17.0.0** de Python 3.15 y contiene
el casefold completo, no un simple lowercase JS. El codigo tambien
trata U+0085 y separadores U+001CU+2013U+001F como espacios. Tests cruzados
cubren griego con iota subscripta, j, U+0390/U+03B0, cirilico, Cherokee y
combinaciones. La normalizacion NFKC usa el runtime Unicode de cada host:
versiones futuras deberan actualizar la tabla y revalidar los fixtures.
`docs/openapi.yaml` se analiza con PyYAML SafeLoader estricto que
rechaza claves YAML duplicadas. `semantic_reason` enum incluye
`no_semantic_match`, `query_vector_invalid` y `unsafe_query_encoder_url`.

**QUERY_ENCODER_URL.** Se elimina el punto terminal del host antes de
evaluar restricciones, se bloquean dominios `.lan`, `.corp`,
`.home.arpa`, `.nip.io` y `.sslip.io`, mas todos los IP literales.
`QUERY_ENCODER_HOSTS` permite una lista explicita de hosts HTTPS
autorizados, separados por coma. La respuesta sigue limitada a 16 KiB
por lectura en streaming y al throttle de consulta/IP.

### YAML exacto que debe aplicar el operador; NO modificado en este PR

En el workflow, reemplazar la seccion de concurrencia del modo
`workflow_dispatch` por la siguiente logica, manteniendo
`cancel-in-progress: false`:
```yaml
concurrency:
  group: ${{ inputs.mode == 'query_embedding' && 'clipoteca-query-embedding' || format('clipoteca-ingest-{0}', inputs.request_id || github.run_id) }}
  cancel-in-progress: false
```

En el job `ingest`, agregar o ajustar:
```yaml
    timeout-minutes: ${{ inputs.mode == 'query_embedding' && 10 || 60 }}
```

Conservar el input `mode` y las condiciones `if: inputs.mode !=
'query_embedding'` para ffmpeg, traduccion, ingesta y publicacion de jobs
ya descritas arriba. Para el paso liviano, usar estas variables y
revisiones fijas verificables del repositorio Hugging Face:
```yaml
      - name: Cache semantic query vector (M22)
        if: inputs.mode == 'query_embedding'
        env:
          GITHUB_REPOSITORY: ${{ github.repository }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          CLIPOTECA_QUERY: ${{ inputs.query }}
          CLIPOTECA_TEXT_MODEL_REVISION: e64755877a7a03686463dbdf668a080bb78ff0fa
          CLIPOTECA_IMAGE_MODEL_REVISION: afe976e3f4ea04b633edb0089a7e5088ddbe9212
          UV_CACHE_DIR: .cache/uv
          HF_HOME: .cache/huggingface
          HF_HUB_CACHE: .cache/huggingface/hub
        run: |
          uv sync --frozen --extra embeddings
          uv run --frozen --extra embeddings python -m clipoteca query-embedding --query="$CLIPOTECA_QUERY"
```

El parametro `--query="$CLIPOTECA_QUERY"` acepta incluso una consulta
que empiece con `-`. No se edito ningun archivo `.github/`.

**Argos Issue 26:** los asserts de frases exactas se verifican con un
traductor determinista inyectado. El test real conserva modelos
verificados por checksum, reintentos acotados (incluida la primera
traduccion de titulo), invariantes bilingues y rechazo fail-closed de
salidas repetidas o desproporcionadas. Limita hilos CPU cuando procede.


Python 3.13 (Unicode 15.1) and Worker Unicode 17.0 are compared against
the same 671-case digest fixture. A U+1C89 mapping is explicitly bridged
in Python to match the later casefold table; no raw Unicode literals.
