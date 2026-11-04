# Reviewed AI Port instance reconciliation on Apple container

**Implemented / fixture-tested, not native-verified.** This one-shot operator
adds the Mac create/start path for already prepared stable AI Port slots. It is
not a daemon, installer, pairing tool or native-qualified replacement for the
[historical Apple recipe](apple-container.md). The broader
[instance issue](https://github.com/Olli0103/unifi-ai-key-emulator/issues/45)
remains OPEN: real Mac/NAS creation/removal, recording continuity and native
Protect verification are still required.

## Existing components and supported contract

Use the existing capacity/listener planner and reviewed rollout to prepare
independent identities; see [NAS and rollout planning](aiport-nas-compose.md).
Mac and NAS now share the same fresh inventory/plan guard. This operator does
not provision identities during preview, repack cameras, remove idle slots,
move adopted identities or change camera permissions.

The selected contract is Apple container **1.6.1**, Linux ARM64, an already local
immutable `IMAGE@sha256:DIGEST`, default networking and a distinct, already
assigned LAN address per slot. Commands follow Apple's maintained
[1.6.1 command reference](https://github.com/apple/container/blob/1.6.1/docs/command-reference.md)
and [inspection reference](https://github.com/apple/container/blob/1.6.1/docs/container-inspection.md).
Document review and synthetic responses do **not** qualify that actual runtime.
Historical 1.4.1 observations do not qualify 1.6.1 either.

Each instance publishes only `ASSIGNED_ADDRESS:443:8443/tcp`. The separate Key
stays on 8080 and is never started/stopped/recreated here. If the capacity plan
contains a Key address, independent private Key state and fresh pinned health
are mandatory. UDP discovery and stream listeners remain `needs_evidence`, not
guessed ports. No source-IP allowlist is broadened to accommodate NAT.

## Prepare and review

Run as the ordinary user owning the daemon and private state. Reserve and
assign addresses through the reviewed network procedure first. Do not replay
provisioning over adopted state. Required inputs are the private capacity plan,
existing slot state, verified controller pin, and fresh inventory. Supported
Protect versions, freshness, source classes and measured-capacity guards stay
unchanged. The image must already be installed; no build, pull or installer runs.

The image must declare `org.local-aikey.host-activation=1` and implement the
startup contract below. Old images are refused before create/start. The AI Port
Dockerfile declares this compatibility contract; a label is not an independent
audit. Optional models mount read-only at `/models`. Key, Port and model roots
must be disjoint; instances cannot share identities, private keys or certificates.

These examples contain placeholders, not usable settings. Omit `--aikey-state`
only for a genuinely Port-only capacity plan. Repeat `--slot-state` for selected
Mac slots. UID/GID default to the current non-root user.

```sh
local-aiport-apple plan --plan /private/host/deployment.json \
  --inventory /private/host/inventory.json --slot-state 1=/private/port-1 \
  --aikey-state /private/aikey --controller-ip CONTROLLER_IPV4 \
  --controller-pin VERIFIED_CONTROLLER_SHA256 --image IMAGE_AT_SHA256_DIGEST \
  --dns REACHABLE_PRIVATE_DNS_IPV4 --output /private/host/apple-manifest.json
```

For an automatic current read, replace `--inventory FILE` with `--live-inventory`
and all three file options: `--api-key-file`, `--web-trust-file` and
`--web-cert-file`. These are pinned Integration credentials, not model keys.
Unused or incomplete credentials, stale/changed plans and changed configuration
or identity bytes fail closed. The manifest includes private paths/addresses:
do not paste it into public issues. Publication is create-only; identical bytes
are a no-op, different existing bytes are retained. Planning issues no container
command, loads no model and changes no identity or configuration.

Admission failures use fixed actionable codes, for example
`apple_fresh_supported_inventory_required`,
`apple_plan_inventory_or_slot_identity_changed`,
`apple_selected_slot_requires_assigned_address` and
`apple_onvif_ingest_unsupported`. Unknown shared errors stay a fixed generic
preflight refusal, never a raw exception or private path.

```sh
local-aiport-apple run --plan /private/host/deployment.json \
  --inventory /private/host/inventory.json --slot-state 1=/private/port-1 \
  --aikey-state /private/aikey --controller-ip CONTROLLER_IPV4 \
  --controller-pin VERIFIED_CONTROLLER_SHA256 --image IMAGE_AT_SHA256_DIGEST \
  --dns REACHABLE_PRIVATE_DNS_IPV4 --manifest /private/host/apple-manifest.json \
  --state-dir /private/host/authority
```

The authority directory must already exist, be private and be disjoint from
all guest mounts and Key state, including ancestor/descendant paths. A worker
must not be able to write its own host journal. Keep using that same directory.
Default `run` is read-only: image/runtime inspection and pinned
health, with a brief non-listening TCP bind probe for proposed starts. It creates
no lock/journal, container, service listener or identity. Review `would_start`,
blockers and `ready_for_apply`; the latter is local preflight, not native proof.

## Host checks do not repair the host

Before each create/start, the exact address must belong to one interface,
the controller route must use that same non-loopback/non-tunnel interface,
and exact-address TCP 443 binding must succeed. All running containers are
checked for wildcard/exact/ranged TCP conflicts and overlapping identity mounts.
An enabled or unknown application-firewall policy requires separate qualified
review. The operator never disables a firewall, adds aliases/routes, invokes
sudo, installs privileged helpers or falls back to wildcard publication. Other
packet filters, proxy source-IP behavior and controller reachability still need
native tests.

Apple reports explicit-address privileged-port limitations in
[issue 1985](https://github.com/apple/container/issues/1985) and
[issue 590](https://github.com/apple/container/issues/590). These older reports
do not establish 1.6.1 behavior on every host. A failed exact-address 443 bind
blocks apply. Do not run another user's daemon as root or silently change ports.
The existing separate host-relay contract, if needed, requires its own reviewed
deployment; it is not automatically installed here.

## Apply and uncertain outcomes

Add `--apply` to the complete reviewed `run` invocation for an authorized start.
Running instances must match image, resources, DNS, network, mounts, process and
identity exactly. Fresh own-CA/exact-leaf health must report the loaded config
SHA-256. Adopted Ports and Key must stay connected. A new unadopted Port is
`awaiting_adoption`, not paired or native-qualified.

Only absent/stopped selected containers are created/started. Intent is durable
before mutation; acknowledgement and positive inspection are separate records.
Readiness failure stops only this operation's known new starts. Stopped
containers and private identities are retained. Prior running/unrelated
containers are never stopped or deleted.

Lost acknowledgement, timeout, authority change or uncertain journal publication
does not trigger blind retry or speculative cleanup. The explicitly uncertain
result fences another apply. Keep the original manifest, plan, authority and
private state. Use the same complete `run` invocation with `--reconcile` instead
of `--apply` for positive read-back only. It never retries daemon mutations,
stops a container, rewrites config or relabels the original failure successful.
Absence cannot prove a possibly late create/start had no effect. Unproved
postconditions or changed ownership remain fenced.

### Cross-process startup reservation

Apply holds the existing configuration OS mutexes shared with supported Admin
and device writers. Before create/start it also persists the private per-config
`.config.json.host-activation.json` record (`aikey-host-activation/1`), binding
operation, authority, manifest and raw config hash. After the owner exits, an
unfinished reservation still blocks ordinary writers, device Undo and device
reconciliation. Late startup can load only those exact reviewed bytes, before
constructing models/runtime. New worker health must report the loaded hash and
known startup contract. A different authority cannot clear the reservation.

Only positively observed success/rollback or original-intent reconciliation
settles it. Failed marker/journal publication retains uncertainty. Never edit or
delete a marker to force a retry. All supported writers and newly started images
must contain this coordinated source contract; an old Admin writer mixed with
the new operator is unsupported.

Coherent backup refuses prepared reservations and retains settled markers.
Back up the separate host authority directory with device state for whole-host
recovery: device-only archives do not include that external journal. Follow
[restore boundaries](uninstall-restore.md); an incomplete controller transaction
is not a native recovery proof.

## Acceptance boundary

Synthetic tests use actual private files, OS locks, another writer process,
CLI publication, and injected daemon/health responses. They do not run an Apple
1.6.1 container, change host networking, adopt/pair devices, perform model
inference, inspect recordings or establish sustained lifecycle behavior.
Every started instance and paired camera set needs fresh native Protect
verification. Keep the original five acceptance items open until that actual
end-to-end evidence exists.
