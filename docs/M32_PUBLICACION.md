# M32: indexado de clips y entrega

El contrato nuevo sustituye el antiguo esquema de publicación de video.
Todos los clips importados que pasaron los filtros normales de ingesta
se indexan, incluso cuando no se usan en el video o este falla QA.

    python -m clipoteca.studio.index --work work
    python -m clipoteca.studio.index --work work --dry-run build/indexado

El índice lee todos los manifiestos scenes/ID/import/imported.json.
Valida rutas, tamaños y SHA-256 del MP4, miniaturas y storyboards
regulares y no vacíos (hasta 5 MiB por preview). Deduplica por SHA
y Catalog.find_duplicate contra el catálogo y entre escenas; reserva
IDs, sube assets verificados al Release de lotes y ejecuta una sola
actualización del catálogo. Si falla, revierte únicamente assets nuevos
no referenciados por un catálogo observado. Un reintento reutiliza
registros existentes. index.json contiene clips (mapa provisional a
definitivo), reused_clips e indexed. La simulación escribe solo en disco.

M28 no conserva hashes originales de previews: se calculan al indexar
y se verifica el asset remoto. No hay reglas nuevas de licencias.
La escritura requiere un único job serializado con contents: write.

    python -m clipoteca.studio.delivery --work work --catalog data/clips.json

entrega.json es un manifiesto local de los bytes del MP4, QA, fuentes,
clips usados, indexados y tiempos, no una ficha persistente. Se adjunta
a los reportes de Actions. El MP4 aprobado se entrega mediante artefacto
temporal de siete días. El antiguo comando studio.publish se retira y
falla sin efectos remotos; no se crean Releases ni fichas de video.

Ver [workflow](M32_RENDER.md) y [esquema](../schemas/video-delivery.schema.json).
