# Threaded AI Port work during shutdown — 29 October 2026

## Reproduced boundaries

Cancelling `asyncio.to_thread`'s waiter does not stop the synchronous detector or
provider call. Previously `FairInference.close()` cancelled its worker and
returned while an actual model load or request could still be running. A
cancelled `join()` also cancelled that worker and could hide the remaining thread
from subsequent shutdown. Neither wrapper completion proves a paid request
finished or never happened.

Separately, the service emitted final native leaves before closing inference.
A deliberately interleaved synthetic observer could reopen a session during
that leave, leaving a new session after stop completed. This is a source race
reproduction, not attribution of a historic recording failure to this cause.

## Correction

- Track the actual model-load/detection thread task separately and shield it
  from async-waiter cancellation. Cancelling `join()` stops only that waiter,
  not ownership of scheduled processing.
- `close()` immediately seals admission and drops queued/confirmation frames,
  then awaits the actual thread. No retry or new model request is started.
  A model load completed after sealing cannot start detection.
- A cancelled close caller propagates cancellation; another caller can still
  await the same operation. Shutdown does not expose raw thread exceptions.
- Only after thread completion may async result delivery be cancelled. A stuck
  async observer remains cancellable; it is not an uninterruptible model thread.
- The service closes inference **before** its final native leaves, preventing
  the old in-flight observer from recreating a session during that cleanup.

The snapshot's `closed` flag means admission is sealed, **not** draining complete.
Await `close()` for that boundary. Provider deadlines remain unchanged; no
fallback, budget, camera scope, model, sensitivity or provider policy changes.

## Verification

Seven real-thread/async cases use invented frames and fake detectors with no
external/model-server requests:

- Running detection (success and late provider failure) holds close until the
  thread finishes; queued and newly arriving frames never run.
- Close during model loading waits without starting detection afterward.
- Cancelled close propagates cancellation; a second close awaits the same work.
- Cancelled join cannot hide running work from close.
- A blocked async observer is cancellable after model completion.
- An interleaved observer cannot reopen a native session during shutdown leave;
  exactly the existing enter/leave remains and no session is left open.

Six cases fail on original source; async-observer cancellation is an existing
positive control. All seven pass after correction. The focused inference,
camera-engine and candidate suite passes **174 tests**; Ruff/diff checks pass.

## Limits

No running service was redeployed for this correction. The preceding
[four-Port upgrade](aiport-source-upgrade-20261029.md) independently drained old
workers before stopping them. This is not a cold restore or host-crash test.

A hard process kill, power loss, entire-event-loop cancellation or genuinely
stuck native model cannot be made safe by cancelling a Python wrapper. Shutdown
may wait for that call. Operators must allow real client/model work to finish
and distinguish uncertain charged outcomes from confirmed completion. No
duplicate-charge, unattended-recovery, universal shutdown-time or native
alarm/quality acceptance is claimed.
