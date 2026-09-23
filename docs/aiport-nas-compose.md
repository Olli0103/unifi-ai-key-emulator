# Multi-instance AI Port network plan for a Linux NAS

`local-aiport-nas-compose` turns an **addressed** AI Port capacity plan and already-provisioned private slot identities into a reviewable Docker Compose JSON file. It is a deployment input, not a pairing or feature-parity claim. The current candidate stays passive unless a separate expiring diagnostic is armed; it does not continuously process every camera.

Protect expects each AI Port identity at HTTPS port 443 on its own LAN address. The number of instances follows camera resolution and source capacity, not one port per camera. A Linux macvlan network can assign each container a distinct IP and MAC on the NAS LAN. Docker's [macvlan documentation](https://docs.docker.com/engine/network/drivers/macvlan/) requires a real parent interface and warns that the NAS host cannot directly reach its macvlan containers without an additional network path. This mode has **not** been tested on the UGREEN NAS.

Before generating a manifest, confirm the NAS has Docker Engine/Compose with macvlan support, identify the physical parent interface and subnet/gateway, and reserve each AI Port IP outside DHCP or through the router. Do not treat an unanswered ping as proof an address is free. The existing AI Key IP, NAS IP, gateway, controller IP, and camera IPs cannot be reused. On the current lab plan, five AI Port instances are required for the selected G3–G5/legacy scope, while only the existing Mac candidate's first address is assigned. Four additional reserved addresses are `needs_evidence`.

Create a fresh, saved `local-aiport-plan` result with all instance addresses. Preserve the prior plan when adding slots so that an adopted identity does not silently move. Provision each NAS slot with `local-aiport-provision` in a private, persistent directory owned by the intended non-root container UID/GID. The generator verifies the stored MAC, device certificate, controller pin, fixed IP, and file ownership; it will not create or rotate identities. An already-running Mac slot can be excluded while later slots are prepared for the NAS:

```sh
local-aiport-nas-compose \
  --plan PRIVATE_ADDRESSED_PLAN_JSON \
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

On the NAS, first run `docker compose -f /private/nas/aiport/compose.json config --quiet`. Then verify the parent interface and reserved IPs again before `up -d` for one new slot. From the controller or another LAN host, check its pinned HTTPS certificate and `/healthz`, then verify Protect sees the separate identity. Do not start an overlapping NAS service at the Mac candidate's existing IP. Retire that Mac service only as a deliberate migration after its replacement state and network are ready. Preserve the adopted state directory; never regenerate its MAC or certificate to troubleshoot reconnects.

Automatic container reconciliation, DHCP reservation, adoption, multi-camera pairing, native detection persistence, and sustained model operation remain open. Camera pairing should follow a per-camera tested rollback path, not the Compose generation step.
