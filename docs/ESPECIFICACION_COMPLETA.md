# Clipoteca Comunitaria — Especificación v1.0

**Corte de diseño:** 20 de noviembre de 2026  
**Estado:** especificación y roadmap; no representa software implementado o desplegado.  
**Objetivo de infraestructura adicional:** USD 0 dentro de las cuotas y políticas de los servicios.  

## Objetivo

Biblioteca pública y acumulativa de escenas audiovisuales de **hasta 20 segundos**, accesible por **API REST y MCP remoto**, sin interfaz Astro. Una consulta como «lluvia» primero busca el catálogo local y además puede desencadenar **búsquedas paralelas en español, inglés, chino y ruso**; los resultados nuevos con archivo accesible se descargan, normalizan, describen en **español e inglés**, deduplican y publican automáticamente en **GitHub Releases**. Las siguientes consultas reutilizan lo ya guardado.

## Decisiones fijas de v1

- Repositorio de código **público**, videos en GitHub Releases, catálogo público versionado en Git.
- API y MCP en **Cloudflare Workers (TypeScript)**; orquestación y transformación en **GitHub Actions (Python + FFmpeg)**.
- **Sin Oracle, sin Astro, sin créditos, sin autenticación de usuario propia, sin servidor 24/7**.
- **Cuatro idiomas de búsqueda obligatorios en cada ronda externa:** `es`, `en`, `zh`, `ru`. Metadatos publicados siempre `es` + `en`.
- Orientación conservada y clasificada: `9:16`, `16:9`, `1:1` u `other` con dimensiones reales.
- Búsqueda local siempre primero. Ingesta externa asíncrona e idempotente; jamás volver a descargar un archivo ya catalogado.
- Contribución comunitaria en v1 mediante Issue con enlace de descarga o adjunto dentro del límite vigente de GitHub.
- Las reglas comunitarias de autorización y licencias de clips se discutirán después. No formar parte de los hitos técnicos de esta versión; se conserva procedencia factual para decisiones posteriores.

## Documentación

1. [Alcance y requisitos](docs/PROYECTO.md)
2. [Arquitectura y flujo](docs/ARQUITECTURA.md)
3. [Proveedores e idiomas — **obligatorios**](docs/FUENTES_IDIOMAS.md)
4. [Contratos API, MCP y metadatos](docs/CONTRATOS_API_MCP.md)
5. [Roadmap y criterios de aceptación](docs/ROADMAP.md)
6. [QA, seguridad y operación](docs/QA_OPERACION.md)

## Condición central de aceptación

Una búsqueda de «lluvia» produce solicitudes `lluvia`, `rain`, `下雨`, `дождь` contra el motor de proveedores; identifica material que no está en GitHub, obtiene un archivo accesible, lo normaliza a MP4 H.264 de hasta 20 segundos, conserva 9:16 o 16:9 según corresponda, genera títulos y etiquetas `es`/`en`, publica el recurso en Release, lo añade al catálogo y permite recuperarlo por REST y MCP. Una búsqueda repetida no descarga ni publica otra vez el mismo video.

> Esta carpeta contiene el proyecto documental listo para un repositorio. Los módulos, endpoints y conectores descritos son **objetivos de implementación**, no funciones ya operativas.


---

# PRD — Clipoteca Comunitaria

## 1. Problema y propuesta

Los generadores de video vuelven a buscar repetidamente tomas similares en múltiples plataformas. Se propone un sistema público que **acumula clips** al buscar y los expone de forma normalizada a agentes y aplicaciones.

## 2. Usuarios y casos de uso

- Agente de edición: `search_clips("lluvia", aspect_ratio="9:16", max_duration=20)` → encuentra clips existentes, puede solicitar nuevas búsquedas, obtiene el MP4.
- Integración por REST: solicita escenas según descripción, duración, resolución, orientación y fuente, y descarga por URL de GitHub.
- Colaborador: entrega un enlace o adjunto de video mediante un Issue; el mismo pipeline verifica y procesa sin moderación técnica manual.
- Mantenedor: consulta errores por proveedor, reintenta trabajos y audita cambios sin depender de una infraestructura privada.

## 3. Requisitos funcionales (obligatorios)

**RF-01** Buscar catálogo local antes de salir a Internet.  
**RF-02** Traducir o expandir **cada consulta** a `es`, `en`, `zh`, `ru` sin perder el concepto.  
**RF-03** Enviar consultas en paralelo a proveedores habilitados, con máximo cuatro conectores simultáneos por defecto y límite de **15 s para la ronda de descubrimiento**; YouTube no puede bloquear las demás fuentes.  
**RF-04** Importar automáticamente cada archivo nuevo recuperable, condicionado a controles de límites técnicos y disponibilidad, sin repetir descargas.  
**RF-05** Normalizar a MP4/H.264/yuv420p, duración <=20 s, conservar la relación de aspecto sin deformación.  
**RF-06** Guardar ancho, alto, FPS, duración, relación/orientación, tamaño, SHA256 y hash perceptual.  
**RF-07** Publicar metadatos `title`, `description`, `tags` siempre en español e inglés, preservando nombre, idioma y fuente originales.  
**RF-08** Generar miniatura limpia y versión de vista previa opcional.  
**RF-09** Publicar MP4 y miniatura en Releases; actualizar el catálogo Git solo tras verificar el asset.  
**RF-10** Exponer REST y MCP remoto con búsqueda, detalle, descarga, inicio de ingesta y estado.  
**RF-11** Admitir aportes comunitarios por Issue/URL con el mismo procesamiento automático.  
**RF-12** Controlar errores y reintentos, idempotencia, cuotas y origen sin despliegue en Oracle.

## 4. No objetivos de v1

No Astro, frontend, cuentas propias, créditos, pagos, PostgreSQL, D1, GPU, modelos generativos pesados, microservicios, panel de moderadores, sistema de autorizaciones comunitarias ni búsqueda por embeddings. El tema de autorización y políticas de publicación comunitarias se revisará más adelante; **el diseño técnico no equivale a una garantía de disponibilidad o permisos de cada plataforma**.

## 5. Reglas editoriales/técnicas del clip

- Duración objetivo: 1–20 s. Fuentes mayores se recortan al comienzo para v1; cortes inteligentes posteriores.
- Preferencia 1080p sin aumentar artificialmente resolución; no deformar; orientaciones 9:16, 16:9, 1:1, other.
- Audio opcional; guardar `has_audio`. No agregar música ni marcas al material.
- Evitar archivos corruptos, vacíos o ilegibles; rechazar si FFmpeg no puede procesarlos.
- No prometer eliminación automática confiable de marcas de agua; detectar o clasificar posible superposición cuando sea factible.
- Metadatos originales de fuente, enlace y fecha de incorporación inmutables para trazabilidad.

## 6. Criterios de éxito inicial

- Prueba completa con clips reales: cuatro traducciones en una sola consulta; búsqueda distribuida; al menos una fuente recuperable; MP4 <=20 s; ambas lenguas de metadatos; publicación y descarga API/MCP.
- 0 duplicados exactos al repetir la misma petición; 0 transcodificaciones si el archivo ya existe.
- 100% de archivos publicados con width, height, duration, fps, aspect_ratio y títulos en es/en.
- No se filtran secretos en repositorio ni logs.
- Los errores de un proveedor no bloquean resultados de otros.


---

# Arquitectura — sin Oracle

## Componentes y responsabilidades

```text
Cliente de IA / aplicación externa
        │
        ├── /mcp (Streamable HTTP, MCP 2026-07-28)
        └── /v1/* (REST)
               │
          Cloudflare Worker (TypeScript)
             ├─ consulta catálogo público de GitHub (cache corto)
             ├─ responde coincidencias ya publicadas
             ├─ valida cuota, entrada y token para operaciones de escritura
             └─ dispatch del workflow para nuevas búsquedas
                         │
                GitHub Actions (Python 3.13 + FFmpeg)
                 ├─ expandir ES/EN/ZH/RU
                 ├─ buscar en proveedores, concurrencia=4, discovery<=15s
                 ├─ resolver URL de archivo / descargar
                 ├─ deduplicar y procesar FFmpeg
                 ├─ traducir metadata a ES/EN
                 └─ publicar Release + actualizar catálogo
                         │
                GitHub Releases (MP4, JPG/WebP)
                Git versionado (data/clips.json + data/indexes)
```

**No hay un servidor Python permanente.** Cloudflare no transcodifica, ni recibe archivos de gran tamaño como parte del flujo principal. El Worker jamás actúa como proxy de streaming del MP4: devuelve URL directa de Release.

## Decisiones de implementación

- **TypeScript:** Cloudflare Worker, OpenAPI/REST, MCP TS SDK v2, validación de consultas y cuotas. HTTP sin estado.
- **Python 3.13:** adaptadores externos, trabajo paralelo (`asyncio`, `httpx`), traducción offline, FFmpeg/ffprobe, hashes y GitHub API.
- **Almacenamiento:** Releases para binarios, Git para catálogo e índices. No guardar MP4 en commits ni LFS.
- **Mensajería mínima:** `workflow_dispatch` de GitHub. El request devuelve `202 queued` y un `request_id` determinista; Actions escribe estado duradero con el mismo ID. Si un workflow no arranca, la API refleja `queued`/`unknown` y permite reintento.
- **Concurrencia:** búsqueda concurrente limitada, **publicación del catálogo serializada** por `concurrency.group` de Actions. Escrituras con SHA actual y reintento ante conflicto 409.
- **Deduplicación:** 1º `provider:source_id`; 2º SHA256 de original; 3º hash perceptual de frames muestreados. La similitud dudosa nunca fusiona automáticamente dos clips distintos sin suficientes coincidencias.
- **Ingesta idempotente:** clave `sha256(canonical_query + filters + horizon)` para trabajos; claves permanentes `provider:id` y `video_sha256` para objetos.
- **Observabilidad:** conteos por idioma/proveedor, encontrados, archivos obtenibles, fallidos, publicados, duplicados, uso de runner; no guardar tokens o URLs firmadas en logs.

## Estados de un trabajo

`queued → searching → fetching → processing → publishing → published`  
Ramas de salida: `no_results | duplicate | failed | rate_limited | unavailable`.

La API responde resultados existentes inmediatamente; los nuevos aparecen al terminar Actions. No prometer respuesta sincrónica de descarga para un video que aún no existe.

## GitHub: formato sugerido

- Releases agrupadas por lote/fecha (`batch-2026-11-20-001`) para no crear un Release por archivo.
- Assets: `clip_<id>.mp4`, `thumb_<id>.webp`, con nombres deterministas.
- `data/clips.json` como listado canónico; cuando crezca, `data/index/es/*.json` y `data/index/en/*.json` para búsqueda rápida en Workers.
- Cada publicación primero sube el video, comprueba el asset por API y **solo después** agrega la entrada al índice.
- Publicación de estados e índices serializada; resultados de búsqueda externos se pueden paralelizar.

## Estrategia del MVP de aporte humano

- Issue con plantilla `Aportar escena`, campos URL/título/etiquetas opcionales. Action con permisos mínimos comprueba el enlace, descarga y procesa.
- El adjunto directo de video en GitHub Free está limitado a 10 MB según documentación de septiembre 2026; archivos mayores requieren URL de descarga accesible o, en una fase posterior, una entrada temporal de objetos.
- No exponer el token que crea Releases a PRs no confiables; la entrada del Issue se considera texto/datos y no comandos ejecutables.

## Riesgos de plataforma a evaluar antes de escalar

Releases es un mecanismo de distribución de software y GitHub puede limitar patrones de uso excesivos. No es almacenamiento multimedia infinito garantizado. Objetivo de costo $0 es condicional: acciones gratuitas en repos públicos estándar, Workers dentro de cuota y proveedores sin costes externos. Si hay restricciones, el adaptador `storage` se cambia sin afectar a MCP/API.


---

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
| Douyin | plataforma china | investigar integración oficial, documentar barreras y entregar adaptador de descubrimiento cuando exista vía accesible | M6 |
| TikTok | video social | API oficial de usuario autorizado; evaluación de búsqueda pública general como capacidad separada | M6 |
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


---

# Contratos — REST, MCP y catálogo

## Endpoints REST

| Método | Ruta | Función | Autorización |
|---|---|---|---|
| GET | `/v1/health` | estado de API/MCP | pública |
| GET | `/v1/clips?q=lluvia&aspect_ratio=9%3A16&max_duration=20` | catálogo local | pública |
| GET | `/v1/clips/{id}` | ficha, meta, miniatura | pública |
| GET | `/v1/clips/{id}/download` | redirección a asset GitHub Releases | pública |
| POST | `/v1/search` | busca local y opcionalmente solicita nuevo trabajo externo | limitada por cuota; escritura protegida |
| GET | `/v1/jobs/{request_id}` | progreso de descubrimiento/importación | pública para estado no sensible |
| POST | `/v1/import` | solicita ingesta externa | credencial/permiso específico |
| POST | `/v1/contributions` | registra Issue/URL de contribución | autenticación GitHub o control antiabuso |

`GET /v1/clips` jamás dispara trabajo costoso. `POST /v1/search` puede devolver `200` con coincidencias o `202` con coincidencias y `request_id` de búsqueda ampliada. El sistema devuelve enlaces de descarga cuando el archivo ya fue publicado; antes de eso solo hay `processing`.

## Herramientas MCP remotas (SDK oficial TS v2, Streamable HTTP)

- `search_clips(query, aspect_ratio?, max_duration?, min_width?, source?, limit?, import_if_missing?)`: primero catálogo; opcionalmente solicita ronda externa.
- `get_clip(clip_id)`: ficha verificable de un video.
- `download_clip(clip_id)`: enlace a MP4 en GitHub Releases.
- `search_and_import(query, aspect_ratio?, max_results?)`: solicita nueva búsqueda + persistencia asíncrona; operación con permisos.
- `get_job_status(request_id)`: estado de trabajo.
- `list_sources()`: adaptadores configurados y estados reales.

Ninguna respuesta MCP incluye bytes de video embebidos. Debe retornar objetos estructurados estables y URLs directas al almacenamiento.

## Metadatos publicados, esquema v1

```json
{
  "id": "clip-000125",
  "title": {"es": "Lluvia en las calles", "en": "Rain on the streets"},
  "description": {"es": "Calle mojada de noche", "en": "Wet street at night"},
  "tags": {"es": ["lluvia", "calle"], "en": ["rain", "street"]},
  "source": {
    "provider": "rutube",
    "provider_id": "source-id",
    "source_language": "ru",
    "source_title": "Дождь на улице",
    "source_url": "https://example.org/original/123"
  },
  "media": {
    "duration_seconds": 18.2,
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "aspect_ratio": "9:16",
    "orientation": "vertical",
    "codec": "h264",
    "container": "mp4",
    "has_audio": false,
    "size_bytes": 14300000,
    "sha256": "<sha256-real-al-publicar>",
    "perceptual_hash": "<hash-real-al-publicar>"
  },
  "storage": {
    "provider": "github_releases",
    "asset_url": "<url-real-al-publicar>",
    "thumbnail_url": "<url-real-al-publicar>"
  },
  "status": "published",
  "created_at": "2026-11-20T00:00:00Z"
}
```

**Los ejemplos no son registros reales.** No mostrar URLs inventadas como si un archivo estuviera publicado.

## Clasificación precisa de orientación

Calcular con width/height del stream de video, incorporando rotación de metadatos:  
- Igualdad/aspecto próximo a 9:16 → `9:16`, `vertical`.
- Próximo a 16:9 → `16:9`, `horizontal`.
- Próximo a 1:1 → `1:1`, `square`.
- Otro → `other` con razón reducida (`4:3`, `21:9`, etc.).

Usar tolerancia definida y testeada (p. ej. `abs(w/h - target) < 0.01`); no recortar para convertir orientación. Guardar width/height reales de salida, no deducirlos del nombre de archivo.

## Semántica de búsqueda

- Orden: resultados existentes, relevantes y accesibles; `import_if_missing` inicia nueva ronda sin bloquear.
- Indexar cadenas Unicode normalizadas y ambas lenguas de metadatos; guardar también keywords del idioma original para dedup y trazabilidad.
- Filtros: `aspect_ratio`, `max_duration`, `min_width`, `fps`, `source`, `language`, `has_audio`.
- Paginación con cursor estable cuando el catálogo supere 1000 clips.


---

# ROADMAP de implementación — Clipoteca Comunitaria

**Objetivo:** MVP gratuito, público y demostrable, sin Oracle, Astro ni créditos. Cada hito exige código + tests + evidencia; un hito no cuenta como terminado solo por tener documentos.

## Bloque A — base ejecutable

- [ ] **M0 · Crear repo y contratos (1 PR).** Estructura TS/Python, licencia del código que se autorice publicar, `README`, ADR, `CONTRIBUTING`, pyproject/lockfiles, CI (Ruff/pytest/TypeScript), pruebas sin red. **Aceptación:** CI verde, JSON Schema de clip y búsqueda, contratos API/MCP revisados.
- [ ] **M1 · Catálogo y Releases (1–2 PR).** `storage/github.py` y adaptador de lectura Worker, publicación por lotes, comprobación de asset, índices y locks, descarga directa. **Aceptación:** publicar clip de prueba, encontrar por API, descargar y repetir sin duplicar.
- [ ] **M2 · FFmpeg y metadatos (1–2 PR).** ffprobe, recorte <=20s, MP4 H.264, FPS, yuv420p, respeto de aspecto, thumbnail WebP, SHA256, pHash. **Aceptación:** fixtures horizontal 16:9, vertical 9:16, 1:1, 4:3, corrupto, >20s, codecs distintos; catalogación correcta.

## Bloque B — búsquedas OBLIGATORIAS

- [ ] **M3 · Motor multilingüe (2 PR).** `es/en/zh/ru` forzados, traducción local y fallback, consultas paralelas con semáforo=4, ventana de 15s para descubrimiento, timeouts por fuente y deduplicación. **Aceptación:** una petición «lluvia» evidencia exactamente `lluvia`, `rain`, `下雨`, `дождь`; consulta fallida no bloquea las otras.
- [ ] **M4 · 5 proveedores básicos (3–5 PR).** **Pexels, Pixabay, Wikimedia Commons, Internet Archive, NASA** con tests de búsqueda, resolver descarga, procesar y publicar cuando haya video accesible. **Aceptación:** matriz de capacidades real, fixtures por proveedor y publicación E2E desde al menos 2 proveedores distintos.
- [ ] **M5 · 4 proveedores globales/RU (3–4 PR).** **YouTube, RuTube, PeerTube, Odysee/LBRY**. YouTube corre como descubrimiento no bloqueante. **Aceptación:** búsqueda ES/EN/ZH/RU sobre los conectores que la acepten, diagnóstico fiel `search_only` vs `search_download`, y un video real descargado e importado desde alguna fuente de este bloque con acceso disponible.
- [ ] **M6 · China/Rusia y redes sociales (3–5 PR).** **Bilibili, VK Video, Douyin, TikTok, Instagram Reels, Snapchat**. Investigar endpoints, implementar interfaces de descubrimiento donde existen y estados de soporte explícitos donde no. **Aceptación:** cada proveedor aparece en `list_sources` y su fixture verifica búsquedas, o declara con precisión el límite técnico y la prueba realizada. Ninguno queda ignorado.

## Bloque C — agente completo y uso comunitario

- [ ] **M7 · API + MCP remoto (2–3 PR).** Worker TypeScript, SDK oficial TS v2/Streamable HTTP, `GET /v1/clips`, `GET /v1/clips/{id}`, download, `POST /v1/search`, job status y herramientas MCP. **Aceptación:** agente encuentra «lluvia 9:16», recibe URL real y descarga; una búsqueda de importación devuelve `202` con estado comprobable.
- [ ] **M8 · Ingesta automática / GitHub Actions (2 PR).** Dispatch del Worker, Actions confiables, permisos mínimos, `concurrency`, publicación idempotente, retries, estados, métricas. **Aceptación:** búsqueda desde agente dispara publicación sin operación manual y repetición no duplica clips; no se filtran secretos.
- [ ] **M9 · Aportes de la comunidad (1–2 PR).** Plantilla GitHub Issue, URL/adjunto, ingesta automatizada idéntica a proveedor, mensajes de fallo. **Aceptación:** colaborador sin acceso de escritura puede entregar clip y verlo catalogado tras procesamiento; documentado límite de adjuntos GitHub Free.
- [ ] **M10 · Resiliencia y cierre v1 (1–2 PR).** Smoke tests sobre fuentes reales, ratelimits, SSRF, reintentos, dead letters simples, rollback catálogo, documentación de despliegue/operación, cobertura de 4 lenguas/15 fuentes. **Aceptación:** demostración E2E reproducible, CI verde, informe de proveedores y costos/uso reales.

## Camino crítico (orden de ejecución)

`M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → M9 → M10`

Se pueden comenzar contratos REST/MCP antes, pero **no cerrar la versión 1.0 sin los cuatro idiomas ni la matriz de todos los proveedores comprometidos**. Proveedores sin vía pública de archivo deben estar documentados como búsqueda solamente/no disponibles; no inventar descarga.

## Criterios de lanzamiento de la versión 1.0

- [ ] Consulta `lluvia` ejecuta y registra los cuatro idiomas.
- [ ] Motor funciona con varias fuentes y fallos aislados.
- [ ] Descarga de video accesible → FFmpeg → thumbnail → GitHub Release → JSON → búsqueda y descarga API/MCP.
- [ ] Clasificación verificada 9:16, 16:9, 1:1 y otros.
- [ ] Metadatos siempre en español e inglés, incluso origen ruso o chino.
- [ ] Duplicados y trabajos simultáneos no generan entradas repetidas o corrupción de `clips.json`.
- [ ] Aporte mediante Issue/URL ingestado automáticamente.
- [ ] Tokens seguros; GitHub Actions solo desde rama confiable; límites de cuotas.
- [ ] Todo proveedor indicado tiene adaptador o evidencia de limitación técnica reproducible.
- [ ] Los tests corren en CI y la demostración final usa archivos reales.

## Próxima evolución, posterior a 1.0 (fuera de alcance)

Créditos/aportes obligatorios, página web Astro, búsqueda semántica/embeddings, almacenamiento alternativo R2/Hugging Face si escala, miniaturas animadas, ranking de calidad avanzado, incorporación de acuerdos comunitarios de uso y licencias. Estos elementos **no bloquean** el desarrollo técnico inicial.


---

# QA, protección operativa y costos

## Pruebas automáticas

| Capa | Fixtures obligatorias | Resultado esperado |
|---|---|---|
| Idiomas | `lluvia`, traducciones ES/EN/ZH/RU, caracteres Unicode | se ejecutan cuatro búsquedas y se guarda `es`/`en` |
| Proveedores | respuesta válida/vacía/429/500/timeout, HTML no media | conector aislado con estado fiel |
| Medios | 9:16, 16:9, cuadrado, 4:3, rotación, VFR, 24s, corrupto | dimensiones calculadas y MP4 <=20s o fallo explícito |
| Duplicados | misma fuente, mismo SHA, distintas resoluciones de la misma toma | ninguna entrada doble exacta |
| Releases | asset subido y validado; subida incompleta | el JSON solo indexa assets verificados |
| Paralelismo | dos búsquedas del mismo tema a la vez | estado serializado, sin commits perdidos |
| API/MCP | filtros, paginación, 202, 404, herramientas | respuestas tipadas, URL real, sin bytes MP4 |
| Seguridad | URL local/metadata endpoint/redirección, ZIP bomb, 1GB fake | bloqueado con límites claros |

## Controles técnicos esenciales

- URLs externas validadas y redirecciones revalidadas: bloquear IP internas, `localhost`, metadatos cloud, esquemas fuera de HTTPS, puertos peligrosos y cambios DNS (SSRF).
- Descargar con `Content-Length` máximo configurable, límite de bytes real, timeouts y eliminación de temporales; no permitir archivos enormes por un clip de 20 s.
- FFmpeg desde paquetes de runner, proceso con límites de CPU/tiempo/memoria/espacio, nombres seguros, sandbox del job, sin comandos de shell derivados de entradas.
- Escaneos/metadata no ejecutan scripts del clip; nunca ejecutar PR de terceros con `GITHUB_TOKEN` de escritura.
- Tokens GitHub con permisos mínimos, secreto Worker para dispatch, claves de proveedores solo en Actions Secrets, logs con redacción.
- API pública de lectura; crear trabajos/invocar importación requiere autorización o límites antiabuso explícitos (sin necesidad de cuentas propias).
- Uso de `concurrency` GitHub en publicación y escrituras del catálogo; retries de conflictos SHA.
- Salida no falsificada: si no hay URL real de media, devolver `search_only`, no simular descarga.

## Límites de plataforma verificados en documentación de noviembre 2026

- GitHub Releases: hasta **1.000 assets por Release** y **<2 GiB por archivo**; no equivale a espacio gratis ilimitado garantizado.
- GitHub Actions: runners estándar gratuitos en repositorios públicos, con políticas de uso y límites de concurrencia/duración.
- GitHub adjuntos de video: **10 MB en GitHub Free** (documentación actualizada en septiembre 2026); subir por URL para mayores.
- Cloudflare Workers Free: **100.000 solicitudes/día**, límite de CPU por invocación; solo API/metadata, no ejecutar FFmpeg allí.
- Traducción offline: modelos Argos (`zh`, `ru`, `en`, `es`) empaquetados/fijados en runner. Si el paquete exacto falla, registrar error, no inventar texto traducido.

## Indicadores (observabilidad sin base de datos)

- `search_requests_total`, `search_requests_by_language`, `provider_success_rate`, `provider_timeout_rate`.
- `new_candidates`, `downloaded_clips`, `deduplicated_clips`, `published_clips`, `failed_imports`.
- `mean_discovery_duration`, `mean_publish_duration`, `github_actions_minutes`, `assets_storage_estimate`, `cloudflare_requests`.
- Registrar por workflow en un reporte JSON o summary de Actions, y sumar en reporte de operaciones versionado periódicamente.

## Runbook mínimo

1. Si falla un proveedor: verificar su estado en `list_sources`, no detener los demás.
2. Si falla publicación: no indexar el asset; reintentar después de verificar nombre/hash.
3. Si hay duplicado: retornar el ID existente y no consumir nuevamente red/CPU.
4. Si hay conflicto de `clips.json`: releer SHA y fusionar por IDs, luego commit serializado.
5. Si se supera cuota gratuita: pausar dispatch de búsquedas externas, mantener API de lectura y descargas existente.
6. Si GitHub limita distribución multimedia: suspender nuevas subidas y cambiar el adaptador de almacenamiento sin pérdida del catálogo.

## Documentación oficial para mantener actualizada

- https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- https://docs.github.com/en/actions/concepts/billing-and-usage
- https://docs.github.com/en/rest/actions/workflows
- https://developers.cloudflare.com/workers/platform/limits/
- https://github.com/modelcontextprotocol/typescript-sdk
- https://github.blog/changelog/2026-09-01-github-cli-media-in-issues-pull-requests-and-comments/