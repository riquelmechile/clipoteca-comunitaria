# Especificacion de aporte

## Evidencia del catalogo

En `pipeline/clipoteca/media/normalize.py`, los clips de catalogo usan MP4,
H.264, yuv420p, pixeles cuadrados, preset medium y faststart. No fijan bitrate
ni CRF explicito (libx264 usa 23 por defecto); conservan FPS de origen entre
1 y 60 y audio, si existe, AAC 128 kbit/s. Lado corto maximo 1080 sin ampliar,
duracion hasta 20 s. En data/clips.json se observaron clips publicados con
320x180 a 24 FPS (fixture de 2 s), 1920x1080 a 50 FPS (10,54 s) y
1080x1920 a 25 FPS (17,76 s), todos h264/mp4 y sin audio.
Estos son metadatos del catalogo, no una medicion independiente de cada Release.

El esquema clip.schema.json requiere ID clip-NNNNNN, titulo, descripcion y
tags es/en, origen (proveedor, ID, idioma, titulo y URL), duracion, ancho,
alto, FPS, proporcion, orientacion, codec, contenedor, audio, bytes, SHA-256,
hash perceptual, URL de Release, miniatura, estado y fecha. Licencia, autor y
ventana temporal se conservan cuando existen. El indice textual tiene shards
por idioma; los embeddings son artefactos separados, no campos obligatorios
del clip. El contrato actual localizado del clip es es/en.

## Aportes

- Video: 120 s y 100 MiB maximos; H.264, MP4, yuv420p, CRF 23, medium,
  faststart, AAC 128k si hay audio. Lado corto maximo 720, dimensiones pares
  y sin ampliar una fuente menor; se conserva el encuadre completo y FPS
  dentro de 1..60. Se respeta la orientacion visual declarada por el archivo
  (incluyendo metadatos de orientacion), no se recorta ni se gira por eleccion.
- Imagen: un fotograma, 24 MP y 10 MiB maximos de entrada, WebP calidad 85,
  hasta 1920 en el lado largo, sin ampliar y preservando transparencia.
- Ingesta: valida manifiesto, tamanos y SHA-256 de partes, decodifica original,
  normaliza, vuelve a decodificar y valida la salida. Solo publica salida.
  El registro contiene los datos tecnicos reales y SHA-256 de salida.
- Descripcion en espanol obligatoria (1..400 caracteres); ingles y tags
  opcionales (1..8 por idioma, 1..40 caracteres por tag). Los LLM deben aportar
  ambos idiomas y tags cuando puedan. Las personas solo describen en espanol.
  La ingesta deriva palabras de la descripcion sin inventar contenido y usa
  el traductor Argos verificado existente para completar el ingles.
  metadata_auto enumera los campos generados. Si falla Argos, se conserva
  e indexa el espanol, sin rechazar el archivo. Los tickets antiguos sin
  descripcion se admiten por compatibilidad.
- Los registros antiguos de URL y modo legacy sin Actions mantienen su
  contrato historico. Para normalizacion obligatoria usa la ingesta KV/Actions.
- Al finalizar la publicacion de un aporte con metadatos, se actualiza
  `data/aportes.json` mediante escritura optimista con reintentos. El indice
  conserva fotos y videos completos y permite buscar por texto es/en con
  `buscar_aportes` o GET /v1/media/search?q=texto, devolviendo media_id.
  No se inventan embeddings ni se agrega un video falso para las fotos.
  El registro de Release es el punto de commit; si se agotan los reintentos
  del indice el workflow falla e informa el problema, pero el aporte ya
  registrado sigue accesible por su ID. El indice puede requerir reparacion
  administrativa en ese caso. No se indexan registros sin QA ni metadatos.
- Los aportes no se deben confundir con clips: el catalogo de clips exige
  video de hasta 20 s, QA visual y miniatura. Un archivo aportado puede ser
  foto o video de hasta 120 s. Un clip derivado de un render mantiene la
  QA existente, y hereda descripcion/tags del registro. El archivo original
  normalizado permanece accesible con su media_id.
- La toma de una parte dura 120 s; un reintento con una nueva capacidad
  reemplaza una toma vencida. El contenido se guarda por hash para que una
  escritura vieja no reemplace los bytes de otra. Las partes abandonadas
  caducan por TTL.

La especificacion de CLI y el flujo del LLM estan en [SUBIDAS.md](SUBIDAS.md).

Para activar Argos en la ingesta desplegada, el operador debe aplicar
`propuestas/media-ingest.yml` sobre el workflow existente. Usa los modelos
verificados ya compartidos por las busquedas; si la preparacion falla,
la ingesta sigue con los metadatos en espanol. No necesita nuevas llaves.
