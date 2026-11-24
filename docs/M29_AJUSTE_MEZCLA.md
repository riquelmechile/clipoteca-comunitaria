# M29: ajuste de clips y mezcla de audio

M29 recibe la línea de tiempo real de M26, los clips mudos que producirá M28
PR2 y la música con golpes/fundido de M27. Ajusta el vídeo a cada escena sin
relleno con fotogramas congelados y crea una mezcla PCM16 a 48 kHz, estéreo,
con voz clara, música atenuada bajo la voz y loudness verificado. No edita
M28 ni descarga recursos.

## Contratos de entrada

`timeline.json` contiene `bpm`, `beat`, `total`, `cuts` y escenas con
`id`, `type`, `dur`, `start`, `len` y `vo`. Los tiempos son segundos:
`len` es la longitud real de la escena después de conocer la duración del
audio; `vo` es la posición de la voz en la línea de tiempo, 0,3 s después
del corte. Los IDs de escena usados como nombre de archivo deben contener
solo letras ASCII, números, guiones o guion bajo, con máximo 64 caracteres,
y nunca `..` ni separadores.

- Clips de entrada: `clips/<id>.mp4` sin audio y ya recortados al formato.
- Voz: `voice/audio/<id>.wav`, uno por escena hablada, WAV PCM16 mono
  o estéreo. Los archivos de otra frecuencia se remuestrean con FFmpeg
  a 44,1 kHz antes de componer la pista.
- Música: `music.wav` de M27, 44,1 kHz estéreo 16 bits, ya contiene los
  golpes en `cuts` y el fundido de salida.

## Ajuste sin congelaciones

`fit_plan(D, Lr)` mide el largo de la pista de vídeo con `ffprobe`
(`stream.duration`, no duración del contenedor) y calcula
`factor = Lr / D`, con tolerancia de 1e-6 en los límites.

| Factor | Modo | Tratamiento |
|---|---|---|
| factor ≤ 1 | `trim` | Recodificar y recortar a la longitud requerida |
| 1 < factor ≤ 1,25 | `slow` | `setpts=factor*PTS`, desaceleración máxima 1,25 |
| 1,25 < factor ≤ 2,4 | `pingpong` | `setpts=1.2*PTS` + versión inversa concatenada |
| factor > 2,4 | `too_short` | Error explícito, requiere nuevo material |

Los tres modos recodifican a H.264, 30 fps, `yuv420p`, sin audio,
`libx264 -preset veryfast -crf 19`, con límite de
`Lr + 0.1 s` y comprobación posterior con `ffprobe`. La duración de
salida debe cubrir `Lr`, permitiendo un fotograma (1/30 s) de tolerancia,
sin superar `Lr + 0.2 s`. Nunca se utiliza `tpad`, `clone`,
`loop` ni `freeze` para completar escenas.

`fit_all(timeline, clips_dir, out_dir)` intenta cada clip de forma
independiente, escribe los MP4 exitosos y un `fit.json` atómico:

```json
{
  "scenes": {
    "intro": {"mode": "trim", "factor": 0.8, "source_s": 5, "out_s": 4.1}
  },
  "failed": {"fact-1": "too_short"}
}
```

Los valores del ejemplo ilustran solo el contrato de campos, no son
mediciones del proyecto. Cualquier fallo queda por escena para
re-despacho; el CLI sale con código 3 cuando hay `failed` y 0 cuando
se ajustaron todas.

## Mezcla de voz y música

`build_voice_stem` coloca cada WAV en la muestra
`int(vo * 44100)`, suma si hay superposición y recorta exactamente a
`int(total * 44100)`. Convierte estéreo a mono y remuestrea archivos
de otra frecuencia mediante FFmpeg. Normaliza el pico a 0,89 y guarda
`voice.wav` mono, 44,1 kHz PCM16. Escenas mudas (`dur=0` o
`wav` declarado ausente) se omiten. Falta de un WAV de escena hablada
o voz completamente silenciosa provoca `MixError`.

La mezcla emplea los dos canales de entrada previstos, no mezcla el audio
de los vídeos. Su grafo FFmpeg aplica:

1. Música recortada a `total` con `volume=0.55`.
2. Voz con high-pass a 80 Hz, de-esser, compresor 2,4:1,
   ecualización de 200 Hz y 3.500 Hz y limitador.
3. `sidechaincompress` desde la voz sobre la música
   (threshold 0,05; ratio 3,5; attack 22 ms; release 420 ms).
4. `amix` de voz y música atenuada a 48 kHz.
5. `loudnorm` en dos pasadas: primera mide I, TP, LRA, umbral y offset;
   segunda aplica parámetros medidos y limitador final a 0,89.

`measure_loudness` mide el audio final con FFmpeg y devuelve
`lufs`, `true_peak`, `lra`. La compuerta dura exige
`|lufs - (-14.0)| <= 1.0` y `true_peak <= -0.9 dBTP`.
Si no pasa, no se publica el nuevo `mix.wav`. La duración debe
coincidir con `timeline.total` con tolerancia de 20 ms.

Se entrega `mix.wav` estéreo PCM16 a 48 kHz y `mix.json` con
`lufs`, `true_peak`, `lra`, `duration_s`, `music_volume`,
`target_lufs`, `target_tp` y `voice_lines`. Las escrituras son
atómicas con temporales más `os.replace`. El número de líneas
cuenta solo WAV realmente utilizados.

## Ejecución

```bash
python -m clipoteca.studio.fit \
  --timeline build/voice/timeline.json --clips build/clips --out build/fitted

python -m clipoteca.studio.mix \
  --timeline build/voice/timeline.json \
  --voice build/voice --music build/music.wav --out build/mix.wav
```

No se instala ningún paquete nuevo: se utiliza `numpy`, biblioteca
estándar y ejecutables `ffmpeg` y `ffprobe`. Los procesos externos
reciben listas de argumentos, nunca comandos mediante shell, con límite
de tiempo y errores acotados.

## Verificación y pendientes

Las pruebas usan fuentes sintéticas `testsrc2` de 320×180 a 30 fps y
tonos `sine` generados por FFmpeg. Verifican los tres modos con
`freezedetect`, codec, duración, formato, fallos de re-despacho,
muestras exactas de voz, conversión de frecuencia, medición de LUFS
y reducción de energía musical durante los tramos hablados.

Los tests dependientes de FFmpeg/FFprobe se omiten limpiamente cuando
no existen. El orquestador agregará `tests/test_fit_m29.py` y
`tests/test_mix_m29.py` al job `studio` del CI, fuera del grant
de este PR.

| Evidencia en Actions | Estado |
|---|---|
| Tiempo real de ajuste trim/slow/pingpong | Pendiente de medir en Actions |
| Tiempo de mezcla y normalización | Pendiente de medir en Actions |
| Métricas del ejemplo con archivos definitivos | Pendiente de medir en Actions |


## Segunda vuelta A: rutas, diagnóstico y publicación de mezcla

FFprobe y FFmpeg reciben rutas resueltas a absolutas en los
comandos de ajuste, resampling, análisis y mezcla, incluso cuando
el directorio o archivo empieza con signo menos. Los mensajes
de subprocess no publican rutas absolutas del proyecto o del
sistema de archivos temporal: se sustituyen por indicadores
neutros antes de producir FitError o MixError.

Al empezar un intento de mezcla se invalida el mix.json anterior.
La compuerta de sonoridad y formato se valida mientras el WAV final
y el JSON están todavía en el directorio temporal. Se escribe
mix.json primero a un archivo temporal, luego se reemplaza mix.wav
y por último mix.json. Si el segundo reemplazo falla, se restaura
el WAV anterior, si existía, o se elimina el nuevo. Nunca queda un
nuevo mix.wav como éxito sin el recibo correspondiente. Si falla
la compuerta, no se publica ningún artefacto nuevo y los temporales
se limpian automáticamente.

Las CLIs conservan los mismos argumentos y códigos de salida,
y además informan en stdout JSON de resultados (fit.json o mix.json)
al aprobar, o un objeto status=error y clase de error al rechazar.
Los contratos y campos de fit.json y mix.json se mantienen idénticos.
