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

## Fresh native speech results after the fix (read back 27 Sep 2026)

Read-only, from Protect's API (the private API in a signed-in console session). Only counts, timestamps and field names were read; no transcript text.

- **Protect's stored state:**
  - flags: `supportTts` true, `supportFaceRecognition` true, `supportRecognizeAnything` true, `supportImageSearch` true, `supportRetroactiveProcessing` true, `supportLicensePlateRecognition` false;
  - `speechToTextSettings` enabled for 1 camera;
  - `recentProcessedSTTTasks` is 226.
- **Events since 20 Sep:** 226 audio events with speech state `done`, all on Wohnzimmer (the configured speech camera), and 124 `failed`.
- **Persisted transcripts after the fix:** the newest 60 done events all ended after the 16:52 local status change. On a fresh API read, **40 of them return stored transcript segments** (up to 31 per event, fields `start`, `end`, `text`, `id`). The newest with segments ended 26 Sep 21:42 UTC.
- **Backlog:** this closes the backlogged "fresh indoor transcript after the flag change" item.
- **Still open:**
  - a fresh AI Key face result after the fix;
  - re-reading the Camera Coverage tooltip in the UI;
  - names and plate accuracy (Olli).
- **Reverification:** Protect's `reverificationSettings` is enabled for all cameras. The Key's `find_anything.reverification` opt-in stays off, so it refuses reverification tasks and events keep their type. That is the N13 boundary, and nothing was changed.

## Face detection, grouping and the tooltip's zero (#20, 27 Sep 2026)

Read-only from Protect 7.3.68. Only counts were read; group IDs were compared inside the page and not printed.

**The tooltip's 0 is eligibility, not behavior.**
- The face line counts *legacy* cameras that can use the Key's face recognition (`isFaceDetectionSupportedViaAiprocessor`: unpaired G4/G5/Doorbell Lite).
- The Wohnzimmer G6 is excluded because it detects faces itself: `face` is both a hardware smart type and enabled in its inventory.
- The Key's face setting still covers it, so Protect sent the Key face tasks for it.

**What happened on Wohnzimmer (smart events since the 26 Sep 16:52 local fix).**
- **Events:** 93 smart events.
- **Camera faces:** 93, clustered by Protect into 42 groups.
- **AI Key faces:** 65, 0 named. They landed in **65 distinct groups**:
  - none of those groups holds another face;
  - in the 38 events where the camera also saw a face, the AI Key face's group differs from the camera face's.
- **Why:** Protect's face daemon clusters on `attributes.faceEmbed`; its own face flows use a 512-value camera-model embedding. AI Key faces carry none, so each becomes its own group. A local SFace (128-value) embedding would be a different model space, so it is not sent.

**Fix.** The Key now answers face tasks for cameras with native, enabled face detection with **no AI Key faces and no video fetch**.
- **Scope:** the face flag, the setting and task completion are unchanged.
- **Unknown capability:** a missing inventory keeps processing on.
- **Override:** `face_recognition.native_face_cameras: process` restores it.
- **Tests:** positive and negative tests in `tests/test_local_faces.py`.
- **Deploy:** `local-aikey-mac-findanything15`, 27 Sep 00:20:56 UTC.
  - The Key is adopted and connected, and search is connected.
  - Protect still stores `supportFaceRecognition` true, with the face setting on 1 camera.
  - The Key recognizes 1 native-face camera.

**needs_evidence.**
- **Fresh readback:** new Wohnzimmer events with camera faces and no new AI Key faces, plus the Key's `skipped_native_face_camera` counter. Wohnzimmer has had no smart events since 23:05 UTC, and the household is away.
- **Existing groups:** the 65 singleton groups made earlier stay in Protect. Removing them is a face-gallery action for the owner.
- **Named recognition:** it still requires an owner-named group on a camera the Key may serve. No such legacy camera is eligible today.

## License plates: AI Key Off vs AI Port results (#19, 27 Sep 2026)

Read-only from Protect 7.3.68. Counts only; no plate text or images.

- **AI Key LPR is Off by eligibility.**
  - `supportLicensePlateRecognition` is false and `licensePlateRecognitionSettings.enabled` is false.
  - `isLprDetectionSupportedViaAiprocessor` admits only unpaired G4/G5/Doorbell Lite cameras. Every connected G4/G5 is paired to an AI Port; the only unpaired G4 ("Wohnzimmer alt") is offline.
  - The one camera with plate hardware, the Wohnzimmer G6, is unpaired, indoors and not an AI Key candidate.
- **AI Port plates.**
  - In 7 days there is 1 native plate event: Einfahrt, 26 Sep 13:58 UTC.
  - It carries `licensePlate` in the event's smart types and a plate on the vehicle thumbnail, and no separate `licensePlate` thumbnail.
  - Since then Einfahrt had 8 vehicle events and Garage 13, all between 16:00 and 22:00 local, most after dark, and **0 plate events**.
- **Gap fixed: config drift.** The canonical slot 3 and 4 configs lacked the `plate_cameras` that the deployed r25 configs carry. Any allowlist or provider edit uploaded from them would have silently turned plate reading off.
  - The planner now reports `slot_config_drift` and blocks automated edits on a drifted slot.
  - The canonical copies were re-synced from the deployed ones (backups `config.bak-before-plate-sync-20260927.json`).
  - The live dry run went from a no-op, to a drift report with `deployed_config` set, back to a no-op with the original plan revision.
  - Nothing was uploaded to the NAS, and no pairing changed.
- **needs_evidence.**
  - **Transport:** a fresh native plate event on Einfahrt or Garage, on the original camera timeline (a daytime vehicle with a legible plate).
  - **Plate accuracy:** needs Olli's ground truth.

## Transcript search on Protect 7.3.68 (#15, 27 Sep 2026)

Read-only, in a signed-in console session. Only counts, event-ID match status and HTTP behavior were read; no transcript text was read out, and no audio was fetched.

**Contract.**
- **Server (7.3.60 code):** when a transcript is saved, Protect sets `metadata.sttSearchable` if it has real text, and adds the event label `smartDetectType:transcript`. No query reads `transcriptions.text` (no ILIKE, full-text or vector search).
- **Web UI (7.3.68 code, loaded on the Find Anything page):** it has a per-event transcript viewer (`getTranscriptions` → `GET events/{id}/transcriptions?camera=`, `TranscriptText`) and `hasTranscript` (the event labels include `transcript`). No route searches transcript text.

**Native test.**
- **Target:** a stored Wohnzimmer `smartAudioDetect` (`alrmSpeak`) event with `sttSearchable: true`. A common 6-letter word from its stored transcript was picked in the page from a fixed stopword list and never printed. The control was a nonsense token.
- **`detection-nls`:** 200 with 50 image objects for both term and control. The target event was **not** returned. It searches `ramDetections` object vectors only.
- **`GET events` (Wohnzimmer, target window ±1 min):** 3 events for every variant, identical to the window alone:
  - `searchText` with the term, and with the control;
  - `keyword` with the control;
  - `labels` set to `transcript`, and to a bogus label;
  - `smartDetectTypes=vehicle`.
  - These parameters are ignored. The term "matches" only through the time window.

**Result.**
- **Not supported:** basic mode on 7.3.68 offers no transcript text search, only a "has transcript" label and the transcript viewer.
- **No safe emulator-side fix:** the only lever would put transcript-derived vectors into the image-object index. That mixes modalities, embeds speech content, and would rely on deep-understanding session search, which is disabled and is not changed here.
- **Status:** `needs_evidence`, controller-limited.
