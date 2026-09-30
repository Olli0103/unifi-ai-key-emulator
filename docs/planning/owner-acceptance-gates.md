# Owner acceptance gates for AI Key and AI Port parity (30 Sep 2026)

These gates are what still stands between the current code and native parity. None of them can be closed by the agent alone. Each plan is bounded and reversible, and records only states, counts, types and times: never caption text, transcripts, images, faces, names or credentials.

**Standing constraints:**
- Firmware rights and the public-release review are open. Nothing below assumes they are cleared.
- A 2xx callback, a completed job or a growing row count is not native acceptance (see C15 in the compatibility manifest). Each gate therefore ends in a readback from Protect's own records or UI.

**Common preconditions:**
- The monitor tick shows no alerts.
- The Key queue is below 20.
- The Key and the AI Ports run the images recorded in `docs/planning/nas-final-cutover.md` and in memory.

## G1: native caption readback on 7.3.70 (N15)

- **Needs:** an owner browser session on the console. Either Olli signs in at unifi.ui.com in Chrome, or accepts the console certificate warning himself; the agent does not click past it.
- **Steps:**
  1. Pick one event whose `recognizeKeyFrames` job reached `completed` after 30 Sep 06:00 local.
  2. Do one exact-event GET (`/proxy/protect/api/events/{id}`).
- **Record:** `metadata.ramState`, and whether `metadata.ramDescription` is non-empty.
- **Pass:** `done` and non-empty. Then set `live-protect-7.3.70` to `native-verified` for `callback.ram_full_event_tagging`.
- **Rollback:** none needed; the step is read-only.

## G2: player AI summary (N14)

- **Needs:** Olli presses the summary button once, on one recent event with a caption.
- **Recommended before it (owner deploy decision):** deploy a Key image that includes `worker.inference_concurrency` and set it to 1. The local Ollama serves one request at a time, so a summary then waits for at most one caption instead of up to six. Health `worker.inference_gate` shows `on_demand_waiting`. Rollback: remove the key and restart the Key; it drains accepted jobs first.
- **Status (30 Sep):** Olli reported a rendered summary (owner screenshot, not read back). The gate is deployed (r6), but 7 summaries timed out after a restart with a backlog, because gated captions held every worker. The caption-lane fix exists in code with synthetic tests only; it takes effect with the next Key deploy.
- **Record:** whether the summary rendered, and the state of the matching `on_demand` job in the NAS Key journal.
- **Pass:** it rendered and the job reached `completed`. That closes the C15 gap for `control.request_ai.on_demand_inference` on 7.3.70.
- **Fail:** record the job's fixed error or `worker_rejection` label. Do not repeat the press in a loop.
- **Rollback:** none; the step is read-only.

## G3: transcripts and faces read back on 7.3.70

- **Needs:** the same browser session as G1.
- **Steps:**
  1. For one `alrmSpeak` event on an AI Port camera, count the rows returned by `GET events/{id}/transcriptions?camera=`.
  2. For one person event with a face on a paired camera, count its face thumbnails and group IDs.
- **Pass:** at least one transcription row, and at least one face thumbnail with a group.
- **Rollback:** none; the step is read-only.

## G4: repeat-visit face grouping

- **Needs:** Olli walks past one paired camera twice, at least 10 minutes apart, with Face enabled on it.
- **Record:** the face-group ID count across the two events.
- **Pass:** both events share one group.
- **Rollback:** none. Delete the resulting face group in Protect if Olli wants.

## G5: reverification readback (N13)

- **State:** `find_anything.reverification` is on in the NAS Key config since r3 (30 Sep). No `second_verifier` request has arrived. The 7.3.70 bundle explains why: Protect sends an enabled `reVerificationPolicy` to cameras and AI Ports only while an AI reverification policy (person, vehicle or animal) exists. Without one, no track is ever flagged `reVerifyEligible`.
- **Code (9bf674f, AI Ports r43):** AI Port tracks inside the policy's presence-probability window now pass and carry `reVerifyEligible` in their snapshot; scores below the window are dropped.
- **Readback (30 Sep, owner's web session, counts only):** all three reverification policies have been enabled since about 23 Sep (person earlier) with zero hits. In 3 hours, 28 detected thumbnails fell in the 40–80 % window, but 27 were face confidences, which verification does not cover. The one person, on the native Wohnzimmer G6, was not verified. The AI Ports publish only scores at or above their 0.8 detector threshold, so they never produce an in-window track to flag.
- **Needs:** a test that produces in-window tracks. Either lower one AI Port slot's detector threshold to the window's floor for a bounded trial (flagged tracks would then reach Protect with `reVerifyEligible`), or an owner walk in poor light past a native camera.
- **Record:** health `reverification_person_enabled` on the AI Ports, the Key's `reverify` job count, and for one reverified event `detectedThumbnails` `preReverificationObjectType` and the confidence type. Also confirm that an unsure verdict left the event type unchanged.
- **Rollback:**
  1. Delete the policy in Protect.
  2. Optionally restore `config.json.before-r3-20260930` in `/home/olli/aiport-deployment/aikey/state` (reverification off) and restart the Key once; it drains accepted jobs first.

## G6: native face camera indexed through face tasks

- **Needs:** a new person event on the Wohnzimmer G6 after the r3 deploy.
- **Record:** whether Protect's Find Anything returns that event's person for a "person" query, and its hit count.
- **Pass:** the event is returned. Then promote `index.face_task_search_tags` beyond `fixture-tested`.
- **Status (30 Sep, read-only counts):** after the r5 restart the Key answered 3 G6 face tasks (native-face skip path, which now carries the search tags), and the G6 gained 4 `ramDetections` rows between 05:00 and 06:08 UTC. This is consistent with the path but does not attribute the rows; the gate still needs the search hit.
- **Rollback:** none; the step is read-only.

## G7: alarm and household sounds (`live_sound`, 5c3a1a6)

- **Needs:**
  1. The owner's approval of the exact download: file names, source, size, licence and SHA-256 are shown before anything is fetched.
  2. A check of the export's input and output: `SoundClassifier` feeds a 16 kHz waveform, or YAMNet's log-mel patch when the input shape ends in [96, 64] (the frontend is synthetic-tested only). It needs an output whose last axis equals the class-map length. One offline run on a known clip, recording the top class only, confirms the frontend matches before any camera uses it.
- **Steps:**
  1. Put the model and class map under `aiport-deployment/models/sound` and record their SHA-256.
  2. Load them once offline: `SoundClassifier` refuses a class map that lacks any mapped AudioSet label.
  3. Add `live_sound` for one camera on one slot, backing up that slot's `config.json` first.
  4. Redeploy that slot only (`up -d --no-deps --no-build aiport_slot_N`).
  5. Olli enables the wanted audio types for that camera in Protect.
- **Record:** health `sounds.classifications` greater than 0 and `errors` empty.
- **Controlled test:** Olli presses a smoke detector's test button near the camera. Read back one native `smartAudioDetect` event with `alrmSmoke`, type and time only.
- **Pass:** that event exists on the original camera's timeline, with no duplicate speech event overlapping it.
- **Rollback:**
  1. Restore the slot's `config.json` backup and redeploy that slot.
  2. Olli turns the audio types off in Protect.

## G9: object limits in the live configs (owner deployment decision)

- **State:** the staged code has no per-camera object-event ceiling and no count ceiling on the local fallback. The live slot configs still carry `max_events_per_hour` (12, and 30 on slot 1) and `fallback.max_per_hour` 120, and the running images apply them. No slot sets the optional paid `max_requests_per_hour`.
- **Owner decisions:**
  1. Deploy the new AI Port image, which ignores those keys (health `legacy_limits_ignored`).
  2. Delete the keys from each slot config.
  3. Whether any paid cost guard should exist at all.
- **Steps (per slot, one at a time):**
  1. Back up the slot's `config.json`.
  2. Deploy the image with `up -d --no-deps --no-build aiport_slot_N`.
  3. Confirm health lists the two ignored keys and 9 of 9 streams decode.
  4. Delete the keys and redeploy.
  5. Confirm `legacy_limits_ignored` is empty.
- **Record:** per camera, `events_entered` against the former cap over a busy hour, NAS load, `pool_inference.api_fallback` `backoff_skipped` and failures, and Ollama latency. Counts only.
- **Pass:** Protect saves events past the former 12 or 30 per hour on at least one busy camera (native readback of event counts), and NAS load stays below the monitor's alert level.
- **Rollback:** restore the slot's `config.json` backup and the previous image tag in Compose, then redeploy that slot.

## G8: diskInfo reply and two open audio types (resolved in code, 30 Sep)

- `diskInfo` answers `{storageSize}` (aca60ed), from the owner-copied 7.3.70 bundle.
- Two audio types share Protect's one audio event per camera (f243d2d, AI Ports r43).
- **Record:** one native `smartAudioDetect` event whose `smartDetectTypes` names two types (type and time only).

## G10: deep understanding (7.3.70)

- **State:** Key r14 reports `supportDeepMode`, with re-ID (NPU), E5 (CPU) and the local Qwen3-VL describer. Protect's describe prompts have not been pushed yet.
- **Needs:** Olli enables Deep Understanding in Protect's AI settings (all cameras or selected ones).
- **Record:**
  1. getInfo `aiMode` reads deep and `describeConfigHash` is non-empty.
  2. The Key's `deep` counters: `embed_tasks`, `describe_tasks`, `described`.
  3. Row counts in `smartDetectSessionsSearch` and `smartDetectSessionObjects` on the Key's search host.
  4. One session found by a deep search.
- **Known risk:** Protect joins people to a session at cosine 0.75, tuned for its native 512-value model. This Key uses Intel's 256-value re-ID model zero-padded to 512, so grouping may be too loose or too strict. Protect's per-camera `reidCutoffByCamera` setting can tune it.
- **Rollback:** Olli disables Deep Understanding in Protect; Protect then switches the Key back with `modelMode: basic`.
