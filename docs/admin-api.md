# Private administration JSON API

Work in progress for #13. These version-1 endpoints adapt the existing private
administration listener and configuration stores. They are **not** the device
service, a new network listener, a public diagnostic export, a generic file
editor or a command-execution API. Identity/pairing/host edits remain outside
generic configuration patches. Typed rollout and host-approved maintenance
have separate guards. #13 remains open until the integration and its remaining
native controller-status access are qualified; this document is not a native
parity claim.

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
| `GET /api/admin/v1/maintenance` | Only host-published one-use plans for known profiles; not execution or worker-liveness proof. |
| `POST /api/admin/v1/maintenance` | `{operation, revision, request}`; authenticated/audited durable queue admission, no caller command/path/URL. HTTP 202 is not completion. |
| `GET /api/admin/v1/maintenance/requests/{request}` | Fixed host result for the same retained request ID; an unknown result is not automatically retried. |
| `POST /api/admin/v1/profiles/{profile}/preview` | `{revision, patch}`; pure validated JSON Merge Patch preview with changed fields, resulting revision and restart/current-job boundary. No history/file/model write. |
| `POST /api/admin/v1/profiles/{profile}/apply` | `{revision, patch, preview_revision}`; check predicted revision then existing locked/OCC/atomic history/write. Concurrent callers cannot overwrite an unseen revision. |
| `POST /api/admin/v1/profiles/{profile}/rollback` | `{revision, target_revision}`; explicit complete saved-configuration rollback through immutable identity/index/pairing guards, not provider-only Undo or a controller reset. |

Bodies must be exact finite UTF-8 JSON objects, without duplicate/unknown fields;
the listener's 8-KiB request bound applies. Configuration patches cannot replace
write-only fields. Provider credentials still use the dedicated write-only
provider operation. The Key store preserves device identity/credential locations
and refuses incompatible index changes outside migration. No restart, drain,
past-event reprocessing or model activation is performed by configuration routes.

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
errors and arbitrary health namespaces are not copied from the peer. The
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

### Runtime camera/policy order

New Port runtimes report versioned `aikey-runtime-pool-binding/1`: a digest of
their own MAC and ordered camera MACs, without raw identifiers in the health
payload. Per-camera policy/zone counts retain runtime order; absent policy is
unknown, while a received policy with no zone has zero zones. Detection types,
event counters and remaining budget are allowlisted, not arbitrary peer data.
No zone geometry or private model/media data is copied.

The private admin API compares this declaration to the captured saved Port
configuration. Only an exact identity/order match permits joining configured
camera MACs to those ordinals. A mismatch, missing old-runtime declaration or
unavailable saved config leaves the mapping false/unknown and never labels
another camera's counters as this camera. The matching saved revision and runtime
observation time are retained separately. This is **not** controller-native
pairing proof, proof of the latest complete loaded settings, zone-quality
ground truth or authorization to edit a controller zone. Older running Ports
cannot be claimed to report these new fields before an explicitly qualified
runtime upgrade.

Per-camera observations also retain the existing stream-active flag, requested
width/height/fps, reserved capacity points, restart/deferred-start counters and
full-frame count when reported. Missing old fields remain unknown, not zero.
Geometry follows the same numeric limits as maintained stream admission;
boolean/nonfinite/extra-field geometry and contradictory geometry/capacity are
refused. The same runtime-order binding gate applies before joining camera MACs.
Requested geometry is not actual decode fidelity, a controller channel index,
playback continuity or PTZ/recording control.

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

## Local rollout transaction recovery

Rollout applies share the existing Port configuration locks with the JSON and
browser writers. Configuration/rollout lock contention is bounded to five
seconds per acquired lock rather than retaining an executor indefinitely.
Registration is part of the same rollback boundary as Compose and allowlist
writes, including a lost acknowledgement after replacement.

Before any target write, one private, bounded transaction journal beside the
rollout registry retains the last-known-good bytes and digests. Successful apply
or verified rollback marks it terminal. An interrupted or uncertain transaction
blocks further planning/apply until host-side reconciliation; it is never
automatically replayed. A rollback will not overwrite bytes from a later writer
that are not one of this transaction's own outputs. Generated, unregistered
device identities remain for reconciliation rather than being deleted. This
local recovery journal contains private configuration; include it in private
backup/retention policy, not public diagnostics. It is not a complete database
or processor-state backup and is not evidence of a live restore drill.

## Host-owned maintenance

`GET /api/admin/v1/maintenance` lists only host-published, frozen, **one-use**
plans for configured profiles. Without a configured bridge it returns
`configured: false` and no operations. This is not a worker-liveness claim.
`POST` to the same endpoint takes exactly `operation` (published digest),
`revision` (reviewed saved configuration digest), and `request` (32 lower-case
hex digits chosen once by the caller). It requires the existing session,
Origin/CSRF/shared mutation limit and a durable audit admission. HTTP 202 means
**queued, not executed**. The client must retain the same request ID and read
`GET /api/admin/v1/maintenance/requests/{request}`. It must not retry an unknown
write with a fresh identifier. An exact resubmission is idempotent; one plan
cannot execute twice even under distinct request IDs. Terminal state describes
that one operation's result, not a fresh execution for each receipt.

The separate host owner runs `python -m aikey.admin_maintenance work-once` with
explicit `--host` and `--bridge` roots. Each invocation admits at most one
runbook and uses an exclusive kernel lock. Runbooks inherit that lease; the
maintained adapters pass it to their engine clients as well. A killed owner
therefore cannot be replaced while its surviving runbook still owns the lease.
A recorded `started` or `uncertain` outcome prevents **all new plans** from
executing until host-side effect/recovery reconciliation. Never clear it merely
because a PID disappeared, a request timed out, or a later health check is green.
There is no automatic replay, rollback of an unknown host effect, or live
database overwrite.

### Deployment authority

The host root contains plans, the one-use ledger and private staging. **Never
mount it in the admin container.** Mount bridge `catalog` and `receipts`
read-only and only `requests` writable, then set `--maintenance-bridge` to
their common container parent. Do not mount the Docker socket, engine binary,
host root, or private runbook inputs in the admin container. The processor
credentials do not grant maintenance authority. Do not publish host plans,
archive contents, stdin, stdout, stderr or private filesystem paths as API
diagnostics. Only allowlisted plan metadata and fixed receipts cross back.

Host `prepare` freezes a reviewed Python runbook, its host inputs/resources,
interpreter digest, guarded file digests, current saved revision and timeout
into its content-addressed operation ID. It is a **host-only** operation, not
an API for uploading code. The runbook is trusted operator code, not sandboxed
untrusted code: qualify its own locked preflight, backup, effect and downstream
read-back before publishing it. Guard hashes are an admission observation, not
a replacement for the runbook's own transaction locks. No caller-selected
command, argument, URL, image, manifest path or script is accepted over HTTP.
Removing a published plan before host admission revokes a queued execution.

Requests are atomically published without replacing a prior ID. Files, JSON,
queue/catalog/ledger sizes, output and execution time are bounded. Raw output
is discarded; a runbook must return exactly the fixed
`aikey-maintenance-result/1` schema with an outcome of `succeeded`, `rejected`,
`rolled_back` or `uncertain`. The owner terminates its own process group on
timeout/output overflow and collects its direct child. An interrupted owner's
private staging is deliberately retained under its host root for reconciliation;
normal completion removes it. No scheduler/daemon or privileged service is
installed automatically. The host operator must arrange the owner invocation.
The exit watcher is non-reaping (`waitid(WNOWAIT)` on Linux, native `kqueue`
on macOS), retaining the process-group anchor until the final group signal.
This is not an untrusted-process sandbox or a Linux subreaper. A runbook that
creates an escaping session is not claimed contained/killed. Missing output
EOF after direct-child exit is bounded and forces `uncertain`, not success;
its inherited lease and the unresolved ledger retain the admission barrier.
At the 128-record capacity, stop admission, drain/reconcile and withdraw the
old catalog. Retain the old host authority and its one-use history privately;
if new authority is needed, prepare a distinct host root deliberately rather
than deleting ledger entries in place. There is no automatic retention pruning
or reset of one-use history.

### Maintained adapters and actual limits

Host `prepare-state --plan PRIVATE_DEFINITION_JSON` prepares device-file export
or a **fresh offline restore**. Its definition selects `kind` (`backup` or
`restore`), `profile_kind` (`aikey` or `aiport`), the existing host `directory`,
private `archive` path, known Docker `container`/absolute `engine`, optional
profile label and a new `destination` for restore. Profile kind, saved identity,
own public certificate, current revision and actual runtime state mount must
agree. The published API metadata contains none of these private paths.

The backup includes **every regular file and empty directory** in the state
root: saved configuration, adopted/factory identity, TLS key/certificate,
controller/search trust, referenced local keys, configuration histories,
worker/budget/replay journals and local biometric stores when present. Nested
state-local/container-state key references are supported. External references
are refused rather than silently omitted or followed outside the profile.
All containers mounting an overlapping source path must be stopped, including
other writers; the adapter does not stop them for the caller. The normal
Key/Port writer lock is held, and complete Docker inventory is checked again
after the export. A newly running writer makes the outcome uncertain, not a
completed backup. Matching container identities, images, restart counts and
start/finish timestamps also detect a start-and-stop between those checks.
A stopped processor alone is not claimed sufficient when
another container can write that same state tree.

The private, bounded uncompressed archive has content digests, saved revision,
identity/certificate binding and a validated certificate/private-key pair.
Existing identity MACs, supported Key state schema and Port controller-adoption
bindings are checked using the maintained state validators. Factory certificate
fingerprints are checked when stored. No missing identity is regenerated or
state migration performed by an export/restore check. Encrypted device-key
formats are refused without an interactive password prompt.
Symlinks, hardlinks, special files, unsafe paths, duplicate members, extension
headers, hidden trailing payload and mismatched metadata are rejected. Source
files are copied via anchored descriptors; the bytes actually copied and a
second same-descriptor read must match, not merely timestamps. The default cap
is 1 GiB of file data and 200,000 total entries, with a 64-MiB manifest and
separately bounded tar overhead, not a silently truncated backup. Sequential
tar readers/writers do not retain an additional complete `TarInfo` cache.
Anonymous staging uses the chosen private destination volume rather than
placing a potentially large backup in a default system temporary directory.
Model inputs outside the state root and the database are **separate** artifacts;
this is not a complete installation/host backup. Archive output must be in a
private host directory. It is **not application-encrypted**: select protected
backup storage and appropriate volume/backup encryption for the installation.

Restore verifies an immutable anonymous archive copy completely **before**
creating the destination. Only a new, unmounted offline directory is supported;
an existing/live directory is never overwritten. Directory descriptors anchor
all writes and read-back, every restored byte is checked, permissions are kept
owner-only, and the profile is **not started or activated**. The original source
may stay running during this isolated restore because it is not modified.
A partial export/restore created before an IO or postflight failure is retained
privately for reconciliation, not deleted through a potentially replaced path.
An archive filename's existence alone is not success: require the terminal
operation receipt and verified digests. Activation, original-path rebinding,
database recovery and a full production disaster-restore drill remain separate
qualified steps, not inferred from this file restore.

Host `prepare-search --plan PRIVATE_DEFINITION_JSON` freezes the existing
`search_backup` implementation, not a second database backup implementation.
The definition selects `kind` (`backup` or `restore`), `config`, `container`,
an absolute `engine` binary, `output`, and optional `profile`/`manifest` paths.
The backup action writes a private logical dump and performs the maintained
scratch restore/count/dimension verification before success. The restore
action **only** verifies a host-selected existing dump in an isolated scratch
database and drops that scratch DB; it never overwrites the live index. This
is not a complete adopted-device/credential/filesystem backup or a disaster
restore drill. Use the existing separate state-backup preflight for that scope.

Host `prepare-apple-update` freezes the existing `apple_upgrade` and
`apple_supervisor` implementation. Its definition selects host `config`,
`state_dir`, `service`, `new_name`, immutable digest-addressed `image`, processor
`kind`, private literal `health_url`, own `certificate`, retained
`recovery_archive`, expected Port `streams`, optional profile label and engine.
It makes a read-only exact plan, binds the engine/archive digests, re-plans
before execution, requires CA validation plus the exact leaf pin before health
headers, and refuses unavailable/malformed/nonidle health before hold/stop.
The existing tool preserves the live runtime layout, pins only after readiness,
and attempts the existing old-container rollback on failed replacement. The
old container/image and recovery archive remain. This is an **Apple same-host**
adapter, not a Docker/NAS image-swap or Key/PG namespace migration. Idle is an
observation followed by graceful stop, not producer-pause/zero-inflight proof;
archive hashing is not a full restore drill. Those native/endurance and recovery
requirements stay in their own acceptance issues.
If a failed `run` acknowledgement leaves an existing/unknown new target, the
tool preserves it and does not restart the old identity beside it; it retains
the hold and reports uncertainty for host reconciliation instead of deleting
an unowned container name. A conclusive absent-target failure can restore the
old container normally. The selected config/state mount and own certificate
are bound to the profile, not just a user-facing label.

No live restore, unsupported update platform, unqualified PTZ/recording action,
ONVIF ingest, arbitrary export or generic shell endpoint is advertised as
implemented. Supported plans exist only after host preparation/qualification.
Local tests use fictional state, engine processes and owned HTTPS peers; they
do not establish an actual native update, database recovery or hardware parity.

## Integration acceptance

These checkpoints alone do not close #13. The exact frozen integration still
requires full applicable suites, a separate source-derived gate, qualified
private/native read-back and original rejection/concurrency/identity criteria.

## Browser maintenance console

The authenticated **Maintenance operations** link opens `/maintenance`. It is a
server-rendered form, not a shell, path editor or implicit worker. Only host-
prepared, typed built-in plans with the current saved configuration revision
are offered: device-file backup, **inactive** file restore, search backup with
scratch verification, isolated search restore verification, and the maintained
Apple image-update adapter. It does not turn that Apple adapter into a NAS
update or activate a restored directory. An update shows the immutable image
**digest**, not a private registry name, URL, credential or host path. Old/custom
plans without a reviewable built-in scope remain visible but cannot be
submitted here. They retain their constrained JSON API compatibility.

The host publisher attaches `purpose` (one of the above fixed scopes) and, for
an image update, `target_image`. The worker compares all published metadata to
the frozen plan before executing it. A changed profile, kind, timeout, effect,
scope or target cannot silently describe another frozen action. Update host
worker source deliberately when adopting these new typed plans; an older
worker is not silently upgraded by changing the web image. Historical frozen
plans, source and receipts must not be rewritten to make a used plan available.

The form requires explicit confirmation and existing same-origin/session/CSRF
and shared mutation limits. Its correlation ID is stable for that session and
plan; double submission does not create another request. Competing HTML forms
are serialized through durable admission, including after the HTTP waiter
leaves. Used plans are not offered again. Any retained request with no reported
result, `started` or `uncertain` disables new console submissions until the host
reconciles it. The console never resets, prunes, automatically retries or
reconciles a plan. A lost acknowledgement links the **same** request and does
not claim that nothing happened. Durable admission auditing must be available.

`GET /api/admin/v1/maintenance/requests` exposes bounded validated correlation
metadata only (at most 128 retained requests), with `automatic_retry: false` and
worker liveness explicitly unasserted. `/maintenance/requests/REQUEST` reads the
same retained results, even if the current configuration or catalogue becomes
unavailable. “No host result reported” is **not** a claim of queued, idle or
running execution. A reported success is the frozen runbook's result, not a
full host disaster-recovery, power-loss or all-platform acceptance result.
There is no automatic history deletion; exhausted bounded storage requires
host review and preservation of the one-use ledger, not a web reset button.

HTML uses `Referrer-Policy: same-origin`: real Chrome form submissions retain
an own-origin `Origin` header, while cross-origin navigation sends no referrer.
The previous HTML `no-referrer` policy caused Chrome to send `Origin: null`,
which the authentication policy correctly rejected. JSON API responses retain
`no-referrer`; no origin/CSRF check or HTTPS requirement has been weakened.

### Isolated browser check

Install `.[browser]` in a separate development environment. Run
`python tools/maintenance_browser_check.py --output PRIVATE_OUTPUT` after
installing Playwright's matching Chromium, or specify `--browser-executable`
with an existing Chrome executable. The runner creates and collects **empty,
owned headless profiles**; it never imports a user's cookies/profile or contacts
a real processor, controller or model provider. The expanded fixture also
provides an explicitly fictional loopback provider, returning a 503 or a wrong
synthetic image answer; no inference server or model is involved. It tests fictional loopback state
in desktop and 390-pixel mobile-width views: login, required input refusal,
one-camera provider save/exact rollback, role-model preview/confirm/exact
rollback, invalid model refusal, separate synthetic cost acknowledgement,
fictional provider failure/answer mismatch, maintenance confirmation, keyboard
submission, one-use result reads and session expiry. CI uses its installed
Chrome with the Chromium sandbox enabled and records its actual version;
that rolling browser is not an immutable version pin. Screenshots and the
source-digest result contain only fictional fixtures. Inspect the screenshots
before claiming visual acceptance; the script reports that review as pending.
This is not an iPhone, Tailscale-client, native Protect or production-browser
acceptance test. No browser is installed into the runtime images.

## Native Port event correlation (wire observation only)

A reviewed Port can expose `native_event_correlation` through its own health
and the authenticated runtime view. It retains at most 64 SHA-256 correlation
fingerprints in process memory, expiring 900 seconds after the first completed
smart-event wire send. Repeats do not extend retention. Restart loses these
records. No new controller/media/model request, persistent journal, worker,
configuration switch or camera enrollment is introduced.

The digest input is UTF-8 `local-aiport-native-snapshot/1`, one NUL byte, then
compact JSON `[CAMERA_MAC,kind,trackerId,clockBestWall]`. MAC is uppercase with
no separators; kind is person/vehicle/animal/package; both numbers must be
positive JavaScript-safe integers. Native read-back derives the same tuple
from the event's **original camera** mapped through current bootstrap and each
`metadata.detectedThumbnails` entry's `type`, `attributes.trackerId` and
`clockBestWall`. Do not round clocks, substitute event timestamps or guess
camera order. Current native records expose those field types; exact emitted
tuple preservation still requires a fresh matched read-back after deployment.

Only `leave` snapshot metadata for configured paired cameras is recorded, and
only after the real `send_bytes` completes. Failed/cancelled sends create no
record. Face/plate/identity templates, filenames, coordinates, raw identifiers,
labels, text and images are not copied into the per-event diagnostic records.
These deterministic digests are **linkable private diagnostic metadata**, not
anonymization, a signature or authentication.

The fixed scope is `wire_sent_only_native_readback_required`. Neither a
completed send nor a hash alone asserts native acceptance, persistence, no
false positives or quality. Compare to independently fetched native records
and read the exact event again before claiming observed persistence. An empty
or overwritten set is incomplete evidence, not zero activity. Confirming
positive tuples does not establish outside-zone behavior, alarm delivery,
recording playback, ONVIF support or an entire parity contract. The API refuses
oversized, duplicate or malformed correlation replies rather than laundering
an arbitrary runtime field into private output.
