# Saved AI Port recognition-scope restrictions — 30 October 2026

The existing authenticated control site could restrict saved Key speech scope,
but had no corresponding operation for already configured Port face/sound
scopes. This adds removal only, reusing existing private-file, configuration
validation, optimistic-revision, shared-lock, history and atomic-save primitives.
No new framework, provider, model, network endpoint or permission-grant system
is introduced.

## Boundary

- Unknown/unconfigured roles, malformed/duplicate/new camera IDs and stale
  revisions refuse before a new apply lock/history is created. A no-op retains
  exact saved bytes. Valid restrictions preserve unrelated raw values and
  omitted defaults rather than writing loader normalization into other roles.
- Only the selected role's camera list shrinks; an empty selection removes that
  saved role. Other roles, paired streams, identity/trust, provider/cost settings,
  models, files and keys stay. The prior configuration remains private history.
- Two overlapping saves use the same revision and lock: only one commits; the
  other is stale. No new provider Undo record is made. An earlier provider Undo
  cannot restore removed scope; later provider-only Undo retains it.
- HTTP reads/posts require the existing session and actual Host/origin rules.
  Mutations also require CSRF, explicit saved-only confirmation, fixed profile/
  feature, revision and opaque HMAC selection labels. Labels bind session,
  profile, feature, revision and stored camera, not a caller-supplied MAC/path.
  Authority lost during preview cannot start apply. Already admitted file work
  is not promised cancelled by logout or client cancellation.
- Camera names are escaped; missing/ambiguous inventory labels never disable the
  whole-role removal option. No name/vector/secret/template, model request or
  camera media is needed for this settings operation. The audit uses fixed
  action/result codes and the allowlisted profile only.
- Post-commit IO/readback failure is uncertain, not “nothing changed.” Possibly
  committed settings/history are retained, with no automatic retry or undo.

## What this does not do

The running Port must be restarted separately. No service is stopped, no live
recognition/audio processing is synchronously revoked, no permission is added
and no evidence/key/model/backup is deleted. Sound-role removal leaves separately
configured speech/audio capture permissions untouched. This is not full identity
enrollment/privacy management, native browser/mobile acceptance or an ancestor/
hostile-same-user filesystem audit. Existing processor feature scopes are not
changed by implementing/testing the page.

The focused store/HTTP/undo/auth/audit/accessibility group passed 125 tests.
The first 52-check run had one fixture-signature failure and 51 passes; supplying
the response as well as body to the existing private-page assertion fixed the
fixture without dropping it. The corrected group passed 52 checks before the
expanded concurrency/session/expiry cases. Full-suite and separate exact-commit
gate results are reported at integration; these fixture counts alone do not
claim deployment or broad native/security acceptance.
