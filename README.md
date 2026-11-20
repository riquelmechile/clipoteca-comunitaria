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
