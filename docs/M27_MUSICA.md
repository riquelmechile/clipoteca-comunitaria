# M27: Música offline con Tone.js

El módulo `clipoteca.studio.music` compone una cama musical instrumental
determinista con Tone.js 15.1.22 en Chromium sin conexiones de red. Genera:

- `head.wav`: introducción y dos períodos musicales de 16 pulsos.
- `bed.wav`: cama ampliada repitiendo el segundo período, ya estabilizado.
- `hit.wav`: acento de corte con un golpe a 0,4 s del comienzo.
- `music.json`: clave de caché, parámetros de arreglo y tiempo de render.
- `music.wav` (fase track): cama final con acentos en cortes reales y fundidos.

Los moods aceptados son `upbeat`, `chill` y `epic`. No se descargan modelos,
archivos JS, canciones ni dependencias en tiempo de ejecución.

## Entradas

`plan.json` (producido por M28) contiene `version`, `script_id`, `aspect`,
`music: {mood, bpm}`, `bpm`, `beat`, `total_est` y `scenes`. Cada escena
contiene `id`, `type`, `voice_est`, `len_est`, `len_fetch` y `start_est`.
M27 utiliza mood, bpm, total_est y el inicio estimado de la segunda escena.
Los campos numéricos requeridos deben ser finitos. El intro termina al comenzar
la segunda escena o, si solo hay una, a los cuatro pulsos.

`timeline.json` (producido por M26 PR2) contiene `bpm`, `beat`, `total`,
`cuts` (lista de segundos) y `scenes` con `start`. La mezcla se ajusta al
tiempo real de esta línea de tiempo, sin estiramiento de la música.

## Fórmulas del arreglo

- `beat = 60 / bpm` segundos; `loop_len = 16 * beat`.
- `intro_end = scenes[1].start_est` o `4 * beat`.
- `head_s = round(intro_end + 2 * loop_len, 3)`.
- `loop_start = intro_end + loop_len`.
- `bed_s = round(ceil((total_est * 1.25 + 6) / beat) * beat, 3)`.
- `key = sha256(mood|bpm|round(intro_end,3)|v3|sha256(music.html)|sha256(Tone.js))[:16]`.

La clave no depende de `total_est` porque solo el largo del bucle cambia,
no el patrón musical. El WAV original del compositor tiene dos canales,
44 100 Hz y PCM de 16 bits. `write_bed` replica los períodos completos,
sin alterar el tempo ni convertir a otros formatos.

Al construir el track, cada corte coloca `hit.wav` desde 0,4 s antes.
La rampa de silencio del acento comienza 0,08 s después de ese punto y
dura 0,15 s. El track termina 0,5 s después del último corte final,
con ceros desde `timeline.total`; el fundido de salida se aplica en
los últimos 1,5 s antes de ese instante y el de entrada en 20 ms.
Si la cama estimada es corta, se extiende a `timeline.total + 2` segundos
y se actualiza `music.json` de forma atómica.

## Recursos y aislamiento

El archivo estático `assets/music.html` utiliza exclusivamente
`vendor/Tone.js`, copiado sin alteraciones del paquete
`tone@15.1.22`. El bundle ocupa 345 500 bytes y su SHA-256 es:

`e290952fa43d9a7a780182a83c6fccf44d79cb7ae2cba102ef1f2b9d98124e22`

Antes de navegar, Playwright intercepta todas las peticiones y permite
únicamente URLs `file://` cuya ruta resuelta esté dentro de
`studio/assets`; registra y rechaza todas las demás. Una petición
externa hace fallar el render aunque exista audio. Se usa la opción
`--allow-file-access-from-files` para que el script local cargue.
El ejecutable de Chrome puede especificarse en `CLIPOTECA_CHROME`,
con límite de 180 s configurable mediante `CLIPOTECA_MUSIC_TIMEOUT`.
Los imports pesados son perezosos para preservar las pruebas base.

## Uso

```bash
uv run --extra studio python -m clipoteca.studio.music render \
  --plan plan.json --out build/music
uv run --extra studio python -m clipoteca.studio.music track \
  --music build/music --timeline build/voice/timeline.json --out build/music.wav
```

Todas las salidas se reemplazan con archivos temporales y `os.replace`.
La caché omite el segundo render cuando coinciden la clave y los tres WAV.

## Evidencia

| Modo | Tiempo de render en Actions | RMS |
|---|---|---|
| upbeat | Pendiente de medir en Actions | Pendiente de medir en Actions |
| chill | Pendiente de medir en Actions | Pendiente de medir en Actions |
| epic | Pendiente de medir en Actions | Pendiente de medir en Actions |
