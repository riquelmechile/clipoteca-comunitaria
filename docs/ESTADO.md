# Estado de Clipoteca

**Versión del repositorio 2.0.6 · medido el 22 de noviembre de 2026.**
El Worker desplegado permanece en **2.0.5** hasta un despliegue autorizado; no se
redespliega en este cambio.
Esta página es la referencia de estado del proyecto. Todo lo que afirma tiene una ejecución enlazada; lo que no se midió, se dice.

## Resumen

| | |
|---|---|
| Servicio | https://clipoteca-worker.clipoteca.workers.dev (`/v1/*` y `/mcp`), responde versión 2.0.5 |
| Catálogo | 25 clips publicados tras el run 45085865562 |
| Fuentes con búsqueda y descarga comprobadas | 13 de 23 |
| Fuentes que esperan una API key | 8 |
| Fuentes que exigen sesión iniciada | 2 (TikTok y Douyin), apagadas |
| Búsqueda semántica | implementada, apagada de fábrica |

## Dónde corre

| Pieza | Dónde |
|---|---|
| Motor de búsqueda, descarga y corte | GitHub Actions (`ubuntu-latest`), workflows de este repositorio |
| API REST y servidor MCP | Cloudflare Worker, en una cuenta de Cloudflare dedicada al proyecto |
| Catálogo y registro de trabajos | `data/clips.json` y `data/jobs/` en este repositorio |
| Archivos de video | GitHub Releases |

Ninguna máquina personal ni servidor propio forma parte del servicio.

## Verificación de la versión 2.0.6

El motor se ensayó en una rama temporal con permisos de solo lectura,
persistencia de jobs deshabilitada, sin catálogo ni Release. El workflow se
retiró antes del PR. Los registros históricos 2.0.5 se mantienen debajo.

| Prueba | Resultado | Evidencia |
|---|---|---|
| Descubrimiento real «bosque» (4 idiomas) | 24,02 s; 11 búsquedas OK, 1 timeout propio, 10 AUTH_REQUIRED y 1 estado distinto por idioma; **0 discovery_deadline** en los cuatro idiomas; 10 fuentes aportaron candidatos | [run 45084878519](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45084878519) |
| Planificación de 23 proveedores x 4 idiomas (falsos) | 92 intentos; las 22 fuentes sanas llegan a ruso; el proveedor lento agota su timeout individual | tests/test_engine_v206.py |
| Selección y publicación entre fuentes (falsas, controles de calidad reales) | Con 3 fuentes válidas publica 3 clips de varias fuentes; si solo una es válida puede publicar 3 | tests/test_ingest_m4c.py |
| CI Python, Worker, Argos real | 678 Python aprobadas, 4 omitidas; 133 Worker aprobadas; 3 de 3 jobs verdes | [run 45085652824](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45085652824) |
| Smoke en main, modo all | 92 sondas/23 fuentes/4 idiomas; 37,18 s, búsqueda operativa de 13 fuentes | [run 45085855870](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45085855870) |
| Smoke en main, modo downloads | 13 de 23 fuentes descargaron video; 53,12 s; 8 AUTH_REQUIRED y 2 SESSION_REQUIRED | [run 45085860846](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45085860846) |
| Ingesta real «bosque» en main | 23 consultas por idioma, **0 discovery_deadline** (es/en/zh/ru), 10 fuentes con candidatos, 3 clips publicados de Pexels/NASA/Wikimedia Commons; discovery 18,09 s, proceso 6 min 23 s, job Actions 7 min 12 s | [run 45085865562](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45085865562) · data/jobs/eac415941e00e4a4415112ebfe84c67e734faaa261b5a1294cc9decb5f670f86.json |

## Verificación de la versión 2.0.5

Todas las ejecuciones corrieron sobre `main` en el commit `9d85365`.

| Prueba | Resultado | Evidencia |
|---|---|---|
| CI (Python, Worker, traductor real) | 672 pruebas Python y 133 del Worker aprobadas | [run 45081834187](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45081834187) |
| Búsqueda en vivo, 23 fuentes × 4 idiomas | 92 sondas en 39 s; 130 resultados de 13 fuentes | [run 45082009984](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45082009984) · `data/reports/smoke-45082009984.json` |
| Descarga real, un video por fuente | 13 descargas correctas en 30 s | [run 45082001946](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45082001946) · `data/reports/smoke-45082001946.json` |
| Importación de punta a punta | `POST /v1/import` con «cascada» → traducida a los 4 idiomas → 3 clips publicados (`clip-000020` a `clip-000022`) | [run 45082441011](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/45082441011) |
| Guion de 5 escenas por REST (`POST /v1/script`) | 5 de 5 escenas con clip | lluvia, nieve, volcán, aurora boreal, cascada |
| Guion de 5 escenas por MCP (`resolve_script`) | 5 de 5 escenas con clip; el servidor lista sus 8 tools y declara 2.0.5 | mismo guion, vía `/mcp` |
| Salud del servicio | `status: ok`, catálogo y MCP correctos | `GET /v1/health` |

La prueba de descarga baja como máximo 32 MiB por fuente y no publica nada. La importación de punta a punta sí publica.

## Fuentes, una por una

Columnas `es` a `ru`: resultados que devolvió la búsqueda de prueba en ese idioma (máximo 3 por sonda). «Descarga real»: si se pudo bajar un video de esa fuente desde GitHub Actions.

| Fuente | es | en | zh | ru | Descarga real |
|---|---|---|---|---|---|
| Bilibili | 3 | 3 | 3 | 3 | sí (17.6 MiB) |
| Dzen | 3 | 3 | 3 | 3 | sí (8.3 MiB) |
| Internet Archive | 3 | 3 | 3 | 3 | sí (32.0 MiB) |
| Library of Congress | 3 | 3 | limitado | limitado | sí (32.0 MiB) |
| NASA | 0 | 3 | 0 | 0 | sí (6.2 MiB) |
| Odysee | 3 | 3 | 3 | 2 | sí (32.0 MiB) |
| OK.ru | 3 | 3 | 3 | 3 | sí (7.1 MiB) |
| PeerTube | 0 | 2 | 0 | 0 | sí (32.0 MiB) |
| Pexels | 3 | 3 | 3 | 3 | sí (32.0 MiB) |
| Pixabay | 3 | 3 | 3 | 3 | sí (21.2 MiB) |
| RuTube | 3 | 3 | 3 | 3 | sí (1.1 MiB) |
| VK Video | 3 | 3 | 3 | 3 | sí (2.5 MiB) |
| Wikimedia Commons | 3 | 3 | 3 | 3 | sí (19.8 MiB) |
| Coverr | sin clave | sin clave | sin clave | sin clave | sin clave |
| DPLA | sin clave | sin clave | sin clave | sin clave | sin clave |
| Europeana | sin clave | sin clave | sin clave | sin clave | sin clave |
| Instagram | sin clave | sin clave | sin clave | sin clave | sin clave |
| NARA | sin clave | sin clave | sin clave | sin clave | sin clave |
| Smithsonian | sin clave | sin clave | sin clave | sin clave | sin clave |
| Snapchat | sin clave | sin clave | sin clave | sin clave | sin clave |
| YouTube | sin clave | sin clave | sin clave | sin clave | sin clave |
| Douyin | sin sesión | sin sesión | sin sesión | sin sesión | sin sesión |
| TikTok | sin sesión | sin sesión | sin sesión | sin sesión | sin sesión |

Lectura de la tabla:

- **Library of Congress** limita la frecuencia: las consultas en chino y ruso fueron rechazadas por exceso de solicitudes.
- **NASA y PeerTube** solo devolvieron resultados en inglés para la consulta de prueba. No es un fallo: su contenido está catalogado en inglés.
- **Bilibili, RuTube y Dzen** descargan, pero quedan declaradas como «mejor esfuerzo»: dependen de espejos y firmas que el proveedor cambia sin aviso.
- **Sin clave**: la fuente está implementada y responderá cuando se cargue su API key como secreto de Actions. YouTube, DPLA, Smithsonian, Instagram y Snapchat serían solo de búsqueda aun con clave.
- **Sin sesión**: TikTok y Douyin solo permiten buscar por palabra clave con una cuenta iniciada. Ver más abajo.

## Límites conocidos

1. **Descubrimiento: corregida la cancelación por cola.** En v2.0.5 la importación «cascada» (run 45082441011) dejó 23 consultas con discovery_deadline, principalmente ruso, por el tope fijo 15 s. En v2.0.6 cada proveedor procesa sus idiomas en serie, hasta 16 proveedores concurren, y cada petición tiene su timeout propio. El límite global se calcula para cubrir todas las oleadas con máximo 180 s. La prueba real no persistente de «bosque» duró 24,02 s y marcó **0 discovery_deadline en es/en/zh/ru**; aún puede haber timeout propio, límites de proveedor o fuentes sin credencial.
2. **RuTube** respondió bien en la prueba de búsqueda y con error en esa misma importación. Su disponibilidad desde GitHub Actions es irregular.
3. **Reparto de publicaciones: corregido el sesgo del primer proveedor.** En v2.0.5 «cascada» publicó sus 3 clips desde Pexels. V2.0.6 intercala resultados de distintas fuentes antes del límite de candidatos y reserva la primera ronda de publicación a una ventana por fuente; solo si no bastan los clips válidos ejecuta una segunda ronda para completar cupo. La prueba de tres fuentes y la prueba de una sola fuente válida publicaron 3 clips sin cambiar filtros, deduplicación ni calidad. El reparto real se validó en «bosque»: clip-000023 de Pexels, clip-000024 de NASA y clip-000025 de Wikimedia Commons, todos publicados y verificados en el catálogo.
4. **Traducción.** Si el traductor entrega un resultado inválido para un idioma, la ingesta sigue con los demás y deja un aviso. Una consulta poco común puede terminar buscándose en un solo idioma.
5. **Caché.** El catálogo se sirve desde `raw.githubusercontent.com`, que guarda copia unos 5 minutos: un clip recién publicado puede tardar ese tiempo en aparecer.
6. **Costos.** No hay cifras de facturación medidas de GitHub Actions ni de Cloudflare; el proyecto usa los planes gratuitos.

## TikTok y Douyin

El código para ambas existe. Su búsqueda por palabra clave exige la cookie de una cuenta iniciada.

- **TikTok** viene apagado. Se enciende creando la variable de repositorio `TIKTOK_SESSION_ENABLED` con valor `1`, con el secreto `TIKTOK_COOKIE` cargado. Una prueba del 22-nov-2026 obtuvo 12 videos y una descarga correcta desde GitHub Actions; minutos después, tras varias consultas más desde otros servidores, TikTok empezó a responder sin resultados y la cuenta pidió un captcha. Sirve como fuente adicional de bajo volumen, no como base.
- **Douyin** no tiene sesión configurada.

Procedimiento y cautelas en [OPERACION_V2.md](OPERACION_V2.md).

## Cómo repetir estas pruebas

```bash
# Búsqueda en vivo y descarga real (no publican nada)
gh workflow run smoke.yml --ref main -f mode=all
gh workflow run smoke.yml --ref main -f mode=downloads

# Salud del servicio
curl --fail-with-body -sS https://clipoteca-worker.clipoteca.workers.dev/v1/health

# Guion de escenas sin credencial
curl -sS -X POST https://clipoteca-worker.clipoteca.workers.dev/v1/script \
  -H 'Content-Type: application/json' \
  -d '{"scenes":[{"id":"s1","narration":"Empieza a llover.","query":"lluvia","orientation":"vertical","min_duration":3}]}'
```

Al publicar una versión nueva, repetir las pruebas y actualizar esta página con las ejecuciones nuevas.
