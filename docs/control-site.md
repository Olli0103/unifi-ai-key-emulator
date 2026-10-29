# Local control site

The control site is a separate loopback HTTP service for the AI Key and one or more AI Port configuration files. It has administrator sign-in, a signed session, CSRF protection, revision-checked saves and write-only API-key replacement. It manages the vision provider, model and endpoint for AI Key descriptions and AI Port object detections. OpenAI, Claude/Anthropic, Ollama and an OpenAI-compatible endpoint are selectable. Each AI Port form also manages classes, score threshold and an optional API-request cost cap (empty means no cap) for that instance only. Object events have no per-camera cap, and a save keeps the local fallback, plate cameras and held-package mode. A configured AI Key speech role has its own provider form, described below. The site does not manage embeddings, audio camera opt-ins, face/plate models, adoption, camera pairing or service restarts yet. Those controls remain open work under [issue #17](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17).

## Data flow and retention

The signed-in [data-flow and retention page](data-retention.md) separately shows
all output/state categories, other destinations, product defaults and current
lifecycle mechanisms. It is read-only: unavailable counts are not zero, local
file permissions are not encryption, and deleting a local copy does not delete
Protect, model-server, host-log or backup copies.

## Speech provider

If `speech_to_text` already contains a valid explicit camera allowlist, the Speech provider form changes only that role's provider, model, API base URL, optional language, private-network permission and write-only key. The existing camera list and clip-length policy stay unchanged. No form is offered for an unconfigured role; this is not audio enrollment or permission management.

Choose OpenAI's official API or a local compatible speech server. Every OpenAI save requires the separate audio-sharing checkbox, including a model-only change through this form. Audio may incur provider charges independently of the caption budget. The form shows its destination; it does not send a connection test, audio or any provider request when saved. Local-network HTTP needs an explicit checkbox and remains unencrypted. Existing provider URL validation rejects arbitrary public compatible servers.

Speech keys are separate from vision keys. With an empty new-key field, the existing speech reference is kept only for the exact same provider and configured base URL. A changed destination drops it; OpenAI requires a new speech key if there is no matching prior reference. Key files remain private server-side files, never prefilled in HTML. Old files remain for rollback. The existing AI Key rollback restores the complete exact prior config only at the recorded revision, and refuses if a prior vision or speech key is missing. It does not test key validity, revoke cloud credentials or remove obsolete key files.

For every provider form (AI Key vision, AI Port detection and AI Key speech), an interrupted save is not a confirmed rollback. Once configuration apply was attempted, a generic failure retains the new private key file: the active config **or a later archived revision** may need it. Reload settings and check the recorded revision before retrying. Only a rejection before apply or a proven revision conflict deletes the unused newly written key. An uncertain failure may therefore leave an unused file; there is no automatic orphan-key pruning. Before manual cleanup, check active and archived configuration references, and separately revoke obsolete provider credentials where appropriate. No key value is included in the error page or audit record.

These settings take effect after the affected processor restarts. A successful save does not establish model compatibility, transcription quality, native persistence or a live deployment change.

## Model catalogs

Each configured editable API role has a **List available model IDs** action in
Models by role. The form shows the saved destination before the request. This
action is authenticated, same-origin/CSRF checked and bound to the configuration
revision and role. It accepts no endpoint or credential from the form. A stale
revision fails before contact; a configuration changed during the request makes
the result unavailable rather than presenting an old catalog as current.

The action makes only catalog GET requests with that role's configured key. It
sends no camera media or inference prompt, selects no model and changes no
settings. OpenAI/compatible APIs use `/models`, Anthropic uses its bounded
`after_id` pagination, and Ollama uses `/api/tags`. Unsupported, malformed,
oversized or incomplete catalogs fail without a partial list or fallback. Manual
model IDs remain available. At most one catalog request is in flight for the
site, with a five-second cooldown. The whole fetch has a 15-second deadline,
eight-page/512-model and 256-KiB response limits; redirects and response
decompression are disabled. Keys remain private files with the same documented
host-state/container-state mapping as provider saves.

Catalog availability is not tested modality, quality, cost or deployment
compatibility. The page separately labels provider-advertised image support and
Ollama remote markers when supplied. An omitted remote marker does not prove a
local model or zero upstream cost. No returned model is automatically selected,
and the site does not automatically validate or change an index/model space.

## Synthetic vision test

Configured caption and API-detection roles have a separate **Run one synthetic
vision test** action. It shows the saved model and destination and requires an
unchecked cost acknowledgement. The test sends one generated image containing
random characters, never camera media. Provider charges may apply separately
from camera caption/request caps. Saving settings or listing models never
starts this test. Speech and other role tests are not yet available.

The signed-in, same-origin/CSRF-checked form is bound to its profile, role,
revision and session with a 120-second single-use grant. Grants are consumed
before credential loading/contact; old forms fail after a restart. At most one
test runs at a time, with a 30-second cooldown and a bounded pending-form set.
The client makes one request, without redirects, cookies, environment proxies,
decompression, retries or fallback, with 20-second/64-KiB response bounds and a
128-output-token request limit. It uses only the selected role's saved key.

The page reports a match/mismatch and timing, not model text, keys or images.
One matching synthetic answer is not general modality, internal model identity,
recognition quality, deployment or billing evidence. No model is selected,
activated or changed. A settings change during the test makes its result stale.
Known preflight failures report that no request started; an unverified outcome
after a possible request is not automatically retried and may have been charged.
A new explicit test needs a new form and acknowledgement.

### Provider hostnames

Catalogs and the asynchronous AI Key model clients accept public DNS names only
when all resolved addresses are global unicast. A private DNS name is refused;
use an explicit ordinary LAN/VPN IP for a private backend, or `localhost` that
actually resolves only to loopback. Link-local metadata services and special
addresses are not supported model backends. IDNs use their ASCII A-label form.
Existing remote/plain-HTTP opt-ins remain necessary; this rule grants neither.
For HTTPS, the certificate must still cover the selected hostname or IP. Do not
disable verification to make an endpoint work. A private IP is not proof of
local inference or zero upstream charges. No live configuration was migrated
by adding this rule.

With optional pinned Protect inventory settings, the signed-in site has a read-only Cameras page. Each refresh fetches the current inventory, calculates the number of AI Port instances needed for connected legacy and G3–G5 targets, and shows which configured instance, if any, contains each camera MAC in its validated local stream allowlist. Exact known single-stream model maximums and conservative unknown-model reservations determine the count; an unknown camera source shows `needs_evidence` instead of inventing a slot. The count does not create device identities or pair cameras. An allowlisted camera is not necessarily paired or streaming in Protect. The page never enables processing or sends camera media. If Protect is unavailable or its version changes, it shows an error instead of stale camera data.

The site can run on the host while each processor runs in a container. Give it the host paths to the two private configuration files. It writes new key files into the corresponding host state folder with mode 600 and stores the processor-visible path in the config. AI Key reads its processor state path from `runtime.state_dir`; pass AI Port's processor path with `--aiport-runtime-state-dir`. Both processor mounts must expose the host state folders at those paths. Never expose the current loopback listener through a LAN port publish or reverse proxy; LAN administration needs authenticated HTTPS and a separate security review.

Initialize the administrator credential in a local terminal. The command prompts without echoing the password and refuses to overwrite an existing administrator:

```sh
local-aikey-control init --state-dir /private/admin-state
```

For unattended installation, add `--generate`. This writes a mode-600 `admin-bootstrap-password` file and prints only its path. Move the password into your password manager and delete the plaintext bootstrap file after signing in; keep the password record and signing key. No API key or administrator password is printed by the site.

Start the site after both profile configs exist:

```sh
local-aikey-control run --state-dir /private/admin-state \
  --aikey-config /private/aikey-state/config.json \
  --aiport-config /private/aiport-state/config.json \
  --aiport-instance nas-2=/private/nas-slot-2/config.json \
  --aiport-instance nas-3=/private/nas-slot-3/config.json \
  --aiport-runtime-state-dir /state --port 8765 \
  --inventory-controller CONSOLE_PRIVATE_IPV4 \
  --inventory-api-key-file PRIVATE_PROTECT_API_KEY_FILE \
  --inventory-web-trust-file PRIVATE_WEB_TRUST_JSON \
  --inventory-web-cert-file PRIVATE_PINNED_WEB_CERT
```

Open `http://127.0.0.1:8765/` on the same host. Each AI Port form appears when that instance's config has a `paired_streams` pool. `--aiport-instance NAME=CONFIG` can be repeated; names use lowercase letters, digits, underscores and hyphens. All instances use the `--aiport-runtime-state-dir` path inside their respective containers. Selecting OpenAI requires a new key or an existing key reference for the same provider and endpoint. A new key never returns to the browser. Saving replaces only the selected instance's provider settings, preserves its identity and paired streams, and displays that the affected service needs a restart. Restart through the deployment's service manager after checking the saved settings. The site does not restart, unpair or re-adopt cameras. A control site running on a different host edits only the config files actually mounted there; a staged Mac copy is not a live NAS config.

API-backed AI Port detection is still experimental. The motion gate, provider-failure backoff, object output validation and the optional durable request cap have synthetic tests, but real-camera object locations, score calibration and saved Protect events are `needs_evidence`. Do not treat a successful settings save as proof that the selected model is suitable or that Protect accepted its events. See [AI Port candidate](aiport-candidate.md) for the backend configuration and current native evidence.
