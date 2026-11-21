# Clipoteca M20: runner propio (CachyOS / Arch Linux)

Este PR agrega seleccion declarativa de proveedores entre GitHub-hosted Actions y un runner autoalojado. La configuracion por defecto es segura: `data/runner-routing.json` contiene `{"self_hosted_providers":[]}`, por lo que **todos los proveedores siguen en Actions** si aun no existe PC/runner dedicado. No se despliega nada con este PR ni se toca `.github/`.

## Preparar el PC

1. Crear un usuario Linux **sin privilegios** (por ejemplo `clipoteca-runner`) sin acceso a Docker, sudo, claves personales ni repositorios de trabajo. Preparar una carpeta de runner de permisos exclusivos y espacio suficiente para video temporal. La GPU es opcional, utilizada solo para embeddings cuando el modelo lo permita; ffmpeg funciona en CPU.
2. Instalar ffmpeg, git, curl y Python **3.13** (CachyOS/Arch: `sudo pacman -Syu --needed ffmpeg git curl` y una instalacion administrada de Python 3.13), junto con `uv`. Comprobar `ffmpeg -version`, `uv --version`, `python3.13 --version` y Node 22 cuando se necesiten tests Worker.
3. En GitHub, ir a **Settings > Actions > Runners > New self-hosted runner** para el repositorio. Seleccionar Linux x64 (o ARM64 segun host), y ejecutar solo en la cuenta restringida los comandos de instalacion indicados por GitHub, con el token temporal que entrega esa pantalla. **No guardar ni registrar el token** en este repo, en el chat ni en archivos del PR. Registrar la etiqueta personalizada `clipoteca-home`, ademas de `self-hosted`.
4. Instalar el servicio systemd de runner usando el instalador oficial `sudo ./svc.sh install clipoteca-runner` y `sudo ./svc.sh start`; revisar `systemctl status 'actions.runner.*'`. El comando sudo es solo para registrar el servicio, nunca debe otorgar permisos root al proceso que ejecuta jobs.
5. Para activar un proveedor despues de hacer smoke y comprobar que falla en Actions pero funciona localmente, poner su nombre exacto en `data/runner-routing.json` bajo `self_hosted_providers`. Por defecto la lista sigue vacia.

## Ejecutar manualmente el pipeline

Seleccion normal (todas las fuentes que NO estan asignadas al runner propio):

```sh
CLIPOTECA_RUNNER=actions uv run python -m clipoteca ingest --query "lluvia" --providers bilibili
```

Seleccion runner local (solamente nombres asignados en el JSON):

```sh
CLIPOTECA_RUNNER=self-hosted uv run python -m clipoteca ingest --query "lluvia" --providers bilibili
```

El selector tambien acepta `--runner actions|self-hosted` y `--runner-config data/runner-routing.json`. Si no hay fuentes para el runner seleccionado, devuelve `no_sources_for_runner` sin iniciar ingestion ni consumir red. No se altera el identificador de solicitud de los filtros; es responsabilidad del operador asignar cada trabajo/proveedor a **una sola** clase de runner, para evitar que dos jobs escriban el mismo estado a la vez.

## YAML propuesto para revision manual (NO aplicado)

Estos cambios son para `.github/workflows/ingest.yml` (y, por analogia, `smoke.yml`). Mantener solo workflows que el operador dispare explicitamente (`workflow_dispatch`), sin disparadores `pull_request`, `pull_request_target` ni codigo de forks en el runner privado.

```yaml
on:
  workflow_dispatch:
    inputs:
      runner:
        description: Runtime class for provider partition
        type: choice
        required: false
        default: actions
        options:
          - actions
          - self-hosted

jobs:
  ingest-actions:
    if: github.event_name == 'workflow_dispatch' && inputs.runner != 'self-hosted'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      # Mantener checkout, uv, ffmpeg, Argos y todos los pasos de ingest existentes.
      - name: Run selected providers
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          CLIPOTECA_RUNNER: actions
          CLIPOTECA_QUERY: ${{ inputs.query }}
        run: uv run --frozen python -m clipoteca ingest --query="$CLIPOTECA_QUERY" --runner actions

  ingest-self-hosted:
    if: github.event_name == 'workflow_dispatch' && inputs.runner == 'self-hosted'
    runs-on: [self-hosted, clipoteca-home]
    permissions:
      contents: write
    timeout-minutes: 45
    steps:
      # Mantener el mismo checkout y setup verificado del job Actions, instalar
      # dependencias sin sudo desde el propio usuario de servicio, y aislar.
      - name: Run selected providers
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          CLIPOTECA_RUNNER: self-hosted
          CLIPOTECA_QUERY: ${{ inputs.query }}
        run: uv run --frozen python -m clipoteca ingest --query="$CLIPOTECA_QUERY" --runner self-hosted
```

El YAML es una **plantilla de incorporacion**, no reemplazo completo de los pasos reales: conservar los inputs y comandos actuales (`request_id`, `filters_json`, `max_results`, caches, Argos, metrics) y su identidad. No duplicar workflows ni crear rutas nuevas de dispatch en el Worker. Cuando se envien inputs de runner por API, ampliar la definicion de `workflow_dispatch.inputs.runner` en el mismo workflow y validar que el actor autorizado controle ese campo.

Para `smoke.yml`, usar exclusivamente la misma condicion y label:

```yaml
jobs:
  smoke-self-hosted:
    if: github.event_name == 'workflow_dispatch' && inputs.runner == 'self-hosted'
    runs-on: [self-hosted, clipoteca-home]
    permissions:
      contents: read
```

## Seguridad de repo publico

El codigo de un PR de un fork no debe ejecutarse jamas en este host: permite leer el sistema de archivos, secretos y red del usuario. Restringir el workflow del runner a `workflow_dispatch` del propio repositorio y a un actor con permisos de escritura. Configurar aprobaciones y reglas para evitar modificaciones no revisadas al workflow; fijar dependencias, evitar permisos amplios, mantener el usuario separado, firewall/sandbox y actualizaciones del runner. No almacenar credenciales permanentes en la carpeta de trabajo ni montar el directorio personal. El job recibe solo el token efimero que necesita.

La separacion de proveedores NO prueba acceso de Bilibili: primero ejecutar smoke de busqueda y descarga controlada desde Actions; asignarlo al runner local solo si ese smoke confirma que Actions esta bloqueado. El codigo Bilibili es `best_effort` hasta ese smoke.
