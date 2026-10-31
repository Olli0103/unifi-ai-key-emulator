# Observed worker failure phases — 31 October 2026

This bounded source change addresses a diagnostic blind spot: a timeout could
be recorded while a job had already exhausted its admission deadline in a
queue, while waiting for the inference slot, or during a request. Existing
metadata cannot distinguish those boundaries retrospectively. This is not a
repair claim for a production backend or a reason to replay failed work.

Implementation uses the existing bounded private failure-note store and Python
standard-library contextvars/contextlib; no external service, dependency,
telemetry, raw-log reader or new storage system. New exact schema-2 notes add
one finite phase enum. Strict schema-1 reads and first-note filename identity
remain supported; neither existing notes nor journals are migrated/deleted.
See [the schema and its limits](../worker-failure-notes.md#version-2-observed-exception-boundary).

Qualification covers both versions, malformed/extra/unbounded fields,
first-note immutability, per-task/per-worker isolation, timeout cancellation,
recovered/implicit-context errors, active-inference preservation on gate
waiter timeout, expired queued work without a second model request, fence
waiting without network work, real owned-loopback media/model/callback errors,
truncated model response, and preservation of the original decode failure
through an accepted or uncertain on-demand error callback. Existing replay,
callback-uncertainty, private file, capacity/lock, restart and success/no-note
checks remain required. These are same-agent synthetic checks, not independent
review, native persistence/quality acceptance or a live activation claim.

No policy, model, budget, deadline, admission, scheduling, camera assignment or
network destination changes. No real inference, callback, media fetch or job
retry is needed for this qualification. A real worker rollout requires a new
safe deployment attempt and current natural-idle evidence; previous busy
refusals cannot be reused as permission to stop an active worker. Broader
administration, sustained-operation and native compatibility issues remain OPEN.
