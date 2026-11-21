# M6c: fuentes sociales y matriz de capacidad

## M6c: Instagram, Snapchat y matriz `list_sources`

| Fuente | Endpoint oficial | ES | EN | ZH | RU | Estado y límites |
|---|---|---|---|---|---|---|
| Instagram | `GET https://graph.instagram.com/v27.0/<ig_user_id>/media?fields=id,media_type,media_url,permalink,caption,timestamp` | título propio | título propio | título propio | título propio | Requiere `INSTAGRAM_ACCESS_TOKEN` + `INSTAGRAM_USER_ID`, cuenta profesional autorizada. Solo filtra video propio, `SUPPORTED_SEARCH_ONLY`; no copia automática de `media_url` ni hashtags globales sin permiso. |
| Snapchat | `GET https://businessapi.snapchat.com/v1/public_profiles/<profile_id>/spotlights` | texto autorizado | texto autorizado | texto autorizado | texto autorizado | Requiere OAuth `SNAPCHAT_ACCESS_TOKEN` y perfil conocido `SNAPCHAT_PROFILE_ID`. Lista Spotlight de perfil autorizado; no descubre todos los Snaps públicos. `SUPPORTED_SEARCH_ONLY` en ausencia de derechos/licencia verificables; el API puede dar `media_url` efímera. |

Referencias oficiales: https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/v21.0/ (el esquema consultado publica API actual v27.0); https://marketingapi.snapchat.com/docs/profile.html (sección `Get Spotlights by Profile ID`). Para ambas plataformas se usaron fixtures completos de API pero **sin tokens live**.

`python -m clipoteca list_sources` (alias `list-sources`) retorna JSON con exactamente 21 fuentes: `name`, `category`, `status` observacional, `support_status` de adaptador, cuatro idiomas de consulta cuando soportados, `scope`, `requires`, nombres de credenciales ausentes, soporte directo y advertencias. Nunca devuelve valores de tokens. `AUTH_REQUIRED` no es un error genérico de red; se calcula por credencial. La matriz no garantiza éxito en cada idioma: un backend puede ignorar idioma o aplicar controles antifraude.

**Advertencia de derechos:** títulos, thumbnails, HLS y enlaces de reproducción no habilitan descargar y redistribuir material ajeno. Por defecto, las APIs de cuentas autorizadas se usan en modo búsqueda y el sistema no inventa archivos. El gate de descarga no reemplaza la validación de licencia.
