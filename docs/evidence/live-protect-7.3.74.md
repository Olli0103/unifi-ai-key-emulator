# Protect 7.3.74 — bounded observations, 29 October 2026

This is partial evidence for the AI Key contract (#2), including the specific
caption-persistence observations below, not full compatibility or caption/search
quality acceptance. Dates and versions are not inferred
from older 7.3.60/7.3.68/7.3.70 trials.

## Observed

These sections record successive bounded observations. The later
[source upgrade and native checks](nas-source-upgrade-20261029.md) replace the
inventory-only deployment while preserving its identity/configuration. Its
manifest now reports `live_partial`; earlier `unknown` observations below
remain historical, not the current running status.

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

## Additional session-search and control observations

The authenticated native session statistics return 13,399 sessions. An initial
search hit a client-side 12-second deadline. A bounded terminal read returned
30 sessions for `person in a room` in 796 ms and 30 for the contrast query
`a sailing boat on the ocean` in 2,927 ms. The result-ID sets differ, compared
inside the browser without exporting IDs or descriptions. This is ranked
search availability, not labeled relevance, a negative-query PASS or an
explanation of the initial timeout. The live search host has one tokenizer
registration; the isolated restore's empty catalog is not a live failure.

The current Deep policy was disabled, with the existing all-camera scope, and
the processor reported Basic. A bounded normal `ai-policies/deepUnderstanding`
PATCH was accepted with HTTP 200 and read back enabled, then restored. A
follow-up asynchronous observation exceeded the browser evaluation deadline;
intermediate controller/processor observations disagreed. Its restoration
also returned HTTP 200. Independent final reads subsequently confirmed the
policy disabled, the same all-camera/empty-explicit-list scope, and Basic in
both the controller's processor record and the Key's persisted state. No
internal-user/permission flags or provider/camera settings were changed.

Key counters still show zero new Deep description/embedding tasks and an empty
queue. Therefore this proves the settings write/read/restore path, not Deep
processing, description persistence, a restart round trip or quality. No new
processing-mode acceptance or version allowlist entry follows from it.

## Additional persisted captions, later on 29 October

Read-only native event samples find non-empty `metadata.ramDescription` with
`ramState: done` for G4 and G5 `smartAudioDetect` events. Exact GETs for the
same two event IDs, compared inside the authenticated browser after a helper
page reload, still return those fields. This is audio-event image-caption
persistence, not transcript or general audio-quality acceptance. The worker
journal's description-only summaries do not provide an exact ID join for those
two audio examples; that association is not claimed.

A separate filtered video-event sample finds G3 and G4 `smartDetectZone`
captions. Both exact events remain captioned on independent native GETs after
reload. Their event-ID digests match completed `recognizeKeyFrames` records
with non-empty descriptions in the private worker journal, within 30 minutes.
Only counts, generations, state/type and booleans leave the browser/NAS; no
event/camera IDs, descriptions or media are published. These observations
come from the unchanged r30 inventory-overlay deployment, not deployment of
the later source/admin changes. They do not prove caption quality, a fresh
post-caption restart, all cameras, overload behavior or new Deep processing.

## Not observed / not authorized by this evidence

- Fresh adoption, a new management-password rotation, or a controller upgrade
  from a captured before-state on 7.3.74.
- A new persisted 7.3.74 audio transcript, identity/enhancement workflow,
  deep-session round trip or comparative quality result. Later bounded native
  image-query transport and unresolved ranking/contrast results are recorded
  in the source-upgrade report, not promoted to general relevance acceptance.
- Complete restored-device/controller bootstrap, host reboot, sustained pilot,
  or a native ONVIF test. The owner has no ONVIF camera and explicitly keeps
  that requirement open.

The source continuous-caption version allowlist now includes exactly 7.3.74
on the strength of the paired worker/native video readbacks above. Existing
camera/model scope, fresh inventory, explicit continuous mode and budget gates
remain required; the controller-reported version must itself be listed. No
running service or configuration was changed by this source update. This does
not transfer unrelated older feature acceptance to 7.3.74, or admission to
neighboring 7.3.73/7.3.75 or other untested versions. Unknown/unsupported commands still fail with
fixed categories without recreating the device identity. Raw media, captions,
transcripts, biometric templates, private IDs and credentials remain private.
