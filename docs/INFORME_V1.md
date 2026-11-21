# Informe de verificación de Clipoteca v1.0.0

**Actualización:** 21 de noviembre de 2026. **Estado:** Worker en producción y rutas críticas demostradas con ejecuciones reales. Este informe documenta evidencia de operación, no certifica todas las fuentes ni costos facturados.

## Evidencia externa verificable

| Flujo | Ejecución GitHub Actions | Resultado demostrado |
|---|---|---|
| Ingesta real | [44848918832](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44848918832) | Ingesta, procesamiento y publicación de archivos de video reales |
| Importación desde Worker | [44858494471](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44858494471) | Despacho REST/Worker hacia pipeline de Actions |
| Fuente M5: PeerTube | [44858474026](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44858474026) | Video real procesado desde fuente del bloque M5 |
| Aporte automático M9 | [44863391766](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44863391766) | [Issue #26](https://github.com/riquelmechile/clipoteca-comunitaria/issues/26) procesado y `clip-000008` publicado, sin moderación manual |
| Smoke M10 | [44858482821](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44858482821) | Consultas y estados observados en runner; no implica resultados positivos de las 15 fuentes |

El Worker público está desplegado en `https://clipoteca-worker.sc-plasticov.workers.dev`: rutas REST `/v1/*` y MCP `/mcp`. El catálogo alcanza **8 clips reales publicados**, incluidos Pexels, PeerTube y el aporte comunitario. El motor soporta términos ES/EN/ZH/RU; los metadatos se registran en ES/EN. La demostración real incluye reproducción de jobs, catálogo y archivos publicados, sin afirmar disponibilidad perpetua de terceros.

## Fuentes y limitaciones conocidas

Las 15 fuentes están representadas con su capacidad y clasificación en [matriz M10](MATRIZ_FUENTES_M10.md). Las fuentes sin credenciales obligatorias se informan como `AUTH_REQUIRED`; Bilibili puede bloquear IP de centros de datos. La columna de idiomas admitidos expresa capacidad del adaptador, **no** cuatro respuestas semánticas exitosas verificadas por cada proveedor.

## Costos y métricas

| Métrica | Evidencia o campo | Estado |
|---|---|---|
| Tiempo medido de probes | `elapsed_seconds_measured` | Consultable en artefacto del smoke real |
| Probes por fuente e idioma | `provider_languages`, `total_probes` | Ejecutados en smoke; resultados por fuente según artefacto |
| Assets existentes | `github_usage.release_bytes_first_page`, `release_assets_first_page` | Consulta de primera página; no gasto total |
| Minutos facturados Actions | `actions_billed_minutes` | No medidos |
| Costo USD de GitHub y Cloudflare | `actions_cost_usd` | No medido; no inferir de tiempos |
| API y MCP | Worker HTTPS desplegado | Verificado para rutas principales, no para toda combinación de parámetros |
| Publicación comunitaria | Issue #26, `clip-000008`, run 44863391766 | Verificada |

El workflow conserva artefactos `clipoteca-smoke-{run_id}` durante el período configurado (30 días). Los datos de facturación deben obtenerse de los paneles del proveedor, no calcularse como hechos a partir del tiempo de ejecución.

## Pendiente o condicionado

1. Mantener **CI verde** en el PR de preparación v1, incluidos Python, Worker y `real-argos` sobre el runner x86; un CI previo de `main` falló por el validador de desproporción.
2. Registrar de forma consolidada las respuestas reales **por proveedor e idioma** cuando estén disponibles, sin confundir `AUTH_REQUIRED` ni `SUPPORTED_SEARCH_ONLY` con ingestión exitosa.
3. Revisar minutos y costos **facturados** de Actions, Workers y GitHub. No afirmar costo cero universal.
4. Revalidar accesibilidad de proveedores sujetos a geobloqueo, cuotas, cambios de API o credenciales.

No se crearon tags ni GitHub Releases como parte de esta preparación; la publicación de la versión corresponde al responsable del lanzamiento.
