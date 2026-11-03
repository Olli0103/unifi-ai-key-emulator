# CLI enrollment joins existing model-work fencing (3 November 2026)

Narrow source work toward local identity lifecycle #20 and cooperative maintenance
#24. Only fictional bytes/vectors and temporary kernel locks are used. No real
photo, template, service, native controller, model request or operator is touched.

## Confirmed boundary

On unchanged source `71723bfc89b624159140b1b32001dbd0f3be1af9`, the owner-invoked
CLI enrollment reaches a fictional model double and saves a fictional template
while the same temporary state's exclusive maintenance writer holds the model-work
lock. The CLI did not consult its existing opt-in marker. This is a controlled
counterexample, not an observed production overlap, disclosure or native outage.

## Change

Only CLI `enroll` constructs the maintained `ModelWorkFence.from_state`. It waits
up to ten seconds for the existing shared lease before reading the photo or calling
the existing local enrollment transport. Broken/unsafe markers fail closed with
fixed diagnostics; a missing marker preserves the maintained legacy behavior.
No lock/intent is created, replaced, cleared or automatically recovered.

Lease acquisition runs on a short-lived event loop, then the existing HTTP helper
runs synchronously on the owning CLI thread. There is no executor HTTP waiter that
could be cancelled while a detached local helper continues. The lease is closed
in `finally` when that synchronous helper returns or unwinds, including a normal
error or keyboard interrupt. The fence's independent file descriptions and
binding/intent rules remain unchanged. The bounded advisory wait does not spend
its deadline on a photo read or send a model request while waiting.

This is a **model-work** lease, released before the separate FaceStore write.
Store writes keep their existing private serialized/atomic behavior; this does
not turn the fence into a lock for all metadata. No enrollment retry, live policy
change, marker enrollment, fairness change or forced maintenance window is added.
Read-only listing and explicit metadata deletion/purge never acquire this model
fence. CLI main remains a synchronous entry point, not an async application API.

## Qualification and limits

Temporary-state checks cover a real shared lease during the fictional synchronous
model request, exclusive-writer waiting, failure/keyboard-interrupt cleanup,
malformed/symlink/permissive-marker refusal without repair or path disclosure,
forced expired admission before a missing image is read, absent-marker legacy
behavior and non-model commands not constructing a fence. Existing local transport,
model-space and model-work cases are included in the initial **109-pass** group.
After integration onto merged #265 source
`f3c3082f16e39e11916a68380658ca8d9123f504`, all **12 dedicated cases** and
**640 combined transport/model-space/store/enrollment/fence/documentation cases**
pass. All non-`main` face classes/functions, including the new metadata preflight,
are AST-identical; the maintained fence bytes and dependencies are unchanged.
Whole-tree Ruff/diff pass. The owned source patch was preserved through an exact
VCS checkpoint and its CLI-main AST verified after base integration.
These are source/fixture results, not native deployment or an independent review.

A CLI process may exit or be killed, and a remote HTTP server can continue work
after an uncertain transport failure. This change does not prove remote inference
has stopped when the caller unwinds; qualified service idle/readiness checks and
known-source counters remain necessary before maintenance. It is not universal
consumer admission, starvation repair or permission to repeat a completed native
update. The separately completed Face262 attempts remain terminal with zero
service effects. No actual writer/rollback/renewal/GPU operation is invoked here.

Model binding, local-only transport, thresholds, vectors, CLI output on valid
legacy use and FaceStore semantics are otherwise preserved. No dependency, camera
permission, configuration, native source/image or release changes are included.
Broader issues remain OPEN pending their actual native and lifecycle evidence.
