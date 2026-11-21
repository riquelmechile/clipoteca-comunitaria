# Informe de verificación de Clipoteca v1

**Fecha de preparación:** 20 de noviembre de 2026. **Estado:** implementación y pruebas de desarrollo, sin despliegue ni aceptación E2E real.

## Evidencia disponible

Los gates de CI prueban Python y TypeScript sin acceso a red externa. Los mocks no certifican que un proveedor haya respondido realmente. El workflow `smoke.yml` permite ejecutar 15 fuentes por los cuatro idiomas (ES/EN/ZH/RU), si la fuente declara ese soporte, con presupuestos acotados y sin almacenar URLs de videos ni credenciales.

## Uso y costos observables por el workflow

| Métrica | Campo del artefacto | Estado |
|---|---|---|
| Tiempo medido de consultas | `elapsed_seconds_measured` | Medible al ejecutar workflow |
| Cantidad de probes y resultado | `total_probes`, `provider_languages` | Medible al ejecutar workflow |
| Bytes de assets existentes | `github_usage.release_bytes_first_page` | Consulta real; primera página de 100 Releases |
| Número de assets | `github_usage.release_assets_first_page` | Consulta real; primera página |
| Minutos facturados Actions | `github_usage.actions_billed_minutes` | No medidos; factura no disponible desde job |
| Costos USD de GitHub/Cloudflare | `github_usage.actions_cost_usd` | No medidos; no se infieren del tiempo |
| Publicación real de clip y miniatura | SHA y Release verificables | Pendiente |
| API y MCP desplegados | HTTP real + 302 | Pendiente |
| Issue comunitario catalogado | Issue, catálogo, Release | Pendiente |

El workflow guarda su informe como artefacto `clipoteca-smoke-{run_id}` durante 30 días. Los resultados de una ejecución deben adjuntarse a la revisión de v1; no declarar pruebas de red o costos sin los datos reales.

## Criterios todavía abiertos para v1

1. Ejecutar smoke real con secretos autorizados y registrar los proveedores que funcionan por idioma.
2. Descargar y procesar videos reales de al menos dos fuentes, incluida una fuente M5, hasta publicar Releases verificadas.
3. Confirmar catálogo ES/EN, hashes, miniaturas, búsqueda REST/MCP y redirect 302.
4. Confirmar que un usuario sin escritura abre un Issue y el flujo lo ingesta automáticamente.
5. Revisar costos facturados reales cuando se tenga acceso a esos datos.

El código puede estar listo para revisión sin que estos pasos sean verdaderos.
