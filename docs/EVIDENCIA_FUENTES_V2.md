# Evidencia de fuentes candidatas v2 - smoke M24 r6

**Run:** https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44977121143
**Ref:** `refs/heads/main`; **SHA:** `fd17e46aee3fb10a957425e5841cbe43ef572ed9`;
**Artefacto:** `clipoteca-smoke-44977121143` (id 14735119727);
**Reporte:** `data/reports/smoke-44977121143.json`.
**Fecha UTC:** 2026-11-22T02:41:01.046489+00:00.

**Cobertura:** 23 proveedores x 4 idiomas (92 sondas principales) y 8
proveedores candidatos (48 sondas).
Resultados de busqueda principales: 64 (anterior) y 118 (actual).
Antes: AUTH_REQUIRED:43, SUPPORTED_SEARCH_DOWNLOAD:33, SUPPORTED_SEARCH_ONLY:4, TIMEOUT:3, UNAVAILABLE:9.
Despues: AUTH_REQUIRED:32, RATE_LIMITED:2, SESSION_REQUIRED:7, SUPPORTED_SEARCH_DOWNLOAD:46, UNAVAILABLE:5.
El workflow verde no implica disponibilidad de todos los proveedores ni descarga de clips.

**Integridad de evidencia:** artefacto y log del job smoke descargados;
ref/SHA/run_id contrastados y 92 pares unicos proveedor/idioma revisados;
48 sondas candidatas y campo error_detail verificados. El JSON completo
se conserva con serializacion ASCII (ensure_ascii=True), sin eliminar filas.
El reporte original no contenia URLs, cookies, tokens, credenciales ni
URLs firmadas: no fue necesario redactar datos.

## Busqueda principal por proveedor e idioma, segundo smoke

| Proveedor | es | en | zh | ru |
| --- | --- | --- | --- | --- |
| bilibili | UNAVAILABLE (Bilibili silent empty search) | UNAVAILABLE (Bilibili silent empty search) | UNAVAILABLE (Bilibili silent empty search) | UNAVAILABLE (Bilibili silent empty search) |
| coverr | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED |
| douyin | SESSION_REQUIRED (session_required) | SESSION_REQUIRED (session_required) | SESSION_REQUIRED (session_required) | SESSION_REQUIRED (session_required) |
| dpla | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED |
| dzen | 3 resultados | 3 resultados | 3 resultados | 3 resultados |
| europeana | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED |
| instagram | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED |
| internet_archive | 3 resultados | 3 resultados | 3 resultados | 3 resultados |
| library_of_congress | 3 resultados | 3 resultados | RATE_LIMITED (provider rate limit) | RATE_LIMITED (provider rate limit) |
| nara | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED |
| nasa | 0 resultados | 3 resultados | 0 resultados | 0 resultados |
| odysee | 3 resultados | 3 resultados | 3 resultados | 2 resultados |
| ok_ru | 3 resultados | 3 resultados | 3 resultados | 3 resultados |
| peertube | 0 resultados | 2 resultados | 0 resultados | 0 resultados |
| pexels | 3 resultados | 3 resultados | 3 resultados | 3 resultados |
| pixabay | 3 resultados | 3 resultados | 3 resultados | 3 resultados |
| rutube | 3 resultados | 3 resultados | 3 resultados | 3 resultados |
| smithsonian | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED |
| snapchat | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED |
| tiktok | UNAVAILABLE (provider peer changed after DNS validation) | SESSION_REQUIRED (session_required) | SESSION_REQUIRED (session_required) | SESSION_REQUIRED (session_required) |
| vk_video | 3 resultados | 3 resultados | 3 resultados | 3 resultados |
| wikimedia_commons | 3 resultados | 3 resultados | 3 resultados | 3 resultados |
| youtube | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED | AUTH_REQUIRED |

## Comparacion por proveedor e idiomas


| Proveedor | Antes: run 44972077685 | Despues: run 44977121143 | Variacion |
| --- | --- | --- | --- |
| bilibili | 0 resultados; SUPPORTED_SEARCH_ONLY:4 | 0 resultados; UNAVAILABLE:4 | +0 |
| coverr | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| douyin | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; SESSION_REQUIRED:4 | +0 |
| dpla | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| dzen | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| europeana | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| instagram | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| internet_archive | 0 resultados; UNAVAILABLE:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +12 |
| library_of_congress | 0 resultados; SUPPORTED_SEARCH_DOWNLOAD:1, TIMEOUT:3 | 6 resultados; RATE_LIMITED:2, SUPPORTED_SEARCH_DOWNLOAD:2 | +6 |
| nara | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| nasa | 3 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 3 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| odysee | 11 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 11 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| ok_ru | 0 resultados; UNAVAILABLE:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +12 |
| peertube | 2 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 2 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| pexels | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| pixabay | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| rutube | 0 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +12 |
| smithsonian | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| snapchat | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |
| tiktok | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; SESSION_REQUIRED:3, UNAVAILABLE:1 | +0 |
| vk_video | 0 resultados; AUTH_REQUIRED:3, UNAVAILABLE:1 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +12 |
| wikimedia_commons | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | 12 resultados; SUPPORTED_SEARCH_DOWNLOAD:4 | +0 |
| youtube | 0 resultados; AUTH_REQUIRED:4 | 0 resultados; AUTH_REQUIRED:4 | +0 |

Proveniencia: ref refs/heads/main, SHA fd17e46aee3fb10a957425e5841cbe43ef572ed9, run_id 44977121143.
El numero de sondas candidatas difiere del numero de resultados principales.
Los diagnosticos de candidato pueden no coincidir con la busqueda principal.


## Sondas candidatas registradas (no son descargas)

| Proveedor | Idioma | Operacion | HTTP | Codigo | Resultados | Diagnosis | has_stream |
| --- | --- | --- | --- | --- | --- | --- | --- |
| rutube | es | search | 200 | - | 0 | - | false |
| rutube | en | search | 200 | - | 0 | - | false |
| rutube | zh | search | 200 | - | 0 | - | false |
| rutube | ru | search | 200 | - | 0 | - | false |
| ok_ru | es | search | 301 | - | 0 | - | false |
| ok_ru | en | search | 301 | - | 0 | - | false |
| ok_ru | zh | search | 301 | - | 0 | - | false |
| ok_ru | ru | search | 301 | - | 0 | - | false |
| vk_video | es | init | 200 | - | 0 | - | false |
| vk_video | en | init | 200 | - | 0 | - | false |
| vk_video | zh | init | 200 | - | 0 | - | false |
| vk_video | ru | init | 429 | - | 0 | - | false |
| dzen | es | search | 200 | - | 0 | - | false |
| dzen | en | search | 200 | - | 0 | - | false |
| dzen | zh | search | 200 | - | 0 | - | false |
| dzen | ru | search | 200 | - | 0 | - | false |
| library_of_congress | es | search | 429 | - | 0 | - | false |
| library_of_congress | en | search | 429 | - | 0 | - | false |
| library_of_congress | zh | search | 429 | - | 0 | - | false |
| library_of_congress | ru | search | 429 | - | 0 | - | false |
| douyin | es | init | 200 | 0 | 0 | - | false |
| douyin | es | search | 200 | 2483 | 0 | session_required | false |
| douyin | en | init | 200 | 0 | 0 | - | false |
| douyin | en | search | 200 | 2483 | 0 | session_required | false |
| douyin | zh | init | 200 | 0 | 0 | - | false |
| douyin | zh | search | 200 | 2483 | 0 | session_required | false |
| douyin | ru | init | 200 | 0 | 0 | - | false |
| douyin | ru | search | 200 | 2483 | 0 | session_required | false |
| tiktok | es | init | 200 | 1001 | 0 | session_required | false |
| tiktok | es | search | 200 | - | 0 | - | false |
| tiktok | en | init | 200 | 1001 | 0 | session_required | false |
| tiktok | en | search | 200 | - | 0 | - | false |
| tiktok | zh | init | 200 | 1001 | 0 | session_required | false |
| tiktok | zh | search | 200 | - | 0 | - | false |
| tiktok | ru | init | 200 | 1001 | 0 | session_required | false |
| tiktok | ru | search | 200 | - | 0 | - | false |
| bilibili | es | init | 200 | 0 | 0 | - | false |
| bilibili | es | init | 200 | -101 | 0 | - | false |
| bilibili | es | search | 200 | 0 | 0 | silent_block | false |
| bilibili | en | init | 200 | 0 | 0 | - | false |
| bilibili | en | init | 200 | -101 | 0 | - | false |
| bilibili | en | search | 200 | 0 | 0 | silent_block | false |
| bilibili | zh | init | 200 | 0 | 0 | - | false |
| bilibili | zh | init | 200 | -101 | 0 | - | false |
| bilibili | zh | search | 200 | 0 | 0 | silent_block | false |
| bilibili | ru | init | 200 | 0 | 0 | - | false |
| bilibili | ru | init | 200 | -101 | 0 | - | false |
| bilibili | ru | search | 200 | 0 | 0 | silent_block | false |

**Discrepancias observadas:** RuTube tuvo 0 resultados candidatos frente a 3
por idioma en busqueda principal. OK.ru tuvo redireccion 301 en candidata
frente a 3 por idioma en principal. VK tuvo 429 al iniciar candidato ru
pero 3 resultados por idioma en principal. Ninguna es prueba de descarga.

## Bloqueos observados y limites

- Bilibili: cuatro UNAVAILABLE (Bilibili silent empty search); candidato
  silent_block en es/en/zh/ru.
- Douyin: cuatro SESSION_REQUIRED; candidata session_required en es/en/zh/ru.
- TikTok: tres SESSION_REQUIRED y un UNAVAILABLE por
  provider peer changed after DNS validation; candidata init 1001
  diagnosticada session_required en cuatro idiomas.
- Library of Congress: tres resultados es/en, RATE_LIMITED zh/ru.
  El 429 exige contraste de cuota/ritmo antes de imputar bloqueo IP.
- VK Video: 3 resultados en cuatro idiomas; 429 aislado en init candidato ru.
  No constituye bloqueo persistente de la busqueda principal.
- NASA y PeerTube: resultados solo en ingles; limitacion de contenido.
- AUTH_REQUIRED por falta de claves no equivale a bloqueo IP.

**PROPUESTA, no activacion:** comparar Bilibili, Douyin y TikTok en runner
aislado, y LoC solo tras aislar cuota/ritmo. No modificar
data/runner-routing.json por una hipotesis de diferencia de redes.

## Lo que aun no demuestra el smoke

No verifica descarga de MP4, stream/CDN utilizable, derechos, despliegue
de Worker 2.0.0, demo E2E ni mejora por runner propio.
No promover capacidad SUPPORTED_SEARCH_DOWNLOAD con solo busqueda.

## Registro previo preservado: smoke 44972077685

**Estado:** smoke live all completado en GitHub Actions, 2026-11-22 UTC.
Un HTTP 200 no garantiza busqueda ni descarga funcional.

**Proveniencia verificable:** ref=refs/heads/main,
SHA=bf0454baa76c7aa8e2528b51f38302b30581cf0d,
run_id=44972077685. Artefacto: clipoteca-smoke-44972077685.
[Run 44972077685](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44972077685).

**Cobertura:** 23 proveedores x 4 idiomas (92 filas) y 8 candidatos
(44 llamadas) en modo all. En proveedores hubo 64 resultados de busqueda,
43 filas AUTH_REQUIRED, 9 UNAVAILABLE y 3 TIMEOUT. La sonda
de candidatos encontro 20 elementos en Library of Congress. Es evidencia
de busqueda, no de un clip descargado ni de un deploy Worker.


**Fecha UTC de generacion:** 2026-11-22.

| Proveedor | Idioma | Operacion | HTTP | Codigo plataforma | search_nil_info | Error tipo | ms | Resultados | Stream/archivo |
|---|---|---|---:|---:|---|---|---:|---:|---|
| rutube | es | search | 200 |  |  |  | 395.7 | 0 | no |
| rutube | en | search | 200 |  |  |  | 392.4 | 0 | no |
| rutube | zh | search | 200 |  |  |  | 393.8 | 0 | no |
| rutube | ru | search | 200 |  |  |  | 399.7 | 0 | no |
| ok_ru | es | search | 301 |  |  |  | 568.1 | 0 | no |
| ok_ru | en | search | 301 |  |  |  | 200.9 | 0 | no |
| ok_ru | zh | search | 301 |  |  |  | 236.2 | 0 | no |
| ok_ru | ru | search | 301 |  |  |  | 337.9 | 0 | no |
| vk_video | es | init | 200 |  |  |  | 498.4 | 0 | no |
| vk_video | en | init | 429 |  |  | HTTP_ERROR | 124.1 | 0 | no |
| vk_video | zh | init |  |  |  | ReadTimeout | 7009.0 | 0 | no |
| vk_video | ru | init | 200 |  |  |  | 418.0 | 0 | no |
| dzen | es | search | 200 |  |  |  | 749.5 | 0 | no |
| dzen | en | search | 200 |  |  |  | 566.5 | 0 | no |
| dzen | zh | search | 200 |  |  |  | 406.3 | 0 | no |
| dzen | ru | search | 200 |  |  |  | 580.9 | 0 | no |
| library_of_congress | es | search | 200 |  |  |  | 3413.1 | 20 | si |
| library_of_congress | en | search |  |  |  | ReadTimeout | 7009.3 | 0 | no |
| library_of_congress | zh | search | 200 |  |  |  | 243.2 | 0 | no |
| library_of_congress | ru | search | 200 |  |  |  | 245.2 | 0 | no |
| douyin | es | init | 200 | 0 |  |  | 379.3 | 0 | no |
| douyin | es | search | 200 | 2483 | no |  | 360.6 | 0 | no |
| douyin | en | init | 200 | 0 |  |  | 275.3 | 0 | no |
| douyin | en | search | 200 | 2483 | no |  | 595.0 | 0 | no |
| douyin | zh | init | 200 | 0 |  |  | 310.6 | 0 | no |
| douyin | zh | search | 200 | 2483 | no |  | 313.1 | 0 | no |
| douyin | ru | init | 200 | 0 |  |  | 315.1 | 0 | no |
| douyin | ru | search | 200 | 2483 | no |  | 292.2 | 0 | no |
| tiktok | es | init | 200 | 1001 |  |  | 249.2 | 0 | no |
| tiktok | es | search | 200 |  |  |  | 83.9 | 0 | no |
| tiktok | en | init | 200 | 1001 |  |  | 305.9 | 0 | no |
| tiktok | en | search | 200 |  |  |  | 89.4 | 0 | no |
| tiktok | zh | init | 200 | 1001 |  |  | 326.1 | 0 | no |
| tiktok | zh | search | 200 |  |  |  | 82.3 | 0 | no |
| tiktok | ru | init | 200 | 1001 |  |  | 263.4 | 0 | no |
| tiktok | ru | search | 200 |  |  |  | 60.8 | 0 | no |
| bilibili | es | init | 200 | -101 |  |  | 386.2 | 0 | no |
| bilibili | es | search | 200 | 0 |  |  | 273.6 | 0 | no |
| bilibili | en | init | 200 | -101 |  |  | 244.9 | 0 | no |
| bilibili | en | search | 200 | 0 |  |  | 269.9 | 0 | no |
| bilibili | zh | init | 200 | -101 |  |  | 237.9 | 0 | no |
| bilibili | zh | search | 200 | 0 |  |  | 277.9 | 0 | no |
| bilibili | ru | init | 200 | -101 |  |  | 247.5 | 0 | no |
| bilibili | ru | search | 200 | 0 |  |  | 292.7 | 0 | no |

## Ejecucion y limites

- No se guardaron tokens, cookies, payloads completos ni URLs firmadas.
- Los estados 2483, 403 y 429 evidencian restricciones; no implican soporte.
- Un resultado positivo debe verificarse con procesamiento real en el hito de la fuente.
- **Runner propio frente a Actions:** pendiente de medicion desde ambos entornos (M20).
- El modo all del run 44972077685 incluyo la misma sonda candidates.
- Los resultados de esta tabla son metricas observadas, no disponibilidad de descargas.
- Los 429 (VK) y codigos de plataforma (2483 Douyin, 1001 TikTok,
  -101 Bilibili) no prueban por si solos bloqueo de IP 403/412/444.
- No se modifica data/runner-routing.json; una ruta propia exige
  comparacion controlada desde Actions y el runner aislado.
- **Pendiente:** clip real, comparacion de runner, despliegue y demo E2E.


## M24 r4 - diagnostico del smoke 44972077685 y correcciones

**Procedencia:** [workflow live en main](https://github.com/riquelmechile/clipoteca-comunitaria/actions/runs/44972077685),
SHA bf0454baa76c7aa8e2528b51f38302b30581cf0d. El artefacto
data/reports/smoke-44972077685.json es inmutable: el codigo de esta
rama todavia no ha sido probado en un nuevo smoke.

- Internet Archive: las cuatro consultas UNAVAILABLE se atribuyeron al
  eco cambiado en advancedsearch.php. responseHeader.params usa qin para
  la consulta original y query para la forma normalizada, no q. El
  adaptador acepta q o qin igual al enviado; sin eco registra un aviso
  y conserva solo documentos con identifier y mediatype=movies.
  Un eco explicitamente distinto sigue rechazado.
- RuTube: cuatro consultas HTTP 200 sin resultados. El endpoint de
  busqueda necesita client=wdp; la reproduccion play/options no se
  modifica porque no hay evidencia nueva de que requiera ese parametro.
- OK.ru: URL anterior responde 301. La ruta nueva es
  /video/search/<termino-codificado>; desde el entorno de prueba se
  obtuvo HTTP 200 y una pagina con 20 registros dentro de
  video-search-result data-props. Fixture recortado y sin URLs/CDN;
  esto no certifica una descarga real.
- Library of Congress: tiempos agotados de 6 s en Actions y HTTP 429
  en la reproduccion externa. El smoke usa 45 s, separa idiomas del
  mismo proveedor al menos 500 ms y la API respeta Retry-After
  de 429 con un maximo acotado.
- Bilibili: code=0 con result vacio y sin numResults ya no cuenta
  como busqueda exitosa. Prueba SPI invitado buvid3/buvid4 en memoria
  y solo los envia a api.bilibili.com. Un vacio sin numResults tras
  el reintento es silent_block; el estado queda search-only.
- Douyin: status_code 2483 indica session_required; TikTok: registro
  invitado 1001 junto con lista vacia indica session_required.
  Requieren DOUYIN_COOKIE / TIKTOK_COOKIE o una prueba comparativa en
  runner propio aislado. No se evaden verificaciones ni se activan
  rutas nuevas por defecto.
- VK Video: el login anonimo devuelve 429 o timeout segun la red.
  Se comparte el token valido entre instancias y un backoff de
  fallo entre idiomas; se reporta RATE_LIMITED para 429, respetando
  el Retry-After acotado. No equivale a falta de credenciales.
- Smoke r4: probe por idioma en serie dentro de cada proveedor,
  error_type mas error_detail solo para excepciones propias con
  mensaje ASCII limitado; estado HTTP solo si se observa en un
  mensaje propio. No guardar URLs, headers, tokens ni cuerpos.

NASA devolvio resultados en ingles; que no devuelva material para
es, zh o ru es una limitacion del contenido y no un fallo tecnico.
PeerTube depende de instancias permitidas y mostro material en ingles:
la ausencia de resultados en otros idiomas tampoco es un error.

**Aun pendiente:** repetir smoke en main despues del merge, verificar
descarga de clips reales, comparar bloqueo IP en runner aislado y
realizar demo E2E. Ninguna fuente asciende a SUPPORTED_SEARCH_DOWNLOAD
con evidencia de solo busqueda. No se modifica .github/.
