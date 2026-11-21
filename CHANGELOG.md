# Historial de cambios

## 1.0.0 (preparación de lanzamiento, 21-11-2026)

### Plataforma y catálogo (M0–M2)
- Contratos de catálogo, índices y assets públicos de GitHub Releases; publicación idempotente y recuperación.
- Pipeline Python/FFmpeg con normalización, miniaturas, metadatos y deduplicación.

### Búsqueda y proveedores (M3–M6)
- Expansión y búsqueda en español, inglés, chino y ruso, con modelos Argos verificados y aislamiento de fallos.
- Adaptadores y clasificación explícita de las 15 fuentes del roadmap: descarga donde está permitida, solo búsqueda o `AUTH_REQUIRED` cuando faltan credenciales.
- Pruebas y diagnóstico de conectividad real; Bilibili puede bloquear IP de datacenter.

### API, MCP e ingesta (M7–M9)
- Worker Cloudflare con REST `/v1/*`, MCP HTTP `/mcp`, salud del catálogo y jobs.
- GitHub Actions para importación y publicación con reintentos, protección de secretos y deduplicación.
- Aportes públicos mediante Issues, con validaciones de URL/SSRF, metadatos, comentarios de estado y retiro de etiquetas de error tras éxito.

### Estabilización y operación (M10)
- Smoke tests, documentación de operación, recuperación y matriz de proveedores.
- Ajustes de compatibilidad Argos real, traducción y detección de salidas degeneradas; User-Agent en descargas.
- Versión sincronizada a `1.0.0` entre paquete Python, Worker, OpenAPI, CLI, salud y MCP.

### Evidencia y límites
- Worker desplegado en `https://clipoteca-worker.sc-plasticov.workers.dev` y 8 clips reales catalogados, incluidos Pexels, PeerTube e Issue #26 (`clip-000008`).
- Ejecuciones documentadas en [informe v1](docs/INFORME_V1.md), con pruebas de ingesta, importación Worker, PeerTube, aporte y smoke.
- No se afirma que las 15 fuentes devuelvan clips en todos los idiomas, ni que existan mediciones de facturación. Esta entrada **no** constituye un tag, GitHub Release ni despliegue nuevo.
