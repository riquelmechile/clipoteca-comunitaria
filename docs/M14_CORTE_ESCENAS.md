# M14 · Corte por escena, miniatura y storyboard

## Selección local, sin modelos

La ingesta descarga por la ruta actual con comprobaciones SSRF, límites de bytes
y verificación del host; **ni FFmpeg ni el selector reciben URLs remotas**.
Se examina como máximo una ventana de 900 segundos del archivo seguro local.
M13 identifica escenas (y subdivide tomas largas); M14 muestrea a un fotograma
por segundo, 96×54 gris, con proceso FFmpeg acotado por timeout y un hilo. El
puntaje combina nitidez aproximada (gradiente), movimiento entre muestras y
luminancia. Negro, sobreexposición y títulos estáticos reciben penalizaciones.
Las ventanas candidatas duran hasta 20 segundos, se ordenan de forma
determinista con desempate por inicio de origen y no pueden solaparse.

Con videos menores al máximo solicitado se conserva el intervalo original.
El límite por fuente es **2** por defecto, configurable en CLI mediante
`--max-clips-per-source 1..3`; `--max-results` sigue acotando el total.
Cada ventana se normaliza con `-ss` antes de `-i`, `-t`, recodificación
H.264/yuv420p, FPS y tamaño existentes. No se emplean modelos ni GPU.

## Archivos publicados y trazabilidad

La miniatura corresponde al centro del clip normalizado. Se publican seis
WebP de 320 px, equidistantes dentro del clip, junto al MP4 y la miniatura
en GitHub Releases. **Todos** los assets deben verificarse antes de actualizar
el catálogo. Los clips nuevos incluyen `source.source_start` y
`source.source_end` en segundos del archivo original y
`storage.storyboard_urls` (lista de URL verificadas). Los campos son
opcionales para registros antiguos; `GET /v1/clips/{id}/storyboard` devuelve
`frames: []` en ese caso.

La herramienta MCP `get_storyboard` devuelve URLs y segundos relativos al
clip para que un agente inspeccione previews **sin descargar el MP4**.
También se expone esa metadata en `get_clip` y búsquedas de catálogo.
La deduplicación M18 sigue ejecutándose para cada ventana, y las ventanas
del mismo origen con rangos explícitos y disjuntos no se tratan como
el mismo clip exclusivamente por URL/provider ID.

## Comprobaciones y pendiente de despliegue

Los tests usan videos FFmpeg lavfi sin red: portada estática y negro seguidos
de tomas dinámicas, fuente corta y toma de 45 s; además verifican WebP reales,
rango y metadatos, publicación simulada de dos clips y endpoints Worker/MCP.

**Pendiente:** criterio `clip real E2E con storyboard` sobre `main`.
El CI de PR confirma comportamiento reproducible, pero no simula publicación
real y **no** significa que la prueba de extremo a extremo en producción haya
terminado. No se hace merge, tag ni despliegue desde este trabajo.
