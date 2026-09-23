# Isolated AI Port candidate

For the Linux NAS multi-instance network manifest, see [AI Port NAS Compose](aiport-nas-compose.md).

This profile is for bounded protocol tests. It presents a separate AI Port identity, keeps an outbound certificate-pinned camera WebSocket to Protect and exposes an HTTPS management listener. Camera ingress is off unless a private, expiring diagnostic policy enables one camera or a bounded pool. The profile has no camera credentials and does not advertise AI detection by default. Its management-token adoption handshake has passed synthetic tests; a separate existing-device reconnect was accepted by Protect 7.3.60 and survived a container restart. One legacy camera paired and supplied frames to the optional local model during a bounded test. A later, one-use recorded-frame trial sent a native smart event and showed a Smart card on that camera's Protect timeline; continuous live detection and feature parity remain unverified.

Protect 7.3.60 showed the separate candidate in Devices. An early legacy-camera pairing attempt displayed **Unable to Pair** while the device panel said **Connecting**. The candidate's temporary Mac listener on host port 8443 had no management requests. A later live trial proved that camera pairing uses the WebSocket stream command and can succeed without that management request. See [firmware and native discovery evidence](evidence/ai-port-firmware-contract.md).

For a controlled detection trial, start `local-aiport-event-watch` before enabling a camera stream. It uses the pinned, read-only Protect integration API and [the official event subscription](https://developer.ui.com/protect/v7.2.105/get-v1subscribeevents):

```sh
local-aiport-event-watch --controller CONSOLE_PRIVATE_IPV4 \
  --api-key-file PRIVATE_PROTECT_API_KEY_FILE \
  --web-trust-file PRIVATE_WEB_TRUST_JSON \
  --web-cert-file PRIVATE_PINNED_WEB_CERT \
  --camera-scope legacy-and-g3-g5 --camera-name Flur --seconds 120
```

The watch caps its runtime at ten minutes and discards event bodies. `--camera-name` must exactly identify one currently eligible camera; omit it to watch the full selected scope. It counts `add` and `update` messages for that validated camera set, including smart-video and person fields on either message. For targeted smart-video messages, `smart_type_states` separately counts an omitted `smartDetectTypes` key, explicit null, empty list, list containing person, or another nonempty list. These are aggregate message shapes, not a readback of the saved event; an update can omit fields present in another message. A matching subscription message proves Protect announced an event for that camera during the watch. It does not identify whether the AI Port or the camera's onboard model caused it, and it does not prove recording-timeline persistence. Check the original camera timeline after an event appears. In the recorded-frame trial below, the previous watcher saw one targeted smart-video add and one update, but counted neither as person. Whether those messages omitted the field or contained an empty list is `needs_evidence`.

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

For a fully addressed multi-instance deployment, `local-aiport-host-relay` verifies every existing private slot identity against the saved plan and its controller certificate pin. It checks that each candidate is already reachable on its own address at TCP 8443 with the expected TLS certificate **before** binding any host TCP 443 listener and before forwarding each later connection. If one bind fails, all newly opened listeners close. It does not create identities, assign host addresses, change firewall rules or pair cameras. First run `--preflight-only`, which opens no listener:

```sh
local-aiport-host-relay --plan PRIVATE_ADDRESSED_PLAN_JSON \
  --slot-state 1=PRIVATE_SLOT_1_STATE_DIR \
  --slot-state 2=PRIVATE_SLOT_2_STATE_DIR \
  --controller-ip CONSOLE_LAN_IP --controller-pin VERIFIED_CONTROL_CERT_SHA256 \
  --preflight-only
```

Supply one `--slot-state` for **every** plan slot. The command refuses an unaddressed slot or a missing, changed, incomplete or world-readable identity. After a passing preflight, an administrator can bind fixed port 443 for the addressed slots; the process then drops to the specified non-root user. `--seconds 0` keeps it running until SIGTERM under a service manager. Use a supervisor that restarts it only against the same verified plan and state, and never overwrite an adopted identity to fill a new slot. Additional slots need distinct routed LAN addresses and candidate containers. The host relay is transport, not proof of native event delivery or production camera pairing.

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

The independent ingress pool can route commands for up to five explicitly allowed cameras and hold no more than ten capacity points per AI Port. It charges two points for HD, three for 2K and five for 4K, counting a stalled decoder until closed. Its synthetic tests cover two 4K streams, five HD streams, unlisted cameras and source-address changes. An expiring `diagnostic_streams` private configuration connects that pool to the adopted candidate's stream-control and per-camera status paths. It accepts two to five exact camera/source entries. Without the separate pool detector permit below, smart readiness stays false for every pooled camera. Multi-camera native pairing remains `needs_evidence`.

An optional private `diagnostic_pool_detector` and matching `diagnostic_pool_event_until` can now connect the pool to one local, checksum-pinned RF-DETR model for a test lasting at most ten minutes. The deadline must equal `diagnostic_hello_until`, and the detector specifies `checkpoint_path`, `checkpoint_sha256`, `threshold` and `max_frames_per_camera` (2–120). This permit cannot coexist with the single-camera detector or smart-event permits. A fair scheduler retains at most one pending frame per camera, shares one model, limits calls per camera and disables only the camera whose inference or result handling fails. When a model fails or a camera exhausts its frame allowance, the candidate sends a per-camera status update with smart readiness false. `CameraPolicyEngine` keeps each camera's parsed person policy, zone filter, reverification threshold and temporal tracks separate. When the controller supplies a valid person policy for an active allowlisted stream, synthetic tests show the candidate can emit separate `EventSmartDetect` envelopes; replacing one policy closes only its own candidate event. These tests do not prove that Protect persists events on the original camera timelines. Native multi-camera pairing, model endurance, delivery acknowledgement, policy timing and Protect timeline acceptance remain open in [#79](https://github.com/Olli0103/unifi-ai-key-emulator/issues/79). Keep this diagnostic off for normal operation.

During an expiring diagnostic, `/healthz` also reports counts for fixed, allowlisted WebSocket `functionName` values. The allowlist includes the observed handshake and stream commands plus selected controller/firmware-known status, time-sync and detection names. Other names and unparseable binary frames have counts only; their names and payloads are not returned. `stream_reconnects_preserved` and `stream_grace_closures` show whether the bounded reconnect path ran without exposing a stream alias or frame. A bounded Protect 7.3.60 trial preserved one stream across a reconnect. Protect then sent `ResetAIPortStreams`; the first deployed build counted but did not answer it. The current implementation closes the bounded decoder before acknowledging a well-formed reset. A second native trial observed that response, but Protect did not request a new stream on the following reconnects. The reason for this and the remaining unclassified frames is `needs_evidence`; a synthetic controller test alone does not prove durable native streaming.

For a single-camera diagnostic while its event permit is active, an accepted stream stop or `ResetAIPortStreams` now sends a leave edge for any bounded active event before revoking its policy and tracker. A replacement or disabled smart policy uses the same closure path. Synthetic tests cover the event order and state reset; Protect's handling of a leave during a native stop or reset remains `needs_evidence`.

The candidate also counts WebSocket close codes, retaining at most 16 distinct numeric codes. It never records close reasons, which may contain private device data. `last_disconnect_origin` distinguishes expiration of our own diagnostic timer from a peer or transport close; the latter cannot identify the exact initiator by itself.

An optional private `diagnostic_function_fingerprints_until` timestamp, limited to ten minutes, adds at most eight short SHA-256 function-name fingerprints to `/healthz`. It never records command payloads or raw unknown names, remains off by default, and disappears from health output at expiry. A no-camera trial matched one fingerprint against the inspected controller package: Protect sent `UpdateFaceDBRequest` to the connected AI Port. The candidate answers that command with an explicit unsupported status. It does not fetch or retain the face-database URL and does not advertise face recognition as ready.

During an enabled stream diagnostic, a successful stream-control response sends a per-camera `EventAIPortStatus` with the observed streaming state. Smart-detection and audio-event readiness are false by default. A separate expiring, one-camera probe temporarily sends `EventFeatureFlagsUpdated` with one selected object capability and reports smart readiness. The candidate classifies a narrow `ChangeSmartDetectSettings` subset for the exact camera: full-frame person, vehicle and animal detection, or primary-lens polygons for those classes, with bounded event timing. Protect 7.3.60 sent `enableSmartDetect: []` alongside a Person detection zone for a paired legacy G3 camera; the candidate now derives a single class only from validated primary-lens zones in that case. An empty list with no usable zone remains disabled. Secondary-lens zones, lines, exclusions, tamper, access triggers and PTZ remain unsupported. The settings-only probe counts matching requests and still replies unsupported; it never retains the nested policy.

An additional private `diagnostic_event_until` field can enable one single-class event trial. It must equal both the stream and smart-probe deadline, which can be at most ten minutes ahead. A model-backed trial also requires a local detector policy with 2-120 frame attempts. The default class is person; `diagnostic_smart_type` may explicitly select vehicle or animal for a separate bounded model test. Only a matching single-class setting for the selected camera receives an acknowledgement. If reverification is enabled with a valid integer probability range, the candidate drops observations at or below its upper bound before temporal tracking; it does not perform the requested second-stage verification. For a supported primary-lens zone, the candidate requires the whole object box strictly inside a simple polygon, then includes that zone ID in the native event candidate. Configured zones with unsupported geometry or access triggers are rejected. This conservative geometry rule can miss objects on a zone boundary and does not calibrate camera zone sensitivity. A later disabled or unsupported setting revokes the policy. After two overlapping qualifying model observations, the in-memory tracker may send one `EventSmartDetect` enter and a leave when the track disappears. The payload includes a wall-clock timestamp and normalized box, but no image, face, plate, speech or camera credential. Protect 7.3.60 accepted the bounded zone-only Person settings acknowledgement for a paired legacy G3 camera, and the detector processed 120 frames without a person observation. Event persistence on the original camera timeline, vehicle and animal live acceptance, full reverification and alarms remain `needs_evidence`. The default passive profile emits no smart events.

For a protocol-only original-timeline test when no person enters the camera view, `diagnostic_native_event_probe` can replace `diagnostic_detector`. It is off unless a private config names one exact stream camera, a new 32-character lowercase hex nonce, and a normalized `[x1, y1, x2, y2]` box. `diagnostic_hello_until`, `diagnostic_smart_probe_until`, and `diagnostic_event_until` must have the same expiry, at most ten minutes ahead. The controller must pair that camera, start its stream, and send an enabled Person policy. The configured box must pass that policy's zone and confidence checks. The candidate claims the nonce in a mode-600 state marker **before** sending one synthetic Person enter and a leave two seconds later. A restart with the same private state cannot replay it. Health exposes only claim and error counters. This is a deliberate false test event, not a model result; it can appear in the camera timeline and trigger configured alarms. Use it only during an approved test after checking alarm actions, then remove the diagnostic fields and unpair the camera. It has not been run against Protect yet, so native persistence is still `needs_evidence`.

For a test based on an existing recording, `diagnostic_recorded_event_probe` is a separate one-use alternative to both the synthetic probe and live `diagnostic_detector`. It requires the same exact-camera stream and matching ten-minute hello, smart-settings and event deadlines, plus a person-only Protect policy. Build with `ENABLE_LOCAL_DETECTOR=1`, as for the live model probe. Put exactly two exported JPEG frames, captured no more than three seconds apart, in a private `recorded-probe` subdirectory of the candidate state (directory mode 700, files mode 600). The second frame must be at least one minute and at most 24 hours old when the candidate loads its config. Mount the same local detector checkpoint read-only, and add a private block like this:

```json
"diagnostic_recorded_event_probe": {
  "camera_mac": "CAMERA_MAC_WITHOUT_COLONS",
  "nonce": "NEW_32_CHARACTER_LOWERCASE_HEX_NONCE",
  "frames": [
    {"path": "/state/recorded-probe/first.jpg", "sha256": "FIRST_JPEG_SHA256", "captured_ms": 1700000000000},
    {"path": "/state/recorded-probe/second.jpg", "sha256": "SECOND_JPEG_SHA256", "captured_ms": 1700000002000}
  ],
  "checkpoint_path": "/models/rf-detr-nano.pth",
  "checkpoint_sha256": "CHECKPOINT_SHA256",
  "threshold": 0.5
}
```

Replace every placeholder with current private values; the shown timestamps are examples and will be rejected by the 24-hour check. The candidate checks both JPEG hashes again at use, runs the pinned local model, requires two overlapping person observations, and applies Protect's current confidence and zone policy. Only then does it durably claim the nonce and send one native enter/leave pair with the recording's original timestamp. Images stay local; the native payload contains a class, confidence and normalized box. This does **not** cryptographically prove the frames or capture times came from Protect, and an old event may be rejected or create a duplicate event or notification. Test with an existing recording only when that duplicate is acceptable. The feature is off by default. A single Flur G3 trial on Protect 7.3.60 sent one model-qualified enter/leave pair and showed a Smart card at the old recording time, including after a full page reload while paired. The read-only subscription saw one targeted smart-video add and one update. Protect's exact class labeling, alarm behavior, long-term persistence, live operation and other cameras remain `needs_evidence`. The camera's AI Event recording was then disabled again, Flur was unpaired, and the candidate restarted with passive settings.

That first trial sent the two edges back-to-back even though their `clockWall` values were two seconds apart. The recorded probe now waits two real seconds between enter and leave, and checks that the same stream, policy and expiring permit still apply before sending leave. This pacing passed a timing test. In a second bounded trial, Flur was paired and **Record AI Events** was enabled; the candidate again reported one model-qualified historical Person enter/leave pair with no probe error. Protect's Flur **Person** filter still showed **0 Results while paired**. Pacing therefore did not establish class indexing. After the trial, Flur's AI Event recording was disabled, the camera was unpaired, and the candidate restarted with no diagnostic policy. The original cause remains `needs_evidence`.

For a hello-only provisioning probe, the candidate answers empty `ChangeVideoSettings` and `ChangeIspSettings` queries with a minimal no-camera profile. Protect 7.3.60 requested both in that order during a no-camera trial. `unlisted_envelope_counts` divides other functions into request, response and other shapes without saving their names or payloads. The fixed allowlist also counts `StartService`, `StopService`, `UpdateUsernamePassword` and `ChangeSoundLedSettings`, which Protect 7.2.105 may send after provisioning. Because this container has no SSH server, it acknowledges a request to stop SSH and rejects a request to start it. An exact `UpdateUsernamePassword` payload received during an expiring diagnostic is acknowledged only after its SHA-512 crypt hash is atomically stored in a mode-600 private file. The candidate never stores the plain password. HTTPS `/api/1.2/login` checks the new password, and `/api/1.2/manage` rejects incorrect credentials. Sessions are short-lived, rate-limited and invalidated on rotation. The container includes OpenSSL for verification of the controller's hash format. In a no-camera Protect 7.3.60 trial, the candidate persisted one credential rotation and replied successfully; Protect then sent `ChangeSoundLedSettings`. Native login and subsequent provisioning remain `needs_evidence`. The diagnostic ended with the candidate passive and no camera paired. These replies do not prove adoption or durable operation.

The candidate now accepts the exact AI Port `ChangeSoundLedSettings` and timezone-only `ChangeDeviceSettings` payloads during that same expiring diagnostic. It validates and persists them as private logical state before replying. This container has no physical LED or speaker; these replies do not claim a light or sound occurred. Protect 7.3.60 accepted both replies in no-camera trials. A subsequent five-minute, single-Flur trial paired the camera, decoded 129 frames without a reported stream error, and received an explicit stop when Flur was unpaired. No control reconnect happened during that brief interval. The camera returned to Online HD/Adaptive, the separate AI Key remained Online, and the candidate restarted passively with zero frames. This demonstrates bounded initial streaming after the settings sequence, not permanent operation, adoption, smart detection, or AI Port feature parity.
