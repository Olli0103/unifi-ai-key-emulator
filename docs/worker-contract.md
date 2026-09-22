# AI media worker

`aikey.worker.JobProcessor` runs the media, inference, and callback part of an AI job. It is independent Python code. It never imports or executes Ubiquiti firmware and contains no inference fixtures. Synthetic model responses exist only in tests and the explicit local lab.

The worker needs an explicitly configured vision model endpoint. It sends real images to that endpoint and uses the returned text. No description is substituted when inference fails. Native Protect acceptance and search indexing remain untested until an adopted instance runs against the target controller.

## Interface

```python
processor = JobProcessor(config, state_dir, ssl_context=controller_tls)
await processor.start()
admission = await processor.submit(request_ai_body)
await processor.wait_for_idle()
status = processor.get_status(admission["jobId"])
await processor.stop()
```

`submit()` validates the entire job and reserves a bounded queue slot before returning. It returns `accepted`, `jobId`, and `duplicate`. Device control handling can acknowledge admission without waiting for inference. `handle()` accepts the same input and waits for completion, raising `WorkerError` on failure. `status()` exposes queued, active, pending, and queue-capacity counts. `stop()` cancels queued and active work and closes sessions.

Each input is a RequestAI body with `targetUri`, `payload`, `resUrl`, and optional `timeoutMs`. Only `:7968/describe` and `:7968/on_demand_inference` are implemented. The worker never executes a supplied port, path, command, or local file reference. The command deadline includes queue waiting, downloads, inference, and callback.

For a bounded native trial, [one-camera, one-job test scope](scoped-camera-test.md) adds an explicit camera ID, strict export-query binding, and a durable single-use permit. It is opt-in and does not change existing unscoped configurations.

## Separate result contracts

| Job | Required input | Result callback |
| --- | --- | --- |
| On-demand vision | `cameraId`, `eventId`, `timestamp` in milliseconds, `videoUrl` | JSON `{"description": "model output"}` at the supplied `/internal/camera-upload/<token>` route. Before the deadline, a processing error sends JSON `{"error": "reason"}`. |
| Task description | `camera`, `event`, and exactly one nonempty `images` or `videos` array. Each item contains `reqUrl`. Optional `pass` is preserved. | JSON with `camera`, `event`, `description`, model identifier, optional `pass`, and optional `descEmbedding`, at `/internal/aiprocessors/descriptions/<taskId>`. |
| Explicit legacy adapter | The same media input accepted by `/describe`, with the legacy callback URL and an explicitly selected source profile. | Multipart with one `application/json` part named `ram`, at `/internal/aiprocessors/recognize-anything`. |

The legacy adapter does not recreate firmware `/vlm_inference` or its vendor-generated local files. For `worker.legacy_profile="key-2.2.8"`, its result has `eventId`, `status="success"`, and `description`. The inspected Protect 7.2.105 receiver requires `cameraId` too, so `worker.legacy_profile="protect-7.2.105"` includes the input camera identity. These are explicit version profiles, not a claim that the older device output passes the newer receiver.

The new task callback preserves the controller's task ID in the callback route. It does not create tasks or alter controller task ownership. `callback="http_accepted"` means the callback returned HTTP 2xx. It does not prove the controller stored, indexed, or displayed the description.

The worker uses one conservative description prompt. It currently does not reproduce vendor `promptProfile` behavior, VLM model preprocessing, labels, face recognition, license-plate recognition, event reverification, or detection embeddings. A video input contributes one decoded frame. For on-demand jobs this is the requested timestamp; for task video inputs it is the first frame. This limits action descriptions and is not full-video understanding.

## Media and transport limits

Controller origins must be configured explicitly. Relative media/callback URLs resolve against `controller_media_origin`, or the first configured origin. Only known internal image, snapshot, video-export, and callback paths are accepted. Credential-bearing URLs, fragments, traversal encodings, and unknown origins are rejected. Redirects are never followed, including redirects on an allowed origin.

Controller connections require TLS certificate verification. A configured SHA-256 pin is checked before HTTP headers are sent. Disabling hostname verification requires an explicit pin in addition to certificate verification. The caller supplies a context with the emulator's client certificate and trusted controller certificate. Plain HTTP is limited to loopback in `runtime.mode="lab"`.

The inference session is separate and never receives the device client certificate or device headers. The inference base URL and model must both be configured. Its endpoint is `<base_url>/chat/completions`, using OpenAI-compatible image data URLs. Non-loopback inference requires `inference.allow_remote=true`; non-loopback plain HTTP additionally requires `inference.allow_insecure_http=true`. This supports an explicitly chosen local NAS model service without implicitly allowing external model services.

Supported images are JPEG, PNG, and WebP, selected by file signature. Video extraction requires an explicitly configured absolute `worker.ffmpeg_path`. It uses fixed arguments, an MP4 demuxer, local-file/pipe protocols only, and no shell. Downloaded media and extracted frames are bounded by configured byte limits. Temporary video/frame files are removed after processing or cancellation.

Raw UBV is not decoded. Optional `worker.request_mp4_exports=true` adapts an unsigned, known `/internal/aiprocessors/video/export` URL from literal `format=ubv` to `format=mp4`. It preserves every other raw query component. The adapter requires a valid start/end interval no longer than one hour, rejects repeated or unknown query fields, and does not alter signed/authenticated query URLs or other paths. The response must still have an MP4 signature. This adaptation is supported by the inspected controller export code, but it has not been tested against Protect 7.3.56.

## Configuration

| Setting | Default or requirement |
| --- | --- |
| `controller_origins` | Required nonempty list of explicitly permitted origins. |
| `controller_media_origin` | Defaults to first origin. Prefer the configured controller media port. |
| `device.mac` | Required. Used only as the emulator's own device identity header. |
| `inference.base_url`, `inference.model` | Required. No model is selected or downloaded implicitly. |
| `inference.api_key` | Optional hydrated secret. Keep persistent secrets in the root configuration's supported secret file. |
| `worker.max_queue`, `worker.max_concurrency` | 8 waiting jobs, 1 active job. |
| `worker.timeout_s` | 120 seconds, further capped by RequestAI `timeoutMs`. |
| `worker.max_media_bytes`, `worker.max_video_bytes` | 10 MiB per image and 100 MiB per video. |
| `worker.max_images` | 4 media inputs. |
| `worker.max_description_chars` | 8192. Truncated/incomplete model responses are rejected. |
| `worker.ffmpeg_path` | Required for video jobs. |
| `worker.request_mp4_exports` | False unless explicitly enabled. |
| `worker.callback_mode` | `enabled`. `disabled` performs inference but makes no callback and reports that explicitly. |
| `worker.legacy_profile` | Required for legacy callbacks; no implicit profile in the worker. |
| `worker.description_embeddings` | False. When true, the configured `aikey.search.EmbeddingService` produces document embeddings. Missing or failed embeddings fail the job. |
| `worker.max_ledger_entries` | 1024. The journal stops admitting new identities when full rather than silently losing deduplication history. |

Vision inference supports OpenAI Responses, native Ollama, and vision-capable OpenAI-compatible Chat Completions servers. See [provider configuration](providers.md) for request formats, credentials, and examples. Provider selection is explicit and failures never trigger a fallback.

Optional description embeddings use the same E5 backend as query embeddings. Configuring a backend does not establish that the controller's native search uses these records correctly. Disabling embeddings omits `descEmbedding`; it does not fabricate a vector.

Enabling document embeddings records the encoder identity in `search-profile.json` before the worker opens connections, even if query search is disabled. Query startup checks the same file. Changes to the configured source, model, revision, or preprocessing fail startup until the existing index is reconciled. Invalid or unreadable state is not repaired automatically. The file records configuration, so an operator must also keep the model weights behind a configured endpoint stable.

## Deduplication and recovery

In-flight jobs sharing a task/callback identity share one computation. Reuse of an identity with different input is rejected. Successful callback results persist in private files under `<state_dir>/worker-jobs`; subsequent identical requests, including after restart, return the stored result without another callback.

Before sending a callback, the worker records `callback_sending`. A timeout, connection failure, rejection, or interrupted callback becomes `callback_uncertain`. On restart, either state blocks automatic replay. This is conservative because HTTP failure can occur after the controller processed a request. It is not an exactly-once delivery guarantee. Review the controller outcome and journal before deciding whether to retry an uncertain job. Journal entries include descriptions and are local camera metadata, so retain and back them up accordingly.

`callback_mode="disabled"` also stores completion. Turning callbacks on later does not automatically send a previously processed job. Use a new controller task, or review and deliberately reset the relevant local journal entry. Entries are not deleted automatically.

## Evidence and verification

The original worker input and legacy callback shapes were read from official AI Key 2.2.8 firmware:

- `ui-vlm-agent-0.0.11.1-Linux.deb/data/usr/local/bin/vlmenv/app.py`, lines 145–284: on-demand input, timestamped media flow, and success/error callback JSON.
- The same file, lines 378–400: legacy description JSON and multipart `ram` upload.
- `ui-websocketd-0.1.47.1-Linux.deb/data/usr/local/bin/syswrapper.sh`, lines 3907–4003: RequestAI worker forwarding and callback URL propagation.

Controller evidence comes from statically extracted Protect 7.2.105 `service.js` modules:

- `42220`: `:7968/describe` task dispatch, payload identity, media arrays, and task callback route.
- `68823`: description callback schema, internal authentication, and video export query forwarding.
- `13944`: legacy description receiver requires `cameraId`.
- `76776`: on-demand video export uses UBV and a timestamp-centered interval.
- `1924`: footage format strings `mp4` and `ubv`.
- `42220`: speech-to-text requests the same AI processor video export endpoint with MP4 format.

This source evidence identifies interfaces; it does not prove runtime interoperability. In particular, newer Protect 7.3.56 behavior still needs live evidence.

Run `python -m pytest tests/test_worker.py -q` from the project virtual environment. Tests use actual loopback HTTP/TLS servers, synthetic model responses, generated test certificates, and synthetic video decoded by real ffmpeg when available. They verify queue admission and limits, deduplication across restart, uncertain-callback handling, origin/path restrictions, redirect rejection, byte/time limits, model failures, exact callbacks, optional embedding integration, and certificate pin checks before device headers are transmitted. No live controller or external model is contacted.
