# Private administration JSON API

Work in progress for #13. These version-1 endpoints adapt the existing private
administration listener and configuration stores. They are **not** the device
service, a new network listener, a public diagnostic export, a generic file
editor or a command-execution API. Identity/pairing/host edits, live operational/trust
status and supported backup/restore/update actions are not yet implemented by
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
| `GET /api/admin/v1/profiles/{profile}/configuration` | Masked configuration plus saved role registry, both from the same captured revision. Explicit `saved_not_active_runtime`. |
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

## Remaining #13 work

These local checkpoints do not close #13. Complete saved versus actual
identity/adoption/trust/job/stream observations, native
controller-owned unsupported status, authenticated supported maintenance
operations, their approval/preflight/recovery boundaries, full original scope
and rejection/concurrency qualification before the integration PR. No unqualified
backup/export/restore/update/PTZ/recording operation is advertised as available.
