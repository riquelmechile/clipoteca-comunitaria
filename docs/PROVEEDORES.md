# Clipoteca v1: matriz te\u0301cnica de proveedores M4

Fecha de verificacio\u0301n documental: 20-11-2026. El estado es de **implementacio\u0301n y tests con fixtures**, no prueba de descarga real ni de disponibilidad contractual. El servidor registra errores por consulta e idioma; cada candidato solo se publica si tiene un archivo verificable y supera FFmpeg, dedup y GitHub Releases.

| Fuente | Bu\u0301squeda | Metadatos | Archivo directo | Autenticacio\u0301n | Limitacio\u0301n/estado |
|---|---|---|---|---|---|
| Pexels | `GET https://api.pexels.com/v1/videos/search` | `video_files` / detalle | MP4 HTTPS escogido sin aumentar resolucio\u0301n | `PEXELS_API_KEY` obligatoria | `AUTH_REQUIRED` sin clave; `SUPPORTED_SEARCH_DOWNLOAD` solo si hay variante real |
| Pixabay | `GET https://pixabay.com/api/videos/` | `hits[].videos` | `large/medium/small/tiny` MP4 | `PIXABAY_API_KEY` obligatoria | `AUTH_REQUIRED` sin clave; `SUPPORTED_SEARCH_ONLY` por candidato sin MP4 |
| Wikimedia Commons | MediaWiki `generator=search`, namespace File | `imageinfo` | `imageinfo.url`, MIME video | Pu\u0301blica; User-Agent identificable | Filtra ima\u0301genes y MIME no video; no fuerza falsas descargas |
| Internet Archive | `/advancedsearch.php` (`mediatype:movies`) | `/metadata/{identifier}` | `/download/{identifier}/{file}` | Pu\u0301blica | Rechaza HTML/imagen, taman\u0303o > 1 GiB o sin derivado; prioriza MP4 de mayor taman\u0303o admisible |
| NASA | `https://images-api.nasa.gov/search?media_type=video` | `collection.items[].data` | `/asset/{nasa_id}` entrega MP4 real | `NASA_API_KEY` opcional; por defecto `DEMO_KEY` | `SUPPORTED_SEARCH_ONLY` si manifiesto no tiene MP4 |
| Library of Congress | `GET https://www.loc.gov/film-and-videos/?fo=json&at=results,pagination` | `results[].resources[0]` | `video`, con fallback `video_stream` | Pu\u0301blica | Excluye `download_restricted`; timeout 45 s y al menos 500 ms entre solicitudes |
| Coverr | `GET https://api.coverr.co/videos?query=...&page=...&page_size=...&urls=true` | `hits[].urls` | `mp4_download`, con fallback `mp4_preview` | `COVERR_API_KEY` obligatoria (Bearer) | `AUTH_REQUIRED` sin clave; solo conserva URL MP4 HTTPS directa |

**Idiomas:** cada ronda de bu\u0301squeda de concepto reconocido envi\u0301a te\u0301rminos `es/en/zh/ru` al adaptador habilitado. Algunos servicios no interpretan correctamente los cuatro idiomas, pero se registra el idioma/te\u0301rmino utilizado, sin afirmar soporte sema\u0301ntico inexistente. Pixabay usa `lang` cuando es aceptado; chino se consulta con `lang=zh`, siempre con el te\u0301rmino original.

**Errores tipados:** `SUPPORTED_SEARCH_DOWNLOAD`, `SUPPORTED_SEARCH_ONLY`, `AUTH_REQUIRED`, `RATE_LIMITED`, `UNAVAILABLE`, `TIMEOUT`. El estado del adaptador indica capacidad; no equivale a que todo resultado sea descargable. Los HTTP 429/500, respuestas vaci\u0301as, timeout y HTML falso se verifican sin red en pytest.

**Li\u0301mites/seguridad:** solo HTTPS, DNS pu\u0301blico y redirecciones reevaluadas; Content-Length y bytes reales limitados; formatos MP4/WebM/OGV/Ogg con firma reconocida. El cliente valida adema\u0301s la IP efectivamente conectada (cuando el transporte expone `network_stream.get_extra_info('server_addr')`), compara\u0301ndola con el DNS pu\u0301blico previamente autorizado. Si no puede verificarla en produccio\u0301n, falla cerrado. La comprobacio\u0301n se repite tras cada redirect y aplica plazo total de descarga; el transporte debe someterse a una prueba real con proxy/red antes de despliegue.

**Modelos Argos:** se declara `ARGOS_MODEL_CACHE_DIR` y `ARGOS_MODEL_PINS_JSON` con pares `es_en`, `en_es`, `en_zh`, `zh_en`, `en_ru`, `ru_en`. Por cada par se exige `version` y SHA256 real del paquete `.argosmodel`. El archivo `data/argos-pins.json` contiene los seis SHA-256 **calculados desde descargas reales** de `argos-net.com/v1` el 20-11-2026. `clipoteca.translate.download_models` descarga los faltantes a temporal y valida hash antes del reemplazo ato\u0301mico. El workflow ejecuta esta verificacio\u0301n al iniciar, incluso si hay cache\u0301. La ausencia o huella equivocada falla cerrado. El diccionario incorpora 200 conceptos traducidos que funcionan sin modelos.

**Estado de ingesta:** Actions `workflow_dispatch` se ejecuta u\u0301nicamente desde `main`, usa concurrencia `clipoteca-catalog` y emite registros de job/metrics como artefactos. En Actions se habilita `CLIPOTECA_PERSIST_JOBS=1`, que persiste `data/jobs/<request_id>.json` vi\u0301a Contents API con SHA optimista en cada transicio\u0301n, incluidos `queued` y te\u0301rminos multilingu\u0308es. Por defecto local en tests, para no usar red. La serializacio\u0301n global `clipoteca-catalog` prioriza integridad; concurrencia por request_id durante bu\u0301squeda sigue pendiente de una separacio\u0301n segura de las fases. No se ejecuto\u0301 publicacio\u0301n real en GitHub Releases ni despliegue.

## Referencias consultadas

- https://www.pexels.com/api/documentation/
- https://pixabay.com/api/docs/
- https://www.mediawiki.org/wiki/API:Imageinfo
- https://archive.org/developers/
- https://api.nasa.gov/ y https://images.nasa.gov/docs/images.nasa.gov_api_docs.pdf
- https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json


## Validacio\u0301n de M4 corregida (20-11-2026)

- Tests offline de APIs y regresiones; 113 pruebas Python locales (3.15.2), con FFmpeg real para VFR, 4K\u21921080 y dimensiones impares. No implica disponibilidad de credenciales reales de Pexels/Pixabay.
- Reintentos HTTP 429 (ma\u0301ximo dos, Retry-After con techo 1 s), breaker separado con cooldown, round-robin con un task por proveedor.
- Los procesos FFmpeg/FFprobe reciben entorno filtrado para excluir secretos; el token de escritura existe solo en el paso Actions `Ingest` y no se pasa a los subprocess.
- Las resoluciones de NASA `http://images-assets.nasa.gov` se reescriben a HTTPS exclusivamente para ese host; prioridad de rendicio\u0301n expli\u0301cita.
- Para assets de Releases parcialmente cargados se reserva el identificador y el pro\u0301ximo intento usa otro, sin borrar assets de terceros.
- Las funciones de bu\u0301squeda detectan idiomas por escritura del ti\u0301tulo fuente con fallback ingle\u0301s, una heuri\u0301stica que no sustituye deteccio\u0301n estadi\u0301stica.

**Pendientes de validacio\u0301n online:** pruebas con token autorizado de las APIs privadas; condiciones/contratos de licencia de cada fuente; comprobacio\u0301n de IP del peer en runner real/proxy; verificacio\u0301n de una publicacio\u0301n en Releases con credenciales reales.

## M5a: YouTube Data API v3 (verificacio\u0301n documental 20-11-2026)

| Fuente | Consulta real | Idiomas | Descarga/estado | Credencial |
|---|---|---|---|---|
| YouTube | `GET https://www.googleapis.com/youtube/v3/search?part=id,snippet&type=video&videoDuration=short&q=...&relevanceLanguage=...&key=...`; `/videos?part=snippet,contentDetails&id=...&key=...` | es/en/zh/ru en `relevanceLanguage` | **SUPPORTED_SEARCH_ONLY**: no hay MP4 accesible mediante Data API con API key. `resolve_media` declara `data_api_no_direct_media` y `direct_media=None`. | `YOUTUBE_API_KEY`; falta \u2192 `AUTH_REQUIRED` |

El filtro `videoDuration=short` significa menos de 4 minutos, **no** 20 segundos. `videos.list` devuelve `contentDetails.duration` en ISO-8601, pero dimensiones exactas requieren `fileDetails` con permisos de propietario; se dejan como `None`. No se usa scraping ni extraccio\u0301n no autorizada. Evidencia: esquema y para\u0301metros oficiales; bu\u0301squeda real no ejecutada sin credencial de usuario, respuesta verificada con fixtures offline para va\u0301lido, vaci\u0301o, 429, 500, timeout y no-media.

- https://developers.google.com/youtube/data/reference/rest/v3/search/list
- https://developers.google.com/youtube/data/reference/rest/v3/videos/list
- https://developers.google.com/youtube/v3/docs/videos

## M5b: Rutube (API pu\u0301blica, 20-11-2026)

| Fuente | Endpoint | es | en | zh | ru | Archivo directo / estado |
|---|---|---|---|---|---|---|
| Rutube | `/api/search/video/?query=...&per_page=N`; `/api/video/<id>/`; `/api/play/options/<id>/` | texto | texto | texto | texto | **SUPPORTED_SEARCH_ONLY** habitual: `video_balancer.default` expone HLS `.m3u8`, no MP4; solo un MP4 HTTPS expli\u0301cito puede ser importado por el downloader acotado |

**Evidencia HTTP directa en entorno Oracle el 20-11-2026:** `GET /api/search/video/?query=rain&limit=2` \u2192 **200 application/json** con `count=69`, `results[0].id` de 32 hex, `title`, `duration`, `video_url`; `GET /api/video/8411dde9e6521796b6805e5966ad4297/` \u2192 **200 JSON**; `GET /api/play/options/8411dde9e6521796b6805e5966ad4297/` \u2192 **200 JSON**, `video_balancer.default` con `https://bl.rutube.ru/route/....m3u8`. El downloader actual NO abre playlists HLS anidadas por su riesgo SSRF. No se probaron descargas HLS reales ni copyright. Fixtures incluyen cuatro idiomas, vaci\u0301o, 429, 500, timeout, metadata y no-media.

- https://rutube.ru/info/embed/
- https://rutube.ru/api/search/video/?query=rain&limit=2
- https://rutube.ru/api/play/options/8411dde9e6521796b6805e5966ad4297/

## M5c: PeerTube y Odysee (evidencia real 20-11-2026)

| Fuente | Descubrimiento | Detalle/archivo | Descarga real y restricciones |
|---|---|---|---|
| PeerTube | `GET https://sepiasearch.org/api/v1/search/videos?search=...&count=N` | `GET https://<instancia>/api/v1/videos/<uuid>`; `files[].fileUrl` o `streamingPlaylists[].files[].fileUrl` | **SUPPORTED_SEARCH_DOWNLOAD** cuando hay MP4/WebM HTTPS con taman\u0303o conocido y \u22641 GiB. Solo instancias expli\u0301citamente autorizadas en `PEERTUBE_ALLOWED_INSTANCES`; HLS sin archivo verificable queda search-only. |
| Odysee | Lighthouse `GET https://lighthouse.odysee.tv/search?s=...&size=N&claimType=file&free_only=true`; `POST https://api.na-backend.odysee.com/api/v1/proxy?m=claim_search` | SDK `resolve` y `get` por JSON-RPC; `get` puede devolver `https://player.odycdn.com/v6/streams/...mp4` | **SUPPORTED_SEARCH_DOWNLOAD** solo si hay MP4/WebM HTTPS permitido, taman\u0303o reclamado \u22641 GiB y el stream URL se puede verificar por el downloader. No se inventan streams para claims inaccesibles o de gran taman\u0303o. |

**Comprobacio\u0301n HTTP real:** SepiaSearch devolvio\u0301 **200 JSON** con `total=3050`, `data[0].uuid=84ed4b77-a3e5-411f-8723-026106481b6e`, `url=https://pt.na4.eu/videos/watch/...`, duracio\u0301n 301. La instancia `pt.na4.eu/api/v1/videos/<uuid>` devolvio\u0301 **200 JSON** con `files=[]` y `streamingPlaylists[0].files` con URLs `.mp4` fragmentadas de varios taman\u0303os (la primera ~765 MB, rechazada). Lighthouse devolvio\u0301 **200 JSON** con `claimId` y `name`. SDK `claim_search` devolvio\u0301 **200 JSON**, `result.items[0].value.source.size=390601464`; SDK `get` devolvio\u0301 **200 JSON** con `result.streaming_url` MP4. Con el tope de 1 GiB (2.0.2) ese claim ya entra. No se descargo\u0301 ni redistribuyo\u0301 material de usuarios externos.

Los tests incluyen cuatro lenguas, vaci\u0301o, 429/500, timeout, licencia/metadatos, proteccio\u0301n contra instancias no autorizadas y una ejecucio\u0301n offline de FFmpeg+ingesta PeerTube\u2192GitHub Releases simulado. Los endpoints concretos, formatos y licencias pueden variar.

- https://docs.joinpeertube.org/api-rest-reference/api/video.html
- https://sepiasearch.org/api/v1/search/videos?search=rain&count=2
- https://github.com/OdyseeTeam/odysee-frontend/blob/master/llms.txt

## M6a: Bilibili y VK Video (20-11-2026)

| Fuente | Endpoint | ES | EN | ZH | RU | Autenticacio\u0301n / estado / limitacio\u0301n |
|---|---|---|---|---|---|---|
| Bilibili | `GET https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=...&page=1`; `GET /x/web-interface/view?bvid=...` | texto | texto | texto | texto | `BILIBILI_COOKIE` opcional; bu\u0301squeda pu\u0301blica puede responder 0, -352/412 por controles anti-bot. Solo **SUPPORTED_SEARCH_ONLY** sin URL MP4 reutilizable autorizada. |
| VK Video | `GET https://api.vk.com/method/video.search?q=...&access_token=...&v=5.199`; `video.get` | texto | texto | texto | texto | `VK_ACCESS_TOKEN` obligatorio; falta/invalidacio\u0301n \u2192 **AUTH_REQUIRED**; la presencia de `files.mp4_360` en API no prueba derechos de redistribucio\u0301n y no se importa automa\u0301ticamente. **SUPPORTED_SEARCH_ONLY** por defecto. |

**Prueba real desde Oracle:** Bilibili `/x/web-interface/search/type?search_type=video&keyword=rain&page=1` devolvio\u0301 **HTTP 200 JSON** `code=0`, `message=OK`, `data.result[]` con `bvid`, `arcurl`, `title`, `duration`. No hubo login, descarga ni reproduccio\u0301n. VK: no habi\u0301a token OAuth autorizado y se limitaron las pruebas a fixtures exactos conforme al [esquema oficial de VK API v5.199](https://github.com/VKCOM/vk-api-schema/blob/master/video/methods.json). Riesgo Bilibili: bu\u0301squeda ano\u0301nima no garantizada en otras redes ni regiones. Referencias: https://github.com/moushicheng/bilibili-API-collect/blob/master/docs/search/search_request.md, https://dev.vk.com/reference/methods, https://github.com/VKCOM/vk-api-schema.

## M6b: Douyin y TikTok (histo\u0301rico: API de cuenta autorizada, reemplazado por M19)

| Fuente | API oficial | ES | EN | ZH | RU | Autorizacio\u0301n y alcance real |
|---|---|---|---|---|---|---|
| Douyin | `GET https://open.douyin.com/video/list/` con header `access-token` | filtro local | filtro local | filtro local | filtro local | OAuth `video.list` **solo videos de cuenta autorizada**, NO bu\u0301squeda pu\u0301blica general. `DOUYIN_ACCESS_TOKEN` obligatorio; sin e\u0301l **AUTH_REQUIRED**. **SUPPORTED_SEARCH_ONLY** para ti\u0301tulos propios y sin MP4 directo autorizado. |
| TikTok | `POST https://open.tiktokapis.com/v2/video/list/?fields=id,title,video_description,share_url,duration,create_time` | filtro local | filtro local | filtro local | filtro local | OAuth `video.list` **solo videos de cuenta autorizada**. `TIKTOK_ACCESS_TOKEN` obligatorio; falta \u2192 **AUTH_REQUIRED**. Display API no ofrece bu\u0301squeda arbitraria ni descarga MP4. La Research API es un permiso distinto, no asumido. |

No hay OAuth/tokens facilitados para probar llamadas reales. Validacio\u0301n mediante fixtures del contrato publicado por Douyin/TikTok, con respuestas va\u0301lidas, vaci\u0301as, 429, 500, timeout, ausencia/token inva\u0301lido y `direct_media=None`. La API de bu\u0301squeda global se declara expli\u0301citamente no soportada, no se simula con scraping.

- https://open.douyin.com/platform/resource/docs/openapi/video-management/douyin/search-video/account-video-list
- https://developers.tiktok.com/docs/en/tiktok-api-v2-video-list/
- https://developers.tiktok.com/docs/en/display-api-overview
- https://developers.tiktok.com/docs/en/tiktok-api-v2-video-query

## M18: archivos institucionales (fixtures offline, 21-11-2026)

| Fuente | Bu\u0301squeda oficial | Credencial | Resolucio\u0301n de media |
|---|---|---|---|
| Europeana | Record API v2, `TYPE:VIDEO`, cursor | `EUROPEANA_API_KEY` | `SUPPORTED_SEARCH_DOWNLOAD`; acepta solo un `edmIsShownBy` HTTPS que sea video directo |
| NARA | Catalog API v2, `records/search`, `searchAfter` | `NARA_API_KEY` | `SUPPORTED_SEARCH_DOWNLOAD`; exige recurso digital expli\u0301cito con MIME/extensio\u0301n de video |
| DPLA | Items API v2, `sourceResource.type=Moving Image` | `DPLA_API_KEY` | `SUPPORTED_SEARCH_ONLY` por defecto; las pa\u0301ginas del contribuidor y miniaturas no se promueven a video |
| Smithsonian | Open Access v1.0, `online_media_type:Videos` | `SMITHSONIAN_API_KEY` | `SUPPORTED_SEARCH_ONLY` por defecto; un player web no se trata como archivo directo |

Las cuatro fuentes fallan con `AUTH_REQUIRED` antes de hacer red cuando falta su
clave. Las pruebas reproducibles usan respuestas guardadas para resultado va\u0301lido,
vaci\u0301o, filtrado de tipo y media directa/search-only; no prueban disponibilidad,
cuota, licencia ni descarga online.


## Deduplicacio\u0301n entre proveedores y procedencia

El cata\u0301logo pu\u0301blico (`schemas/clip.schema.json`) admite **un solo** objeto
`source` por clip. Cuando una segunda fuente presenta el mismo archivo,
la URL cano\u0301nica ide\u0301ntica, SHA-256 igual o pHash cercano permiten reconocer la
duplicacio\u0301n. Un ti\u0301tulo similar **nunca basta por si\u0301 solo**; requiere otra sen\u0303al
independiente, como duracio\u0301n en \xb11 segundo o creador/coleccio\u0301n coincidentes.

La procedencia descartada de la segunda fuente **solo queda en el trabajo**
(`data/jobs/<request_id>.json`): `source_errors[provider:provider_id]` y
`duplicate_ids`. No se an\u0303ade una segunda fuente al cata\u0301logo ni se crea otro
asset. Las URL publicadas eliminan para\u0301metros de credenciales como
`api_key` y `access_token`, pero conservan para\u0301metros de identidad.


## M19: Douyin y TikTok, bu\u0301squeda pu\u0301blica web (21-11-2026)

M19 sustituye el adaptador histo\u0301rico de cuenta autorizada de M6b; no necesita
DOUYIN_ACCESS_TOKEN ni TIKTOK_ACCESS_TOKEN. Ambos adaptadores buscan por palabra
clave y declaran bu\u0301squeda y descarga best_effort hasta medir el smoke en main.
DOUYIN_COOKIE y TIKTOK_COOKIE son opcionales. Primero se obtiene ttwid de
invitado y se consulta sin cookie privada. La cookie opcional se utiliza
una sola vez cuando el invitado no basta: status_code 2483, HTTP 401/403,
respuesta 200 vaci\u0301a, HTML/no JSON, captcha o verify_check (incluido
search_nil_info.search_nil_type). Si no hay cookie o tambie\u0301n falla, se informa
AUTH_REQUIRED: session_required. M19 r3 detecta verify_check solo en
search_nil_info.search_nil_type; captcha solo en claves superiores
verify_data, verify_center_decision_conf o captcha, nunca en ti\u0301tulos.
Un JSON con solo status_code: 0 carece de lista y requiere sesio\u0301n.
Una lista vaci\u0301a va\u0301lida de resultados
(data/item_list expli\u0301citos) NO es bloqueo y permite probar la bu\u0301squeda general
Douyin. Sin cookie se declara el li\u0301mite, no se inventan resultados.

| Fuente | Bu\u0301squeda | Paginacio\u0301n | Archivo | Acceso |
|---|---|---|---|---|
| Douyin | /aweme/v1/web/search/item/ y fallback /aweme/v1/web/general/search/single/ | offset/count | video.bit_rate[].play_addr.url_list[-1], playwm a play; detalle firmado | ttwid + msToken + a_bogus + X-Bogus Python |
| TikTok | /api/search/item/full/ | offset acumulado con i\u0301tems recibidos y search_id de log_pb.impr_id primero | playAddr o bitrateInfo; detalle firmado | ttwid + X-Dynosaur + X-Gnarly Python |

En TikTok un device_id aleatorio puede ocasionar un 403 al abrir URLs de
video, aun con bu\u0301squeda funcional (Evil0ctal/Douyin_TikTok_Download_API
issue #831). La disponibilidad live queda por medir y no se promete descarga
universal.

La descarga se limita a CDN enumerados, con HTTPS, seguridad SSRF/DNS/peer y
allowlist en cada redireccio\u0301n. M13/M14 siguen decidiendo el corte por escena.
Cookies y URLs de descarga firmadas nunca se incorporan al cata\u0301logo, reportes
ni logs. Las firmas son Python puro, sin navegador; atribucio\u0301n y licencias en
pipeline/clipoteca/providers/signing/NOTICE.

Verificacio\u0301n: fixtures offline M19 en tests/fixtures/m19 y pruebas de
bu\u0301squeda, detalles, firmas, sesio\u0301n requerida y cookies. No sustituyen smoke
real en main.

Para CI, configurar los secrets opcionales DOUYIN_COOKIE y TIKTOK_COOKIE.
Los anteriores DOUYIN_ACCESS_TOKEN y TIKTOK_ACCESS_TOKEN dejan de utilizarse.
No se modifican workflows en M19.


## M24 r4 - diagnostico del smoke 44972077685 y correcciones

**Procedencia:** [workflow live en main](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44972077685),
SHA bf0454baa76c7aa8e2528b51f38302b30581cf0d. El artefacto
data/reports/smoke-44972077685.json es inmutable: el codigo de esta
rama todavia no ha sido probado en un nuevo smoke.

- Internet Archive: las cuatro consultas UNAVAILABLE se atribuyeron al
  eco cambiado en advancedsearch.php. responseHeader.params usa qin para
  la consulta original y query para la forma normalizada, no q. El
  adaptador acepta q o qin igual al enviado; sin eco registra un aviso
  y conserva solo documentos con identifier y mediatype=movies.
  Un eco explicitamente distinto sigue rechazado.
- RuTube: cuatro consultas HTTP 200 sin resultados. El endpoint de
  busqueda necesita client=wdp; la reproduccion play/options no se
  modifica porque no hay evidencia nueva de que requiera ese parametro.
- OK.ru: URL anterior responde 301. La ruta nueva es
  /video/search/<termino-codificado>; desde el entorno de prueba se
  obtuvo HTTP 200 y una pagina con 20 registros dentro de
  video-search-result data-props. Fixture recortado y sin URLs/CDN;
  esto no certifica una descarga real.
- Library of Congress: tiempos agotados de 6 s en Actions y HTTP 429
  en la reproduccion externa. El smoke usa 45 s, separa idiomas del
  mismo proveedor al menos 500 ms y la API respeta Retry-After
  de 429 con un maximo acotado.
- Bilibili: code=0 con result vacio y sin numResults ya no cuenta
  como busqueda exitosa. Prueba SPI invitado buvid3/buvid4 en memoria
  y solo los envia a api.bilibili.com. Un vacio sin numResults tras
  el reintento es silent_block; el estado queda search-only.
- Douyin: status_code 2483 indica session_required; TikTok: registro
  invitado 1001 junto con lista vacia indica session_required.
  Requieren DOUYIN_COOKIE / TIKTOK_COOKIE o una prueba comparativa en
  runner propio aislado. No se evaden verificaciones ni se activan
  rutas nuevas por defecto.
- VK Video: el login anonimo devuelve 429 o timeout segun la red.
  Se comparte el token valido entre instancias y un backoff de
  fallo entre idiomas; se reporta RATE_LIMITED para 429, respetando
  el Retry-After acotado. No equivale a falta de credenciales.
- Smoke r4: probe por idioma en serie dentro de cada proveedor,
  error_type mas error_detail solo para excepciones propias con
  mensaje ASCII limitado; estado HTTP solo si se observa en un
  mensaje propio. No guardar URLs, headers, tokens ni cuerpos.

NASA devolvio resultados en ingles; que no devuelva material para
es, zh o ru es una limitacion del contenido y no un fallo tecnico.
PeerTube depende de instancias permitidas y mostro material en ingles:
la ausencia de resultados en otros idiomas tampoco es un error.

**Aun pendiente:** repetir smoke en main despues del merge, verificar
descarga de clips reales, comparar bloqueo IP en runner aislado y
realizar demo E2E. Ninguna fuente asciende a SUPPORTED_SEARCH_DOWNLOAD
con evidencia de solo busqueda. No se modifica .github/.


## M24 r7 - HTTPS con DNS pinning real sin rebinding

El smoke 44977121143 detecto un falso positivo intermitente en TikTok es:
provider peer changed after DNS validation. Antes, validate_public_url()
comprobaba un conjunto de IP publicas pero HTTPX volvia a resolver al
conectarse: dos respuestas CDN distintas (ambas publicas) producian un
error y abrian una carrera DNS (TOCTOU).

providers/http.py y security/download.py comparten security/pinning.py:
validan cada URL HTTPS y su DNS completo con validate_public_url,
rechazan IP privadas/reservadas y usan una IP validada como destino TCP.
La solicitud conserva Host y la extension sni_hostname con el nombre
original; secure_pinned_transport() usa TLS con verify=True, verificacion
de certificados y hostname habilitada, trust_env=False y transporte aislado
por intento para impedir reutilizar una sesion TLS de otro hostname
servido por la misma IP CDN.

Una falla de conexion permite probar solo la siguiente IP ya validada;
no se vuelve a consultar DNS ni se reintentan fallos de peer. La
comparacion de IP normaliza IPv6 y IPv4-mapped. La validacion del
certificado sigue contra el nombre original, no contra la IP CDN.

follow_redirects=False se conserva. En descargas, cada salto se valida
por separado, con allowlist de hostname, tamano maximo, MIME y plazo
total sin cambios. MockTransport exacto se conserva en pruebas sin red,
con validacion de peer si el transporte simulado lo publica; un transporte
externo distinto no elude la comprobacion de IP conectada.
security/hls.py ya hacia pinning directo (IP + Host + SNI) y
comparacion IPv6 normalizada: no se altero su comportamiento.
El DASH mux usa download_media en ambas rutas y FFmpeg solo ve archivos
locales, nunca URL remota.

Las pruebas M24 r7 reproducen DNS rotativo A/B, rechazo privado,
segundo destino ante fallo de conexion, Host/SNI y TLS verificado,
redireccion de descarga validada por salto e IPv6 equivalente.
Este cambio no demuestra disponibilidad nueva de TikTok ni cambia
estados de proveedores; una confirmacion live exige otro smoke en main.


## M24 r8 - cookies y URL de origen en DNS pinning

Reemplaza a PR #67 sin fusionarlo. El pinning M24 r7 mantiene conexion
directa a IP publica validada, Host y sni_hostname del DNS original,
TLS verify=True y check_hostname=True, sin carreras de segunda resolucion.

Una conexion con IP en request.url puede contaminar el jar de cookies
si HTTPX extrae Set-Cookie usando la IP en lugar del nombre original.
Con un cliente temporal de transporte nativo las cookies no regresaban al
jar del cliente original; con un transporte inyectado podian guardarse
como cookies de IP o ignorar Domain=.dzen.ru.

stream_pinned conserva una copia del request original antes de sustituir
el hostname por la IP. Despues de verificar la IP conectada y antes de
entregar la respuesta restaura response.request con esa copia y llama
client.cookies.extract_cookies(response), usando el dominio original.
Los requests siguientes usan client.build_request con la URL de dominio,
por lo que incluyen solo cookies que correspondan a ese hostname y a
su Path/Secure; no transportan cookies de api.bilibili.com al CDN.

Se aisla tambien el envio para transportes inyectados no MockTransport:
un wrapper prestado evita cerrar el transporte original y evita que
client.send extraiga cookies para la IP antes de corregir el origen.
El request de red permanece IP-pinneado, y response.url y
response.request.url visibles a adaptadores siguen apuntando al hostname
original. Las redirecciones relativas se resuelven contra el origen
real, nunca contra la IP.

Pruebas sin red: Set-Cookie Domain=.dzen.ru seguido de Cookie correcto;
cookie host-only de api.bilibili.com; aislamiento al CDN; Location relativa
en descargas y URL visible; regresiones M24 r7, TLS y HLS anteriores.
Este PR no representa evidencia de smoke live ni cambia capacidades.

### Wikimedia Commons: búsqueda geográfica

Cuando se detecta un nombre propio, Commons intenta resolver categorías mediante
MediaWiki (namespace 14), con coincidencia exacta normalizada y prioridad para
`Videos of X`. Se aceptan los alias documentados Rapa Nui, Easter Island e Isla de
Pascua; no se crean alias a partir de la consulta libre. Se recorren hasta seis
categorías, dos niveles de profundidad y 50 miembros por petición (sin paginación
adicional), con un máximo de tres segundos o la mitad del presupuesto del adaptador.
Las peticiones son seriales por adaptador, usan el User-Agent del proyecto y maxlag=5.
Los videos encontrados por categoría preceden a los de texto y se deduplican por ID.
Sin categoría, o si falla su API, continúa la búsqueda textual. El registro conserva
el título original, `commons_category` y `commons_place_aliases` opcionales;
la categoría aporta evidencia de pertinencia, no una garantía del contenido visual.
Se mantienen las verificaciones existentes de licencia, MIME, HTTPS y descarga
segura; WebM/OGV se normalizan a MP4 por el pipeline habitual. En escenas con nombre
propio Commons se programa antes que los bancos stock, sin excluir otras fuentes.
No se incorporan los bancos oficiales Imagen de Chile ni Sernatur: sus términos
prohíben el acceso automatizado, la minería de datos y la redistribución.
