# Despliegue de Clipoteca v2

**Estado:** guia operativa de la version 2.0.1. El despliegue de 2.0.0 ya se verifico por separado; este PR no ejecuta despliegues.

## Cloudflare Workers

Requiere Node 22, una cuenta Cloudflare autorizada y credenciales GitHub de minimo privilegio. Preparar tokens **distintos** para disparar workflows (CLIPOTECA_DISPATCH_TOKEN), crear Issues (CLIPOTECA_ISSUE_TOKEN) y autenticar escritura (CLIPOTECA_WRITE_TOKEN). Nunca pegarlos en TOML, Issues ni codigo.

```bash
cd worker
npm ci
npx wrangler login
npx wrangler secret put CLIPOTECA_DISPATCH_TOKEN
npx wrangler secret put CLIPOTECA_WRITE_TOKEN
npx wrangler secret put CLIPOTECA_ISSUE_TOKEN
npx wrangler deploy
```

El codigo Worker usa por defecto raw.githubusercontent.com para
data/clips.json (catalog.ts), data/jobs/*.json (jobs.ts) y
data/sources.json (mcp.ts). CATALOG_URL y CATALOG_JOBS_URL conservan
prioridad cuando se configuran, pero NO es necesario pasar --var al
desplegar ni modificar worker/wrangler.toml. No poner credenciales en [vars].

raw.githubusercontent.com puede cachear aproximadamente cinco minutos:
un clip publicado recientemente o el cambio de estado de un job
pueden tardar hasta ~5 minutos en aparecer en API/Worker, ademas
de la cache local CATALOG_TTL_SECONDS. Esto no es un error de persistencia.

Como alternativa futura, una API de GitHub autenticada puede usar
CLIPOTECA_READ_TOKEN (secret de solo lectura, opcional) y fallback a raw.
Este PR no activa el token ni cambia la autorizacion de escritura.


Los adaptadores de ingesta requieren las siguientes credenciales. **Todas se configuran
como GitHub Actions Secrets**, no como variables publicas de Worker ni en archivos
del repositorio:

- `COVERR_API_KEY`: API oficial de Coverr.
- `EUROPEANA_API_KEY`: busqueda de registros de Europeana.
- `NARA_API_KEY`: catalogo NARA (api.data.gov).
- `DPLA_API_KEY`: API de la Digital Public Library of America.
- `SMITHSONIAN_API_KEY`: Smithsonian Open Access.

Library of Congress no requiere credencial.

`MCP_ALLOWED_HOSTS` es opcional. Si no esta definido, `/mcp` acepta el hostname **propio** de la request (por ejemplo, `clipoteca.mi-cuenta.workers.dev`) y rechaza Host falsificado. Para proxy legitimo configurar allowlist explicito. Configurar `MCP_ALLOWED_ORIGINS` con los clientes de navegador autorizados. Clientes MCP sin Origin pueden usar el mismo hostname sin variable adicional.

## Verificacion posterior al despliegue

```bash
export CLIPOTECA_API='https://clipoteca.mi-cuenta.workers.dev'
curl --fail-with-body -sS "$CLIPOTECA_API/v1/health"
curl --fail-with-body -sS "$CLIPOTECA_API/v1/clips?limit=2"
curl -i -sS "$CLIPOTECA_API/mcp"
```

Un GET de MCP puede responder con estado de protocolo distinto a 200; se requiere un handshake Streamable HTTP. Health devuelve 503 si el catalogo o el servidor MCP estan degradados.

## GitHub Actions

Crear los cinco **Actions Secrets** enumerados arriba antes de habilitar las
fuentes que requieren credenciales. Sin la clave correspondiente, cada fuente
informa `AUTH_REQUIRED`. No colocar sus valores en logs, URLs publicas, artefactos,
variables normales ni catalogos.

- `ingest.yml`: cada solicitud tiene concurrencia propia; conflictos al persistir catalogo se resuelven con CAS/409 y reintentos acotados.
- `contribution.yml`: Issue publico abierto o editado dispara ingesta automatica sin revision manual.
- `sources.yml`: actualizacion diaria de estados basados en presencia de credenciales sin exponer valores.
- `smoke.yml`: desde Actions, en main, ejecutar `workflow_dispatch` y conservar el artefacto JSON por proveedor/idioma. El JSON contiene tiempo observado y bytes de Releases (hasta la primera pagina), no costos de facturacion inventados.

Consultar [OPERACION_M10](OPERACION_M10.md) e [INFORME_V1](INFORME_V1.md). Todo despliegue o integracion a main queda fuera del alcance de estos PRs.

**HLS seguro y FFmpeg:** requiere el demuxer HLS con `-allowed_segment_extensions`
(backports en **4.4.6+**, **6.1.3+**, **7.0.3+** o **7.1.2+**; **8.0+**; las
compilaciones pueden variar). FFmpeg 6.1 de Ubuntu 24.04 no ofrece esta
opcion. Comprueba con `ffmpeg -hide_banner -h demuxer=hls` que figure
`allowed_segment_extensions`: la aplicacion tambien comprueba la capacidad
una vez por proceso y **rechaza HLS sin la opcion**, nunca deshabilita el
control para admitir instalaciones antiguas.
Referencia: https://ffmpeg.org/pipermail/ffmpeg-cvslog/2025-August/149555.html
