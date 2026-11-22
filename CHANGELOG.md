# Historial de cambios

## 2.0.0 (preparacion de cierre, noviembre 2026; NO release ni deploy)

**Alcance:** M11-M23 fusionados en main y preparacion M24 en curso. El numero
2.0.0 se sincroniza en la rama de preparacion, pero este cambio no crea tag,
release de GitHub ni despliega un Worker. La matriz live, los smokes reales y
la demostracion E2E quedan pendientes hasta su ejecucion en main.

### M11 - Roadmap y definicion del contrato (PR #31)
- Roadmap v2 y camino critico para motor de corte, fuentes y herramientas MCP.

### M12 - Fuentes declarativas y sonda de proveedores (PR #39)
- Capacidades por fuente, snapshot, candidatos y sonda de cuatro idiomas;
  los estados de fixtures no se interpretan como conectividad live.

### M13 - Deteccion de escenas (PR #38)
- Deteccion de cortes y degradacion segura por FFmpeg, limites de escena,
  rangos y casos sin cortes, con pruebas offline.

### M14 - Corte, thumbnail y storyboard (PR #49)
- Seleccion de mejores fragmentos, miniatura por escena y fotogramas de
  storyboard; recuperacion y deduplicacion de fuente.

### M15 - Internet Archive v2 (PR #43)
- Descubrimiento ordenado, ranking y seleccion de derivados de video.

### M16 - HLS + RuTube (PR #40)
- Descargas HLS con proteccion de red, seguridad de playlists y segmentos,
  RuTube con clasificadores de bloqueo y fallos.

### M17 - OK.ru, VK Video y Dzen (PR #52; integraciones anteriores revisadas)
- Fuentes y clasificacion best_effort, tokens y reproductores sujetos a smoke.
  Los resultados live de cada fuente siguen sin afirmarse.

### M18 - Instituciones, archivos y dedup (PR #42)
- Library of Congress, Europeana, Coverr, NARA, DPLA, Smithsonian y
  deteccion entre proveedores con identidad y datos verificables.

### M19 - Douyin/TikTok (PR #53, #55)
- Busqueda web invitado y firma nativa con paginacion y fallback; cookies
  opcionales. El YAML de cookies pendiente esta en OPERACION_V2.

### M20 - Bilibili WBI + runner (PR #60; correcciones PR #61)
- PR #60: Firma WBI, ruta DASH/MP4, UA/Referer, CDNs validadas,
  seleccion declarativa de runner y tests offline.
- PR #61: Preferencia HTML5 MP4 antes de DASH, backups CDN, lock de WBI,
  bloqueos de IP, recibos de fuentes diferidas y seguridad del runner
  publico. Bilibili queda search-only hasta smoke real.

### M21 - Embeddings de escenas (PR #54, #55)
- CLIP multilingue, vectores f16 por clip, ranking contra consulta,
  degradacion por CPU y dual-write de indices.

### M22 - Busqueda semantica (PR #57, #58, #59)
- Worker REST/MCP con RRF, corpus de shards con checksums, cache acotado,
  cuota por isolate, Unicode casefold y consulta de encoder fuera de Worker.
- PR #61 (M24 preparacion): limite de vectores de consulta por guion;
  tras el tope mantiene resultados lexicales.

### M23 - Guion a clips (PR #59, #60)
- `POST /v1/script` y MCP `resolve_script` con 1..50 escenas, clips video
  con fuentes/preview/storyboard, busqueda, matching y reutilizacion
  explicita, importacion existente, cuotas, resumen y estados mixtos.

### M24 - Trabajo preparado, aun incompleto
- Version 2.0.0 sincronizada en Python, Worker, OpenAPI, CLI, health y MCP.
- Operacion integrada en docs/OPERACION_V2.md; procedimientos de runner en
  docs/RUNNER_PROPIO.md; matriz generada desde fuentes declaradas y
  docs/DEMO_E2E_V2.md como receta de validacion.
- **Pendiente:** smoke live de todos los proveedores por idioma en main,
  prueba Bilibili/routing donde corresponda, prueba semantica con embeddings
  reales y demo MCP de cinco escenas. No se inventan resultados.

---


## 1.0.0 (preparacion de lanzamiento, 21-11-2026)

### Plataforma y catalogo (M0-M2)
- Contratos de catalogo, indices y assets publicos de GitHub Releases; publicacion idempotente y recuperacion.
- Pipeline Python/FFmpeg con normalizacion, miniaturas, metadatos y deduplicacion.

### Busqueda y proveedores (M3-M6)
- Expansion y busqueda en espanol, ingles, chino y ruso, con modelos Argos verificados y aislamiento de fallos.
- Adaptadores y clasificacion explicita de las 15 fuentes del roadmap: descarga donde esta permitida, solo busqueda o `AUTH_REQUIRED` cuando faltan credenciales.
- Pruebas y diagnostico de conectividad real; Bilibili puede bloquear IP de datacenter.

### API, MCP e ingesta (M7-M9)
- Worker Cloudflare con REST `/v1/*`, MCP HTTP `/mcp`, salud del catalogo y jobs.
- GitHub Actions para importacion y publicacion con reintentos, proteccion de secretos y deduplicacion.
- Aportes publicos mediante Issues, con validaciones de URL/SSRF, metadatos, comentarios de estado y retiro de etiquetas de error tras exito.

### Estabilizacion y operacion (M10)
- Smoke tests, documentacion de operacion, recuperacion y matriz de proveedores.
- Ajustes de compatibilidad Argos real, traduccion y deteccion de salidas degeneradas; User-Agent en descargas.
- Version sincronizada a `1.0.0` entre paquete Python, Worker, OpenAPI, CLI, salud y MCP.

### Evidencia y limites
- Worker desplegado en `https://clipoteca-worker.sc-plasticov.workers.dev` y 8 clips reales catalogados, incluidos Pexels, PeerTube e Issue #26 (`clip-000008`).
- Ejecuciones documentadas en [informe v1](docs/INFORME_V1.md), con pruebas de ingesta, importacion Worker, PeerTube, aporte y smoke.
- No se afirma que las 15 fuentes devuelvan clips en todos los idiomas, ni que existan mediciones de facturacion. Esta entrada **no** constituye un tag, GitHub Release ni despliegue nuevo.
