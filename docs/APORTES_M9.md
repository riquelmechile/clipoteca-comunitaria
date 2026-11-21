# Aportes de la comunidad: aprobación explícita antes de importar

`POST /v1/contributions` y la plantilla GitHub Issue **sólo crean una propuesta**, sin descargar el URL.
El triage valida formato y deja un comentario; no publica archivos.

## Pasos del revisor con acceso de escritura

1. Revisar que el vídeo sea **archivo directo MP4/WebM/OGV** con URL HTTPS pública, sin query con tokens y sin red privada. Confirmar derechos de redistribución y condiciones de la fuente, no solo declaración del remitente.
2. Añadir manualmente la etiqueta `clipoteca-approved` al Issue **después de la revisión**. Ningún webhook público añade esa etiqueta.
3. Disparar manualmente `Approved community contribution ingest` desde `main`: ingresar `issue_number` (entero positivo) y `rights_confirmed=yes`. Requiere permisos de Actions de colaborador; un Issue público por sí solo **jamás** ejecuta la ingesta.
4. El workflow recupera otra vez el Issue desde GitHub, exige estado abierto, etiqueta y declaración de derechos; no acepta redirects a la API ni texto de Issue como comando.
5. Solo entonces crea un adaptador aislado `community_contribution` (aparte de las 15 fuentes) y ejecuta el mismo `ingest()`: SSRF/DNS, MIME/firma, FFmpeg, dedup, hash, miniatura, Releases, catálogo y job persistente. Las descargas fallidas conservan un job con estado real, no un clip falso.

**Limitación:** todavía se necesita un ejercicio con un enlace real autorizado para verificar la publicación E2E. Una URL a una página web (YouTube, Instagram, etc.) se rechaza; no se hace scraping. Si el revisor no puede confirmar derechos, el Issue no se aprueba.

El workflow usa `concurrency: clipoteca-catalog` y tokens efímeros Actions con permisos mínimos de `contents:write` e `issues:read`. No se despliega ni se ejecuta desde esta rama de desarrollo.
