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
