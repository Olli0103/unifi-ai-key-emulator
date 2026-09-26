# AI Key capability status in Protect (issues #15, #19, #20, #21)

On 26 Sep 2026, Protect 7.3.68 showed Speech to Text, License Plate Recognition and Face Recognition as **Off** for our AI Key. Native transcript, face and plate results already existed at that point. This record explains why and what changed (bda0f0e).

## Where Protect shows it

The status appears in the AI Key panel (Devices → AI Key), in the **Camera Coverage** tooltip. The 7.3.68 web UI maps its lines to AI Key feature flags:

| Tooltip line | Feature type | Flag |
|---|---|---|
| Computer Vision Enhancement | (policies) | — |
| Speech to Text | `SPEECH_TO_TEXT` | `supportTts` |
| License Plate Recognition | `LPR_LEGACY` | `supportLicensePlateRecognition` |
| Face Recognition | `FACE_RECOGNITION_LEGACY` | `supportFaceRecognition` |

A feature whose flag is not enabled is shown as **Off**. Otherwise the line shows the number of cameras its policy covers. The console-side table (`FEATURE_TYPE_CONFIG`, 7.3.60 `service.js`) pairs the same flags with `speechToTextSettings`, `recognizeAnythingSettings` (`supportRecognizeAnything`), and so on.

## Why it was Off

- **Speech:** the Key's `getInfo` never sent `supportTts`, so Protect stored `null`.
- **Face and plates:** the Key sent `supportFaceRecognition` and `supportLicensePlateRecognition` (and `supportRecognizeAnything`) as explicit `{enabled: false}`. Protect's `getInfo` handler **defaults only absent flags** to enabled for a UP-AI-KEY, so an explicit false stays false.
- **Behaviour, not just display:**
  - `findPreferenceAiProcessor` refuses the face and plate settings when their flag is off.
  - `getAiprocessorOnlyDetectionTypes` adds `face` or `licensePlate` to a camera only when the flag, the setting and the camera selection all agree.

## Fix

The Key now derives these flags from what it is configured to serve. An explicit `feature_flags.<name>.enabled: false` still wins.

| Flag | Enabled when |
|---|---|
| `supportTts` | a speech backend with cameras is configured |
| `supportFaceRecognition` | local face recognition (server and cameras) is configured |
| `supportRecognizeAnything` | Find Anything indexing is configured (captions remain `supportAiSummary`) |
| `supportImageSearch` | the CLIP search profile is enabled |
| `supportLicensePlateRecognition` | never, for now: the Key has no plate reader |

## Readback after the reconnect (16:52)

- **Protect `aiprocessors` flags:** `supportTts` true, `supportFaceRecognition` true, `supportRecognizeAnything` true, `supportImageSearch` true, `supportLicensePlateRecognition` false.
- **Settings and pairings:** unchanged (speech: Wohnzimmer; face: Wohnzimmer; plates: disabled).
- **Camera Coverage tooltip:** Computer Vision Enhancement **10**, Speech to Text **1** (was Off), License Plate Recognition **Off**, Face Recognition **0** (was Off).

**Why face shows 0.** The face line counts *legacy* cameras under Legacy Camera Enhancement, which admits only unpaired G4/G5 and Doorbell Lite models (`isFaceDetectionSupportedViaAiprocessor`). The only such camera here is "Wohnzimmer alt": offline, and not selected. The Wohnzimmer G6 is not counted because it reports `face` itself. The Key's face results on its person regions are still saved as native face thumbnails (#20).

**Plate recognition through the AI Key is blocked by eligibility, not code.**
- `isLprDetectionSupportedViaAiprocessor` requires an unpaired G4/G5 or Doorbell Lite. Every connected G4/G5 is paired to an AI Port, and the G6 is not on the list.
- Paired cameras get plates from the AI Port instead: the native `licensePlate` on an Einfahrt track, #19.
- Unblocking would need Olli to unpair a G4/G5 camera, or to reconnect "Wohnzimmer alt" and select it. Only then would an AI Key plate reader be worth enabling and verifying.

## Fresh native results after the fix (backlog)

- **Object indexing:** fresh after the fix. Three Garage vehicle search snapshots at 17:16:36 are searchable in Protect, and "a car" ranks them above "person" and "an animal".
- **Speech and faces:** Protect sent no Wohnzimmer speech or person tasks between 16:52 and 17:55, and Olli reports the household is away. A **fresh** indoor transcript and a fresh AI Key face after the flag change are `needs_evidence` (backlog), as is re-reading the Camera Coverage tooltip after such events. Earlier native transcripts (#15) and AI Key faces (#20) stand.
- **Names and plate accuracy:** stay `needs_evidence` until Olli validates them.
