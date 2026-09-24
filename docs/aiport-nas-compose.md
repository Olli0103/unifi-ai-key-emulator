# Multi-instance AI Port network plan for a Linux NAS

`local-aiport-nas-compose` turns an AI Port capacity plan and already-provisioned private slot identities into a reviewable Docker Compose JSON file. Every selected NAS slot must have a reserved address; later, unselected slots may remain unaddressed. It is a deployment input, not a pairing or feature-parity claim. The candidate runs passively by default. Explicit live detector policies exist for one paired camera and for a camera pool, but the NAS manifest does not create those private policies or prove native multi-camera operation.

Protect expects each AI Port identity at HTTPS port 443 on its own LAN address. The number of instances follows camera resolution and source capacity, not one port per camera. A Linux macvlan network can assign each container a distinct IP and MAC on the NAS LAN. Docker's [macvlan documentation](https://docs.docker.com/engine/network/drivers/macvlan/) requires a real parent interface and warns that the NAS host cannot directly reach its macvlan containers without an additional network path. This mode has **not** been tested on the UGREEN NAS.

Before generating a manifest, confirm the NAS has Docker Engine/Compose with macvlan support, identify the physical parent interface and subnet/gateway, and reserve each AI Port IP outside DHCP or through the router. Do not treat an unanswered ping as proof an address is free. The existing AI Key IP, NAS IP, gateway, controller IP, and camera IPs cannot be reused. On the current lab plan, five AI Port instances are required for the selected G3–G5/legacy scope, while only the existing Mac candidate's first address is assigned. Four additional reserved addresses are `needs_evidence`.

Create a fresh, saved `local-aiport-plan` result with an address for every slot selected in this NAS manifest. Other planned slots can wait for their reservations. Preserve the prior plan when adding slots so that an adopted identity does not silently move. Provision each selected NAS slot with `local-aiport-provision` in a private, persistent directory owned by the intended non-root container UID/GID. The generator verifies the stored MAC, device certificate, controller pin, fixed IP, and file ownership; it will not create or rotate identities. An already-running Mac slot can be excluded while later slots are prepared for the NAS:

```sh
local-aiport-nas-compose \
  --plan PRIVATE_PLAN_WITH_SELECTED_SLOTS_ADDRESSED \
  --slot-state 2=/private/nas/aiport/slot-2 \
  --slot-state 3=/private/nas/aiport/slot-3 \
  --slot-state 4=/private/nas/aiport/slot-4 \
  --slot-state 5=/private/nas/aiport/slot-5 \
  --controller-ip PROTECT_IPV4 --controller-pin VERIFIED_SHA256_PIN \
  --nas-ip NAS_IPV4 --subnet LAN_CIDR --gateway LAN_GATEWAY_IPV4 \
  --parent VERIFIED_NAS_ETHERNET_INTERFACE \
  --image LOCAL_AI_PORT_CANDIDATE_IMAGE --uid NAS_NONROOT_UID --gid NAS_NONROOT_GID \
  --output /private/nas/aiport/compose.json
```

The output is mode 600 and never overwritten if it differs. Compose gets one service per selected slot, a static LAN IP and the slot's stored MAC, fixed HTTPS 443 inside the container, a writable private state mount, a read-only root filesystem, dropped Linux capabilities, and a namespaced unprivileged-port sysctl. It publishes no host ports. A separately armed stream diagnostic opens TCP 7447 on that same container IP; no extra host port is needed. [Docker Compose network attributes](https://docs.docker.com/reference/compose-file/services/#networks) and [network IPAM](https://docs.docker.com/reference/compose-file/networks/#ipam) support this layout. The NAS runtime must still prove that its kernel accepts the sysctl and that each service can bind 443 as the selected non-root user.

On the NAS, first verify the parent interface and each IP reservation. `local-aiport-nas-reconcile` provides a read-only default run and an explicit `--apply` mode. Pass the same plan, Compose file, slot-state assignments and network/identity arguments used to create the manifest, plus `--api-key-file`, `--web-trust-file`, and `--web-cert-file` for the pinned Protect integration API. Keep all these files private. The command fetches a fresh inventory, rejects changed camera assignments, regenerates and compares the complete manifest, validates Docker Compose, and reads the current service states. It also inspects each existing container's image, port-443 command, IP, MAC, non-root user, read-only root filesystem, dropped capabilities, no-new-privileges setting, unprivileged-port sysctl, absence of published host ports, and private state mount against the manifest. Its dry run reports which slots would start and gives each running slot a readiness state: `connected`, `awaiting_adoption`, or `controller_disconnected`. With `--apply`, it starts only absent or stopped selected slots using `--no-recreate --no-build --pull never`; it never replaces a running service. It refuses to start another slot while an existing adopted slot is disconnected. After each start it checks the container's own pinned HTTPS `/healthz` through `docker compose exec`, because the NAS host usually cannot reach a macvlan container directly. A newly started, unadopted slot can report `awaiting_adoption`; an adopted slot must reconnect during 16 bounded checks separated by two seconds. If it cannot, the reconciler stops only slots attempted during that invocation. It does not remove any containers or volumes.

Example dry run for slot 2 (add `--apply` only after checking the output and LAN reservations):

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

The command cannot prove that a router reservation exists, that the NAS kernel accepts the container's networking and port sysctl, or that Protect can reach the service. Check those on the real LAN before `--apply`, then verify each started slot from the controller or another LAN host and in Protect. Do not start an overlapping NAS service at the Mac candidate's existing IP. Retire that Mac service only as a deliberate migration after its replacement state and network are ready. Preserve the adopted state directory; never regenerate its MAC or certificate to troubleshoot reconnects.

Automatic DHCP reservation, adoption, multi-camera pairing, native detection persistence, and sustained model operation remain open. Camera pairing should follow a per-camera tested rollback path, not the Compose generation step.
