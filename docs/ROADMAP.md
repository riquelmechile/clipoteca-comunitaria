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

La evolución del motor (corte por escena, fuentes nuevas, Douyin/TikTok con búsqueda web, búsqueda semántica y tool guion → clips) está planificada en [ROADMAP_V2.md](ROADMAP_V2.md), hitos M11–M24.
