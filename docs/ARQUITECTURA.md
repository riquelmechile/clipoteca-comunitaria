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
