# Live AI Key evidence on Protect 7.3.70 (29–30 Sep 2026)

The Protect controller on the UDM Pro Max auto-updated from 7.3.68 to 7.3.70 at 03:59 local time on 29 Sep. The adopted software AI Key (UP-AI-KEY profile) ran first as an Apple container on the Mac. From 29 Sep 18:35 UTC it ran as a Docker container on the NAS (amd64). Throughout, it kept its identity, its address 192.168.0.98 and its adoption.

The values below are content-free device health counters, worker journal states and integration-API readbacks. No captions, transcripts, frames, faces or identifiers are published.

## Observed

- **Adoption across the move.** The processor moved hosts on the same state files and was connected and adopted within a minute. No re-adoption was needed.
- **Credential rotation.** On the first connect after the move, `changeUserPassword` answered 13 three times. The cause was that the restored search database still had its initial role password. After the role was set from the device's stored password, the next connect's `changeUserPassword` answered 0.
- **Controller version.** Protect reported 7.3.70 in `setConsoleInfo`, and `getInfo`, `getTaskQueueInfo`, `setConsoleInfo` and `updateTimezone` answered 0.
- **Camera inventory.** The local integration API on 7.3.70 returned the same row and feature-flag fields as 7.3.68. All 11 rows parsed under the existing contract: 10 smart-event candidates and 1 offline camera, with no unknown smart types. Once 7.3.70 was listed, the registry went fresh with 10 eligible cameras.
- **Continuous captions (worker journal only).**
  - Before 7.3.70 was listed, the stale registry refused every `postVLM` task as `video_contract`.
  - Afterwards, 10 of the 12 video tasks in the first minutes were admitted. Nine `recognizeKeyFrames` caption jobs reached `completed`, which means Protect answered each callback 2xx. Whether Protect saved the captions was not read back.
  - One job failed after Protect answered the media export with 503 on every retry.
- **Speech to text (worker journal only).** 32 `speechToText` jobs reached `completed` in 30 minutes across the AI Port cameras: each fetched the audio-only export and Protect answered the segment callback 2xx. No transcription rows were read back.
- **Faces (worker journal only).** `recognizeFaces` jobs reached `completed`. On the camera with native face detection they were answered with no AI Key face.
- **Search index (row counts only).** The Key's PostgreSQL gained rows, including 2 for the native face camera after the face-task search tags change (30 Sep). Counts do not attribute rows to that change.
- **Player AI summary.** All 4 on-demand jobs at 06:56–06:57 local on 30 Sep timed out behind a speech backlog (fixed in 7807494). No successful summary was observed.
- **Unhandled commands.**
  - During a bounded fingerprint window, the two unhandled command names Protect sends on connect were identified as `diskInfo` and `updateLcmSettings`. The same names appear in Protect's own AI processor log as "Failed to sync storage size to AI Key" and "Failed to updateLcmSettings for AI Key".
  - `RequestAI` for the `second_verifier` target answered 95 while reverification was off.

## Not observed

- A caption read back from Protect's exact-event record (`metadata.ramDescription`) on 7.3.70. That readback needs an owner browser session.
- Transcription rows or face thumbnails read back per event on 7.3.70.
- A successful player AI summary on 7.3.70.
- The expected reply of `diskInfo` (it arrives with an empty body). `updateLcmSettings` carries `brightness` and `nightMode.{onMinute, offMinute}`, field names only.
- Deep mode or `/describe` tasks on 7.3.70.
- A reverified event.

## Limits

These are journal states, callback status codes and counters from one controller over about one day. A 2xx callback, a completed job or a growing row count is not a native saved caption, transcript, face or summary.
