# Private administration JSON API

Work in progress for #13. These version-1 endpoints adapt the existing private
administration listener and configuration stores. They are **not** the device
service, a new network listener, a public diagnostic export, a generic file
editor or a command-execution API. Identity/pairing/host edits and supported
backup/restore/update actions are not yet implemented by
this JSON contract; #13 remains open until its whole scope is reconciled.

## Authentication and response boundaries

Sign in using the existing administrator login, keeping its session cookie.
`GET /api/admin/v1/session` returns the current session's CSRF token. POSTs
require that cookie, the exact configured `Origin` and `X-CSRF-Token`; forged
Host/forwarding headers do not supply authority. The runtime-wide mutation
window is shared with the browser forms, not reset by API login or profile.
Logout, expiry, revocation or admin restart ends authority. Already admitted
commits are not cancelled by logout or a disconnected HTTP waiter.

Every JSON response has `schema: "aikey-admin/1"`, `Cache-Control: no-store`,
no-referrer, nosniff and restrictive CSP headers. Error codes are fixed and do
not echo body values, secret references or exceptions. There is no automatic
retry after a write failure: `outcome_uncertain` requires fresh revision
read-back and reconciliation. An IO failure is not evidence of rollback.

The authenticated configuration view is **private operator data**: it can include
camera IDs, server destinations, file locations and policy values needed to
administer the installation. Credentials and credential-file references remain
write-only markers; it does not load keys/templates/models or query providers.
Do not post a full response as public diagnostics. Use existing allowlisted
diagnostic/count-only pages for that purpose.

## Version-1 routes currently implemented

| Route | Meaning |
| --- | --- |
| `GET /api/admin/v1/session` | Session-bound CSRF token, shared mutation-boundary notice. No new session/credential is created. |
| `GET /api/admin/v1/profiles` | Configured Key and Port profile IDs/kinds and supported configuration-edit flag; not evidence those processors are running. |
| `GET /api/admin/v1/cameras` | Complete validated current Protect camera inventory and separately labeled saved Port bindings, not native pairing proof or a new scope grant. |
| `GET /api/admin/v1/profiles/{profile}/configuration` | Masked configuration plus saved role registry, both from the same captured revision. Explicit `saved_not_active_runtime`. |
| `GET /api/admin/v1/profiles/{profile}/runtime` | Bounded allowlisted observation from an explicitly configured own-runtime HTTPS peer, or unknown/unavailable. Never inferred from saved settings. |
| `GET /api/admin/v1/profiles/{profile}/identity` | Private saved MAC/certificate, persisted adoption and configured controller-trust observations, explicitly not a current connection/adoption proof. No initialization or credential hydration. |
| `POST /api/admin/v1/profiles/{profile}/preview` | `{revision, patch}`; pure validated JSON Merge Patch preview with changed fields, resulting revision and restart/current-job boundary. No history/file/model write. |
| `POST /api/admin/v1/profiles/{profile}/apply` | `{revision, patch, preview_revision}`; check predicted revision then existing locked/OCC/atomic history/write. Concurrent callers cannot overwrite an unseen revision. |
| `POST /api/admin/v1/profiles/{profile}/rollback` | `{revision, target_revision}`; explicit complete saved-configuration rollback through immutable identity/index/pairing guards, not provider-only Undo or a controller reset. |

Bodies must be exact finite UTF-8 JSON objects, without duplicate/unknown fields;
the listener's 8-KiB request bound applies. Configuration patches cannot replace
write-only fields. Provider credentials still use the dedicated write-only
provider operation. The Key store preserves device identity/credential locations
and refuses incompatible index changes outside migration. No restart, drain,
past-event reprocessing or model activation is performed by these routes.

A full rollback deliberately selects a prior complete configuration; review its
policy/permission consequences rather than treating it as a provider-only Undo.
The existing remove-only provider Undo boundary remains separate. Restore of
backups, search indexes or adopted device identities is not a configuration
rollback. Reconcile retained secret references and actual runtime state before
activation. Invalid/unknown profile returns 404.

Port patches may change only live detection policy, face/sound policy, audio
camera scope and its event limit. Native identity, controller trust/address,
host/decoder/firmware and paired stream bindings are immutable here; new slots
and pairings use the separate rollout operation. Full runtime validation checks
model references, camera membership, feature dependencies, sizes and supported
arguments before save, without model loads or provider contact. Raw unrelated
saved omissions/spelling are retained. A retained detection/OCR/fallback key
cannot be retargeted by a generic patch; use the role-specific write-only flow.
Rollback verifies the exact archived digest and required retained key references,
and cannot import another identity/slot's streams.

Writes admitted by the API remain owned until their store operation and final
fixed audit outcome settle. Service cleanup drains them. Audit action names are
`config_preview`, `config_apply`, `config_rollback`, with fixed result/profile
labels, never settings, paths, names or key values. A store error after write
admission records `uncertain`; no success/rollback/retry is inferred from it.

## Optional runtime observations

Configure each intended target on the server with repeated
`--runtime-health PROFILE=HTTPS_URL`. Profiles must already exist; the target
must be one literal private/loopback/VPN IP, HTTPS and exactly `/healthz`, with
no credentials/query/fragment. No request parameter can choose a URL, trust
file, host, command or credential. Ports have no guessed publication port.
An omitted target means **not configured/unknown**, not offline or disabled.

The observer trusts the configured profile's own public `device.crt`, retains
normal CA/validity verification and verifies that exact leaf SHA-256 pin before
HTTP headers. Hostname checking may be replaced only by this explicit identity
pin, not by `ssl=False`/disabled certificate validation. The client sends no
controller/provider credential or cookie, uses no environment proxy, follows no
redirect and refuses compressed replies. One request, three-second transport /
four-second total admission deadline, 256-KiB strict JSON body. Wrong service,
duplicate/nonfinite fields, wrong counter types or oversized replies do not
become runtime facts and are never echoed.

Per-profile coalescing and a two-second result cache limit polling; at most two
peers are contacted concurrently. Successful reports include observation time,
allowlisted boolean/count fields and CA+leaf-pin verification. Missing metrics
remain null, not zero. Raw images, camera identifiers, descriptions, provider
errors and arbitrary health namespaces are not copied into this response. The
running process does not report its loaded configuration revision, so that
field remains null; a live connection is not proof it uses the newest saved
settings. PTZ is not an implemented action here; recording remains
controller-owned/unmeasured rather than falsely reported working or disabled.

Runtime observations make only read-only own-runtime requests, not controller
discovery, camera fetches, model inference, activation or a remote health export.
Logout/expiry after an awaited read still refuses delivery. The helper's tests
use owned TLS peers: CA refusal, separately trusted-but-wrong leaf pin, redirect,
ambiguous/oversized/mistyped JSON, bounded slow response, twelve concurrent
coalesced reads and explicit unknown/zero distinctions.

## Saved identity and controller trust

The separate identity view is private operator data, not public diagnostics.
It reads only bounded private regular state/public-certificate files, never the
device private key or a provider/management credential value. No schema migration,
state initialization, certificate issuance or controller handshake is performed.
Missing/malformed/future-schema state stays unknown/unavailable; it never creates
a replacement identity. Key state MAC must match saved configuration before
its persisted adopted flag is reported. Port adoption records bind to controller
address/pin and do not contain the Port MAC; that limitation is explicit rather
than a fabricated identity-binding proof. Saved stream bindings are counted,
not described as running or currently paired in Protect.

Controller public-certificate validity and, when configured, agreement with the
saved controller pin are separate observations. A valid file or persisted
adopted flag is not a verified live connection. Use the runtime observation and
the independently recorded native controller acceptance for their own scopes.
No template, token, raw state, private key, credential location or unrelated
state value is included in the identity response.

## Private camera inventory

The camera endpoint uses only the server's existing pinned inventory loader,
never a caller URL/key. It reuses the validated camera schema, bounded counts,
supported Protect version and fresh observation time; stale/partial/malformed
or unavailable inventory returns a fixed 503, not an empty success. Only known
fields are returned and classifications are recomputed from validated camera
fields. Arbitrary loader metadata is discarded. Two-second coalesced caching
bounds API polling; this does not cache the rollout operation's required fresh
planning read.

Configured camera MAC bindings are joined to named Port profiles as **saved
configuration only**, not proof of current native pairings or successful
processing. Names/IDs/MACs here are private operator data, not a public export.
The observed inventory does not identify every source's Protect-vs-ONVIF kind;
that remains unknown, not invented from a model name. ONVIF ingest remains
unimplemented and its native acceptance issue stays open. Reading this endpoint
does not change pairing, permissions, configuration or processing activation.

## Remaining #13 work

These local checkpoints do not close #13. Complete runtime camera/policy
binding, native controller-owned unsupported status,
authenticated supported maintenance
operations, their approval/preflight/recovery boundaries, full original scope
and rejection/concurrency qualification before the integration PR. No unqualified
backup/export/restore/update/PTZ/recording operation is advertised as available.
