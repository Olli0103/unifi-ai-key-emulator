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
- **Player AI summary.**
  - 06:56–06:57 local, 30 Sep: all 4 on-demand jobs timed out behind a speech backlog (fixed in 7807494).
  - **Owner-reported, not independently read back:** Olli's screenshot shows a summary rendered in the Büro player for an event at 09:09 local.
  - **Deployment, health readback only:** the Key then ran r6 with `worker.inference_concurrency` 1 (health `inference_gate.capacity` 1).
  - **Worker journal only:** 7 on-demand jobs timed out at 08:25–08:26 UTC, just after a Key restart with a 17-job backlog. Caption jobs waiting for the model gate held all six workers, so the summaries waited for a free worker despite their queue priority.
  - **Code only, synthetic tests:** a bounded caption lane (see *Limits and their scopes* in `docs/aiport-candidate.md`). It is not deployed.
- **Unhandled commands.**
  - During a bounded fingerprint window, the two unhandled command names Protect sends on connect were identified as `diskInfo` and `updateLcmSettings`. The same names appear in Protect's own AI processor log as "Failed to sync storage size to AI Key" and "Failed to updateLcmSettings for AI Key".
  - `RequestAI` for the `second_verifier` target answered 95 while reverification was off.

## Not observed

- A caption read back from Protect's exact-event record (`metadata.ramDescription`) on 7.3.70. That readback needs an owner browser session.
- Transcription rows or face thumbnails read back per event on 7.3.70.
- A successful player AI summary on 7.3.70.
- Protect's own view after the connect-time replies. The replies are now known statically from the owner-copied 7.3.70 `service.js` (`static-protect-7.3.70-bundle`): `diskInfo` expects `{storageSize: "<GB>"}` and `changeAiInferAgentSettings` carries `deepModeSupported`, `enableFaceEnhance`, `enableFaceRecognize`, `enableLprRecognize`, `enableRAM`, `enableSTT` and `region`. Both are answered in code (fixture-tested); the Key's `featureFlags.storageSize` and the absence of Protect's failure log lines were not read back.
- Deep mode or `/describe` tasks on 7.3.70.
- A reverified event.

## Limits

These are journal states, callback status codes and counters from one controller over about one day. A 2xx callback, a completed job or a growing row count is not a native saved caption, transcript, face or summary.
