<div align="center">

<img src="https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/assets/clipoteca-banner.svg" width="100%" alt="Clipoteca: encuentra, guarda y comparte escenas. Klipi, la mascota, explora clips con una lupa." />

<br/>

**Una biblioteca comunitaria de clips, pensada para personas y agentes de IA.**

**Multilingüe · Abierta · Reutilizable · Hasta 20 segundos**

[📋 Roadmap](docs/ROADMAP.md) · [🧭 Arquitectura](docs/ARQUITECTURA.md) · [🌐 Fuentes e idiomas](docs/FUENTES_IDIOMAS.md) · [🎨 Identidad](docs/IDENTIDAD.md) · [🤝 Colaborar](CONTRIBUTING.md)

</div>

---

> **Estado: documentación y diseño publicados.** La API, el MCP, la búsqueda externa y la ingesta de videos **todavía no están implementados ni desplegados**.

## La idea

Buscar una escena no debería significar encontrarla y descargarla de cero una y otra vez.

**Clipoteca** está diseñada para que un agente solicite, por ejemplo, **«lluvia, vertical 9:16»**. El sistema consultará su catálogo y, cuando haga falta, buscará en fuentes externas usando cuatro idiomas, procesará clips nuevos de hasta 20 segundos y conservará sus archivos en **GitHub Releases** para futuras consultas.

### Cuatro idiomas, una biblioteca

| Español | English | 中文 | Русский |
|---|---|---|---|
| **lluvia** | **rain** | **下雨** | **дождь** |
| Metadatos ES | Metadata EN | Búsqueda ZH | Búsqueda RU |

Las búsquedas externas en **español, inglés, chino y ruso son requisitos obligatorios**, no características opcionales. Todos los clips se catalogarán con títulos, descripciones y etiquetas en **español e inglés**, conservando el idioma y fuente de origen.

## Qué se construirá

| Componente | Responsabilidad prevista |
|---|---|
| **API REST + MCP** | Buscar clips por concepto, duración, proporción, orientación y fuente; obtener sus archivos |
| **Motor multilingüe** | Ejecutar búsquedas paralelas ES/EN/ZH/RU con límites y fallos aislados |
| **Ingesta automática** | Descargar material nuevo accesible, deduplicar, normalizar con FFmpeg y generar miniaturas |
| **Catálogo público** | Metadatos ES/EN, 9:16 o 16:9, resolución, FPS, duración y procedencia |
| **GitHub Releases** | Almacenar los clips procesados y hacerlos recuperables sin buscar dos veces |

**Stack planificado:** Cloudflare Workers + TypeScript (API y MCP), GitHub Actions + Python/FFmpeg (procesamiento), GitHub Releases y catálogo JSON. No se necesita un servidor Oracle ni una web Astro.

## Fuentes del roadmap

**Stock:** Pexels, Pixabay. **Archivos:** Wikimedia Commons, Internet Archive, NASA. **Video:** YouTube, RuTube, PeerTube, Odysee, VK Video, Bilibili. **Redes:** Douyin, TikTok, Instagram Reels y Snapchat.

Cada fuente tendrá un adaptador con capacidades y estados verificables. La posibilidad técnica de descargar archivos varía entre plataformas; no se considera integrado un proveedor solo por figurar en esta lista.

[Leer matriz de fuentes e idiomas](docs/FUENTES_IDIOMAS.md)

## Ruta de trabajo

El roadmap tiene **11 hitos, de M0 a M10**: estructura inicial, catálogo y Releases, FFmpeg, motor multilingüe obligatorio, grupos de proveedores, API/MCP, publicación automática, aportes comunitarios y validación final.

**[Consultar el roadmap completo con criterios de aceptación](docs/ROADMAP.md)**

### Documentación técnica

| Documento | Contenido |
|---|---|
| [Proyecto y alcance](docs/PROYECTO.md) | Requisitos, casos de uso y límites |
| [Arquitectura](docs/ARQUITECTURA.md) | Componentes y ejecución sin servidor propio |
| [Fuentes e idiomas](docs/FUENTES_IDIOMAS.md) | Proveedores y cuatro idiomas obligatorios |
| [Contratos REST/MCP](docs/CONTRATOS_API_MCP.md) | Interfaces y metadatos previstos |
| [Roadmap](docs/ROADMAP.md) | Hitos, pruebas y condiciones de cierre |
| [QA y operación](docs/QA_OPERACION.md) | Verificación, controles y costos |
| [Especificación completa](docs/ESPECIFICACION_COMPLETA.md) | Documento integral original, conservado sin cambios de contenido |
| [Identidad visual](docs/IDENTIDAD.md) | Mascota Klipi, paleta y SVG |

## Conoce a Klipi

<img src="https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/assets/klipi-mascot.svg" width="190" align="right" alt="Klipi, mascota exploradora de Clipoteca" />

**Klipi** es el explorador de fotogramas de la biblioteca: un personaje original que combina el contorno de una película, un botón de reproducción y una lupa para descubrir escenas.

Los SVG originales del [banner](assets/clipoteca-banner.svg), [logotipo](assets/clipoteca-logo.svg), [isotipo](assets/clipoteca-mark.svg), [mascota](assets/klipi-mascot.svg) y [tarjeta social](assets/clipoteca-social.svg) están incluidos en este repositorio.

## Colaboración y licencia

El proyecto se publica con **[licencia MIT](LICENSE)**, compatible con desarrollar en el futuro funciones de créditos, servicios comerciales u otras modalidades de acceso. La implementación de créditos no forma parte de esta primera fase; los recursos almacenados públicamente en GitHub no se vuelven privados por incorporar créditos a una API.

Puedes [proponer ideas y mejoras](CONTRIBUTING.md). Las políticas comunitarias para los clips y sus aportes se debatirán posteriormente.

---

<div align="center">

**Una escena encontrada. Una biblioteca que crece.**

<img src="https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/assets/clipoteca-logo.svg" width="280" alt="Logotipo Clipoteca" />

</div>
