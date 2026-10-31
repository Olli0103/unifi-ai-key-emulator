# Cooperative model work and watchdog maintenance

## Reproduced operational boundary

An existing minute watchdog submitted a small synthetic image to the local
model server and restarted it after repeated failed probes. A real Key caption
was still running when such a restart disconnected its model request. The
watchdog recorded two failed probes without either of its known GPU-resource
or descriptor-exhaustion markers. The model server's shutdown, client transport
failure and subsequent start were correlated; this does not retrospectively
identify the causes of older already archived failures.

A before-restart idle poll is insufficient: a job can arrive after that poll.
The opt-in fence uses existing POSIX shared/exclusive kernel locks, not a new
model gateway, listener, paid probe, retry, library dependency or provider policy.
This source does not activate the private operator workflow by itself.

## Participants and operator setup

Enrollment is the operator-provisioned private `model-work.lock` marker in a
runtime's state directory. Key workers and live multi-camera AI Ports recognize
it; absent markers leave legacy behavior unchanged and are never automatically
created. Unsafe/broken markers fail closed rather than silently disabling a
participant. No configuration/policy field, browser setting or restart authority
is added. Other Port modes refuse enrollment. Older images ignore this separate
file; the new watchdog refuses legacy/unenrolled peers, preserving image rollback
without injecting unknown fields into old configurations.

An operator first pauses the old watchdog and captures its script, units,
state and application baseline. While it remains held, initialize one private
`model-work.lock` using `aikey.model_work_fence.initialize` and hard-link that
**same inode** into each enrolled runtime's private state directory. The canonical
host directory must be outside all consumer state roots. This is one kernel
and filesystem, not distributed locking across Mac/NAS or independent replicas.
The NAS/container/filesystem combination must be tested, not inferred.

Roll out only qualified source/configuration with verified backups and retained
identities. All consumers must report the same retained descriptor binding before
the watchdog can run. A copied file is not a shared lock. Separate restores or
recreated lock files require deliberate re-enrollment with the watchdog held;
do not silently create fresh files or delete unresolved intent. Backup tools
must preserve/document this relationship; regular per-state backups alone do
not prove it is restored. The existing state-backup preflight inventories the
private regular marker but refuses a generic restore-success claim until its
separate re-enrollment requirement is addressed. Ordinary image rollback retains current state and
holds the watchdog if old consumers no longer report enrollment.

The host watchdog configuration pins enrolled container images and state mounts,
the existing script digest, metadata-client image and exact provider image/IP/
model. Configuration and script snapshots are private, bounded regular files.
Execute `tools/model_work_watchdog.py --config /private/operator.json` with the
qualified package on its Python path. Preserve the existing timer cadence and
allow a finite service timeout covering the 90-second legacy execution plus
90-second readiness window and participant checks. Never resume before all
participants and actual kernel-lock behavior are verified.

## Ownership and failure ordering

- Each Key job gets its own shared-lock open description inside its original
  deadline, before media/model processing, and holds it through its existing
  completion/error handling. It does not extend a budget or authorize replay.
- Each live-pool detector load/call also gets an independent shared lease.
  Cancelling the async waiter does not release protection around a still-running
  model thread: that actual thread's completion releases its lease.
- Host maintenance takes a non-waiting exclusive lock. Existing readers mean
  **busy**, without a probe, restart or consumer-queue mutation. It verifies all
  configured participants through their pinned image/state and existing local
  certificate-verified health endpoints. Legacy, changed, missing or split-inode
  participants refuse maintenance. Already waiting consumers get priority.
  Each consumer must also observe the host's exclusive lock as `writer_active`;
  equal binding digests alone are not accepted as proof of coherent locking.
- Before invoking the exact existing script, the writer fsyncs a maintenance
  transaction into the shared inode. It holds exclusion across that script and
  verified provider metadata/per-model readiness. A new reader arriving between
  any idle observation and restart cannot enter model work.
- Only normal script completion and verified readiness finalize that exact
  transaction as idle. Cancellation, owner loss, malformed/torn state or
  uncertain effect leaves maintenance intent. Losing a process/kernel lock is
  **not** permission to clear it or repeat a restart. Reconciliation is separate;
  there is no TTL, force-unlock command or automatic recovery in this version.

The old script's probe policy/cooldown remains explicit operator policy, not
inferred inference health. The wrapper does not add another generation request.
After the old script returns, readiness uses only `/v1/models` and the existing
`/v2/models/<model>/ready` metadata routes. Those reads establish registration/
readiness, not generation, caption quality or complete GPU recovery. Script
success alone is not downstream success. Its anonymous executable snapshot
cannot be replaced by retargeting the original path after verification.

## Scope and limits

This is cooperative fencing for the enrolled, qualified applications. Arbitrary
external clients, direct host/root mutations, unenrolled modes and unrelated
model services are not covered. Enrollment/config/image changes must themselves
hold the watchdog and preserve its unresolved work; operator mutation is not a
way around an active fence. A cancelled/abandoned HTTP call can leave server-side
work; no server-completion guarantee is inferred from client cancellation.

The record is bounded, no-follow and same-user 0600; it contains a schema, phase
and transaction nonce, not media, text, camera IDs, URLs or credentials. Health
reports fixed counters, maintenance intent/lock observations and a linkable local
inode-binding digest, not paths or current model correctness. An `idle` intent
does not mean no shared work is running; reader leases are counted separately.
File I/O is not forcibly preempted. A partial write
is unavailable; an uncertain final fsync may leave readable idle bytes after
effects have already been verified. This is not universally crash-/power-loss-
tested recovery or secure deletion.

Unknown maintenance can block or exhaust a waiting job's original deadline.
It must surface as unavailable/timeout, not fabricated detections, inferred
success, automatic paid retry or weakened replay protection. Preserve and
investigate the transaction rather than deleting it to make a page green.
Continuous shared work may defer this non-waiting watchdog indefinitely; writer
fairness or a bounded whole-system recovery time is not promised by this version.
