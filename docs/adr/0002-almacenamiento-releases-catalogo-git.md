# ADR 0002: Releases para binarios y Git para catálogo

## Estado

Aceptada para v1.

## Decisión

Los MP4 y miniaturas se publican en GitHub Releases por lotes `batch-YYYY-MM-DD-NNN`; `data/clips.json` e índices ES/EN viven en Git. Un asset se verifica antes de incorporarlo al catálogo y las escrituras se serializan.

## Consecuencias

El catálogo es auditable e idempotente, pero el almacenamiento depende de las cuotas y políticas de GitHub y puede sustituirse mediante el adaptador de almacenamiento.

