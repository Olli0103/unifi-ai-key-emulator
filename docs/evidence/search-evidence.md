# Search, description and storage contracts

Static inspection on 2026-09-22. No firmware was executed and no device, network service or database was changed. This note describes shipped code, not a demonstrated working emulator.

**Finding:** Native descriptions and native semantic search have recoverable contracts. A caption alone does not satisfy semantic search. The inspected versions expose two distinct generations, and they must not be mixed.

## Evidence scope

- `K` = root of the selected extracted AI Key 2.2.8 packages.
- `P` = `usr/share/unifi-protect/app` inside the extracted Protect 7.2.105 package.
- `A` = `usr/share/ai-feature-controller` inside the extracted AI feature controller 2.0.17 package.
- `P/service.js` SHA-256: `a7370e9a1b35db67d56104268841b9c2ba16342befb750ffef6654ee2b07bfdb`.

The controller bundle is minified. References below identify webpack module IDs and zero-based character offsets in that exact file. Key-side references give paths beneath K and source line ranges.

## 1. Two separate search profiles

| Contract | Older RAM / image search | New session description search |
|---|---|---|
| Stored semantic vector | `ramDetections.embedding vector(768)` | `smartDetectSessionsSearch.descEmbedding vector(384)` |
| Query encoder identifier | `clip-ViT-L-14` in Protect | `multilingual-e5-small` in Protect |
| Other vector | RAM `reidEmbedding vector(1024)` | Session member `reidEmbedding vector(512)` |
| Result ingress | Multipart `/internal/aiprocessors/recognize-anything` | JSON `/internal/aiprocessors/descriptions/:taskId` |
| Query request | UCP4 `NL_PARSE`, normally CLIP | UCP4 `NL_PARSE` with `model: multilingual-e5-small` |
| Main search | Image/text cosine similarity plus tags/labels | Description cosine similarity, optional BM25 fusion and reranking |

**Confirmed:** Protect 7.2.105 contains both paths. The session path is shipped production code and migrations, not the unrelated example database in `uaibd`. **Open:** whether the target Protect version enables this branch, what firmware matches it, and whether all advertised capabilities are active. Package presence does not establish runtime enablement.

### Older 768-dimensional path

`K/ui-slm-runtime-engine-0.0.17.3-Linux.deb/data/usr/local/bin/slmenv/text_encoder.py:14-75` loads a TensorRT engine and allocates output shape `(1,768)` in float32. Text tokens and attention mask have length 77. `slm_server.py:246-254` prepends `a photo of ` to queries shorter than three words and L2-normalizes the resulting vector before returning `txtEmbed`.

Production defaults in `slm_server.py:799-810` are `/usr/local/lib/slm_model/mlc_q4f16_0`, its `slm_model.so`, and `/usr/local/lib/text_encoder/textual_fix_shape_fp32.engine`. The standalone text-encoder script has a differently spelled test default. The production default is the relevant one.

`slm_server.py:148-156,778-796` connects to `wss://{console}:7443/wss/nl-search/v1` using UCP4, `x-ident` MAC, and the device certificate. `NL_PARSE` also returns tags, object types, and optional time filters. `IMAGE_SEARCH` fetches a console image, runs `RecognizeAnythingProc`, and returns `keyMomentsTags[0].imgEmbed` (`slm_server.py:719-770`).

Protect module `50407` (offset 4226985 vicinity) names the CLIP query model. Module `3465` (offset 316xxx) validates image-query `imgEmbed` as exactly 768 floats. The RAM inference schema near offset 1176336 validates optional frame `imgEmbed` as 768 and `reidEmbed` as 1024. The model definition near offset 4429402 uses `vector(768)` and `vector(1024)`. Module `18740` installs HNSW cosine and L2 indexes respectively, with `m=4`, `ef_construction=32`.

**Limit:** The Key package proves the tensor dimensions and preprocessing, while the controller names `clip-ViT-L-14`. The exact shipped Key encoder weights/checkpoint were not recovered here. Arbitrary 768-dimensional embeddings will not share the expected space. A replacement must use a compatible text/image pair and validate retrieval quality.

The AI feature controller demonstrates why width alone is insufficient: `A/pipelines/embedding_utils.py` targets 768 storage dimensions, while its comments and `config/gen7e/model_configs/clip_vision.json` describe a LongCLIP/CVFlow 512-dimensional output padded to 768. `config/qcs8550/model_configs/clip_vision.json` specifies 768 output. This is platform-specific evidence, not proof that either is the AI Key encoder.

### New 384-dimensional session path

`P/migrations/aiprocessor/1781535214000_create-smartDetectSessions-search.js:4-45` creates:

- `smartDetectSessionsSearch`, keyed by `sessionId`, with `descEmbedding vector(384)`, label/camera/object-type arrays, and first/last timestamps.
- HNSW cosine index on `descEmbedding`, `m=16`, `ef_construction=64`, plus filter indexes.
- `smartDetectSessionObjects`, keyed by smart-detection object ID, with session/event/camera references, timestamps, thumbnail ID, `reidEmbedding vector(512)`, and model string.

`1790000000000_add-session-search-text.js:4-11` adds `description` and `labelText` text columns. No prototype table is used to infer this schema.

Module `50407` defines `E5_QUERY_EMBED_DIM=384`. `encodeSessionSearchQuery` requires an available external AI Key, requests `NL_PARSE` with `model: multilingual-e5-small`, and uses a 10-second timeout. It rejects a supplied model name whose prefix before `@` differs and rejects vectors of any other length. Missing model metadata is accepted. Encoder failure returns no results, not a CLIP fallback.

**Compatibility gap:** The inspected Key 2.2.8 Python services contain no `/describe` route, E5 encoder, `descEmbedding`, or session-description contract. Its older `NL_PARSE` handler does not process the requested model selector and returns 768 dimensions. That response would fail the controller's new 384-dimensional check. Treat the two packages as evidence for distinct supported profiles, not as a confirmed matched version pair.

## 2. What a native session-description worker must do

The description endpoint is a callback for controller-created work, not an arbitrary event-ingest API.

1. The controller builds a session from native smart-detection objects. Module `72881` (offset 5975332) selects person, face, vehicle and animal representatives, forms an `open` or `close` pass, and creates a `generateDescription` task. Its stored arguments include `sessionId`, `cameraId`, `eventId`, `pass`, `promptProfile: session-v1`, and images or videos.
2. Module `42220` dispatches `devices.sendRequest` with command `RequestAI`, `targetUri: :7968/describe`, `timeoutMs: 30000`, a callback under `/internal/aiprocessors/descriptions/{taskId}`, and controller-owned media URLs. This preserves the existing camera and event identity.
3. Module `68823` exposes the callback with device middleware and body validation. It permits `description`, optional labels, optional `descEmbedding` array or null, optional failures, model and version. It sends HTTP 200 before asynchronous persistence. Therefore HTTP 200 is not an indexing receipt.
4. Module `31512` (offset 2810238) resolves `{taskId}` from `aiprocessorTasks`. It requires a stored smart-detection object ID, session ID, camera ID and pass of `open` or `close`. Unknown task IDs or tasks whose arguments were cleared return null and are dropped by `saveDescriptions`.
5. Module `60965` (offset 5107xxx) checks that deep understanding remains enabled for the camera, that the session exists, and that the returned description is nonempty. It sanitizes labels, creates/resolves label IDs, upserts the search-host row, tokenizes BM25 when available, updates the console session and marks the task done.

Module `20129` (offset 1715155) checks global deep-understanding enablement and whether all cameras or the specific camera are selected. There is also a development feature-flag path; its presence is not authority to enable or rely on it.

**For native search:** A nonempty description can be accepted with `descEmbedding:null`, but module `30435` (offset 2751242) filters out null `descEmbedding` in both dense and BM25 candidate queries. The worker therefore needs a compatible 384-dimensional description embedding and a matching query encoder. The callback schema itself does not enforce dimension or model compatibility; the PostgreSQL vector column and query path impose those requirements later.

The E5 checkpoint/tokenization/pooling/normalization/prefix details for generating document embeddings are **needs_evidence**. The controller's model identifier and width do not establish these details. A successful retrieval test with known positive and negative examples is required before claiming compatibility.

The old automatic caption path is separate. `K/ui-vlm-agent-0.0.11.1-Linux.deb/data/usr/local/bin/vlmenv/app.py:378-422` posts multipart `ram` JSON with event ID, success status and description to `/internal/aiprocessors/recognize-anything`, with device headers and client certificate. The model wrapper class is `InternVL2`, with a default name suggesting `InternVL2_5-1B`; production loads `/usr/local/lib/vlm-model`, so the default name alone does not establish the deployed weights. Protect module `24160` updates `ramDetections.ramDescription` and notifies clients. This does not create a session search embedding.

## 3. Storage, SQL privileges and search extensions

`K/ai-key-base-file-0.1.49.0-Linux.deb/data/usr/local/bin/maintain_postgres_unifi.sh:3-12,130-158,173-240` configures PostgreSQL 14, database/user `unifi-protect`, data under `/mnt/uiapps/postgresql_data`, and a login role that is explicitly granted SUPERUSER. Remote HBA entries are `hostssl` for configured console IPv4 `/32` addresses using SCRAM-SHA-256. The script supplies a provisioning password; it should not be treated as the live credential.

Protect modules `65737`, `63092` and `12280` connect to port 5432 with TLS and server verification disabled, first trying the device's current password, then a provisioning fallback only after an invalid-password failure. Module `27299` runs migrations and search setup before registering its SQL connection. Module `33815` runs the controller's `migrations/aiprocessor` scripts and records migration metadata and source in the remote database. An emulator therefore needs a real compatible PostgreSQL service, privileges to apply these migrations, and required extensions, rather than merely a caption HTTP endpoint.

Module `4169` (offset 363154) provisions optional `pg_tokenizer`, `vchord_bm25`, and `plpython3u` components. It reads `/usr/local/lib/e5-small/tokenizer.json`, creates `session_tok`, adds the BM25 column/index, and defines a rerank SQL function that calls `http://127.0.0.1:8123/rerank` on the search host. It revokes that function from PUBLIC and grants execution to the Protect role.

Module `30435` implements dense retrieval or dense plus BM25 reciprocal-rank fusion, optionally followed by reranking. When reranking is expected, missing hybrid objects or unavailable reranking can produce zero results. Without that requirement, it can degrade to dense-only search. The target feature flags and extensions remain **needs_evidence**.

## 4. Job lifecycle and retries

Controller modules `38701` (offset 3382597), `90379` (offset 7632935), and `74771` establish:

- Persistent task states: pending, queued, failed, failedRetry, done. Arguments are stored in `aiprocessorTasks.args` for retry.
- Description/embedding task timeout defaults to 180 seconds, with a positive configuration override. Other task types default to 30 minutes. The dispatch request timeout is separately 30 seconds.
- Completion clears task arguments. Nonretryable failure also clears them. A repeated description callback then fails the ledger validation; callbacks are not a general idempotent ingest operation.
- No free processor and some video-export failures are retryable. Unknown external failures and timeouts are marked nonretryable at this task level. A later session pass may be separate work.
- Video-export retry backoff is five minutes. Built-in AI-controller failure backoff is one minute, with a ten-minute maximum task age. These built-in rules do not establish external Key retry behavior.
- Failed-retry selection takes up to 30 latest distinct tasks. `retryTasks` is triggered from processor queue state, debounced to at most once per 60 seconds, and checks for an empty queue.
- Empty descriptions leave the session available for later retry but mark that task done. A failed search-host upsert leaves the console session unindexed for reconciliation.

**Recovery limit:** Module `66079` reconciles missing session search rows with a null description embedding; it does not regenerate the lost embedding. The code seen here cannot justify a promise of automatic full search-index recovery.

On the older Key path, `syswrapper.sh:2503-2610` retries media download/conversion up to three attempts with five-second delays. `syswrapper.sh:4040-4178` uploads results once and logs failure; the Python VLM callback also has no local resend loop. Binary task-queue strings indicate SysV queues, stale-message dropping, task counts and worker pools, but exact binary scheduling, durability and retry semantics remain **needs_evidence**.

## 5. Excluded examples and next validation

`K/ui-uaibd-0.0.43.0-Linux.deb/.../database/cli/create.py` defines a `t1` table with name/age/place and dummy people. Its database model/examples are not the native search schema. `common/queue.py` is a time-graded telemetry queue, not the inference task queue. Neither supports claims about production search persistence.

Implement only the profile supported by the actual controller and firmware interface. Before device testing, establish its version, feature enablement, adoption/authentication contract, task dispatch, SQL migration requirements, and compatible model pair. A valid callback, persisted row, visible native description, positive search result, negative search result, and restart/retry behavior are separate acceptance checks. None has been demonstrated on a real device in this static investigation.
