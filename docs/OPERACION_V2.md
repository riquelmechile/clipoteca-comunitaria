# Clipoteca v2: operacion y preparacion 2.0.0

**Estado:** codigo y contratos preparados; NO es evidencia de smoke real ni autorizacion para desplegar. En este PR no se modifican archivos `.github/`. Todos los bloques YAML de M19/M20/M22 pendientes se conservan aqui para que el operador los aplique y revise en una ejecucion separada. M24 no esta cerrado hasta ejecutar smokes y demo en main.

## 1. Requisitos y fuentes de verdad

- GitHub Actions para tareas normales, con `GITHUB_TOKEN` temporal y `GITHUB_REPOSITORY`.
- Python >=3.13, `uv sync --frozen`, ffmpeg, modelos Argos con pins existentes; Node >=22 para el Worker.
- `data/sources.json` y `pipeline/clipoteca/sources.py` son la matriz declarativa; no equivalen a disponibilidad real. `Bilibili` queda `SUPPORTED_SEARCH_ONLY` hasta un smoke en main.
- `data/argos-pins.json` permanece sin cambios. No imprimir secretos ni URLs firmadas.
- Hitos M11-M23 fusionados en main, release 2.0.0 **preparado**, sin tag, merge nuevo, despliegue ni smoke live desde este trabajo.

## 2. Flags y credenciales

| Variable | Default | Accion y cautela |
|---|---|---|
| `SEMANTIC_SEARCH` | apagado | `1` tras migrar embeddings y comprobar corpus |
| `CLIPOTECA_QUERY_DISPATCH_MODE` | apagado | `1` solo tras pegar YAML de modo liviano en ingest.yml |
| `SEMANTIC_MIN_COSINE` | `0.20` | umbral absoluto, con ventana relativa a mejor coseno 0.05 |
| `SEMANTIC_MAX_CLIPS` | `256` | maximo declarado 4096, medir CPU; Workers Paid para catalogos grandes |
| `QUERY_ENCODER_URL` | no definido | servicio HTTPS opcional; se comprueban host y longitud |
| `QUERY_ENCODER_HOSTS` | no definido | hosts permitidos separados por coma, sin IP ni dominios internos |
| `CLIPOTECA_M21_SIDECARS` | `1` | pasar a `0` solo despues de verificar migracion M21 a M22 |
| `CLIPOTECA_RUNNER` | `actions` | `actions` o `self-hosted`; archivo `data/runner-routing.json` vacio por defecto |
| `DOUYIN_COOKIE` | vacio | sesion opcional, solo secrets en Actions |
| `TIKTOK_COOKIE` | vacio | sesion opcional, solo secrets en Actions |
| `BILIBILI_COOKIE` | vacio | opcional para WBI; descarga NO habilitada hasta smoke |
| `BILIBILI_USER_AGENT` | sin definir | por defecto UA propio `USER_AGENT`; UA de navegador solo si operador lo decide |
| `EUROPEANA_API_KEY` | vacio | M18 opcional, proveedor AUTH_REQUIRED sin clave |
| `COVERR_API_KEY` | vacio | M18 opcional, proveedor AUTH_REQUIRED sin clave |
| `NARA_API_KEY` | vacio | M18 opcional, proveedor AUTH_REQUIRED sin clave |
| `DPLA_API_KEY` | vacio | M18 opcional, proveedor AUTH_REQUIRED sin clave |
| `SMITHSONIAN_API_KEY` | vacio | M18 opcional, proveedor AUTH_REQUIRED sin clave |
| `PEXELS_API_KEY`/`PIXABAY_API_KEY` | vacio | proveedores existentes, requieren clave |
| VK_ACCESS_TOKEN | vacio | token propio opcional para VK Video |
| YOUTUBE_API_KEY | vacio | credencial de YouTube, ausente = AUTH_REQUIRED |
| INSTAGRAM_ACCESS_TOKEN | vacio | cuenta profesional autorizada |
| INSTAGRAM_USER_ID | no definido | vars en Actions, vinculado a token Instagram |
| SNAPCHAT_ACCESS_TOKEN | vacio | cuenta autorizada |
| SNAPCHAT_PROFILE_ID | no definido | vars en Actions, vinculado a token Snapchat |
| NASA_API_KEY | DEMO_KEY | valor por defecto real de NASA (cuota limitada) |
| PEERTUBE_ALLOWED_INSTANCES | pt.na4.eu,tilvids.com,diode.zone,framatube.org | allowlist por defecto |
| DOUYIN_ACCESS_TOKEN | vacio | token legado opcional; web guest no requiere |
| TIKTOK_ACCESS_TOKEN | vacio | token legado opcional; web guest no requiere |
| CLIPOTECA_IMAGE_MODEL_REVISION | None (sin definir) | pin opcional de CLIP imagen, fijar en jobs reproducibles |
| CLIPOTECA_TEXT_MODEL_REVISION | None (sin definir) | pin opcional de CLIP texto |
| CLIPOTECA_GITHUB_REPO | riquelmechile/clipoteca-comunitaria | repositorio Worker por defecto |
| CLIPOTECA_DISPATCH_TOKEN | no definido | token para dispatch de Actions |
| CLIPOTECA_WRITE_TOKEN | no definido | bearer para import REST |
| CLIPOTECA_ISSUE_TOKEN | no definido | token para aportes Issues |
| MCP_ALLOWED_HOSTS | host de request | por defecto solo host del Worker |
| MCP_ALLOWED_ORIGINS | lista vacia | allowlist de origins, por defecto ninguno externo |
| CATALOG_URL | default raw.githubusercontent.com/.../main/data/clips.json | opcional; override de URL para el Worker |
| CATALOG_JOBS_URL | default raw.githubusercontent.com/.../main/data/jobs/ | opcional; override del prefijo de jobs |
| CATALOG_TTL_SECONDS | 10 (max 30) | cache catalogo, 0 desactiva |
| `CLIPOTECA_PERSIST_JOBS` | apagado local | `1` en job de Actions para recibos remotos y estado del Worker |

`SEMANTIC_SEARCH` y `CLIPOTECA_QUERY_DISPATCH_MODE` se activan de manera independiente. No poner ambos en `1` antes de verificar los archivos de embeddings y los inputs de Actions. La consulta vectorial usa cuota `query-embed`, separada de cuota `import` por IP. Los errores no deben publicar valores de las variables.

## 3. Orden de activacion y retroceso

1. `uv sync --frozen --extra dev` y `cd worker && npm ci && npm run typecheck && npm test`. Comprobar la paridad de version 2.0.1 y los tres jobs CI.
2. Ejecutar migracion local desde datos validados: `uv run python -m clipoteca migrate-embeddings --root data/embeddings`; revisar el manifiesto SHA y respaldar HEAD. Publicar solo mediante una operacion explicitamente autorizada: `uv run python -m clipoteca migrate-embeddings --root data/embeddings --publish` con `GITHUB_REPOSITORY` y `GITHUB_TOKEN` efimeros. No sobrescribir un indice existente sin cotejar checksums.
3. Pegar primero el YAML de modo liviano M22 descrito abajo, con revision de los permisos. En modo `query_embedding` no se ejecutan Argos ni ingesta normal. Verificar que Actions acepta `mode`, `query` y `request_id` y que hay 1 running + 1 pending; el segundo pendiente puede ser reemplazado por uno nuevo (no FIFO durable).
4. Con manifiesto, permisos y job mode confirmados, activar `SEMANTIC_SEARCH=1`; si el encoder no corre, mantener `CLIPOTECA_QUERY_DISPATCH_MODE=0`. Activarlo en `1` solamente tras smoke de consulta con recibos.
5. Cuando no haya sidecars M21 faltantes, cambiar `CLIPOTECA_M21_SIDECARS=0` y monitorizar `source_warnings`. Mantener un procedimiento de rollback del flag.
6. Elegir runner propio solo tras smoke comparativo desde Actions y equipo aislado. El default `self_hosted_providers: []` significa que Actions conserva todos los proveedores. Consultar [RUNNER_PROPIO.md](RUNNER_PROPIO.md).
7. Ejecutar `uv run python scripts/smoke_sources.py --mode all --output data/reports/smoke.json` **en main**, generar [MATRIZ_FUENTES_V2.md](MATRIZ_FUENTES_V2.md) con reporte real y seguir [DEMO_E2E_V2.md](DEMO_E2E_V2.md). Cerrar M24 solo con evidencias concretas.

Rollback: desactivar flags semanticos y consultas nuevas, dejar ruta lexical; mantener `/v1/search` y `submitImport`. Para el runner poner lista vacia y `CLIPOTECA_RUNNER=actions`. El motor no debe perder un job: los proveedores diferidos se registran como `source_errors`; un runner sin fuentes persiste `unavailable` y retorna codigo diferente de cero.

## 4. YAML pendiente M19: cuatro bloques de environment

Agregar los dos cookies **opcionales** como secrets de GitHub, nunca variables literales ni valores en el PR. Aplicar la misma insercion en estos cuatro bloques existentes de los workflows, conservando todas las variables previas:

`.github/workflows/ingest.yml` > `Publish credential-presence source snapshot`:
```yaml
          DOUYIN_COOKIE: ${{ secrets.DOUYIN_COOKIE }}
          TIKTOK_COOKIE: ${{ secrets.TIKTOK_COOKIE }}
```

`.github/workflows/ingest.yml` > `Ingest`:
```yaml
          DOUYIN_COOKIE: ${{ secrets.DOUYIN_COOKIE }}
          TIKTOK_COOKIE: ${{ secrets.TIKTOK_COOKIE }}
```

`.github/workflows/smoke.yml` > `Probe sources across four languages without publishing media`:
```yaml
          DOUYIN_COOKIE: ${{ secrets.DOUYIN_COOKIE }}
          TIKTOK_COOKIE: ${{ secrets.TIKTOK_COOKIE }}
```

`.github/workflows/sources.yml` > paso de snapshot/exportacion de fuentes:
```yaml
          DOUYIN_COOKIE: ${{ secrets.DOUYIN_COOKIE }}
          TIKTOK_COOKIE: ${{ secrets.TIKTOK_COOKIE }}
```

BILIBILI_COOKIE ya existe en los cuatro env y debe conservarse sin duplicar. No agregar dos veces `BILIBILI_COOKIE` si ya existe en el bloque. Verificar que el workflow de fuentes usa esas claves solo cuando corresponde. `BILIBILI_USER_AGENT` no es secreto; solo asignarlo si se prueba un UA diferente al propio y se acepta su compatibilidad.

Para M18 (Europeana, Coverr, NARA, DPLA y Smithsonian), aplicar en esos mismos pasos solo los secretos que el adaptador vaya a utilizar:
```yaml
          EUROPEANA_API_KEY: ${{ secrets.EUROPEANA_API_KEY }}
          COVERR_API_KEY: ${{ secrets.COVERR_API_KEY }}
          NARA_API_KEY: ${{ secrets.NARA_API_KEY }}
          DPLA_API_KEY: ${{ secrets.DPLA_API_KEY }}
          SMITHSONIAN_API_KEY: ${{ secrets.SMITHSONIAN_API_KEY }}
```

## 5. YAML pendiente M22: input, concurrencia y encoder ligero

En `.github/workflows/ingest.yml`, bajo `on.workflow_dispatch.inputs`:
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

Sustituir `concurrency` existente por la clausula que separa el modo ligero:
```yaml
concurrency:
  group: ${{ inputs.mode == 'query_embedding' && 'clipoteca-query-embedding' || format('clipoteca-ingest-{0}', inputs.request_id || github.run_id) }}
  cancel-in-progress: false
```

En cada job de ingesta (normal y autoalojado, si se configura), timeout:
```yaml
    timeout-minutes: ${{ inputs.mode == 'query_embedding' && 10 || 45 }}
```

Los pasos ffmpeg/Argos, snapshot e ingesta normal requieren `if: inputs.mode != 'query_embedding'`. El paso de subir metricas debe usar `if: always() && inputs.mode != 'query_embedding'`. Checkout y setup-uv quedan en ambos modos. Bajo setup-uv agregar los dos pasos:
```yaml
      - name: Cache query encoder dependencies and HuggingFace weights
        if: inputs.mode == 'query_embedding'
        uses: actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830 # v4
        with:
          path: |
            .cache/uv
            .cache/huggingface
          key: query-embedding-v1-${{ runner.os }}-${{ hashFiles('uv.lock') }}
      - name: Cache semantic query vector M22
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

Concurrencia en GitHub Actions: una ejecucion activa y una pendiente por grupo; el ultimo pendiente sustituye al anterior, no garantiza cola FIFO ni durabilidad. Esto debe monitorearse por recibos y limites del Worker.

## 6. YAML pendiente M20: runner autoalojado y smoke

La integracion completa con todos los inputs y guards se encuentra en [RUNNER_PROPIO.md](RUNNER_PROPIO.md) y se debe combinar con el modo liviano M22 de este documento antes del despliegue.

Anadir a `ingest.yml` `workflow_dispatch.inputs.runner`:
```yaml
      runner:
        description: Runtime class
        type: choice
        required: false
        default: actions
        options:
          - actions
          - self-hosted
```

Guard del job de runner propio, sin relajar ninguna condicion:
```yaml
  ingest-self-hosted:
    if: github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch' && inputs.runner == 'self-hosted'
    runs-on: [self-hosted, clipoteca-home]
```

El job normal debe excluir `inputs.runner == 'self-hosted'`. En el paso Ingest usar `CLIPOTECA_RUNNER` y `--runner` con mismo valor. **No ejecutar codigo de forks** ni suponer que un guard protege contra un workflow malicioso nuevo en un PR. Un runner de repositorio personal no dispone de las restricciones de runner groups por workflow de una organizacion; por ello es preferible el runner efimero aislado en VM sin acceso a la LAN.

Para `smoke.yml` agregar `on.workflow_dispatch.inputs.runner` y un job propio con el guard exacto, manteniendo la sonda de cuatro idiomas sin publicacion de media. El bloque completo esta en [RUNNER_PROPIO.md](RUNNER_PROPIO.md).

## 7. Cloudflare y limites operativos

Para 256 embeddings y trafico moderado, medir Worker CPU y latencia reales antes de elegir plan. Workers Free limita CPU por peticion a unos 10 ms y no se puede prometer fuerza bruta de hasta 4096 vectores bajo esa cuota. Para 1000 o 5000 clips y 50 escenas usar Workers Paid o reducir candidatos y medir en workerd; las cifras Node no predicen consumo en produccion. La Cache API no es limitador global atomico entre colos. Evitar expender Action minutes por consultas sin embeddings o por guiones repetidos.

Documentacion: [M22_BUSQUEDA_SEMANTICA.md](M22_BUSQUEDA_SEMANTICA.md), [M23_GUION.md](M23_GUION.md), [RUNNER_PROPIO.md](RUNNER_PROPIO.md).

## 8. Criterios abiertos para cerrar 2.0.0

- [ ] Smoke real de todas las fuentes en main con timestamp y respuesta por idioma.
- [ ] Bilibili HTML5/DASH probado con video real y politica para habilitar descarga.
- [ ] Runner propio medido en PC aislado solo si necesario; nunca con forks sin control.
- [ ] Migracion de embeddings comprobada y consulta semantica real `aguacero`.
- [ ] Demo E2E con video/storyboard y cinco escenas MCP realmente reproducidos.
- [ ] Informe de proveedores y CI verde sobre SHA final, luego decidir tag/release y despliegue fuera de este PR.

No declarar satisfaccion de estos criterios a partir de tests offline ni de un PR verde.

## Operacion 2.0.1: raw GitHub y smoke de descarga sin publicacion

El Worker usa por defecto raw.githubusercontent.com para clips.json
y jobs/*.json en worker/src/catalog.ts y worker/src/jobs.ts;
CATALOG_URL y CATALOG_JOBS_URL, si se configuran, tienen prioridad.
No hace falta pasar --var al desplegar ni tocar worker/wrangler.toml.
MCP list_sources usa tambien
raw.githubusercontent.com/.../main/data/sources.json. Esto evita
la cuota anonima compartida de 60 requests/h de la API Contents.
El CDN raw puede cachear alrededor de cinco minutos: un clip nuevo o
el estado de un job publicado pueden tardar hasta ~5 minutos en reflejarse
en las respuestas. CATALOG_TTL_SECONDS es la cache adicional local
del Worker y no anula el cache de origen.

Alternativa para una futura operacion autorizada: una API GitHub
autenticada con token de solo lectura (secret CLIPOTECA_READ_TOKEN).
No se implementa ni se requiere ese token en este cambio; si se
incorpora despues, debe ser opcional, privado y con fallback a raw,
nunca guardado en wrangler.toml, logs ni artefactos.

El smoke habitual --mode all NO descarga videos. La prueba real se
activa expresamente con:
uv run python scripts/smoke_sources.py --mode downloads --output data/reports/downloads.json
O bien --mode all --include-downloads para incluirla como parte
separada del reporte; sin la flag all conserva su significado.
La sonda toma un candidato por proveedor, resuelve direct_media,
toma como maximo 32 MiB (Range en MP4, 20 s de segmentos en HLS)
y valida la firma del contenedor. Guarda resultado y motivo
sanitizados por proveedor, sin URLs firmadas, tokens ni videos. Los
temporales se borran siempre y no se crea release ni publicacion.

Una sonda DOWNLOAD_OK observada en main es evidencia para revisar
la capacidad download en data/sources.json en un PR APARTE,
nunca una promocion automatica en este script.

## Operacion 2.0.5: sesion de TikTok opcional y apagada

Desde 2.0.5 los pasos de ingest.yml y smoke.yml reciben el secreto
solo cuando la variable de repositorio lo habilita:

```yaml
TIKTOK_COOKIE: ${{ vars.TIKTOK_SESSION_ENABLED == '1' && secrets.TIKTOK_COOKIE || '' }}
```

Con la variable ausente o distinta de `1` (estado de fabrica) no se
instala Chromium, no se usa ninguna sesion y TikTok informa
SESSION_REQUIRED sin afectar al resto de las fuentes.

Para encenderla: Settings > Secrets and variables > Actions > Variables,
crear `TIKTOK_SESSION_ENABLED` con valor `1`. Para apagarla, borrar la
variable o ponerla en `0`. No requiere cambios de codigo ni despliegue.

Cautelas medidas el 22-nov-2026:

- La busqueda con firma nativa responde AuthRequired aun con cookie
  valida; el camino que entrega resultados es el navegador (Playwright).
- Una cuenta nueva usada a la vez desde una red domestica y desde
  runners de GitHub fue marcada por TikTok: las respuestas pasaron a
  HTTP 200 con cero videos y la cuenta pidio captcha en el navegador
  del propietario. No es un error del codigo ni de la cookie.
- Usar siempre una cuenta dedicada, nunca personal. Si se enciende,
  esperar pocas consultas diarias y captchas manuales ocasionales.
- Una cookie vencida o rechazada degrada a AUTH_REQUIRED saneado; el
  valor de la cookie nunca aparece en logs, artefactos ni catalogo.

El estado medido de todas las fuentes esta en [ESTADO.md](ESTADO.md).
