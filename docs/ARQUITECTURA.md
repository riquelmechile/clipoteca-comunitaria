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
- **Concurrencia:** búsqueda concurrente limitada, publicación tradicional serializada por `concurrency.group` de Actions; Studio indexa independientemente con reservas exclusivas de assets y reintentos de deduplicación/asignación ante conflictos. Escrituras con SHA actual.
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
- Publicación tradicional de estados e índices serializada; el indexado de Studio utiliza reintentos optimistas y no retrasa la entrega del MP4.

## Estrategia del MVP de aporte humano

- Issue con plantilla `Aportar escena`, campos URL/título/etiquetas opcionales. Action con permisos mínimos comprueba el enlace, descarga y procesa.
- El adjunto directo de video en GitHub Free está limitado a 10 MB según documentación de septiembre 2026; archivos mayores requieren URL de descarga accesible o, en una fase posterior, una entrada temporal de objetos.
- No exponer el token que crea Releases a PRs no confiables; la entrada del Issue se considera texto/datos y no comandos ejecutables.

## Riesgos de plataforma a evaluar antes de escalar

Releases es un mecanismo de distribución de software y GitHub puede limitar patrones de uso excesivos. No es almacenamiento multimedia infinito garantizado. Objetivo de costo $0 es condicional: acciones gratuitas en repos públicos estándar, Workers dentro de cuota y proveedores sin costes externos. Si hay restricciones, el adaptador `storage` se cambia sin afectar a MCP/API.

## Motor de video (studio)

El motor v3 se ejecuta por etapas y usa archivos como contrato.
El Worker ofrece despacho y consulta de Actions en el código 3.0.0;
el workflow de entrega está propuesto y falta promoverlo y desplegar.
El job studio de CI comprueba componentes con permiso de lectura.

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
| `selection` | Coincidencia fuerte o comparación entre catálogo e importados |
| `index` | Indexado de todo material importado válido, independiente de QA |
| `delivery` | Manifiesto local de entrega ligado a QA y a los bytes del MP4 |

Los modelos de Supertonic 3, wav2vec2 fonético y CLIP ViT-B/32 ONNX int8
se fijan por tamaño y SHA-256 en [models.json](../pipeline/clipoteca/studio/models.json),
con assets del Release `models-v1`; Whisper small tiene revisión fija
en Hugging Face e inferencia int8. El extra `studio` fija las versiones
de ejecución en [pyproject.toml](../pyproject.toml). HyperFrames 0.9.36
y GSAP 3.15.1 se obtienen de npm; GSAP se verifica por hash y no se incluye
en el repositorio. Tone.js 15.1.22 está incluido como bundle local.

Importar una escena no escribe en Git ni en Releases. El job index único
verifica y deduplica todos los clips válidos importados en un commit de catálogo,
independiente de assemble y QA. El resultado de index se incluye en entrega.json.
El MP4 se conserva únicamente como artefacto de Actions durante siete días.

El Worker despacha por API con un id aleatorio de 128 bits. Para que el nombre
público de la corrida no revele esa credencial, el request_id de Actions es
su SHA-256. El Worker consulta jobs y artefactos por ese hash y transmite los
bytes del MP4 sin reenviar el token a la URL firmada. Ver [M32_RENDER.md](M32_RENDER.md).
