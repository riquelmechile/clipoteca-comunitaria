# Despliegue de Clipoteca v1

**Estado:** guía operativa. El código está en PR; no se ha desplegado Cloudflare ni ejecutado un smoke real.

## Cloudflare Workers

Requiere Node 22, una cuenta Cloudflare autorizada y credenciales GitHub de mínimo privilegio. Preparar tokens **distintos** para disparar workflows (CLIPOTECA_DISPATCH_TOKEN), crear Issues (CLIPOTECA_ISSUE_TOKEN) y autenticar escritura (CLIPOTECA_WRITE_TOKEN). Nunca pegarlos en TOML, Issues ni código.

```bash
cd worker
npm ci
npx wrangler login
npx wrangler secret put CLIPOTECA_DISPATCH_TOKEN
npx wrangler secret put CLIPOTECA_WRITE_TOKEN
npx wrangler secret put CLIPOTECA_ISSUE_TOKEN
npx wrangler deploy
```

El archivo `worker/wrangler.toml` tiene `[vars]` para `CATALOG_TTL_SECONDS = "10"` y `CLIPOTECA_GITHUB_REPO`. No colocar credenciales allí.

Los adaptadores de ingesta requieren las siguientes credenciales. **Todas se configuran
como GitHub Actions Secrets**, no como variables públicas de Worker ni en archivos
del repositorio:

- `COVERR_API_KEY`: API oficial de Coverr.
- `EUROPEANA_API_KEY`: búsqueda de registros de Europeana.
- `NARA_API_KEY`: catálogo NARA (api.data.gov).
- `DPLA_API_KEY`: API de la Digital Public Library of America.
- `SMITHSONIAN_API_KEY`: Smithsonian Open Access.

Library of Congress no requiere credencial.

`MCP_ALLOWED_HOSTS` es opcional. Si no está definido, `/mcp` acepta el hostname **propio** de la request (por ejemplo, `clipoteca.mi-cuenta.workers.dev`) y rechaza Host falsificado. Para proxy legítimo configurar allowlist explícito. Configurar `MCP_ALLOWED_ORIGINS` con los clientes de navegador autorizados. Clientes MCP sin Origin pueden usar el mismo hostname sin variable adicional.

## Verificación posterior al despliegue

```bash
export CLIPOTECA_API='https://clipoteca.mi-cuenta.workers.dev'
curl --fail-with-body -sS "$CLIPOTECA_API/v1/health"
curl --fail-with-body -sS "$CLIPOTECA_API/v1/clips?limit=2"
curl -i -sS "$CLIPOTECA_API/mcp"
```

Un GET de MCP puede responder con estado de protocolo distinto a 200; se requiere un handshake Streamable HTTP. Health devuelve 503 si el catálogo o el servidor MCP están degradados.

## GitHub Actions

Crear los cinco **Actions Secrets** enumerados arriba antes de habilitar las
fuentes que requieren credenciales. Sin la clave correspondiente, cada fuente
informa `AUTH_REQUIRED`. No colocar sus valores en logs, URLs públicas, artefactos,
variables normales ni catálogos.

- `ingest.yml`: cada solicitud tiene concurrencia propia; conflictos al persistir catálogo se resuelven con CAS/409 y reintentos acotados.
- `contribution.yml`: Issue público abierto o editado dispara ingesta automática sin revisión manual.
- `sources.yml`: actualización diaria de estados basados en presencia de credenciales sin exponer valores.
- `smoke.yml`: desde Actions, en main, ejecutar `workflow_dispatch` y conservar el artefacto JSON por proveedor/idioma. El JSON contiene tiempo observado y bytes de Releases (hasta la primera página), no costos de facturación inventados.

Consultar [OPERACION_M10](OPERACION_M10.md) e [INFORME_V1](INFORME_V1.md). Todo despliegue o integración a main queda fuera del alcance de estos PRs.
