# Vision providers

Choose one vision provider in `inference.provider`. The model name must be explicit. The worker sends its configured prompt and the downloaded camera images, or extracted video frames, to that provider. It makes no provider requests until it receives a job. A failed request does not trigger a different provider or model.

| Provider | API | Default base URL |
| --- | --- | --- |
| `openai` | Responses, `/v1/responses` | `https://api.openai.com/v1` |
| `anthropic` | Messages, `/v1/messages` | `https://api.anthropic.com/v1` |
| `ollama` | Native chat, `/api/chat` | `http://127.0.0.1:11434` |
| `openai-compatible` | Chat Completions, `/v1/chat/completions` | `http://127.0.0.1:11434/v1` |

The default provider is `openai-compatible`. Configuring a provider does not install a model or start a model server. Use a vision-capable model available on the selected backend. These adapters have passed local HTTP fixture tests; real provider access, model compatibility, latency, and description quality remain unverified.

## OpenAI

Merge this fragment into the generated configuration and replace the model placeholder. Put the API key in the private file referenced by `api_key_file`, never in a shared configuration or command argument.

```json
{
  "inference": {
    "provider": "openai",
    "base_url": "https://api.openai.com/v1",
    "model": "YOUR_VISION_MODEL",
    "api_key_file": "/state/openai-api-key",
    "allow_remote": true,
    "max_output_tokens": 1024
  }
}
```

OpenAI receives camera images and the prompt when a job runs. This adapter requires an API key and the official HTTPS endpoint. The only endpoint exception is a loopback fixture in explicit `runtime.mode="lab"`.

Requests use `input_text` and base64 `input_image` items. They set `store:false` and `stream:false`. The parser accepts completed assistant text and rejects errors, refusals, or incomplete responses. It skips reasoning and commentary. `store:false` controls response storage; it is not a claim about every provider retention policy. See [OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision) and the [Responses reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).

## Claude / Anthropic

Select `anthropic`, an explicit vision model ID, and a private Claude API key file. The official endpoint is `https://api.anthropic.com/v1`. The adapter sends base64 image blocks before the text prompt and uses the Messages API with `x-api-key` and `anthropic-version` headers. It accepts only an `end_turn` assistant message with nonempty text; refusals, truncation and tool output fail closed. See Anthropic's [vision guide](https://platform.claude.com/docs/en/build-with-claude/vision), [Messages API guide](https://platform.claude.com/docs/en/build-with-claude/working-with-messages), and [API key guide](https://platform.claude.com/docs/en/get-api-key).

The control site stores a new key in the profile's private state folder. It requires a Claude API key from the Claude Console; a Claude app or coding-plan entitlement is not an API credential. This adapter is for scene descriptions or coarse object observations. It does not establish face identification or calibrated bounding boxes. No live Claude request has been made in this project.

## Ollama

```json
{
  "inference": {
    "provider": "ollama",
    "base_url": "http://127.0.0.1:11434",
    "model": "YOUR_INSTALLED_VISION_MODEL",
    "max_output_tokens": 256,
    "temperature": 0
  }
}
```

Use the server root or `/api` as the base URL. Ollama receives base64 image strings in the message's `images` array, with `stream:false`. `max_output_tokens` maps to `options.num_predict`. A response must have `done:true` and nonempty `message.content`; truncated or tool-call output fails the job. See [Ollama vision](https://docs.ollama.com/capabilities/vision) and [native chat](https://docs.ollama.com/api/chat).

For an Ollama server at a different address, set that address explicitly. A non-loopback endpoint requires `allow_remote:true`; plain HTTP there also requires `allow_insecure_http:true`. Those flags permit the configured endpoint only. They do not start, discover, or install Ollama.

## Compatible servers

```json
{
  "inference": {
    "provider": "openai-compatible",
    "base_url": "http://127.0.0.1:1234/v1",
    "model": "YOUR_LOADED_VISION_MODEL",
    "max_output_tokens": 256,
    "temperature": 0
  }
}
```

Use this adapter for servers implementing vision Chat Completions, including compatible vLLM or LM Studio configurations. It appends `/chat/completions` to the base URL and sends image data URLs. It requires one completed text choice with `finish_reason="stop"`. API compatibility and the selected model's image support still need testing on that server.

## Shared behavior

`max_output_tokens` accepts integers from 1 through 32768. Defaults are 1024 for OpenAI and Claude and 256 for the other adapters. For reasoning models, this budget can also cover reasoning tokens. An incomplete result fails instead of becoming a partial caption. OpenAI gets no temperature parameter unless configured explicitly; Claude's adapter does not send one; the other adapters default to zero. A selected model may reject a configured parameter.

The worker's queue, deadline, image count, response size, and description length limits apply to all providers. HTTP errors and redirects fail the job without retry or fallback. Provider sessions never receive the Protect client certificate or device headers. Protect media and callback requests never receive the provider API key.

Changing the vision provider does not change the E5 embedding backend. Description and query embeddings retain their separate shared profile check. None of these adapters establishes native Protect adoption, callback persistence, or search acceptance.

## Independent AI Port plate-text role

For an API detector with an already explicit `plate_cameras` allowlist,
`live_pool_detector.ocr_provider_config` may select a separate vision adapter,
model, endpoint and server-side `api_key_file` for plate-only crops. It uses the
same supported vision adapter fields and existing approved-endpoint policy,
not a transcription or embedding API. No role key is inherited when an explicit
OCR selection omits its key. Inline keys and unscoped OCR configurations are
refused. The normal object prompt then does not ask the detection model for
plate text; only the separate OCR reply may supply it.

The crop still requires the selected camera, vehicle/crop/interval checks and
the detector's existing optional request budget. Selecting another role is not
a budget waiver. It may add a separate paid crop request instead of the old
combined object/plate reply; no automatic fallback to the detection model occurs
on OCR failure. Replies require one unambiguous `plate` string or null; invalid
or uncertain text never becomes an invented plate. The guarded transport,
redirect/TLS/DNS/reply/deadline rules are shared, but credentials are not.

Without this optional field, the existing combined detection/plate configuration
and request policy are retained. There is no automatic migration or new camera
permission. Existing provider forms preserve the separate OCR assignment. Its
configured model appears as its own role with revision-checked preview/save,
catalog and explicitly acknowledged one-use synthetic image test. These controls
do not create the initial OCR assignment or add plate cameras; broader setup UI
remains #17. Role undo requires both retained role keys and never changes native
identity/pairings. Saving a configuration does not activate it or prove plate
quality; native/night/glare/blur acceptance remains #19.
