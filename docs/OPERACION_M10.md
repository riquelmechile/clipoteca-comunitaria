# Clipoteca · Operación y recuperación M10

## Estado de esta entrega

Los cambios se proponen mediante PR encadenados de desarrollo. **No** están desplegados en Cloudflare ni integrados en `main`. El CI sólo ejecuta pruebas locales y simulaciones de redes/GitHub; no confundirlo con el smoke E2E en producción.


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

## Smoketest después de aprobar y desplegar

```bash
set -euo pipefail
: "${CLIPOTECA_API:?debe ser URL pública HTTPS}"
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

La demostración obligatoria es: cuatro términos ES/EN/ZH/RU registrados, clip real (<=20 s), FFmpeg, miniatura, SHA real, Release, catálogo ES/EN, búsqueda REST/MCP y redirect 302 a un asset existente. La ejecución de estos comandos y la captura de evidencias requieren los secrets y el despliegue, por lo que no se reemplazan por mocks.

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
[APORTES_M9.md](APORTES_M9.md). Todavía se necesita evidencia E2E real.

Los costos cero dependen de cuotas de Actions, Workers, proveedores y Releases.
Si se excede la cuota, pausar nuevos despachos y mantener la API de lectura.

## Puertas de salida que todavía bloquean v1.0

- Worker Cloudflare y MCP desplegados con allowlists y secretos válidos.
- E2E real de descarga y publicación con dos proveedores, incluyendo uno M5, y medición de costos/uso reales.
- Comprobación de resultados multilingües por proveedor y pruebas de red en runners confiables.
- Prueba E2E real de publicación automática desde un Issue comunitario.

No marcar esos puntos como hechos por ver un CI verde.
