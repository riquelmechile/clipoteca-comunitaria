# M13 · Segunda vuelta: detección de escenas segura

## Decisiones de seguridad

- **HTTP Range remoto diferido.** La detección directa mediante FFmpeg contra URLs
  externas no se habilita hasta implementar y probar pinning del peer DNS durante
  toda la conexión y cada redirección. Se eliminó la sonda `Range: bytes=0-0`,
  que exponía una petición adicional sin aportar información al camino seguro.
- Las URLs remotas HTTPS pasan por `validate_public_url` y por
  `security.download.download_media`, que verifica cada salto y el peer de la
  conexión, y limita tamaño, tiempo, MIME y firma del archivo. El analizador
  FFmpeg recibe **solo la ruta local descargada**. No se confunde un `runner`
  inyectado en pruebas con un permiso de acceso remoto.
- Los encabezados personalizados `headers` **no se envían ni se ignoran**
  silenciosamente: por ahora causan `ValueError`. El descargador seguro no
  acepta encabezados arbitrarios, y habilitarlos sin restricciones podría
  filtrar secretos por redirecciones.
- FFmpeg y el detector opcional se ejecutan en `asyncio.to_thread` desde la
  API asíncrona. La API síncrona remota rechaza explícitamente la invocación
  dentro de un event loop: allí debe usarse `await detect_scenes_async(...)`.

## Algoritmo

- El detector opcional PySceneDetect usa `seek(start)` y un límite de
  `end_time`; si funciona, FFmpeg no vuelve a analizar el archivo.
  Si no está instalado o falla su backend compatible, FFmpeg es el fallback.
- Los cortes menores a 1,5 s se fusionan. Las tomas mayores a 20 s se
  subdividen en tramos de 10 s.
- Si el número de cortes supera el máximo de 120, la cobertura se reconstruye
  con tramos uniformes sin exceder **20 s**. Si `max_scenes` es demasiado bajo
  para cubrir la ventana sin exceder 20 s, se emite `ValueError` en vez de
  entregar un segmento final incorrecto.
- Ventana máxima de análisis: 900 segundos; ni la descarga ni el análisis
  publican o alteran el video fuente.

## Atribución

Diseño informado por `broll-mcp` (licencia MIT). La implementación de
Clipoteca es independiente; no se incorpora código GPL ni una dependencia
obligatoria de PySceneDetect.

## Verificación

`uv sync --frozen --extra dev`, `uv run ruff check`,
`uv run ruff format --check` y `uv run pytest`, además de
los jobs de CI en el PR. La integración real con recortes/storyboard
y el pinning de FFmpeg remoto permanecen fuera de M13.
