# NAS reconciliation intent and interruption boundaries — 4 November 2026

Scope: the source-side NAS apply remainder of issue #45. This is not the
Mac/NAS/Protect creation, camera-removal or recording-continuity acceptance.

## Reproduced baseline

On main `c9fe54a70209e85689d06cf4aee532ccc4fc0d37`, a synthetic invocation of
`reconcile(..., apply=True)` without a host authority returned `applied` and
issued one start command. The original failure path stopped service names and
ignored stop errors. The new public entry refuses missing durable authority
before any Docker call.

## Implemented boundary

- Shared host writer, existing configuration mutexes and startup reservations.
- Original durable operation, configuration/identity digest and private recipe;
  retained prior receipts, no restoration of old private state.
- Explicit local Docker Unix socket and Compose project, no saved remote context.
- Create without starting, verify operation ownership, then start by container ID.
- Exact-ID rollback only for positively observed starts. Unacknowledged daemon
  requests, failed or unobserved stops retain an uncertain intent and block retry.
- Existing command guardian retains the inherited host lease through child
  collection and publishes command-bound terminal receipts.
- Explicit original-operation settlement requires matching identity and positive
  postconditions, not absence or an assumed successful CLI return.
- Health reads target the inspected container ID and retain the original TLS pin.

## Qualification and retained failures

Synthetic tests cover positive start/no-op, lost ACK, failed health/stop,
replacement-container refusal, recipe modification, missing/stale admission,
namespace selection, runtime layout and command allowlisting. Intermediate
focused groups passed 126 and 59 cases (overlapping; do not sum them).
The full initial local run was interrupted after nine obsolete namespace/CLI
fixture failures and 2,423 passes; it is not a full PASS. Those fixtures now use
the durable admission path and continue checking the selected namespace.
Final full-suite and CI qualification are recorded with the PR, not inferred
from these focused groups.

An actual Linux NAS Docker 29.6.2 fixture exercised the final guardian's
**Compose create → ID start → ID stop**, including three persisted terminal
command receipts and separately inspected postconditions. Its container had
network mode `none`, no data mounts, a read-only root, non-root user and dropped
capabilities. It was removed afterward; IDs, images, PIDs and restart counts of
all 41 pre-existing running containers were unchanged. No existing service was
recreated, reconfigured or stopped.

The first fixture attempt failed closed because the NAS assigned mode 0777 to
the test authority/lease despite requested private creation modes. The fixture
was collected; the subsequent own test files were explicitly made private.
The final run used the same strict permission guards. Native Compose help also
showed that `create` does not accept `--no-deps`; the generated manifest contains
no dependencies, and the final command omits that unsupported flag.

## Not established

This fixture proves the Linux Docker command/guardian boundary, **not** a full
real AI Port reconciler apply, adoption, pairing, media/recording continuity,
quality, actual host power-loss recovery or long-term operation. The Mac host
still lacks its container runtime. No original issue checkbox is closed by
these results; issue #45 remains open. No model request or public release was
performed.
