# Compose project selection — 30 October 2026

This fixes a deployment-tool boundary for #45, not Key/Port feature parity or
approval to apply a freshly generated plan to adopted cameras.

## Reproduced failure

The previous generator fixed `name` to `local-aiport`, while the four existing
NAS Ports share a different reviewed project. The old reconciler ran
`docker compose -f FILE` without an explicit project selection. A private,
status-only manifest with the existing project and service names demonstrated
the problem: with a fictional `COMPOSE_PROJECT_NAME`, the old command returned
zero containers. With the same file and environment but explicit project
selection, it returned all four. These were two read-only status calls, not an
apply. No container identity, image, PID or restart count changed.

The initial probe refused to use the vendor Compose path from container labels:
its metadata did not satisfy the private-file guard. Its contents were not read,
modified or executed. The successful reproduction used a newly written private
minimal manifest, not a relaxation of that guard.

## Correction and checks

- Both CLIs accept the same bounded, lowercase `--project-name`; its default
  remains `local-aiport`. The complete manifest comparison includes that name.
- All Compose commands explicitly select the frozen project: validation,
  status, health, start and rollback. Invalid names fail before a Docker call;
  invalid generator names fail before identity provisioning.
- The 49 new repository cases failed on the parent and pass after correction.
  The focused combined suite passed 134 tests. Static checks passed.
- A separate same-agent, source-derived harness rejected 23 invalid values and
  checked six valid argument vectors without transport. It then used the
  corrected helper for one native status read, finding all four existing Ports
  despite the conflicting environment.
- The same helper passed real Compose `config`, `up`, `ps`, `exec` and `stop`
  against exactly one owned fictional container: no network, mounts, published
  ports, model or provider requests. Its exact label-bound container was
  collected and a final check found no fixture containers. All seven live
  containers retained their IDs, images, PIDs and restart counts.

The existing bounded-health script is unchanged from #211. There was no native
start, stop, apply, camera reassignment, identity rotation or producer upgrade.
The independent harness means an independent test design by the same agent,
not a human review. Machine-readable observations are in the
[companion receipt](compose-project-fencing-20261030.json).

## Remaining scope

Fresh inventory contains nine selected cameras and requires four Ports, but an
initial plan without prior stable assignments groups them differently from the
adopted deployment. That is not authority to move them. Existing mixed producer
images and the additional private health network also need an explicitly
reviewed representation; the strict image/network inspection is not bypassed.
The complete production reconciler path and #45 therefore remain open.
