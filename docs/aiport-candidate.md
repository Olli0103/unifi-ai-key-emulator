# Isolated AI Port candidate

This profile is for bounded protocol tests. It presents a separate AI Port identity, keeps an outbound certificate-pinned camera WebSocket to Protect and exposes an HTTPS management listener. Camera ingress is off unless a private, expiring one-camera diagnostic policy enables it. The profile has no camera credentials, AI detection or adopted state. Its `/api/1.2/manage` route records only recognized JSON field names in memory and returns HTTP 501. One legacy camera paired during a bounded stream test; detection delivery remains unverified.

Protect 7.3.60 showed the separate candidate in Devices. An early legacy-camera pairing attempt displayed **Unable to Pair** while the device panel said **Connecting**. The candidate's temporary Mac listener on host port 8443 had no management requests. A later live trial proved that camera pairing uses the WebSocket stream command and can succeed without that management request. See [firmware and native discovery evidence](evidence/ai-port-firmware-contract.md).

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

The relay exits after ten minutes or SIGINT/SIGTERM. Its final counters contain no request content. The candidate still returns HTTP 501 to management requests, so this trial can reveal only the sanitized management request shape. Camera stream control uses the separate WebSocket path described below. Stop the relay immediately after the single management request has been checked.

`GET /healthz` reports a successful WebSocket upgrade, whether the connection remains open, and counts of binary and text application frames with only the last frame length. It never reports frame content. A successful upgrade proves only candidate discovery; a frame count only proves transport activity. A management request count or HTTP response cannot prove adoption, pairing, camera streaming or detection. Static Protect 7.2.105 code routes camera pairing through a WebSocket stream-control request, while the management endpoint is used for adoption. Test those flows separately and keep camera pairing disabled until the wire contract and camera rollback are verified.

For a controlled hello experiment, a private config may include `diagnostic_hello_until`, a Unix timestamp no more than ten minutes ahead. This is off by default. During the window, the candidate sends a minimal `ubnt_avclient_hello`, answers parameter agreement and the read-only `GetStreamList` request with an empty list, and explicitly rejects `UiStreamControl` and `OnvifStreamControl` unless the separate one-camera stream diagnostic below is configured. It records only allowlisted command names and counters, never payloads, and closes the WebSocket at expiry. A reconnect after expiry is passive. An empty stream list and explicit refusal must not be presented as camera pairing support.

An opt-in stream diagnostic can now add `diagnostic_stream` to that same private config. It requires `diagnostic_hello_until`, one exact camera MAC, one exact private IPv4 stream source, and an absolute executable path to `ffmpeg` inside the container. The default image includes `/usr/bin/ffmpeg`. Once the timestamp expires, a restart comes up passive. This is an operator-controlled one-camera test, not automatic pairing or a detection service:

```json
{
  "diagnostic_hello_until": 1790000000,
  "diagnostic_stream": {
    "camera_mac": "2A1122334455",
    "source_ip": "PRIVATE_PROTECT_STREAM_SOURCE_IPV4",
    "ffmpeg_path": "/usr/bin/ffmpeg"
  }
}
```

The timestamp above is a placeholder, not a usable activation. The stream receiver accepts only the configured camera and source, TCP 7447 and a simple RTSP alias. It reports `started` only after decoding a bounded JPEG frame and keeps frames in memory. During the expiring diagnostic, a control WebSocket disconnect gives an active decoder at most 15 seconds to complete a fresh parameter agreement; otherwise the decoder stops. The decoder also stops at diagnostic expiry, on an explicit stop command, or when the service stops. This reconnect behavior passed a synthetic controller test; whether it resolves the live reconnect pattern is `needs_evidence`. The candidate does not run a model, emit a smart event, or retain video. FFmpeg error text can contain a private stream URL, so the candidate never logs or returns it. Health reports only fixed error labels and terms from a fixed vocabulary, a numeric decoder exit status, and whether FFmpeg produced error text. A Protect 7.3.60 trial decoded frames and paired one legacy camera after switching to FFmpeg's RTSP-specific `-timeout` option. The camera was unpaired and the candidate returned to passive mode after the test. Detection delivery and permanent operation remain `needs_evidence`; do not leave a camera paired after a diagnostic until those are verified.

During an expiring diagnostic, `/healthz` also reports counts for fixed, allowlisted WebSocket `functionName` values. The allowlist includes the observed handshake and stream commands plus selected controller/firmware-known status, time-sync and detection names. Other names and unparseable binary frames have counts only; their names and payloads are not returned. `stream_reconnects_preserved` and `stream_grace_closures` show whether the bounded reconnect path ran without exposing a stream alias or frame. A bounded Protect 7.3.60 trial preserved one stream across a reconnect. Protect then sent `ResetAIPortStreams`; the first deployed build counted but did not answer it. The current implementation closes the bounded decoder before acknowledging a well-formed reset. A second native trial observed that response, but Protect did not request a new stream on the following reconnects. The reason for this and the remaining unclassified frames is `needs_evidence`; a synthetic controller test alone does not prove durable native streaming.

The candidate also counts WebSocket close codes, retaining at most 16 distinct numeric codes. It never records close reasons, which may contain private device data. `last_disconnect_origin` distinguishes expiration of our own diagnostic timer from a peer or transport close; the latter cannot identify the exact initiator by itself.

During an enabled stream diagnostic, a successful stream-control response now sends a per-camera `EventAIPortStatus` with the observed streaming state. It always reports smart-detection and audio-event readiness as false because this candidate has neither service. This event remains part of the expiring one-camera diagnostic, not a permanent AI Port implementation.
