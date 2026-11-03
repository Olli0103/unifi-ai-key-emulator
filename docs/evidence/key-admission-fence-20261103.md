# Key job acceptance joins the existing model-work fence — 3 November 2026

Narrow source/fictional-loopback progress toward maintenance #24 and the
coordinated readers needed by #20. The broader issues remain OPEN. No actual
service, camera, model, photo, template, controller or maintenance actor is used.

## Reproduced boundary

On parent `bcf41f2e34198864ce9d1f953906a2c8c7ed6f21`, a temporary exclusive
model-work writer prevents execution but not acceptance: a new submission is
acknowledged and queued with no shared lease. Conversely, a parked accepted
queue item does not exclude a new writer. Two negative tests reproduce these
distinct failures. A separate observation forbids HTTP and parks consumption;
it confirms the acceptance gap without alleging unprotected inference.

This matters for a future coordinated process replacement: an execution-only
lease is not an atomic new-job acceptance boundary. It is not evidence that any
production restart overlapped a job or lost a callback.

## Change

The worker acquires its maintained opt-in `ModelWorkFence` lease before a new
job can reserve a caption/test permit, enter the queue or return acceptance.
The original job deadline includes this cancellable wait, with an outer timeout
as well as the fence's own deadline. Current camera
eligibility, deduplication, journal capacity, queue capacity, drain and stop state
are checked again after the await, without another await before enqueueing.
Completed cached duplicates do not start new work and remain available.

An accepted job owns that lease through queue/caption-lane waiting, execution
and existing terminal handling. Refused or duplicate admissions release their
extra lease; cancellation before acceptance has no queue/journal reservation.
Stopped queued work also releases its lease. Before execution, the existing
inode binding and strict idle-record checks are reapplied: holding an old inode
does not authorize work after enrollment is retargeted or corrupted.

Fixed admission diagnostics preserve timeout, unavailable and binding-changed
categories through the existing native worker/request-AI/speech classifiers.
Unknown fence or OS exception payloads are not exported. Existing timeout text
remains compatible as `Worker admission timed out`.

A further synthetic race was found during implementation: a waiting admission
could resume after `stop()` flushed the queue but while session cleanup awaited.
Its dedicated negative test failed before the additional stopping-state check;
it now refuses acceptance rather than leaving an orphaned queue item.

## Qualification and limits

- **25 dedicated cases** cover both parent failures, waiting cancellation and
  expiry, unchanged original budget, concurrent duplicates and queue contenders,
  rejected reservations, cached completion, terminal release, caption-wait stop,
  stop/cleanup races, marker retarget/corruption, fixed/redacted diagnostics and
  absent-marker legacy behavior.
- The corrected expanded worker/device/speech/failure-note/phase/inference/fence/
  watchdog group records **356 passed and 86 skipped** locally. Unavailable optional media/model
  prerequisites are not counted as covered; complete Linux CI is a separate
  exact-head gate.
- No UI or browser flow changes. The fence implementation, writer fairness,
  watchdog actor, provider transports, models, identity store, configuration,
  dependencies and capability manifest are unchanged.

The initial whole local run is retained, not a PASS: it was interrupted after
5,274 passes, 118 skips and eleven failures when an existing failure-phase fixture
substituted an indefinitely waiting `acquire`. The moved admission needed its own
outer original-deadline timeout. That fixture now correctly asserts no accepted
job, journal row or invented failure note for an admission timeout. A separate
queue-expiry failure showed that entering `asyncio.timeout(0)` could reach a media
request before cancellation; an already expired queued job now raises immediately
before execution, retaining the existing `queue_expired` diagnostic.

Nine other failures were credential fixtures invoking unavailable SHA-512-crypt
support in the host's LibreSSL CLI. Another fictional decoder-start fixture had
no session; its separate narrow rerun passed, but its initial cause is not
established. No unrelated credential/ingress implementation or test is changed.
The first exact-head CI run was cancelled for this correction; a later successful
run must qualify the corrected head, not relabel the interrupted run.

An absent marker still creates no file or physical lock. This is cooperative
enrollment, not universal client admission, a new maintenance controller or a
finite-time idle guarantee. Continuous accepted work can defer a non-waiting
writer; no consumer is forcibly drained, paused or reset to create a window.
Client completion/cancellation still does not prove that remote server work has
stopped, so independent known-source service-idle/readiness evidence is required.

No native installation, cold backup/restore, interrupted-update recovery,
biometric quality or independent human review is claimed. The already qualified
source-269 candidates do not contain this later source change and are not
relabelled. Completed Face262 attempts remain terminal; none is replayed.
