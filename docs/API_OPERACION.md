# M8a: despacho de ingesta desde Worker

La ruta `POST /v1/import` requiere un token Bearer igual a `CLIPOTECA_WRITE_TOKEN`.
La ruta `POST /v1/search` lee primero el catálogo, y solo si no hay coincidencias y
`import_if_missing=true` solicita `workflow_dispatch` con la cuota de caché.

## Configuración (secrets, no repositorio)

- `CLIPOTECA_DISPATCH_TOKEN`: secreto de Cloudflare Worker con permiso GitHub Actions: write
  para **riquelmechile/clipoteca-comunitaria**.
- `CLIPOTECA_WRITE_TOKEN`: otro secreto distinto para clientes con permisos de importación.
- `CLIPOTECA_GITHUB_REPO`: opcional, propietario y repositorio explícitos.
- `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`: dominios de confianza para el MCP HTTP.
- `CATALOG_URL` y `CATALOG_JOBS_URL`: fuentes públicas en Github; nunca un token en URLs.

## Flujo y degradación

1. Se busca en el catálogo. Un clip existente responde 200 y no consume GitHub Actions.
2. Un clip ausente calcula `request_id` estable, lee el job remoto; evita redispatch si
   ese job está en marcha o fue publicado.
3. Se aplica cuota Worker, luego POST a `/actions/workflows/ingest.yml/dispatches`
   con `ref=main` y `inputs.request_id`.
4. Solo HTTP 204 equivale a `202 queued`. Red, credenciales o errores GitHub dan
   errores tipados sin fingir un trabajo en marcha.
5. Se guarda una marca temporal 60 s en Cache API por `request_id`. Esta caché **no es un
   bloqueo transaccional global** y puede haber duplicados entre centros de Cloudflare;
   la idempotencia duradera corresponde al JobStore y a la publicación GitHub.
6. Las publicaciones del catálogo permanecen serializadas en el workflow de ingesta.

Nunca se muestra el token del dispatch ni la respuesta interna de error GitHub.
En versión pública aún sin secrets, las consultas existentes siguen funcionando;
las importaciones devuelven `dispatch_unavailable`, no un 202 engañoso.

Las pruebas de este hito usan `MockTransport`/fetch interceptado, no GitHub real.
