# Reviewed NAS runtime layout — 30 October 2026

This is a source-qualified, read-only reconciliation checkpoint for #45.
It does not start a Port, change pairings or establish full Key/Port parity.

## Implemented boundary

The optional private `aikey-nas-runtime-layout/1` input represents only three
existing deployment details: images by selected slot, one separate read-only
model directory, and a pre-existing internal health bridge with explicit IPv4
subnet, gateway and selected-slot addresses. Both CLIs use the same input; full
manifest regeneration must still match exactly. The default single-image,
state-only, LAN-only manifest is unchanged.

Unknown fields, invalid or unselected slots, invalid images, missing/relative/
symlink/foreign-owned/state-overlapping model paths, public/overlapping health
subnets and unusable/duplicate/gateway addresses are refused. Generation does
not create a model directory or network. Explicit JSON null/non-object,
malformed or duplicate-field layout files fail before inventory retrieval.

Runtime checks require the exact image, declared mount count, bind sources,
destinations and write permissions, and exact network set plus static IPs.
State remains writable; models remain read-only. The health bridge must match
its internal IPv4-only subnet/gateway contract. Extra or missing mounts and
networks do not become exceptions. Existing identity, isolation, stale-input,
unexpected-service, no-recreate and rollback checks remain in force.

## Qualification

- 54 new repository cases failed against the parent, across the main matrix and
  added start/rollback/file-admission cases. The corrected combined group passed
  **188 tests**. Ruff passes.
- A separate same-agent source-derived matrix rejects **72** invalid cases before
  identity-file verification/provisioning or transport, checks three valid
  normalization controls, preserves caller input and collects its fixture tree.
  A positive control proves the valid baseline reaches the identity-verification
  boundary; no socket/DNS/provision call occurs.
- A network-disabled, read-only import container verifies **111** source modules
  (109 existing plus the two candidate overlays), builds the manifest from four
  actual saved profiles and validates it against fresh pinned native inventory
  and the existing 3/2/2/2 camera assignment constraints. No worker, model or
  device listener starts. The owned container is collected.
- The exact shipped reconciliation helpers then run on the NAS host in
  **dry-run mode**: four running slots, **zero would-start**, two qualified image
  variants, two exact binds and two exact networks per slot. Network inspection
  confirms the existing internal health bridge. Four pinned own-device health
  GETs report adopted/connected. Seven live container IDs, images, PIDs and
  restart counts and all five profile hashes remain unchanged.
- The final native gate uses the final two module hashes in the
  [machine-readable receipt](nas-runtime-layout-20261030.json). An earlier gate
  before the stricter file reader is retained separately, not relabeled final.

The initial focused run correctly refused a changed state bind but failed an
existing diagnostic-string assertion; its result is retained (174 pass, one
failure). The fixed diagnostic remains backward-compatible. The first native
harness failed local compilation before SSH and was repaired without changing
product code. An initial separate validation harness used an incomplete plan
and incorrectly counted a valid empty image override as a negative; it is not
scored. Its replacement has a valid positive baseline and explicit failure-order
sentinel. These are same-agent checks, not independent human review.

## Limits

No native up/stop/apply, new identity, camera reassignment, producer rollout,
model/provider request, model-directory permission change or public image
publication occurred. Read-only binding does not attest host model content,
licensing or protection against host writers. Health status does not establish
recognition quality, controlled zone/nonmatch outcomes, recording continuity,
new-slot creation/removal, Mac lifecycle or power-loss recovery. #45 remains open.
