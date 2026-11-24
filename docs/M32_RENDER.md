# M32: render, indexado y entrega temporal

El workflow está en [.github/workflows/render.yml](../.github/workflows/render.yml).
El Worker 3.0.0 lo despacha; las corridas reales que lo validan se registran en [ESTADO](ESTADO.md).

| Input | Contrato |
|---|---|
| script_json | Guion JSON, esquema [video.schema.json](../schemas/video.schema.json), 3–12 escenas, ≤60 segundos |
| request_id | Obligatorio: 8–64 caracteres ASCII alfanuméricos, guion o guion bajo |
| index_clips | Booleano, true por defecto |

El Worker genera un id privado de 128 bits y despacha con request_id = SHA-256
del id, para que los nombres públicos de Actions no revelen la credencial.
El nombre de corrida es render seguido del request_id.

## Jobs

- plan valida guion y asigna candidatos del catálogo.
- voice, music y scene (matriz) corren en paralelo. Por escena, si no existe
  una coincidencia fuerte (sim ≥0,27, provisional), se importan candidatos
  y se elige el importado válido más pertinente; el catálogo débil solo es respaldo
  cuando no hay ningún importado válido. La primera
  ronda busca hasta ocho candidatos validos, con corte anticipado por tres
  clips suficientes y un plazo de seguridad de 600 s. Prioriza consultas
  especificas y Pexels, sin excluir las demas fuentes. Si ningún importado supera los filtros
  de escena, una ronda adicional simplifica título y prompt, hasta dos
  candidatos en 180 s. El total máximo es diez. Una coincidencia débil válida
  del catálogo se marca fallback=true cuando resulta elegida. Sin candidato
  válido, entrega.json registra el motivo de la escena y no se entrega MP4.
- index descarga artefactos de scene, detecta manifiestos de importación
  no vacíos e indexa todos los clips válidos en un solo commit, aunque
  falle assemble. Es el único job con contents: write; no usa una cola que
  cancele indexados pendientes. Los conflictos de assets o SHA provocan
  una nueva lectura, deduplicación y asignación, con seis intentos y backoff.
- assemble no espera a index; reintenta duplicados
  hasta dos veces, ajusta, mezcla, compone y ejecuta QA. Entrega el MP4
  únicamente cuando pasan las 14 compuertas.

## Artefactos

| Nombre | Contenido | Retención |
|---|---|---|
| video-${request_id} | Solo video.mp4, compresión 0 | 7 días |
| entrega-${request_id} | entrega.json, qa_report.json, render.json, timings.json disponibles | 7 días |
| indexed-clips | index.json, mapa de IDs y nuevos clips | 7 días |

El [esquema de entrega](../schemas/video-delivery.schema.json) exige SHA-256,
tamaño y duración del MP4, script_id, título, aspecto, QA, clips usados y
su procedencia, clips indexados conocidos y tiempos. El índice definitivo se
publica por separado como indexed-clips; el estado lo incorpora cuando llega. No hay video Release ni ficha
persistente en data/videos. Los artefactos de diagnóstico pueden existir
aunque no exista el video aprobado.

## Seguridad y operación

Las acciones se fijan por SHA, el guion se pasa por variable de entorno
y los IDs de escena se validan y entrecomillan. Los secretos de fuentes
solo existen en el paso importador. El GITHUB_TOKEN de Actions se usa
solo en index con contents: write. En el Worker, CLIPOTECA_DISPATCH_TOKEN
necesita actions: read y write sobre este repositorio; CLIPOTECA_WRITE_TOKEN
autoriza nuevas solicitudes. El id de descarga es una credencial, no debe
registrarse ni compartirse. Los POST de video tienen cuota propia (5 minutos
entre solicitudes y 50 por día UTC por IP, Cache API best-effort); la cuota
normal de importación no cambia. Tras aceptar el POST puede haber un 404
transitorio mientras aparece el run; reintentar. La descarga admite Range,
streaming para ZIP almacenado y fallback deflate de hasta 8 MB. El estado
advierte si la evidencia de entrega o indexado no es legible.

La acción [install-ffmpeg](../.github/actions/install-ffmpeg/action.yml) usa FFmpeg/FFprobe b6.1.1
fijados por SHA-256, con apt acotado como respaldo; el tiempo medido
por escena se guarda como install_ffmpeg, aparte de catalog, import e import_retry.
El workflow está activo y se usó en las corridas reales de los tres formatos
(ver [ESTADO.md](ESTADO.md)); la instalación fijada de FFmpeg tarda segundos.

## Primera corrida del workflow anterior

[Run 45309334286](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45309334286):
cinco escenas 9:16; cuatro resolvieron catálogo (~1 min por escena), dos
esperaron ~17 min de instalación apt. La importación real trajo tres clips
(Pexels, Wikimedia Commons, TILvids) en ~2,5 min tras una sola consulta
(tarpaulin). Los tres fueron rechazados por relevancia; assemble falló con
lona (no_candidate). No se produjo un video final. Estos datos motivan las
dos rondas, el indexado independiente y el instalador fijo. Los resultados
del nuevo flujo siguen pendientes de corrida real.

## Diagnostico de busqueda rapida

El flujo no requiere promover un workflow nuevo: los defaults del CLI se
aplican a ambas rondas. El manifest conserva los clips verificados al
cortar por suficiencia, cantidad, plazo o agotamiento. Entrega y timings
incluyen por escena y ronda los segundos y contadores del importador, con
el motivo como clave numerica (por ejemplo,
scene.intro.import.termination_reason.enough = 1), para mantener el contrato
numerico del esquema de entrega. El objetivo de 1-2 min por escena con
material disponible requiere validacion real posterior al merge; no es
una medicion de este PR.


## Qué ocurre si QA rechaza el video

Por ejemplo, la corrida 45322952524 (Chile, de norte a sur) renderizó
49,8 s, pero falló layout_playwright (desborde de subtítulo) y
loudness_ebur128 (pico -0,8 dBFS con máximo -0,9). No entrega MP4.

Al finalizar la corrida, GET /v1/videos/{id} devuelve status "failed";
stages.qa.conclusion indica "failure" si falló ese paso. Si el artefacto
entrega está disponible y se puede leer, qa.pass es false y qa.failed
enumera las compuertas fallidas. En el ejemplo sería:

    {"status":"failed","qa":{"pass":false,
      "failed":["layout_playwright","loudness_ebur128"]}}

La respuesta real conserva también id y stages; no hay file_url ni
campo separado reason. El motivo detallado y mediciones están en
qa_report.json del artefacto entrega (si se pudo generar). Si la evidencia
no puede leerse, la respuesta puede omitir qa y advertir
delivery_evidence_unavailable; stages sigue indicando el estado del paso.
El usuario puede volver a solicitar el render respetando las cuotas.
La política de no entregar videos rechazados no cambia; no se añade
deliver_on_qa_fail.
