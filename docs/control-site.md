# Local control site

For a credentials-free, read-only preview with invented data, use the separate
[synthetic demo](synthetic-demo.md). It is not an administration or device server
and cannot read deployment state or send a provider request.

The control site is a separate loopback HTTP service for the AI Key and optional AI Port configuration files. It has administrator sign-in, a signed session, CSRF protection, revision-checked saves and write-only API-key replacement. It manages the vision provider, model and endpoint for AI Key descriptions and AI Port object detections. OpenAI, Claude/Anthropic, Ollama and an OpenAI-compatible endpoint are selectable. Each AI Port form also manages classes, score threshold and an optional API-request cost cap (empty means no cap) for that instance only. Object events have no per-camera cap, and a save keeps the local fallback, plate cameras and held-package mode. A configured AI Key speech role has its own provider form, described below. The site does not manage embeddings, audio camera opt-ins, face/plate models, adoption, camera pairing or service restarts yet. Those controls remain open work under [issue #17](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17).

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

The read-only Search index migration page reports historical scratch-restore
records and live-row comparisons. Damaged/unsafe newest metadata blocks backup
readiness instead of silently falling back to an older record. It neither
rehashes nor restores a dump during page loads; re-verify before recovery.
See the [backup-status boundary and limits](evidence/index-backup-status-20261029.md).

## Data flow and retention

The signed-in [data-flow and retention page](data-retention.md) separately shows
all output/state categories, other destinations, product defaults and current
lifecycle mechanisms. It is read-only: unavailable counts are not zero, local
file permissions are not encryption, and deleting a local copy does not delete
Protect, model-server, host-log or backup copies.

## Speech provider

If `speech_to_text` already contains a valid explicit camera allowlist, the Speech provider form changes only that role's provider, model, API base URL, optional language, private-network permission and write-only key. The existing camera list and clip-length policy stay unchanged. No provider form is offered for an unconfigured role. Initial audio enrollment remains unsupported; the separate remove-only permission controls are described below.

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
read the configured Protect inventory. Initial audio enrollment remains unsupported.

Selection values are opaque and bound to the signed-in session/configuration
revision. Raw camera IDs are not put into selection values or URLs/audit entries.
Unknown, repeated, cross-session or stale selections fail. A no-op preserves
the configuration bytes. Existing private history/keys remain available for
recovery; an old provider Undo is invalidated by a permission revision and cannot
silently restore removed scope. A later provider-only Undo preserves that reduced
scope. An uncertain save is reported as uncertain: reload before retrying.

These are source/HTTP integration results, not native live revocation, browser
visual/mobile acceptance or a deployed permission change. See [checks and limits](evidence/speech-permission-restrictions-20261029.md).

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

## Dated native administration evidence

The [30 October NAS loopback qualification](evidence/native-admin-loopback-20261030.md)
records a separate authenticated service reading the actual Key/four-Port
profiles, five maintenance-page responses, explicit refusal cases, one admin-only
restart and unchanged native device/pairing baseline. It remains NAS-loopback
only; there is no LAN TLS, browser/mobile, real apply/rollback, host-reboot or
sustained-operation acceptance claim. This does not change the default deployment
or enable an administration service merely by upgrading a processor image.

## Removing saved AI Port recognition permissions

For attached Port profiles, **Remove saved AI Port face or sound permissions**
opens a remove-only page. It can shrink an already configured face-recognition
or sound-classification camera list, or disable that saved role. It cannot enroll
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

Source/fixture verification does not mean that a deployed admin/Port image has
been updated or that real camera permissions were changed. See the
[permission boundary and limits](evidence/aiport-permission-restrictions-20261030.md).
