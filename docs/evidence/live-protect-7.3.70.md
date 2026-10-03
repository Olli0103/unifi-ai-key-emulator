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
- *Detector fix, deployed 14:39:54:*
  - The `npu-20261001-faces` image ran YuNet once per output (12 inferences per detector call), because its output mapping called the compiled model inside a generator. `named_outputs` (07ddb6c) runs one inference and maps all 12 names; it ships as `npu-20261001-faces2`, with the previous container kept stopped.
  - Synthetic benchmark on the NPU (same random image, 10 calls each): 40.2 ms per detector call before, 9.1 ms after.
  - First 4 minutes live: 8 re-ID and 12 embedding requests, 0 failed. No Enhance has run on the new image yet, so its saved Protect outcome stays needs_evidence.

**Faces on every camera (15:15–17:05).**
- *Why only Büro and Wohnzimmer:* faces are analysed only for detected people, and in that hour only Büro (272 person observations) and Flur saw people. Wohnzimmer is the native G6. History shows faces from 8 cameras.
- *What was wrong:* the 50° angle gate held back 70 of 98 Büro faces, and every frame was decoded at no more than 1280 px wide, so a 4K camera's faces shrank threefold.
- *AI Ports r46–r49 (44c7b30, fffb392, 09474db, fe44d7f, 50882ec):*
  - the angle gate holds back only clear profiles (65°);
  - a person without a sendable face gets up to 8 tries;
  - faces are counted per camera, with angle and size bands per slot;
  - each face camera's FFmpeg splits its 2 fps frames into the 1280 px detection output and a twin of up to 2688 px on a second pipe; face analysis and crops use the twin of the same frame.
- *Readback:*
  - all 9 paired cameras: face listed, enabled by Protect, engine loaded, twins at 2 fps; Protect has face detection on for all 10 connected cameras;
  - real FFmpeg on a 4K test pattern: 20 + 20 frames in 10 s for 1.8 CPU s;
  - slot CPU 20–39 % of a core, NAS load about 3;
  - Büro 17:04: 27 of 27 analyses on full resolution (16 ready, 11 after waiting for the twin), 26 faces, 9 sent.
- *Two fixes on the way:* a 4 s pairing window (r48 widened it to 15 s), and twins still encoding when faces were scheduled (r49 waits up to 1.5 s).
- *Open:* the outdoor cameras saw no person during the readback, so their face yield stays needs_evidence.
- *Start-up race (r50, 4a732a0):* a slot restart sometimes refused one camera's UiStreamControl, and Protect never repeats it, so the camera stayed off until a second restart.
  - Transient start failures (no first frame within 7 s, a decoder exit without a known reason, invalid data, RTSP 5xx) are now accepted and retried by the watch loop, as a refused relay connection already was. Clear refusals (access denied, not found, protocol rejected, RTSP 4xx) still return to Protect.
  - Refusal reasons are counted (`stream_control_rejection_reasons`).
  - The r50 rollout at 19:03 started every stream on the first try, so the fix is test-backed but not yet observed live.
- *AdaFace comparison (owner-approved, 1 Oct evening):*
  - *Data:* 150 thumbnails from the owner's three larger named groups (50 each; two G6-only groups, one AI Port-only group), fetched through the Key's media route into RAM and deleted when the session ended.
  - *Method:* AI Port pipeline (YuNet, ArcFace 5-point alignment to 112 px); 149 faces aligned. The two models embed the same crops; 3,626 same-person and 7,400 different-person pairs.
  - *Results, all pairs:*
    - EER: ArcFace R100 (current) 10.5 %, AdaFace IR101 5.7 %;
    - true accepts at 1 % false accepts: 62.9 % vs 88.9 %;
    - true accepts at 0.1 % false accepts: 36.3 % vs 77.8 %;
    - d′: 2.48 vs 2.99.
  - *G6 faces only:* EER 15.7 % vs 6.2 %; true accepts at 1 % false accepts 40.4 % vs 83.3 %.
  - *Rank-1:* 99.3 % for both.
  - *Caveats:* three identities, and the cross-group pairs also cross cameras.
  - *Switched (owner decision, 2 Oct 06:46–06:50):* AI Ports r51 (22c7dca) take `live_face.embedder_input: adaface`, scaling aligned RGB crops to -1…1 (ArcFace keeps raw 0…255). All four slots use `/models/adaface-ir101/adaface_ir101.onnx` (pinned sha256 1bffc499…).
    - Slot configs and compose were backed up as `*.before-adaface-20261002`.
    - All streams came up on the first start, with no face-engine errors; slot memory is unchanged (about 1–1.3 GiB).
    - *Readback by 06:58:* 9 AI Port faces were saved, in 2 new groups and none in the older ArcFace groups, as expected. Named AI Port groups need renaming once new faces collect. The G6 keeps Protect's own model.

**Parity checks (2 Oct, Europe/Berlin).**
- *Manifest:* brought up to the 1 Oct readbacks (`ai-key/2026-10-02.2`). Deep mode, describe tasks and their callback, E5 session search, the description embedding, hybrid BM25/rerank search, enhanced-face callbacks and emulator upgrades are now native-verified. Capability flags stay fixture-tested with an `indirect` live result, because a flag is never evidence on its own.
- *Plates:* Einfahrt saved 6 licence-plate events in 7 days, the last on 1 Oct at 17:13. Garage had 97 vehicle events and no plate; most likely no plate is visible from its view (inference). The AI Ports acknowledge Protect's LPR settings (`smart_settings_lpr_acks` 2 per slot).
- *Search recovery drill:* the 07:11 backup (871 sessions, 1,841 members, 5,612 CLIP vectors) restored into a network-less throwaway container on the bm25 image; the counts matched the live database and dense queries served. BM25 queries failed until Protect's own hybrid setup SQL ran, because the tokenizer catalog lives in extension tables that the dump does not carry. Protect runs that SQL on every Key connect, so the restore path is complete. A live restore through Protect stays needs_evidence.
- *Plate reading quality (r53, 79bea28, slots 3 and 4 from 08:50):*
  - Protect showed plates such as "H?K? 3058" and "L?S RH ?7", because the plate was read from the 1280 px detection frame in the same request as the whole scene.
  - A vehicle on a plate camera whose plate is missing or uncertain now gets one plate-only read on a crop of the same frame's full-resolution twin; plate cameras decode their twin at native width, 3840 px on Einfahrt. The crop reading is merged with the scene reading, so an uncertain character stays "?". A complete plate is reused for an overlapping box for 2 minutes. At most one crop per camera every 2 s.
  - Per-camera counts are in `plate_crops`; slot 3 CPU is 46 % of a core.
  - First readback: Garage made 2 crop reads with no plate visible, as expected from its view. Einfahrt's improvement needs a passing vehicle (needs_evidence).
  - *Attribution fix (code only, not deployed):* the detector sees no track identity, yet r53 reused a complete plate for any overlapping box for 2 minutes. A synthetic replacement vehicle with no reading of its own, or with a contradicting partial reading ("ABC 9?9"), inherited the old plate. Now a cached plate only completes a reading whose every legible character agrees. A contradicting reading drops the old entry and is cropped on its own. A recently read spot is not cropped again, which keeps requests bounded, but it keeps its own reading. Paid budget and uncertain characters are unchanged. This was a source finding, not an observed misidentification; real plate quality, saved native outcomes and NAS load stay needs_evidence.
- *Emulator upgrades:* seven Key image replacements on 1 Oct kept adoption and identity (same AI Key, adopted, no re-adoption). A rollback and a real state migration stay needs_evidence.
- *Faces after the AdaFace switch (06:50–07:32):* Flur, Schlafzimmer, Büro, Esszimmer and Haustür sent faces; the four outdoor cameras saw no person. Esszimmer recovered one dropped stream on its own.
- *AI Port firmware:*
  - Protect lists the four AI Ports at 5.1.12 with 5.1.16 available, an early-access build that is not in the public catalog, whose newest release is 5.1.12. Device auto-update is on at 03:00.
  - An update sends `UpdateFirmwareRequest` (download link, timeout) over the AI Port control socket, and the AI Ports used to leave it unanswered. r52 (c3b69ac, live 07:36) refuses it at once with status 501 `firmware_update_unsupported`, counts it and never fetches the link.
  - Protect 7.3.70 gates no AI Port behaviour on firmware versions above `minFirmwareVersion` 5.0.6, so the version string only drives the update badge (static).
- *Second-stage verification with real confidences (started 2 Oct, owner-approved):*
  - *Score detector:* YOLOX-S (Megvii, Apache-2.0, COCO; release 0.1.1rc0, sha256 c5c2d13e…) serves `/v1/detect` on the NAS NPU in 32 ms per frame (CPU 52 ms). On YOLOX's own sample image it gives the reference result: bicycle 0.95, dog 0.91, truck 0.61.
  - *AI Port r55 (b1de9ee), slot 1 only from 12:09:* the vision model still decides what exists. Each person, vehicle or animal takes the confidence of an overlapping same-kind YOLOX box; a weak or missing match is reported as 0.5, inside the reverification window, instead of being dropped. Packages keep their score, and the vision scores are kept when the score detector fails.
  - *Readback:* Protect saved real confidences on Flur (92, 86, 88, 70, 89, 77 %). For the first time Protect sent second-stage requests to the Key (11). The Key refused all 11 (`export_interval`): Protect spans the export from the first to the last thumbnail, an AI Port track has one thumbnail, and the MP4 adaptation required start < end.
  - *Key r26 (a5cc171, 12:26):* a zero-length window now exports the second that starts at the thumbnail.
  - *Open:* in the next 20 minutes no new request arrived (RequestAI unrefused, no reverify job). Whether Protect retries after the earlier failures, and a saved verdict (`preReverificationObjectType`), stay needs_evidence.

**Outage and deep-mode reset (2 Oct, 11:25–14:31).**
- The Model Server was stuck in `CL_OUT_OF_RESOURCES` from 11:25:50 until a restart at 14:25: every caption, describe and player summary failed (HTTP 400; 162 `describeImage` failures alone). The triggering request had already rotated out of the job journal. Since the restart: captions, session describes and re-ID run with 0 failures, and a player summary (`POST /aiprocessors/vlm/analyze`) answered in 7 s with a saved description.
- Between about 12:47 and 13:20 the Protect app reconnected to the Key several times (5 credential rotations since 12:26). On each Protect start, `enforceDeepUnderstandingInternalOnly` disables the Deep Understanding policy when the console owner is not an internal account (static, 7.3.70 bundle), so the Key fell back to basic mode. At 14:27 the policy was re-enabled at the owner's standing request (keep deep); the Key reports `aiMode: deep` again.
- The NAS monitor now reads the Key's `ai_mode` and raises `deep_mode_off` (local overlay, not in the repository). Its `ovms_failing` alert existed, but no tick ran during the outage.

**Second outage, watchdog and the first saved verdict (2–3 Oct).**
- *Model Server:* stuck in `CL_OUT_OF_RESOURCES` again from 2 Oct 14:45 until a restart on 3 Oct 15:01. Session describes, captions and player summaries failed for about 24 hours (Key: 81 of 7200 describe tasks saved at the time).
- *Reproduction (3 Oct, synthetic images only):*
  - One describe-sized request (20 crops of 180×220 px with Protect's person prompt, schema and sampling) raised the error after 17 s. From then on, every request failed until the process restarted.
  - The same request then passed three times. Requests without the schema or sampling passed, and so did up to 30 crops, 1 MP in total, grey, RGBA, CMYK and tiny or extreme sizes, and 4 concurrent requests.
  - The NAS gave no kernel log or GPU error state without root.
  - A fixed `--cache_size 2 --max_num_seqs 4` did not help: the first error came 53 s after the container was recreated. The error is intermittent and happens per request, not when the cache fills. Container memory stayed below 12 of 16 GB, with no cgroup limit events.
- *Watchdog (NAS, outside the repository):*
  - A systemd user timer runs every minute (linger on).
  - It restarts the Model Server when the error appears after its last restart and a one-token image probe fails. It allows at most one restart per 3 minutes and starts a stopped container.
  - It logs times and counts only.
  - First automatic restart: 3 Oct 15:09.
- *First saved native verdict:*
  - On 3 Oct 14:10 Protect asked the Key to verify a Wohnzimmer (G6) animal thumbnail. The Key's CLIP verifier confirmed it and the callback answered 200 at 14:11:28.
  - Protect saved `preReverificationObjectType: animal` and `preReverificationConfidence: 72` and now shows the detection at 99 %.
  - AI Port-sourced verdicts stay needs_evidence.
- *Long tracks:* one task was refused as `reverify_export`. Protect spans the export from the first to the last thumbnail, and a long G6 track exceeds the 120 s video bound. Since this change (code), the Key verifies the 120 s window from the first person, vehicle or animal thumbnail and counts it as `narrowed`. Later thumbnails keep their saved state. A long task with no such region in its window is still refused before any media.
- *Thumbnail at the export end:* one task failed at frame extraction (3 Oct, about 15:08). Protect ends the export at the last thumbnail, and the Key seeked to the very end, which holds no frame. A reverification thumbnail at the export end now decodes the final second (code).
- *Verdicts after the restarts (3 Oct, 15:00–15:40):* three more, all Wohnzimmer G6: person 74 → 86 % and 74 → 89 %, animal 74 → 99 %. In-window AI Port thumbnails existed in the same period (Büro person 56 %, Flur animal 76 % and 50 %) but got no verdict.
- *AI Port marking (code, not deployed):*
  - The source path from eligibility to the published snapshot works in synthetic tests. A person tracked at 56 % inside a 40–80 % window is published with `reVerifyEligible: true`, and one at 95 % with false.
  - The one defect found was at the window edges: the check used the raw score while events publish whole percent, so a score published as 80 % stayed unflagged and one published as 40 % was dropped. Both now follow the published percent.
  - New per-camera health counts show whether the current policy carries a window, how often Protect installed a policy with or without one, and where entered tracks and published snapshots fell.
  - Why Protect asked for no AI Port verdict since 2 Oct about 12:26 stays needs_evidence until these counts are deployed and read. One hypothesis to check is that a later settings message withdrew the window.
- *Firmware:* every AI Port slot refused Protect's update request at least once (1–2 each, 501 `firmware_update_unsupported`). The 03:00 auto-update leaves 5.1.12 in place.
- *Plates (counts since the slots started 2 Oct):*
  - Einfahrt: 59 vehicles, 11 plates read, 2 partial; 23 crop re-reads, 6 read, 4 complete, 5 improved, 2 conflicts kept apart.
  - The second slot-4 plate camera: 386 vehicles and 105 crop re-reads, none with a legible plate.

**Deep mode replaces the basic per-event path.**
- From 02:14 to 05:14, 13 of 14 smart events and 4 of 4 audio events got a caption and RAM tags.
- From 05:14 to 08:40, 0 of 140 smart events and 0 of 291 audio events did, and no new Find Anything (`ramDetections`) rows were written (last at 05:12). The Key received no `recognizeKeyFrames` task.
- Speech transcripts continued. Deep mode currently trades per-event captions, tags and image-similarity search for session descriptions and session search. The owner decided on 1 Oct to keep deep mode, and will name faces himself; the AdaFace comparison follows once named groups exist.

## Limits

These are journal states, callback status codes and counters from one controller over about one day. A 2xx callback, a completed job or a growing row count is not a native saved caption, transcript, face or summary.
