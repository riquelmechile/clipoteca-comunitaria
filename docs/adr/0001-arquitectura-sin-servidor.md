# ADR 0001: arquitectura sin servidor permanente

## Estado

Aceptada para v1.

## Decisión

La API REST y MCP se ejecutan en un Cloudflare Worker TypeScript sin estado. GitHub Actions con Python 3.13 y FFmpeg procesa e ingesta clips. El Worker nunca transcodifica ni actúa como proxy del MP4; devuelve enlaces de GitHub Releases.

## Consecuencias

No hay Oracle, Astro, base de datos ni servidor Python 24/7. La ingesta es asíncrona y su estado queda versionado.

