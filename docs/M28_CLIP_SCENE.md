# M28: planificación, selección y materialización de clips

Este hito prepara cada escena de un guion M25 para encontrar material de la biblioteca
existente sin red durante el plan. PR2 descarga los assets asignados,
puntúa fotogramas con CLIP local, los encuadra y entrega cada clip verificado.

## Contratos escritos

`python -m clipoteca.studio.plan --script guion.json --out build/plan` valida
el guion con `parse_video_script` y escribe:

- `build/plan/plan.json`: `version=1`, `script_id`, título, aspecto, idioma,
  música (`mood`, `bpm`), voz (`voice`, `speed`), `bpm` como número,
  `beat`, `total_est` y escena por escena.
- `build/plan/scenes/<id>/job.json`: un contrato de trabajo por escena con
  título, subtítulo, voz, caption, aspecto, estimaciones, consultas `es/en`,
  prompt visual, `avoid`, `negatives`, `min_sim=0.22`, `min_prob=0.5`,
  `top_n=4` y lado corto mínimo de 720 píxeles.

La CLI imprime una sola línea JSON con `script_id`, lista ordenada de IDs y
`total_est` para una matriz de Actions. Los IDs de carpeta se vuelven a validar
para evitar separadores de ruta y `..`, incluso con un guion construido a mano.
Los archivos se reemplazan mediante temporales y `os.replace`.

Las estructuras internas del guion son inmutables y su hash SHA-256 mantiene
el contrato original de M25, incluidos los valores previos de los fixtures.
El parser rechaza claves no string y surrogates Unicode inválidos con la ruta
del campo. Un `music.bpm` como `120.0` equivale canónicamente a `120`.

## Fórmulas del plan

El cálculo comienza en `estimate_durations(script)` de M25:

- `beat = 60 / bpm`
- `extra = 1.2` segundos en outro y `0.5` en otras escenas.
- `L = ceil((voice_est + extra) / beat - 1e-9) * beat`
- Mínimos: `3.5` segundos en outro y `4 * beat` en las demás.
- `len_est = round(L, 3)`.
- `len_fetch = round(len_est + max(1.0, 0.12 * len_est), 2)`.
- `start_est` acumula cada `len_est`, redondeado a tres decimales.
- `total_est` es la suma de las duraciones estimadas, redondeada a dos decimales.

El tiempo de búsqueda permite material adicional para que el ensamblador
posterior pueda recortar el clip tras conocer la duración real del audio.
El ajuste a la duración real se hace posteriormente en M29.

## Ventanas visuales y CLIP

`clipscore.ClipScorer(model_dir)` trabaja solo con un directorio local que
contenga `vision_model_quantized.onnx`, `text_model_quantized.onnx` y
`tokenizer.json`. Si falta alguno, falla explícitamente: jamás descarga.
ONNX Runtime usa `CPUExecutionProvider`, sesiones perezosas con lock y número
de hilos `CLIPOTECA_ORT_THREADS` o `max(2, CPU//2)`.
Texto: minúsculas, máximo 77 tokens, salida normalizada. Imagen: escala bicúbica
con lado mínimo 224, recorte central, RGB y normalización CLIP, lotes de 32.
Las métricas por fotograma incluyen varianza laplaciana, brillo, desplazamiento
por correlación de fase y diferencia absoluta respecto al anterior.

`windows(A, pemb, L, start, nemb)` analiza ventanas cada 0,5 segundos,
con fotogramas a 2 fps. Descarta ventanas con menos de 80 % de muestras y
penaliza cortes internos, oscuridad, temblor y escenas estáticas. La similitud
robusta descarta el cuarto inferior de cosenos; con embeddings negativos,
la probabilidad usa softmax de `100*coseno` entre prompt y distractores.

Puntaje:

```text
10 * (sim - 0.2) + 2 * prob + 0.8 * sharp
- dark - shake - static - 0.6 * internal_cuts
```

`fit_mode` devuelve `window` si la duración cubre el largo, `whole`
si cubre al menos el 42 %, y sin ajuste si es demasiado corta.
`choose_window` distingue `no_window`, `low_sim` y `low_prob`
(`min_sim=0.22`, `min_prob=0.5`). Los empates se resuelven por inicio.

## Biblioteca sin red

`load_catalog(path)` carga el formato de `data/clips.json`: solo clips
publicados con `storage.asset_url` y campos de medio válidos; orden por ID,
con recuento de entradas malformadas y no publicadas.

`text_score` compara palabras completas de cuatro o más caracteres de
consultas españolas e inglesas con título, descripción, tags y título fuente,
ignorando mayúsculas y tildes. Cada palabra distinta coincide por +0,5;
cada término `avoid` presente resta 6.

`candidates` exige una duración de al menos el 42 % de `len_est`, lado
corto de al menos 720, puntaje positivo y URL de asset existente.
`assign_scenes` procesa primero escenas con menos candidatos; solo asigna
un titular libre por clip. Los respaldos no pueden ser titulares de otra
escena. Una escena sin opción queda en `needs_import` explícito, nunca
como falso éxito. Todo es determinista y sin red.


## Catálogo y asignaciones M28 PR2

El lector conserva solo entradas publicadas con ID, URL del asset y
media.duration_seconds, width, height, sha256 y size_bytes válidos.
No exige FPS, códec ni otros campos que la selección no utiliza.
Acepta duración mayor a 20 s y cuenta como inválidos los ID repetidos
tras el primero válido. Cada candidato incorpora sha256 y size_bytes
para comprobar la descarga. La resolución se acepta por lado corto
de al menos 720 píxeles o, si la orientación coincide con el destino,
por lado largo de al menos 53 % del lado largo final.

Plan con catálogo (sin --catalog preserva plan.json anterior):

    python -m clipoteca.studio.plan --script guion.json --out build/plan --catalog data/clips.json

Genera assignments.json: version=1, script_id y scenes, donde cada ID
contiene status=catalog|needs_import, primary (o null) y backups.
La misma asignación se guarda en scenes/ID/assignment.json.
Los titulares y respaldos se reparten antes del trabajo en paralelo:
ningún clip se repite como titular; los respaldos no incluyen titulares
de otras escenas. Se usa primero la escena con menos candidatos.

## Runner por escena

    python -m clipoteca.studio.clipscene --job scenes/ID/job.json --assignment scenes/ID/assignment.json --out scenes/ID

--exclude clip-id puede repetirse. Cada runner es independiente,
sin archivo de registro ni coordinación compartida. El proceso lee
primero el titular y luego los respaldos. Descarga en temporal usando
models.download_asset (HTTPS y hosts permitidos, redirecciones seguras,
SHA-256 y tamaño exacto), o el descargador inyectado en tests.

FFprobe comprueba resolución y duración del stream. Para el CLIP
local se decodifica vídeo a 2 fps y 384 píxeles de ancho en BGR,
se calculan métricas y embeddings por fotograma, y se detectan
cortes internos usando FFmpeg (select scene > 0,3). Las ventanas
se ordenan con el puntaje existente y se filtran por
min_sim=0,22 y min_prob=0,5. Una fuente de al menos 0,42 veces
la longitud se puede usar entera, sin relleno fijo aquí.

La vista previa pasa OCR en dos fotogramas (25 % y 75 % de la ventana)
ampliados dos veces. Se rechaza texto alfanumérico de 3+ caracteres
con confianza >=0,75. El OCR por defecto es RapidOCR importado
solo cuando hace falta y permite inyectar una función en tests.

El encuadre siempre es cover, sin barras, y tiene destinos
9:16=1080x1920, 16:9=1920x1080 y 1:1=1080x1080.
Saliencia por residuo espectral en hasta cuatro fotogramas determina
el foco mediano; si es degenerada se usa el centro. En recortes
horizontales CLIP compara nueve posiciones en hasta tres fotogramas,
y solo cambia el foco si mejora más de 0,004 en puntuación media.
FFmpeg codifica H.264, libx264 veryfast CRF19, yuv420p, 30 fps,
sin audio, con +faststart.

Control final: salida con duración >= max(2, 0,42*L), negro
con blackdetect desde 0,3 s, congelación con freezedetect
desde 1,5 s, OCR a 20/50/80 % del clip y pHash del fotograma central.
La primera opción aceptada se mueve atómicamente a clip.mp4.

result.json contiene version, id, status=ok, used=primary|backup,
tried, time_s y clip: clip_id, source_url, asset_url, start, len,
out_dur, fit, score, sim, prob, crop=[fx,fy,fw,fh], src_w, src_h,
out_w, out_h, phash. Un fallo deja status=fail,
reason=needs_import|no_candidate y tried con motivos por candidato:
download_failed, low_resolution, too_short, low_sim, low_prob,
no_window, text_in_preview, short_output, black, frozen,
on_screen_text, processing_failed, excluded o timeout.
El fallo no deja clip.mp4. Se borran los temporales. El límite
CLIPOTECA_SCENE_TIMEOUT (predeterminado 600 s) evita trabajo infinito.

CLI: código 0 si ok, 3 en fallo con result.json, 2 para entrada
inválida. No filtra rutas absolutas del runner en los mensajes.

## Unicidad global posterior

check_global compara exclusivamente escenas con status=ok.
Conflicto si el clip_id coincide y los tramos se pisan
(abs(start_a-start_b) < max(len_a,len_b)), o si la distancia
Hamming entre pHash centrales es <= 6. Se retiene la escena
con mayor score y, en empate, el ID menor en orden estable.
Devuelve el mapa de perdedoras con duplicate_clip o
duplicate_phash. No usa estado compartido.

## Límites y evidencia

La importación de nuevo material cuando no haya candidatos queda
para un PR posterior, sin persistencia durante render. M30 incorpora
franja de título, composición y render. No se inventan mediciones
de tiempo ni coste: evidencia de Actions pendiente de medir.
Las pruebas generan vídeos FFmpeg testsrc2, y utilizan descargador,
CLIP y OCR inyectados; no descargan modelos ni abren red. FFmpeg
y OpenCV se omiten limpiamente cuando no están disponibles.
