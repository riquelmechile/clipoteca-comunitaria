# Embeddings M21

`index.json` contiene `vectors[clip_id] = {path, sha256}`. Cada `clip-XXXXXX.f16` tiene 512 valores IEEE-754 float16 little-endian (1024 bytes), normalizados L2; no contiene URLs, cookies ni texto de consultas. M22 puede cargar el índice y comparar por fuerza bruta. Los archivos se crean al publicar clips con el extra `embeddings` y el flujo persistente de ingest; sin el extra se usa M14 y no se genera un vector ficticio.
