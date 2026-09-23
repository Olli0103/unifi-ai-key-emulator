# Isolated AI Port candidate

This profile is for bounded protocol tests. It presents a separate AI Port identity, keeps an outbound certificate-pinned camera WebSocket to Protect and exposes an HTTPS management listener. Camera ingress is off unless a private, expiring one-camera diagnostic policy enables it. The profile has no camera credentials and does not advertise AI detection by default. Its management-token adoption handshake has passed synthetic tests; a separate existing-device reconnect was accepted by Protect 7.3.60 and survived a container restart. One legacy camera paired and supplied frames to the optional local model during a bounded test; native detection delivery remains unverified.

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

The relay exits after ten minutes or SIGINT/SIGTERM. Its final counters contain no request content. Without a private adoption window, the candidate rejects management requests and retains only their sanitized field shape. Camera stream control uses the separate WebSocket path described below. Stop the relay immediately after the single management request has been checked.

The optional `diagnostic_adoption_until` config field is a Unix timestamp no more than ten minutes ahead. A management request during that window must present the controller-rotated credential and a `wss` token with a host list containing the certificate-pinned controller's exact address and control port. The candidate writes a pending token to a mode-600 file, then reconnects to that controller with the token. It marks itself adopted only after a verified WebSocket upgrade and removes the token from the adopted file. The adopted record is bound to the controller address, certificate pin and port; a changed destination cannot reuse it. The synthetic tests cover these transitions and rejection cases. Fresh management-token adoption and reset behavior on Protect 7.3.60 remain `needs_evidence`. This window does not enable any camera stream; that still requires the separate one-camera stream diagnostic.

For a device that Protect already treats as adopted and whose certificate and identity have not changed, `diagnostic_resume_until` allows a separate, ten-minute tokenless reconnect. It cannot coexist with `diagnostic_adoption_until`. The candidate advertises its existing adopted state only during that window and saves a controller-bound adopted record only after the pinned WebSocket upgrade. Protect 7.3.60 accepted this path for the existing candidate. After removing the window and restarting the container, Protect still showed the AI Port Online; the private record contained no token, and stream ingestion stayed off. This does not validate fresh management-token adoption or authorize pairing more cameras.

`GET /healthz` reports a successful WebSocket upgrade, whether the connection remains open, and counts of binary and text application frames with only the last frame length. It never reports frame content. A successful upgrade proves only candidate discovery; a frame count only proves transport activity. A management request count or HTTP response cannot prove adoption, pairing, camera streaming or detection. Static Protect 7.2.105 code routes camera pairing through a WebSocket stream-control request, while the management endpoint is used for adoption. Test those flows separately and keep camera pairing disabled until the wire contract and camera rollback are verified.

For a controlled hello experiment, a private config may include `diagnostic_hello_until`, a Unix timestamp no more than ten minutes ahead. This is off by default. During the window, the candidate sends a minimal `ubnt_avclient_hello`, answers parameter agreement and the read-only `GetStreamList` request with an empty list, and explicitly rejects `UiStreamControl` and `OnvifStreamControl` unless the separate one-camera stream diagnostic below is configured. It records only allowlisted command names and counters, never payloads, and closes the WebSocket at expiry. A reconnect after expiry is passive. An empty stream list and explicit refusal must not be presented as camera pairing support.

An opt-in stream diagnostic can now add `diagnostic_stream` to that same private config. It requires `diagnostic_hello_until`, one exact camera MAC, one exact private IPv4 stream source, and an absolute executable path to `ffmpeg` inside the container. The default image includes `/usr/bin/ffmpeg`. Once the timestamp expires, a restart comes up passive. This is an operator-controlled one-camera test, not automatic pairing or a detection service:
The stream source is the `ip` in Protect's `UiStreamControl` command, not necessarily the camera's own address. On the tested Protect 7.3.60 console, Flur paired only when this policy named the pinned controller address; using Flur's camera address failed closed with `stream_source_not_authorized`. Do not infer a source address for another installation without a scoped test.

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

The timestamp above is a placeholder, not a usable activation. The stream receiver accepts only the configured camera and source, TCP 7447 and a simple RTSP alias. It reports `started` only after decoding a bounded JPEG frame and keeps frames in memory. During the expiring diagnostic, a control WebSocket disconnect gives an active decoder at most 15 seconds to complete a fresh parameter agreement; otherwise the decoder stops. The decoder also stops at diagnostic expiry, on an explicit stop command, or when the service stops. This reconnect behavior passed a synthetic controller test; whether it resolves the live reconnect pattern is `needs_evidence`. Without the optional detector probe below, the candidate runs no model. It emits no smart event and retains no video in either mode. FFmpeg error text can contain a private stream URL, so the candidate never logs or returns it. Health reports only fixed error labels and terms from a fixed vocabulary, a numeric decoder exit status, and whether FFmpeg produced error text. A Protect 7.3.60 trial decoded frames and paired one legacy camera after switching to FFmpeg's RTSP-specific `-timeout` option. The camera was unpaired and the candidate returned to passive mode after the test. Detection delivery and permanent operation remain `needs_evidence`; do not leave a camera paired after a diagnostic until those are verified.

An optional local detector probe can inspect at most three decoded frames from that same camera and window. Build a separate image with `--build-arg ENABLE_LOCAL_DETECTOR=1`; this installs pinned CPU-only PyTorch wheels from the official PyTorch index, then RF-DETR. Mount an operator-supplied RF-DETR Nano `.pth` checkpoint read-only inside the container, and add this private config alongside `diagnostic_stream`:

```json
{
  "diagnostic_detector": {
    "checkpoint_path": "/models/rf-detr-nano.pth",
    "checkpoint_sha256": "SHA256_OF_THE_EXACT_LOCAL_CHECKPOINT",
    "threshold": 0.5,
    "max_frames": 3
  }
}
```

The checksum placeholder must be replaced with 64 hexadecimal characters. The adapter verifies the local checkpoint before importing the optional model package and never selects a default download. The bounded frame queue drops samples while inference is busy; a model failure disables further observations for that stream without changing Protect's stream response. A bounded in-memory tracker now requires two overlapping, same-class observations before it records an internal enter transition. It records a leave after three seconds without a match, keeps at most 32 tracks and drops new candidates when full. Health exposes only attempted/successful frame, object and track-transition counts plus a fixed failure code. It retains no model boxes, classes, frames or stream aliases in health or on disk. These internal transitions are not native Protect events. The [RF-DETR documentation](https://rfdetr.roboflow.com/latest/learn/run/detection/) lists Nano as an Apache-2.0 COCO detector and describes its image-prediction API; its published latency is measured on NVIDIA T4, not this Mac or the UGREEN NAS. A disposable 1 GiB Apple container loaded the pinned 366 MB RF-DETR Nano checkpoint and correctly identified the dog, person and background car in the documentation's public sample image after the COCO category map was corrected. That one run peaked at about 851 MiB before an RTSP decoder or control service was added.

In a live Protect 7.3.60 trial, the adopted AI Port ran the CPU-only detector image with a 2 GiB container limit and paired one G3 camera. It decoded the native stream and completed three of three permitted model calls without an error. Those frames yielded zero object observations; no precision or recall conclusion follows. Protect showed the camera paired during the test. The camera was then unpaired and remained Online at its original HD/Adaptive setting. The separate AI Key stayed Online. The candidate restarted from the passive image and private base configuration; pinned health showed adoption and control connected, stream ingest off, and zero frames. No camera image, model output or stream alias was saved. This proves live model execution in the bounded stream path, not native detection delivery. The candidate reports `isSmartDetectReady: false` by default and emits no `EventSmartDetect`. Sustained resource use, tracking, settings, zones, event timing, original-camera timeline, alarms, face recognition, LPR and speech remain `needs_evidence`.

The independent ingress pool can route commands for up to five explicitly allowed cameras and hold no more than ten capacity points per AI Port. It charges two points for HD, three for 2K and five for 4K, counting a stalled decoder until closed. Its synthetic tests cover two 4K streams, five HD streams, unlisted cameras and source-address changes. An expiring `diagnostic_streams` private configuration now connects that pool to the adopted candidate's stream-control and per-camera status paths. It accepts two to five exact camera/source entries and cannot coexist with detector, smart-settings or native-event permits. Smart readiness stays false for every pooled camera. This path has synthetic tests only; multi-camera native pairing and detection remain `needs_evidence`.

An independent `CameraPolicyEngine` now keeps parsed person policies, zone filters, reverification thresholds and temporal tracks separate for up to five cameras. Synthetic interleaved observations show that replacing one policy closes only that camera's candidate event. The engine is not connected to the pooled streams or native transport. Shared model scheduling, delivery acknowledgement, fault isolation and Protect timeline acceptance remain open in [#79](https://github.com/Olli0103/unifi-ai-key-emulator/issues/79).

During an expiring diagnostic, `/healthz` also reports counts for fixed, allowlisted WebSocket `functionName` values. The allowlist includes the observed handshake and stream commands plus selected controller/firmware-known status, time-sync and detection names. Other names and unparseable binary frames have counts only; their names and payloads are not returned. `stream_reconnects_preserved` and `stream_grace_closures` show whether the bounded reconnect path ran without exposing a stream alias or frame. A bounded Protect 7.3.60 trial preserved one stream across a reconnect. Protect then sent `ResetAIPortStreams`; the first deployed build counted but did not answer it. The current implementation closes the bounded decoder before acknowledging a well-formed reset. A second native trial observed that response, but Protect did not request a new stream on the following reconnects. The reason for this and the remaining unclassified frames is `needs_evidence`; a synthetic controller test alone does not prove durable native streaming.

The candidate also counts WebSocket close codes, retaining at most 16 distinct numeric codes. It never records close reasons, which may contain private device data. `last_disconnect_origin` distinguishes expiration of our own diagnostic timer from a peer or transport close; the latter cannot identify the exact initiator by itself.

An optional private `diagnostic_function_fingerprints_until` timestamp, limited to ten minutes, adds at most eight short SHA-256 function-name fingerprints to `/healthz`. It never records command payloads or raw unknown names, remains off by default, and disappears from health output at expiry. A no-camera trial matched one fingerprint against the inspected controller package: Protect sent `UpdateFaceDBRequest` to the connected AI Port. The candidate answers that command with an explicit unsupported status. It does not fetch or retain the face-database URL and does not advertise face recognition as ready.

During an enabled stream diagnostic, a successful stream-control response sends a per-camera `EventAIPortStatus` with the observed streaming state. Smart-detection and audio-event readiness are false by default. A separate expiring, one-camera probe temporarily sends `EventFeatureFlagsUpdated` with a person capability and reports smart readiness. The candidate classifies a narrow `ChangeSmartDetectSettings` subset for the exact camera: full-frame person, vehicle and animal detection, or primary-lens person polygons, with bounded event timing. Secondary-lens zones, lines, exclusions, tamper, access triggers and PTZ remain unsupported. The settings-only probe counts matching requests and still replies unsupported; it never retains the nested policy.

An additional private `diagnostic_event_until` field can enable one person-event trial. It must equal both the stream and smart-probe deadline, which can be at most ten minutes ahead. It also requires a local detector policy with 2-120 frame attempts. Only an exact person setting for the selected camera receives an acknowledgement. If person reverification is enabled with a valid integer probability range, the candidate drops observations at or below its upper bound before temporal tracking; it does not perform the requested second-stage verification. For a supported primary-lens zone, the candidate requires the whole person box strictly inside a simple polygon, then includes that zone ID in the native event candidate. Configured zones with unsupported geometry or access triggers are rejected. This conservative geometry rule can miss people on a zone boundary and does not calibrate camera zone sensitivity. A later disabled or unsupported setting revokes the policy. After two overlapping qualifying model observations, the in-memory tracker may send one `EventSmartDetect` enter for that person and a leave when the track disappears. The payload includes a wall-clock timestamp and normalized box, but no image, face, plate, speech or camera credential. Protect 7.3.60 accepted the bounded full-frame person settings acknowledgement while a legacy G3 camera was paired, but no person observation appeared in that trial. A G5 camera previously sent a configured smart zone and was rejected; the new zone path has synthetic tests but has not been retried live. Native zone acceptance, event persistence on the original camera timeline, full reverification and alarms remain `needs_evidence`. The default passive profile emits no smart events.

For a hello-only provisioning probe, the candidate answers empty `ChangeVideoSettings` and `ChangeIspSettings` queries with a minimal no-camera profile. Protect 7.3.60 requested both in that order during a no-camera trial. `unlisted_envelope_counts` divides other functions into request, response and other shapes without saving their names or payloads. The fixed allowlist also counts `StartService`, `StopService`, `UpdateUsernamePassword` and `ChangeSoundLedSettings`, which Protect 7.2.105 may send after provisioning. Because this container has no SSH server, it acknowledges a request to stop SSH and rejects a request to start it. An exact `UpdateUsernamePassword` payload received during an expiring diagnostic is acknowledged only after its SHA-512 crypt hash is atomically stored in a mode-600 private file. The candidate never stores the plain password. HTTPS `/api/1.2/login` checks the new password, and `/api/1.2/manage` rejects incorrect credentials. Sessions are short-lived, rate-limited and invalidated on rotation. The container includes OpenSSL for verification of the controller's hash format. In a no-camera Protect 7.3.60 trial, the candidate persisted one credential rotation and replied successfully; Protect then sent `ChangeSoundLedSettings`. Native login and subsequent provisioning remain `needs_evidence`. The diagnostic ended with the candidate passive and no camera paired. These replies do not prove adoption or durable operation.

The candidate now accepts the exact AI Port `ChangeSoundLedSettings` and timezone-only `ChangeDeviceSettings` payloads during that same expiring diagnostic. It validates and persists them as private logical state before replying. This container has no physical LED or speaker; these replies do not claim a light or sound occurred. Protect 7.3.60 accepted both replies in no-camera trials. A subsequent five-minute, single-Flur trial paired the camera, decoded 129 frames without a reported stream error, and received an explicit stop when Flur was unpaired. No control reconnect happened during that brief interval. The camera returned to Online HD/Adaptive, the separate AI Key remained Online, and the candidate restarted passively with zero frames. This demonstrates bounded initial streaming after the settings sequence, not permanent operation, adoption, smart detection, or AI Port feature parity.
