# M28 PR1: planificación de escenas y selección local

Este hito prepara cada escena de un guion M25 para encontrar material de la biblioteca
existente, sin descargar vídeos ni abrir conexiones de red. Es la primera parte de M28;
no produce todavía un vídeo.

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
No hay estiramiento a la duración objetivo en este PR.

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

## Pendiente de M28 PR2

La descarga real del asset, muestreo y análisis CLIP de fotogramas,
ventanas y encuadre `cover` por saliencia, detección OCR de texto en pantalla,
negro prolongado, congelados, anti-duplicado pHash, selección de respaldos,
importación temporal sin persistencia y CLI `clip-scene` quedan para M28 PR2.
Aquí no se fabrican clips ni se inventan medidas de rendimiento.

## Pruebas

Los tests son sintéticos y locales. Los que requieren OpenCV se saltan
explícitamente cuando el extra `studio` no está instalado. Ni ONNX ni
tokenizer cargan modelos en las pruebas de unidad; se inyectan sesiones falsas.
