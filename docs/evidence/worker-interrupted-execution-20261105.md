# Interrupted worker execution and replay fencing

The original operational issue [#24](https://github.com/Olli0103/unifi-protect-local-ai/issues/24)
remains OPEN. This change addresses one actual process-death boundary, not host
power-loss recovery, provider billing reconciliation or the full fault matrix.

## Reproduced boundary

A real child worker sent a generated image to a loopback HTTP model fixture.
After that server had received the request, the parent killed the child before
the response or callback. A fresh worker using the same state and task sent the
model request again. There was no journal record between execution start and
the later callback/terminal write. An hourly budget or a one-use camera permit
can independently refuse some retries, but is not a general execution witness.

## Change

Before execution, the worker writes `execution_started` with the existing
hashed job identity/fingerprint, operation and timestamp. File contents and
the containing directory entry are synchronized before any execution I/O.
Normal completion or a handled failure uses the existing terminal behavior;
callback-send uncertainty retains its existing separate state.

A restarted worker refuses a matching unfinished execution with the fixed
`execution_uncertain` diagnostic. Changed input still fails the identity guard.
The unfinished record is visible through worker status and is never rolled
over or treated as a completed result. Independent new tasks can still run.
No automatic retry, historical backfill, budget reset or guessed outcome is
introduced. A marker can conservatively remain even if a process died before
its first external call; absence of a callback is not proof of no side effect.

The marker is not a cross-process ownership lock. The deployment must retain
its existing single active worker/state owner. It cannot repair attempts that
predate the new marker or prove a storage device honored synchronization.

## Operator and rollback boundary

An `execution_started` record in a live worker can be active work. After that
owner has ended, preserve the original record and independently reconcile the
model/controller outcome. Do not delete it or change the task identifier to
force another attempt. The existing callback-uncertainty resolver does not
resolve execution uncertainty. No new general retry authority is supplied.

Older workers reject this previously unknown state rather than silently
replaying it. Before an old-image rollback, drain the new worker and inspect
unfinished records. If any remain, retain the compatible runtime and resolve
the original operation; do not restore an older journal merely to start it.
Existing terminal journal formats and archived replay markers are unchanged.

## Checks and limits

The regression uses actual child processes, SIGKILL, loopback HTTP and a fresh
worker process. It covers an image-description request and an unmetered native
caption-shaped request with a real generated MP4 decoded by FFmpeg. Restart
must leave the original marker unchanged and make no second model or callback
request. A directory-sync refusal must prevent media/model/callback I/O.
An old unfinished record, changed-input refusal and an independent successful
task check retention without a blanket deployment lockout.

The first continuous test fixture omitted its required absolute FFmpeg path;
that pre-effect failure was corrected and is not counted as a replay
reproduction. All media, identities and responses are synthetic. No production
service, real camera, paid provider, model policy or release artifact changes
are established by these checks.

## Isolated NAS kernel faults

A separate non-root, read-only-root, network-none Linux container ran the
179-module candidate against its own loopback fixture and an 8 MiB tmpfs.
Filling that tmpfs returned real kernel `ENOSPC` (28). The attempted job made
zero media/model/callback requests. After removing only the owned filler, a
fresh worker completed exactly one request and one callback. Corrupting that
fixture's terminal journal then refused startup; restoring its exact retained
bytes returned the previous result without another model request or callback.

The test container exited zero without OOM, was independently inspected and
removed. Eighty pre-existing containers retained their IDs, image pins,
running states, PIDs and start times. No productive state was mounted into the
test. This qualifies the tested kernel-write/refusal/restart boundaries, not
exhausting the actual NAS filesystem, arbitrary data reconstruction, host power
loss, external billing or native controller recovery.
