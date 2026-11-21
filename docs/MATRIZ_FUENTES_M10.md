# Matriz consolidada de fuentes y lenguas · M10

**Fecha del inventario:** 20 de noviembre de 2026. **Ámbito:** código, contratos y pruebas con fixtures de GitHub Actions; no se interpreta como prueba online de cuentas de terceros.

La matriz se genera a partir de `data/sources.json`, que representa las **21 fuentes conocidas con variables de credenciales ausentes**. Un estado dinámico real depende de la configuración de Actions, la red, licencias y cuotas. Las cuatro columnas de idioma indican que el adaptador admite recibir texto, no que la búsqueda semántica haya sido verificada en vivo.

| Fuente | ES | EN | ZH | RU | Capacidad anunciada | Estado sin credenciales | Credenciales |
|---|---|---|---|---|---|---|---|
| Pexels | D* | D* | D* | D* | búsqueda + archivo | AUTH_REQUIRED | `PEXELS_API_KEY` |
| Pixabay | D* | D* | D* | D* | búsqueda + archivo | AUTH_REQUIRED | `PIXABAY_API_KEY` |
| Wikimedia Commons | D* | D* | D* | D* | búsqueda + archivo | SUPPORTED_SEARCH_DOWNLOAD | ninguna |
| Internet Archive | D* | D* | D* | D* | búsqueda + archivo | SUPPORTED_SEARCH_DOWNLOAD | ninguna |
| NASA | D* | D* | D* | D* | búsqueda + archivo | SUPPORTED_SEARCH_DOWNLOAD | ninguna |
| YouTube | D* | D* | D* | D* | solo búsqueda | AUTH_REQUIRED | `YOUTUBE_API_KEY` |
| RuTube | D* | D* | D* | D* | solo búsqueda | SUPPORTED_SEARCH_ONLY | ninguna |
| PeerTube | D* | D* | D* | D* | búsqueda + archivo | SUPPORTED_SEARCH_DOWNLOAD | ninguna |
| Odysee | D* | D* | D* | D* | búsqueda + archivo | SUPPORTED_SEARCH_DOWNLOAD | ninguna |
| Bilibili | D* | D* | D* | D* | solo búsqueda | SUPPORTED_SEARCH_ONLY | ninguna |
| VK Video | D* | D* | D* | D* | solo búsqueda | AUTH_REQUIRED | `VK_ACCESS_TOKEN` |
| Douyin | D* | D* | D* | D* | búsqueda + archivo best_effort | SUPPORTED_SEARCH_DOWNLOAD | invitado; `DOUYIN_COOKIE` opcional |
| TikTok | D* | D* | D* | D* | búsqueda + archivo best_effort | SUPPORTED_SEARCH_DOWNLOAD | invitado; `TIKTOK_COOKIE` opcional |
| Instagram Reels | D* | D* | D* | D* | solo búsqueda | AUTH_REQUIRED | `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` |
| Snapchat | D* | D* | D* | D* | solo búsqueda | AUTH_REQUIRED | `SNAPCHAT_ACCESS_TOKEN`, `SNAPCHAT_PROFILE_ID` |

**D*** = idioma declarado para consulta en el adaptador, **sin verificación semántica online**. No equivale a resultados ni a descarga publicada. `AUTH_REQUIRED` corresponde a configuración ausente, no a un fallo comprobado del proveedor. `SUPPORTED_SEARCH_ONLY` no autoriza obtener MP4 por APIs públicas. `SUPPORTED_SEARCH_DOWNLOAD` exige además validar cada URL, seguridad, hash, FFmpeg y Releases.

## Evidencia técnica reproducible (sin secretos ni llamadas externas)

```bash
uv sync --frozen
uv run python -m clipoteca export-sources --output /tmp/clipoteca-sources.json
uv run pytest tests/test_sources_m6c.py tests/test_providers_m6a.py tests/test_providers_m6b.py tests/test_providers_m6c.py
uv run pytest tests/test_resilience_m10.py tests/test_recovery_m10.py
(cd worker && npm ci && npm run typecheck && npm test)
```

Los fixtures cubren la presencia de los proveedores, clasificación de errores y restricciones declaradas; **no** confirman que cada término `lluvia`, `rain`, `下雨` y `дождь` devolvió videos reales. El trabajo de descubrir los cuatro términos está cubierto por el motor y pruebas de expansión.

## Verificaciones externas todavía necesarias

1. Comprobar desde un runner confiable cada endpoint público y registrar por proveedor/idioma: código HTTP, término solicitado, tiempo de respuesta y número de resultados, sin tokens ni URLs firmadas.
2. En fuentes que requieren tokens, usar secretos propios de cuenta autorizada y registrar explícitamente qué permisos existían.
3. Descargar y procesar al menos un archivo de M5; constatar integridad, límites y publicación a Releases.
4. Confirmar filtros y términos ES/EN/ZH/RU desde el Worker desplegado y las seis herramientas MCP.
5. Confirmar que el despacho de Actions escribe un job durable, finaliza publicación real y la segunda petición no duplica clips.

Mientras esas verificaciones no consten en CI/evidencias fechadas, no se puede declarar la versión 1.0 plenamente aceptada.

Para detalles del grupo M4 y proveedores individuales, consultar [análisis documental previo](PROVEEDORES.md). Los estados estáticos se consultan mediante `export-sources`; no exponen secretos.
