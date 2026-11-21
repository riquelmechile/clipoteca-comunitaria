# Búsquedas internacionales y proveedores — requisito no negociable

## Idiomas obligatorios

**Ninguna implementación se considera completa sin ejecutar búsquedas en cuatro idiomas por cada concepto cuando se pide búsqueda externa:**

| Código | Ejemplo con lluvia | Qué se guarda |
|---|---|---|
| `es` | `lluvia` | término usado, resultado, idioma origen |
| `en` | `rain` | término usado, resultado, idioma origen |
| `zh` | `下雨` | término usado, resultado, idioma origen |
| `ru` | `дождь` | término usado, resultado, idioma origen |

La expansión usa diccionario de términos comunes + traducción local (`Argos Translate` con paquetes de modelos fijados y cacheados) + normalización; puede conservar sinónimos (`lluvia fuerte`, `heavy rain`, `暴雨`, `сильный дождь`) en búsquedas ampliadas. Si una traducción falla, la corrida informa `translation_failed` y no inventa una consulta. Metadatos finales `es` y `en` aunque la fuente esté en `zh`/`ru`.

## Proveedores dentro del roadmap obligatorio

Esta tabla **no equivale a garantizar una API pública de descarga**. Cada adaptador distingue `search`, `metadata`, `direct_media`, `rate_limit`, `auth`, `origin_id` y `errors`. Descarga y publicación automáticas siempre que el archivo sea realmente accesible mediante la integración. Nunca marcar `published` cuando solo se encontró una página o miniatura.

| Plataforma | Categoría | Estrategia de integración | Hito |
|---|---|---|---|
| Pexels | video stock | API oficial de búsqueda de videos + resolutor de variantes | M4 |
| Pixabay | video stock | API oficial de videos + resolutor MP4 | M4 |
| Wikimedia Commons | archivos públicos | API MediaWiki/Commons para búsqueda y archivo | M4 |
| Internet Archive | archivos de archivo | Advanced Search + Item Metadata/descarga | M4 |
| NASA Images & Video | video espacial | API Images NASA + detalles de asset | M4 |
| YouTube | video global | YouTube Data API para descubrimiento; resolver disponibilidad del archivo separadamente; **no bloquear** otras fuentes | M5 |
| RuTube | plataforma rusa | adaptador para búsqueda/metadata pública comprobada; probar extracción técnica y fallas | M5 |
| PeerTube | video federado | REST API / federación y enlaces de archivo | M5 |
| Odysee/LBRY | video distribuido | búsqueda claims y resolución del contenido cuando exista | M5 |
| Bilibili | plataforma china | investigar API oficial/accesible y crear adaptador con diagnóstico de límites | M6 |
| VK Video | plataforma rusa | API/video.search cuando esté disponible y autorizado técnicamente | M6 |
| Douyin | video social de China | búsqueda web por invitado y cookie opcional, descarga best_effort y firmas Python; idiomas es/en/zh/ru aún no medidos semánticamente live | M19 |
| TikTok | video social | búsqueda web, paginación por search_id y cookie sólo si exige sesión, descarga best_effort; idiomas es/en/zh/ru pendientes de verificación live | M19 |
| Instagram Reels | video social | evaluar capacidades para contenido propio/autorizado; búsquedas globales requieren acceso específico | M6 |
| Snapchat | video social | evaluar APIs oficiales, feeds disponibles y condiciones técnicas | M6 |

La cobertura por **idioma** y por **fuente** es doble: buscar en ruso **no** es sinónimo de haber buscado en RuTube; `ru` se prueba para cada adaptador que acepte texto y se prueban plataformas rusas/chinas además de motores internacionales.

## Política de fallos por fuente

- `SUPPORTED_SEARCH_DOWNLOAD`: resultados y archivo descargable disponible.
- `SUPPORTED_SEARCH_ONLY`: metadatos accesibles, pero no URL de media; no crear clips falsos.
- `AUTH_REQUIRED`: credencial que aún no se ha instalado.
- `RATE_LIMITED`: reintento por proveedor con backoff y límite global.
- `UNAVAILABLE`: endpoint inexistente, cambio de plataforma o acceso técnico no disponible.
- `TIMEOUT`: agotó su propio plazo; los otros adaptadores siguen.

**Condición de M6:** se entrega una matriz de cobertura real con tests/fixtures para todas las fuentes anteriores. Los proveedores que carezcan de interfaz de descarga accesible se incluyen como `search_only`/`unavailable`, nunca se ignoran silenciosamente ni se sustituyen por un proveedor distinto.

## Concurrencia

- Predeterminado `4` adaptadores simultáneos, configurable por Actions.
- Ventana global de **descubrimiento 15 segundos**; resolver y descargar archivos ocurren en fase separada, con timeouts propios.
- Circuit breaker simple por proveedor, límite de páginas y máximo de candidatos; YouTube se consulta sin esperarlo al terminar la ventana.
- Búsqueda local previa; prohibido repetir descarga si `source_id`/hash ya existen.

## Referencias técnicas principales (20-11-2026)

- Pexels: https://www.pexels.com/api/documentation/
- Pixabay: https://pixabay.com/api/docs/
- YouTube search: https://developers.google.com/youtube/v3/docs/search/list
- Wikimedia Commons: https://www.mediawiki.org/wiki/API:Search
- Internet Archive: https://archive.org/developers/
- NASA: https://api.nasa.gov/ y https://images.nasa.gov/docs/images.nasa.gov_api_docs.pdf
- PeerTube: https://docs.joinpeertube.org/api-rest-reference/api/video.html
- TikTok: https://developers.tiktok.com/docs/en/display-api-overview
- VK schema: https://github.com/VKCOM/vk-api-schema
- Argos Translate: https://github.com/argosopentech/argos-translate
