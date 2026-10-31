# Unplanned NAS kernel panic and partial recovery — 31 October 2026

This is a real interruption, not an agent-triggered reboot, a clean upgrade or
a completed recovery PASS. The native and host observations below are separate.
Issue #24 remains OPEN.

## Before the interruption

Four existing Ports had nine active streams and unchanged container state.
Six recent completed-send fingerprints matched six stored native events on two
original cameras, with no duplicate association or wrong-camera match in that
selection. Four had native RAM done state; two had failed RAM despite nonempty
descriptions and completed deterministic local caption-job records. Their cause
is **not established**. The observation is not an outside-zone, physical-person
or general no-cross-camera quality control.

The event-list response had 50 rows under a requested 1,000-row bound. Effective
pagination was not independently established before the interruption. Do not
call it a complete event inventory or use it to prove global absence of
duplicates. The selected positive matches remain bounded observations.

Existing failure notes separately identified unusable speech replies. One
generated 3.897-second speech fixture was sent once to the already selected
local backend, with its model-work fence held. It parsed successfully, so it
did not reproduce those historical failures. No camera audio, controller media
request, callback, retry, new model, credential or cloud route was involved.
The local result receipt was written at 06:11:03 UTC. The later kernel panic's
cause is unknown; this successful request does not rule out a driver/backend
relationship. The investigation made no further standalone inference request
after the interruption; normal processor work is a separate activity.

## Observed reboot and native continuity

The appliance's reset-reason record reports **kernel_panic**, with boot time
06:12:07 UTC. The protected kernel trace and system journal were not readable
through the current user; no specific kernel subsystem or causal workload is
claimed. No reboot, host-service stop or container restart was issued by this
work before the failure.

During Docker startup, Protect retained all five original adopted identities
and nine pairings, with zero processors connected at the sampled point. All
eleven cameras reported recording. At 06:16:25 UTC, all five processors were
connected again, with the same identities/pairings and eleven recording flags.
These flags are not decoded playback or proof of uninterrupted footage.

A later host read compared six processor/database container IDs and images to
the prior qualified receipt: all match, all run, and process IDs changed after
boot. All five saved profile hashes match. Persistent administrator authority
and TLS bytes match the earlier NAS archive. No old state was restored, and no
new identity, camera scope, provider, model or permission was configured.

## Two real recovery gaps

1. The existing enabled private-network daemon failed at boot. The separate
   Admin repeatedly exited; its fixed VPN bind was unavailable (`EADDRNOTAVAIL`).
   The existing user cannot start that system service or read its protected
   journal. An administrator start was requested. HTTPS/bind policy, credentials
   and the VPN configuration were not changed; no wildcard/HTTP fallback was
   introduced. This access path is not recovered or qualified by this record.
2. The lingering user manager started at **06:12:22 UTC**, but the NAS `/home`
   mount became active only at **06:12:39 UTC**. Three existing enabled project
   timers were inactive and the already active `timers.target` had no wants.
   The enabled symlinks and files existed after the mount. Reloading the user
   daemon changed its target wants from empty to precisely those three timers;
   explicitly starting them restored their active states. No timer definition
   or cadence was edited. Service/downstream outcomes are checked separately.

The optional [home-readiness integration](../nas-boot-recovery.md) addresses the
second gap using a bounded pre-start mount check. It is not installed on the
live host and has not been proved by a later boot. It does not repair the VPN
daemon, explain the kernel panic, complete restore/soak/quality gates or close
the broader operational issue.

At 06:30:14 UTC the three recovered timers were active/enabled. The Key monitor's
latest downstream heartbeat was **UP**; the separate Admin monitor was **DOWN**.
The watchdog's latest service completed successfully with `busy` or
`consumer_waiting` and `legacy_invoked: false`, preserving the model-work fence
instead of restarting a busy backend. The certificate service had no new
execution in this boot; an active timer is not renewal proof.

## Isolated guard qualification

The exact fragment SHA-256 is
`4f5850fc6015a32d5a0b0af74b41b0f5de8157898a5c3593dfb46b59491809d1`.
The host's `systemd-analyze verify` accepted its merge with a fictional service;
that unit was neither installed nor started. The literal pre-start command
passed against the existing mounted home without output or mutation.

Twelve isolated native Linux cases passed: ready, delayed mount, missing mount
and TERM cancellation under each of `/bin/sh`, Bash and Dash. Missing-mount
cases used the same GNU timeout with a shortened 350-ms fixture deadline and
returned 124; cancellation exited on TERM. Recorded children and each owned
process group were absent after settlement. This does not claim a literal
300-second wait was measured or a real mount was delayed/unmounted. Fixture
probe/sleep executables and disposable parse-only units were collected; no
production unit file, home mount, user manager or processor was replaced.

## Evidence boundary

The private receipts retain the original failures, pre-interruption selection,
synthetic probe, post-boot inventory, integrity comparison and service states.
Public artifacts contain no device/camera identifiers, linkable fingerprints,
credentials, private addresses, caption text, transcripts, images or kernel
memory. Same-agent checks are not independent human or hardware acceptance.
