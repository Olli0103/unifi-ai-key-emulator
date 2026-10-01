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
- Deep mode or `/describe` tasks on 7.3.70 (observed from 1 Oct; see the owner-approved tests below).
- A reverified event.
- A session search result before the search host gained hybrid objects (observed from 08:36, 1 Oct).

## Static findings from the owner-copied 7.3.70 service.js (30 Sep)

These come from reading Protect's own bundle, not from live observation.

- **Audio events.** Protect keeps one audio event per camera (`onAudioAlarm`). Every type that reads `enter` is added to the event's `smartDetectTypes`, and the event ends only when a message reads `leave` or `none` for every type. Two sounds, or speech and a sound, are therefore one event with both types.
- **Audio-event thumbnails.** `pushAudioTask` sends `recognizeKeyFrames` with `ramType: image`. An audio event has no smart objects, so `saveEventTagging` keeps only event-level results: the description and key-moment tags (`metadata.ramTags`). Tag names must exist in Protect's own `ramTags` table; unknown names are skipped with a warning.
- **Reverification.** `ChangeSmartDetectSettings` carries `reVerificationPolicy`, built from enabled AI policies of type person, vehicle or animal reverification. Without such a policy every class reads `enable: false`, so no camera flags a track (`reVerifyEligible`) and the detection service never asks the AI Key. This explains why no `second_verifier` request has arrived.
- **Transcript search.** There is no transcript text search. An event with a non-empty transcript gets the filter label `smartDetectType:transcript`; transcripts are shown per event.
- **Deep understanding.** Capability `featureFlags.supportDeepMode` (or `supportVlm`). The mode is switched with `changeAiInferAgentSettings {modelMode}` and confirmed from getInfo's `featureFlags.aiMode`. Prompts, sampling and a JSON schema per object-type combination arrive with `changeDescribePrompts` and are confirmed from `featureFlags.describeConfigHash`. Tasks:
  - `RequestAI :7445/generate-embeddings`: person crops, answered at `/internal/aiprocessors/embeddings/{taskId}`. Protect joins sessions at cosine 0.75 by default.
  - `RequestAI :7968/describe` (`promptProfile: session-v1`, `open` or `close` pass): answered at `/internal/aiprocessors/descriptions/{taskId}` with `{description, labels, descEmbedding}`.
  - Session search encodes the query with E5 through `NL_PARSE` (`model: multilingual-e5-small`, 384 values).
- **Search host (read back 30 Sep).** The Key's PostgreSQL already holds `smartDetectSessionsSearch` (`descEmbedding vector(384)`) and `smartDetectSessionObjects` (`reidEmbedding vector(512)`), both empty, with pgvector 0.8.6.

## Native readback through the owner's web session (30 Sep evening)

Read with the Protect web app's own API client in the owner's signed-in browser; counts and states only.

- **Audio-event thumbnails:** 31 `smartAudioDetect` events have `ramState: done`, event-level `ramTags` (7 to 15 each) and a `ramDescription`. This is the Key's `ramType: image` path with RAM++ tags and a local description.
- **A 2xx is not a save.** 735 audio events and most smart events between 16:00 and 18:30 UTC read `ramState: failed`. The Model Server had failed every request since a full-size 4K snapshot exhausted the iGPU (CL_OUT_OF_RESOURCES); the Key had already bounded images at 1280 px, but the server was not restarted after that fix. After a restart it served every request.
- **Transcripts:** 96 events in one hour carry `sttSearchable`, so they get Protect's transcript filter label.
- **Second-stage verification:** all three reverification policies (person, vehicle, animal) exist and are enabled at 40 to 80 %. The settings page hid Person because no AI Key reported `supportPersonReId`; with Key r16 the console's `aiFeatureFlag` lists it and the Person row is shown and ticked.
- **Deep understanding:** the Key reads as `supportDeepMode: true`, `aiMode: basic`. Protect 7.3.70 offers the switch only in an internal QA window; the setting itself is the standard `deepUnderstanding` AI policy (disabled).

## Owner-approved tests (1 Oct 2026, Europe/Berlin)

Olli approved three gates. Each one was read back natively, in the owner's browser or on the NAS. The results below separate what was saved from what was only counted.

**Slot 4 stream recovery.**
- Garage dropped out after a DISCONNECTED state, and Protect never re-sent the stream start.
- Restarting only `aiport_slot_4` at 05:11:29 brought the slot back to `streams_with_decoded_frames` 2/2 by 05:12:14, with 0 rejected stream controls. Garage resumed.

**Deep Understanding on all cameras (enabled 05:14:07).**
- *Saved settings:* the `deepUnderstanding` AI policy reads `enabled: true` with `cameras: []`. The NVR's `deepUnderstandingSettings` reads `{enabled: true, allCameras: true}`. The Key reports `aiMode: deep` and `describeConfigHash` 9fec385b7771.
  - Rollback: `PATCH ai-policies/deepUnderstanding {"enabled": false}`, with the baseline kept in the private rollback directory.
- *Saved by Protect:*
  - `detection-sessions/stats` reads 9 sessions, 9 described, 0 pending and 17 detections at 05:34.
  - The session feed carries a description and labels.
  - At 05:53 the search host held 15 `smartDetectSessionsSearch` rows, all with a 384-wide, unit-norm `descEmbedding`, and 29 `smartDetectSessionObjects` rows, 20 of them with a `reidEmbedding`.
- *Key counters:* 24 describe tasks, all described with 110 labels in total; 15 embed tasks covering 20 crops, 0 failed.
- *Latency:* describe median 5.4 s and maximum 13.7 s, measured on the first tasks; a live check with Protect's own prompt took 6.9 s.
- *NAS during the test:* load 0.9 to 2.5, about 28 GB available. OVMS used about 7.5 GiB, the vision server about 2.2 GiB.

**Session search returns nothing (needs_evidence).** `GET detection-sessions/search` answers in about 35 ms with 0 sessions, for every query. What was ruled out:
- The Key answers every E5 `NL_PARSE` query with a 384-value vector that passes Protect's schema.
- Matching description/query pairs score cosine 0.85 to 0.93, which is inside Protect's distance cut-off.
- Protect's dense query, replayed read-only on the search host, returns all 9 sessions.

What actually happens:
- The table's scan counters do not move during a Protect search, so Protect stops before querying the table.
- On connect, Protect provisions hybrid search on an external search host: the `pg_tokenizer` and `vchord_bm25` extensions, an E5 `tokenizer.json` at `/usr/local/lib/e5-small/`, the `descBm25` column, and a `plpython3u` `rerank()` function that calls `http://127.0.0.1:8123/rerank`.
- The Key's PostgreSQL has none of these, so `hybridSearchAvailable` is false.
- With `featureFlags.deepUnderstandingHybridRerank` set, `readRankedSessionIds` then fails closed with 0 results by design. That flag is server configuration and could not be read.

Closing this gap needs a search-host image with those extensions and a local cross-encoder rerank service. That is a new service and was not deployed.

**Büro second-stage verification test.**
- Baseline: slot threshold 0.8 with no per-camera override, verified before the change.
- 05:20:42: Büro alone was lowered to 0.4 with image r44 (`camera_thresholds`, 2efc5ba).
- 05:50:45: the bounded automatic restore ran. The config and compose file are byte-identical to their backups, the image is `deep-r43-20260930`, threshold 0.8 with no override, and the slot reads 3/3 streams with speech on.
- During the window Büro had 0 observations and almost no motion (early morning), so no track entered the reverification window. The Key counted 0 reverification requests and 0 refusals; no problems arose with events, notifications or NAS load.
- Result: inconclusive. A reverified event stays needs_evidence.

## Owner-approved follow-ups (1 Oct 2026, 07:50–09:10 Europe/Berlin)

**Session search works natively (closes the gap above).**
- *Search host:* a new image `local-postgres-search:nas-amd64-bm25-20261001` adds:
  - VectorChord-bm25 0.3.0 (deb sha256 0631499a…);
  - pg_tokenizer 0.1.1 (468b0316…);
  - plpython3u at the same PostgreSQL 14.24;
  - the E5 `tokenizer.json` at `/usr/local/lib/e5-small/`;
  - `shared_preload_libraries=vchord_bm25,pg_tokenizer`, with `mem_limit` 2g.
- *Before the change:* a verified backup (search-20261001T081906, 125 session rows, 5612 Find Anything vectors) and a rehearsal of Protect's own setup SQL on a throwaway instance. The rehearsal showed one trap: Protect sets `search_path` with `ALTER DATABASE` and runs the BM25 block in the same session, where `bm25vector` is not yet visible. Protect's identical `ALTER DATABASE` statement was therefore applied once beforehand.
- *Provisioning:* on the Key's reconnect (08:33), Protect provisioned everything itself: the extensions, `bert_local`, `session_tok`, the `descBm25` column, the BM25 index and its `rerank()` function.
- *Rerank sidecar:* the Key answers `127.0.0.1:8123/rerank` (`rerank_relay.py`) and relays to the vision server's new `/v1/rerank`. That endpoint runs `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (Apache-2.0, revision 1427fd65, converted FP32; rerank.bin sha256 89b93bdf…) on the NPU, within 0.018 logits of the CPU.
- *Native readback:* four queries in the owner's browser each returned 30 ranked sessions in 0.26–1.05 s with Protect's rerank confidence:
  - "white cat on a table": the animal session at 99, the rest near 0;
  - "person sleeping in bed": 99 and 99;
  - "schlafende Person": 97, 96, 96;
  - "car in the driveway": vehicle sessions first, at 5–7.

  Relay and vision counters: 4 requests, 120 documents, 0 failures, no PostgreSQL errors.

**Face enhancement saves natively.**
- The first attempt (07:53) ran GFPGAN on the NPU and uploaded, but Protect refused the upload. Protect's multipart parser keeps only parts typed exactly `text/plain`, and aiohttp labels plain fields `text/plain; charset=utf-8`, so `camera`, `type` and `smartDetectObject` were dropped. The detection stayed `faceEnhanceState: queued`.
- With r20 (fields typed `text/plain`), Protect's native enhance route on a fresh face detection (08:36) gave `faceEnhanceState: done` and an `enhancedImageId`, and Protect counted `recentProcessedFaceEnhanceTasks` 1. It was triggered the way the app's Enhance action does it; Protect only sends `enhanceImage` on that request.

**Describe load.**
- r20 caps the crops of one describe request at 1 MP in total. Up to eight 768 px crops had left OVMS failing with CL_OUT_OF_RESOURCES at 06:39; it was restarted at 06:49.
- Between 07:30 and 08:40, close-pass describes timed out at about 30 per 10 minutes, against 12–29 completions. The Key cut deep tasks off after 60 s while Protect waits 180 s (`resolveTaskTimeoutMs`), and close passes first wait for Protect's HIGH-channel session export. r21 (09:09) uses 170 s and names close-pass export refusals; 14 had been `unclassified_worker_error`.

**Describe optimization (09:53–10:54, r22–r25).**
- *Measurement:* per-stage timing counters in r22 showed the time going to waits, not work:
  - export fetch 0.3 s, frame extraction 0.8 s, inference about 5 s;
  - waiting for the single iGPU gate: avg 16–19 s, max 86 s;
  - waiting for a worker slot: avg 19–78 s, max 154 s.
- *Cause:* about 17 % of answers ran away. Protect's describe schema leaves `labels` unbounded and samples greedily, so Qwen3-VL kept adding labels until the token cap and the JSON never closed. Normal answers use about 40 tokens. Each runaway held the gate for up to a minute, and the queue backed up behind it.
- *Fixes:*
  - r23: a 90 s bound per describe inference and 10 worker slots;
  - r24: `maxItems` 32 on the label list sent to the Model Server (`deep_mode.bounded_schema`, Protect's stored schema untouched) and a 384-token cap;
  - r25: admits Protect's retry of a failed local deep task (it re-sends the same task ID) and names the remaining admission refusals.
- *Result (r24/r25, 10:29–10:54):* 36 of 36 describes saved; 0 cut-off answers (largest 207 tokens); 0 timeouts; 0 refusals; gate wait avg 0.7–2.7 s; queue wait avg 0.3–2.6 s.
- *Protect readback (10:54):* 190 sessions, 184 described, 6 pending, 502 detections.

**Face pipeline (13:30–14:30, owner-approved).**
- *Identity reset:* at the owner's request, the 5 named face groups were unnamed and their 308 detections taken out of their groups (`PATCH` name null, then `assign-group` with `groupId: null`). Groups went from 534 to 529 with 0 named. Group and detection ids are backed up privately; names were not recorded.
- *AI Ports r45 (c9e2046):*
  - face crops for Protect go up to 512 px at JPEG quality 92 (was 256 px at 85);
  - a face reaches Protect's grouping only when it is at least 40 px, turned at most 50° and not flat (blurness ≤ 0.85);
  - the embedding sent is the normalised mean of the track's best three.
  - First readback on slot 1: 18 analyses, 15 faces, 9 held back as turned, 5 sent.
- *Vision server (npu-20261001-faces):* YuNet on the NPU finds five landmarks, the face is aligned to GFPGAN's FFHQ 512 template, restored and pasted back with a feathered mask. Crops under 64 px are declined, and the square method is the fallback.
- *Native readback:* an Enhance on a fresh face (14:28) went through the aligned path (`enhanced_aligned` 1) and Protect saved it (`faceEnhanceState: done`, `enhancedImageId`).
- *Start-up race:* restarting slot 4 once left Garage unstarted (one `UiStreamControl` refused); a second start gave 2/2. The same code path existed before r45.
- *Not yet done:* an AdaFace comparison needs named people to measure against; it waits until faces are named again.
- *Code fix, not deployed:* the deployed `npu-20261001-faces` image ran YuNet once per output (12 inferences per detector call) because its output mapping called the compiled model inside a generator. `named_outputs` now runs one inference and maps all 12 names. Runtime gain, saved Protect outcomes and NAS load stay needs_evidence until a later, separately approved vision deploy.

**Deep mode replaces the basic per-event path.**
- From 02:14 to 05:14, 13 of 14 smart events and 4 of 4 audio events got a caption and RAM tags.
- From 05:14 to 08:40, 0 of 140 smart events and 0 of 291 audio events did, and no new Find Anything (`ramDetections`) rows were written (last at 05:12). The Key received no `recognizeKeyFrames` task.
- Speech transcripts continued. Deep mode currently trades per-event captions, tags and image-similarity search for session descriptions and session search. Choosing between them is the owner's decision.

## Limits

These are journal states, callback status codes and counters from one controller over about one day. A 2xx callback, a completed job or a growing row count is not a native saved caption, transcript, face or summary.
