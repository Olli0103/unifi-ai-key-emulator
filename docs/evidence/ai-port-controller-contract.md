# AI Port controller contract: initial evidence

This is an initial interface inventory for issue [#6](https://github.com/Olli0103/unifi-ai-key-emulator/issues/6), not a full native compatibility claim. The table records a local, read-only analysis of the Protect **7.2.105** controller package. Later live results on Protect **7.3.60** are recorded in the [firmware and live-contract notes](ai-port-firmware-contract.md). The initial table is retained as versioned evidence; its open statuses have not all been updated by those later results. No vendor source or firmware is included in this repository.

## Evidence and confidence

| Finding | Evidence | Status |
| --- | --- | --- |
| AI Port is a separate controller device type, `aiport`, with product name `UVC AI Port` and sysid `0xa5f1`. The existing AI Key profile uses `0xa5f0`. | Protect 7.2.105 `service.js`, device model and AI Port model modules | Observed in static controller code; native 7.3.60 acceptance `needs_evidence` |
| Controller adoption sends a credentialed `POST` to the device's `manage` path. Its `mgmt` object contains a token, controller hosts and `wss` protocol; the generic device-adoption path can also include mode and console metadata. The next camera WebSocket uses `?token=` and `Adopted: true`. | Protect 7.2.105 `service.js`, generic device adoption, AI Port adoption and WebSocket verification modules | Observed statically and covered by synthetic candidate tests; actual Protect 7.3.60 adoption `needs_evidence` |
| The controller exposes `/api/aiports`, `/api/aiports/:id`, and actions for `adopt`, `readopt`, `pair`, `unpair`, `locate`, `update` and `reboot`. | Protect 7.2.105 bundled OpenAPI fixture | Observed statically; 7.3.60 route behavior `needs_evidence` |
| Pair and unpair accept a JSON `cameraIds` array and return per-camera status. Pairing checks adoption, existing pairing, capacity and resolution. It may change a camera's downscale mode and restart its stream. | Protect 7.2.105 OpenAPI fixture and pairing module | Observed statically; camera impact must be tested before routine use |
| The controller tracks streaming, smart-service and audio-service readiness per paired camera. It only applies smart settings after streaming is ready. | Protect 7.2.105 AI Port status modules | Observed statically; wire messages and event ingress `needs_evidence` |
| G3 and ONVIF cameras need AI Port smart detections before AI Key processes them. | [Ubiquiti AI Key FAQ](https://help.ui.com/hc/en-us/articles/29221435686039-UniFi-AI-Key-Setup-and-FAQs) | Vendor-documented behavior |

The analyzed files were `service.js` (SHA-256 `a7370e9a1b35db67d56104268841b9c2ba16342befb750ffef6654ee2b07bfdb`) and its `fixtures/api/openapi.json` (SHA-256 `f3912fe08a9503c53a6f3e5e9a6f94d25133bae06183ae6f645a0ed20319fcee`). These hashes identify the inspected package, not a runtime attestation of the current console.

On 24 September 2026, a pinned, read-only request to Protect 7.3.60 returned **401** for `GET /proxy/protect/api/aiports` using this project's existing Integration API key. `GET /proxy/protect/integration/v1/aiports` returned **404**. This proves neither that the internal pairing route is absent nor that another credential could use it. It does show that the current camera-inventory key cannot drive unattended pairing, and the public integration path tested here does not expose AI Ports. An authenticated, supported pairing path remains `needs_evidence`; the deployment planner must not silently claim to pair cameras.

## Implementation boundary

The AI Port profile needs its **own MAC, certificate, management credential, state directory, network address and adoption record**. It cannot be created by changing the model or sysid of an adopted AI Key. The current AI Key configuration validator now rejects that shortcut. Existing AI Key deployment state stays untouched.

Native proof requires the following sequence:

1. Confirm the AI Port device identity, discovery response and `manage` request against Protect 7.3.60 using a synthetic, isolated profile. Record only sanitized request shapes and result codes.
2. Prove adoption and reconnect with a separate identity while the existing AI Key remains online. If Protect does not accept it, record the first failed check and stop before camera pairing.
3. Pair one approved G3 test camera only after recording its current resolution, recording mode and settings for rollback. Confirm the controller's stream and smart-service readiness states.
4. Produce one bounded synthetic detection and verify its **original camera timeline**, recording, Alarm Manager trigger and AI Key handoff. A successful device command or an independent dashboard is insufficient.
5. Unpair and restore the camera, then test reconnect and a second event before expanding the detector or adding ONVIF support.

The device-side streaming, event-ingress, timebase, PTZ, edge-recording and result schemas are still `needs_evidence`. Until those are proved, the implementation issue [#28](https://github.com/Olli0103/unifi-ai-key-emulator/issues/28) remains blocked by #6. The [AI Port FAQ](https://help.ui.com/hc/en-us/articles/28315005177239-Protect-AI-Port-FAQs) describes the supported camera classes, resolution limits and stream behavior, but it does not publish the device protocol.

## Stream, PTZ and recording contracts on Protect 7.3.68 (26 Sep 2026, #6)

**Stream (evidenced).**
- **Command:** for each paired camera, Protect sends the AI Port `UiStreamControl` with `{streaming: true, ip, port: 7447, uri, deviceID, width, height, fps}`. That is a stream alias on Protect's own RTSP relay at the controller; the AI Port never connects to the camera directly.
- **Health:** since this change the AI Port reports the negotiated `stream_geometry` (width, height, fps) per camera. It never reports the alias, an address or a camera identity.
- **Mac AI Port readback, after a gated restart at 19:40:**

  | Camera | Model | Requested | Main-lens maximum |
  |---|---|---|---|
  | Flur | G3 Instant | 1920×1080 @ 30 fps | 1920×1080 |
  | Schlafzimmer | G3 Instant | 1920×1080 @ 30 fps | 1920×1080 |
  | Büro | G5 Flex | 2688×1512 @ 30 fps | 2688×1512 |

  Protect pairs the **main (HQ) channel** at its full resolution, not a lower channel.
- **NAS AI Ports:** their reserved capacity points match the main channel. That is 5 points (above 1440p) for Einfahrt (G4 Pro), both G4 Bullets, Esszimmer (G4 Instant) and Garage (G4 Dome), and 2 points for Haustür's 1600×1200 main lens. Their exact geometry appears after their next redeploy.
- **Recovery:** after the AI Port container restarted on the same identity, Protect re-sent the stream start and the smart policy for all three paired cameras within 30 s (`stream_controls_started: 3`, 3 streams decoding). No re-pairing was needed.

**Not evidenced (`needs_evidence`).**
- **Protect's own channel list** for the exact channel index of each stream (needs the private camera API in a console session).
- **PTZ:** no PTZ camera is paired, and the AI Port implements no PTZ commands.
- **Edge recording:** the AI Port sends no recording controls. That recordings stay unchanged on paired cameras has not been read back natively on 7.3.68.

## Recording continuity around the 19:40 Mac AI Port restart (read back 26 Sep 2026, 21:15 UTC)

Read-only, from the signed-in console. No video was exported or transmitted. The probe is Protect's `GET /video/export/estimate` (7.3.60 route): it sizes the recording files in a range, and fails with "No files found" (HTTP 500) when none overlap.

**The restart.** The Mac AI Port container restarted at 17:40:00 UTC and relays Flur, Schlafzimmer and Büro.

**Five-minute windows, 17:25–17:55 UTC.** All three cameras answer 200 for every window, including the one spanning 17:38–17:43.

**30-second windows, 17:39:00–17:42:00 UTC.**

| Camera | Size estimate per window |
|---|---|
| Flur | 1060, 1060, 4231, 7623, 6355, 3857 kB (motion from 17:40) |
| Büro | 571 kB in every window |
| Schlafzimmer | 1154 kB in every window |

- **Reading the sizes:** the constant values for Büro and Schlafzimmer show the estimate reflects file segments, not duration. So this proves **file coverage of every 30-second window**, not bitrate.
- **Events:** Flur's 5 motion events between 17:30 and 17:50 stay on Flur, one spanning 17:40. Büro and Schlafzimmer had no events. There were no smart events on the three cameras (the household is away).
- **Pairing and recording settings:** all nine AI Port cameras remain `isPairedWithAiPort: true`, `recordingSettings.mode: adaptive`, `CONNECTED`. The unpaired cameras are Wohnzimmer (G6) and the offline "Wohnzimmer alt".

**Result.** Native recordings of the Mac-paired cameras covered the restart, with no gap of 30 s or more, and events kept their original cameras.

**Still open:**
- gaps shorter than 30 s cannot be excluded;
- an export byte-length check was not done, because camera media must not be exported.

## Smart zones on the live cameras (#74, 27 Sep 2026)

Read-only. Protect camera settings are summarized as zone count, classes and covered area only. The AI Port counters are sanitized.

**Protect zones (7.3.68).**
- **Smart zones:** every AI Port camera has one smart zone covering **100%** of the frame, except **Flur at 88%**. Flur's is an axis-aligned rectangle inset **2.4–4.0%** on each side: Protect's default rectangle.
- **None anywhere:** no `excludeZones`, lines or loiter zones.
- **Privacy masks:** Einfahrt (13.5% and 7.8%), Giebel Vorn (22.2%), Haustür (1.6%) and Giebel hinten (1.6%) have privacy masks. The camera applies these in its stream; they are not part of the smart-detection policy.
- **Second-lens zones:** Haustür and Garage carry full-area second-lens zones, accepted by the policy parser.

**Effective admission.** Zone vertices within 5% of the frame edge snap to it before the 90% overlap test (the Flur edge correction). So Flur's zone, like all the others, admits the **whole frame**.

**AI Port counters since each slot's last restart.**
- All 9 cameras have enabled 4-class policies with no policy rejection.
- Zone rejections are **0** on every camera (`outside_zone`, `excluded`, `below_overlap`, `no_class_zone`), while 143 native events were entered and left.
- API item rejections: `box` 1 at Haustür and 1 at Garage; `kind` 115 and `shape` 1 at Garage; `label:vehicle` 2 at Haustür. Out-of-range boxes are too rare to justify clamping. Garage's unsupported kinds belong to detection vocabulary (#79).

**Tests.** The edge snap is pinned at its boundary (`test_the_frame_edge_snap_stops_at_five_percent`): an inset of exactly 5% snaps, while 5.1% and 6% keep rejecting edge objects.

**needs_evidence.** The live outside-zone control needs a deliberate inset over 5%, or an exclusion zone, on one camera; so does zone change and disable revocation. Both are owner changes to camera settings. No natural outside-zone candidate can occur with the current zones. The 90% Person overlap rule stays provisional.
