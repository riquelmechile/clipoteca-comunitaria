# M20/M24 - runner propio para Clipoteca en CachyOS/Arch

**Estado actual:** codigo de enrutamiento preparado; no hay runner configurado ni smoke confirmado por este PR. En `data/runner-routing.json`, `{"self_hosted_providers":[]}` deja todos los proveedores en Actions. Un runner propio solo se justifica tras evidencia de bloqueo en Actions y funcionamiento en PC aislado.

## 1. Riesgo critico de un repo publico

**Nunca confiar solo en un `if` del workflow.** Un PR de fork podria anadir OTRO workflow con `runs-on: [self-hosted, clipoteca-home]` y trigger `pull_request` para ejecutar codigo arbitrario en el PC. GitHub > Settings > Actions > General: elegir **Require approval for all outside collaborators**, no solo first-time contributors. No enviar tokens ni secretos a PR de forks, no usar `pull_request_target` con checkout del fork, bloquear workflows no revisados en branch protection y revisar cambios de workflows de manera independiente.

Los **runner groups restringidos por workflow existen para organizaciones/enterprise**; este es un repo personal, no asumir ese control. Lo mas seguro es un runner **efimero** (`./config.sh --ephemeral`) en una VM o contenedor dedicado, sin acceso a los archivos de la sesion personal, ni redes de confianza, montajes de Docker ni otras credenciales. **No basta una VLAN si puede alcanzar LAN u otros servicios locales.** Bloquear acceso a la LAN de la casa; agentes como Ollama en localhost o servicios privados del host podrian ser accesibles si no se aisla tambien el espacio de red. Usar firewall, red NAT aislada, ACL de salida, DNS restringido y sin acceso al host/LAN.

**No conviene instalar runner propio** para un repo publico si se reutiliza el desktop de uso diario, el usuario del runner tiene sudo/Docker, hay secretos personales, llaves SSH o servicios LAN/localhost accesibles, no se puede mantener un entorno efimero supervisado o no hay aprobacion de contribuciones externas. En esos casos mantener Actions y declarar `ip_blocked`/`unavailable` en vez de exponer el PC.

## 2. Provisionamiento restringido

1. Preparar VM o contenedor de uso exclusivo, disco temporal independiente y usuario `clipoteca-runner` sin sudo, Docker, acceso SSH al host ni permisos sobre `HOME` personal. Para Linux Arch/CachyOS instalar `ffmpeg`, `git`, `curl`, Python 3.13, `uv` y Node 22 (si se ejecutan tests Worker). GPU es opcional.
2. GitHub repo > Settings > Actions > Runners > New self-hosted runner. Registrar label adicional `clipoteca-home`. Ejecutar comandos oficiales bajo usuario restringido y no registrar el token temporal ni un personal access token en archivo, stdout de CI o PR. Preferir `./config.sh --ephemeral` y recrear la VM por cada job.
3. Para servicio persistente solo dentro de VM aislada, instalar con `sudo ./svc.sh install clipoteca-runner` y `sudo ./svc.sh start`. Aplicar override a la unidad oficial, ajustando ruta a la carpeta dedicada:
```ini
[Service]
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/srv/clipoteca-runner /var/tmp
```
No alojar el runner bajo `/home` si `ProtectHome=true`. Probar el workspace, cache, ffmpeg y logs con los permisos efectivos. Restringir acceso de red por fuera de systemd.
4. Controlar autoactualizaciones: si se deshabilitan con `./config.sh --disableupdate`, mantener un procedimiento manual documentado para actualizar la version del runner dentro del plazo de compatibilidad de GitHub. Si se permiten, controlar reinicios, revisiones y funcionamiento. Nunca olvidar actualizar.
5. Probar `ffmpeg -version`, `uv --version`, `python3.13 --version`, `node --version` si corresponde. Validar espacio, DNS publico e imposibilidad de conectar a LAN. Poner el proveedor en `self_hosted_providers` solo tras smoke comparativo. El default debe continuar vacio.

## 3. CLI y recibos

Normal (Actions):
```sh
CLIPOTECA_RUNNER=actions uv run --no-sync python -m clipoteca ingest --query "lluvia" --providers bilibili
```

Runner aislado, solo con enrutamiento aprobado:
```sh
CLIPOTECA_RUNNER=self-hosted uv run --no-sync python -m clipoteca ingest --query "lluvia" --providers bilibili --runner self-hosted
```

`--runner actions|self-hosted` y `--runner-config data/runner-routing.json` seleccionan una particion. Las fuentes omitidas se registran en el job remoto (cuando `CLIPOTECA_PERSIST_JOBS=1`) y en el reporte CLI como `deferred_to_self_hosted` / `deferred_to_actions`. Un conjunto de proveedores vacio persiste estado `unavailable`, con ID determinista, y retorna codigo de salida distinto de cero para evitar `404` repetidos del Worker. No iniciar el mismo request_id desde ambos runners en paralelo.

## 4. Plantilla completa para ingest.yml (aplicacion MANUAL)

Este documento NO modifica `.github/`. Esta plantilla incorpora los inputs actuales, guard de main, router, secretos opcionales y `--no-sync`. El operador debe coordinarla con **M22 query_embedding** de [OPERACION_V2.md](OPERACION_V2.md) antes de usar el modo liviano. Es un modelo de reemplazo para revision, no permiso de despliegue. La semantica del CLI es la misma que en el workflow actual.

```yaml
name: Ingest verified video candidates
on:
  workflow_dispatch:
    inputs:
      query:
        description: Scene query
        required: true
        type: string
      aspect_ratio:
        description: Optional orientation filter
        required: false
        type: string
      max_duration:
        description: Max output seconds
        required: false
        default: "20"
        type: string
      max_results:
        description: Max new clips
        required: false
        default: "3"
        type: string
      request_id:
        description: Optional deterministic request ID
        required: false
        type: string
      providers:
        description: Comma-separated source allowlist
        required: false
        type: string
      filters_json:
        description: Canonical full filters from Worker
        required: false
        default: ""
        type: string
      concurrency:
        description: Discovery concurrency
        required: false
        default: "4"
        type: string
      runner:
        description: Runtime class
        required: false
        type: choice
        default: actions
        options:
          - actions
          - self-hosted

permissions:
  contents: read

concurrency:
  group: clipoteca-ingest-${{ inputs.request_id || github.run_id }}
  cancel-in-progress: false

jobs:
  ingest-actions:
    if: github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch' && inputs.runner != 'self-hosted'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    timeout-minutes: 45
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
      - uses: astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e
        with:
          python-version: "3.13"
      - run: sudo apt-get update && sudo apt-get install -y ffmpeg
      - name: Prepare verified translation resources
        uses: ./.github/actions/prepare-translations
      - name: Publish credential-presence source snapshot
        env:
          GITHUB_REPOSITORY: ${{ github.repository }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PEXELS_API_KEY: ${{ secrets.PEXELS_API_KEY }}
          PIXABAY_API_KEY: ${{ secrets.PIXABAY_API_KEY }}
          NASA_API_KEY: ${{ secrets.NASA_API_KEY }}
          YOUTUBE_API_KEY: ${{ secrets.YOUTUBE_API_KEY }}
          VK_ACCESS_TOKEN: ${{ secrets.VK_ACCESS_TOKEN }}
          DOUYIN_ACCESS_TOKEN: ${{ secrets.DOUYIN_ACCESS_TOKEN }}
          TIKTOK_ACCESS_TOKEN: ${{ secrets.TIKTOK_ACCESS_TOKEN }}
          DOUYIN_COOKIE: ${{ secrets.DOUYIN_COOKIE }}
          TIKTOK_COOKIE: ${{ secrets.TIKTOK_COOKIE }}
          BILIBILI_COOKIE: ${{ secrets.BILIBILI_COOKIE }}
          EUROPEANA_API_KEY: ${{ secrets.EUROPEANA_API_KEY }}
          COVERR_API_KEY: ${{ secrets.COVERR_API_KEY }}
          NARA_API_KEY: ${{ secrets.NARA_API_KEY }}
          DPLA_API_KEY: ${{ secrets.DPLA_API_KEY }}
          SMITHSONIAN_API_KEY: ${{ secrets.SMITHSONIAN_API_KEY }}
          INSTAGRAM_ACCESS_TOKEN: ${{ secrets.INSTAGRAM_ACCESS_TOKEN }}
          INSTAGRAM_USER_ID: ${{ vars.INSTAGRAM_USER_ID }}
          SNAPCHAT_ACCESS_TOKEN: ${{ secrets.SNAPCHAT_ACCESS_TOKEN }}
          SNAPCHAT_PROFILE_ID: ${{ vars.SNAPCHAT_PROFILE_ID }}
          PEERTUBE_ALLOWED_INSTANCES: ${{ vars.PEERTUBE_ALLOWED_INSTANCES }}
        run: uv run --no-sync python -m clipoteca.source_snapshot --publish
      - name: Ingest
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          PEXELS_API_KEY: ${{ secrets.PEXELS_API_KEY }}
          PIXABAY_API_KEY: ${{ secrets.PIXABAY_API_KEY }}
          DOUYIN_COOKIE: ${{ secrets.DOUYIN_COOKIE }}
          TIKTOK_COOKIE: ${{ secrets.TIKTOK_COOKIE }}
          BILIBILI_COOKIE: ${{ secrets.BILIBILI_COOKIE }}
          EUROPEANA_API_KEY: ${{ secrets.EUROPEANA_API_KEY }}
          COVERR_API_KEY: ${{ secrets.COVERR_API_KEY }}
          NARA_API_KEY: ${{ secrets.NARA_API_KEY }}
          DPLA_API_KEY: ${{ secrets.DPLA_API_KEY }}
          SMITHSONIAN_API_KEY: ${{ secrets.SMITHSONIAN_API_KEY }}
          ARGOS_MODEL_CACHE_DIR: .cache/argos
          ARGOS_MODEL_PINS_JSON: data/argos-pins.json
          CLIPOTECA_RUNNER: actions
          CLIPOTECA_QUERY: ${{ inputs.query }}
          CLIPOTECA_ASPECT: ${{ inputs.aspect_ratio }}
          CLIPOTECA_MAX_SECONDS: ${{ inputs.max_duration }}
          CLIPOTECA_MAX_RESULTS: ${{ inputs.max_results }}
          CLIPOTECA_REQUEST_ID: ${{ inputs.request_id }}
          CLIPOTECA_CONCURRENCY: ${{ inputs.concurrency }}
          CLIPOTECA_PROVIDERS: ${{ inputs.providers }}
          CLIPOTECA_FILTERS_JSON: ${{ inputs.filters_json }}
          CLIPOTECA_PERSIST_JOBS: "1"
        run: |
          uv run --no-sync python -m clipoteca ingest \
            --query "$CLIPOTECA_QUERY" --aspect-ratio "$CLIPOTECA_ASPECT" \
            --max-duration "$CLIPOTECA_MAX_SECONDS" \
            --max-results "$CLIPOTECA_MAX_RESULTS" --request-id "$CLIPOTECA_REQUEST_ID" \
            --concurrency "$CLIPOTECA_CONCURRENCY" \
            --providers "$CLIPOTECA_PROVIDERS" \
            --filters-json "$CLIPOTECA_FILTERS_JSON" --runner actions
      - name: Upload job records and metrics
        if: always()
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
        with:
          name: clipoteca-jobs-${{ github.run_id }}
          path: data/jobs/
          if-no-files-found: ignore
          retention-days: 30

  ingest-self-hosted:
    if: github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch' && inputs.runner == 'self-hosted'
    runs-on: [self-hosted, clipoteca-home]
    permissions:
      contents: write
    timeout-minutes: 45
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
      - uses: astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e
        with:
          python-version: "3.13"
      - name: Verify local FFmpeg
        run: ffmpeg -version
      - name: Prepare verified translation resources
        uses: ./.github/actions/prepare-translations
      - name: Ingest on isolated runner
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          DOUYIN_COOKIE: ${{ secrets.DOUYIN_COOKIE }}
          TIKTOK_COOKIE: ${{ secrets.TIKTOK_COOKIE }}
          BILIBILI_COOKIE: ${{ secrets.BILIBILI_COOKIE }}
          PEXELS_API_KEY: ${{ secrets.PEXELS_API_KEY }}
          PIXABAY_API_KEY: ${{ secrets.PIXABAY_API_KEY }}
          EUROPEANA_API_KEY: ${{ secrets.EUROPEANA_API_KEY }}
          COVERR_API_KEY: ${{ secrets.COVERR_API_KEY }}
          NARA_API_KEY: ${{ secrets.NARA_API_KEY }}
          DPLA_API_KEY: ${{ secrets.DPLA_API_KEY }}
          SMITHSONIAN_API_KEY: ${{ secrets.SMITHSONIAN_API_KEY }}
          ARGOS_MODEL_CACHE_DIR: .cache/argos
          ARGOS_MODEL_PINS_JSON: data/argos-pins.json
          CLIPOTECA_RUNNER: self-hosted
          CLIPOTECA_QUERY: ${{ inputs.query }}
          CLIPOTECA_ASPECT: ${{ inputs.aspect_ratio }}
          CLIPOTECA_MAX_SECONDS: ${{ inputs.max_duration }}
          CLIPOTECA_MAX_RESULTS: ${{ inputs.max_results }}
          CLIPOTECA_REQUEST_ID: ${{ inputs.request_id }}
          CLIPOTECA_CONCURRENCY: ${{ inputs.concurrency }}
          CLIPOTECA_PROVIDERS: ${{ inputs.providers }}
          CLIPOTECA_FILTERS_JSON: ${{ inputs.filters_json }}
          CLIPOTECA_PERSIST_JOBS: "1"
        run: |
          uv run --no-sync python -m clipoteca ingest \
            --query "$CLIPOTECA_QUERY" --aspect-ratio "$CLIPOTECA_ASPECT" \
            --max-duration "$CLIPOTECA_MAX_SECONDS" \
            --max-results "$CLIPOTECA_MAX_RESULTS" --request-id "$CLIPOTECA_REQUEST_ID" \
            --concurrency "$CLIPOTECA_CONCURRENCY" --providers "$CLIPOTECA_PROVIDERS" \
            --filters-json "$CLIPOTECA_FILTERS_JSON" --runner self-hosted
      - name: Upload isolated job records and metrics
        if: always()
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
        with:
          name: clipoteca-jobs-${{ github.run_id }}
          path: data/jobs/
          if-no-files-found: ignore
          retention-days: 30
```

**Nota integracion:** este ejemplo replica los inputs del workflow actual y ofrece rutas alternativas; aun se debe incorporar el bloque M22 de `mode: query_embedding` descrito en [OPERACION_V2.md](OPERACION_V2.md) y validar workflows con GitHub antes de habilitar flags. No despliega ni crea runner en este PR.

## 5. Plantilla completa smoke.yml

La prueba de cuatro idiomas no descarga media. No ejecutar smoke desde PR de fork. Conservar los secretos opcionales solo como `secrets` y subir artifacts, nunca imprimirlos.

```yaml
name: Live provider smoke and usage evidence
on:
  workflow_dispatch:
    inputs:
      runner:
        description: Smoke runner
        type: choice
        default: actions
        required: false
        options:
          - actions
          - self-hosted
permissions:
  contents: read
jobs:
  smoke-actions:
    if: github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch' && inputs.runner != 'self-hosted'
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: read
      actions: read
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e
        with:
          python-version: "3.13"
      - run: uv sync --frozen
      - name: Probe sources four languages
        env:
          GITHUB_REPOSITORY: ${{ github.repository }}
          GITHUB_RUN_ID: ${{ github.run_id }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PEXELS_API_KEY: ${{ secrets.PEXELS_API_KEY }}
          PIXABAY_API_KEY: ${{ secrets.PIXABAY_API_KEY }}
          DOUYIN_COOKIE: ${{ secrets.DOUYIN_COOKIE }}
          TIKTOK_COOKIE: ${{ secrets.TIKTOK_COOKIE }}
          BILIBILI_COOKIE: ${{ secrets.BILIBILI_COOKIE }}
          EUROPEANA_API_KEY: ${{ secrets.EUROPEANA_API_KEY }}
          COVERR_API_KEY: ${{ secrets.COVERR_API_KEY }}
          NARA_API_KEY: ${{ secrets.NARA_API_KEY }}
          DPLA_API_KEY: ${{ secrets.DPLA_API_KEY }}
          SMITHSONIAN_API_KEY: ${{ secrets.SMITHSONIAN_API_KEY }}
        run: uv run --no-sync python scripts/smoke_sources.py --mode all --output "data/reports/smoke-$GITHUB_RUN_ID.json"
      - name: Preserve measured results
        if: always()
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
        with:
          name: clipoteca-smoke-${{ github.run_id }}
          path: data/reports/
          if-no-files-found: warn
          retention-days: 30

  smoke-self-hosted:
    if: github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch' && inputs.runner == 'self-hosted'
    runs-on: [self-hosted, clipoteca-home]
    timeout-minutes: 30
    permissions:
      contents: read
      actions: read
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e
        with:
          python-version: "3.13"
      - run: uv sync --frozen
      - name: Probe sources four languages in isolated VM
        env:
          GITHUB_REPOSITORY: ${{ github.repository }}
          GITHUB_RUN_ID: ${{ github.run_id }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          DOUYIN_COOKIE: ${{ secrets.DOUYIN_COOKIE }}
          TIKTOK_COOKIE: ${{ secrets.TIKTOK_COOKIE }}
          BILIBILI_COOKIE: ${{ secrets.BILIBILI_COOKIE }}
          PEXELS_API_KEY: ${{ secrets.PEXELS_API_KEY }}
          PIXABAY_API_KEY: ${{ secrets.PIXABAY_API_KEY }}
          EUROPEANA_API_KEY: ${{ secrets.EUROPEANA_API_KEY }}
          COVERR_API_KEY: ${{ secrets.COVERR_API_KEY }}
          NARA_API_KEY: ${{ secrets.NARA_API_KEY }}
          DPLA_API_KEY: ${{ secrets.DPLA_API_KEY }}
          SMITHSONIAN_API_KEY: ${{ secrets.SMITHSONIAN_API_KEY }}
        run: uv run --no-sync python scripts/smoke_sources.py --mode all --output "data/reports/smoke-$GITHUB_RUN_ID.json"
      - name: Preserve isolated smoke evidence
        if: always()
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
        with:
          name: clipoteca-smoke-${{ github.run_id }}
          path: data/reports/
          if-no-files-found: warn
          retention-days: 30
```

**Sigue pendiente** medir Bilibili desde Actions y, solo ante bloqueo confirmado, comparar con el runner aislado. El contenido de este documento no es evidencia real ni resultado de una ejecucion.
