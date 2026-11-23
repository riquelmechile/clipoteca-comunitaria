# M26: voz con Supertonic 3

El render de voz usa `sherpa-onnx` y Supertonic 3. La configuracion predeterminada es
la voz F1, velocidad 1,2 y ocho pasos de generacion. Los imports pesados y las descargas
son perezosos: importar `clipoteca.studio.voice` no descarga ni carga modelos.

Cada linea se normaliza con un lexico que solo modifica el texto hablado. Se intentan
hasta tres tomas con velocidades levemente distintas. El control principal es PER
contra fonemas espeak-ng, con maximo 0,12. Mientras el modelo PER siga sin hashes
publicados, se informa `per_model_unpinned` y se usa Whisper small con WER maximo 0,2.
La mejor toma siempre se conserva; una toma fuera de umbral queda como
`below_threshold` y no detiene las demas escenas.

El recorte usa ventanas RMS de 10 ms, umbral 0,008, conserva 100 ms antes y 80 ms
despues, y aplica un fade-in de 4 ms. La cache por linea incluye texto hablado, voz,
velocidad, pasos, SHA del modelo y version del algoritmo. El resultado contiene
`audio/<scene_id>.wav` y `voice.json` con metricas, duraciones y estado por linea.

## Manifiesto y licencias

Los modelos se guardan en `CLIPOTECA_STUDIO_MODELS` o, por defecto,
`.cache/studio-models`. Las descargas aceptan solo HTTPS y hosts declarados, limitan
el numero de bytes, verifican SHA-256 antes de extraer y rechazan archivos tar con
rutas peligrosas, enlaces o dispositivos.

- Supertonic 3: OpenRAIL-M. Se conserva una copia en el release `models-v1` porque el
  proyecto original esta archivado.
- wav2vec2 espeak: Apache-2.0. Los dos hashes quedan pendientes del workflow de exportacion.
- faster-whisper small: MIT, revision de Hugging Face fijada en el manifiesto.

## Uso

```bash
uv sync --frozen --extra studio
uv run python -m clipoteca.studio.voice render --script guion.json --out build/voice
uv run python -m clipoteca.studio.voice sample --out build/voice-sample
```

`sample` genera diez frases para cada una de las diez voces, los WAV, `sample.json` y
una tabla Markdown.

## Evidencia

| Voz | PER medio | WER medio | Peor frase | RTF |
|---|---:|---:|---|---:|
| PENDIENTE | PENDIENTE | PENDIENTE | PENDIENTE | PENDIENTE |

La evidencia real se completa al ejecutar el workflow manual. El PR2 de M26 agregara
tiempos por palabra con faster-whisper y la linea de tiempo real.
