# Roll out continuous captions

Use a staged rollout for the selected camera/model policy. This page is a general
operator checklist, not a private installation's journal or an instruction to
replay earlier experiments. Start with [provider setup](../providers.md),
[camera inventory](../camera-inventory-preflight.md) and [caption budgets](../caption-budget.md).

## Before activation

Check the running source/configuration revision, Protect compatibility, exact
camera allowlist, provider destination, model identity and request budget.
Back up current private state and resolve uncertain callbacks before admitting
new work. Do not archive an uncertain callback merely because it is old, and do
not replay a completed request to test whether the connection works.

Preflight output is advisory. Read the runtime's actual admission gates and
reported version. A saved setting, controller API acknowledgement or budget
counter is not native caption persistence. Historical version allowlists and
local image tags are not current configuration instructions.

## Start with one camera

Choose one event and a narrow processing scope. Record the intended provider and
maximum paid-request exposure before starting. Verify the resulting caption on
the exact original event in Protect after refresh. Keep caption content and all
identifiers private. A model reply or HTTP acceptance alone is not a saved result.

Test onboard-camera and AI Port-sourced events separately. Success on one path
does not qualify the other. Add another camera only after the first native result
and the unchanged prior configuration have been checked.

## Budget and stop conditions

Use the configured rolling budget and its durable journal, not process-lifetime
counters, to measure admitted requests. Review fairness, queue/ledger capacity,
provider backoff, uncertainty and callback failures. Do not widen limits to hide
a failure or refund an uncertain attempt without evidence.

Pause admission if the agreed error, queue, cost or latency threshold is crossed.
Let already accepted work reach a known outcome before replacement or retry.
Keep the original failure and reconciliation outcome separate.

## Observe and accept

Measure a defined interval with positive and negative cases, missed tasks,
failures, resource use and actual saved native results. A short trial is not a
24-hour or sustained-load result. Keep per-family and per-source outcomes separate.
Restart recovery and full restore need their own checks.

## Rollback

Restore the reviewed processing policy, not an old identity or credential snapshot.
Retain journals and reconcile possible side effects before retrying. Widening or
removing the application does not delete controller recordings, provider data or
backups. See [pause/uninstall/restore](../uninstall-restore.md) and the
[native acceptance checklist](owner-acceptance-gates.md).
