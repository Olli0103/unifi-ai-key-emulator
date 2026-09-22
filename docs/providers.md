# Vision providers

Choose one vision provider in `inference.provider`. The model name must be explicit. The worker sends its configured prompt and the downloaded camera images, or extracted video frames, to that provider. It makes no provider requests until it receives a job. A failed request does not trigger a different provider or model.

| Provider | API | Default base URL |
| --- | --- | --- |
| `openai` | Responses, `/v1/responses` | `https://api.openai.com/v1` |
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

`max_output_tokens` accepts integers from 1 through 32768. Defaults are 1024 for OpenAI and 256 for the other adapters. For reasoning models, this budget can also cover reasoning tokens. An incomplete result fails instead of becoming a partial caption. OpenAI gets no temperature parameter unless configured explicitly; the other adapters default to zero. A selected model may reject a configured parameter.

The worker's queue, deadline, image count, response size, and description length limits apply to all providers. HTTP errors and redirects fail the job without retry or fallback. Provider sessions never receive the Protect client certificate or device headers. Protect media and callback requests never receive the provider API key.

Changing the vision provider does not change the E5 embedding backend. Description and query embeddings retain their separate shared profile check. None of these adapters establishes native Protect adoption, callback persistence, or search acceptance.
