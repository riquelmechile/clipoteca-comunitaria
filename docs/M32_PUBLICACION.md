# M32 PR2: publicacion del video terminado

## Entradas

El directorio de trabajo contiene guion.json (M25), timeline.json (M26),
video.mp4 y render.json (M30), qa_report.json (M31), y un result.json
con status ok por escena bajo scenes/ID/. Para una escena importada
tambien contiene scenes/ID/import/imported.json, clips/, thumbs/ y
storyboards/ (M28 PR3). timings.json es opcional.

    python -m clipoteca.studio.publish --work work
    python -m clipoteca.studio.publish --work work --dry-run salida

La CLI lee GITHUB_REPOSITORY y GITHUB_TOKEN del entorno, nunca de
argumentos. El job real necesita contents: write. La simulacion no
instancia clientes GitHub: usa el catalogo del checkout o el catalogo
ya existente en salida/data/clips.json; escribe assets y fichas locales.

## Orden de publicacion

0. La compuerta verifica QA pass=true, failed vacio, bytes y SHA del MP4,
   script_id canonico, todos los resultados ok y size_bytes de render.
   Valida las rutas y los archivos de todas las importaciones usadas
   antes de contactar GitHub. No publica candidatos descartados.
1. Lee el catalogo. Reutiliza coincidencias por SHA o find_duplicate;
   reserva IDs definitivos sin pisar los assets del lote. Sube y
   verifica clip, miniatura y storyboard con nombres v2. Un solo
   writer.update publica todos los nuevos registros. Si falla,
   intenta borrar solo los assets nuevos de esta corrida.
2. Publica el MP4 en video-AAAA-MM-DD-NNN con nombre
   video_<video_id>.mp4, sin draft, prerelease ni make_latest.
   Verifica nombre, bytes y SHA. Conflictos 422 tienen reintentos
   acotados; una ficha existente por SHA evita otro upload, y un
   asset de video ya verificado sin ficha reutiliza su Release.
3. Escribe data/videos/<video_id>.json al final, por Contents API
   con control optimista. La ficha es el marcador de publicacion.
   publish.json local se reemplaza atomicamente con el mismo resultado
   que imprime la CLI: version, video_id, asset_url, record_path,
   clips (mapa provisional a definitivo), reused_clips, already_published.

La publicacion presupone el escritor unico del workflow. Tras un
corte posterior al commit del catalogo se reutilizan sus clips; tras
el upload del video se reutiliza el asset; tras la ficha se devuelve
already_published=true. Los IDs reservados por uploads interrumpidos
no se reciclan. No se borran videos.

## Ficha

schemas/video-record.schema.json exige version, id, created_at,
script_id, title, aspect, language, media (duration_s, width, height,
fps, size_bytes, sha256), storage (release_tag, asset_url), script
original, scenes (id, clip_id definitivo, imported, source_url,
start, len), qa (pass, checks, wer, sync_mean_ms, sync_p95_ms, lufs,
true_peak_dbfs), timings y engine (clipoteca, hyperframes, gsap_sha256).

Ejemplo parcial de la ficha (identificadores ilustrativos, no mediciones):

    {"version":1,"id":"video-2026-01-02-001",
     "storage":{"release_tag":"video-2026-01-02-001",
       "asset_url":"https://github.com/example/clipoteca/releases/download/video-2026-01-02-001/video_video-2026-01-02-001.mp4"}}

Los tests construyen fichas completas y las validan contra el esquema.

## Codigos y limites

- 0: publicado o ya publicado.
- 4: QA, bytes, guion o resultados de escena rechazados.
- 2: entrada invalida.
- 1: operacion remota fallida.

M28 guarda el hash original solo del MP4. Para miniaturas y storyboards
se validan rutas, archivos regulares no vacios (maximo 5 MiB) y cantidad
de previews; se calcula su SHA antes de subir y se verifica remoto.
Su integridad queda acreditada desde la publicacion, no desde M28.
Agregar hashes al manifiesto queda pendiente de M28.

Solo se conserva procedencia factual, sin decisiones de licencias,
creditos ni derechos. Los tokens y cuerpos HTTP nunca se imprimen;
las URLs publicas de assets contienen el repositorio por definicion.
El workflow y sus permisos se configuran fuera de este PR.

## Ajustes de la revisión del orquestador

- Si una corrida se corta después de crear el Release del video y antes
  de subir el archivo, la siguiente reutiliza ese Release vacío del mismo
  día en vez de dejarlo huérfano y crear el siguiente número. Esto exige
  que la publicación esté serializada (un solo job de publicación a la
  vez): el workflow debe usar un grupo de concurrencia para este paso.
- Los tests incluyen un recorrido completo con las clases reales
  (almacenamiento de video, escritor del catálogo y fichas) contra una API
  de GitHub simulada en memoria, sin red.
- Pendiente conocido: un asset de storyboard que no se pudo borrar en un
  rollback no reserva su número de clip, y una corrida posterior puede
  chocar con ese nombre (termina en error, no corrompe nada).
