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

Las consultas unicas se ordenan por numero de palabras especificas (sin
calificativos comunes), conservando ingles antes de espanol en empates.
Cada consulta reutiliza clipoteca.ingest.ingest en lotes progresivos,
con horizon studio-v1-N-batch-M, max_results restante y filtro de duracion
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
storage siempre se inyectan. La ingesta normal conserva su descubrimiento
y filtros; solo el importador proporciona lotes y un presupuesto de escena.

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

Flujo de v3: plan → selección de catálogo fuerte; si no hay coincidencia
fuerte, importer → comparar importados con catálogo → fit / mix / render / QA.
El job index verifica e incorpora TODOS los clips importados válidos, no
solo los usados, incluso si falla QA. El MP4 final nunca se publica.

Las pruebas en test_import_m28.py prueban ingesta con FFmpeg
sintetico, fuente falsificada, no persistencia, duplicados,
restauracion de entorno, timeout, rerun y camino local clip-scene.
test_clipscene_m28.py cubre rutas absolutas, traversal, symlink,
selector ambiguo y hash incorrecto con fallback.
Los tests saltan si falta ffmpeg o cv2; no descargan modelos.
No se cambian dependencias, workflows ni datos persistentes.

## Política de búsqueda para entrega v3

El workflow conserva --spread-queries como flag compatible, sin reservar
cupos para consultas futuras. El limite es ocho candidatos validos o 600 s.
Se corta antes con --enough 3 (default; 0 desactiva esta salida), si tres
clips ya verificados cumplen len_fetch, lado corto minimo y coincidencia
lexica de una misma consulta: dos terminos distintos, o al menos la mitad
si tiene uno o dos terminos, sin tildes, mayusculas ni palabras vacias.
Cuando las consultas contienen un nombre propio reconocido (por ejemplo
Rapa Nui), se exige coincidencia de nombre completo en titulo real o
localizado. Las importaciones temporales omiten tags sintetizados a partir
de la consulta, para que no simulen evidencia de identidad.
El tope de cantidad y de suficiencia cuenta clips unicos por SHA-256
que cumplen el filtro de asignacion. Los clips no pertinentes conservan
su lugar en el manifiesto e indexado, pero no adelantan el corte.
Entre importados que superan todas las compuertas visuales, clip-scene
prefiere coincidencia de nombre en titulo, descripcion o tags derivados
de la fuente. No altera el puntaje visual, ni la prioridad de catalogo
fuerte. El vocabulario de nombres conocidos y las secuencias de nombres
con mayusculas son una heuristica, no un reconocedor NER universal.

En cada consulta se priorizan en, es, zh y ru; dentro de cada idioma,
proveedores con descarga, prioridad declarada por adaptador (Pexels 100) y orden
estable. No se excluye ninguna fuente elegible; se ejecutan lotes de hasta
concurrency proveedores en paralelo y se consumen en orden de prioridad.
Al alcanzar el objetivo se cancelan y esperan las busquedas pendientes;
no se programa otro lote. La segunda ronda --simple conserva la misma
logica con dos candidatos o 180 s. Los manifiestos de ambas rondas se
conservan para indexado.

imported.json agrega termination_reason (enough, max_results, timeout,
exhausted), searches_started, searches_avoided, searches_cancelled,
downloads_started y downloads_avoided. Las busquedas evitadas son el
maximo teorico de pares consulta/idioma/proveedor elegibles no iniciados;
las descargas evitadas cuentan IDs de origen descubiertos pero no
descargados, no candidatos de consultas que nunca se ejecutaron.
time_s mide el tiempo total; la meta, aun sin medir, es 1-2 min por escena
con material disponible, en lugar de aproximadamente 10 min.
En Actions (POSIX, hilo principal) el plazo tambien interrumpe trabajo
sincrono; en otros contextos se conserva el plazo cooperativo.

La prueba de reejecución compara metadatos semánticos y valida los hashes y
tamaños reales de cada MP4 por separado. No exige que FFmpeg produzca bytes
idénticos entre dos codificaciones equivalentes; los IDs derivados del hash
y las rutas asociadas se normalizan solo para esa comparación.


Los plazos POSIX no reemplazan temporizadores existentes ni se instalan
desde hilos secundarios. El temporizador y el manejador se restauran incluso
si el cuerpo falla. FFmpeg de deteccion de escenas, HLS y DASH se espera como
subproceso asincrono; la cancelacion mata y recoge el hijo, y borra las salidas
parciales de mux.

### Wikimedia Commons: búsqueda geográfica

Cuando se detecta un nombre propio, Commons intenta resolver categorías mediante
MediaWiki (namespace 14), con coincidencia exacta normalizada y prioridad para
`Videos of X`. Se aceptan los alias documentados Rapa Nui, Easter Island e Isla de
Pascua, Torres del Paine / Torres del Paine National Park y Chiloé / Chiloé Island;
no se crean alias a partir de la consulta libre. Se recorren hasta seis
categorías, dos niveles de profundidad y 50 miembros por petición (sin paginación
adicional), con un máximo de tres segundos o la mitad del presupuesto del adaptador.
Las peticiones son seriales por adaptador, usan el User-Agent del proyecto y maxlag=5.
Los videos encontrados por categoría preceden a los de texto y se deduplican por ID.
Sin categoría, o si falla su API, continúa la búsqueda textual. El registro conserva
el título original, `commons_category` y `commons_place_aliases` opcionales;
la categoría aporta evidencia de pertinencia, no una garantía del contenido visual.
Se mantienen las verificaciones existentes de licencia, MIME, HTTPS y descarga
segura; WebM/OGV se normalizan a MP4 por el pipeline habitual. En escenas con nombre
propio Commons se programa antes que los bancos stock, sin excluir otras fuentes.
No se incorporan los bancos oficiales Imagen de Chile ni Sernatur: sus términos
prohíben el acceso automatizado, la minería de datos y la redistribución.
