# Revisión comparativa de búsqueda: Chiloé

## Evidencia histórica

El render [45503776238](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45503776238)
(24 de noviembre de 2026, SHA 6ca50bbf) entregó «Chile, de norte a sur»,
ocho escenas, 49,8 s y QA aprobada. Artefactos examinados: plan
14973186440, scene-chiloe 14972803934 y entrega 14974160733.
No existe data/videos en main ni en v3.0.0; los diagnósticos históricos
se llamaban timing*.json en vez de stage-*.json.

- assignment.json: clip-000068, una bandurria, coincidencia textual 1,0
  con «Chiloe island Chile».
- result.json: fallback=true, degraded_reason=low_sim, catalog_strong=false,
  sim=0,1733 (umbral 0,27), prob=0,001.
- import/imported.json: timeout 600,005 s, 888 brutos, 789 text_gate,
  0 relevantes, 0 evaluados visualmente, Commons consultado; 7 descargas
  iniciadas, 3 completadas. Solo una de cuatro consultas primarias y
  cuatro relacionadas fueron intentadas.
- import-retry/imported.json: timeout 180,014 s, 449 brutos,
  406 text_gate, 0 relevantes, 0 evaluados visualmente, una descarga
  completada. No hubo reemplazo para el respaldo.
- Ocho términos relacionados de Commons resueltos sin errores. No se
  acredita fallo de traducción; sí agotamiento del presupuesto con baja
  relevancia textual y ninguna evaluación visual.

## Propuesta mínima

El #154 añadió posteriormente un corte tras el primer término con respaldo
de catálogo y ninguna coincidencia visual. La regresión nueva demuestra
que las cuatro consultas históricas no se intentan: se abandona tras la
primera. El cambio mantiene el corte para consultas no geográficas y
permite otras semillas geográficas dentro de los límites existentes.
No relaja los umbrales ni aumenta el presupuesto total. No se atribuye
retroactivamente este corte al render histórico.

## Comparación obligatoria antes de fusionar

Desde el repositorio, con el guion de tres escenas de la rama candidata:

```bash
SCRIPT=$(cat docs/ejemplos/guion-revision-chiloe.json)
gh workflow run render.yml --ref main -f request_id=chiloe-base-20261126 -f index_clips=false -f script_json="$SCRIPT"
gh workflow run render.yml --ref panter/pulido-v3-2-c -f request_id=chiloe-candidato-20261126 -f index_clips=false -f script_json="$SCRIPT"
```

Comparar queries_tried, termination_reason, text_gate, relevant_count,
evaluated_count, commons_consulted, duración, stage-importer.json y clip
visual elegido. Exigir palafitos o costa de Chiloé, no una bandurria,
QA aprobada y ausencia de degradación de las otras escenas. Si no mejora,
no fusionar: investigar text_gate y presupuesto de análisis y descarga
con los nuevos artefactos.
