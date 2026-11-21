# M22 semantic search - current contract (r4)

## Runtime flags and costs

- `SEMANTIC_SEARCH=1` enables vector search; without it, existing lexical search stays active.
- `CLIPOTECA_QUERY_DISPATCH_MODE=1` is only enabled after the workflow YAML below is actually deployed. Missing query vectors without that mode return `semantic: unavailable` without dispatching an ingest job.
- `SEMANTIC_MAX_CLIPS` defaults to 256 and is capped at 4096. Exceeding the budget preserves lexical results with `semantic_reason: cpu_budget_exceeded`. A Workers Free request has 10 ms CPU; even 256 requires measurement. Paid CPU budget is different; do not claim free-tier performance without profiling.
- `SEMANTIC_MIN_COSINE` defaults to 0.20; at most 50 candidate videos satisfy `cos >= max(absolute_tau, best_cosine - 0.05)` before lexical/semantic Reciprocal Rank Fusion (`k=60`). No candidates above threshold returns lexical results with `semantic_reason: no_semantic_match`.
- Each manifest fetch is TTL 10 seconds. The packed Float32 corpus is cached by manifest SHA for 20 minutes **only when nonempty and clean**. Empty or partially corrupt corpus is cached at most 60 seconds, so an intermittent 503 does not suppress semantic search for 20 minutes.
- Before dispatching a missing query vector, the Worker checks the cached manifest. An empty manifest produces `semantic_reason: empty_corpus` and **no GitHub Actions dispatch**. Legacy sidecars are checked when no M22 manifest exists.

Reference: https://developers.cloudflare.com/workers/platform/limits/

## Clip embedding storage

M21 CLIP ViT-B/32 video-frame vectors share the same 512-dimensional space as the `sentence-transformers/clip-ViT-B-32-multilingual-v1` text encoder. The Worker never runs Torch; Python encodes text and images.

`data/embeddings/manifest.json` v3 stores SHA-256, Git blob SHA, 256-vector sealed shards, bounded hashed tails and separate ID files. Vectors are 512 little-endian float16 values (1024 bytes each); the Worker creates a float16-to-float32 lookup table once per isolate. All blob paths, including the tail, contain the checksum. Sealed files are never changed. A new tail and manifest are committed atomically via Git Data API with optimistic fast-forward CAS; obsolete paths leave HEAD in the same commit. A partial/corrupt shard is isolated with `semantic_reason: shard_corrupt`. Large manifests and CPU limits fail closed to lexical search.

`CLIPOTECA_M21_SIDECARS=1` retains M21 dual-write until migration is verified; then set it to `0` to avoid one new sidecar commit per video. A failed M21 write cannot invalidate the M22 result, and vice versa. `GitHubShards.publish_many` flushes **once per ingest job, including CancelledError and timeout paths in finally**. A remote flush failure is a warning, not a reversal of already published clips.

Migrate historical records without removing them until verified:

`uv run python -m clipoteca migrate-embeddings --root data/embeddings`

`GITHUB_REPOSITORY=owner/repo GITHUB_TOKEN=... uv run python -m clipoteca migrate-embeddings --root data/embeddings --publish`

## Query vector cache and Unicode

`data/embeddings/queries/manifest.json` is a bounded cache: 64-vector sealed shards, checksum-addressed tail, one Git Data API commit per publish batch, maximum 512 active query vectors. When full, the oldest **sealed shard** is evicted (FIFO, **not LRU**). Paths both created and pruned inside one batch are removed from the proposed tree rather than emitted as `sha: null`; GitHub rejects deletion of paths absent in the base tree.

The query is normalized by NFKC, Unicode casefold and whitespace collapse; `request_id` is SHA-256 of that normalized query. Worker casefold mappings use Python Unicode 17 in compact hex pairs (ASCII source). Full parity requires a Python runtime with Unicode 17, such as Python 3.15. Python 3.13 reports Unicode 15.1 and includes 91 codepoints which were unassigned in that version; a bridge is provided for U+1C89 only, so full parity should not be claimed under 3.13. Cross-runtime fixtures use escaped Unicode strings, never raw control characters.

Transitional reads of old per-query JSON remain supported; new writes use query shards. An operator may clean legacy query JSON files only after verifying active shard coverage.

## Rate limits, security and workflow queue

A query-embedding dispatch requires both flags, a valid token and a nonempty corpus. Reservations for digest, IP and the hour counter are synchronous within one isolate, before the first await. A real HTTP attempt gets a 10-minute digest marker even on 422/network failure; a quota denial does not poison that digest for another user. Limit: 20 attempts per hour best-effort per Cloudflare colo. Cloudflare Cache API is not a globally atomic rate limiter. Both query embeddings and `import_if_missing` share existing import checks; a script resolver must not open an independent Actions lane.

The global GitHub Actions concurrency group for `query_embedding` admits at most **one running and one pending** workflow. New pending runs can replace and cancel older pending runs. This is deliberate cost control, **not a durable FIFO queue**. Clients should poll job/request status and retry according to quota rules.

Optional `QUERY_ENCODER_URL` requires HTTPS, blocks IP literals, localhost, private/rebinding suffixes and redirects, limits streamed responses to 16 KiB, and validates 512 finite values. `QUERY_ENCODER_HOSTS` can restrict accepted hostnames.

Cloudflare Cache API caveat: https://developers.cloudflare.com/workers/runtime-apis/cache/

## YAML for the operator (NOT changed in this PR)

Apply manually to `.github/workflows/ingest.yml` only after review. Under `on.workflow_dispatch.inputs` add:

```yaml
      mode:
        description: Execution mode
        required: false
        type: choice
        default: ingest
        options:
          - ingest
          - query_embedding
```

Use the shared, bounded concurrency group and job timeout:

```yaml
concurrency:
  group: ${{ inputs.mode == 'query_embedding' && 'clipoteca-query-embedding' || format('clipoteca-ingest-{0}', inputs.request_id || github.run_id) }}
  cancel-in-progress: false
```

```yaml
    timeout-minutes: ${{ inputs.mode == 'query_embedding' && 10 || 45 }}
```

Guard the existing apt-get ffmpeg step, translation resource preparation, source snapshot and normal Ingest steps with `if: inputs.mode != 'query_embedding'`. Change Upload job records and metrics from `if: always()` to `if: always() && inputs.mode != 'query_embedding'`. Checkout and setup-uv remain available in both modes.

After setup-uv add:

```yaml
      - name: Cache query encoder dependencies and HuggingFace weights
        if: inputs.mode == 'query_embedding'
        uses: actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830 # v4
        with:
          path: |
            .cache/uv
            .cache/huggingface
          key: query-embedding-v1-${{ runner.os }}-${{ hashFiles('uv.lock') }}

      - name: Cache semantic query vector (M22)
        if: inputs.mode == 'query_embedding'
        env:
          GITHUB_REPOSITORY: ${{ github.repository }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          CLIPOTECA_QUERY: ${{ inputs.query }}
          CLIPOTECA_TEXT_MODEL_REVISION: e64755877a7a03686463dbdf668a080bb78ff0fa
          CLIPOTECA_IMAGE_MODEL_REVISION: afe976e3f4ea04b633edb0089a7e5088ddbe9212
          UV_CACHE_DIR: .cache/uv
          HF_HOME: .cache/huggingface
          HF_HUB_CACHE: .cache/huggingface/hub
        run: |
          uv sync --frozen --extra embeddings
          uv run --frozen --extra embeddings python -m clipoteca query-embedding --query="$CLIPOTECA_QUERY"
```

This path does not download/normalize videos or run Argos. Once deployed and validated, enable both Worker flags. No file under `.github/` is modified by this PR.

## Validation

`uv sync --frozen --extra dev`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -q`, `npm run typecheck` and `npm test`. Offline fixtures test float16, Unicode, manifest integrity, CAS conflicts, rate limits, 300/5000 clip storage, query eviction and recovery after a transient corrupt shard. Real encoder latency and actual deployed Worker end-to-end are separate verifications, not simulated CI results.
