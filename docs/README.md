# Documentación de Clipoteca

Empieza por el [README del repositorio](../README.md). Esta página ordena el resto según lo que necesites.

## Vigente

| Documento | Para qué sirve |
|---|---|
| [ESTADO.md](ESTADO.md) | Qué funciona hoy, con qué evidencia y con qué límites. Es la referencia de estado; los demás documentos no la reemplazan |
| [STUDIO.md](STUDIO.md) | Guía del motor de video por etapas, sin servicio público de video |
| [ROADMAP_V3.md](ROADMAP_V3.md) | Hitos de video, avances y pendientes |
| [openapi.yaml](openapi.yaml) | Contrato REST `/v1` |
| [mcp-tools.md](mcp-tools.md) | Las ocho tools del servidor MCP |
| [CONTRATOS_API_MCP.md](CONTRATOS_API_MCP.md) | Esquemas y metadatos compartidos por REST y MCP |
| [ARQUITECTURA.md](ARQUITECTURA.md) | Componentes y ejecución sin servidor propio |
| [PROVEEDORES.md](PROVEEDORES.md) | Cada fuente: endpoints, credenciales y límites técnicos |
| [DESPLIEGUE.md](DESPLIEGUE.md) | Desplegar el Worker y configurar secretos |
| [OPERACION_V2.md](OPERACION_V2.md) | Flags, credenciales y procedimientos de operación |
| [RUNNER_PROPIO.md](RUNNER_PROPIO.md) | Alternativa opcional con runner propio. No se usa: el motor corre en GitHub Actions |
| [API_OPERACION.md](API_OPERACION.md) | Despacho de ingestas desde el Worker, cuotas y degradación |
| [QA_OPERACION.md](QA_OPERACION.md) | Verificación, controles y costos |
| [IDENTIDAD.md](IDENTIDAD.md) | Klipi, paleta y gráficos SVG |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Cómo aportar escenas y código |
| [../CHANGELOG.md](../CHANGELOG.md) | Cambios por versión |

## Diseño y decisiones

| Documento | Contenido |
|---|---|
| [ESPECIFICACION_COMPLETA.md](ESPECIFICACION_COMPLETA.md) | Especificación integral original de v1 |
| [PROYECTO.md](PROYECTO.md) | Requisitos, casos de uso y límites |
| [FUENTES_IDIOMAS.md](FUENTES_IDIOMAS.md) | Por qué cuatro idiomas obligatorios y qué fuentes se eligieron |
| [ROADMAP.md](ROADMAP.md) | Hitos M0–M10 (v1) |
| [ROADMAP_V2.md](ROADMAP_V2.md) | Hitos M11–M24 (v2) y su cierre |
| [adr/](adr/) | Registros de decisiones de arquitectura |

## Histórico por hito

Estos documentos describen el estado **en el momento de cada hito**. Sus cifras y advertencias de «pendiente» o «sin desplegar» ya no describen el presente; para eso está [ESTADO.md](ESTADO.md).

| Documento | Hito |
|---|---|
| [INFORME_V1.md](INFORME_V1.md) · [OPERACION_M10.md](OPERACION_M10.md) · [MATRIZ_FUENTES_M10.md](MATRIZ_FUENTES_M10.md) | Cierre de v1 (M10) |
| [M6C_SOURCES.md](M6C_SOURCES.md) · [APORTES_M9.md](APORTES_M9.md) | Fuentes y aportes en v1 |
| [M13_ESCENAS_SEGURIDAD.md](M13_ESCENAS_SEGURIDAD.md) · [M14_CORTE_ESCENAS.md](M14_CORTE_ESCENAS.md) | Detección y corte por escena |
| [M21_EMBEDDINGS.md](M21_EMBEDDINGS.md) · [M22_BUSQUEDA_SEMANTICA.md](M22_BUSQUEDA_SEMANTICA.md) | Búsqueda semántica (implementada, apagada de fábrica) |
| [M23_GUION.md](M23_GUION.md) | Guion a clips (`resolve_script`) |
| [MATRIZ_FUENTES_V2.md](MATRIZ_FUENTES_V2.md) · [EVIDENCIA_FUENTES_V2.md](EVIDENCIA_FUENTES_V2.md) · [DEMO_E2E_V2.md](DEMO_E2E_V2.md) | Mediciones y guion de demo de M24, anteriores a los desbloqueos de 2.0.3 |
| [ROADMAP_V3.md](ROADMAP_V3.md) · [esquema de guion](../schemas/video.schema.json) | M25: contrato |
| [M26_VOZ.md](M26_VOZ.md) | M26: voz y línea de tiempo |
| [M27_MUSICA.md](M27_MUSICA.md) | M27: música offline |
| [M28_CLIP_SCENE.md](M28_CLIP_SCENE.md) | M28: plan, biblioteca y selección |
| [M28_IMPORT.md](M28_IMPORT.md) | M28: importación temporal |
| [M29_AJUSTE_MEZCLA.md](M29_AJUSTE_MEZCLA.md) | M29: ajuste y mezcla |
| [M30_COMPOSICION.md](M30_COMPOSICION.md) | M30: composición y render |
| [M31_QA.md](M31_QA.md) | M31: control de calidad |
| [M32_PUBLICACION.md](M32_PUBLICACION.md) | M32: módulo de publicación, sin workflow integrado |
