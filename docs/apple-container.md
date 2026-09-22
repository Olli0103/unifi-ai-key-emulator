# Apple container on macOS

This guide uses Apple container 1.4.1 on an Apple silicon Mac. The Linux ARM64 image has been built and run against Protect 7.3.56 on a UDM Pro Max. Native discovery, adoption, an online control connection, matching time synchronization, management-password rotation and reconnect after a planned restart were observed. Factory authentication was rejected after enrollment.

Later tests on Protect 7.3.60 verified one native on-demand description and one automatic G5 Flex event caption through OpenAI. The on-demand route returned its caption without persisting it. The automatic route saved the caption to the event, verified by an exact-event GET after a full Safari page reload. Each used a separate single-use camera permit; continuous operation remains unverified. Face/plate recognition, audio and full legacy-camera enhancement are not implemented. Search and PostgreSQL remained disabled. NAS deployment is untested. The static protocol research uses Protect 7.2.105 and AI Key firmware 2.2.8, distinct from these live checks.

Install the release from [Apple's 1.4.1 release page](https://github.com/apple/container/releases/tag/1.4.1). The commands below use its [command reference](https://github.com/apple/container/blob/1.4.1/docs/command-reference.md). Use a dedicated directory outside the repository for state and replace the uppercase address placeholders. No real LAN addresses or API secrets belong in this guide.

## Build and initialize

Run from the project directory. The state directory holds the device identity, TLS key, controller trust, credentials and job journal. Keep it when replacing the container or image.

```sh
AIKEY_STATE="/absolute/private/path/aikey-state"
AIKEY_IMAGE="local-aikey:mac-arm64"
AIKEY_MAC_IP="MAC_LAN_IPV4"
AIKEY_CONTROLLER="CONSOLE_HOST_OR_IP"
AIKEY_HOST_USER="$(id -u):$(id -g)"
AIKEY_DNS="REACHABLE_DNS_IPV4"
mkdir -p "$AIKEY_STATE"
chmod 700 "$AIKEY_STATE"
container system start
container builder start --cpus 4 --memory 3G --dns "$AIKEY_DNS"
container build --platform linux/arm64 --dns "$AIKEY_DNS" --tag "$AIKEY_IMAGE" .

aikey_local() {
  container run --rm --dns "$AIKEY_DNS" --user "$AIKEY_HOST_USER" \
    --read-only --tmpfs /tmp:size=256m,mode=1777 \
    --volume "$AIKEY_STATE:/state" "$AIKEY_IMAGE" "$@"
}

aikey_local init --config /state/config.json --state-dir /state \
  --controller "$AIKEY_CONTROLLER" --device-ip "$AIKEY_MAC_IP"
```

The explicit UID and GID let the process write its host-owned state mount. The image's root filesystem stays read-only; `/tmp` is temporary scratch space. `init` generates credentials locally and refuses to overwrite an existing configuration. Reuse existing state only after checking its paths and ownership.

Use a DNS server reachable from the guest, such as the LAN router. In the tested installation, the default virtual gateway did not answer DNS queries, while the router did. Set DNS on both the builder and application containers. After building, `container builder stop` releases its resources without stopping application containers.

## Configure the first connection

Edit the generated `config.json` in the private state directory. Preserve its generated identity and credential paths. Set these values in their existing sections:

| Setting | Initial value |
| --- | --- |
| `runtime.deployment` | `Apple container on macOS` |
| `runtime.bind` | `0.0.0.0` |
| `runtime.https_port` | `8080` |
| `runtime.enable_http` | `false` |
| `device.ip` | Mac's LAN IPv4 address |
| `controller.host` | Console hostname or address reachable from the container |
| `controller.control_profile` | `device-service` for the tested Protect 7.3.56 frontend; requires an independently verified certificate pin |
| `discovery.enabled` / `discovery.multicast` | `false` / `false` |
| `search.enabled` / `database.enabled` | `false` / `false` |
| `worker.description_embeddings` | `false` |
| `worker.ffmpeg_path` | `/usr/bin/ffmpeg` |

`runtime.bind` is inside the Linux container. Binding to the Mac's LAN address there will fail because that address belongs to the host. `device.ip` is the address advertised to Protect, so it must identify the Mac's published management endpoint. `runtime.deployment` is a report label only, up to 128 printable characters; leaving it out reports `unspecified`.

Create a private `openai-key` file in the state directory using a trusted local editor, then restrict it to mode 600. Do not put the key value in shell commands or logs. Select the chosen vision model by its API identifier:

```sh
aikey_local provider openai --config /state/config.json \
  --model VISION_MODEL_ID --api-key-file /state/openai-key
```

This command only saves configuration. During inference, OpenAI receives the selected camera frames. See [provider configuration](providers.md) for request behavior and other providers. Container loopback addresses refer to the container itself, so a model server on the Mac needs a separately verified reachable endpoint.

Verify the controller's SHA-256 certificate fingerprint independently, then import trust:

```sh
aikey_local trust --config /state/config.json --fingerprint VERIFIED_SHA256
aikey_local check --config /state/config.json
```

Trust import makes a TLS handshake but sends no adoption request. Keep hostname verification enabled when the certificate covers `controller.host`. Otherwise follow the explicit certificate-pin configuration in the [main README](../README.md). The `device-service` profile requires `controller.expected_fingerprint` even when hostname verification succeeds. A passed check confirms local configuration only; each new controller or network still needs its own acceptance check.

## Start the authorized device test

Verify that port 8080 is free on the chosen Mac interface. Publish only the HTTPS management port for this first test:

```sh
container run --detach --name local-aikey-mac --dns "$AIKEY_DNS" \
  --user "$AIKEY_HOST_USER" --read-only --tmpfs /tmp:size=256m,mode=1777 \
  --volume "$AIKEY_STATE:/state" \
  --publish "${AIKEY_MAC_IP}:8080:8080/tcp" \
  "$AIKEY_IMAGE" run --config /state/config.json

container logs local-aikey-mac
```

`run` initiates controller connections and may create a candidate processor record. The controller must be able to reach the Mac's port 8080 through the host firewall. Outbound controller connections use ports 7442 and 7444 for control and media/callback traffic. Search port 7443 remains unused while search is disabled.

New configurations use management username `ui` with a random password in the private `management-password` file. The tested Protect 7.3.56 adoption panel had no custom-password field, so ordinary adoption initially failed authentication. The [temporary factory enrollment](factory-enrollment.md) window allowed native adoption while preserving the generated password file. The controller then rotated the management password over the confirmed control connection; factory authentication stopped working. Enable that bounded window immediately before an authorized adoption attempt. Use the generated password instead if a controller offers a credentialed flow.

Existing configurations retain their explicit username. If an older configuration uses `local-aikey`, align it deliberately before adoption unless the target controller provides a working username override. Keep the generated private password and persistent certificate/key.

## Networking limits and later search work

Apple's default network uses NAT. Published ports pass through a proxy that opens a separate connection to the container. The application therefore cannot assume it sees the original controller IP. This follows from the 1.4.1 `ConnectHandler.swift` and `UDPForwarder.swift` implementation, reviewed alongside `NetworkMode.swift` in the [release source](https://github.com/apple/container/tree/1.4.1/Sources).

Keep discovery disabled inside the container. Publishing UDP 10001 does not establish LAN multicast forwarding, and a proxied sender address conflicts with the emulator's exact controller-IP allowlist. Do not broaden that allowlist to make the test pass. The tested Mac setup used host-side discovery to populate the native candidate's address. For a separate host responder, use the opt-in companion below.

Keep search and PostgreSQL disabled until the network design is verified separately. The NAS recipe assumes Linux host networking and a PostgreSQL peer allowlist containing the console's actual address. A Mac published-port proxy changes that assumption. Dense search also needs the matched E5 backend, database migrations and credential synchronization before adoption rotates credentials. The [search contract](search-contract.md) and [database contract](database-contract.md) describe those requirements. This guide does not supply a working Mac search deployment.

Stop the test with `container stop local-aikey-mac`. Retain the state directory for recovery. Adoption, reconnect, native on-demand analysis and one persisted automatic caption passed in the tested setup. Continuous operation, search retrieval and longer recovery tests remain separate acceptance checks.

## Optional host discovery companion

The companion runs on macOS while control and inference stay in the container. It binds UDP 10001 on the host and joins `233.89.188.1` on the interface identified by `device.ip`. It answers only the configured controller's exact IPv4 address. Both addresses must be explicit LAN IPv4 values; a controller hostname is insufficient for this companion. Confirm that UDP 10001 is free before starting it.

From the project's existing Python environment on the Mac:

```sh
.venv/bin/python -m aikey.host_discovery \
  --config "$AIKEY_STATE/config.json" --state-dir "$AIKEY_STATE" \
  --allow-macos-host
```

This command runs in the foreground. SIGINT or SIGTERM stops it and closes the socket. It does not change the container's `discovery.enabled` setting. A host service manager can run this same command when unattended operation is intended.

The process reads the configuration and the public `device.crt` from the supplied host state directory. It never loads the management password, model API key or device private key. It pins the certificate's SHA-256 fingerprint while polling the runtime's HTTPS `GET /api/info` and `GET /healthz` endpoints. Redirects are rejected. It validates the reported MAC and adoption status before caching the reply data.

No replies leave the host before the first successful health check. A failed check immediately disables replies; a cached check expires after ten seconds even if polling stalls. Startup retries when the runtime is unavailable. Only information queries and queries for this device's MAC receive replies. There are no periodic UDP advertisements or mutation commands.

The private `host-discovery-status.json` file records health freshness, response/rejection counters and error class names. It contains no credential values. A reply counter proves that the companion answered a request, not that Protect accepted or adopted the device. Stop the companion before moving the test identity to another host.
