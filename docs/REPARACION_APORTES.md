# Reparacion local de metadatos de aportes

Desde la raiz del repositorio, en un checkout actualizado y sin otro escritor
del indice, prepara los recursos fijados por SHA-256:

```bash
uv sync --frozen --extra translate
ARGOS_MODEL_CACHE_DIR=.cache/argos ARGOS_MODEL_PINS_JSON=data/argos-pins.json \
  uv run --no-sync python -m clipoteca.translate.prepare_resources
export ARGOS_MODEL_CACHE_DIR=.cache/argos
export ARGOS_MODEL_PINS_JSON=data/argos-pins.json
uv run --no-sync python -m clipoteca.repair_upload_metadata
```

El ultimo comando es una simulacion: traduce pero no escribe. Repite con
`--write` para modificar solo el indice local `data/aportes.json` de forma
atomica. Revisa `git diff -- data/aportes.json` antes de publicar por PR.
No sube ni modifica assets, IDs, URLs ni hashes. No reemplaza traducciones
existentes y conserva la procedencia `metadata_auto`. Los tags que el
traductor rechaza (por ejemplo `unchanged_translation`) se omiten del ingles;
no se elimina el tag original en espanol.

El resumen indica registros cambiados y pendientes (sin descripcion o tags
en ingles). Un codigo de salida 1 indica pendientes; se puede volver a
ejecutar cuando los recursos esten disponibles. El aviso de traduccion
incluye solo un codigo de error o clase, nunca el texto de usuario.
