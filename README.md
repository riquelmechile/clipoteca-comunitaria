<p align="center">
  <img src="https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/assets/clipoteca-hero.svg" width="100%" alt="Clipoteca: busca en el mundo, conserva la escena. Biblioteca comunitaria de clips de hasta 20 segundos, en español, inglés, chino y ruso, con API REST y MCP. A la derecha, Klipi, la mascota con forma de fotograma y lupa." />
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/assets/clipoteca-typing.svg" width="100%" alt="Consola que escribe consultas de ejemplo: GET /v1/clips?q=lluvia&amp;aspect_ratio=9:16, search_clips con aurora boreal, cuatro idiomas en un solo catálogo, clips de hasta 20 segundos listos para reutilizar." />
</p>

<p align="center">
  <a href="https://github.com/riquelmechile/clipoteca-comunitaria/releases/latest"><img src="https://img.shields.io/github/v/release/riquelmechile/clipoteca-comunitaria?style=flat-square&label=versi%C3%B3n&color=67F5CF&labelColor=0B1731" alt="Última versión publicada" /></a>
  <a href="https://github.com/riquelmechile/clipoteca-comunitaria/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/riquelmechile/clipoteca-comunitaria/ci.yml?branch=main&style=flat-square&label=CI&labelColor=0B1731" alt="Estado de CI en main" /></a>
  <img src="https://img.shields.io/badge/API-REST%20%2B%20MCP-47D8C5?style=flat-square&labelColor=0B1731" alt="API REST y MCP" />
  <img src="https://img.shields.io/badge/idiomas-es%20%C2%B7%20en%20%C2%B7%20zh%20%C2%B7%20ru-5A9EF2?style=flat-square&labelColor=0B1731" alt="Idiomas: español, inglés, chino y ruso" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/licencia-MIT-FFB571?style=flat-square&labelColor=0B1731" alt="Licencia MIT" /></a>
</p>

## Una biblioteca de escenas que se llena sola

Un agente de IA que arma un video necesita «lluvia, vertical, 8 segundos». Hoy cada agente la busca, la descarga y la corta desde cero, una y otra vez.

**Clipoteca** guarda esa escena la primera vez y la entrega al instante la siguiente. Si no la tiene, la sale a buscar en fuentes públicas en **español, inglés, chino y ruso**, la corta a un máximo de 20 segundos y la deja publicada para todos. Sin frontend, sin cuentas y sin servidor propio: una API REST y un servidor MCP.

> **Estado al 22 de noviembre de 2026:** servicio en producción, **13 fuentes** con búsqueda y descarga comprobadas desde GitHub Actions. El detalle medido, con sus límites, está en [docs/ESTADO.md](docs/ESTADO.md).

## Úsala ahora

No necesitas clave para buscar ni para descargar.

**Desde un agente (MCP remoto, Streamable HTTP):**

```json
{
  "mcpServers": {
    "clipoteca": {
      "url": "https://clipoteca-worker.clipoteca.workers.dev/mcp"
    }
  }
}
```

**Desde cualquier programa (REST):**

```bash
curl -sS 'https://clipoteca-worker.clipoteca.workers.dev/v1/clips?q=lluvia&aspect_ratio=9%3A16'
```

La respuesta trae título y descripción en español e inglés, duración, resolución, proporción, la fuente original y la URL del MP4 en GitHub Releases.

| Tool MCP | Para qué sirve |
|---|---|
| `search_clips` | Buscar en el catálogo por concepto, proporción, duración, fuente o idioma |
| `resolve_script` | Entregar un guion de hasta 50 escenas y recibir un clip por escena |
| `get_clip` | Leer la ficha completa de un clip |
| `get_storyboard` | Ver fotogramas del clip antes de descargarlo |
| `download_clip` | Obtener la URL del MP4 |
| `search_and_import` | Pedir que se busque material nuevo (requiere credencial) |
| `get_job_status` | Seguir una búsqueda en curso |
| `list_sources` | Ver el estado de cada fuente |

Los mismos datos están en REST bajo `/v1`: `clips`, `clips/{id}`, `clips/{id}/storyboard`, `clips/{id}/download`, `search`, `script`, `jobs/{request_id}`, `sources`, `import`, `contributions` y `health`. Contrato completo en [docs/openapi.yaml](docs/openapi.yaml) y [docs/mcp-tools.md](docs/mcp-tools.md).

## Cómo funciona

<p align="center">
  <img src="https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/assets/clipoteca-pipeline.svg" width="100%" alt="Diagrama en seis pasos: un agente o persona consulta por REST o MCP; el Worker de Cloudflare recibe la consulta; el catálogo responde si el clip existe. Si falta, GitHub Actions busca en español, inglés, chino y ruso, descarga y corta con FFmpeg a un máximo de 20 segundos y publica en GitHub Releases sin duplicados." />
</p>

- **Si el clip existe**, responde el catálogo (`data/clips.json`) y no se gasta nada más.
- **Si falta** y la consulta lo pide (`import_if_missing`, con cuota, o `/v1/import` con credencial), el Worker despacha una ingesta en GitHub Actions: traduce la consulta, busca en paralelo en todas las fuentes disponibles, descarga, elige la mejor escena y la normaliza con FFmpeg a MP4 H.264 respetando 9:16, 16:9 o 1:1.
- **Lo publicado se queda**: el archivo va a GitHub Releases y su ficha al catálogo, con deduplicación por contenido. La siguiente consulta ya lo encuentra.
- **Dónde corre**: el motor en GitHub Actions, la API y el MCP en un Cloudflare Worker, los datos en este repositorio. No depende de ningún servidor personal.

Cuando el traductor falla en un idioma, la búsqueda sigue con los idiomas que sí resultaron y deja un aviso; nunca se inventa una traducción.

## Video a partir de un guion (v3)

La versión del repositorio es **3.0.0**; la versión desplegada se indica en [ESTADO.md](docs/ESTADO.md).
El motor produce un MP4 desde un guion español de 3 a 12 escenas, hasta 60 segundos,
en 9:16, 16:9 o 1:1. No genera el guion ni usa un modelo de lenguaje.
Cada escena busca una coincidencia fuerte en el catálogo (sim ≥0,27, umbral provisional).
Si no existe, importa desde las fuentes y elige el candidato válido más pertinente,
con material válido del catálogo como respaldo. Se reparten hasta ocho candidatos
entre las consultas y se prueban hasta dos adicionales con consultas simplificadas
si ningún importado supera la evaluación. La biblioteca incorpora todos los
clips importados que pasan los filtros normales de ingesta, incluso si no se usan
en el montaje o el video falla QA. La selección ocurre en paralelo por escena.
La voz, música, títulos y subtítulos se componen y renderizan localmente en Actions;
las 14 compuertas deben aprobar antes de entregar el MP4.
El video final nunca se publica en Releases ni en el catálogo.

<p align="center">
  <img src="https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/assets/clipoteca-studio.svg" width="100%" alt="Guion a MP4: plan, voz, música y escenas en paralelo, indexado separado de clips, ensamblaje y QA, entrega temporal al solicitante." />
</p>

La API REST/MCP y el workflow de entrega están desplegados y probados con
corridas reales en 9:16, 16:9 y 1:1. Los límites conocidos están en
[docs/ESTADO.md](docs/ESTADO.md).

### Cómo pedir un video

Con el token de escritura:

```bash
jq -n --slurpfile script docs/ejemplos/chile-vertical.json \
  '{script:$script[0],index_clips:true}' | \
  curl --fail-with-body -X POST "$CLIPOTECA_URL/v1/videos" \
    -H "Authorization: Bearer $CLIPOTECA_WRITE_TOKEN" \
    -H 'Content-Type: application/json' --data-binary @-
```

Consulta el `status_url` devuelto (reintenta si recibes un 404 transitorio)
hasta que el estado sea `done`,
`failed` o `expired`. Descarga el MP4 desde `file_url` cuando exista.
En MCP, llama a `render_video` con el mismo objeto script e index_clips,
y a `get_video_status` con el id retornado. El id es una credencial de
descarga: mantenlo privado. Los artefactos se conservan siete días.
La cuota de video es de una solicitud cada cinco minutos y cincuenta por día UTC
por IP (best-effort). El token de escritura se exige para solicitar, no para consultar o descargar
con el identificador. El identificador de Actions es su SHA-256, no el secreto.

Consulta la [guía Studio](docs/STUDIO.md), el [workflow](docs/M32_RENDER.md)
y el [roadmap v3](docs/ROADMAP_V3.md).

## Fuentes: lo medido, no lo prometido

Medición del 22 de noviembre de 2026 desde GitHub Actions sobre `main`, con las pruebas en vivo [45082009984](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45082009984) (búsqueda en cuatro idiomas) y [45082001946](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45082001946) (descarga real de un video por fuente).

| Resultado medido | Fuentes |
|---|---|
| **Buscan y descargan** (13) | Pexels, Pixabay, Wikimedia Commons, Internet Archive, NASA, Library of Congress, PeerTube, Odysee, RuTube, OK.ru, VK Video, Dzen, Bilibili |
| **Esperan una API key** (8) | Coverr, Europeana, NARA, DPLA, Smithsonian, YouTube, Instagram, Snapchat |
| **Exigen sesión iniciada** (2) | TikTok y Douyin, apagadas por defecto |

Que una fuente funcione hoy no garantiza que funcione mañana: los proveedores cambian, limitan por IP y bloquean centros de datos. Límites vistos en esta medición: Library of Congress limitó las consultas en chino y ruso; NASA y PeerTube solo devolvieron resultados en inglés; YouTube, DPLA, Smithsonian, Instagram y Snapchat serían solo de búsqueda aun con clave. La tabla por fuente e idioma está en [docs/ESTADO.md](docs/ESTADO.md).

## Aporta una escena

[Abre el formulario «Aportar escena»](https://github.com/riquelmechile/clipoteca-comunitaria/issues/new/choose) con una URL HTTPS directa a un MP4, WebM u OGV y sus datos de procedencia. El Issue se procesa solo: valida, corta, deduplica y publica, sin permisos de escritura ni revisión manual. Si algo falla, el motivo queda comentado en el Issue y editarlo reintenta. Más en la [guía de colaboración](CONTRIBUTING.md).

## Documentación

| Quiero… | Documento |
|---|---|
| Saber qué funciona hoy y qué no | [Estado actual](docs/ESTADO.md) |
| Integrar la API o el MCP | [OpenAPI](docs/openapi.yaml) · [Tools MCP](docs/mcp-tools.md) · [Contratos](docs/CONTRATOS_API_MCP.md) |
| Entender el diseño | [Arquitectura](docs/ARQUITECTURA.md) · [Especificación completa](docs/ESPECIFICACION_COMPLETA.md) |
| Operar o desplegar | [Despliegue](docs/DESPLIEGUE.md) · [Operación v2](docs/OPERACION_V2.md) · [Proveedores](docs/PROVEEDORES.md) |
| Ver cómo se llegó hasta aquí | [Roadmap v1](docs/ROADMAP.md) · [Roadmap v2](docs/ROADMAP_V2.md) · [Roadmap v3](docs/ROADMAP_V3.md) · [Historial de cambios](CHANGELOG.md) |
| Ejecutar el motor de video | [Studio](docs/STUDIO.md) · [Guion de ejemplo](docs/ejemplos/guion-minimo.json) |
| Ver todo | [Índice de documentación](docs/README.md) |

## Qué no es

- La búsqueda de biblioteca entrega clips de hasta 20 segundos; el motor compone videos de hasta 60 segundos.
- No define todavía reglas de licencias ni de uso de los clips: conserva la procedencia factual de cada uno (fuente, URL y autor cuando existe) y deja esa decisión a quien lo usa. La licencia MIT cubre el código, no los clips de terceros.
- No promete disponibilidad de las fuentes externas.

## Klipi

<img src="https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/assets/klipi-mascot.svg" width="150" align="right" alt="Klipi, mascota exploradora de Clipoteca" />

**Klipi** es el explorador de fotogramas de la biblioteca: un fotograma con perforaciones de película, un botón de reproducción y una lupa. La paleta, la mascota y todos los SVG del repositorio son originales y están descritos en [docs/IDENTIDAD.md](docs/IDENTIDAD.md).

## Licencia

Código bajo [licencia MIT](LICENSE). Las ideas y mejoras son bienvenidas en [CONTRIBUTING.md](CONTRIBUTING.md).

---

<p align="center">
  <strong>Una escena encontrada. Una biblioteca que crece.</strong><br/><br/>
  <img src="https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/assets/clipoteca-logo.svg" width="240" alt="Logotipo de Clipoteca" />
</p>
