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
                 ├─ buscar en proveedores, hasta 16 proveedores paralelos, 4 idiomas seriales por proveedor; tope <=180s
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

## Motor de video (studio)

El motor v3 no es un segundo servicio desplegado. Se ejecuta por etapas,
con archivos como contrato entre módulos: a mano desde un checkout o con
el workflow manual [render.yml](M32_RENDER.md), que reparte las etapas en
jobs y pasa los archivos por artefactos.
El [job studio](../.github/workflows/ci.yml) comprueba componentes en
Actions, con permiso de lectura; no publica videos.

| Módulo | Responsabilidad |
|---|---|
| `script` | Validación, defaults, ID determinista y estimación |
| `plan`, `library` | Plan por escena y asignación local del catálogo |
| `voice`, `phon`, `textnorm`, `timeline` | Síntesis local, control de pronunciación, cifras y tiempos por palabra |
| `music` | Música offline con Chromium y arreglo a la línea de tiempo |
| `clipscene`, `clipscore`, `windows`, `framing`, `checks` | Selección de tramo, encuadre, OCR, calidad y conflictos entre escenas |
| `importer` | Ingesta temporal si falta material, con escritores locales |
| `fit`, `mix` | Ajuste a duración real, mezcla y medición de sonoridad |
| `compose` | HTML, GSAP, render HyperFrames y mux FFmpeg |
| `qa` | 14 compuertas; liga el resultado al hash del MP4 |
| `publish` | Publicación verificada, ficha final y simulación local |

Los modelos de Supertonic 3, wav2vec2 fonético y CLIP ViT-B/32 ONNX int8
se fijan por tamaño y SHA-256 en [models.json](../pipeline/clipoteca/studio/models.json),
con assets del Release `models-v1`; Whisper small tiene revisión fija
en Hugging Face e inferencia int8. El extra `studio` fija las versiones
de ejecución en [pyproject.toml](../pyproject.toml). HyperFrames 0.9.36
y GSAP 3.15.1 se obtienen de npm; GSAP se verifica por hash y no se incluye
en el repositorio. Tone.js 15.1.22 está incluido como bundle local.

Importar una escena no escribe en Git ni en Releases: el catálogo y los
assets son locales hasta que se apruebe QA. La publicación vuelve a validar
el hash del video y solo incorpora material importado que se usó realmente,
en un solo commit de catálogo; escribe la ficha del video al final.
Requiere un escritor único.

**Workflow de render:** valida el guion, ejecuta voz, música y un job por
escena en paralelo, pasa archivos por artefactos, ensambla, repite las
escenas que eligieron un clip repetido y publica con concurrencia
serializada solo si se pide y QA aprueba. Existe en main pero no está
validado con corridas reales. Falta la parte REST/MCP de video que lo
despache.
La [guía Studio](STUDIO.md) describe el recorrido manual actual; el
[roadmap v3](ROADMAP_V3.md) conserva el plan.
