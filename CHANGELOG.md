# Historial de cambios

## No publicado

- M25: roadmap v3 y contrato del guion para producción futura de videos; esquema JSON estricto, validación offline, defaults, estimación de duración e ID determinista. Sin render ni cambio de versión.
- M26 (PR1): voz con Supertonic 3 en `studio/voice.py`: motor sherpa-onnx, control PER con reintentos y respaldo WER, léxico, caché por línea y manifiesto de modelos con descarga verificada. Extra `studio` en `pyproject.toml`. Workflows manuales `studio-models` y `voice-sample`, y job `studio` en CI. Sin cambio de versión.
- M26: modelo fonético (wav2vec2 int8) publicado en el Release `models-v1` y fijado por tamaño y sha256 en `studio/models.json`; verificado con voces reales (PER 0,05–0,08 con cifras escritas en palabras).
- M26 (PR2): tiempos por palabra con Whisper y `timeline.json` cuantizado al compás (`studio/timeline.py`); cifras a palabras antes de la síntesis (`spell_numbers`); control fonético activo por defecto con descarga verificada del modelo; endurecimiento de descarga, extracción y caché. Sin cambio de versión.
- M27: música con Tone.js offline en Chromium headless (`studio/music.py` + `assets/music.html`): `head.wav`, `bed.wav` en bucle a compás, `hit.wav` en cada corte y pista final con fundidos; render sin red. El job `studio` de CI instala Chromium y renderiza de verdad. Sin cambio de versión.
- M28 (PR1): plan por escena (`studio/plan.py`: `plan.json` y un `job.json` por escena, largos al compás), puntaje CLIP y elección de ventana (`clipscore.py`, `windows.py`) y selección sobre el catálogo sin red ni repetir clip (`library.py`). El guion parseado pasa a ser inmutable. La duración estimada se calcula sobre el texto hablado (cifras y léxico ya expandidos). Sin cambio de versión.
- M28: modelo CLIP ViT-B/32 (ONNX int8, `Xenova/clip-vit-base-patch32`, MIT) fijado por tamaño y sha256 en `studio/models.json`; workflow manual `studio-models-clip` que lo agrega al Release `models-v1` sin sobrescribir nada.
- M29: ajuste de cada clip al largo real de su escena (`studio/fit.py`: recorte, cámara lenta hasta 1,25× o ida y vuelta sin cuadro repetido; nunca imagen congelada) y mezcla final (`studio/mix.py`: voz + música con atenuación bajo la voz, −14 LUFS y pico −1 dBTP medidos). Sin cambio de versión.
- M28 (PR2): `clip-scene` (`studio/clipscene.py`, `framing.py`, `checks.py`): por escena descarga candidatos verificados por sha256, elige ventana con CLIP, descarta texto incrustado con OCR, encuadra al formato y deja `result.json` + `clip.mp4`; sin candidato válido falla con `needs_import`. Sin cambio de versión.
- M30: composición y render (`studio/compose.py`): HTML con HyperFrames 0.9.36 + GSAP 3.15.1 (no se incluye en el repositorio; se baja de npm y se verifica por sha256), títulos, subtítulos con palabra resaltada, render en Chrome headless y mux H.264 + AAC con compuertas de `ffprobe`. Sin cambio de versión.
- CI: el job `studio` corre también los tests de M28 (PR2), M29 y M30 con el extra instalado y renderiza de verdad un video de tres escenas con HyperFrames.
- M31: control de calidad de publicación (`studio/qa.py`) con 14 compuertas que fallan cerradas (estructura HyperFrames, layout en Chromium, archivo, decodificación, negro, congelado, silencio, sonoridad, WER, sincronía de subtítulos y cuatro de clips) y `qa_report.json` atado al sha256 del video. Límite único de 60 s por video (plan, línea de tiempo y QA), tope de 100 MB y mux limitado a 10 Mbps. El job `studio` corre sus tests y la compuerta de layout real. Sin cambio de versión.
- M28 (PR3): importación sin persistencia (`studio/importer.py`): cuando la biblioteca no tiene material para una escena, busca y baja con el motor de la v2 usando escritor y almacenamiento locales (nada se publica), deja `imported.json` como manifiesto y candidatos `local_path` que `clip-scene` evalúa con las mismas compuertas (`--local-root`). Sin filtro de aspecto; los descartes del motor cuentan como «sin resultados», no como error. Sin cambio de versión.
- Segunda vuelta A (correcciones de revisión): lectura en español de cifras con apócope y género («veintiún años», «veintiuna horas»), decimales largos, horas, fechas, fracciones, rangos («diez a veinte»), negativos, monedas y ordinales; léxico por defecto sin siglas de marcas. Línea de tiempo con tolerancia de coma flotante y `script_id` de la voz verificado. Música con plazo total y solo archivos locales propios. Ajuste y mezcla con rutas seguras, errores sin rutas del sistema y par `mix.wav`/`mix.json` publicado de forma atómica. Composición construida en un directorio temporal y sin seguir symlinks. Sin cambio de versión.
- M32 (PR2): publicación del video terminado (`studio/publish.py`): solo si el control de calidad aprobó esos mismos bytes, publica en un único commit los clips importados que el video usó, sube el video a un Release propio `video-AAAA-MM-DD-NNN` y escribe la ficha `data/videos/<id>.json` (esquema `schemas/video-record.schema.json`). Idempotente ante cortes y con ensayo local `--dry-run`. Sin cambio de versión.
- CI: instalación de ffmpeg con límite de tiempo y reintentos (acción `install-ffmpeg`) y tope de 20 minutos para el job `python`, tras tres cuelgues del runner en ese paso.

## 2.0.6 (descubrimiento equitativo y reparto de fuentes)

- Descubrimiento por proveedor: idiomas secuenciales para evitar ráfagas 429,
  hasta 16 proveedores simultáneos y timeout individual por llamada.
  El tope duro de la ronda se calcula en función de oleadas y tiempos
  individuales (máximo 180 s). El antiguo plazo fijo de 15 s ya no corta
  idiomas pendientes por posición en la cola.
- Fuentes sin credenciales conocidas se registran AUTH_REQUIRED sin ocupar slots;
  se mantienen CircuitBreaker, bypass no bloqueante de YouTube y el presupuesto
  extendido opcional del navegador TikTok (gate deshabilitado por defecto).
- Se intercalan candidatos por proveedor antes de aplicar el máximo global.
  Publicación en primera ronda con una ventana por candidato; segunda ronda
  recupera ventanas adicionales si otras fuentes no aportan clips válidos.
  Deduplicación, calidad, límites y verificación de archivos intactos.
- Versión sincronizada en API, Worker, Python, OpenAPI y lockfiles.
- Los releases de lote de clips (batch-AAAA-MM-DD-NNN) se crean con
  make_latest=false, para no quitarle la marca Latest al release de version.

## 2.0.5 (TikTok session and bounded browser fallback)

- ingest.yml and smoke.yml forward repository TIKTOK_COOKIE and optional
  DOUYIN_COOKIE through their pipeline steps. No token is embedded in code.
- TikTok keeps native signing and optionally attempts a session-scoped
  Playwright search if the native route fails or returns no results.
  Browser installation is limited to the applicable workflows and cached.
- Browser response capture is bounded by page/candidate count and response
  bytes; expired/invalid sessions degrade to sanitized AUTH_REQUIRED.
- The source capability remains best_effort: the live verification run
  45080343083 returned HTTP 200/status_code 0 but no search items.
  MediaRef download could not yet be validated in that run.
- The TikTok session is opt-in: workflows forward TIKTOK_COOKIE only when the
  repository variable TIKTOK_SESSION_ENABLED is '1'. It ships disabled, so no
  browser is installed and no session is used until a live probe passes.
- Python, Worker, OpenAPI and lockfiles use version 2.0.5.
- Cierre de v2: README rehecho con portada, consola y diagrama animados
  (SVG puro, SMIL/CSS, texto en trazados, sin recursos externos), nuevo
  docs/ESTADO.md como referencia unica de estado, indice docs/README.md
  que separa lo vigente de lo historico, y M24 cerrado en ROADMAP_V2.md.
- Evidencia de cierre sobre main 9d85365: smoke de busqueda 45082009984
  (92 sondas, 13 fuentes con resultados) y smoke de descarga real
  45082001946 (13 DOWNLOAD_OK, incluido Bilibili). Reportes guardados en
  data/reports/. Worker 2.0.5 desplegado y verificado con /v1/health.
- Demostracion de punta a punta: importacion real de "cascada" desde el
  Worker (run 45082441011, cuatro idiomas, tres clips publicados) y guion
  de cinco escenas resuelto 5/5 por REST y por MCP.


## 2.0.4 (expansion multilingue tolerante a calidad)

- Las traducciones rechazadas por calidad se registran como avisos publicos
  translation:<idioma> y la busqueda continua con idiomas validos,
  siempre incluyendo el idioma original. No se inventan terminos.
- Los errores de modelos, pins o paquetes siguen abortando la ingesta.
- Etiquetas de busqueda opcionales con errores de calidad ya no descartan
  clips cuyos titulos y descripciones bilingues si pasan la validacion.
- Version 2.0.4 sincronizada en Python, Worker, OpenAPI y lockfiles.

## 2.0.3 (desbloqueos medidos desde GitHub Actions)

- Dzen: sesion anonima como bolknote/0W-Tube (MIT): las redirecciones SSO
  (sso.dzen.ru, sso.passport.yandex.ru) solo dejan cookies anonimas; nunca
  se visitan y se repite la URL original (max 4 intentos). Pagina de video
  hasta 8 MiB. Streams en el CDN VK/OK (vd*.okcdn.ru) permitidos; ese CDN
  firma la URL con srcAg=CHROME y responde 400 a otro User-Agent, asi que
  MediaRef lleva un user_agent opcional que usan HLS y MP4 (sin CR/LF).
- Bilibili: busqueda con cabeceras de navegador (UA, Origin, Referer,
  Accept-Language) y SIN cookies de invitado: medido en Actions, con
  buvid3/buvid4/b_nut la busqueda vuelve vacia y sin ellas trae 20
  resultados en es/en/zh/ru. El cid sale de /x/player/pagelist porque
  /x/web-interface/view, wbi/view y la pagina HTML dan 412 desde Actions.
  Espejos upos-*.akamaized.net permitidos.
- Bilibili pasa a SUPPORTED_SEARCH_DOWNLOAD (download best_effort): smoke
  45051277263 DOWNLOAD_OK; en 45051899129 (main 1fb0d1f) el espejo Akamai no
  alcanzo 32 MiB en 90 s. Dzen DOWNLOAD_OK en ambos.
- Douyin y TikTok: medido con Chromium real en Actions, la busqueda por
  palabra exige sesion iniciada (Douyin 2483 + captcha, TikTok 403 + login).
  Sin cambios de codigo en esta version.
- Version 2.0.3 en Python, Worker, OpenAPI, lockfiles y pruebas.
- Worker movido a una cuenta de Cloudflare propia de la Clipoteca:
  https://clipoteca-worker.clipoteca.workers.dev (antes sc-plasticov).
  Tokens nuevos de alcance minimo; despliegue sin depender del PC.

## 2.0.2 (correcciones de descarga, sin deploy ni tag)

- Smoke downloads: archivos MP4/WebM se prueban con Range de 8 MiB y
  validacion de magic, sin exigir tamano total menor al limite. Si el
  servidor ignora Range, el lector corta localmente a 8 MiB. HLS toma
  segmentos con maximo 8 MiB o 20 segundos, validando playlist,
  hosts DNS y firma MPEG-TS. Temporales borrados, sin publicar.
- OK.ru: HLS real dentro de flashvars.metadata.hlsManifestUrl y videos
  con URL firmada sin extension, usando solo hosts OK permitidos.
- VK Video: omite entradas region-restringidas del Web2, selecciona
  solo metadata del ID solicitado y admite CDN publico *.okcdn.ru.
- Dzen: redirecciones de watch limitadas a 3 saltos con DNS y
  allowlist exacta por salto. SSO externo de Yandex sigue bloqueado.
- Odysee: permite resolver streaming_url HTTPS aunque el video total
  supere el limite de ingest; el downloader real conserva su cap.
- LoC: smoke respeta timeout especifico de adaptador hasta 45 s.
- Topes: el ingest real acepta videos de hasta 1 GiB (antes 350 MiB)
  con limite de tiempo de descarga hasta 600 s. El smoke downloads
  toma muestras de 32 MiB (antes 8 MiB) con plazo de 90 s.
- Playlists HLS: tope 256 KiB -> 4 MiB (RuTube largos superaban el
  limite en el smoke 45041573596).
- Capacidades: download pasa a supported en library_of_congress, ok_ru
  y vk_video tras DOWNLOAD_OK real en el smoke 45041573596 sobre main
  f1489d0 (11 proveedores OK). RuTube y Dzen siguen best_effort.
- RuTube: playlists HLS de hasta 20000 lineas (antes 1200); el smoke
  45042490505 fallo con "HLS playlist too large" en videos largos.
- OK.ru: busqueda y embed siguen hasta 3 redirecciones HTTPS dentro de
  ok.ru/okcdn.ru/mycdn.me, cada salto con DNS e IP fijada; fuera de la
  allowlist se rechaza (smoke 45042490505: "provider redirect rejected").
- Version 2.0.2 en Python, Worker, OpenAPI, lockfiles y pruebas.
  Ninguna capacidad se promociona; requiere otro smoke en main.

## 2.0.1 (preparado, sin deploy ni tag)

- Worker: defaults de CATALOG_URL y CATALOG_JOBS_URL usan
  raw.githubusercontent.com sin alterar wrangler.toml; sus overrides
  siguen teniendo prioridad. sources.json se lee por raw en MCP.
  El cache de origen puede retrasar clips y estados de jobs ~5 min.
- Smoke: modo opt-in downloads (o all --include-downloads), una
  descarga real acotada por proveedor, corte temporal de hasta 20 s,
  tope de 8 MiB, borrado de archivos y resultado sanitizado. Nada se
  publica y sources.json no se cambia.
- Version Python, Worker, OpenAPI, CLI, health y MCP sincronizada a 2.0.1.
  Sin commits a main, sin despliegue, tag o release en este PR.

## 2.0.0 (preparacion de cierre, noviembre 2026; NO release ni deploy)

**Alcance:** M11-M23 fusionados en main y preparacion M24 en curso. El numero
2.0.0 se sincroniza en la rama de preparacion, pero este cambio no crea tag,
release de GitHub ni despliega un Worker. El smoke live en main se ejecuto
como run 44972077685; la demo E2E y el despliegue siguen pendientes.

### M11 - Roadmap y definicion del contrato (PR #31)
- Roadmap v2 y camino critico para motor de corte, fuentes y herramientas MCP.

### M12 - Fuentes declarativas y sonda de proveedores (PR #39)
- Capacidades por fuente, snapshot, candidatos y sonda de cuatro idiomas;
  los estados de fixtures no se interpretan como conectividad live.

### M13 - Deteccion de escenas (PR #38)
- Deteccion de cortes y degradacion segura por FFmpeg, limites de escena,
  rangos y casos sin cortes, con pruebas offline.

### M14 - Corte, thumbnail y storyboard (PR #49)
- Seleccion de mejores fragmentos, miniatura por escena y fotogramas de
  storyboard; recuperacion y deduplicacion de fuente.

### M15 - Internet Archive v2 (PR #43)
- Descubrimiento ordenado, ranking y seleccion de derivados de video.

### M16 - HLS + RuTube (PR #40)
- Descargas HLS con proteccion de red, seguridad de playlists y segmentos,
  RuTube con clasificadores de bloqueo y fallos.

### M17 - OK.ru, VK Video y Dzen (PR #52; integraciones anteriores revisadas)
- Fuentes y clasificacion best_effort, tokens y reproductores sujetos a smoke.
  Los resultados live de cada fuente siguen sin afirmarse.

### M18 - Instituciones, archivos y dedup (PR #42)
- Library of Congress, Europeana, Coverr, NARA, DPLA, Smithsonian y
  deteccion entre proveedores con identidad y datos verificables.

### M19 - Douyin/TikTok (PR #53, #55)
- Busqueda web invitado y firma nativa con paginacion y fallback; cookies
  opcionales. El YAML de cookies pendiente esta en OPERACION_V2.

### M20 - Bilibili WBI + runner (PR #60; correcciones PR #61)
- PR #60: Firma WBI, ruta DASH/MP4, UA/Referer, CDNs validadas,
  seleccion declarativa de runner y tests offline.
- PR #61: Preferencia HTML5 MP4 antes de DASH, backups CDN, lock de WBI,
  bloqueos de IP, recibos de fuentes diferidas y seguridad del runner
  publico. Bilibili queda search-only hasta smoke real.

### M21 - Embeddings de escenas (PR #54, #55)
- CLIP multilingue, vectores f16 por clip, ranking contra consulta,
  degradacion por CPU y dual-write de indices.

### M22 - Busqueda semantica (PR #57, #58, #59)
- Worker REST/MCP con RRF, corpus de shards con checksums, cache acotado,
  cuota por isolate, Unicode casefold y consulta de encoder fuera de Worker.
- PR #61 (M24 preparacion): limite de vectores de consulta por guion;
  tras el tope mantiene resultados lexicales.

### M23 - Guion a clips (PR #59, #60)
- `POST /v1/script` y MCP `resolve_script` con 1..50 escenas, clips video
  con fuentes/preview/storyboard, busqueda, matching y reutilizacion
  explicita, importacion existente, cuotas, resumen y estados mixtos.

### M24 - Trabajo preparado, aun incompleto
- Version 2.0.0 sincronizada en Python, Worker, OpenAPI, CLI, health y MCP.
- Operacion integrada en docs/OPERACION_V2.md; procedimientos de runner en
  docs/RUNNER_PROPIO.md; matriz generada desde fuentes declaradas y
  docs/DEMO_E2E_V2.md como receta de validacion.
- **Smoke live documentado:** workflow smoke.yml en main SHA
  bf0454baa76c7aa8e2528b51f38302b30581cf0d, run_id 44972077685,
  artefacto clipoteca-smoke-44972077685. 23 proveedores x 4 idiomas
  (92 pruebas, 64 resultados de busqueda, 43 AUTH_REQUIRED) y 8 candidatos
  (44 llamadas). Reporte data/reports/smoke-44972077685.json;
  docs/MATRIZ_FUENTES_V2.md y docs/EVIDENCIA_FUENTES_V2.md.
- Search declarativo pasa a supported solo para Dzen (12 resultados en
  la prueba principal) y Library of Congress (20 en sonda candidata).
  No hay descarga de clip demostrada. Bilibili sigue search-only;
  data/runner-routing.json se mantiene sin rutas propias.
- **Pendientes de Zana:** YAML del operador, comparacion de runner aislado,
  despliegue del Worker 2.0.0, consulta semantica y demo E2E MCP reales,
  tag/release solo con autorizacion posterior. M24 sigue incompleto.


- **Segundo smoke M24 r6 (sin release):** run_id 44977121143,
  refs/heads/main, SHA fd17e46aee3fb10a957425e5841cbe43ef572ed9,
  artefacto clipoteca-smoke-44977121143; 92 consultas principales,
  48 candidatas y 118 resultados principales (64 en primer smoke).
  Internet Archive, RuTube, OK.ru y VK encontraron 3 resultados por
  idioma en Actions; Bilibili silent_block, Douyin/TikTok session_required,
  LoC rate_limited zh/ru. Evidencia registrada en matriz, reporte
  y docs/EVIDENCIA_FUENTES_V2.md; sin promover descarga.
  PROPUESTAS de runner aislado en docs/RUNNER_PROPIO.md, sin activacion.
- **Sigue M24 [ ]:** Zana debe aprobar deploy Worker 2.0.0, aplicar YAML
  del operador, demostrar E2E real, decidir runner propio y autorizar
  tag/release. Este PR no hace deploy, tag ni release.

---


## 1.0.0 (preparacion de lanzamiento, 21-11-2026)

### Plataforma y catalogo (M0-M2)
- Contratos de catalogo, indices y assets publicos de GitHub Releases; publicacion idempotente y recuperacion.
- Pipeline Python/FFmpeg con normalizacion, miniaturas, metadatos y deduplicacion.

### Busqueda y proveedores (M3-M6)
- Expansion y busqueda en espanol, ingles, chino y ruso, con modelos Argos verificados y aislamiento de fallos.
- Adaptadores y clasificacion explicita de las 15 fuentes del roadmap: descarga donde esta permitida, solo busqueda o `AUTH_REQUIRED` cuando faltan credenciales.
- Pruebas y diagnostico de conectividad real; Bilibili puede bloquear IP de datacenter.

### API, MCP e ingesta (M7-M9)
- Worker Cloudflare con REST `/v1/*`, MCP HTTP `/mcp`, salud del catalogo y jobs.
- GitHub Actions para importacion y publicacion con reintentos, proteccion de secretos y deduplicacion.
- Aportes publicos mediante Issues, con validaciones de URL/SSRF, metadatos, comentarios de estado y retiro de etiquetas de error tras exito.

### Estabilizacion y operacion (M10)
- Smoke tests, documentacion de operacion, recuperacion y matriz de proveedores.
- Ajustes de compatibilidad Argos real, traduccion y deteccion de salidas degeneradas; User-Agent en descargas.
- Version sincronizada a `1.0.0` entre paquete Python, Worker, OpenAPI, CLI, salud y MCP.

### Evidencia y limites
- Worker desplegado en `https://clipoteca-worker.sc-plasticov.workers.dev` y 8 clips reales catalogados, incluidos Pexels, PeerTube e Issue #26 (`clip-000008`).
- Ejecuciones documentadas en [informe v1](docs/INFORME_V1.md), con pruebas de ingesta, importacion Worker, PeerTube, aporte y smoke.
- No se afirma que las 15 fuentes devuelvan clips en todos los idiomas, ni que existan mediciones de facturacion. Esta entrada **no** constituye un tag, GitHub Release ni despliegue nuevo.
