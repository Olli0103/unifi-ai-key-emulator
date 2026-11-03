# Explicit local Key face enrollment (3 November 2026)

Source/fictional UI work toward #20 and #13/#17. This does not enroll a real
identity, install a service, grant camera permissions, name a native Protect group
or establish recognition quality. It reuses the maintained local enrollment
transport and [model-space binding](face-model-space-20261103.md), not a new
recognition engine. The separate Port face space remains unchanged.

## Operator flow

The authenticated local identity page links to a separate owner-supplied JPEG
flow. It requires an already configured local face role, valid private store and
non-legacy model binding (or empty state). It cannot choose a new endpoint, enable
a feature, add a camera or fetch footage. An initially unchecked consent precedes
one request to the existing local service, followed by a private label/count-only
preview and another initially unchecked save acknowledgement. The original image,
thumbnail and vector are not displayed or exported. Discard saves nothing.

One bounded JPEG (10 MiB inclusive) and five short exact fields are streamed
without a temporary upload file. Unknown/duplicate/nested/encoded/malformed fields,
bad JPEG markers, missing consent and wrong origin/host/CSRF are refused before
a model request. Revisions are checked before analysis and again before saving;
a change after analysis cannot retract the completed model call. Upload reading has a 30-second limit. No proxy,
redirect, cloud fallback or automatic retry is introduced; the existing bounded
local transport requires exactly one valid 128-value vector and encoder space.
Different model spaces and nonempty unbound legacy stores remain refused.
Messages distinguish an absent role, legacy migration, capacity and model mismatch;
no private server address/digest is exposed or unobserved no-call claim invented.

## Owned work, authority and persistence

At most one upload/analysis is admitted by this controller. The owned operation,
not its HTTP waiter, retains admission until the actual synchronous helper ends.
When the existing private model-work marker enrolls this state, analysis holds
its shared lease; an exclusive writer blocks the call, and a broken marker is
refused without repair. The flow never initializes a marker, clears intent or
changes the operator. The maintained absent-marker legacy behavior remains;
this is not proof that every older CLI/other consumer joins the fence.

At most eight pending templates are held in RAM. Random one-use grants bind the
session, exact store/configuration revisions, private label, vector and space.
Two-minute timers clear pending vectors; discard, logout, service cleanup and
admitted sibling mutations clear them too. Managed cleanup is not physical memory
erasure. A request ending before confirmation never persists a template; an
already admitted model call/store writer remains owned and drained on cleanup.

Save rechecks session/deadline/grant identity after waits and configured audit,
consumes all sibling enrollment/deletion confirmations, and serializes the
configuration lock before the existing face-writer lock. The saved local role and
configuration revision therefore remain current through the exact-revision
FaceStore enrollment. A competing cooperating configuration writer waits; other
identities and all configuration bytes are preserved. No restart, provider/model
selection, policy mutation, journaled name/vector, original-image copy or Undo
backup is performed. Configured audit refusal stops admission; fixed action/result
codes omit names, tokens, paths, image bytes and vectors.

Known conflicts/refusals do not repeat analysis or save. A possible committed
write or post-save audit failure reports an uncertain outcome, with all old
grants consumed. Reconcile saved counts before deciding on a new action. Logout
cannot undo a write already admitted. Same-owner tools ignoring maintained locks,
manual restoration, original photos, native recordings/groups and server/backup
copies remain outside this transient-template lifecycle.

## Qualification

All inputs are fictional JPEG-marker bytes, vectors and labels in owned temporary
state. No real graph is loaded or inferred. The initial new fixture used the wrong
Admin API attribute and failed before exercising handlers; it was corrected with
setup cleanup retained. A virtual-clock fixture accidentally captured a later
reassigned variable; the fixture was corrected, not the expiry guard. One expanded
command named a nonexistent test file and ran zero cases before correction.

The final dedicated HTTP/lifecycle group passed **43 cases**. Earlier expanded
administration/store/model/fence groups passed 190, 111 and 230 cases at their
recorded checkpoints; these counts are overlapping, not additive or independent
reviews. Coverage includes real loopback consent/preview/save, append/sibling
preservation, legacy refusal, actual other-session refusal, stale/last-moment
configuration/store changes, namespace mismatch, capacity, audit boundaries,
possible committed outcomes/no replay, timer/discard/logout/cleanup, mutual
enrollment/deletion grant invalidation, retained model and store threads after
HTTP cancellation, real temporary kernel fence exclusion and configuration-writer
serialization, unsafe-marker refusal and stalled upload release.

The project-owned sandboxed Chrome runner exercises desktop and mobile with an
owned fictional local HTTP encoder double: unchecked processing/save consents,
discard, keyboard save of exactly one bound template, mismatch refusal preserving
saved bytes, escaped labels, no image file/audit payload/configuration changes,
no unowned browser requests, and collected profiles/servers/pending templates.
It records source-module hashes and screenshots; actual decoded relevant screens
are inspected separately. Browser automation is not a screen-reader audit or an
independent private-VPN/native deployment.

Final combined source/administration/fence/documentation qualification passed
**692 cases**. All 136 current source-module digests match the qualified browser
run. Eight decoded relevant desktop/mobile components were inspected; four
previously inspected preview/saved images are byte-identical in the final run,
and four final form/mismatch components were freshly inspected. A native required-
checkbox tooltip was hidden by focusing the label before the final form capture;
no product guard was changed for screenshot appearance. Whole-tree Ruff/diff pass.
All existing FaceStore and ConfigurationStore methods are AST-identical; new
preflight/serialized enrollment methods are additive. Existing deletion commit,
model worker/fence/upload modules and dependency bytes remain unchanged.

## Rollout boundary

The currently installed Admin258, old Key and old Face image are not upgraded by
this source work. Coordinate the [model-bound readers/server rollout](face-model-space-20261103.md)
before real enrollment: old readers cannot consume schema 2, and a new client
refuses an old server without encoder metadata. The separately qualified
[Face262 candidate](face262-candidate-20261103.md) does not contain these later
changes and its actual switch ended without effect. Do not relabel it as this
candidate or replay its completed attempt roots.

Native explicit-identity recognition, grouping/search/restart and consented
quality/false-match/retention acceptance remain `needs_evidence`; the broader
issues remain OPEN. No actual camera/photo/template/controller/model/service,
credential, scheduler, dependency or release change occurs in this qualification.
