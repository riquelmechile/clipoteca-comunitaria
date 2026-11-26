# Espera larga de video y aportes

Flujo MCP: crear_subida, subir_archivo, render_video inmediatamente con los media_id,
esperar_video repetidamente hasta listo:true. No pedir otro turno mientras el
host permita continuar. Si done, entregar file_url. Si failed/expired, informar
el estado; si el turno termina prematuramente, entregar tracking_url.
esperar_aportes es opcional para conocer el resultado y calidad de cada aporte.

REST:
- GET /v1/videos/{id}/esperar?max_segundos=300
- POST /v1/media/esperar con {"session_id":"...","max_segundos":300} o
  {"media_ids":["..."],"max_segundos":300}; exactamente una modalidad.
- GET /v1/videos/{id}/ver: seguimiento, reproduccion y descarga.
- Accept: text/event-stream en las dos esperas produce comentarios keep-alive
  cada 15 s y un evento result con http_status y result. JSON es el default REST.

CLIPOTECA_WAIT_MAX_SECONDS reduce el tope (300 s absoluto). Se consulta cada
10 s, se detiene por estado terminal, desconexion o plazo. expired es terminal.
El SDK 2.x usa ctx.mcpReq.signal, _meta.progressToken y notify, responseMode=sse,
keepAliveMs=15000. No se inventa token de progreso. No se usa waitUntil.

Cloudflare: HTTP no tiene limite fijo de tiempo de pared mientras sigue
conectado. Esperar I/O no equivale a tiempo CPU; evitar procesamiento continuo.
Free: 50 subrequests externos y 1000 internos; Paid: 10000 externos por defecto.
El estado de GitHub requiere varias consultas (runs, jobs, artifacts, evidencia);
no son 30 subrequests totales. El presupuesto externo por espera es 45 por
defecto; los redirects se cargan conservadoramente como cinco. Si se agota,
se devuelve la ultima observacion no terminal para que el cliente repita.
El operador de un plan Paid puede subir CLIPOTECA_WAIT_SUBREQUESTS hasta 2000.
No se afirma que el despliegue actual sea Paid. El DO permite observar aportes
activos sin consultar GitHub cada vez; se verifica el manifest periodicamente.

Referencias:
- https://developers.cloudflare.com/workers/platform/limits/
- https://ts.sdk.modelcontextprotocol.io/v2/servers/logging-progress-cancellation
- https://ts.sdk.modelcontextprotocol.io/v2/api/@modelcontextprotocol/server/server/createMcpHandler.html

## Calidad de aportes

La ingesta asincrona valida el lado corto >=360 antes de publicar; 360..719
queda marcado low_resolution. El render escala los aportes de ese rango y
emite advertencia de QA, sin degradar la politica de clips del catalogo.
Si un aporte se rechaza o no se registra en 15 min, la escena con query y
prompt usa busqueda, con el motivo y media_id en QA. Sin esos campos falla
el plan con el motivo. El workflow propuesto requiere promocion por el
mantenedor: propuestas/render.yml (no se modifica el workflow activo).

## Diagnostico fileParams

Documentacion oficial:
https://developers.openai.com/plugins/reference#define-file-inputs
El host entrega download_url, file_id, mime_type y file_name. No hay un
parametro documentado para solicitar el original en lugar de una previsualizacion.
getFileDownloadUrl renueva la URL temporal; no garantiza otra calidad.
La normalizacion local usa min(1,720/lado_corto), conserva duracion y no
recorta. Por eso 464x688 no puede originarse de 1080p mediante esa regla,
salvo transformacion anterior o metadatos de origen diferentes. Esto es una
hipotesis, no prueba de compresion por ChatGPT; dos archivos identicos en
dimension y duracion tampoco demuestran igualdad de bytes.

Para confirmar: crear un video sintetico 1920x1080 de 6 s con ffmpeg, guardar
sha256, bytes y ffprobe; adjuntarlo en ChatGPT, llamar subir_archivo via
fileParams y comparar dimensiones/duracion/sha256 del origen antes de
normalizar con los valores originales y el registro final. No guardar ni
publicar download_url, file_id ni tokens. La prueba nativa del host requiere
Clipoteca conectado y un adjunto real; no se reemplaza por una llamada REST
con URL inventada. Hasta que se mida, no se atribuye la causa al host.
