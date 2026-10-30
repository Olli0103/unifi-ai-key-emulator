# Continuous paid-caption acceptance (#12), 30 October 2026

This closes the original budget/scheduler evidence gaps, not detection quality,
an independent installation, controller-restart persistence or a physical soak.
The change adds integration evidence only; production code/configuration is
unchanged. No live media or real model service was used for these checks.

## Twelve complete captions and restart

`test_continuous_real_loopback_callback_and_duplicate_no_extra_charge` now runs
twelve different events, alternating two synthetic cameras, through the actual
`JobProcessor`, durable reservation/journal files and owned HTTP media, provider
and callback endpoints. Only video-frame decoding returns a fixed synthetic PNG.
Each result must be processed, have an accepted callback and exactly match its
callback RAM payload, camera and event. The thirteenth new task is refused before
another media/provider/callback request. After worker destruction/recreation on
the same state, the thirteenth is still refused and all twelve completed results
are reused without another charge or callback. Counters remain exactly twelve.

This is application/HTTP evidence, not proof that a fake HTTP callback persisted
in Protect. That separate original native criterion already has the dated
[two-family reload evidence](live-protect-7.3.74.md): G3/G4 captions remain after
helper-page reload with private exact worker/event joins. No private join or
description is exported here; no controller reboot is imputed.

## Automatic retention across virtual days

`test_four_virtual_days_of_real_callbacks_roll_over_without_manual_reset` runs
**96 hourly batches / 1,152 successful synthetic provider calls and callbacks**,
two cameras, with worker stop/recreation at every hour. Wall time advances by
3,601 seconds; monotonic callback deadlines remain real. This is four **virtual**
days, not four elapsed days or a NAS endurance claim.

The active journal is deliberately capped at thirteen entries (one spare lets
the thirteenth request reach the paid-budget gate). Every hour's thirteenth
request must be budget-refused; the next hour proceeds automatically. Each
completed prior batch moves to private replay tombstones. The active journal
stays at twelve and then rolls to zero on the final later restart. The durable
budget's distinct **24-hour** replay retention plateaus at 288 records, below
its 300-record limit; its paid-admission window remains one hour. All 1,152
tombstones remain, and replay of the first event after budget retention expires
still returns `already_completed` without another model request/callback.
No manual permit deletion, journal rewrite/aging or budget reset is used.

Initial new-test fixture expectations were corrected, not production guards:
an active cap of twelve rejected at the journal before reaching the budget;
budget records actually retain 24 hours, not one; startup at the final later
hour automatically archives the last batch too. Earlier receipts are retained
privately; these are not reproduced product failures.

## Other original criteria

- [Budget tests](../../tests/test_caption_budget.py): process-concurrent admission
  (eight processes / 32 attempts), global twelve, restart, exact expiry,
  duplicate identity, clock rollback, corrupt/unsafe files and uncertain writes.
- [Continuous admission](../../tests/test_continuous_admission.py): reservation
  before media, orphaned reservations cannot replay a paid call, inventory
  revocation, busy-camera fair headroom, visible budget/fair-share reasons,
  explicit metered/unmetered policy and summary charging.
- [Worker/inference tests](../../tests/test_worker_inference_gate.py): bounded
  queue, waiting captions counted, local/summary priorities, cancellation and
  stop collection. Actual worker deadlines include queue wait; timed-out jobs
  do not dispatch a late callback. This does not establish a new supported
  controller version or an urgent real-world alarm result (#26).
- [Rollover](../../tests/test_worker_rollover.py): private tombstones preserve
  replay decisions, archive failure refuses admission before charging, corrupt
  tombstones fail closed and uncertain callbacks remain visible for review.
  Unresolved work filling capacity is an explicit refusal, not silent deletion
  to admit more paid calls. Tombstone expiry is a separate read-only plan, never
  an unverified automatic loss of deduplication.

Paid reservations do not gate the separate Port base-detection/alarm pipeline.
Within the Key, local operations bypass caption reservation; overall bounded
queue/ledger capacity still applies. This is not a promise of unlimited storage
or that every failure can be retried without operator review.

## Runtime boundary

Read-only comparison of the current NAS Key found `worker.py`,
`caption_budget.py` and `camera_registry.py` byte-identical to the tested source.
The archive module differs in later read-only inventory/UI scan budgets; the
worker-used `valid_tombstone` function's AST is identical. No Key, PostgreSQL,
Port or admin image/configuration was changed for this evidence-only PR.
Full validation and exact frozen head/CI results are recorded in its PR before
merge and in #12's acceptance read-back.
