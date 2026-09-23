# Isolated AI Port candidate

This profile is for bounded protocol tests. It presents a separate AI Port identity, keeps an outbound certificate-pinned camera WebSocket to Protect and exposes an HTTPS management listener. It has no camera credentials, stream receiver, pairing handler, AI detection or adopted state. Its `/api/1.2/manage` route records only recognized JSON field names in memory and returns HTTP 501. Do not use Protect's Pair action as a test of detection until the native management and stream contracts are implemented.

Protect 7.3.60 showed the separate candidate in Devices. A later Flur pairing attempt displayed **Unable to Pair** while the device panel said **Connecting**. This is not evidence of a paired camera. The candidate's temporary Mac listener on host port 8443 had no management requests; Protect expects the device's fixed HTTPS 443 endpoint. See [firmware and native discovery evidence](evidence/ai-port-firmware-contract.md).

## Private configuration

Create a separate state directory per instance with mode 700. Supply a newly generated, locally administered unicast MAC; `device.crt` and `device.key`; the independently pinned controller CA certificate; and a mode-600 `config.json` with exactly these fields:

```json
{
  "controller_ip": "PRIVATE_CONSOLE_IPV4",
  "device_ip": "DISTINCT_AI_PORT_LAN_IPV4",
  "mac": "NEW_LOCAL_UNICAST_MAC_NO_COLONS",
  "controller_pin": "VERIFIED_CONTROLLER_SHA256_HEX",
  "firmware_version": "5.1.12"
}
```

Keep the identity and TLS files private and persistent. Do not reuse the AI Key identity. The firmware version is a compatibility hint from the inspected official package, not a claim that this independent implementation runs that firmware.

## Mac network test

Build the dedicated image from source and run it as the state directory's owner. The container listens internally on unprivileged TCP 8443:

```sh
container build --platform linux/arm64 --file Dockerfile.aiport-candidate \
  --tag local-aiport:candidate .
container run --detach --name local-aiport-candidate \
  --user "$(id -u):$(id -g)" --read-only \
  --tmpfs /tmp:size=64m,mode=1777 \
  --volume "${AIPORT_STATE}:/state" \
  --publish "${AIPORT_LAN_IP}:8443:8443/tcp" \
  local-aiport:candidate --config /state/config.json --port 8443
```

The host's port 8443 is only a transport check. Protect's device management uses HTTPS 443 on the advertised address. Apple container 1.4.1 refused a host 443 publish on this Mac with a privileged-port error. The optional [bounded relay](../src/aikey/aiport_relay.py) can bind host 443 after local administrator authentication, then drops to the named non-root UID/GID. It forwards raw TLS bytes to 8443 for at most ten minutes and accepts only the console's exact source IP. It does not read HTTP bodies, modify firewall rules or persist credentials. A tested NAS network mode is the alternative deployment path.

For one diagnostic trial, start the candidate first and verify its `control_connected` health field. Then run the relay from a local Terminal, entering the macOS administrator password there, not in chat or a file:

```sh
sudo /opt/homebrew/bin/python3 -I /absolute/project/src/aikey/aiport_relay.py \
  --listen-ip AI_PORT_LAN_IP --controller-ip CONSOLE_LAN_IP \
  --drop-uid "$(id -u)" --drop-gid "$(id -g)"
```

The relay exits after ten minutes or SIGINT/SIGTERM. Its final counters contain no request content. The candidate still returns HTTP 501 to management requests, so this trial can reveal only the sanitized request shape, not achieve adoption or camera pairing. Stop the relay immediately after the single request has been checked.

`GET /healthz` reports a successful WebSocket upgrade, whether the connection remains open, and counts of binary and text application frames with only the last frame length. It never reports frame content. A successful upgrade proves only candidate discovery; a frame count only proves transport activity. A management request count or HTTP response cannot prove adoption, pairing, camera streaming or detection. Static Protect 7.2.105 code routes camera pairing through a WebSocket stream-control request, while the management endpoint is used for adoption. Test those flows separately and keep camera pairing disabled until the wire contract and camera rollback are verified.
