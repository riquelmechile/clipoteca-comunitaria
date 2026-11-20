# Clipoteca v1: matriz técnica de proveedores M4

Fecha de verificación documental: 20-11-2026. El estado es de **implementación y tests con fixtures**, no prueba de descarga real ni de disponibilidad contractual. El servidor registra errores por consulta e idioma; cada candidato solo se publica si tiene un archivo verificable y supera FFmpeg, dedup y GitHub Releases.

| Fuente | Búsqueda | Metadatos | Archivo directo | Autenticación | Limitación/estado |
|---|---|---|---|---|---|
| Pexels | `GET https://api.pexels.com/v1/videos/search` | `video_files` / detalle | MP4 HTTPS escogido sin aumentar resolución | `PEXELS_API_KEY` obligatoria | `AUTH_REQUIRED` sin clave; `SUPPORTED_SEARCH_DOWNLOAD` solo si hay variante real |
| Pixabay | `GET https://pixabay.com/api/videos/` | `hits[].videos` | `large/medium/small/tiny` MP4 | `PIXABAY_API_KEY` obligatoria | `AUTH_REQUIRED` sin clave; `SUPPORTED_SEARCH_ONLY` por candidato sin MP4 |
| Wikimedia Commons | MediaWiki `generator=search`, namespace File | `imageinfo` | `imageinfo.url`, MIME video | Pública; User-Agent identificable | Filtra imágenes y MIME no video; no fuerza falsas descargas |
| Internet Archive | `/advancedsearch.php` (`mediatype:movies`) | `/metadata/{identifier}` | `/download/{identifier}/{file}` | Pública | Rechaza solo HTML/imagen, tamaño > 1 GiB o sin derivado de video |
| NASA | `https://images-api.nasa.gov/search?media_type=video` | `collection.items[].data` | `/asset/{nasa_id}` entrega MP4 real | `NASA_API_KEY` opcional; por defecto `DEMO_KEY` | `SUPPORTED_SEARCH_ONLY` si manifiesto no tiene MP4 |

**Idiomas:** cada ronda de búsqueda de concepto reconocido envía términos `es/en/zh/ru` al adaptador habilitado. Algunos servicios no interpretan correctamente los cuatro idiomas, pero se registra el idioma/término utilizado, sin afirmar soporte semántico inexistente. Pixabay usa `lang` cuando es aceptado; chino se consulta con el término sin `lang`.

**Errores tipados:** `SUPPORTED_SEARCH_DOWNLOAD`, `SUPPORTED_SEARCH_ONLY`, `AUTH_REQUIRED`, `RATE_LIMITED`, `UNAVAILABLE`, `TIMEOUT`. El estado del adaptador indica capacidad; no equivale a que todo resultado sea descargable. Los HTTP 429/500, respuestas vacías, timeout y HTML falso se verifican sin red en pytest.

**Límites/seguridad:** solo HTTPS, DNS público y redirecciones reevaluadas; Content-Length y bytes reales limitados; formatos MP4/WebM/OGV/Ogg con firma reconocida. **Límite residual que requiere revisión de red antes del despliegue:** el cliente HTTP vuelve a resolver el dominio al conectar, por lo que la comprobación previa por DNS no constituye fijación/pinning de la IP ante DNS rebinding. Se debe introducir transporte con IP verificada antes de aceptar URLs controladas por terceros sin restricciones de hosts.

**Modelos Argos:** se declara `ARGOS_MODEL_CACHE_DIR` y `ARGOS_MODEL_PINS_JSON` con pares `es_en`, `en_es`, `en_zh`, `zh_en`, `en_ru`, `ru_en`. Por cada par se exige `version` y SHA256 real del paquete `.argosmodel`. Solo se instala el paquete cacheado si la huella coincide; la ausencia falla como `translation_failed`. No se publican hashes imaginarios ni se descargan paquetes sin verificar. El diccionario incorpora 200 conceptos traducidos que funcionan sin modelos.

**Estado de ingesta:** Actions `workflow_dispatch` se ejecuta únicamente desde `main`, usa concurrencia `clipoteca-catalog` y emite registros de job/metrics como artefactos. Los archivos `data/jobs` del runner no se reflejan aún en commits Git automáticamente: la recuperación cross-run por API queda pendiente de M7/M8. No se ejecutó publicación real en GitHub Releases ni despliegue.

## Referencias consultadas

- https://www.pexels.com/api/documentation/
- https://pixabay.com/api/docs/
- https://www.mediawiki.org/wiki/API:Imageinfo
- https://archive.org/developers/
- https://api.nasa.gov/ y https://images.nasa.gov/docs/images.nasa.gov_api_docs.pdf
- https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json
