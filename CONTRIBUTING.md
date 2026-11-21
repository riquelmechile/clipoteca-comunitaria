# Colaborar con Clipoteca

Clipoteca acepta aportes de escenas mediante [GitHub Issues](.github/ISSUE_TEMPLATE/aportar-escena.yml).
Cualquier colaborador puede crear un Issue con una URL HTTPS **directa** a un
archivo MP4, WebM u OGV. El repositorio procesa automáticamente el aporte con
controles de SSRF, límite de tamaño, FFmpeg, deduplicación, catálogo y Releases;
no exige permisos de escritura ni moderación técnica manual.

La API `POST /v1/contributions`, si está desplegada, también puede crear el
Issue. Un Issue inválido recibe comentario de fallo y permanece abierto para
corrección. El registro conserva autor, URL y número de Issue como procedencia.

## Otros aportes

- Reportar errores del [roadmap](docs/ROADMAP.md).
- Proponer mejoras de búsqueda obligatoria en español, inglés, chino y ruso.
- Proponer fuentes, pruebas y casos reproducibles.
- Enviar pull requests de código o documentación.

No incluyas claves, tokens ni información privada en Issues.
Las futuras normas comunitarias quedan fuera de la versión técnica v1.

## Desarrollo

Requiere Python 3.13, `uv`, Node.js 22 y FFmpeg.

```bash
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
cd worker
npm ci
npm run typecheck
npm test
```

Las pruebas Python no usan sockets externos: `respx` y fixtures sustituyen servicios remotos.

**HLS seguro y FFmpeg:** requiere el demuxer HLS con `-allowed_segment_extensions`
(backports en **4.4.6+**, **6.1.3+**, **7.0.3+** o **7.1.2+**; **8.0+**; las
compilaciones pueden variar). FFmpeg 6.1 de Ubuntu 24.04 no ofrece esta
opción. Comprueba con `ffmpeg -hide_banner -h demuxer=hls` que figure
`allowed_segment_extensions`: la aplicación también comprueba la capacidad
una vez por proceso y **rechaza HLS sin la opción**, nunca deshabilita el
control para admitir instalaciones antiguas.
Referencia: https://ffmpeg.org/pipermail/ffmpeg-cvslog/2025-August/149555.html
