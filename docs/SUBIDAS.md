# Como aportar tus videos y fotos

Todo lo que subas queda publico. No subas material privado ni de terceros sin permiso.
No necesitas cuentas ni llaves personales. La subida pasa por Cloudflare y GitHub Actions:
la validacion completa ocurre antes del registro y la QA del clip antes de indexarlo.

## Para personas

Pide al LLM que suba tus archivos. Si el host no puede transmitirlos, pide una sesion
(`crear_subida`) y abre su enlace de lote desde el celular. Selecciona hasta 12 archivos,
escribe una descripcion y tags en espanol e ingles para cada archivo, opcionalmente titulo y autor, y pulsa Subir archivos publicos. Veras el progreso
y los IDs. Consulta el estado con el boton de la pagina o con `estado_subida`.
El enlace dura 24 horas; guardalo como una capacidad temporal, no lo publiques.
Los archivos fallidos consumen un cupo si ya recibieron ticket; crea otra sesion si hace falta.

## Para LLM y agentes

1. Llama a `crear_subida` una vez por lote. No muestres el enlace si puedes subir tu mismo.
2. Si el host proporciona un archivo MCP, llama a `subir_archivo(session_id, file, description, tags)`.
   ChatGPT admite `_meta["openai/fileParams"]` con un objeto que tiene `download_url`
   y `file_id` obligatorios, `mime_type` y `file_name` opcionales.
   Ver [referencia oficial de OpenAI](https://developers.openai.com/plugins/reference#file-apis).
   No inventes URLs, IDs ni bytes. El Worker descarga directamente la URL temporal;
   no la guarda en el manifiesto ni la envia a Actions. Solo HTTPS de los dominios
   permitidos y sin redirecciones; por defecto oaiusercontent.com y sus subdominios.
   El operador puede ajustar CLIPOTECA_FILE_HOSTS (lista de sufijos de hosts confiables)
   segun el host observado. La documentacion no garantiza un dominio concreto.
   El origen debe entregar Content-Length y bytes sin compresion de transporte.
3. Si tienes shell o red, usa `ticket_subida(session_id, kind, size, description, tags)` con el tamano exacto en bytes (`wc -c < "$FILE"`). El ticket devuelve `part_size` (24 MiB) y `parts`. Para archivos de hasta 95 MiB puedes ejecutar:
   ```sh
   curl --fail-with-body -H "Authorization: Bearer $UPLOAD_TOKEN" \
     --upload-file "$FILE" "$PUT_URL"
   ```
   Usa el archivo binario, no JSON ni multipart. El servidor divide el flujo en chunks
   de hasta 24 MiB (100 MiB = cinco chunks), con SHA-256 por chunk.
   Para archivos mayores de 95 MiB **siempre** usa partes; tambien se recomienda para
   redes moviles. En un directorio temporal vacio, con GNU split:
   ```sh
   split -b 24M -d -a 1 "$FILE" parte-
   for p in parte-*; do
     curl --fail-with-body --retry 3 --retry-delay 2 \
       -H "Authorization: Bearer $UPLOAD_TOKEN" \
       --upload-file "$p" "$PUT_URL/parts/${p##*-}" || exit 1
   done
   curl --fail-with-body -X POST \
     -H "Authorization: Bearer $UPLOAD_TOKEN" "$PUT_URL/complete"
   ```
   Puedes repetir una parte o enviarlas fuera de orden. No uses JSON ni multipart/form-data
   para los bytes. El ticket dura 2 horas y el PUT unico tiene un limite de 95 MiB.
   Completa antes de vencer para dar tiempo a la ingesta. Si se pierde la respuesta de
   complete, consulta el estado antes de volver a intentar.
4. Para una imagen realmente disponible en bytes, `subir_imagen_base64` acepta
   base64 puro de hasta 40 KiB decodificados; no uses data URI. Este limite permite
   respetar los 64 KiB del cuerpo MCP. Es preferible el archivo o PUT binario.
5. Si ninguna via aplica, comparte solo el enlace de lote, no un enlace por archivo.
6. Consulta `estado_subida(session_id)`; cada archivo incluye media_id, kind, status y
   motivo cuando corresponda. Usa el media_id directamente en `media.ref` solo
   si status es registered. El render rechaza otros estados con 409; no espera.
   Puedes consultar de nuevo durante un plazo acotado, por ejemplo 2 minutos.

## REST

- POST /v1/uploads (sin Bearer): crea sesion, devuelve session_id, max_files,
  expires_at y upload_url.
- GET /v1/uploads/{session_id}: estado del lote.
- POST /v1/uploads/{session_id}/files: JSON con kind image/video, titulo y autor
  opcionales (campos title y author), source y license_note opcionales; description y tags es/en obligatorios; size obligatorio en bytes para un ticket.
  Tambien admite file o image_base64 como en MCP; devuelve 202 si recibe los bytes,
  o 201 con ticket si no se suministran bytes.
- PUT /v1/media/upload/{id}/parts/{n}: parte binaria con Content-Length exacto y Bearer del ticket (n desde 0). POST /v1/media/upload/{id}/complete: verifica las partes y dispara ingesta.
- POST /v1/media/upload: API anterior por archivo; declara size en bytes; publica cuando KV, Durable Object,
  token interno de firma y dispatch estan configurados. El modo legacy sin QA
  por Actions sigue requiriendo Bearer de escritura; no se abre al publico.
- GET /v1/media/{media_id}: consulta individual; POST /v1/media (URL publica)
  conserva su contrato y autenticacion. Enlaces compartidos Drive/Dropbox no se
  normalizan: usa un archivo, PUT o URL directa compatible con el registro existente.

## Limites y seguridad

Video MP4/MOV/WebM: 100 MiB, 120 segundos. Imagen JPEG/PNG/WebP: 10 MiB y 24 MP.
El tipo declarado o inferido del MIME/nombre se contrasta con cabeceras y con
decodificacion en Actions. No se aceptan otros formatos ni se publican bytes sin QA.
El DO aplica cuotas atomicas (UTC): 4 sesiones/hora por IP, 24 tickets/hora por IP,
100 sesiones y 200 tickets/dia globales; 40 archivos y 1 GiB/dia por IP,
200 archivos y 2 GiB/dia globales. Los intentos admitidos consumen cuota aunque
fallen; no hay devolucion para evitar abuso. Si recibes 429, espera la ventana.
La cuota de bytes se reserva al emitir el ticket; no se cobra otra vez por parte. Los tickets KV duran 2 horas y se consumen al completar o por PUT unico compatible. El enlace de sesion es una
capacidad aleatoria; no es un secreto del servidor. Los tokens internos nunca
se entregan al cliente. Los registros temporales del lote se retienen hasta
48 horas desde su ultima escritura; las referencias registradas permanecen.

## Preparacion automatica

Primero escribe el guion con el aspecto solicitado, describe el contenido real de
cada archivo (no el tema general del guion) y verifica el permiso de publicacion.
Ejemplo de `aporte.json`:

```json
{"kind":"video","title":"Lluvia en el campo","author":"Autor","source":"Grabacion propia","description":{"es":"Lluvia sobre el campo","en":"Rain falling over a field"},"tags":{"es":["lluvia","campo"],"en":["rain","field"]}}
```

Con Python 3, curl, ffmpeg y ffprobe disponibles:

```sh
curl -fsS https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/scripts/preparar_y_subir.py -o preparar_y_subir.py
python3 preparar_y_subir.py archivo.mov --api "$ORIGEN_HTTPS" --metadata aporte.json --desde 2 --hasta 32
```

Omite desde/hasta si no necesitas recorte temporal. Puedes pasar --session para
compartir un lote. El script imprime solo el media_id al quedar registered.
Comando equivalente para video (elige W/H pares proporcionalmente, lado corto
hasta 720 sin ampliar, y FPS original entre 1 y 60):

```sh
ffmpeg -i ORIGINAL -vf scale=W:H:flags=lanczos,setsar=1,fps=FPS -c:v libx264 -pix_fmt yuv420p -preset medium -crf 23 -c:a aac -b:a 128k -movflags +faststart SALIDA.mp4
```

Nunca recortes el encuadre ni cambies la orientacion visual. El servidor vuelve a
normalizar y verificar los bytes, incluso si el cliente no los prepara. Si no
hay shell, usa subir_archivo o el archivo original en las partes. Tras registered,
coloca media_id en media.ref, llama render_video y consulta get_video_status
hasta obtener la URL de entrega.

Ver [especificacion del aporte](APORTES-ESTANDAR.md).

Busca aportes ya registrados mediante `buscar_aportes(q)` (fotos y videos),
o GET /v1/media/search?q=texto. Los clips derivados siguen en search_clips.
