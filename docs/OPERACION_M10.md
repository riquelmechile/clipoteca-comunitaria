# Clipoteca · Operación y recuperación M10

## Estado de esta entrega

**Estado verificado 21-11-2026:** Worker Cloudflare desplegado en `https://clipoteca-worker.sc-plasticov.workers.dev` con REST `/v1/*` y MCP `/mcp`; 8 clips reales publicados (Pexels, PeerTube y aporte comunitario). Los workflows reales de ingesta, importación desde Worker, PeerTube, aporte automático y smoke cuentan con evidencias enlazadas en [INFORME_V1.md](INFORME_V1.md). El CI de preparación del release debe validarse aparte; los mocks no sustituyen E2E.


## Gates de CI sin red

Desde un checkout limpio:

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest
(cd worker && npm ci && npm run typecheck && npm test)
```

Requiere Python 3.13, Node 22 o superior y FFmpeg. Para métricas y 15 proveedores: [matriz y evidencia](MATRIZ_FUENTES_M10.md). No se desactivan pruebas fallidas ni se relajan los controles de seguridad.

## Preparación de secrets y acceso

1. Verificar `CATALOG_URL`, `CATALOG_JOBS_URL`, `CATALOG_TTL_SECONDS`, `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS` del Worker.
2. Guardar `CLIPOTECA_DISPATCH_TOKEN`, `CLIPOTECA_WRITE_TOKEN` y `CLIPOTECA_ISSUE_TOKEN` como secretos **distintos** de Cloudflare, con privilegios mínimos de GitHub.
3. Mantener credenciales de proveedores en Actions Secrets, nunca en variables públicas ni URLs. Probar `workflow_dispatch` únicamente desde `main` tras pasar las comprobaciones técnicas.
4. La API sólo entrega links de GitHub Releases previamente verificados. No actúa como proxy de MP4.
5. El Worker usa Cache API para rate-limit e idempotencia de corta duración; **no constituye un lock global** ni evita todas las carreras entre centros. El JobStore y el catálogo deben conservar la idempotencia persistente.

## Smoketest del servicio ya desplegado

```bash
set -euo pipefail
CLIPOTECA_API="${CLIPOTECA_API:-https://clipoteca-worker.sc-plasticov.workers.dev}"
curl --fail-with-body -sS "${CLIPOTECA_API}/v1/health"
curl --fail-with-body -sS "${CLIPOTECA_API}/v1/clips?q=lluvia&aspect_ratio=9%3A16"
curl --fail-with-body -sS -H 'content-type: application/json' \
  -d '{"query":"lluvia","filters":{"aspect_ratio":"9:16"}}' \
  "${CLIPOTECA_API}/v1/search"
# Verificar con un clip ID verdaderamente publicado, no un ID de fixture:
# curl -i "${CLIPOTECA_API}/v1/clips/clip-XXXXXX/download"
# Con token autorizado, solicitar un término ausente, guardar request_id real,
# consultar /v1/jobs/{request_id} hasta terminal y repetir para probar dedup.
```

Las rutas principales ya se probaron con el Worker desplegado y hay publicaciones reales. Para regresión, ejecutar los comandos anteriores y conservar respuestas HTTP/IDs, sin copiar tokens ni URLs firmadas. La cobertura por proveedor e idioma y la facturación real siguen siendo verificaciones separadas; ver [INFORME_V1.md](INFORME_V1.md).

## Retries y dead letters

Los trabajos de fallo `failed`, `rate_limited`, `unavailable` y `no_results` pueden volver a `queued` con **attempt+1** hasta un máximo de **3** intentos. El tercer fracaso fija `dead_letter`; HTTP devuelve error tipado si se solicita una cuarta ingesta. `published` y `duplicate` son terminales irreversibles.

Investigar un `dead_letter` manualmente con `request_id`, `source_errors` saneados y artefacto de métricas. No reiniciar alterando un JSON de producción sin reconciliar los datos y assets. GitHub API 409/422 sólo reintenta con backoff corto; nunca reusa ID de un asset a medias.

## Rollback de catálogo, sin borrar Releases

El catálogo Git es la fuente de verdad. Tras detectar un cambio defectuoso:
1. Bloquear despachos nuevos y conservar la evidencia del job y SHA Git afectado.
2. Identificar un commit previo revisado y crear un PR de reversión contra `main`. No forzar `main` ni eliminar assets: archivos de Releases huérfanos se revisan por separado.
3. Para reconstrucción local de una copia del catálogo, usar el helper optimista y revisar el diff ANTES de publicar cualquier commit:

```bash
sha256sum data/clips.json
uv run python -m clipoteca.recovery \
  --catalog data/clips.json \
  --snapshot /ruta/a/clips.aprobado.json \
  --index-root data/index \
  --expect-sha256 SHA256_ACTUAL
git diff -- data/clips.json data/index
```

El comando valida el JSON completo contra schema, no acepta IDs duplicados, reconstruye índices por etapas y comprueba SHA para no sobrescribir cambios concurrentes. Sólo cambia archivos locales, no hace push ni elimina recursos remotos.

## Aportes y límites

`POST /v1/contributions` registra un Issue con estado `queued`.
El workflow `contribution.yml` se dispara automáticamente cuando se crea o edita
el formulario **Aportar escena**. Sin etiqueta de aprobación y sin disparo manual,
el adaptador `community` utiliza las mismas verificaciones de URL,
DNS/SSRF, MIME/tamaño, FFmpeg, deduplicación y persistencia que otras fuentes.
Los estados se comentan en el Issue y el éxito lo cierra; ver
[APORTES_M9.md](APORTES_M9.md). **E2E verificado**: [Issue #26](https://github.com/riquelmechile/clipoteca-comunitaria/issues/26) → `clip-000008` publicado ([run 44863391766](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44863391766)). Tras un éxito, la etiqueta previa `clipoteca-ingest-failed` debe retirarse aunque proviniera de un intento anterior.

Los costos cero dependen de cuotas de Actions, Workers, proveedores y Releases.
Si se excede la cuota, pausar nuevos despachos y mantener la API de lectura.

## Evidencias reales de cierre M10

- Ingesta: [run 44848918832](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44848918832).
- Importación Worker → Actions: [run 44858494471](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44858494471).
- Fuente PeerTube (M5): [run 44858474026](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44858474026).
- Aporte M9 sin aprobación manual: [run 44863391766](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44863391766).
- Smoke real: [run 44858482821](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44858482821).

## Verificaciones aún pendientes o recurrentes

- Confirmar los gates Python, Worker y real Argos en la rama de release, sin saltar fallos del runner.
- Registrar resultados online detallados por los 15 proveedores y cada idioma realmente admitido; `AUTH_REQUIRED` es un estado legítimo, no una prueba de descarga.
- Medir minutos/costos facturados de Actions, GitHub y Cloudflare desde sus paneles; no inferir gasto desde duraciones.
- Revalidar adaptadores sujetos a bloqueo de IP (Bilibili), políticas de cuentas, cambios de API, límites y tokens.

**Publicación:** la preparación técnica se presenta en PR para revisión; los tags, GitHub Releases y merges quedan fuera de este encargo.
