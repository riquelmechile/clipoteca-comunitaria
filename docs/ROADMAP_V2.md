# ROADMAP v2 - motor mas potente (post-1.0)

**Objetivo:** que el motor encuentre mas material real, entregue el mejor momento de cada video y no los primeros 20 s, y que los agentes elijan clips viendolos. Mismas reglas que v1: cada hito exige codigo + tests + evidencia; un hito no cuenta como terminado solo por tener documentos.

**Fuera de alcance de v2:** reglas de copyright, licencias y autorizacion comunitaria (se definen despues; se sigue conservando solo procedencia factual), web Astro, cuentas, creditos y pagos.

**Origen:** auditoria del 21-nov-2026 de broll-mcp, FootageFlow, sift-video, MediaCrawler, Youhai020616/douyin, Evil0ctal/Douyin_TikTok_Download_API y 0W-Tube. Ningun endpoint de terceros de este documento fue probado en vivo: M12 los mide.

**Licencias de codigo de referencia:** se puede copiar con atribucion desde broll-mcp (MIT), FootageFlow (MIT), Youhai020616/douyin (MIT), 0W-Tube (MIT, Java: solo como referencia) y Evil0ctal v5 (Apache-2.0). De sift-video (GPL-3.0) y MediaCrawler (licencia no comercial) **solo ideas, nunca codigo**.

## Bloque D - contrato y evidencia

- [x] **M11 . Roadmap v2 (1 PR).** Este documento en `docs/ROADMAP_V2.md`, enlace desde `README.md` y desde la seccion "Proxima evolucion" de `docs/ROADMAP.md`. Sin cambios de codigo. **Aceptacion:** CI verde, documento revisado. **Codigo integrado en main:** PR #31.
- [x] **M12 . Matriz declarativa + sonda real de fuentes (1-2 PR).** Capacidades por proveedor declaradas en un solo lugar (`search`, `preview`, `download`: supported / best_effort / unavailable), exportadas por `export-sources`. Workflow `probe` manual en Actions que pega una vez, sin secretos, a cada endpoint candidato de M16-M20 con `lluvia`, `rain`, `\u4e0b\u96e8`, `\u0434\u043e\u0436\u0434\u044c` y registra codigo HTTP, tiempo, cantidad de resultados y si trae URL de archivo. Endpoints: RuTube busqueda y `play/options`, OK.ru busqueda, VK token anonimo + busqueda, Library of Congress, Douyin busqueda web como invitado, TikTok busqueda web como invitado, Bilibili busqueda WBI como invitado. **Aceptacion:** `docs/EVIDENCIA_FUENTES_V2.md` con la tabla fechada, generada por el workflow; define que fuente corre en Actions y cual necesita runner propio (M20). **Codigo integrado en main:** PR #39.

## Bloque E - motor de corte

- [x] **M13 . Deteccion de escenas (1-2 PR).** Nuevo `pipeline/clipoteca/media/scenes.py`. Fallback sin dependencias: `ffmpeg -ss S -t D -i URL -vf "scale=320:-2,select='gt(scene,0.3)',showinfo" -an -f null -` leyendo `pts_time` de stderr, con `-reconnect 1 -reconnect_on_network_error 1 -rw_timeout`. Opcional PySceneDetect `AdaptiveDetector(min_scene_len=15)`, `adaptive_threshold=3.0`, `downscale=2`. Lectura remota por rangos HTTP sin bajar el video completo cuando el servidor acepta `Range`. Normalizacion: fusionar escenas <1,5 s, partir tomas >20 s en pasos de 10 s, maximo 120 escenas, ventana maxima 900 s. Referencia: broll-mcp `detect_worker.py`, `scenes.py`, `media.py`. Respetar SSRF y limites de `security/`. **Aceptacion:** fixtures con cortes conocidos (video largo con titulos al inicio, toma unica >20 s, video sin cortes) devuelven escenas esperadas; tests sin red. **Codigo integrado en main:** PR #38.
- [x] **M14 . Corte por escena, miniatura y storyboard (2 PR).** `normalize.py` recibe inicio y duracion: `-ss` antes de `-i`, `-t`, re-encode como hoy (corte a nivel de fotograma). Seleccion de escena sin modelos: puntaje simple de nitidez, movimiento y luminancia, descartando negro, titulos estaticos y creditos; ventana <=20 s. `thumbnail.py` toma el punto medio de la escena elegida en vez del 10 %. Storyboard: N fotogramas repartidos (WebP 320 px) publicados junto al clip, expuestos en `clip.schema.json`, API y MCP para que el agente mire antes de descargar (idea de pexels-mcp-pro `video_storyboard`). Un video largo puede dar mas de un clip, con dedup. **Aceptacion:** el fixture con titulos al inicio ya no publica los titulos; schema, OpenAPI y tools MCP actualizados; clip real E2E con storyboard. **Codigo integrado en main:** PR #49.
- [x] **M15 . Internet Archive v2 (1 PR).** `providers/internet_archive.py`: `advancedsearch.php` con `mediatype:movies AND format:(h.264 OR "h.264 IA" OR "512Kb MPEG4" OR MPEG4)`, `sort[]=downloads desc`, `rows=100`; dos etapas (todos los terminos en title/subject/description; si hay <25 resultados, cualquier termino); ranking local (titulo +3, subject +2, shotlist +1, log10(downloads)/2, bonus h.264); elegir derivado liviano por cadena `original` (h.264 > MPEG4 > 512Kb) en vez del archivo mas grande; usar 512Kb para escanear escenas; rate limit 1 req/s con `Retry-After`; validar que la respuesta corresponde a la consulta pedida. Referencia: broll-mcp `search.py` (`build_query`, `normalize_brief`, `score_hit`), `catalog.py` (`group_videos`, `best_cut`, `CUT_RANK`), `archive.py` (`TokenBucket`). **Aceptacion:** fixtures de busqueda y de listado de archivos; clip real publicado desde un documental largo usando M13-M14. **Codigo integrado en main:** PR #43.

## Bloque F - fuentes

- [x] **M16 . HLS generico + RuTube con archivo (1-2 PR).** Descarga de HLS m3u8 con FFmpeg (`-headers` para Referer), con tope de duracion y bytes, validando SSRF en playlist y segmentos. RuTube: `GET rutube.ru/api/search/video/?query=&page=1&limit=30` y `GET rutube.ru/api/play/options/{id}/?format=json&no_404=true&mq=all` con Referer de la pagina; tomar `video_balancer` (hoy se descarta). Errores de bloqueo vienen en `detail`. Referencia: 0W-Tube `SearchClient.newRutubeSearch`, `StreamResolver.inspectRutube`. **Aceptacion:** RuTube pasa de `SUPPORTED_SEARCH_ONLY` a `SUPPORTED_SEARCH_DOWNLOAD` si M12 lo confirmo; clip real `\u0434\u043e\u0436\u0434\u044c` publicado; tests HLS sin red. **Codigo integrado en main:** PR #40.
- [x] **M17 . OK.ru, VK Video y Dzen (2-3 PR).** OK.ru nuevo: `GET ok.ru/video/search?st.cmd=anonymVideo&st.ft=search&st.gsq=<q>&st.m=SEARCH` (JSON en atributo `data-props`; paginar con `POST ok.ru/web-api/v2/video/fetchSearchResult`); stream en `ok.ru/videoembed/{id}` (atributo `data-options`, `flashvars.metadata` o `metadataUrl`), MP4 o HLS. VK sin `VK_ACCESS_TOKEN`: `POST login.vk.com/?act=get_anonym_token` (cachear y renovar 60 s antes de `expired_at`), busqueda `api.vkvideo.ru/method/catalog.getVideoSearchWeb2` (v=5.282, `content_type=video`), stream con `video.getByIds` + `video_fields=files` (`files.hls`, `mp4_*`), headers Origin/Referer `https://vkvideo.ru`; el token propio queda como opcion. Dzen: `GET dzen.ru/api/web/v1/zen-search?...&type_filter=video` y `videoMetaResponse` de `dzen.ru/video/watch/{id}`, con jar de cookies; implementar o declarar el limite tecnico con prueba. Referencia: 0W-Tube `OkClient`, `VkWebClient`, `DzenClient`. **Aceptacion:** cada uno en `list_sources` con estado medido; clip real publicado desde al menos uno. **Implementacion M17:** adaptadores y pruebas offline incluidos; capacidades declaradas `best_effort` hasta ejecutar el smoke real en `main`. **Codigo integrado en main:** PR #52.
- [x] **M18 . Archivos e instituciones + dedup cruzada (2-3 PR).** Nuevos adaptadores: Library of Congress (`GET https://www.loc.gov/film-and-videos/?fo=json&at=results,pagination&c=20&q=<q>&sp=<pagina>`, video en `results[].resources[0].video`, saltar `download_restricted`, timeout 45 s, >=500 ms entre peticiones); Europeana (`api.europeana.eu/record/v2/search.json?query=&rows=40&media=true&qf=TYPE:VIDEO&cursor=*`, header `X-Api-Key`, archivo en `edmIsShownBy`); Coverr (`api.coverr.co/videos?query=&page=&page_size=&urls=true`, Bearer, `urls.mp4_download`); NARA (`catalog.archives.gov/api/v2/records/search?q=&limit=20`, header `x-api-key`); DPLA y Smithsonian Open Access (clave gratuita). Las que piden clave quedan `AUTH_REQUIRED` sin ella, como v1. Dedup entre proveedores: URL canonica + titulo normalizado + pHash existente. Referencia: FootageFlow `LibraryOfCongressProvider.swift`, `EuropeanaProvider.swift`, `CoverrProvider.swift`, `SearchDuplicateAnalyzer.swift`; heritage-research-mcp para DPLA y Smithsonian. **Aceptacion:** fixtures por proveedor; clip real desde Library of Congress; el mismo video desde dos fuentes no se publica dos veces. **Codigo integrado en main:** PR #42.
- [x] **M19 . Douyin y TikTok con busqueda web real (2-3 PR).** Hoy `douyin.py` y `tiktok.py` solo listan la cuenta autorizada por API oficial y `direct_media` devuelve None: reemplazarlos por busqueda web por palabra clave. Mismo diseno para ambos: funciona como invitado (cookie `ttwid` obtenida sola) y acepta cookie de sesion opcional como secreto (`DOUYIN_COOKIE`, `TIKTOK_COOKIE`); si como invitado responde, el secreto no se usa. Douyin: `/aweme/v1/web/search/item/` (video) y `/aweme/v1/web/general/search/single/`, parametros base webapp `aid=6383` + `msToken`; URL de archivo en `video.bit_rate[].play_addr.url_list[-1]` con `playwm`->`play`; detalle con `x-secsdk-web-signature`. TikTok: `/api/search/item/full/` con `search_id` para paginar. Firmas en Python puro, sin navegador. Referencia: Youhai020616/douyin `engines/api_client.py` (`search`, `_parse_bit_rates`, `get_download_url`, `_secsdk_sign`); Evil0ctal `src/dtk/signing/native/` (`abogus.py`, `xbogus.py`, `tiktok_sign.py`, `tokens.py`, `sm3.py`) y `platforms/tiktok/params.py`. Respuesta 2483 = sesion requerida: estado claro, no error generico. **Aceptacion:** fixtures de respuesta real; con cookie o como invitado segun M12, clip real `\u4e0b\u96e8` desde Douyin y `rain` desde TikTok; donde corre queda definido por M12/M20. **Codigo integrado en main:** PR #53, #55.
- [x] **M20 . Runner propio para fuentes bloqueadas en datacenter + Bilibili (1-2 PR).** Runner autoalojado de GitHub Actions en el PC de casa con etiqueta propia; el motor enruta a ese runner solo los proveedores que M12 marco como bloqueados desde Actions. Bilibili: busqueda `/x/web-interface/wbi/search/type` (`search_type=video`) con firma WBI (claves de `GET /x/web-interface/nav` -> `wbi_img`, tabla de 64 indices, MD5 -> `w_rid`), archivo via `/x/player/wbi/playurl` (avid, cid, qn, fnval, platform=pc), Referer `https://www.bilibili.com` en busqueda y descarga. Referencia: Evil0ctal `signing/native/wbi.py`, `wbi_keys.py`, `platforms/bilibili/params.py`. **Aceptacion:** sin el runner todo lo demas sigue funcionando en Actions; con el runner, clip real `\u4e0b\u96e8` desde Bilibili. **Codigo integrado en main:** PR #60.

## Bloque G - inteligencia

- [x] **M21 . Embeddings y puntaje de escena contra la consulta (2 PR).** Encoder de imagen CLIP ViT-B/32 + encoder de texto `sentence-transformers/clip-ViT-B-32-multilingual-v1` (512 dimensiones, es/en/zh/ru). Fotogramas por escena (~1 fps o 0,5 fps en videos largos), coseno contra la consulta, media movil de ~3 s y ventana de <=20 s con mejor promedio; reemplaza o combina el puntaje simple de M14. Vector por clip (float16, normalizado) guardado en `data/`. Medir tiempo por minuto de video en CPU de Actions y en GPU del runner propio y decidir donde corre. Idea tomada de sift-video, sin su codigo. **Aceptacion:** fixtures en los cuatro idiomas eligen la escena correcta mejor que M14; medicion publicada. **Codigo integrado en main:** PR #54, #55.
- [x] **M22 . Busqueda semantica en API y MCP (1-2 PR).** El Worker compara la consulta contra los vectores del catalogo por fuerza bruta (sin base de datos) y combina con la busqueda por termino actual. El texto de la consulta debe pasar por el mismo encoder de M21: definir y documentar donde se calcula. **Aceptacion:** "aguacero" encuentra clips de "lluvia" en los cuatro idiomas; contrato OpenAPI y MCP actualizado. **Codigo integrado en main:** PR #57, #58, #59.
- [x] **M23 . Tool "guion -> clips" (1 PR).** Herramienta MCP y endpoint que reciben una lista de escenas (`id`, `narration`, `query`, `orientation`, `min_duration`) y devuelven un clip por escena, primero del catalogo y si falta disparando importacion, marcando explicitamente las escenas sin material (nunca imagen en lugar de video sin avisarlo). Referencia del formato: broll-fetcher `scenes.json` / `scenes.with_media.json`; Footage-Engine `search_script_beat` como idea. **Aceptacion:** guion de 5 escenas resuelto E2E desde un agente MCP. **Codigo integrado en main:** PR #59, #60.
- [ ] **M24 . Cierre v2 (1-2 PR).** Smoke real sobre todas las fuentes, matriz final por proveedor e idioma, documentacion de operacion (incluido runner propio), version 2.0.0 sincronizada en Python, Worker, OpenAPI, CLI, health y MCP. **Aceptacion:** demostracion E2E reproducible, CI verde, informe de proveedores.

## Camino critico

`M11 -> M12 -> M13 -> M14 -> M15 -> M16 -> M17 -> M18 -> M19 -> M20 -> M21 -> M22 -> M23 -> M24`

M12 va antes de las fuentes porque decide que corre en Actions y que en el runner propio. M13-M14 van antes de las fuentes nuevas porque todo clip nuevo ya debe salir con corte por escena. Ninguna fuente se declara con descarga sin evidencia; si M12 o el hito muestran que no responde, se declara el limite tecnico, igual que en v1.


## Estado de cierre v2 (preparacion M24)

M11-M23: implementacion integrada en main mediante los PR indicados. M24 esta
**en curso**, sin tag/release/deploy. El workflow smoke.yml en main
(ref refs/heads/main, SHA bf0454baa76c7aa8e2528b51f38302b30581cf0d,
run_id 44972077685) midio 92 pruebas de 23 proveedores en cuatro idiomas
y 44 sondas de candidatos M12. De las 92, 43 requirieron credenciales;
se registraron 64 resultados de busqueda. Los datos completos estan en
data/reports/smoke-44972077685.json; las tablas regeneradas estan en
docs/MATRIZ_FUENTES_V2.md y docs/EVIDENCIA_FUENTES_V2.md.
La medicion no publica clips ni prueba CDN, runner propio o Worker 2.0.0.

**Pendientes para Zana:** probar Bilibili/runner propio solo tras evidencia
comparativa, aplicar manualmente el YAML de operacion que corresponda,
autorizar y medir el despliegue del Worker 2.0.0, ejecutar la consulta
semantica real y la demo E2E MCP de cinco escenas, y decidir tag/release.
M24 **permanece [ ]** hasta esa demo E2E reproducible. Consultar
docs/OPERACION_V2.md, docs/RUNNER_PROPIO.md y docs/DEMO_E2E_V2.md.

## Evidencia M24 r6 - segundo smoke real en main

Ejecucion smoke.yml por workflow_dispatch en refs/heads/main:
run_id 44977121143, SHA fd17e46aee3fb10a957425e5841cbe43ef572ed9
(merge de PR #65). Artefacto clipoteca-smoke-44977121143.
**Cobertura:** 92 filas de 23 proveedores y cuatro idiomas y 48 sondas
candidatas. Las busquedas principales observaron 118 resultados
(64 en el run anterior 44972077685). Internet Archive, RuTube,
OK.ru y VK Video entregaron 3 resultados por idioma desde Actions.
Las cifras no prueban descarga de clips, CDN ni soporte en todas las redes.
Los resultados y fallos completos constan en
data/reports/smoke-44977121143.json, docs/MATRIZ_FUENTES_V2.md
y docs/EVIDENCIA_FUENTES_V2.md.

Bilibili permanece silent_block; Douyin y TikTok session_required;
LoC presenta RATE_LIMITED zh/ru. Se proponen ensayos controlados en
runner propio aislado, NO se activa data/runner-routing.json.

**M24 sigue [ ] y NO se cierra con el smoke. Acciones de Zana:**
- Decidir y autorizar el deploy real del Worker 2.0.0.
- Aplicar manualmente el YAML del operador con controles de seguridad.
- Ejecutar y documentar demo E2E real REST/MCP, cinco escenas y assets.
- Decidir sobre runner propio tras comparacion de Actions y red aislada.
- Autorizar tag/release solo una vez cerradas las verificaciones anteriores.

Ningun tag, release o despliegue se realizo en M24 r6.
