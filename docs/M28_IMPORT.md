# M28 PR3: importacion temporal de material para escenas

El motor M28 PR3 reutiliza la ingesta v2 cuando una escena carece de material
de catalogo (needs_import) o ha agotado sus candidatos (no_candidate).
Ningun clip se publica aqui; no se crean Releases, commits ni jobs remotos.

## Entrada y comando

import_scene(job, out_dir, catalog_path=..., adapters=None, providers=None,
max_results=3, max_clips_per_source=2, concurrency=8, downloader=None,
translator=None, embedding_encoder=None, timeout_s=None) recibe job.json
de studio.plan. Requiere id seguro, aspect 9:16, 16:9 o 1:1, len_est,
len_fetch, prompt, queries.es y queries.en, min_short_side opcional.

Las consultas se realizan en orden ingles primero, espanol segundo, sin
repetir por casefold. Cada consulta usa el mismo clipoteca.ingest.ingest,
con horizon studio-v1-N, max_results restante, y filtro de duracion
min(20, max(8, ceil(len_fetch)+4)). No se pasa filtro de aspecto: clip-scene
reencuadra cualquier orientacion al formato del video, y filtrar aqui
descartaba casi todo el material horizontal para un video vertical.

El CLI reutiliza los SourceSpec de SOURCES, los stubs de credenciales
faltantes de cli.py y runner.selected_providers(actions) para impedir
que se seleccionen fuentes reservadas a un runner autohospedado.

    python -m clipoteca.studio.importer scene --job escenas/intro/job.json \
      --catalog data/clips.json --out escenas/intro/import \
      --providers pexels,pixabay --max-results 3

Una linea JSON del CLI incluye id, status e imported. Codigo 0: >=1 clip;
3: sin resultados; 2: entrada invalida; 1: error sin importaciones.

Sin resultados y error no son lo mismo. Los candidatos que el motor miro
y descarto (duplicate, search_only, aspect_filter, media_filter,
language_filter, scene_quality_rejected, identity_collision) cuentan
como rechazos: la busqueda funciono y no habia material util, asi que
el estado es no_results (codigo 3) y el conteo queda en rejections.
Solo son error (codigo 1) las fallas reales: excepcion, timeout, fuentes
caidas, limite de peticiones, descarga o ffmpeg fallidos.

Para correrlo de verdad hacen falta, ademas del extra studio, el extra
translate y los recursos de traduccion verificados (accion
.github/actions/prepare-translations, variables ARGOS_MODEL_PINS_JSON y
ARGOS_MODEL_CACHE_DIR), igual que el workflow de importacion. Esa accion
ejecuta uv sync solo con el extra translate: despues hay que volver a
sincronizar con los dos extras.

## Auditoria de efectos remotos de ingest.py

Se reviso ingest.py entero, incluyendo try/finally y publicaciones opcionales.
Los siguientes efectos quedan interceptados o deshabilitados:

| Efecto potencial | Control de importer.py |
|---|---|
| GitHubCatalogWriter read_catalog y update (PUT a clips.json) | LocalWriter lee Catalog de catalog_path y actualiza solo memoria |
| GitHubReleaseStorage Release/upload/verify/delete | LocalStorage implementa las mismas firmas y copia a archivos locales con SHA y tamano verificados |
| GitHubJobWriter, condicionado por CLIPOTECA_PERSIST_JOBS | variable fijada a 0 durante la llamada |
| GitHubEmbeddingWriter, GitHubQueries, GitHubShards (incluso en finally) | CLIPOTECA_PERSIST_JOBS=0 impide crear cliente y pendientes remotos |
| M21 sidecars opcionales | CLIPOTECA_M21_SIDECARS=0 |
| JobStore, write_metrics, QueryStore y EmbeddingStore | job_root bajo out_dir/.ingest-local, todo local, eliminado al terminar |
| SearchEngine, adapters, direct_media y download | solo lecturas publicas y descargas; tests usan adaptadores y downloader falsos |
| MetadataTranslation y expansion de idiomas | se reutiliza translator inyectable, nunca se inventan traducciones |

Las variables de entorno se restauran con finally exactamente como
estaban, incluidas las ausentes; no hay tokens de Github.
Se entrega repository local/local y token vacio. El writer y el
storage siempre se inyectan, por lo que no se modifico ingest.py.

LocalStorage implementa get_or_create_batch_release, reserved_ids,
upload_asset, verify_asset, find_existing_asset, delete_asset y close.
Cada upload copia bytes atomicos, comprueba tamano y SHA256 reales
y retorna un ReleaseAsset local para el contrato de ingest.

## Salidas y contratos de M32

Todas las salidas finales usan temporal y os.replace:

- clips/imp-<12 caracteres SHA>.mp4: original normalizado por ingest.
- thumbs/imp-<12 caracteres SHA>.webp y storyboards/imp-...webp.
- imported.json: version=1, id, status (ok, no_results, error),
  queries_tried, clips, errors (solo fallas reales), rejections
  (conteo de descartes por motivo) y time_s.
- Cada clip incluye imp_id, sha256, size_bytes, duration, width, height,
  local_path (relativa con separador /), source_url, provider, record
  completo de esquema v2, assets con video, thumbnail y storyboard.
- assignment.json: id, status imported o needs_import, primary y backups.
  Cada candidato incluye clip_id=imp-id, score, matched, duration, width,
  height, sha256, size_bytes, source_url, fit, local_path; SIN asset_url.

El registro conserva su ID clip-NNNNNN de la v2 como provisional
y una referencia de almacenamiento local://, nunca publica esa URL.
Para determinismo created_at se marca provisionalmente con el valor
1970-01-01T00:00:00Z; M32 pondra fecha y ID reales al publicar.
El ID visible del candidato es imp- seguido de 12 hex del SHA256.
Se puntua con library.text_score y se filtra por lado corto minimo,
duracion >=0,42*len_est y windows.fit_mode; los empates son
deterministas. El manifest es autosuficiente junto a los archivos.
Iguales entradas dan los mismos JSON salvo time_s.

Timeout default 900 segundos (variable CLIPOTECA_IMPORT_TIMEOUT),
global para todas las consultas; al expirar, se preservan los clips
ya importados y se anota error timeout. No se escriben errores
upstream, URLs privadas, tokens, ni rutas absolutas.
Reutilizar out_dir elimina primero clips/ thumbs/ storyboards/
y carpetas de trabajo previas para no dejar archivos huerfanos.

## Consumo por clip_scene y limites

clip_scene(job, assignment, out_dir, local_root=import_dir) o la CLI
con --local-root import_dir aceptan assignment.status imported.
Un candidato solo puede ofrecer asset_url o local_path, nunca ambos.
La ruta local debe ser relativa, sin segmentos .. ni enlaces simbolicos,
y permanecer estrictamente bajo local_root. Se verifica tamano y
SHA256 antes de copiar al temporal y nuevamente tras copiar.
Las fallas son bad_local_path y hash_mismatch; se prueba el backup.
La ruta de catalogo conserva su comportamiento de descarga original.

Los clips locales ejecutan LAS MISMAS verificaciones de clip-scene:
FFprobe, ranking CLIP, OCR, ventana, encuadre, fondo negro,
congelacion, pHash y timeout. result.json agrega imported=true
y local_path para un clip local seleccionado; omite asset_url
y conserva source_url.

Flujo de M32: plan -> clip_scene del catalogo -> si needs_import
o no_candidate -> importer -> clip_scene con assignment importado
-> fit / mix / render / QA -> publicar SOLO clips efectivamente
usados, con IDs finales y un unico commit al catalogo.
Si falla QA no se publica material nuevo.

Las pruebas en test_import_m28.py prueban ingesta con FFmpeg
sintetico, fuente falsificada, no persistencia, duplicados,
restauracion de entorno, timeout, rerun y camino local clip-scene.
test_clipscene_m28.py cubre rutas absolutas, traversal, symlink,
selector ambiguo y hash incorrecto con fallback.
Los tests saltan si falta ffmpeg o cv2; no descargan modelos.
No se cambian dependencias, workflows ni datos persistentes.
