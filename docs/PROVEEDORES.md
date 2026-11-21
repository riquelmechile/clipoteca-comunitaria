# Clipoteca v1: matriz técnica de proveedores M4

Fecha de verificación documental: 20-11-2026. El estado es de **implementación y tests con fixtures**, no prueba de descarga real ni de disponibilidad contractual. El servidor registra errores por consulta e idioma; cada candidato solo se publica si tiene un archivo verificable y supera FFmpeg, dedup y GitHub Releases.

| Fuente | Búsqueda | Metadatos | Archivo directo | Autenticación | Limitación/estado |
|---|---|---|---|---|---|
| Pexels | `GET https://api.pexels.com/v1/videos/search` | `video_files` / detalle | MP4 HTTPS escogido sin aumentar resolución | `PEXELS_API_KEY` obligatoria | `AUTH_REQUIRED` sin clave; `SUPPORTED_SEARCH_DOWNLOAD` solo si hay variante real |
| Pixabay | `GET https://pixabay.com/api/videos/` | `hits[].videos` | `large/medium/small/tiny` MP4 | `PIXABAY_API_KEY` obligatoria | `AUTH_REQUIRED` sin clave; `SUPPORTED_SEARCH_ONLY` por candidato sin MP4 |
| Wikimedia Commons | MediaWiki `generator=search`, namespace File | `imageinfo` | `imageinfo.url`, MIME video | Pública; User-Agent identificable | Filtra imágenes y MIME no video; no fuerza falsas descargas |
| Internet Archive | `/advancedsearch.php` (`mediatype:movies`) | `/metadata/{identifier}` | `/download/{identifier}/{file}` | Pública | Rechaza HTML/imagen, tamaño > 350 MiB o sin derivado; prioriza MP4 de mayor tamaño admisible |
| NASA | `https://images-api.nasa.gov/search?media_type=video` | `collection.items[].data` | `/asset/{nasa_id}` entrega MP4 real | `NASA_API_KEY` opcional; por defecto `DEMO_KEY` | `SUPPORTED_SEARCH_ONLY` si manifiesto no tiene MP4 |
| Library of Congress | `GET https://www.loc.gov/film-and-videos/?fo=json&at=results,pagination` | `results[].resources[0]` | `video`, con fallback `video_stream` | Pública | Excluye `download_restricted`; timeout 45 s y al menos 500 ms entre solicitudes |
| Coverr | `GET https://api.coverr.co/videos?query=…&page=…&page_size=…&urls=true` | `hits[].urls` | `mp4_download`, con fallback `mp4_preview` | `COVERR_API_KEY` obligatoria (Bearer) | `AUTH_REQUIRED` sin clave; solo conserva URL MP4 HTTPS directa |

**Idiomas:** cada ronda de búsqueda de concepto reconocido envía términos `es/en/zh/ru` al adaptador habilitado. Algunos servicios no interpretan correctamente los cuatro idiomas, pero se registra el idioma/término utilizado, sin afirmar soporte semántico inexistente. Pixabay usa `lang` cuando es aceptado; chino se consulta con `lang=zh`, siempre con el término original.

**Errores tipados:** `SUPPORTED_SEARCH_DOWNLOAD`, `SUPPORTED_SEARCH_ONLY`, `AUTH_REQUIRED`, `RATE_LIMITED`, `UNAVAILABLE`, `TIMEOUT`. El estado del adaptador indica capacidad; no equivale a que todo resultado sea descargable. Los HTTP 429/500, respuestas vacías, timeout y HTML falso se verifican sin red en pytest.

**Límites/seguridad:** solo HTTPS, DNS público y redirecciones reevaluadas; Content-Length y bytes reales limitados; formatos MP4/WebM/OGV/Ogg con firma reconocida. El cliente valida además la IP efectivamente conectada (cuando el transporte expone `network_stream.get_extra_info('server_addr')`), comparándola con el DNS público previamente autorizado. Si no puede verificarla en producción, falla cerrado. La comprobación se repite tras cada redirect y aplica plazo total de descarga; el transporte debe someterse a una prueba real con proxy/red antes de despliegue.

**Modelos Argos:** se declara `ARGOS_MODEL_CACHE_DIR` y `ARGOS_MODEL_PINS_JSON` con pares `es_en`, `en_es`, `en_zh`, `zh_en`, `en_ru`, `ru_en`. Por cada par se exige `version` y SHA256 real del paquete `.argosmodel`. El archivo `data/argos-pins.json` contiene los seis SHA-256 **calculados desde descargas reales** de `argos-net.com/v1` el 20-11-2026. `clipoteca.translate.download_models` descarga los faltantes a temporal y valida hash antes del reemplazo atómico. El workflow ejecuta esta verificación al iniciar, incluso si hay caché. La ausencia o huella equivocada falla cerrado. El diccionario incorpora 200 conceptos traducidos que funcionan sin modelos.

**Estado de ingesta:** Actions `workflow_dispatch` se ejecuta únicamente desde `main`, usa concurrencia `clipoteca-catalog` y emite registros de job/metrics como artefactos. En Actions se habilita `CLIPOTECA_PERSIST_JOBS=1`, que persiste `data/jobs/<request_id>.json` vía Contents API con SHA optimista en cada transición, incluidos `queued` y términos multilingües. Por defecto local en tests, para no usar red. La serialización global `clipoteca-catalog` prioriza integridad; concurrencia por request_id durante búsqueda sigue pendiente de una separación segura de las fases. No se ejecutó publicación real en GitHub Releases ni despliegue.

## Referencias consultadas

- https://www.pexels.com/api/documentation/
- https://pixabay.com/api/docs/
- https://www.mediawiki.org/wiki/API:Imageinfo
- https://archive.org/developers/
- https://api.nasa.gov/ y https://images.nasa.gov/docs/images.nasa.gov_api_docs.pdf
- https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json


## Validación de M4 corregida (20-11-2026)

- Tests offline de APIs y regresiones; 113 pruebas Python locales (3.15.2), con FFmpeg real para VFR, 4K→1080 y dimensiones impares. No implica disponibilidad de credenciales reales de Pexels/Pixabay.
- Reintentos HTTP 429 (máximo dos, Retry-After con techo 1 s), breaker separado con cooldown, round-robin con un task por proveedor.
- Los procesos FFmpeg/FFprobe reciben entorno filtrado para excluir secretos; el token de escritura existe solo en el paso Actions `Ingest` y no se pasa a los subprocess.
- Las resoluciones de NASA `http://images-assets.nasa.gov` se reescriben a HTTPS exclusivamente para ese host; prioridad de rendición explícita.
- Para assets de Releases parcialmente cargados se reserva el identificador y el próximo intento usa otro, sin borrar assets de terceros.
- Las funciones de búsqueda detectan idiomas por escritura del título fuente con fallback inglés, una heurística que no sustituye detección estadística.

**Pendientes de validación online:** pruebas con token autorizado de las APIs privadas; condiciones/contratos de licencia de cada fuente; comprobación de IP del peer en runner real/proxy; verificación de una publicación en Releases con credenciales reales.

## M5a: YouTube Data API v3 (verificación documental 20-11-2026)

| Fuente | Consulta real | Idiomas | Descarga/estado | Credencial |
|---|---|---|---|---|
| YouTube | `GET https://www.googleapis.com/youtube/v3/search?part=id,snippet&type=video&videoDuration=short&q=…&relevanceLanguage=…&key=…`; `/videos?part=snippet,contentDetails&id=…&key=…` | es/en/zh/ru en `relevanceLanguage` | **SUPPORTED_SEARCH_ONLY**: no hay MP4 accesible mediante Data API con API key. `resolve_media` declara `data_api_no_direct_media` y `direct_media=None`. | `YOUTUBE_API_KEY`; falta → `AUTH_REQUIRED` |

El filtro `videoDuration=short` significa menos de 4 minutos, **no** 20 segundos. `videos.list` devuelve `contentDetails.duration` en ISO-8601, pero dimensiones exactas requieren `fileDetails` con permisos de propietario; se dejan como `None`. No se usa scraping ni extracción no autorizada. Evidencia: esquema y parámetros oficiales; búsqueda real no ejecutada sin credencial de usuario, respuesta verificada con fixtures offline para válido, vacío, 429, 500, timeout y no-media.

- https://developers.google.com/youtube/data/reference/rest/v3/search/list
- https://developers.google.com/youtube/data/reference/rest/v3/videos/list
- https://developers.google.com/youtube/v3/docs/videos

## M5b: Rutube (API pública, 20-11-2026)

| Fuente | Endpoint | es | en | zh | ru | Archivo directo / estado |
|---|---|---|---|---|---|---|
| Rutube | `/api/search/video/?query=…&per_page=N`; `/api/video/<id>/`; `/api/play/options/<id>/` | texto | texto | texto | texto | **SUPPORTED_SEARCH_ONLY** habitual: `video_balancer.default` expone HLS `.m3u8`, no MP4; solo un MP4 HTTPS explícito puede ser importado por el downloader acotado |

**Evidencia HTTP directa en entorno Oracle el 20-11-2026:** `GET /api/search/video/?query=rain&limit=2` → **200 application/json** con `count=69`, `results[0].id` de 32 hex, `title`, `duration`, `video_url`; `GET /api/video/8411dde9e6521796b6805e5966ad4297/` → **200 JSON**; `GET /api/play/options/8411dde9e6521796b6805e5966ad4297/` → **200 JSON**, `video_balancer.default` con `https://bl.rutube.ru/route/….m3u8`. El downloader actual NO abre playlists HLS anidadas por su riesgo SSRF. No se probaron descargas HLS reales ni copyright. Fixtures incluyen cuatro idiomas, vacío, 429, 500, timeout, metadata y no-media.

- https://rutube.ru/info/embed/
- https://rutube.ru/api/search/video/?query=rain&limit=2
- https://rutube.ru/api/play/options/8411dde9e6521796b6805e5966ad4297/

## M5c: PeerTube y Odysee (evidencia real 20-11-2026)

| Fuente | Descubrimiento | Detalle/archivo | Descarga real y restricciones |
|---|---|---|---|
| PeerTube | `GET https://sepiasearch.org/api/v1/search/videos?search=…&count=N` | `GET https://<instancia>/api/v1/videos/<uuid>`; `files[].fileUrl` o `streamingPlaylists[].files[].fileUrl` | **SUPPORTED_SEARCH_DOWNLOAD** cuando hay MP4/WebM HTTPS con tamaño conocido y ≤350 MiB. Solo instancias explícitamente autorizadas en `PEERTUBE_ALLOWED_INSTANCES`; HLS sin archivo verificable queda search-only. |
| Odysee | Lighthouse `GET https://lighthouse.odysee.tv/search?s=…&size=N&claimType=file&free_only=true`; `POST https://api.na-backend.odysee.com/api/v1/proxy?m=claim_search` | SDK `resolve` y `get` por JSON-RPC; `get` puede devolver `https://player.odycdn.com/v6/streams/...mp4` | **SUPPORTED_SEARCH_DOWNLOAD** solo si hay MP4/WebM HTTPS permitido, tamaño reclamado ≤350 MiB y el stream URL se puede verificar por el downloader. No se inventan streams para claims inaccesibles o de gran tamaño. |

**Comprobación HTTP real:** SepiaSearch devolvió **200 JSON** con `total=3050`, `data[0].uuid=84ed4b77-a3e5-411f-8723-026106481b6e`, `url=https://pt.na4.eu/videos/watch/…`, duración 301. La instancia `pt.na4.eu/api/v1/videos/<uuid>` devolvió **200 JSON** con `files=[]` y `streamingPlaylists[0].files` con URLs `.mp4` fragmentadas de varios tamaños (la primera ~765 MB, rechazada). Lighthouse devolvió **200 JSON** con `claimId` y `name`. SDK `claim_search` devolvió **200 JSON**, `result.items[0].value.source.size=390601464`; SDK `get` devolvió **200 JSON** con `result.streaming_url` MP4. Ese claim supera 350 MiB y no se importaría. No se descargó ni redistribuyó material de usuarios externos.

Los tests incluyen cuatro lenguas, vacío, 429/500, timeout, licencia/metadatos, protección contra instancias no autorizadas y una ejecución offline de FFmpeg+ingesta PeerTube→GitHub Releases simulado. Los endpoints concretos, formatos y licencias pueden variar.

- https://docs.joinpeertube.org/api-rest-reference/api/video.html
- https://sepiasearch.org/api/v1/search/videos?search=rain&count=2
- https://github.com/OdyseeTeam/odysee-frontend/blob/master/llms.txt

## M6a: Bilibili y VK Video (20-11-2026)

| Fuente | Endpoint | ES | EN | ZH | RU | Autenticación / estado / limitación |
|---|---|---|---|---|---|---|
| Bilibili | `GET https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=…&page=1`; `GET /x/web-interface/view?bvid=…` | texto | texto | texto | texto | `BILIBILI_COOKIE` opcional; búsqueda pública puede responder 0, -352/412 por controles anti-bot. Solo **SUPPORTED_SEARCH_ONLY** sin URL MP4 reutilizable autorizada. |
| VK Video | `GET https://api.vk.com/method/video.search?q=…&access_token=…&v=5.199`; `video.get` | texto | texto | texto | texto | `VK_ACCESS_TOKEN` obligatorio; falta/invalidación → **AUTH_REQUIRED**; la presencia de `files.mp4_360` en API no prueba derechos de redistribución y no se importa automáticamente. **SUPPORTED_SEARCH_ONLY** por defecto. |

**Prueba real desde Oracle:** Bilibili `/x/web-interface/search/type?search_type=video&keyword=rain&page=1` devolvió **HTTP 200 JSON** `code=0`, `message=OK`, `data.result[]` con `bvid`, `arcurl`, `title`, `duration`. No hubo login, descarga ni reproducción. VK: no había token OAuth autorizado y se limitaron las pruebas a fixtures exactos conforme al [esquema oficial de VK API v5.199](https://github.com/VKCOM/vk-api-schema/blob/master/video/methods.json). Riesgo Bilibili: búsqueda anónima no garantizada en otras redes ni regiones. Referencias: https://github.com/moushicheng/bilibili-API-collect/blob/master/docs/search/search_request.md, https://dev.vk.com/reference/methods, https://github.com/VKCOM/vk-api-schema.

## M6b: Douyin y TikTok (histórico: API de cuenta autorizada, reemplazado por M19)

| Fuente | API oficial | ES | EN | ZH | RU | Autorización y alcance real |
|---|---|---|---|---|---|---|
| Douyin | `GET https://open.douyin.com/video/list/` con header `access-token` | filtro local | filtro local | filtro local | filtro local | OAuth `video.list` **solo videos de cuenta autorizada**, NO búsqueda pública general. `DOUYIN_ACCESS_TOKEN` obligatorio; sin él **AUTH_REQUIRED**. **SUPPORTED_SEARCH_ONLY** para títulos propios y sin MP4 directo autorizado. |
| TikTok | `POST https://open.tiktokapis.com/v2/video/list/?fields=id,title,video_description,share_url,duration,create_time` | filtro local | filtro local | filtro local | filtro local | OAuth `video.list` **solo videos de cuenta autorizada**. `TIKTOK_ACCESS_TOKEN` obligatorio; falta → **AUTH_REQUIRED**. Display API no ofrece búsqueda arbitraria ni descarga MP4. La Research API es un permiso distinto, no asumido. |

No hay OAuth/tokens facilitados para probar llamadas reales. Validación mediante fixtures del contrato publicado por Douyin/TikTok, con respuestas válidas, vacías, 429, 500, timeout, ausencia/token inválido y `direct_media=None`. La API de búsqueda global se declara explícitamente no soportada, no se simula con scraping.

- https://open.douyin.com/platform/resource/docs/openapi/video-management/douyin/search-video/account-video-list
- https://developers.tiktok.com/docs/en/tiktok-api-v2-video-list/
- https://developers.tiktok.com/docs/en/display-api-overview
- https://developers.tiktok.com/docs/en/tiktok-api-v2-video-query

## M18: archivos institucionales (fixtures offline, 21-11-2026)

| Fuente | Búsqueda oficial | Credencial | Resolución de media |
|---|---|---|---|
| Europeana | Record API v2, `TYPE:VIDEO`, cursor | `EUROPEANA_API_KEY` | `SUPPORTED_SEARCH_DOWNLOAD`; acepta solo un `edmIsShownBy` HTTPS que sea video directo |
| NARA | Catalog API v2, `records/search`, `searchAfter` | `NARA_API_KEY` | `SUPPORTED_SEARCH_DOWNLOAD`; exige recurso digital explícito con MIME/extensión de video |
| DPLA | Items API v2, `sourceResource.type=Moving Image` | `DPLA_API_KEY` | `SUPPORTED_SEARCH_ONLY` por defecto; las páginas del contribuidor y miniaturas no se promueven a video |
| Smithsonian | Open Access v1.0, `online_media_type:Videos` | `SMITHSONIAN_API_KEY` | `SUPPORTED_SEARCH_ONLY` por defecto; un player web no se trata como archivo directo |

Las cuatro fuentes fallan con `AUTH_REQUIRED` antes de hacer red cuando falta su
clave. Las pruebas reproducibles usan respuestas guardadas para resultado válido,
vacío, filtrado de tipo y media directa/search-only; no prueban disponibilidad,
cuota, licencia ni descarga online.


## Deduplicación entre proveedores y procedencia

El catálogo público (`schemas/clip.schema.json`) admite **un solo** objeto
`source` por clip. Cuando una segunda fuente presenta el mismo archivo,
la URL canónica idéntica, SHA-256 igual o pHash cercano permiten reconocer la
duplicación. Un título similar **nunca basta por sí solo**; requiere otra señal
independiente, como duración en ±1 segundo o creador/colección coincidentes.

La procedencia descartada de la segunda fuente **solo queda en el trabajo**
(`data/jobs/<request_id>.json`): `source_errors[provider:provider_id]` y
`duplicate_ids`. No se añade una segunda fuente al catálogo ni se crea otro
asset. Las URL publicadas eliminan parámetros de credenciales como
`api_key` y `access_token`, pero conservan parámetros de identidad.


## M19: Douyin y TikTok, búsqueda pública web (21-11-2026)

M19 sustituye el adaptador histórico de cuenta autorizada de M6b; no necesita
DOUYIN_ACCESS_TOKEN ni TIKTOK_ACCESS_TOKEN. Ambos adaptadores buscan por palabra
clave y declaran búsqueda y descarga best_effort hasta medir el smoke en main.
DOUYIN_COOKIE y TIKTOK_COOKIE son opcionales. Primero se obtiene ttwid de
invitado y se consulta sin cookie privada. La cookie opcional se utiliza
una sola vez cuando el invitado no basta: status_code 2483, HTTP 401/403,
respuesta 200 vacía, HTML/no JSON, captcha o verify_check (incluido
search_nil_info.search_nil_type). Si no hay cookie o también falla, se informa
AUTH_REQUIRED: session_required. Una lista vacía válida de resultados
(data/item_list explícitos) NO es bloqueo y permite probar la búsqueda general
Douyin. Sin cookie se declara el límite, no se inventan resultados.

| Fuente | Búsqueda | Paginación | Archivo | Acceso |
|---|---|---|---|---|
| Douyin | /aweme/v1/web/search/item/ y fallback /aweme/v1/web/general/search/single/ | offset/count | video.bit_rate[].play_addr.url_list[-1], playwm a play; detalle firmado | ttwid + msToken + a_bogus + X-Bogus Python |
| TikTok | /api/search/item/full/ | offset acumulado con ítems recibidos y search_id de log_pb.impr_id primero | playAddr o bitrateInfo; detalle firmado | ttwid + X-Dynosaur + X-Gnarly Python |

En TikTok un device_id aleatorio puede ocasionar un 403 al abrir URLs de
video, aun con búsqueda funcional (Evil0ctal/Douyin_TikTok_Download_API
issue #831). La disponibilidad live queda por medir y no se promete descarga
universal.

La descarga se limita a CDN enumerados, con HTTPS, seguridad SSRF/DNS/peer y
allowlist en cada redirección. M13/M14 siguen decidiendo el corte por escena.
Cookies y URLs de descarga firmadas nunca se incorporan al catálogo, reportes
ni logs. Las firmas son Python puro, sin navegador; atribución y licencias en
pipeline/clipoteca/providers/signing/NOTICE.

Verificación: fixtures offline M19 en tests/fixtures/m19 y pruebas de
búsqueda, detalles, firmas, sesión requerida y cookies. No sustituyen smoke
real en main.

Para CI, configurar los secrets opcionales DOUYIN_COOKIE y TIKTOK_COOKIE.
Los anteriores DOUYIN_ACCESS_TOKEN y TIKTOK_ACCESS_TOKEN dejan de utilizarse.
No se modifican workflows en M19.
