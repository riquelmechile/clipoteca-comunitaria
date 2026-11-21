# M21 · Embeddings y selección de escenas

## Contrato y funcionamiento

La selección de escenas mantiene el **filtro de calidad de M14**: las ventanas
negras, uniformes, títulos/créditos y aquellas bajo el umbral de movimiento y
textura no se publican aunque su similitud semántica sea alta. Entre las
ventanas que pasan ese filtro, el promedio de similitud coseno CLIP con la
consulta decide el orden. Calidad M14 sirve de desempate; inicio más temprano
como último desempate. Ningún corte excede **20 segundos**.

Los modelos opcionales de la misma familia CLIP son:

- **Imagen:** `sentence-transformers/clip-ViT-B-32` (CLIP ViT-B/32).
- **Texto multilingüe:** `sentence-transformers/clip-ViT-B-32-multilingual-v1`,
  alineado al espacio de imagen de 512 dimensiones. Idiomas de consulta
  `es`, `en`, `zh`, `ru`.
- **Dispositivo:** CPU por defecto; se permite CUDA con un runner que tenga
  GPU y dependencias adecuadas. La interfaz `Encoder` permite pruebas
  deterministas sin descargar ni ejecutar redes neuronales.

Instalar aparte del entorno normal:

```bash
uv sync --frozen --extra dev --extra embeddings
```

El CI existente usa `uv sync --frozen --extra dev`: no instala Torch ni los
pesos. Si falta el extra o los pesos, el motor usa **la selección M14
original**. Se registra `embeddings_unavailable:m14_quality_fallback` o
`embeddings_extraction_failed:m14_quality_fallback` en los avisos durables
del trabajo y el campo `embedding_status` del resultado de ingest. Nunca
se asigna un vector fabricado al clip.

## Fotogramas y fórmula

La extracción es sobre **archivo local ya descargado** por
`security/download.py` / HLS con verificación pública de DNS, pinning de peer,
límites de bytes y allowlist CDN por salto. El módulo de embeddings rechaza
URLs remotas y enlaces simbólicos; FFmpeg sólo permite los protocolos locales
`file,pipe`, procesa como máximo 900 s, con timeout de 150 s y un máximo
explícito de **480 fotogramas RGB de 128×128**. CLIP reescala internamente
cada imagen a 224×224 para su encoder. En fuentes con más de 900 s se
muestrean **sólo los primeros 900 s**, nunca una supuesta cobertura completa
del video. Nunca se envían URLs firmadas
a FFmpeg ni se desactivan las protecciones de descarga.

- Video de hasta **180 s**: muestreo de **1 fps**.
- Video de más de **180 s**: muestreo de **0,5 fps**.
- Cada fotograma CLIP genera un vector `v_i ∈ ℝ^512` y la consulta genera
  `q ∈ ℝ^512`; ambos se normalizan L2.
- Coseno por fotograma: `c_i = (v_i / ||v_i||) · (q / ||q||)`, acotado a
  `[-1, 1]`.
- Media móvil centrada de **3 s**, calculada **dentro de los límites de cada
  ventana**, para evitar contaminación por otra escena:
  `m_i = mean(c_j : |t_j - t_i| ≤ 1,5 s, start ≤ t_j < end)`.
- Puntaje semántico de la ventana admisible `[start, end)`, con
  `0 < end-start ≤ 20 s`: `S = mean(m_i : start ≤ t_i < end)`.
- Orden descendente por `S`; en empate, mayor calidad del filtro M14,
  luego inicio menor. Las ventanas se seleccionan sin solapamiento.
  Si no hay fotogramas semánticos válidos para una candidata, se le asigna
  prioridad inferior a una con datos; no se relaja el filtro M14.

El clipping/normalización, H.264, miniaturas y storyboard siguen usando las
rutas y límites existentes de M13/M14. M21 **no cambia proveedores**, Workflows
ni Worker.

## Persistencia de vectores

Tras el éxito de publicación del MP4 y catálogo, se calcula el promedio de
vectores de imagen de los fotogramas de cada clip y se normaliza L2 en float32.
Se convierte una vez a **512 float16 little-endian** y se almacena en
`data/embeddings/clip-000001.f16` (exactamente 1024 bytes por clip), con
`data/embeddings/index.json`:

```json
{
  "version": 1,
  "dimension": 512,
  "dtype": "float16-le",
  "vectors": {
    "clip-000001": {
      "path": "clip-000001.f16",
      "sha256": "64 caracteres hexadecimales del archivo"
    }
  }
}
```

El índice usa **ID canónico de clip** como clave. Los valores se validan
(dimensión 512, finitos, norma L2 aproximada a 1, checksum SHA-256).
`data/clips.json` queda intacto y **no incluye vectores**; el Worker de M22
podrá recorrer por fuerza bruta los IDs del índice y leer el float16 de 1024
bytes por archivo, convirtiendo a float32 y normalizando para el coseno.

En el runner de ingest, `EmbeddingStore` genera archivos locales con escrituras
atómicas. Si ya está habilitado el modo persistente de trabajos
`CLIPOTECA_PERSIST_JOBS=1`, `GitHubEmbeddingWriter` sube cada vector
inmutable al repositorio y actualiza el índice con SHA/CAS y reintentos,
como el almacenamiento existente de trabajos. Una colisión de ID no sobrescribe
vectores distintos; el aviso de persistencia fallida no revierte un clip ya
publicado. Ningún archivo incluye cookies ni URLs de origen o firmado.

## Medición real y decisión CPU/GPU

```bash
# Video local obtenido mediante descarga segura, no una URL remota
uv run --extra embeddings python scripts/benchmark_embeddings.py \
  --video /ruta/al/clip.mp4 --query lluvia --device both
```

El script mide con reloj real el trabajo de **extracción + embedding de
fotogramas + comparación con la consulta** y devuelve por dispositivo
`elapsed_seconds / (video_seconds / 60)`. La primera carga de los modelos
se ejecuta por separado y no se atribuye como latencia de cada video. El
script no inventa un tiempo si falta CUDA, el extra o el video.

| Plataforma | Video / hardware | Segundos por minuto de video | Estado |
|---|---|---|---|
| CPU GitHub Actions | Pendiente de prueba con extra y modelos reales | Pendiente de medición | No medido |
| GPU runner propio | Pendiente de CUDA y muestra reproducible | Pendiente de medición | No medido |

**Decisión de ubicación:** pendiente de esas dos mediciones reales. Hasta
obtenerlas, `uv sync --frozen --extra dev` conserva el fallback CPU de M14;
no se ejecutan modelos ni GPU en el CI actual. El umbral de 180 s y el
límite de 480 muestras restringen costos previsibles; no constituyen evidencia
de velocidad en hardware concreto.

## Evidencia y atribuciones

`tests/test_embeddings_m21.py` y `tests/fixtures/m21/` evalúan los cuatro
idiomas con encoder falso, ventanas semánticas que ganan a M14 por relevancia,
media móvil, límite de 20 s, persistencia float16 y fallback sin modelos.
El test con modelos reales sólo se habilita explícitamente mediante
`CLIPOTECA_TEST_REAL_EMBEDDINGS=1`; omite la prueba cuando Torch o
sentence-transformers no están instalados.

El proyecto **sift-video (GPL-3.0)** se consultó únicamente como idea de
selección semántica; **no se incorporó ni copió su código**.
