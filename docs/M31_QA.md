# M31: QA de publicación con catorce compuertas

M31 decide si el vídeo renderizado está apto para publicar. No publica
contenidos, no sube al catálogo y no sustituye una falla por una advertencia.
Si una compuerta falla, el resultado global es pass=false, se escribe el
reporte y la publicación se bloquea. Los fallos individuales no interrumpen
las demás compuertas.

## Contrato de entradas

run_qa(work_dir) recibe por defecto:
- video.mp4: mezcla final renderizada M30.
- hf/index.html y chunks.json: HTML y subtítulos M30.
- timeline.json: M26, con bpm, beat, total, cuts y escenas
  {id, type, caption, start, len, vo, dur, words}.
- guion.json: M25, con title, aspect, language y scenes.
- scenes/ID/result.json: M28, con status y clip
  {clip_id, start, len, out_dur, src_w, src_h, out_w, out_h, phash}.
- fit/ID.mp4: clips ya ajustados por M29.

Todas las rutas admiten parámetros específicos en run_qa: video_path,
hf_dir, chunks_path, timeline_path, script_path, scenes_dir, fit_dir y
report_path. Un archivo obligatorio inexistente o un JSON inválido
produce QAInputError; la CLI termina con código 2. Si falta una escena
o un clip, se rechaza mediante las compuertas correspondientes y
se escribe el informe.

## Las catorce compuertas

| Compuerta | Fuente | Aceptación |
|---|---|---|
| hyperframes_check | npx --yes hyperframes@0.9.36 check en hf | código cero, Check passed, suma de N error(s) cero; cuenta los avisos de contraste need 3:1 |
| layout_playwright | Chromium headless, HTML, instantes 35% y 65% de cada escena y +0,16 s de cada chunk | al menos un texto qa-text asentado medido; ningún desborde, ninguna zona segura violada ni solapamientos |
| file_probe | ffprobe JSON, vídeo y audio | un H.264 del tamaño M30, un AAC; duración con diferencia menor a 0,35 s, máximo 60 s y tamaño menor a 100 MB |
| decode_clean | FFmpeg decodifica video.mp4 a null | stderr vacío, código cero |
| blackdetect | FFmpeg blackdetect=d=0.1:pix_th=0.10 | sin black_start |
| freezedetect | FFmpeg freezedetect=n=0.001:d=1.0 | sin freeze_start |
| silencedetect | FFmpeg silencedetect=n=-45dB:d=0.8 | silencio únicamente durante últimos 1,3 segundos |
| loudness_ebur128 | FFmpeg ebur128=peak=true y bloque Summary | integrado -14 ±1 LUFS y true peak <= -0,9 dBFS |
| whisper_wer | audio PCM 16 kHz mono y ASR faster-whisper small int8 | WER normalizado <=0,12 contra el texto escrito o contra el hablado (léxico aplicado), el menor de los dos; guarda primeras 400 letras |
| subtitle_sync | palabras del timeline, inicio absoluto vo+a, vs ASR | >=70% emparejadas, diferencia media <=120 ms y percentil 95 <=350 ms |
| clips_all_scenes | result.json, fit/ID.mp4 y HTML | todas las escenas status=ok, un video por escena, ningún img |
| clips_unique | result.json de cada escena y checks.check_global M28 | cada escena con status=ok, clip_id y pHash hexadecimal válido; sin conflictos por clips/rangos ni pHash con distancia <=6 |
| clips_resolution | dimensiones de result.json y compose.CONFIG | fuente corta >=720 o misma orientación con lado largo >=0,53 del destino; salida exacta |
| clips_black_freeze | checks.black_frozen M28 sobre cada fit | sin negro >=0,3 s ni congelación >=1,5 s |

Playwright reutiliza las zonas seguras de M30:
9:16 (108,192,972,1728); 16:9 (96,54,1824,1026);
1:1 (54,54,1026,1026). Toda petición ajena a file:// se bloquea.
Se esperan las fuentes y se recorre la línea de tiempo GSAP con
seek(t,false). Las cajas de texto se miden solo tras asentarse
sus animaciones para evitar falsos positivos en la entrada.

La transcripción se realiza UNA vez. El modelo real se resuelve
desde la revisión fijada en studio/models.json, con beam_size=5,
language del guion, word_timestamps=True, vad_filter=True,
threshold 0,3, speech_pad_ms 400, min_silence_duration_ms 500
y condition_on_previous_text=False. Las pruebas usan transcriptor
inyectado, sin descargar pesos.

En la sincronización, tras una pausa mayor de 250 ms o una palabra
alargada por Whisper, se compara el FINAL detectado frente al
final del subtítulo, en lugar de comparar el inicio arrastrado
hacia el silencio. Se usa SequenceMatcher sin autojunk y numpy
para media y percentil 95.

## CLI, salida y rechazo

    python -m clipoteca.studio.qa --work build/mi-video
    python -m clipoteca.studio.qa --work build/mi-video --report /tmp/qa_report.json

Ejemplo ilustrativo del reporte (la salida real contiene los 14 checks):

    {
      "version": 1,
      "script_id": "sha256-del-guion",
      "video": {"sha256": "sha256-de-video.mp4", "size_bytes": 12345678},
      "pass": false,
      "failed": ["blackdetect", "clips_unique"],
      "checks": {
        "blackdetect": {"pass": false, "starts": [1.2]},
        "clips_unique": {"pass": false, "conflicts": {"lago": "duplicate_phash"}}
      },
      "time_s": 5.12
    }

El archivo qa_report.json se escribe por temporal y reemplazo atómico.
Al empezar se borra el reporte de una corrida anterior: si esta corrida
termina con código 2 no queda ningún reporte, y un pass=true viejo nunca
respalda a otro vídeo. El campo video ata el veredicto a los bytes exactos
de video.mp4 (M32 debe comparar ese sha256 antes de publicar).
La salida es reproducible con iguales datos y ejecutores, excepto time_s.
Códigos: 0 si todas aprueban, 1 si alguna rechaza, 2 si faltan
entradas principales. Los errores por compuerta son acotados y
no incluyen rutas absolutas del host.

## Pruebas, límites y pendiente de integración

tests/test_qa_m31.py incluye un caso bueno y un caso malo por compuerta,
excepciones independientes, un ASR compartido, códigos CLI, reporte
y determinismo. Usa npx/ffmpeg/ffprobe falsos sin red para pruebas base.
Las pruebas con FFmpeg real crean archivos testsrc2 y color sintéticos;
se saltan si FFmpeg no está disponible. La prueba Chromium real de
texto fuera de zona segura y solapado requiere el flag
CLIPOTECA_M31_REAL_LAYOUT=1 y un navegador instalado.

No se incorporó la compuerta de licencias/créditos del motor original,
porque no pertenece al contrato de las 14 compuertas de Clipoteca v3.
En un PR posterior el orquestador deberá agregar el test M31 al
job studio de GitHub Actions. Este trabajo no puede editar .github/.
M32 deberá exigir qa_report.json.pass=true antes de publicar.

## Límite de un minuto

El motor produce vídeos de hasta 60 segundos. El número vive en un solo
lugar (studio/script.py, MAX_VIDEO_SECONDS) y lo aplican tres etapas:
plan rechaza un guion cuya duración estimada lo supera, timeline rechaza
la voz real que lo supera (antes de gastar render) y file_probe rechaza
el vídeo final. El tope de tamaño es 100 MB: el mux de M30 limita el
vídeo a 10 Mbps (-maxrate 10M -bufsize 20M sobre -crf 20), así que un
minuto ocupa como máximo unos 77 MB con el audio.

## Correcciones de la revisión del orquestador

- clips_unique pasaba en vacío y cuando faltaba el pHash: ahora exige
  resultado y pHash válido por escena.
- layout_playwright pasaba si la página no tenía ningún texto medible.
- whisper_wer comparaba solo contra el texto escrito, aunque la voz lee
  el texto con el léxico aplicado ("km/h" suena "kilometros por hora").
- silencedetect ignoraba un silence_start negativo.
- Un qa_report.json viejo sobrevivía a una corrida que fallaba al empezar.
