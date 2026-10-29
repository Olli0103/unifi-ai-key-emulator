# Role-specific model catalogs — 29 October 2026

Implementation/fixture progress for #8, #13 and #17. Parent issues remain open.

The signed-in control site can list the already configured caption, speech or
AI Port API-detection role's catalog. Endpoint and key come only from the
revision-checked saved role; the form accepts only profile, role, revision and
CSRF. The UI discloses the destination before contact. A list is never inference,
a model selection, a capability test or permission to send camera data.

Requests use a dedicated verified-TLS client with environment proxies and
cookies disabled, no controller identity, no redirects/retry/fallback and no
response decompression. The parser enforces aggregate size/model/page limits
and an overall deadline. Duplicate models, broken cursors, unimplemented
pagination and malformed envelopes fail closed instead of returning a partial
catalog. A provider reflecting the selected key as a model ID is rejected.
File references are server-side; non-private, missing, symbolic or special key
files fail before contact. A revision change during the read invalidates the
result. A site-wide in-flight guard and cooldown bound repeated operator reads.

Model IDs are escaped in HTML; unused upstream metadata, remote host URLs,
raw errors, key values and secret paths are not returned. Provider-advertised
image support is distinct from tested capability. Ollama remote markers are
displayed without treating their absence as local or unmetered proof.

## Official contracts consulted

- [Official OpenAI documentation: List models](https://developers.openai.com/api/reference/resources/models/methods/list):
  `GET /v1/models`, `data[].id`; list availability is not an image/audio test.
- [Claude: List Models](https://platform.claude.com/docs/en/api/models/list):
  `GET /v1/models`, `has_more`/`last_id`/`after_id` and optional advertised
  `capabilities.image_input.supported`.
- [Ollama: List models](https://docs.ollama.com/api/tags): `GET /api/tags`,
  `models[].model`/`name`, optional remote-model/host markers.

Retrieved 29 October 2026. No SDK/package, model, credentials or inference was
installed or invoked to query a real provider during this work. The existing
aiohttp dependency supplies the HTTP client; no extra library was needed.

Synthetic local HTTP tests exercise all four API adapters, role-key separation,
pagination/cookie isolation, limits, redirects, deadlines, reflected keys,
compressed replies, unsafe key files and revision/session/origin/CSRF failures.
HTTP/HTML checks are not visual/mobile acceptance; the earlier disposable
loopback browser navigation remained policy-blocked. Live provider access,
model-capability probing, arbitrary-host DNS policy, missing provider roles and
remaining native/UI acceptance are not claimed.
