# Saved AI Port recognition-scope restrictions — 30 October 2026

The existing authenticated control site could restrict saved Key speech scope,
but had no corresponding operation for already configured Port face/sound
scopes. This adds removal only, reusing existing private-file, configuration
validation, optimistic-revision, shared-lock, history and atomic-save primitives.
No new framework, provider, model, external transport destination or permission-grant system
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

## Native read-only qualification and admin-only rollout

The final source `da2b27515122ae395afa2f3e517bf788532f4594` and its merged main
passed all four required CI jobs: **3,051 tests passed, three skipped**. The
native Mac full run passed the same number on the preceding functional head;
the final one-line documentation clarification leaves all 92 production-module
bytes identical. A separate same-agent, source-derived external fixture gate
passed 74 assertions on the exact final head, with no unowned transport/DNS
attempts or fixture/task/thread residue. This is not independent human review.

A new private candidate image was built and its complete 92-module file set and
license checked without a network. A uniquely owned temporary container then
read the four **actual** Port profiles with read-only mounts and no network.
It displayed eight configured feature panels, with saved camera counts:

| Port slot | Face processing | Sound classification |
| --- | ---: | ---: |
| 1 | 3 | 3 |
| 2 | 2 | 2 |
| 3 | 2 | 2 |
| 4 | 2 | 2 |

These are permitted-camera counts, **not** recognized people, templates or
quality results. Anonymous access, bad CSRF and an unknown-profile POST were
refused. No valid permission operation or model request was admitted. The proof
signed out and collected the owned server/container. Five config bytes, three
administrator authority files and all seven existing containers were unchanged.
Inventory names were not configured, so individual camera checkboxes were absent;
the whole-role controls remained available without exposing camera MACs.

After merged-main CI and byte-identity checks, only the separate administration
image was replaced. Its existing compose configuration changed solely in the
image field; hardening, mounts, command, resource limits and administrator
authority remained. The new service became healthy, refused the old session,
accepted the unchanged persistent credential, displayed all eight feature panels
and signed out. No permission/provider apply was admitted. Five profile bytes,
the six processor/database containers and the current authority's verified
recovery archive stayed unchanged. The older admin image was retained.

Fresh Protect reads before/after retained the original one-Key/four-Port
identities, nine pairings and five adopted/connected processors. No processor
restart, credential replacement, model activation, new camera scope or archive
deletion was used. This checkpoint still used NAS-loopback-only administration;
private HTTPS accessibility is a separate follow-up. Native browser/mobile,
actual saved-policy change plus Port restart/live enforcement, enrollment,
deletion, retention and sustained-operation acceptance remain open.
