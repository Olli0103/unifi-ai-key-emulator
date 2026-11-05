# Multi-instance AI Port network plan for a Linux NAS

For a new installation, follow the [step-by-step Linux guide](aiport-install.md).
This page is the detailed manifest, reconciliation and rollout reference.

`local-aiport-nas-compose` turns an AI Port capacity plan and already-provisioned private slot identities into a reviewable Docker Compose JSON file. Every selected NAS slot must have a reserved address; later, unselected slots may remain unaddressed. It is a deployment input, not a pairing or feature-parity claim. The candidate runs passively by default. Explicit live detector policies exist for one paired camera and for a camera pool; the NAS manifest does not create those private policies.

Protect expects each AI Port identity at HTTPS port 443 on its own LAN address. The number of instances follows camera resolution and source capacity, not one port per camera. A Linux macvlan network can assign each container a distinct IP and MAC on the NAS LAN. Docker's [macvlan documentation](https://docs.docker.com/engine/network/drivers/macvlan/) requires a real parent interface and warns that the NAS host cannot directly reach its macvlan containers without an additional network path. Native observations of this network mode are version-specific; they do not establish feature parity or qualify every NAS.

Before generating a manifest, confirm the NAS has Docker Engine/Compose with macvlan support, identify the physical parent interface and subnet/gateway, and reserve each AI Port IP outside DHCP or through the router. Do not treat an unanswered ping as proof an address is free. The existing AI Key IP, NAS IP, gateway, controller IP, and camera IPs cannot be reused. Plan only the cameras and addresses in your installation. Actual stream dimensions determine capacity; a display label alone is insufficient.

Create a fresh, saved `local-aiport-plan` result with an address for every slot selected in this NAS manifest. Other planned slots can wait for their reservations. Preserve the prior plan when adding slots so that an adopted identity does not silently move. Provision each selected NAS slot with `local-aiport-provision` in a private, persistent directory owned by the intended non-root container UID/GID. The generator verifies the stored MAC, device certificate, controller pin, fixed IP, and file ownership; it will not create or rotate identities. An already-running Mac slot can be excluded while later slots are prepared for the NAS:

```sh
local-aiport-nas-compose \
  --plan PRIVATE_PLAN_WITH_SELECTED_SLOTS_ADDRESSED \
  --slot-state 2=/private/nas/aiport/slot-2 \
  --slot-state 3=/private/nas/aiport/slot-3 \
  --slot-state 4=/private/nas/aiport/slot-4 \
  --controller-ip PROTECT_IPV4 --controller-pin VERIFIED_SHA256_PIN \
  --nas-ip NAS_IPV4 --subnet LAN_CIDR --gateway LAN_GATEWAY_IPV4 \
  --parent VERIFIED_NAS_ETHERNET_INTERFACE \
  --image LOCAL_AI_PORT_CANDIDATE_IMAGE --uid NAS_NONROOT_UID --gid NAS_NONROOT_GID \
  --output /private/nas/aiport/compose.json
```

The output is mode 600 and never overwritten if it differs. Compose gets one service per selected slot, a static LAN IP and the slot's stored MAC, fixed HTTPS 443 inside the container, a writable private state mount, a read-only root filesystem, dropped Linux capabilities, and a namespaced unprivileged-port sysctl. It publishes no host ports. A separately armed stream diagnostic opens TCP 7447 on that same container IP; no extra host port is needed. [Docker Compose network attributes](https://docs.docker.com/reference/compose-file/services/#networks) and [network IPAM](https://docs.docker.com/reference/compose-file/networks/#ipam) support this layout. The NAS runtime must still prove that its kernel accepts the sysctl and that each service can bind 443 as the selected non-root user.

Apple `container image save` emits an OCI archive. Before using the NAS vendor Docker GUI's image import path, convert that local archive to a Docker archive with `skopeo copy --format v2s2 oci-archive:SOURCE.tar docker-archive:DEST.tar:IMAGE_TAG`, then inspect the resulting `manifest.json` for the intended tag and `linux/amd64` image configuration. A `.docker.tar` filename alone does not establish its format. The converted image was imported through the NAS vendor Docker GUI and used by a running NAS slot on 24 September.

If the NAS already has a suitable macvlan, add `--external-network EXISTING_NETWORK_NAME` to both generation and reconciliation commands. The manifest then joins that network instead of creating a second one on the same LAN. Before reading or starting containers, reconciliation inspects the existing Docker network and requires the declared macvlan driver, parent interface, subnet and gateway. It also checks that each container is attached to the named network at its planned IP and MAC. The NAS Docker UI may show a friendly network-card label; use the actual parent interface reported by `docker network inspect` for `--parent`. This check does not reserve an IP in the router or prove that Protect can reach the container.

On the NAS, first verify the parent interface and each IP reservation. `local-aiport-nas-reconcile` provides a read-only default run and an explicit `--apply` mode. Pass the same plan, Compose file, slot-state assignments and network/identity arguments used to create the manifest, plus `--api-key-file`, `--web-trust-file`, and `--web-cert-file` for the pinned Protect integration API. Keep all these files private. The command fetches a fresh inventory, rejects changed camera assignments, regenerates and compares the complete manifest, validates Docker Compose, and reads the current service states. It also inspects each existing container's image, port-443 command, IP, MAC, non-root user, read-only root filesystem, dropped capabilities, no-new-privileges setting, unprivileged-port sysctl, absence of published host ports, and private state mount against the manifest. Its dry run reports which slots would start and gives each running slot a readiness state: `connected`, `awaiting_adoption`, or `controller_disconnected`. With `--apply`, it creates absent selected slots without starting them using `--no-recreate --no-build --pull never`, verifies their operation labels, then starts only the observed container IDs; it never replaces a running service. It refuses to start another slot while an existing adopted slot is disconnected. After each start it checks the container's own pinned HTTPS `/healthz` through `docker exec` addressed to the inspected container ID, because the NAS host usually cannot reach a macvlan container directly. A newly started, unadopted slot can report `awaiting_adoption`; an adopted slot must reconnect during 16 bounded checks separated by two seconds. If it cannot, the reconciler stops only positively observed starts from that operation, by exact container ID. A lost reply or unobserved stop remains uncertain and blocks another apply. It does not remove any containers or volumes.


Apply requires an existing owner-only (0700), canonical `--host-state` directory
outside every guest mount, shared by all operators for this deployment. The
host writer and existing configuration mutexes serialize admission. Before any
start, the actor persists the original operation and a startup reservation,
and uses a private operation-labelled Compose recipe. Dollar interpolation is
refused. The Linux CLI is `/usr/bin/docker` with an explicit local Unix socket, not a saved Docker context; the monitored command child retains
the host lease until collection even if its actor exits. Images to be started
must advertise `org.local-aikey.host-activation=1`; old running slots are only
observed, not upgraded. No config or identity backup is restored on failure.

After interruption, use the same original inputs and authority with `--settle`
instead of `--apply`. Settlement only observes: it requires the original
container/operation, unchanged configuration and identity, plus a terminal
command receipt and positive postcondition for an unacknowledged mutation.
Container absence alone does not prove that a late request is impossible.
Missing evidence keeps the reservation and operation fenced; never delete the
intent or force a retry. Completed prior receipts and failed outcomes are
retained in the private authority directory. This mechanism is not proof of
native adoption, camera pairing, recording continuity or host recovery.

The reconciler and inventory reader share one exact supported-version check
(currently Protect 7.3.60, 7.3.68, 7.3.70 and 7.3.74). Other versions, including
unknown patch releases and prerelease suffixes, remain blocked. This is inventory
schema support, not blanket AI Port feature-parity qualification. Version support
does not bypass inventory freshness, slot/camera assignment or manifest checks.

Example dry run for slot 2 (add `--apply --host-state PRIVATE_HOST_AUTHORITY` only after checking the output and LAN reservations):

```sh
local-aiport-nas-reconcile \
  --plan PRIVATE_PLAN_WITH_SELECTED_SLOTS_ADDRESSED --compose /private/nas/aiport/compose.json \
  --slot-state 2=/private/nas/aiport/slot-2 \
  --controller-ip PROTECT_IPV4 --controller-pin VERIFIED_SHA256_PIN \
  --nas-ip NAS_IPV4 --subnet LAN_CIDR --gateway LAN_GATEWAY_IPV4 \
  --parent VERIFIED_NAS_ETHERNET_INTERFACE \
  --image LOCAL_AI_PORT_CANDIDATE_IMAGE --uid NAS_NONROOT_UID --gid NAS_NONROOT_GID \
  --api-key-file PRIVATE_PROTECT_API_KEY_FILE \
  --web-trust-file PRIVATE_WEB_TRUST_JSON --web-cert-file PRIVATE_WEB_CERT_PEM
```

When generation used `--external-network`, pass the same option and name to reconciliation. An edited network name or changed Docker network stops the run before it starts a slot.

Both commands accept `--project-name` (default `local-aiport`). Use the same
reviewed name for generation and reconciliation; it is frozen in the manifest
and checked with the rest of the deployment inputs. Names contain 1–63 lowercase
ASCII letters, digits, underscores or hyphens and start with a letter or digit.
Every Compose operation explicitly selects that project, including status,
health, start and rollback. An ambient `COMPOSE_PROJECT_NAME` cannot redirect
those operations. This does not import an existing stack, verify an untrusted
vendor Compose file, or authorize repacking adopted camera assignments.
See the [30 October namespace verification](evidence/compose-project-fencing-20261030.md)
for the native status reproduction and isolated lifecycle check.

### Stopped containers and retained network bindings

Docker clears an exited container's runtime IP and MAC fields. The reconciler
does not treat those empty values as a matching identity. A successful apply
with no starts now saves the already verified container IDs in its private host
intent. A later start can reuse that binding only for the same container and
the exact manifest/configuration/identity revision. Newly created endpoints
must expose their explicitly requested MAC. Unknown exited containers without
that prior evidence remain blocked; do not fabricate or edit an intent to
admit them.

Inactive endpoints must retain the exact configured IPAM address for both the
LAN and optional health network. After start, live IP and MAC checks are
mandatory again, before successful completion. This is not a claim that Docker
exposes the inactive MAC, or protection against a privileged daemon operator
rewriting network endpoints between observations. Existing uncertain-operation,
health, state-mount and namespace guards remain in force.

When running the host reconciler inside a dedicated operator container, use
Docker's `--init` so its Python owner is not PID 1. The command guardian refuses
PID 1 as a watched parent. This does not change application container identity,
adoption or ordinary native-host CLI operation.

### Explicit existing NAS runtime layout

The default remains one state bind, one LAN network and the supplied image for
every selected slot. For an already reviewed deployment with different image
revisions, a shared read-only model directory or an internal health bridge, pass
the **same** private `--runtime-layout FILE` to both commands. This is an input to
the complete manifest comparison, not an import or an unchecked override.

Example for selected slots 2–4 (addresses and images are fictional):

```json
{
  "schema": "aikey-nas-runtime-layout/1",
  "slot_images": {"2": "local-aiport:reviewed-canary"},
  "models_dir": "/private/nas/models",
  "health_network": {
    "name": "reviewed_internal_health",
    "subnet": "172.27.1.0/24",
    "gateway": "172.27.1.1",
    "addresses": {"2": "172.27.1.22", "3": "172.27.1.23", "4": "172.27.1.24"}
  }
}
```

Only `schema` is mandatory; the three optional sections are independent. Image
overrides may name only selected slots, using canonical decimal slot strings;
other selected slots keep `--image`. The model directory must already exist, be
owned by the selected UID, have no symlink path, and be separate from selected
state directories. It is bound only at `/models`, read-only, without creating a
host path or changing permissions. This does not attest model contents,
licensing or immunity to updates by host writers.

The health network must already exist as an internal IPv4-only bridge, with
exactly the declared RFC1918 subnet and gateway, disjoint from the LAN. Declare
one distinct usable non-gateway address per selected slot. No network is created
or attached during a dry run. Its manifest selects the LAN's gateway priority;
unsupported Compose versions fail validation before starting a slot. The
reconciler requires **exactly** the declared network set and IPs, exact image,
and exact state/model binds with their expected write permissions. Additional,
missing, duplicate or changed mounts/networks remain errors; the normal
isolation and unexpected-service guards remain in force.

The layout file must be a private JSON object: explicit `null`, malformed JSON,
duplicate fields and non-object files are refused before inventory retrieval.
Layout validation never provisions an identity or creates a model directory.
Native verification and limits are recorded in the
[30 October layout checkpoint](evidence/nas-runtime-layout-20261030.md).

Protect's Integration API does not report stream resolution. In the 24 September live read, it also classified the two initially legacy G3 cameras as smart-event candidates after AI Port pairing. If a previously verified, unselected Mac slot relied on a lower resolution than the conservative unknown-camera fallback, reconciliation checks that its camera IDs and source still match but does not let that untouched slot block a separate NAS start. The verifier tolerates only the descriptive legacy/enhancement counts changing after pairing; camera IDs, slot assignments, addresses, listener settings and reserved capacity must still match. Every **selected** NAS slot must still fit the fresh inventory's conservative capacity; a lower-resolution assumption for a selected slot requires fresh evidence before it can start. This exception does not create or modify a Mac container.

The command cannot prove that a router reservation exists, that the NAS kernel accepts the container's networking and port sysctl, or that Protect can reach the service. Check those on the real LAN before `--apply`, then verify each started slot from the controller or another LAN host and in Protect. Do not start an overlapping NAS service at the Mac candidate's existing IP. Retire that Mac service only as a deliberate migration after its replacement state and network are ready. Preserve the adopted state directory; never regenerate its MAC or certificate to troubleshoot reconnects.

An existing slot may contain a valid camera and detector policy. Host-side identity and Compose checks validate the Linux decoder path without requiring that executable on the host. Before starting a selected slot, reconciliation matches every configured camera MAC to that slot's fresh Protect inventory assignment and requires its Protect stream source to be the configured controller. It allows a partially configured slot but rejects a camera assigned to another slot.

Platform/model observations remain in the [dated evidence records](evidence/README.md).
Do not infer GPU availability from device-node access or a model file's presence.
Verify the actual runtime, file permissions and model backend in isolation first.
Then qualify adoption, pairing and a saved native event separately.

## Slot allowlist diff against Protect pairings

`local-aiport-slot-diff` compares each deployed slot's `paired_streams` allowlist with Protect's authoritative pairing (`aiports[].pairedCameras`). It uses private exports of Protect's `aiports` and `cameras` lists. The Protect integration API does not expose AI Port pairing. It never pairs, unpairs or restarts anything, and its output names cameras and slots without MAC or IP addresses.

| Action | Automated | Meaning |
|---|---|---|
| `remove_from_allowlist` | yes, with `--apply-local` | A stream Protect never starts on this slot (stale entry). The original file is kept as `config.json.before-slot-diff`. |
| `add_to_allowlist` | review only | Protect paired a camera that the slot would reject. Its Protect host becomes the stream source. |
| `manual_pairing` | no | An eligible G3–G5 camera that no AI Port has paired. Pair it in Protect. |
| `over_capacity`, `missing_ai_port`, `unmanaged_ai_port` | no | Reported for review. |

A second run after `--apply-local` reports no safe action, so the diff is idempotent. Changed files take effect after that instance restarts. NAS copies must be uploaded as described above.

## Automatic slot rollout (dry run first)

`local-aiport-rollout` and the control site's **AI Port rollout** page plan slots from Protect's eligible cameras. It needs no Protect administrator session:

- **Eligibility** (connected legacy and G3–G5 Protect cameras) and capacity weights come from the pinned integration inventory, with the planner's rules.
- **Pairing** comes from each slot's pinned `/healthz`. Protect sends a camera's policy and stream only to the AI Port it paired, so a camera with a policy or stream on a slot is paired there.

The plan never moves a paired camera and never renames or re-addresses a slot. A slot whose health cannot be read is left unchanged, and an offline camera keeps its allowlist entry. Applying requires the revision of the dry run that was reviewed, and changes only local files:

| Action | Applied locally | Still needs you |
|---|---|---|
| `add_to_allowlist` for a new eligible camera (existing slot with capacity) | yes, backup `config.json.before-rollout` | `upload_slot_config` and `restart_nas_slot` (NAS) or `restart_mac_container` (Mac), then `pair_in_protect` |
| `remove_from_allowlist` (paired to another slot, or no longer eligible) | yes, backup `config.json.before-rollout` | `upload_slot_config` and `restart_nas_slot`, or `restart_mac_container` |
| `create_slot` from `new_slots.addresses` (identity via `provision_slot` with the plan's MAC, detector copied from the template slot, no request cap) | yes, and registered in the rollout file | `deploy_nas_service` (without `compose`) or `start_mac_container`, `adopt_in_protect`, `pair_in_protect` |
| `compose_add_service` for a new NAS slot (needs `compose`) | yes: the reviewed block is appended to the private Compose copy, backup `<file>.before-<slot>` | `upload_slot_state`, `redeploy_nas_project`, `verify_slot_health`, `adopt_in_protect`, `pair_in_protect` |
| `capacity_unverified`: the new slot is needed only by a fallback weight (unknown model) and the camera would fit an existing slot at the 2-point minimum | no, unless `--allow-estimated-capacity` | review |
| `compose_copy_stale`: the Compose copy's template or any slot service is not on `compose.live_image` (or it is unset) | no: no service is cloned and no slot is created | refresh the copy from the NAS editor, set `live_image`, dry run again |
| `address_conflict`: a `new_slots.addresses` entry is reserved (`reserved_addresses`) or used by a slot | no, the address is skipped | fix the address pool |
| `address_needed`, `slot_unobserved`, `idle_slot`, `capacity_estimate_exceeded` | no | review |

A second run after applying reports no local action; only pending Protect or NAS steps remain. `--apply` also refuses an inventory report older than 15 minutes (`--controller` always fetches a fresh one). Capacity uses observed stream points first, then the model's main-lens weight, then a fallback.

**NAS Compose additions.** With a `compose` section, a new NAS slot gets a service copied from the template service. Only four fields change: the service name, the bind source (`<state_parent>/slot-N`), `ipv4_address`, and `mac_address`. The MAC is derived from the slot label and address, so a dry run and its apply agree. The exact block, service, address, MAC and state path are shown on the rollout page before apply, and the Compose file's hash is part of the plan revision. Apply refuses if the file changed since the dry run. It verifies that every existing service and the networks are unchanged, and appending the same service again is a no-op. The new slot is registered as `pending` with a deadline.

The `--verify` mode of `local-aiport-rollout` settles pending slots:
- if the slot's pinned `/healthz` answers, it is kept;
- if it is still silent after the deadline, exactly its service block is removed from the Compose copy again, the slot is unregistered, and its identity is kept for a later reviewed retry.

The `--rollback` mode of `local-aiport-rollout` undoes local rollout changes:
- every `config.json.before-rollout` is restored byte for byte, then removed, so a later apply takes a fresh backup;
- every still-`pending` new slot has exactly its Compose block removed and is unregistered (its identity directory stays);
- a slot that has answered health is never removed.

Run it before uploading anything to the NAS. After an upload, restore the running NAS copy the same way (upload the restored file, restart the slot).

The NAS project itself is still redeployed by uploading or pasting the Compose copy, and the new slot's state directory is uploaded to `<state_parent>/slot-N`. After a rollback, redeploy the restored copy. Pairing stays a Protect action. Compose editing needs the `rollout` extra (PyYAML).

The rollout file is private (mode 600):

```json
{"schema": "aikey-aiport-rollout/1",
 "slots": [{"label": "mac", "target": "mac", "state_dir": "/…/aiport-mac", "health_port": 8443},
           {"label": "nas-slot-2", "target": "nas", "state_dir": "/…/aiport-slots/slot-2", "health_port": 443}],
 "new_slots": {"target": "nas", "label_prefix": "nas-slot-", "state_parent": "/…/aiport-slots",
               "addresses": [], "health_port": 443, "template": "nas-slot-2"},
 "compose": {"path": "/…/aiport-slots/compose-live.yaml", "template": "aiport_slot_2",
             "state_parent": "/srv/aikey-deployment", "health_timeout_seconds": 1800,
             "live_image": "local-aiport:reviewed-source"}}
```

`new_slots.reserved_addresses` lists addresses a new slot may never take: the controller, the AI Key, the NAS and every Protect camera.

**Steps that still need a person.** The rollout never adopts, pairs, unpairs, redeploys or reserves addresses:
1. **Address:** reserve a LAN address outside DHCP in UniFi Network, then add it to `new_slots.addresses`.
2. **NAS:** upload the new slot's state directory (or an edited `config.json`) and the Compose copy through the NAS management interface, redeploy `local-aiport-nas`, or restart the one slot, without pulling images.
3. **Adoption:** adopt the new AI Port in Protect (Devices, then the pending AI Port, then Adopt).
4. **Pairing:** pair each camera with that AI Port in Protect. A camera is never moved between AI Ports automatically, because the native pairing and rollback contract (#6) is not proven.
5. **Verify:** run `--verify`, and confirm in Protect that the AI Port is `CONNECTED` with its intended cameras.

The control site shows the page when it is started with `--aiport-rollout` and the `--inventory-*` settings.

### Deployed-config drift guard (#19, 27 Sep 2026)

A NAS slot's canonical config (`state/aiport-slots/slot-N/config.json`) is what the rollout planner and the control site edit, and what `upload_slot_config` pushes to the NAS. When a deploy writes the NAS copy from another directory, the two can drift.
- **What happened:** on 26 Sep the r25 build added `live_pool_detector.plate_cameras` to slots 3 and 4 on the NAS only. The next allowlist or provider edit would have uploaded a config without plate reading.
- **The guard:** set `deployed_config` on each NAS slot in `aiport-rollout.json` to the config last deployed to it. The planner then:
  - reports `slot_config_drift` with the differing key names (never their values);
  - makes that slot's allowlist additions and removals manual until the copies match.
- **After every NAS deploy:** point `deployed_config` at the new build copy and re-run the dry run. A no-op means the canonical copy matches what runs.

### Applying a reviewed new-slot plan (#45, 27 Sep 2026)

`--apply` changes only local files, and only when all of these hold:
- **Review:** the dry run's plan revision is passed (`--revision`), and the inventory is fresh.
- **Template:** the new slot copies its detector settings from the template slot, so that template must be verified. A Mac template counts, because its config is mounted. A NAS template counts only with `deployed_config` set and no drift. Otherwise the plan shows `template_config_unverified` (`template_config_drift`, `template_deployed_config_unset` or `template_unknown`) and every step stays manual.
- **Compose:** the Compose copy is the live one (`compose_copy_stale` otherwise).
- **Existing slots:** a slot with `slot_config_drift` gets no automated allowlist edits.

**What apply guarantees**
- **Partial failure:** if any step fails, this run's own writes are undone (allowlist edits, backups, the Compose change) before the error surfaces. The new identity directory stays unregistered.
- **Idempotent retry:** a retry that finds exactly the planned identity (MAC, address and cameras) continues with the remaining steps. A directory that differs from the plan is refused, never overwritten. Re-applying an applied plan is a no-op.
- **Plate cameras:** the new slot's `plate_cameras` keeps only cameras it streams. The AI Port refuses entries it does not stream.
- **Failed health:** a pending new slot still silent after `health_timeout_seconds` has exactly its Compose service removed again by `--verify`, and is unregistered.

**Always manual:** reserving an address, uploading slot state, redeploying the NAS project, adopting the AI Port in Protect, and pairing cameras.

## Verify your own deployment

After any deployment, compare the exact deployed configuration with the copy
used by the planner. Confirm image/source identity, private ownership, expected
streams and native Protect pairings. Do not substitute an old staging directory,
upload receipt or health count for deployed-file verification. Keep unresolved
drift manual and retain prior images/state until native acceptance is complete.
