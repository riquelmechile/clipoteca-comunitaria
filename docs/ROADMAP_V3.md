# ROADMAP v3 — la Clipoteca produce sus propios videos

**Objetivo:** que un agente mande un guion por API/MCP y la Clipoteca devuelva un MP4 terminado de cualquier tema, usando la propia Clipoteca como biblioteca de clips. Mismas reglas que v1 y v2: cada hito exige código + tests + evidencia; un hito no cuenta como terminado solo por tener documentos.

**Origen del código:** motor BeatForge V7 (código propio del autor del repositorio), portado a `pipeline/clipoteca/studio/` bajo la licencia MIT de este repo.

## Reglas fijas de v3

- Todo corre en GitHub Actions (`ubuntu-latest`). Ninguna máquina personal ni servidor propio.
- Voz: **Supertonic 3**, local al runner. Sin servicios de voz de pago ni claves de voz.
- Fuera de alcance: créditos en pantalla, gate de licencias y reglas de copyright (se definen después). Se conserva solo procedencia factual (id de clip y URL de origen), igual que el catálogo.
- Tema libre: sin plantillas de marca, sin fotos de producto, sin intro/outro de marca.
- Sin LLM dentro del motor: el guion lo escribe el agente que llama.
- Nunca imagen fija: una escena sin clip falla de forma explícita.
- Versiones fijadas: HyperFrames 0.9.36, GSAP 3.15.1, Tone.js 15.1.22, sherpa-onnx 1.13.8 (menor que 2: la 2.x cambia la API de Supertonic), faster-whisper 1.2.1 (modelo `small` int8), onnxruntime 1.31.1, CLIP ViT-B/32 ONNX int8, wav2vec2 `facebook/wav2vec2-xlsr-53-espeak-cv-ft` ONNX int8.
- Dependencias pesadas en un extra `studio` de `pyproject.toml` (como `embeddings`): el CI base no las instala. Modelos en caché de Actions.

## Arquitectura

Este diagrama describe el plan del servicio. De él existe hoy el workflow `render.yml`, de disparo manual y sin validar con corridas reales ([M32](M32_RENDER.md)); el Worker todavía no lo despacha. Las etapas ya integradas se detallan en [STUDIO.md](STUDIO.md).

```
Agente ──MCP/REST──▶ Worker ──workflow_dispatch──▶ render.yml
                                    plan
                                     ├─▶ voice   ┐
                                     ├─▶ music   ├─ en paralelo (jobs separados)
                                     └─▶ scene×N ┘  (matriz, un runner por escena)
                                          ▼
                       assemble: ajuste → mezcla → HTML → render → mux → QA
                                          ▼
                       publish: video a Release + ficha en data/videos/
                                + clips nuevos al catálogo (un solo escritor)
```

- Los jobs se pasan archivos por artefactos de Actions.
- Los jobs de escena no escriben en el catálogo. Un único job final publica los clips nuevos: la biblioteca crece con cada video.
- Mismo patrón que `ingest.yml`: `request_id` determinista, registro en `data/jobs/`, estado consultable.

## Bloque H — contrato

- [x] **M25 · Roadmap v3 + contrato del guion (1 PR).** Este documento. `schemas/video.schema.json` y `pipeline/clipoteca/studio/script.py`: `title`, `aspect` (9:16, 16:9, 1:1), `language`, `music {mood, bpm}`, `voice {voice, speed, lexicon}`, `scenes[] {id, type intro|fact|outro, title, sub, say, caption, query {es, en}, prompt, avoid}`. Validación estricta (rechaza claves desconocidas), 3 a 12 escenas, estimación de duración desde el texto, identificador determinista del guion. Sin render y sin dependencias nuevas. **Aceptación:** CI verde, fixtures de guiones válidos e inválidos. **Estado:** (#84) Código y pruebas en main.

## Bloque I — las tres piezas paralelas (independientes entre sí)

- [ ] **M26 · Voz con Supertonic 3 (1–2 PR).** Declara el extra `studio` con `uv.lock` actualizado. Modelo `sherpa-onnx-supertonic-3-tts-int8-2026-05-11` (release `tts-models` de k2-fsa/sherpa-onnx), fijado por SHA-256, con copia propia como asset de un Release del repo (`models-v1`) junto a su LICENSE (OpenRAIL-M), porque el proyecto original está archivado. `studio/voice.py`: motor sherpa-onnx, 10 voces (M1–M5, F1–F5), defaults voz `F1`, velocidad 1,2, 8 pasos. Supertonic es estocástico: cada toma se verifica y se repite si sale mala, con control PER (wav2vec2 contra fonemas espeak-ng, `per_max` 0,12, `max_tries` 3) y, si falta ese modelo, control por WER de Whisper (`line_wer_max` 0,2); se guarda la mejor toma. Léxico (solo afecta lo hablado), tiempos por palabra con faster-whisper, línea de tiempo real, caché por línea. Workflow manual `voice-sample`: las 10 voces con 10 frases fijas en español; mide PER, WER y segundos de cómputo por segundo de audio; deja los WAV como artefacto y la tabla en `docs/EVIDENCIA_VOZ_V3.md`. **Aceptación:** audio real generado en Actions con PER dentro del umbral; tabla de las 10 voces publicada; tests sin red con motor falso. **Estado:** hecho: motor, modelos, timeline y workflow de muestras (#85, #86, #87, #88, #99) / falta: tabla completa publicada de las diez voces.
- [x] **M27 · Música (1 PR).** `studio/music.py` + `assets/music.html`: Tone.js offline en Chrome headless; `head.wav`, `bed.wav` en bucle a compás, `hit.wav` para los cortes; moods upbeat, chill, epic; cubre la estimación ×1,25. **Aceptación:** WAV generado en CI con Chrome real, duración y compás verificados. **Estado:** (#89, #90) Render real en CI; métricas por mood pendientes.
- [x] **M28 · Plan y clip-scene sobre la biblioteca (2 PR).** `plan`: un `job.json` por escena y `plan.json` con duración estimada. `clip-scene` por escena: (1) busca en el catálogo por texto + CLIP sobre storyboard y fotogramas; (2) si no hay material, importa con el motor existente en modo sin persistencia; (3) elige la ventana con la duración de la escena (puntaje CLIP contra `prompt`, nitidez, sin cortes dentro); (4) encuadre cover al formato por saliencia + CLIP (9 posiciones), sin audio, 30 fps; (5) filtros: texto en pantalla (OCR), negro > 0,3 s, congelado > 1,5 s, lado corto ≥ 720 px; (6) registro anti-duplicados entre escenas (mismo clip/tramo, pHash) y candidato de respaldo sin búsqueda nueva. Sale `clip.mp4` + `result.json` (ok/fail, motivo, clip_id, URL de origen, tramo). **Aceptación:** fixtures; una escena resuelta solo desde el catálogo sin red externa; una escena sin material termina en `fail` explícito. **Estado:** (#91, #93, #94, #98) Incluye importación temporal; falta su validación integral en Actions (M32).

## Bloque J — ensamblado

- [x] **M29 · Ajuste y mezcla (1 PR).** Ajuste de cada clip al largo real de su escena: recorta; si falta, cámara lenta ≤ 1,25×; si falta mucho, 1,2× + ping-pong; > 2,4× se reporta para re-despacho; nunca imagen congelada. Mezcla: voz 0,3 s después de cada corte, ducking de la música, golpe en cada corte, fundido final, −14 LUFS y pico real ≤ −1 dBTP. **Aceptación:** tests con clips sintéticos para los tres modos de ajuste; loudness medido con `ebur128`. **Estado:** (#92, #96) Tests en CI; tiempos de procesamiento pendientes.
- [ ] **M30 · Composición y render (2 PR).** `studio/build_html.py` neutro: títulos por escena, subtítulos palabra a palabra, paleta, zona segura, franja del título donde hay menos sujeto. Render con `npx hyperframes@0.9.36` + GSAP en Chrome headless y mux `libx264 -preset slow -crf 20 -pix_fmt yuv420p -profile:v high`, AAC 192 k 48 kHz, `+faststart`. **Aceptación:** video real de 3 escenas renderizado en Actions; tiempo de render medido y publicado. **Estado:** hecho: compose.py y render real en CI (#95, #96, #99) / falta: fuente propia y franja de título según el sujeto.
- [x] **M31 · QA (1 PR).** 14 compuertas: hyperframes_check, layout_playwright, file_probe, decode_clean, blackdetect, freezedetect, silencedetect, loudness, whisper_wer (≤ 0,12), subtitle_sync, clips_all_scenes, clips_unique, clips_resolution, clips_black_freeze. Si una falla, no se publica nada y el motivo queda en el job. **Aceptación:** un fixture malo por compuerta la hace fallar. **Estado:** (#97) QA y layout real en CI.

## Bloque K — servicio

- [ ] **M32 · Workflow `render.yml` en paralelo + publicación (2 PR).** Jobs `plan` → `voice` ∥ `music` ∥ `scene` (matriz) → `assemble` → `publish`. Re-despacho automático de escenas fallidas hasta 2 rondas. Video en Release `video-AAAA-MM-DD-NNN` con `make_latest=false`; ficha en `data/videos/<id>.json` (guion, clips usados, reporte QA, tiempos). Los clips importados durante el video se publican al catálogo en un solo commit. **Aceptación:** video E2E por `workflow_dispatch` manual. **Estado:** hecho: módulo de publicación, esquema y tests (#100); workflow `render.yml` (#103) / falta: prueba integral en Actions con importación real.
- [ ] **M33 · API y MCP (1–2 PR).** `POST /v1/videos` (mismo Bearer de escritura que `/v1/import`), `GET /v1/videos/{id}`; tools MCP `render_video` y `get_video_status`. OpenAPI y cuotas actualizadas. **Aceptación:** un agente MCP manda un guion de 5 escenas y recibe la URL del MP4. **Estado:** falta: endpoints y tools MCP de video.
- [ ] **M34 · Cierre v3 (1–2 PR).** Tres videos reales de temas distintos (9:16, 16:9 y 1:1), minutos de Actions y tiempos por etapa medidos, `docs/ESTADO.md` y operación actualizados, versión 3.0.0 sincronizada en Python, Worker, OpenAPI, CLI, health y MCP. **Aceptación:** demostración E2E reproducible, CI verde. **Estado:** falta: evidencia integral y versión 3.0.0.

## Camino crítico

`M25 → (M26 ∥ M27 ∥ M28) → M29 → M30 → M31 → M32 → M33 → M34`

## Riesgos conocidos (se miden, no se suponen)

1. Supertonic sin mantención: el modelo funciona, pero no recibirá arreglos. Se fija versión y hash y se guarda copia propia.
2. Voz sin acento local y con tomas que a veces salen malas (por eso el control PER con reintentos).
3. Biblioteca chica: al principio casi toda escena va a importar; el costo baja a medida que el catálogo crece.
4. Tiempo total por video en Actions: sin medir hasta M30/M34.
