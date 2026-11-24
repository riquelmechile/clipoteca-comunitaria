# M32: workflow de render en GitHub Actions

`.github/workflows/render.yml` produce un video a partir de un guion. Se
dispara a mano (`workflow_dispatch`); todavía no hay API ni tool MCP que lo
llame (eso es M33).

## Entradas

| Entrada | Qué es |
|---|---|
| `script_json` | El guion completo en JSON (`schemas/video.schema.json`). El tope de GitHub para las entradas de un disparo es de 65 535 caracteres. |
| `publish` | Apagado por defecto. Apagado = ensayo: el video queda como artefacto de la corrida y no se publica nada. Encendido = publica solo si el control de calidad aprueba. |
| `request_id` | Opcional. Las corridas con el mismo identificador no corren a la vez: la segunda espera a la primera. |

## Jobs

    plan ──┬── voice ──┐
           ├── music ──┼── assemble ── publish
           └── scene × N ┘

- **plan**: valida el guion, escribe un trabajo por escena y busca en el
  catálogo. Rechaza de entrada un guion que estime más de 60 segundos.
- **voice**: voz con control de pronunciación y línea de tiempo por palabra.
- **music**: música generada sin red en Chromium.
- **scene** (uno por escena, en paralelo): descarga el modelo de
  evaluación verificando su suma y lo deja en caché; intenta con el catálogo. Si esa
  escena no tiene material utilizable, prepara los recursos de traducción,
  importa material nuevo sin publicarlo y vuelve a intentar con lo
  importado. Una escena sin clip no detiene a las demás.
- **assemble**: junta todo; si dos escenas eligieron el mismo clip, repite
  la de menor puntaje excluyendo los ya usados (hasta dos rondas); exige un
  clip por escena; ajusta, mezcla, compone, renderiza y corre las 14
  compuertas. Si aprueba, hace un ensayo de publicación en disco. Sube el
  video y los reportes como artefacto `video-<run_id>` aunque QA rechace,
  para poder ver por qué.
- **publish**: solo con `publish` encendido, QA aprobado y en `main`. Es el
  único job con permiso de escritura y corre de a uno
  (`concurrency: clipoteca-publish`), porque el commit al catálogo y el
  número del Release del video son compartidos.

Cada job deja su parte en el resumen de la corrida: el plan, el clip de
cada escena con los descartes y su motivo, la tabla de compuertas con sus
datos y los tiempos por etapa.

## Seguridad

- El guion es texto no confiable: llega a los pasos por variable de
  entorno y nunca se interpola en el shell.
- Los identificadores de escena los valida el plan y se usan siempre
  entre comillas.
- Las credenciales de fuentes solo se entregan al paso de importación.
  El token con permiso de escritura solo existe en el job `publish`.
- Las acciones de terceros están fijadas por SHA y ningún job deja el
  token guardado en la configuración de git.
- El job `publish` comprueba que el MP4 que recibe tenga el mismo SHA-256
  que el que aprobó el control de calidad en `assemble`, antes de las
  verificaciones propias del módulo de publicación.

## Artefactos

Los intermedios (plan, voz, música y escenas) se guardan 7 días; el video
con sus reportes, 14; el conjunto publicable, 3.

## Qué está comprobado y qué no

Comprobado fuera de Actions, con los mismos comandos del job `assemble`
ejecutados sobre una corrida real de cinco escenas: resolución de un clip
repetido a propósito, ajuste, mezcla, composición, render, las 14
compuertas, tiempos y ensayo de publicación.

Pendiente de comprobar en Actions, con una corrida real del workflow: el
reparto en jobs y artefactos, la caché de modelos y, sobre todo, la
importación de material nuevo dentro del flujo de video, que hasta ahora
solo se probó con fuentes simuladas.
