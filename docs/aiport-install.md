# First AI Port on a Linux host

Use this path for a new AI Port identity on Docker Engine with macvlan support.
It is separate from the AI Key's root Compose recipe. Read the
[deployment requirements](deployment.md), [compatibility limits](compatibility.md)
and [detailed network contract](aiport-nas-compose.md) first. A passive Port can
connect and accept explicitly permitted streams; it is not a detector until you
configure the model and processing policy.

## 1. Install host tools and build the image

Install the reviewed source into a virtual environment using the
[source installation steps](deployment.md#1-select-and-install-the-source).
The manifest/rollout tools also need the `rollout` extra, containing PyYAML:

```sh
.venv/bin/python -m pip install '.[rollout]'
docker build --file Dockerfile.aiport-candidate --tag local-aiport:reviewed-source .
```

Use a unique local tag or immutable image digest tied to the reviewed commit.
This default image does not install detector extras or download weights.
The candidate [detector/model options](aiport-candidate.md) have separate image,
platform and license requirements. Do not treat a model-enabled image from an
unverified registry as an official project release.

## 2. Reserve addresses and prepare trusted inputs

Each Port needs its own free LAN address, locally administered identity and
reachable HTTPS 443. Reserve addresses outside DHCP or through your router's
supported reservation mechanism. Identify the actual Ethernet parent interface,
LAN subnet and gateway. Do not reuse the host/controller/camera/AI Key address.
macvlan usually prevents direct host-to-container access without another path.

Create a private parent owned by the eventual non-root container UID/GID.
Prepare a Protect integration API-key file and pinned web-leaf certificate/trust
record as described in [inventory trust](camera-inventory-preflight.md). Separately
obtain the controller's public control certificate and independently verify its
fingerprint. The web and control endpoints may use different certificates.
No certificate is trusted merely because it was downloaded from the same host.

## 3. Save a capacity plan

This command performs read-only integration API requests. It does not adopt,
pair, reserve addresses or enable inference. Replace every uppercase operand
and save the output in your private parent:

```sh
umask 077
.venv/bin/local-aiport-plan --controller CONSOLE_PRIVATE_IPV4 \
  --api-key-file /private/ports/protect-api-key \
  --web-trust-file /private/ports/web-trust.json \
  --web-cert-file /private/ports/web-leaf.pem \
  --camera-scope legacy-and-g3-g5 --ai-port-ip FIRST_RESERVED_PORT_IPV4 \
  > /private/ports/plan.json
```

Stop if the command fails. Review the saved plan's cameras, capacity and addresses
before proceeding. Repeat `--ai-port-ip` for additional planned instances when
needed; every selected slot must be addressed. For later planning, use
`--previous-plan` to preserve existing identities/assignments. Do not overwrite
an adopted installation's plan or reshuffle cameras implicitly.

## 4. Provision one new identity and generate Compose

The private paths below are examples; create their parent first and use your
actual absolute paths. Provisioning writes a new passive configuration,
certificate/key and pinned controller trust. It does not connect to Protect:

```sh
.venv/bin/local-aiport-provision --plan /private/ports/plan.json --slot 1 \
  --state-dir /private/ports/slot-1 --controller CONSOLE_PRIVATE_IPV4 \
  --controller-cert-file /private/ports/control-leaf.pem \
  --controller-pin VERIFIED_CONTROL_SHA256

.venv/bin/local-aiport-nas-compose --plan /private/ports/plan.json \
  --slot-state 1=/private/ports/slot-1 \
  --controller-ip CONSOLE_PRIVATE_IPV4 --controller-pin VERIFIED_CONTROL_SHA256 \
  --nas-ip HOST_LAN_IPV4 --subnet LAN_CIDR --gateway LAN_GATEWAY_IPV4 \
  --parent VERIFIED_ETHERNET_INTERFACE --image local-aiport:reviewed-source \
  --uid NAS_NONROOT_UID --gid NAS_NONROOT_GID --project-name local-aiport \
  --output /private/ports/compose.json
```

Keep the generated identity and private directory permanently. Conflicting or
incomplete existing state is refused, not silently repaired. Review the complete
manifest before startup, including image, user, state bind, network and address.
Use the [existing-runtime layout input](aiport-nas-compose.md#explicit-existing-nas-runtime-layout)
only for a separately reviewed model mount or internal health network.

## 5. Start, adopt and configure one camera

Starting makes real controller connections and can create a pending device:

```sh
docker compose --file /private/ports/compose.json --project-name local-aiport config --quiet
docker compose --file /private/ports/compose.json --project-name local-aiport up -d
docker compose --file /private/ports/compose.json --project-name local-aiport ps
```

Verify management reachability from the console's network. Adopt in Protect,
then deliberately pair one intended camera. The plan alone does not grant stream
or inference permission. Use [candidate configuration](aiport-candidate.md) and
[control-site workflows](device-workflows.md) to prepare the explicit camera,
provider/model and detector policy. Keep all camera IDs, keys and model files
private. A cloud detector sends selected frames to its configured service.

Check adopted/control-connected state, the intended pairing, stream decoding,
actual inference and the saved class/event on the original camera separately.
Refresh Protect and test a planned restart. Add further cameras only within the
actual stream-dimension capacity and measured resource/provider budget.
Full quality, alarm parity and sustained-operation qualification remain separate.

## Stop and update

Stop only the intended project or service. Retain its private state and previous
image. Follow [state-preserving updates](deployment.md#5-run-unattended-update-and-recover),
[uninstall/restore](uninstall-restore.md) and the
[network reconciliation contract](aiport-nas-compose.md) before replacing a slot.
Never run two copies of the same adopted identity or delete its state to fix
pairing. Address reservation, native adoption and native pairing remain deliberate
host/controller operations, not side effects of a capacity plan.
