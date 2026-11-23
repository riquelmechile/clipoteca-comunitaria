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
velocidad, idioma, pasos, SHA del modelo y version del algoritmo. El resultado contiene
`audio/<scene_id>.wav` y `voice.json` con metricas, duraciones y estado por linea.

## Manifiesto y licencias

Los modelos se guardan en `CLIPOTECA_STUDIO_MODELS` o, por defecto,
`.cache/studio-models`. Las descargas aceptan solo HTTPS y hosts declarados, limitan
el numero de bytes, verifican SHA-256 antes de extraer y rechazan archivos tar con
rutas peligrosas, enlaces o dispositivos.

- Supertonic 3: OpenRAIL-M. Se conserva una copia en el release `models-v1` porque el
  proyecto original esta archivado.
- wav2vec2 espeak: Apache-2.0. Los dos hashes estan fijados en el manifiesto; la instalacion se verifica por SHA-256.
- faster-whisper small: MIT, revision de Hugging Face fijada en el manifiesto.

Punto abierto del lock: `rapidocr-onnxruntime==1.2.3` incorpora
`opencv-python==5.1.0.95` ademas del pin directo
`opencv-python-headless==5.0.0.93`; ambos distribuyen el modulo `cv2`. El
orquestador agregara el `uv.lock` regenerado para el extra `studio` y resolvera
esa duplicacion en una integracion posterior, sin cambiar pins en este PR.

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

## Linea de tiempo real

\`python -m clipoteca.studio.timeline --script guion.json --voice build/voice
--out build/voice/timeline.json\` mide cada WAV con su duracion real, remuestrea
a 16 kHz y usa faster-whisper con tiempos por palabra. Los empates exactos
usan \`whisper\`; los bloques no coincidentes se interpolan (\`whisper+align\`);
sin palabras ASR, los tiempos son proporcionales (\`proporcional\`).
El archivo \`timeline.json\` contiene id, tipo, titulo, subtitulo, caption, duracion,
palabras, ASR, WER, metodo, inicio, longitud cuantizada a compas, inicio de voz,
total, cortes, bpm, beat y script_id. La voz comienza a 0,3 s despues del corte.
Cada escena conserva su WAV original, sin estirar hacia una duracion objetivo;
se agregan 0,6 s o 1,2 s al outro y se aplica el minimo de 4 pulsos o 3,5 s.

El texto del caption conserva las cifras originales. Solo el texto pronunciado
aplica primero el lexico configurable y luego \`spell_numbers\` en espanol. El
resultado expandido se usa para sintesis, cache y evaluacion PER/WER.
\`voice.json\` se limpia al iniciar un render nuevo y distingue audio invalido,
tomas, mejor toma, duracion y umbrales de PER y WER. La extraccion tar limita
cantidad de miembros y bytes descomprimidos; redirecciones y puertos externos
no pueden eludir el listado permitido; temporales y archivos de descarga
se limpian tras uso, incluso en fallos.

## Evidencia medida (orquestador)

- Voz F1 con Supertonic real, velocidad 1,2: 137 caracteres en 7,47 s,
  44,1 kHz y aproximadamente 0,31 s de computo por segundo de audio.
  A velocidad 1x, \`SEC_PER_CHAR = 0.065\` queda confirmada.
- La frase «mide 2.847 metros» pronunciada con digitos dio PER 0,16–0,21;
  con «dos mil ochocientos cuarenta y siete» dio PER 0,07–0,08.
- «En 1960 … 9,5 grados» pronunciada con digitos dio PER 0,21–0,22;
  al expandir a «mil novecientos sesenta … nueve coma cinco» dio PER 0,02–0,05.
  Mediciones con voz F1, velocidades 1,2 y 1,05. El umbral PER_MAX es 0,12.
