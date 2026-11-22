# Evidencia de fuentes candidatas v2  M12

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
