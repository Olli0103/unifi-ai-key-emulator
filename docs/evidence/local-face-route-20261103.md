# Saved local Face-service route — 3 November 2026

Source/fictional administration progress toward #13, #17 and #20. The broader
issues remain OPEN. No actual endpoint, camera permission, identity, template,
provider or running service was changed or contacted.

## Operator flow and scope

**Local face service** opens an authenticated page for an **already configured**
AI Key face-recognition role. It shows the saved service address and camera count,
not live worker authority. A missing role offers no change form and cannot be
created through this flow. Camera eligibility is not guessed from advertised
smart types, and no camera can be added or recognition enabled.

Only a numeric local HTTP root accepted by the existing enrollment transport
validator may be proposed. Private/loopback destinations remain distinct from
proof of operator control, no forwarding or no cost. Public, hostname, reserved,
link-local, unspecified, credential-bearing, non-root and malformed destinations
are refused before any network contact or save. No new URL parser or HTTP client
is introduced, and viewing/previewing/saving never contacts the endpoint.

The first form creates a non-mutating configuration preview. A separate,
initially unchecked confirmation authorizes only the server-side proposed address;
no caller-supplied apply destination, scope or arbitrary configuration is accepted.
Opaque grants are session-bound, one-use, monotonic two-minute and bounded to 16.
Cleanup discards pending grants. Current authorization, exact configuration and
result revisions, expiry, live grant ownership and configured audit are checked
again after asynchronous work and immediately before committing.

## Saved-only transaction and model boundary

The existing configuration writer lock, immutable identity, credential-route and
search-profile guards remain. The raw change set is restricted to
`face_recognition.server`; camera spelling/order, optional-field omission,
thresholds/native-camera behavior, enhancement, other roles and opaque metadata
are retained. A canonical no-op keeps exact bytes and creates no history.
Successful writes archive the previous private configuration and verify the
persisted revision. Concurrent same-revision changes have one winner.

The operation does not open identity records, copy/relabel/migrate templates,
load models, acquire a model lease, restart services or change Protect. Admitting
a save clears sibling route confirmations and pending in-memory enrollment
previews, including when the final write is later refused or uncertain; saved
identity files stay untouched. Existing enrollment and per-response worker
model-space refusal remain unchanged. No new encoder compatibility or quality
claim follows from changing an address.

The UI distinguishes immediate use of the saved address by new administration
enrollment requests from the running Key's old configuration until a separate
restart. Operators must coordinate all local readers/server before enrollment.
Configuration history retains old routes; no automatic Undo can restore older
camera scope. Re-entering an earlier route still requires a fresh preview and
confirmation against the current configuration.

A possibly committed write or lost saved-audit acknowledgement yields an explicit
uncertain outcome. Grants are already consumed; there is no automatic retry,
rollback, key cleanup or false claim that the old route is still active. The
operator must reload and reconcile the saved address. Private audit contains
only fixed action/result/profile fields, never endpoint, camera IDs, templates,
confirmation tokens or unrelated settings. Existing shared mutation admission
and owned file-operation draining cover both new POST routes.

## Qualification

- **64 new cases:** 30 saved-store and 34 owned HTTP cases. They cover exact
  one-field preservation, no-op, unsupported destinations, missing roles,
  ambiguous saved JSON, stale revisions, two-writer races, separate consent,
  session/origin/Host/CSRF, last-moment authority changes, grant capacity/expiry,
  shared mutation limits, audit refusal, uncertain outcomes and cancelled-waiter
  owned-write draining. Fictional invalid-template canaries remain byte-equal
  without being parsed.
- Expanded configuration/Key-face/store/enrollment/audit/mutation/API/secret
  group: **273 passed**. This is same-agent qualification, not an independent
  review, native deployment or real-provider observation.
- Owned sandboxed Chrome desktop/mobile: initial unchecked confirmation,
  cancel/no-write, keyboard confirmation, saved-field/template/order preservation,
  stale-preview refusal and resource cleanup. Both simulated local servers get
  zero requests. Runtime modules are bound to actual source hashes; captures
  require separate visual inspection, not DOM-only acceptance.
- Final new-feature/documentation/contributor group: **538 passed**, plus Ruff
  and diff checks. The 71 existing configuration-store methods remain AST-exact;
  Face store/enrollment/policy/worker/fence/protocol modules are byte-equal.
- Existing five-job CI gains one owned fictional browser scenario and exact
  screenshot/report artifact paths; it does not gain a sixth workflow job.

Preserved preparation mistakes: an initial workflow patch used the wrong spelling
of the existing browser shell variable, and a runner lambda assignment failed
Ruff. A newline-chained fictional browser command still ran, so that initial
result is not treated as a complete CI/static gate. The exact workflow paths and
runner style were corrected separately. An initial expanded test command named
a nonexistent generic API test file; no tests ran, then the discovered actual
configuration-API file was used. No production actor or private state was involved.
A transient native required-checkbox bubble in captures was handled by dismissing
it and waiting for its close animation, without changing product validation.

The previously qualified three [model-bound candidates](model-bound-candidates-20261103.md)
remain frozen at their earlier source and **do not contain this UI feature**.
No candidate rebuild/activation, new permission or real biometric operation is
part of this change. Actual coordinated rollout, owner client UX, native grouping,
recognition quality, cold restore and sustained operation still need evidence.
