# PRD — Clipoteca Comunitaria

## 1. Problema y propuesta

Los generadores de video vuelven a buscar repetidamente tomas similares en múltiples plataformas. Se propone un sistema público que **acumula clips** al buscar y los expone de forma normalizada a agentes y aplicaciones.

## 2. Usuarios y casos de uso

- Agente de edición: `search_clips("lluvia", aspect_ratio="9:16", max_duration=20)` → encuentra clips existentes, puede solicitar nuevas búsquedas, obtiene el MP4.
- Integración por REST: solicita escenas según descripción, duración, resolución, orientación y fuente, y descarga por URL de GitHub.
- Colaborador: entrega un enlace o adjunto de video mediante un Issue; el mismo pipeline verifica y procesa sin moderación técnica manual.
- Mantenedor: consulta errores por proveedor, reintenta trabajos y audita cambios sin depender de una infraestructura privada.

## 3. Requisitos funcionales (obligatorios)

**RF-01** Buscar catálogo local antes de salir a Internet.  
**RF-02** Traducir o expandir **cada consulta** a `es`, `en`, `zh`, `ru` sin perder el concepto.  
**RF-03** Enviar consultas en paralelo a proveedores habilitados, con máximo cuatro conectores simultáneos por defecto y límite de **15 s para la ronda de descubrimiento**; YouTube no puede bloquear las demás fuentes.  
**RF-04** Importar automáticamente cada archivo nuevo recuperable, condicionado a controles de límites técnicos y disponibilidad, sin repetir descargas.  
**RF-05** Normalizar a MP4/H.264/yuv420p, duración <=20 s, conservar la relación de aspecto sin deformación.  
**RF-06** Guardar ancho, alto, FPS, duración, relación/orientación, tamaño, SHA256 y hash perceptual.  
**RF-07** Publicar metadatos `title`, `description`, `tags` siempre en español e inglés, preservando nombre, idioma y fuente originales.  
**RF-08** Generar miniatura limpia y versión de vista previa opcional.  
**RF-09** Publicar MP4 y miniatura en Releases; actualizar el catálogo Git solo tras verificar el asset.  
**RF-10** Exponer REST y MCP remoto con búsqueda, detalle, descarga, inicio de ingesta y estado.  
**RF-11** Admitir aportes comunitarios por Issue/URL con el mismo procesamiento automático.  
**RF-12** Controlar errores y reintentos, idempotencia, cuotas y origen sin despliegue en Oracle.

## 4. No objetivos de v1

No Astro, frontend, cuentas propias, créditos, pagos, PostgreSQL, D1, GPU, modelos generativos pesados, microservicios, panel de moderadores, sistema de autorizaciones comunitarias ni búsqueda por embeddings. El tema de autorización y políticas de publicación comunitarias se revisará más adelante; **el diseño técnico no equivale a una garantía de disponibilidad o permisos de cada plataforma**.

## 5. Reglas editoriales/técnicas del clip

- Duración objetivo: 1–20 s. Fuentes mayores se recortan al comienzo para v1; cortes inteligentes posteriores.
- Preferencia 1080p sin aumentar artificialmente resolución; no deformar; orientaciones 9:16, 16:9, 1:1, other.
- Audio opcional; guardar `has_audio`. No agregar música ni marcas al material.
- Evitar archivos corruptos, vacíos o ilegibles; rechazar si FFmpeg no puede procesarlos.
- No prometer eliminación automática confiable de marcas de agua; detectar o clasificar posible superposición cuando sea factible.
- Metadatos originales de fuente, enlace y fecha de incorporación inmutables para trazabilidad.

## 6. Criterios de éxito inicial

- Prueba completa con clips reales: cuatro traducciones en una sola consulta; búsqueda distribuida; al menos una fuente recuperable; MP4 <=20 s; ambas lenguas de metadatos; publicación y descarga API/MCP.
- 0 duplicados exactos al repetir la misma petición; 0 transcodificaciones si el archivo ya existe.
- 100% de archivos publicados con width, height, duration, fps, aspect_ratio y títulos en es/en.
- No se filtran secretos en repositorio ni logs.
- Los errores de un proveedor no bloquean resultados de otros.
