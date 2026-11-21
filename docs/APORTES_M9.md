# M9 · Ingesta automática de aportes comunitarios

Un colaborador sin acceso de escritura abre el formulario **Aportar escena** y entrega
un enlace HTTPS público que apunta directamente a MP4, WebM u OGV.
El workflow `.github/workflows/contribution.yml` se activa por `issues.opened` o
`issues.edited`, solo en el repositorio dueño. No requiere etiqueta de aprobación,
intervención manual, ni ninguna declaración comunitaria sobre permisos.

## Flujo operativo

1. Actions recibe el número del Issue como dato, nunca construye comandos con el cuerpo.
2. Python recupera el Issue desde la API fija de GitHub y valida título, estado,
   autor y URL pública. Se rechazan destinos privados, credenciales embebidas,
   redirects inseguros, rutas no multimedia y consultas con tokens.
3. El adaptador `community` entrega un candidato con ID determinista
   `issue-{número}-{hash SHA256 URL[:12]}` al mismo pipeline de 15 proveedores.
   Se aplican las defensas existentes de SSRF/DNS, límites de descarga,
   MIME/firma, Argos, FFmpeg, deduplicación, Releases y catálogo.
4. La fuente conserva URL original, número y autor del Issue y su URL pública.
5. Actions comenta el estado saneado; etiqueta el éxito `clipoteca-ingested`,
   cierra el Issue cuando se publicó o se detectó duplicado y mantiene abierto
   el fallo como `clipoteca-ingest-failed` para editar y reintentar.

Los procesos FFmpeg/ffprobe sólo reciben `safe_media_env()` sin credenciales.
`GITHUB_TOKEN` se ofrece únicamente al paso Python de ingesta, no a pasos
de shell que interpolen contenido del Issue. Permisos limitados por job.

El endpoint `POST /v1/contributions` puede crear el mismo tipo de Issue;
retorna `queued` tras recibir confirmación de creación. No presupone publicación:
sigue siendo necesario probar un enlace real en Actions, y no se declara
desplegada la solución hasta contar con evidencia E2E.
