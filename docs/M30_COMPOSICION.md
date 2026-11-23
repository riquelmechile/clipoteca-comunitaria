# M30: composición HTML sin marca y render verificado

M30 combina un guion M25, timeline real M26, clips ajustados M29 y mix.wav
en HTML/GSAP compatible con HyperFrames 0.9.36. Cada escena exige su
clip real. Si falta, se rechaza el trabajo sin insertar fondos, imágenes
congeladas ni tarjetas de sustitución.

## Entradas y validación

- Guion validado por parse_video_script: title, aspect, language y escenas
  con id, type, title y sub.
- timeline.json: bpm, beat, total, cuts y escenas ordenadas con id, type,
  start, len, vo, dur y words. Los tiempos por palabra son relativos a la voz:
  tiempo absoluto = vo + tiempo de la palabra.
- Clips ajustados en DIR/ID.mp4, H.264 a 30 fps, mudos y con el tamaño correcto.
- mix.wav, PCM16 estéreo a 48.000 Hz y duración total con tolerancia 20 ms.
- gsap.min.js verificado con ensure_gsap o inyectado con --gsap para pruebas.

Antes de escribir, valida identificadores ASCII seguros, escenas coincidentes
y en orden, continuidad temporal, duración total y voz dentro de cada escena.
Toda escena con voz necesita palabras; se rechazan tiempos de palabra que
excedan la escena. Un MP4, mezcla o GSAP inexistente causa ComposeError.

## Árbol de salida

Comando de construcción:

    python -m clipoteca.studio.compose build \
      --script guion.json --timeline timeline.json \
      --clips build/fitted --mix build/mix.wav --out build/composition

Archivos escritos mediante temporales y os.replace:

- hf/index.html: composición local, sin URL HTTP(S), raíz con
  data-composition-id main, data-width, data-height, data-fps=30,
  data-start y data-duration.
- hf/hyperframes.json: schema, registry y rutas oficiales HyperFrames;
  media.autoProxy activado. Sus metadatos de configuración contienen URLs,
  pero el HTML no contiene ninguna.
- hf/meta.json: id y name clipoteca.
- hf/package.json: name hf, private true, type module.
- hf/assets/gsap.min.js, hf/assets/mix.wav y hf/assets/clips/ID.mp4.
- chunks.json: pares de palabras y tiempo de inicio absoluto.

El HTML y los manifiestos son deterministas byte a byte. Cada escena tiene
un elemento video.clip (track 2) y un bloque scene.clip (track 3);
el vídeo llena todo el cuadro con object-fit cover. Las captions son
track 7, la voz/música track 8 y el flash de corte track 9. Las capas
superpuestas intencionales llevan data-layout-allow-occlusion.
No hay marcas, logotipos, créditos, stickers ni tarjetas de cierre.

## Tipografía y espacios

| Aspecto | Cuadro | Título: left,width,top | Caption: left,width,top | Zona segura |
|---|---|---|---|---|
| 9:16 | 1080×1920 | 110,860,300 centrado | 120,840,1500 | 108,192,972,1728 |
| 16:9 | 1920×1080 | 120,760,230 izquierda | 260,1400,890 | 96,54,1824,1026 |
| 1:1 | 1080×1080 | 90,900,80 centrado | 90,900,900 | 54,54,1026,1026 |

Título base de 104/92/70 píxeles, intro 130/110/84; subtítulo
50/44/36; caption 60/54/46 según el aspecto. La fórmula para evitar
desbordamiento es int(min(base, width / (longest_word_len * 0.6))),
con k=0.55 para subtítulos. La fuente es Nunito, DejaVu Sans, system-ui,
sans-serif. Texto blanco con dos sombras de contraste; acento amarillo
#FFD23F.

Las palabras del título entran en secuencia al ritmo de medio pulso,
la escala del clip pasa de 1.06 a 1 en 0.45 s, los títulos salen antes
del siguiente corte y cada corte destella suavemente por 0.22 s.
La animación GSAP se registra pausada en window.__timelines["main"].

Los subtítulos son bloques de máximo 4 palabras, acotados además por
el ancho estimado, con cierre ante puntuación. Cada palabra comienza en
vo + inicio relativo, se ilumina con #FFD23F y vuelve a blanco antes
de terminar el bloque. La duración del bloque se limita por el inicio
del siguiente, el final de la línea y timeline.total. Todo texto pasa
por html.escape, incluidos los caracteres especiales y las comillas.

## Dependencias externas verificadas

- HyperFrames fijo 0.9.36, ejecución con npx --yes hyperframes@0.9.36.
- GSAP 3.15.1 vía npm pack gsap@3.15.1.
- SHA-256 del tarball:
  c70a3bcf656015f32b4f6456e686034564c90d64280db535cfbd84de4497d988
- Archivo extraído: package/dist/gsap.min.js, 73.357 bytes, SHA-256:
  3d1bea374ab1b703cc5c4105358eec63c4465efdc573931dc116e34a0a190c1b

La extracción usa tarfile para un único miembro, no extractall.
El caché se acepta solo si coincide el SHA. No se suben binarios ni
GSAP al repositorio. Las pruebas de cadena de suministro usan npm falso.

## Render y compuerta FFprobe

    python -m clipoteca.studio.compose render \
      --hf build/composition/hf --out build/composition/video.mp4

Se ejecuta npx --yes hyperframes@0.9.36 render -o TEMP/render.mp4
con cwd=hf y timeout configurable (1500 s predeterminado).
La salida y errores van a render.log con rutas de proyecto/temporales
enmascaradas. FFmpeg agrega la mezcla con las opciones:

    -map 0:v -map 1:a -c:v libx264 -preset slow -crf 20
    -pix_fmt yuv420p -profile:v high -c:a aac -b:a 192k
    -ar 48000 -ac 2 -shortest -movflags +faststart

La salida se escribe en un temporal. FFprobe exige perfil H.264 High,
yuv420p, ancho/alto del aspecto, 30 fps, AAC estéreo 48 kHz y duración
dentro de ±0.2 segundos de timeline.total. Solo entonces os.replace
publica video.mp4 y se escribe render.json con duration_s, width,
height, fps, size_bytes, render_s, mux_s, hyperframes y gsap_sha256.
Si falla una compuerta, no queda video.mp4.

## Pruebas y pendientes

Los tests base comprueban los tres aspectos, estructura y tiempos exactos,
escapado HTML, subtítulos, copia de assets, determinismo, errores de
inputs y npm, npx y ffprobe falsos. No utilizan red.

El test de render real se habilita con CLIPOTECA_M30_REAL_RENDER=1 cuando
existan npx, FFmpeg, Chrome y acceso al paquete fijado; genera tres vídeos
sintéticos testsrc2 de 1080×1920 y mix WAV sintético, y mide con FFprobe
el MP4 final. El orquestador debe añadir el test y un paso de render real
en el job studio de Actions, fuera del grant de M30.

Pendientes: incrustar una fuente propia y colocar la franja del título
según la región con menor presencia de sujeto (M30 posterior), además
de las compuertas definitivas de QA de M31. Las mediciones reales de
rendimiento se publican únicamente cuando existan.


## Evidencia de render local (23 de noviembre de 2026)

Con HyperFrames 0.9.36, GSAP 3.15.1 y Chrome Headless ARM64
se renderizaron tres clips móviles testsrc2, cada uno de 2 segundos,
a 1080×1920 con una mezcla sintética PCM16 de 6 segundos.
El navegador se seleccionó mediante HYPERFRAMES_BROWSER_PATH,
sin introducir una ruta privada en código ni en el PR.

La prueba real pasó y FFprobe midió:

- Video: H.264 perfil High, yuv420p, 1080×1920, 30/1 fps.
- Audio: AAC estéreo a 48000 Hz.
- Duración: 6.000 segundos en ambas pistas.
- Tamaño final: 3.740.792 bytes.
- Tiempo de HyperFrames: 28.898 segundos.
- Tiempo del mux FFmpeg: 7.409 segundos.
- SHA-256 de GSAP: 3d1bea374ab1b703cc5c4105358eec63c4465efdc573931dc116e34a0a190c1b.

Son métricas del ejecutor de prueba, no mediciones de Actions.
La ejecución real del CI debe activarse mediante un paso dedicado
en el job studio y documentarse por separado.
