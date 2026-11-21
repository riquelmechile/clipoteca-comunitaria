# Clipoteca v1: matriz técnica de proveedores M4

Fecha de verificación documental: 20-11-2026. El estado es de **implementación y tests con fixtures**, no prueba de descarga real ni de disponibilidad contractual. El servidor registra errores por consulta e idioma; cada candidato solo se publica si tiene un archivo verificable y supera FFmpeg, dedup y GitHub Releases.

| Fuente | Búsqueda | Metadatos | Archivo directo | Autenticación | Limitación/estado |
|---|---|---|---|---|---|
| Pexels | `GET https://api.pexels.com/v1/videos/search` | `video_files` / detalle | MP4 HTTPS escogido sin aumentar resolución | `PEXELS_API_KEY` obligatoria | `AUTH_REQUIRED` sin clave; `SUPPORTED_SEARCH_DOWNLOAD` solo si hay variante real |
| Pixabay | `GET https://pixabay.com/api/videos/` | `hits[].videos` | `large/medium/small/tiny` MP4 | `PIXABAY_API_KEY` obligatoria | `AUTH_REQUIRED` sin clave; `SUPPORTED_SEARCH_ONLY` por candidato sin MP4 |
| Wikimedia Commons | MediaWiki `generator=search`, namespace File | `imageinfo` | `imageinfo.url`, MIME video | Pública; User-Agent identificable | Filtra imágenes y MIME no video; no fuerza falsas descargas |
| Internet Archive | `/advancedsearch.php` (`mediatype:movies`) | `/metadata/{identifier}` | `/download/{identifier}/{file}` | Pública | Rechaza HTML/imagen, tamaño > 350 MiB o sin derivado; prioriza MP4 de mayor tamaño admisible |
| NASA | `https://images-api.nasa.gov/search?media_type=video` | `collection.items[].data` | `/asset/{nasa_id}` entrega MP4 real | `NASA_API_KEY` opcional; por defecto `DEMO_KEY` | `SUPPORTED_SEARCH_ONLY` si manifiesto no tiene MP4 |

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
