# Colaborar con Clipoteca

El proyecto tiene una base ejecutable local; todavía no existe un servicio público de subida ni una API/MCP desplegada.

## Propuestas bienvenidas

- Reportar errores o ambigüedades en el [roadmap](docs/ROADMAP.md) y los contratos.
- Proponer mejoras al motor multilingüe en **español, inglés, chino y ruso**.
- Proponer fuentes de búsqueda y casos de prueba reproducibles.
- Proponer mejoras de accesibilidad, búsqueda, metadatos y experiencia de agente.
- Proponer clips o ejemplos a través de GitHub Issues; la ingesta automática es un objetivo futuro, no una función disponible todavía.

Usa un Issue para la discusión y un pull request para cambios en documentación o imágenes. No incluyas credenciales privadas, tokens ni materiales sensibles.

## Alcance actual

No se ejecutan descargas automáticas, FFmpeg ni procesamiento audiovisual en este repositorio por ahora. Las normas de intercambio, créditos y aportes de la comunidad se decidirán más adelante.

## Contribuciones al código futuro

El código se distribuirá bajo [licencia MIT](LICENSE). La licencia es permisiva y permite crear servicios con funciones comerciales o créditos en el futuro; no convierte en privadas las descargas de archivos que sean públicos en GitHub.

## Desarrollo

Requiere Python 3.13, `uv`, Node.js 22 y FFmpeg.

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest

cd worker
npm ci
npm run typecheck
npm test
```

Los tests Python bloquean sockets; las respuestas HTTP externas se prueban con fixtures y `respx`.
