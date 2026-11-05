# Fresh AI Key on Linux/NAS without editing JSON

This recipe prepares one new Key, not a replacement identity, AI Port, search
database or automatic camera policy. Follow [deployment requirements](deployment.md)
and choose a reviewed source commit first. An available model server and
independently verified controller certificate fingerprint are prerequisites.
No model weights or provider keys are copied from another installation.

## Isolate the new installation

Use a fresh checkout and the standalone [Compose file](../compose.first-run.yaml),
not an overlay on `compose.yaml`. The project is `local-aikey-first-run`; choose
a different explicit Compose project name if that name already exists. Do not
start a second copy of any existing identity. Linux host networking requires a
free management port (8080 by default) and a controller-reachable host address.
Restrict that port to the controller/private management peers before starting.
No firewall or router rule is changed by these commands.

Copy the example environment and set `AIKEY_UID`/`AIKEY_GID` to the numeric
account owning the new directory. The optional-search `CONSOLE_IP` variable is
unused by this recipe. No secret goes in `.env`:

```sh
cp examples/nas.env .env
docker compose -f compose.first-run.yaml config --quiet
docker compose -f compose.first-run.yaml build emulator
mkdir -m 700 first-run-state
chmod 700 first-run-state
```

An existing `first-run-state` is not a fresh installation: inspect it rather
than deleting/reinitializing it. It is excluded from Git, Docker build context
and source distributions. The parent mounts at `/state` for preparation and
runtime; setup exclusively creates `/state/key` with config and nested private
`state/`. Keep these mounts/paths unchanged.
The explicit `chmod` handles hosts that override a requested creation mode;
setup also hardens and verifies its new directory before creating an identity.

## Select a model and prepare

Replace all uppercase placeholders and documentation-only addresses. This
example selects an already installed vision model in Ollama on the NAS host;
with host networking, loopback is the host's loopback. Choose the actual
version-supported control profile and verified pin:

```sh
docker compose -f compose.first-run.yaml run --rm --no-deps emulator setup \
  --directory /state/key --controller 192.0.2.1 --device-ip 192.0.2.110 \
  --protect-version YOUR_PROTECT_VERSION --control-profile device-service \
  --controller-fingerprint VERIFIED_SHA256 \
  --provider ollama --model INSTALLED_VISION_MODEL \
  --ffmpeg-path /usr/bin/ffmpeg --request-mp4-exports --linux-discovery
```

For another provider, use the existing [provider flags](first-run.md). A remote
endpoint requires explicit `--allow-remote`; remote plaintext HTTP additionally
requires `--allow-insecure-http`. A private endpoint may forward data elsewhere;
review its actual destination, retention and cost before enabling inference.
Keys remain private mounted file references, never command-line values.

`--linux-discovery` prepares the existing Linux responder on UDP 10001, with
multicast membership on the advertised device interface and responses restricted
to the single literal controller IPv4 address. Setup itself opens no socket.
The port must be unused on the host, and the controller must reach the advertised
management address. Without discovery, an unadopted control connection can create
a native candidate without a management address; an adoption HTTP 200 alone is
not successful adoption. This flag does not enable factory credentials, search,
camera processing or automatic adoption, and does not relax the macOS runtime
guard. Linux NAS discovery/adoption still requires the actual native check.

If the certificate does not cover the chosen address, explicitly add
`--pin-only-hostname` with the independently verified pin. If the console's
adoption flow needs factory credentials, add `--factory-enrollment-seconds 600`
to this same preparation command. That clock starts immediately, not at runtime
startup. Import trust and attempt adoption within ten minutes. Default factory
enrollment is off; expiry/restart never renews it, and successful adoption/password
rotation uses the existing disabling guards. Do not rerun setup into retained
state if an attempt expires or its outcome is uncertain.

## Trust, start and obtain the native result

```sh
docker compose -f compose.first-run.yaml run --rm --no-deps emulator trust \
  --config /state/key/config.json --channel control --fingerprint VERIFIED_SHA256
docker compose -f compose.first-run.yaml run --rm --no-deps emulator check \
  --config /state/key/config.json
docker compose -f compose.first-run.yaml up emulator
```

Stop at any failed command; do not proceed past a failed readiness check.
Trust import verifies the explicit public certificate pin, not controller login.
`check` is local readiness, not proof that the model can answer. `up` starts real
controller connections. The recipe has no automatic restart policy while first
adoption is being checked.

Adopt only this intended new device in Protect. Then follow the
[one-camera native verification](deployment.md#4-start-adopt-and-verify-one-camera)
and [control-site device workflows](device-workflows.md) to review the selected
model and authorize a bounded camera scope. Preparation itself enables no
continuous processing. Verify a real result on its original native event after
reload; a setup receipt or callback acknowledgement is not that result.

Stop the foreground process with Ctrl-C or explicitly stop this project:

```sh
docker compose -f compose.first-run.yaml stop emulator
```

Retain `first-run-state/key` and its private nested state. This recipe has no
database or named volume to delete. Do not use global pruning, delete an adopted
identity, or move this layout into the lower-level search recipe. Follow
[uninstall and restore](uninstall-restore.md) for native removal and state choices.
Search and unattended operation require their own preparation/acceptance.

This is the no-JSON preparation/deployment path; a complete fresh
install/adopt/model/native-result observation must still be recorded separately.
