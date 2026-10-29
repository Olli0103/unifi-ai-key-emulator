# Queued API failures and diagnostic quotas

Work toward [#79](https://github.com/Olli0103/unifi-ai-key-emulator/issues/79), 29 October 2026.

An isolated regression reproduced a quota bypass in `FairInference`. With a one-attempt camera quota, API confirmation-frame retention and frames queued before the worker started, a failed API request could be followed by a second request for that camera. The success path cleared queued frames at the limit; the API-failure path did not.

Quota completion now runs after both successful and failed API attempts. It drops both pending-frame slots, clears confirmation priority and reports that exact camera unavailable once. Other cameras keep their own queue and allowance. Non-API camera failures still use the existing disable path without duplicate notifications.

The regression fails before the fix, then passes. A separate sustained five-camera test keeps all queues busy with positive results and verifies that one confirming turn cannot starve another camera. The first version of that test incorrectly compared formatted MACs with normalized ones; the corrected fixture passed on the unchanged scheduler. That fixture failure is not evidence of a scheduler fairness defect.

Validation after the fix:

- Inference, camera-engine and candidate tests: 162 passed.
- Full suite: 1,939 passed, 109 skipped, 56 subtests passed, five existing warnings.
- Ruff: passed.

This does not introduce a request cap for uncapped continuous operation, change provider policy or deploy a running AI Port. Native timeline/playback acceptance and automatic instance provisioning remain separate work. Issue #79 is not closed by these synthetic checks.
