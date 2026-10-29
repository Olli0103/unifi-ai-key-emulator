# Protect 7.3.74 — bounded observations, 29 October 2026

This is partial evidence for the AI Key contract (#2), not full compatibility
or a new caption/search-quality acceptance. Dates and versions are not inferred
from older 7.3.60/7.3.68/7.3.70 trials.

## Observed

- The native controller reports Protect 7.3.74. The authenticated, independently
  pinned integration inventory returns 11 cameras, 10 connected, across eight
  UniFi model families. The complete private ID-set digest matches a separately
  authenticated native bootstrap; no IDs or keys are published. See the
  [inventory contract](camera-inventory-7.3.74.md).
- A one-module inventory overlay replaced the prior Key image while preserving
  configuration, certificate, private key and adopted identity. On read-back,
  the same Key is adopted and control/search connected, with fresh inventory.
  Four existing AI Ports remain adopted/connected and were not redeployed.
- The installed, older manifest classifies the current controller as `unknown`
  while adoption and baseline control continue. The updated source records it
  as `live_partial`; that classification must not authorize untested features.
- A native Find Anything request for `person in a room` uses `detection-nls`
  and returns HTTP 200 with 60 detections. The search profile reports
  `clip-basic-v1`. This is a positive native request/response, not a labeled
  relevance/negative-query benchmark or proof that every search mode works.
- Current database inspection observes pgvector 0.8.6 and separate vector
  widths 768 (image), 1,024 (detection ReID), 512 (session-object ReID) and
  384 (session description). Width equality alone never establishes a shared
  model space. The [isolated restore](nas-restore-20261029.md) has separate
  limits, including an empty tokenizer catalog after logical restore.

## Not observed / not authorized by this evidence

- Fresh adoption, a new management-password rotation, or a controller upgrade
  from a captured before-state on 7.3.74.
- A new persisted 7.3.74 video caption, audio transcript, identity/enhancement
  workflow, image query, deep-session round trip or comparative quality result.
- Complete restored-device/controller bootstrap, host reboot, sustained pilot,
  or a native ONVIF test. The owner has no ONVIF camera and explicitly keeps
  that requirement open.

The continuous-caption version allowlist is unchanged. Recognizing this
version as partially observed does not enable caption admission or transfer
older feature acceptance to it. Unknown/unsupported commands still fail with
fixed categories without recreating the device identity. Raw media, captions,
transcripts, biometric templates, private IDs and credentials remain private.
