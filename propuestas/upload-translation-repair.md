# Public upload translation: operational proposal

Observed on run 45737718171 (head 62c9149): the registered upload has no English
description and only the English tag "dog". The report from the operator identifies
disproportionate_translation, even after the extra-sync retry. This is a quality
guard, not proof of its upstream cause. Do not relax it.

## Environment comparison

* All three workflows use ubuntu-latest, Python 3.13, repository-root working
  directory and the same prepare-translations composite action, cache key and
  cached paths. HOME, STANZA_RESOURCES_DIR, ARGOS_DEVICE_TYPE and ARGOS_CHUNK_TYPE
  are not explicitly set in any of them.
* Ingestion alone initially performs base-only uv sync and treats preparation as
  best effort. real-argos prepares first and then explicitly syncs frozen translate
  and dev extras; contribution prepares first and uses --no-sync. The composite
  action's prepare_resources call uses uv run with implicit sync in all three.
* The proposal removes the preliminary base-only sync, mirrors the final real-argos
  sync, allows 45 minutes like contribution, and preserves base-only fallback if
  optional setup fails. It does not change the shared cache or validation rules.
* No observed runtime difference in HOME/chunk/device settings can be inferred
  from YAML. The raw output, package versions, installed paths and allowlisted
  resource settings are now logged for a fixed synthetic sentence. Compare with
  the same probe in the CI environment before attributing a root cause.

After reviewing the PR, copy propuestas/media-ingest.yml to
.github/workflows/media-ingest.yml as the authorized owner. Do not deploy as part
of this PR. In the real-argos job, run the probe with the same ARGOS_MODEL_CACHE_DIR
and ARGOS_MODEL_PINS_JSON variables if comparing the two runners. The existing
real-argos pytest job now includes the exact failing sentence.

## Repair three registered uploads (operator review required)

The proposed JSON contains explicit English translations, not unverified Argos
output. Review the three descriptions and tag lists first. In a fresh checkout
of main containing the merged repair code, run:

```sh
uv sync --frozen
uv run --no-sync python -m clipoteca.repair_upload_metadata \
  --reviewed-overrides propuestas/upload-metadata-overrides.json
uv run --no-sync python -m clipoteca.repair_upload_metadata \
  --reviewed-overrides propuestas/upload-metadata-overrides.json --write
git diff -- data/aportes.json
uv run --no-sync python -m clipoteca.repair_upload_metadata \
  --reviewed-overrides propuestas/upload-metadata-overrides.json
```

Expected first dry run on the observed index: changed=3 pending=0; final dry run:
changed=0 pending=0. Only the targeted registered records change, and a mismatched
Spanish source, missing record, or conflicting English field aborts before writing.
The incomplete automatic dog tag is completed; Lady and leia remain exactly
as supplied. Existing URLs, hashes and media assets remain untouched. Commit
the index only after reviewing the diff; no publishing step is performed here.
