# Local control site

For a credentials-free, read-only preview with invented data, use the separate
[synthetic demo](synthetic-demo.md). It is not an administration or device server
and cannot read deployment state or send a provider request.

The control site is a separate loopback HTTP service for the AI Key and optional AI Port configuration files. It has administrator sign-in, a signed session, CSRF protection, revision-checked saves and write-only API-key replacement. It manages the vision provider, model and endpoint for AI Key descriptions and AI Port object detections. OpenAI, Claude/Anthropic, Ollama and an OpenAI-compatible endpoint are selectable. Each AI Port form also manages classes, score threshold and an optional API-request cost cap (empty means no cap) for that instance only. Object events have no per-camera cap, and a save keeps the local fallback, plate cameras, held-package mode, existing per-camera score gates and existing local score-server routing. Provider/model edits preserve unrelated raw camera spellings and omitted defaults; these forms do not add or retarget per-camera gates or a score server. A configured AI Key speech role has its own provider form, described below. Initial AI Key audio opt-in is available through the separate explicit speech setup below. The site does not manage embeddings, face model enrollment, adoption, camera pairing or service restarts yet. Those controls remain open work under [issue #17](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17).

For a new AI Key, the separate [authenticated first-run mode](first-run.md#authenticated-browser-preparation)
prepares only a new directory and pinned public trust, without network access,
device startup or adoption. It does not reopen an existing profile for setup.

## Navigation and accessibility status

The administrative renderer supplies one main landmark and a keyboard skip link
to it, an explicit visible focus outline, and normal keyboard tab order. The
synthetic demo keeps its own main landmark instead of nesting another one.
The model-role table has a named, keyboard-focusable horizontal scroll region
and column headers. Grid cards may shrink below 340 pixels; header actions wrap
instead of requiring a fixed desktop width. Catalog/retention scroll containers
share the same overflow rule.

Static tests inspect actual signed-in HTML for unique IDs, one main/skip target,
named inputs/selects, no positive tabindex and no provider request/config change.
Declared text colors are checked using the WCAG sRGB relative-luminance formula
for 4.5:1 normal text and 3:1 input boundaries/focus against the declared light
surfaces. These are fixed-palette checks, not observed computed styles, every
hover/disabled/error state or a WCAG conformance certificate. Native control
widgets, forced colors, zoom/reflow, real mobile browsers and keyboard/screen
reader use still require direct acceptance. No browser test is claimed from
these source/HTML checks.

English remains the implemented page language. Further localization and the
complete first-run/feature-control workflow remain open.

## Search index and backup status

The Search index migration page reports historical scratch-restore
records and live-row comparisons. Damaged/unsafe newest metadata blocks backup
readiness instead of silently falling back to an older record. It neither
rehashes nor restores a dump during page loads; re-verify before recovery.
See the [backup-status boundary and limits](evidence/index-backup-status-20261029.md).

### Cancel an idle staged migration

Viewing `/index-migration` remains read-only. A separate confirmation form can
retire an existing `staging`, `staged` or `verified` generation while its original
encoder is still live. The form binds the exact journal and live-profile bytes;
changed progress, a changed profile or an active writer refuses the request.
It requires the normal login, same-origin/CSRF checks and shared mutation limit.

Cancellation changes only that generation's journal to `cancelled`. It does not
contact a model/database, change live vectors or the encoder profile, delete
staging data, stop a running writer, cut over or roll back an index. A running
stage must finish or be stopped separately before cancellation. A generation
already made live needs the explicit rollback workflow, not this form. A
post-commit filesystem failure is an uncertain outcome: reload and reconcile;
the site never retries or restores automatically.

All library stage/verify/cancel/cutover/rollback calls share one private writer
lock per configured state directory and refresh durable state after acquiring
it. Do not delete/replace that lock file or manually edit migration state while
a writer is active. Cancelled generations cannot resume, verify or cut over;
their retained data is not reported as an active migration. Real stored-object
source approval, native cutover/readback and retrieval quality remain separate
gates. See [generation authority and cancellation](evidence/index-generation-cancellation-20261030.md).

## Data flow and retention

The private, versioned [administration JSON adapter](admin-api.md) reuses this
listener's current session, Host/Origin/CSRF checks and shared mutation window.
Its configuration responses are private operator data, not a public diagnostic
export. It currently provides saved profile/configuration/role views and Key
preview/apply/rollback for Key settings and bounded Port feature policy. Native
identity/pairing/host edits remain unavailable. Host-published one-use maintenance
plans can be submitted through the constrained bridge; see the maintenance console
below. This is not arbitrary host execution or automatic worker startup.

The signed-in [data-flow and retention page](data-retention.md) separately shows
all output/state categories, other destinations, product defaults and current
lifecycle mechanisms. It is read-only: unavailable counts are not zero, local
file permissions are not encryption, and deleting a local copy does not delete
Protect, model-server, host-log or backup copies.

## Speech provider

If `speech_to_text` already contains a valid explicit camera allowlist, the Speech provider form changes only that role's provider, model, API base URL, optional language, private-network permission and write-only key. The existing camera list and clip-length policy stay unchanged. No provider form is offered for an unconfigured role. For an absent speech role, the separate initial audio setup below requires fresh camera selection and consent. Existing roles retain the remove-only permission controls described below.

Choose OpenAI's official API or a local compatible speech server. Every OpenAI save requires the separate audio-sharing checkbox, including a model-only change through this form. Audio may incur provider charges independently of the caption budget. The form shows its destination; it does not send a connection test, audio or any provider request when saved. Local-network HTTP needs an explicit checkbox and remains unencrypted. Existing provider URL validation rejects arbitrary public compatible servers.

Speech keys are separate from vision keys. With an empty new-key field, the existing speech reference is kept only for the exact same provider and configured base URL. A changed destination drops it; OpenAI requires a new speech key if there is no matching prior reference. Key files remain private server-side files, never prefilled in HTML. Old files remain for rollback. The existing AI Key rollback restores the complete exact prior config only at the recorded revision, and refuses if a prior vision or speech key is missing. It does not test key validity, revoke cloud credentials or remove obsolete key files.

For every provider form (AI Key vision, AI Port detection and AI Key speech), an interrupted save is not a confirmed rollback. Once configuration apply was attempted, a generic failure retains the new private key file: the active config **or a later archived revision** may need it. Reload settings and check the recorded revision before retrying. Only a rejection before apply or a proven revision conflict deletes the unused newly written key. An uncertain failure may therefore leave an unused file; there is no automatic orphan-key pruning. Before manual cleanup, check active and archived configuration references, and separately revoke obsolete provider credentials where appropriate. No key value is included in the error page or audit record.

These settings take effect after the affected processor restarts. A successful save does not establish model compatibility, transcription quality, native persistence or a live deployment change.

## Remove saved audio permissions

For an already configured speech role, **Remove saved audio permissions** opens
a separate signed-in page. Uncheck cameras to remove their saved permission;
unchecking all removes the saved speech role. This operation cannot add cameras,
change the provider/model/clip policy or alter another feature's permissions.
Existing camera names are shown when inventory is available. If names cannot be
loaded, disabling all saved speech remains available; individual selection is
not presented with ambiguous camera labels.

**This edits saved configuration, not the running worker.** The page requires
acknowledgement that the AI Key must be restarted to change active processing.
It does not stop or restart a service, cancel in-flight work, delete recordings
or transcripts, clean backups, remove key files or revoke provider credentials.
No audio or inference request is sent by loading or saving it; name lookup may
read the configured Protect inventory. Initial audio enrollment uses the separate setup below; this remove-only view never grants permission.

Selection values are opaque and bound to the signed-in session/configuration
revision. Raw camera IDs are not put into selection values or URLs/audit entries.
Unknown, repeated, cross-session or stale selections fail. A no-op preserves
the configuration bytes. Existing private history/keys remain available for
recovery; an old provider Undo is invalidated by a permission revision and cannot
silently restore removed scope. A later provider-only Undo preserves that reduced
scope. An uncertain save is reported as uncertain: reload before retrying.

These are source/HTTP integration results, not native live revocation, browser
visual/mobile acceptance or a deployed permission change. See [checks and limits](evidence/speech-permission-restrictions-20261029.md).

## Separate AI Port plate OCR provider

`/ocr-provider` configures a separate OCR provider, model, destination and
write-only credential without editing JSON. It is offered only for a saved
API-based Port detector with an existing nonempty `plate_cameras` permission;
this page cannot enroll cameras or change plate scope, detector/fallback,
thresholds, request allowance, identity or pairing. A Port without that scope
gets an explicit unavailable explanation, not an activation button.

The first POST validates a preview but creates no credential/configuration file
and makes no network or model call. The confirmation shows the OCR destination
and model; a new credential is entered only there, never echoed or put in hidden
fields. The confirmation is bound to the current login, profile, exact current
and proposed revisions, complete selection and a ten-minute server-private
signature. A service restart invalidates outstanding confirmations. It needs
the normal same-origin/CSRF checks, shared mutation limit and an explicit
acknowledgement before the saved change.

A first separate assignment never inherits the detector's credential. An
existing explicit OCR credential is retained only for the exact same provider
and configured base URL. A changed destination drops it; OpenAI/Anthropic need
a separate supplied OCR credential. New private files are created exclusively
and synced before configuration apply, so a repeated concurrent confirmation
cannot overwrite the first writer's credential. Stale requests fail before key
creation. A possible committed write is reported uncertain; its credential is
retained for active/history references, not automatically deleted or retried.

The existing one-step per-Port provider Undo restores the exact prior config
when its revision and required previous credentials still match. It cannot
discard a later scope/settings revision, and it does not prune new keys. A
no-op retains the original bytes.

This updates saved configuration only. The selected Port needs a separate
controlled restart for runtime activation. OCR uses the detector's existing
per-camera request allowance; no configured cap means no cap. Capability,
native plate persistence and recognition accuracy are not proved by a save.
The maintained isolated browser check covers preview, stale refusal, explicit
keyboard confirmation, separate-key save and exact Undo on desktop/mobile.

## Model catalogs

The authenticated [saved model-role registry](model-roles.md) at
`/model-registry` covers fixed and external-service roles as well as editable
API models. It reports saved assignments and versioned selection IDs, not active
runtime status or checked model capability/weights. It reads no referenced key
or model file and sends no backend request. Unreported server-managed models
stay unknown. Separate camera/enrollment/migration procedures are not offered
as available generic model-edit actions.

### Preview a role-model change

Editable caption, configured speech and API-detection models use **Preview model
change** before the normal confirmation form. The preview reports the old/new
model, unchanged provider/key/permissions and search index, and whether a
controlled processor restart is needed. Current jobs keep the current runtime;
the preview neither drains/cancels jobs nor restarts or hot-swaps a processor.
The unchanged-model case adds no new restart requirement; it does not prove
that a prior saved change is already active. Search encoders
remain non-editable here and require the separate migration flow.

Preview reads and validates only: it does not load a provider key, create history
or undo records, contact a model/catalog, prove model capability or activate the
selection. It does not turn a catalog/manual model ID into verified modality or
versioned weights. Confirmation keeps the same revision and the existing
write/uncertain-outcome rules; an intervening save is refused rather than
overwritten. The revision-checked apply API remains available for explicit
clients, not as proof that a human viewed a preview. Both POST endpoints require
the normal session/origin/CSRF and shared operation-rate checks. The
confirmation form is withheld if the session expires or is revoked during the
read-only preview.

This is source/HTTP evidence; native browser/mobile use and the remaining
complete role registry/capability requirements stay open in #8/#17.

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

## Synthetic provider tests

Configured caption and API-detection roles have a separate **Run one synthetic
vision test** action. It shows the saved model and destination and requires an
unchecked cost acknowledgement. The test sends one generated image containing
random characters, never camera media. Provider charges may apply separately
from camera caption/request caps. Saving settings or listing models never
starts this test.

An already configured AI Key speech role also offers **Run one synthetic speech
test**. It uploads exactly one generated second of silent mono 16-bit/16-kHz WAV
audio through that role's saved transcription adapter, endpoint and key. No
recorded audio, speech, camera permission or test identity is required or enabled.
The form discloses that media/destination/model and requires the same separate,
unchecked cost acknowledgement. It does not start automatically or change audio
scope/provider/model settings. Other role tests remain unavailable.

The signed-in, same-origin/CSRF-checked form is bound to its profile, role,
revision and session with a 120-second single-use grant. Grants are consumed
before credential loading/contact; old forms fail after a restart. At most one
test runs at a time, with a 30-second cooldown and a bounded pending-form set.
The client makes one request, without redirects, cookies, environment proxies,
decompression, retries or fallback, with 20-second/64-KiB response bounds and a
128-output-token request limit for vision. Speech uses the existing adapter's
verbose-JSON segment settings for the one-second input; it has the same response
byte/deadline bound, not a claimed provider output-token or billing cap. Both use
only the selected role's saved key.

The page reports a match/mismatch and timing, not model text, keys or images.
One matching synthetic answer is not general modality, internal model identity,
recognition quality, deployment or billing evidence. No model is selected,
activated or changed. A settings change during the test makes its result stale.
Known preflight failures report that no request started; an unverified outcome
after a possible request is not automatically retried and may have been charged.
A new explicit test needs a new form and acknowledgement.

The speech page reports only whether the API returned no **parsed speech** for
the generated silence, plus elapsed time. A nonempty parsed response is a
silence mismatch; its transcript is never displayed, logged or stored by this
probe. Empty output is not proof of speech recognition or model quality. A
silent fixture cannot verify accent/language/noise handling, native transcript
persistence, internal model identity, provider retention or charges. A shared
one-at-a-time/cooldown gate covers vision and speech. No automatic retry,
fallback or activation follows a mismatch, error or timeout.

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

The site can run on the host while each processor runs in a container. Give it the host path to the private AI Key configuration and any existing AI Port configurations you want to manage. It writes new key files into the corresponding host state folder with mode 600 and stores the processor-visible path in the config. AI Key reads its processor state path from `runtime.state_dir`; pass AI Port's processor path with `--aiport-runtime-state-dir`. Each configured processor mount must expose its host state folder at that path. The default listener stays on loopback HTTP. Do not publish or reverse-proxy that plaintext listener onto a LAN. For explicit direct TLS, use the HTTPS mode below; forwarding headers are not trusted.

The read-only data-retention page checks at most 2,048 archive/reference entries
and a 250-ms monotonic budget between local file operations. A large archive is
shown as **incomplete**, with marker/problem totals unknown; a partial count is
not a total or an expiry/deletion authorization. This is a cooperative scan bound,
not a hard deadline for a stalled filesystem syscall. The separate archive CLI
still performs a full inventory by default, and the expiry planner refuses an
explicitly incomplete inventory. No replay marker is deleted by either status
read. See the [scan boundary](evidence/retention-scan-budget-20261030.md).

Initialize the administrator credential in a local terminal. The command prompts without echoing the password and refuses to overwrite an existing administrator:

```sh
local-aikey-control init --state-dir /private/admin-state
```

For unattended installation, add `--generate`. This writes a mode-600 `admin-bootstrap-password` file and prints only its path. Move the password into your password manager and delete the plaintext bootstrap file after signing in; keep the password record and signing key. No API key or administrator password is printed by the site.

For an AI-Key-only installation, no placeholder AI Port config or device is needed:

```sh
local-aikey-control run --state-dir /private/admin-state \
  --aikey-config /private/aikey-state/config.json --port 8765
```

This offers AI Key provider/model settings, rollback and read-only maintenance
pages. It does not create an AI Port directory, identity, pairing or service.
The same login, Host/origin, CSRF, private-file and revision checks apply.

To attach existing AI Port profiles, add `--aiport-config` for the default slot
and/or repeated `--aiport-instance NAME=CONFIG` for named slots. Named slots do
not require a default slot. A supplied missing/invalid config remains an error;
it is never silently treated as an absent profile. For example:

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

## Session lifetime and logout

Sign-in sessions belong to the current control-site server instance. **Sign in
again after restarting the site**, even if its private signing-key file is
unchanged. Logout revokes that session server-side as well as expiring its browser
cookie; a copied old cookie cannot authorize later reads or saves. Other signed-in
sessions remain valid. Rejected Origin/CSRF logout requests do not revoke a session.

The server keeps at most 128 current sessions by default, with an expiry-bound,
in-memory registry. A full registry refuses a new login without evicting another
valid session; sign out an existing session or wait for expiry. This is distinct
from a wrong password. Observed expiry/revocation cannot be reversed by setting
the wall clock back. Persistent administrator credentials and device/provider
keys are unchanged.

Logout does not cancel an already admitted save, provider test or processor job.
Reconcile any in-flight operation's result separately. These source/HTTP tests
are not a deployed service change or full security-audit approval. See the
[session-lifecycle checks](evidence/admin-session-lifecycle-20261029.md).

## Administrative request admission

After authentication/origin/CSRF checks, at most 60 mutating requests are admitted
per rolling minute across all signed-in sessions. Rejected forms count too;
logging in again does not bypass the limit. HTTP 429 tells you when to retry;
that refused request has not changed configuration, written a replacement key or
contacted a provider. Views and valid sign-out remain available. The first-run
wizard shares this rule. It is an in-process control-site limit, not the durable
caption/cost budget or a change to running camera processing.

## Embedding configuration admission

Configuration preview/apply validates supported embedding backends and the E5
model identity before saving, including local and disabled backends. Enabled
`e5-session-v1` search requires an HTTP or sentence-transformers encoder; an
omitted backend has the runtime's disabled meaning. Disabled search and CLIP
search can keep E5 disabled. Invalid changes preserve the previous configuration
and do not create a history entry. This validation does not contact a provider,
load/download/check the presence of a checkpoint, verify encoder dimensions or
test inference. Existing index-migration and credential-destination guards still
apply; this does not add an embedding-provider form or activate a new model.

For an existing CLIP index, unrelated saves and provider undo compare the
configured CLIP identity with its stored profile, rather than comparing it to
E5. Valid pinned revisions are compared without contacting the encoder or
rewriting the profile; legacy profiles without a revision stay unpinned. A
different CLIP server, primary search profile or E5 encoder identity still
requires migration, even when search is temporarily disabled. A corrupt profile
or malformed revision remains blocked. These checks do not rebuild an index,
validate live encoder weights or activate a changed configuration.

## Explicit direct HTTPS

Mac and Linux can also serve the same authenticated site over direct TLS.
This is opt-in, not an automatic LAN bind or a reverse-proxy mode. Reuse a
maintained certificate authority/certificate tool you already operate; no new
certificate service, DNS entry or trust exception is installed by this command.
Use a **separate administrator** certificate/key, not the adopted device identity.
The browser must trust the certificate chain and its SAN must cover the exact
origin host. Never disable browser/client certificate validation.

```sh
local-aikey-control run --state-dir /private/admin-state \
  --aikey-config /private/aikey-state/config.json \
  --bind 192.168.10.20 --port 8765 \
  --origin https://processor-admin.example:8765 \
  --tls-certificate /private/admin-tls/server-chain.pem \
  --tls-private-key /private/admin-tls/server-key.pem
```

`--bind` is one literal ordinary private/VPN or loopback address; wildcard,
public, link-local and mapped addresses are refused. Non-default binding requires
HTTPS. Origin/listener ports must agree; incomplete TLS flags, credentialed or
path-bearing origins, missing/mismatched certificates and encrypted keys fail
closed before listening. There is no plaintext fallback or automatic key
creation/replacement. Certificate and key must both be owned regular files,
mode 0600 with no final symlink. OpenSSL loads the checked open descriptors via
`/dev/fd` on the supported Mac/Linux hosts, without a temporary private-key copy
or an interactive password prompt. TLS 1.2 is the minimum.

HTTPS uses a distinct `__Host-aikey_admin` cookie: Secure, HttpOnly, SameSite
Strict, Path `/`, no Domain. The HTTP loopback cookie is not used there. The
actual transport scheme, exact Host, configured Origin, session and CSRF are
checked; `Forwarded` and `X-Forwarded-*` do not change that decision or the login
rate-limit actor. Logout expires the same secure host-only cookie. Configuration
revisions, role-separated keys and identity-preserving rollback are unchanged.

The default `http://127.0.0.1:8765` flow remains available for local use. No LAN
port, firewall, DNS, existing certificate, service or deployment is modified by
adding support in source. Tests use synthetic identities and a trusted loopback
TLS client; they are not a native LAN/browser, accessibility, penetration-test
or release-clearance result. Certificate issuance/renewal and the administrator's
network reachability policy remain deployment responsibilities.

### Locally reloading an administrator certificate

The Unix CLI (`run` and `setup`) installs a local `SIGHUP` handler **only in
explicit HTTPS mode**. Wait for the startup JSON marker
`{"admin_tls_reload_protocol": 1}` before using that operation. Older versions
without this marker do not support it: sending them HUP can stop the service.
Embedded applications do not install process signal handlers. There is no
HTTP certificate-control endpoint, watcher, issuance command or renewal timer.

After your existing certificate tool has staged and checked a new certificate
and matching key, replace the **same configured private files** and signal only
the confirmed CLI process. Coordinate file replacement and local signals with
other maintenance; the server does not make two separate file replacements
transactional. It loads the complete candidate pair under the same ownership,
regular-file, permissions, size, no-final-symlink and descriptor checks as
startup, then publishes one context for future TLS handshakes. No file read is
triggered by a client handshake or its SNI hostname. Concurrent local reloads
are serialized. The configured origin, listener, administrator authority,
active connections and session cookies do not change.

Success emits fixed JSON fields `admin_tls_reload: "ready"`, the new process-local
`generation` and `audit_recorded`; a refused file/key load emits `"refused"`
without advancing the generation. The `tls_reload` audit action records the
same fixed result without certificate contents or input paths. Audit failure
is reported separately and does not undo an already loaded TLS context.
A missing, public, symlinked, special, oversized, encrypted or mismatched input
leaves the previous context serving new connections too. Retain the prior pair
for file rollback: a failed reload does not repair the files on disk and a
later restart will validate them again. Stop requests suppress subsequent HUP
callbacks; the CLI removes its own HUP handler during cleanup.

Loading a pair is not certificate-authority, hostname, validity-period or
renewal qualification. Clients must still verify the chain and exact hostname;
the operator's certificate tool must check those properties before publication.
This mechanism does not change trust stores, issue a certificate, prove a future
renewal or authorize adopted-device identity/key replacement.

## Dated native administration evidence

The [policy-preservation checkpoint](evidence/provider-policy-preservation-20261030.md)
records the provider/model save regression fix, real-profile read-only previews,
admin-only installation and coordinated certificate-timer pin update. No native
provider/policy save or model call was made to qualify it.

The [native TLS-context reload checkpoint](evidence/native-admin-tls-reload-20261030.md)
records an admin-only update followed by same-certificate HUP with unchanged
process/session/core containers. It is not an issuer-renewal or automatic
certificate-maintenance result.

The separate [NAS certificate-maintenance checkpoint](evidence/native-admin-certificate-maintenance-20261030.md)
records a guarded user timer and its initial `not_due` result, without claiming
an actual issuer call, new-certificate rotation or future unattended renewal.

The [30 October NAS loopback qualification](evidence/native-admin-loopback-20261030.md)
records a separate authenticated service reading the actual Key/four-Port
profiles, five maintenance-page responses, explicit refusal cases, one admin-only
restart and unchanged native device/pairing baseline. The later
[private-VPN HTTPS checkpoint](evidence/native-admin-private-https-20261030.md)
supersedes that installation's loopback-only transport: default-trusted TLS,
authenticated readback and private-interface-only binding were verified on the
NAS. Remote-client/browser/mobile acceptance and automated renewal remain open,
as do real apply/rollback, host-reboot and sustained-operation qualification.
The subsequent [captured-byte reader rollout](evidence/port-config-snapshot-20261030.md#integrated-checks-and-native-administrator-rollout)
updates only that separate administrator image, retaining HTTPS and processor
state; it preserves the failed first attempt and recovery as well as the final
readback. No live permission or model apply is implied.
The [native Kuma checkpoint](evidence/native-admin-monitoring-20261030.md) adds
direct strict-TLS/JSON-identity and existing expiry-warning observation, without
a new host polling timer. Browser, renewal and shared-host recovery limits remain.
This does not change the default deployment
or enable an administration service merely by upgrading a processor image.

## Removing saved AI Port recognition permissions

For attached Port profiles, **Remove saved AI Port face, sound or speech permissions**
opens a remove-only page. It can shrink an already configured face-recognition,
sound-classification or audio/speech-presence camera list, or disable that saved role. It cannot enroll
a camera, enable an absent role, change its model or provider, delete evidence or
revoke a key. Individual selection requires unambiguous camera names from the
configured pinned inventory; during an inventory outage, disabling the whole
saved role still works. Opaque selection labels are bound to the current session,
profile, feature and saved revision; they are not standalone authorization.

**These are saved settings, not live revocation.** Restart the selected Port
through its deployment manager to apply them. The page does not stop a processor
or its existing audio/detection work. Disabling sound classification does not
disable a separately saved speech/audio policy. Models, keys and private history
remain; no recording, template, provider or backup copy is deleted. A preceding
provider Undo cannot regrant the removed scope; a later provider-only Undo keeps
the reduced scope. After an uncertain save, reload the current saved settings
before retrying; do not assume no effect or automatically repeat the operation.

**Audio capture / Speech presence** manages the existing Port audio allowlist,
not the AI Key speech-to-text provider. The Sound role depends on the same
camera's audio permission: remove its saved Sound permission first. A conflicting
audio removal is refused before lock/history/save with a fixed explanation;
it never silently disables Sound or changes another role. Whole audio removal
also removes its now-orphaned speech event limit; a partial restriction retains
that limit. No new camera, recording, key, model or privacy scope is introduced.
See the [audio-scope boundary](evidence/aiport-audio-permissions-20261030.md).

Source/fixture verification does not mean that a deployed admin/Port image has
been updated or that real camera permissions were changed. See the
[permission boundary and limits](evidence/aiport-permission-restrictions-20261030.md).


## Host-approved maintenance console

The **Runtime configuration** link opens a separate read-only comparison of
saved file bytes against each process's reported startup snapshot. It distinguishes
matches, differences and unknown results. Referenced key/model/trust files and
current controller settings are not covered by that digest. Legacy processors
may report healthy connections but unknown startup provenance. This page neither
restarts them nor claims that a saved setting is active. See the
[versioned API contract](admin-api.md#startup-configuration-observation).

The **Maintenance operations** link opens authenticated `/maintenance` forms
for explicitly typed host-published backup, inactive restore, search scratch-
restore verification and Apple image-update plans. Each form names the device,
fixed scope, saved configuration revision and immutable image digest where
applicable. Legacy or custom plans without that reviewable scope cannot be
submitted from the page. Review and confirmation are required.

The page lists retained requests and reported outcomes, blocks another operation
when an outcome is unsettled and never re-offers a used one-use plan. It does
not start a host worker, infer its liveness, retry a lost acknowledgement,
activate a restored directory or provide a NAS update adapter. Existing host
plans/ledgers and operator-qualified cold windows remain authoritative.
See the [API boundary and isolated browser check](admin-api.md#browser-maintenance-console).


## One-camera AI Port controls and browser coverage

A validated one-camera AI Port pool receives the same provider, threshold,
request-cap and rollback forms as larger pools. Diagnostic-only configurations
without a paired stream still receive no provider form. This does not create
a stream, pair a camera or enable another device.

The optional isolated Chrome runner covers required-input refusal, one-camera
provider save/byte-exact rollback, role-model preview/confirmation/byte-exact
rollback and invalid model rejection. Its two explicitly fictional loopback
provider cases return HTTP 503 and a wrong challenge answer; neither involves
a model, camera media or a real provider. A mismatch remains a mismatch, not a
verified model identity or quality failure. These source/browser fixtures do
not replace native controller or physical mobile-device acceptance.


## Camera-local detection score gates

`/camera-thresholds` edits the already implemented API detector's
`camera_thresholds` policy, without JSON editing. It requires an existing API
Port and a validated inventory no older than five minutes. Every saved stream
must have a matching, unambiguous camera name, MAC and ID. Missing or ambiguous
inventory disables named editing; it does not enroll cameras or guess labels.

An empty camera field inherits the unchanged global threshold. An explicit
finite value must be greater than zero and at most one. A lower gate admits
weaker detections (and potentially more false positives); a higher gate can
miss real detections. Preview shows every camera's old/new effective gate and
whether the new value is inherited. Scores are not calibrated accuracy claims.

Saving requires the normal login, CSRF/Origin checks, shared mutation limit and
an explicit preview confirmation. Server-private, ten-minute signatures bind
the login, profile, exact camera identity/name selection, old/resulting config
revisions and every value. Replaced/renamed cameras, stale settings and expired
sessions refuse. Browser fields carry opaque camera choices, not MACs or IDs.

Only `live_pool_detector.camera_thresholds` changes. Empty mappings remove that
optional field; a semantic no-op preserves exact prior bytes, including raw MAC
spellings and omitted defaults. The complete runtime parser and 4-KiB limit
still apply. Provider/key, global gate, classes, fallback, request allowance,
plate/face/audio scope, identity, streams and all sibling profiles stay intact.
No model request, restart or native controller write is performed. Saved gates
become active only after a separately managed restart of that Port.

A separate revision-checked **Undo camera thresholds** restores exact prior
bytes only while the current config still matches this change and every
non-camera-gate value is unchanged. It is not labeled as provider rollback and
cannot restore a later provider/permission change. Missing/unsafe history
refuses; a successful undo consumes its record. Admitted file writes are
tracked and drained even after a browser waiter disconnects. A post-write or
acknowledgement failure is uncertain, not a confirmed rollback: reload and
reconcile; no automatic retry, restart or rollback is attempted.

The maintained fictional desktop/mobile browser check covers preview, required
acknowledgement, Space/Tab/Enter confirmation, saved result and exact undo.
This is not live camera tuning, native Flur acceptance or full #17 completion.

## Plate-crop camera permission

`/plate-cameras` lets the operator explicitly select **already paired API Port
cameras** whose detected vehicle crops may use the displayed OCR provider,
model and base URL. It neither adds a stream nor silently enables vehicle
classes. If vehicle detection is off, the page says permission alone will not
make plate processing work. Capability and recognition accuracy stay unverified.

Named selection uses the same fresh validated inventory and unambiguous opaque
camera choices as the score-gate page, but a separate server-private signing
key and plate-specific confirmation protocol. Every save requires explicit
plate-data/destination/cost/restart consent. Confirmation binds the login,
profile, raw configuration revision, current camera ID/name selection, full
selection and resulting revision. Each random draft is usable once for ten
minutes; admitting a change also invalidates older open drafts for that profile.
This prevents an old grant from replaying after exact-byte revocation. Pending
authority is bounded to 256 unexpired drafts and is lost on Admin restart;
expired/evicted/restarted forms must be previewed again.

The typed store changes only `live_pool_detector.plate_cameras`. If no camera
remains, it also removes a separate `ocr_provider_config`, because the runtime
schema disallows an OCR assignment without plate scope; that removal is shown
explicitly before confirmation. Private key files remain. Detection route/key,
classes, global/per-camera gates, fallback, request allowance, other recognition
permissions, identity and streams remain unchanged. Selecting the current set
is a byte-exact no-op; complete runtime validation and revision/lock checks are
reused, not a new settings engine.

These are **saved permissions only**, not immediate revocation: restart the
selected Port separately to change running processing. The form sends no crop,
model test, native pairing or restart. No one-click Undo silently regrants a
removed permission. Re-enabling requires a fresh camera/destination review and
explicit consent. An uncertain admitted write consumes its confirmation and
requires saved-state reconciliation, not automatic retry, rollback or regrant.

The fictional browser fixture exercises removal followed by a genuinely new
explicit grant. It checks full semantic config equality and then the fixture
restores its own original formatting; that is **not a product permission Undo**
or proof of live-camera revocation/recognition. Real-client/native #19 acceptance
and the broader #17 workflow remain outstanding.

## Bounded camera inventory observations

Score-gate and plate-camera GET pages share a validated server-side inventory
observation with the private camera API for at most two seconds. Each complete
page uses a single observation for all its profiles. Parallel readers coalesce;
source failure briefly suppresses repeated reads, never substitutes expired
data, and leaves the saved configuration untouched. An unavailable inventory
can hide named editing without disabling any running permission. Reload shortly;
do not interpret a missing form as a changed processing policy.

POST preview/apply remains fresh: camera identity and current configuration are
re-observed before consent can be admitted. Rollout authority remains separate.
The legacy remove-only permission view keeps its existing source contract. See
[the reproduced Protect quota refusal and correction](evidence/shared-camera-inventory-20261030.md).

## Processing and budgets

The [saved caption cost policy](#saved-caption-cost-policy) has a separate
reviewed form; the observations below do not change a running or saved policy.

The signed-in **Processing and budgets** link opens `/processing` and a compact
overview of every attached Key/Port profile. Expand a profile for its queue,
inference gate, stream, provider failure and per-camera API-budget observations.
Keyboard users can focus the horizontally scrollable overview and open the
profile details with Enter. The same data is available to authenticated clients
at `GET /api/admin/v1/processing` (`processing_schema: aikey-processing-status/1`).
Neither route accepts options or implements a POST/action.

This uses the existing explicitly configured, CA-verified and leaf-pinned HTTPS
health peers, byte/deadline bounds, concurrency limit and two-second cache. It
does not discover peers, use native Protect credentials, fetch media, call a
provider, read a state journal, apply configuration, replay jobs or restart a
service. No new dependency, listener or authentication authority is added.
Authentication is checked before and after awaited observations. A missing,
invalid or unavailable peer stays unknown, independently of healthy peers.

Only declared boolean flags and nonnegative integer counters are returned.
Camera rows use **positions in the observed runtime's stream order**, not
inventory names or stable camera identities. Native IDs, correlation hashes,
endpoints, private error messages, model replies and frame/observation content
are not part of this view. Missing groups/counters remain null/Unknown; an
explicit zero is not replaced by an empty or unknown value.

Interpret the counters separately:

- Key caption `admitted`, `exhausted` and `deferred_fair_share` are current-process
  admission counters, not remaining hourly permits or evidence of the active
  budget policy. The conditional continuous metered policy is globally 12 new
  attempts per rolling hour; an explicit local unmetered override is separate.
  Local routing is not a billing guarantee. The existing Caption preflight
  reads **saved** policy/journal data, which may differ from running settings.
- New Key workers separately report their loaded continuous-caption policy:
  metered, explicitly unmetered, one-use test scopes, or disabled. Older workers
  remain unknown. In metered mode, the worker samples its own atomically published
  durable journal without reserving, locking, repairing, pruning or writing it.
  The bounded observation gives sample time, rolling window, limit, used and
  unreserved slots. Missing journals start empty under the existing budget
  contract; corrupt/unsafe journals or backward clocks are unavailable, not zero.
  The single-flight off-loop read waits at most 150 ms per health caller; repeated
  requests/cancelled waiters cannot queue additional reads behind a slow one.
  A completed sample is valid for at most two seconds from read admission; an
  overdue completion is unavailable. Admin may additionally cache health for two
  seconds. Fair sharing and concurrent reservations can still refuse an attempt;
  an unreserved slot is neither admission nor monetary credit. No saved or active
  policy is changed by viewing these observations. Unmetered/noncontinuous modes
  do not read a budget journal or invent a zero/remaining value.
- Failure-note counters describe publication attempts by the current process,
  not failed jobs, unique events, present outages or historical missing causes.
- Port attempts/successes include empty detections; they are not event, object,
  paid-request, precision or quality metrics. Provider failure history does not
  itself prove a current outage; the consecutive-failure counter is separate.
- The optional Port API-attempt cap is per camera/rolling hour and is distinct
  from Key captions and native object-event admission. An explicitly reported
  null cap means no cap configured; an omitted cap means unknown. Remaining
  permits are a health snapshot, not a new reservation or admission guarantee.

This completes a read-only processing view, not configuration of all feature
policies, native event/recording/quality acceptance or the full scope of #13/#17.
See [checks and limits](evidence/processing-status-20261031.md).


## Saved caption cost policy

Signed-in `/caption-policy` configures the cost policy of an **existing** Key
continuous-caption camera policy. It cannot create that camera policy, add or
enable a camera, convert one-use test scopes, change a provider/key/model,
start a processor or modify Port/speech/base-detection policies.

No mode is implicitly selected. Choose and preview either the global metered
policy (12 new caption reservations per rolling hour) or, when existing model
configuration permits it, the explicit local unmetered override. Local routing
does not prove free inference or an absence of upstream billing. Unmetered
confirmation requires a separate unchecked cost-bypass acknowledgement in
addition to the saved-settings confirmation. A remote-only route cannot bypass
the existing configuration validator by posting a hidden option manually.

Preview is read-only and grants one session/revision/result-bound confirmation,
valid for ten monotonic minutes. At most 32 live previews exist across sessions;
capacity refusal does not evict another valid preview. Grants retain no raw
cookie, provider key or configuration body. Apply revalidates the revision after
awaited work, rechecks current session/expiry/one-use authority, consumes the
grant and hands the write to the existing shielded/drained file-operation owner.
Two overlapping confirmations cannot admit two saves. Logout or expired
authority before admission refuses; disconnect/logout does not undo an already
admitted save. An uncertain acknowledgement asks the operator to read saved
policy before another attempt and never auto-retries, resets usage or rolls back.

The typed store changes **only** `worker.continuous.unmetered`: setting the
existing permitted `true` override, or removing it to restore metering. It
preserves unrelated raw fields, spellings and omitted defaults, archives exact
prior bytes, applies under the existing private lock/OCC rules and verifies the
new bytes. Same-policy no-ops keep the original revision/bytes. It never reads,
prunes, clears, repairs or refunds the budget journal. Re-enabling metering can
therefore leave zero available slots if existing reservations exhaust the hour.
There is no new provider-Undo record that silently restores an unmetered bypass;
changing the policy again uses a fresh reviewed choice. Generic administrative
configuration/restore authority is not removed by this convenience form.

`GET /api/admin/v1/caption-policy` returns only saved-policy status/revision and
whether current model configuration allows the local override. Its explicit
scope is `saved_policy_not_running_authority`; it is not an observation of the
running Key. Older workers without the separate running-policy health field
remain unknown in Processing. POST form routes require the existing same-origin
session/CSRF and shared 60-per-minute admission policy. There is no new listener,
credential authority, runtime reload/restart, provider test or dependency.

A separate Key restart is required to apply a changed saved policy. Review
running/startup revision status separately; a successful file save or a visible
option does not prove native work, admission, provider capability or billing.

## Initial speech transcription setup

When no AI Key speech role is saved (absent or null), **Set up speech transcription**
opens `/speech-enrollment`. It lists only connected, unambiguously named Protect
cameras reporting `alrmSpeak` in a fresh validated inventory. No camera is
preselected. Reported capability is not native task-delivery proof; cameras
without reported speech support cannot be enrolled by this flow. Unknown,
stale, unavailable or ambiguous inventory leaves no setup form.

Select 1–32 cameras and a separate speech provider, model, API destination and
optional two-letter language. OpenAI uses its canonical official endpoint; a
compatible API must be loopback or an explicitly allowed private numeric host.
The initial page accepts no secret. Preview shows the exact selected camera
names and destination, without contacting a model or creating a key/config.

Confirmation requires explicit audio sharing and separate cost acknowledgement,
plus a new write-only speech credential for OpenAI (optional for a local API).
A vision key is never reused. The server holds at most 32 one-use preview records
without provider credentials or raw session cookies. They are
session/revision-bound preview records for ten monotonic minutes; a full registry
refuses new previews instead of evicting another valid form. Restart discards
previews. Both preview and apply re-read inventory; changed identity/name/model/
state/audio support refuses the old selection. A second confirmation cannot
claim the same preview. Logout/expiry during awaited reads prevents admission.

Saving adds only `speech_to_text` through the existing private atomic/OCC store
and exact prior-configuration archive. Already configured speech is never
overwritten or widened; its existing provider/restriction controls remain
separate. The setup creates no provider-Undo record that silently regrants audio.
Existing remove-only controls can disable the saved role. A later provider save
keeps its ordinary revision-bound Undo; a permission removal invalidates it.

This is **saved consent only**: no processor restart, microphone/Protect setting,
camera pairing, audio export, model/API test or native request occurs. Existing
worker audio bounds and other feature policies are unchanged. The operator must
restart the Key separately to use the new saved role; this setup does not submit
a maintenance restart. Already admitted filesystem work is shielded and drained
on normal Admin shutdown; a disconnected or logged-out HTTP waiter cannot undo
a save already admitted. A post-write failure is explicitly uncertain, preserves
the possible live/archived key and is never automatically retried or rolled back.

Audit records use only the fixed `speech_enroll` action/result/profile; no selected
camera ID, provider setting, key or transcript is logged. Camera selection values
are opaque and session/revision/source-bound. Credential values, credential file paths and raw camera
IDs do not appear in forms, browser storage or error output. The browser runner
uses fictional cameras and keys only; source/browser results are not permission
to enroll a real household microphone or a quality/native-acceptance claim.
