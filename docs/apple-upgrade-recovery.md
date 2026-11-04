# Fenced same-host Apple image updates

This operator belongs to the interrupted-upgrade work in #16/#24. It is not a
complete product updater, signed publication, native Apple qualification, NAS
Compose updater, full backup restore, or permission to retry charged work.

## Authority and observation

The supervisor, update actor and existing instance reconciler share the private
host writer. The control root cannot overlap any observed guest state mount.
Pins must have distinct container names, identity roots and published endpoints;
a rejected pin leaves the existing registry byte-identical. Each planned update
binds the original complete VM configuration, the reviewed run options, image
default environment/workdir and original service registry.

A private `upgrade.json` records `requested`, `acknowledged` and `observed`
transitions separately. The candidate has an operation-specific own label;
matching its name alone never authorizes deletion. Failed list/image reads are
not evidence of absence. A pending, malformed, unsafe or authority-mismatched
journal prevents ordinary supervisor actions before daemon access. Moving or
restoring the control directory does not silently enroll a new lock inode:
explicit host-owner recovery and requalification are needed. Do not delete the
journal to make supervision resume.

The normal native entry invokes the existing fixed `/usr/local/bin/container`
CLI through an owned stdlib guardian. The guardian retains the inherited writer
lease until its direct CLI child is collected, even if the actor is killed.
Native stdout is bounded to 1 MiB, commands to 90 seconds, and only an owned
process group may be signalled. An unkillable kernel task remains fenced; the
caller does not claim successful cleanup. Temporary failures and cancellation
retain fixed categories without native payloads, environment or credentials.

Each mutating command has a create-only private result file bound to operation,
authority path/inode, action and actual argument digest. It records whether the
CLI was ever spawned, its terminal exit and a fixed failure category. Missing,
unsafe or stale notes are **unknown**, not `spawned=false`. No stdout is saved.
Terminal collection of a CLI does **not** establish cancellation/quiescence of
an arbitrary queued daemon request. Uncertain started/nonzero requests remain
blocked; no generic kill/retry guarantee is offered.

The existing frozen maintenance adapter stages its complete stdlib dependency
closure and uses the same guardian/lease path. An explicitly host-approved
alternate engine is digest-bound and its actual argv is bound in command notes;
the normal native recovery entry refuses mismatched executor notes. Re-prepare
old frozen runbooks to acquire these source changes; historical frozen plans do
not automatically update.

## Explicit reconciliation

`local-apple-upgrade status` exposes only service, phase, fixed first failure
category/exit and whether recovery is needed. `recover` accepts the original
service, not a replacement image/name. It preserves unrelated registry rows,
refuses drift/foreign candidates and never executes stored arbitrary argv.

An interrupted `requested` native command needs exact successful terminal or
positive no-spawn evidence before further compensation. The old VM must retain
its original full configuration. An existing candidate must have the exact own
operation label, image and reviewed settings. A running candidate must be
observed stopped and then absent before the old identity may be restarted.
Missing observations stop the process with its fence intact.

A successful recovery records a separate `reconciled` proof: old running,
candidate absent, fresh verified readiness and restored registry digest. Original
failures and actions are not backfilled with acknowledgements. Three explicit
attempts are allowed, not an unlimited daemon retry loop. Before a later update,
the exact terminal predecessor journal is retained in private host history.

## State and compatibility boundary

Rollback means **old image with current persistent state**, never a blind cold
snapshot restore. Adoption credentials, replay markers, charged-outcome records,
model/index association and current user configuration must not be rolled back
merely to make a service start. No code here clears/replays those stores or opens
a provider/controller operation. A retained archive digest is not a full restore
drill or permission to activate a second identity.

The CLI health reader uses the profile's own CA plus exact leaf and matches its
private literal endpoint/state/kind and process-start configuration revision.
Unknown/legacy startup revision cannot pass. Late positive health cannot extend
a wait budget. Different image environment/workdir defaults are explicitly
unsupported without a reviewed migration. Mutable image tags are not a release
source/signature guarantee and must not be concurrently retargeted.

The predecessor source/configuration compatibility tests are not evidence of a
prior publicly supported release: version 0.1.0 remains experimental. Supported
version policy, native Mac/NAS reconnect/restore, controller/index association,
corruption/disk-full/power-loss, duplicate-charge behavior, quality and sustained
operation remain separate acceptance. No original broad issue is completed by
these fixtures alone.

## Concurrent configuration edits

Native CLI and frozen maintenance updates must bind `/state/config.json` to the
pinned profile. They take the existing Key and Port configuration mutex paths,
then publish the existing `aikey-host-activation/1` reservation before the first
VM mutation. Every managed configuration writer is blocked while it is prepared;
startup may read only its exact reviewed revision. The reservation survives an
actor crash after the in-process configuration locks disappear.

Reconciliation checks those original configuration bytes before any native
request and throughout recovery. A changed file, foreign/missing reservation or
changed original operation keeps the fence; recovery does not overwrite the
user's configuration. Only observed complete upgrade/rollback/reconciliation
settles the reservation. The readiness endpoint must report both the startup
revision and the activation schema; older uncooperative readers are unverified.
Direct in-process test adapters without real guest state are explicitly outside
this native contract. They cannot silently replace the native entry's mandatory
configuration reservation.
