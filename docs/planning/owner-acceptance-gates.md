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

- **State:** `find_anything.reverification` is on in the NAS Key config since r3 (30 Sep). No `second_verifier` request has arrived since then.
- **Record:** for one reverified event, `detectedThumbnails` `preReverificationObjectType` and the confidence type. Also confirm that an unsure verdict left the event type unchanged.
- **Rollback:**
  1. Restore `config.json.before-r3-20260930` in `/home/olli/aiport-deployment/aikey/state` (reverification off, byte-identical otherwise).
  2. Restart the Key once; it drains accepted jobs first.

## G6: native face camera indexed through face tasks

- **Needs:** a new person event on the Wohnzimmer G6 after the r3 deploy.
- **Record:** whether Protect's Find Anything returns that event's person for a "person" query, and its hit count.
- **Pass:** the event is returned. Then promote `index.face_task_search_tags` beyond `fixture-tested`.
- **Rollback:** none; the step is read-only.

## G7: alarm and household sounds (`live_sound`, 5c3a1a6)

- **Needs:**
  1. The owner's approval of the exact download: file names, source, size, licence and SHA-256 are shown before anything is fetched.
  2. A check that the chosen export takes a 16 kHz waveform. `SoundClassifier` assumes waveform input. Exports that expect log-mel patches (some ONNX conversions do) need a small, tested frontend change first.
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

## G8: diskInfo reply and two open audio types

- **Blocked:** the `diskInfo` reply shape, and how Protect treats two audio types open at once, both need static analysis of the Protect 7.3.70 controller package. That needs the owner's download approval **and** clearance of the open firmware-rights review. Neither is assumed.
- **Interim, reversible:**
  - `diskInfo` keeps answering 95, as before. Its only known effect is Protect's "Failed to sync storage size" log line.
  - Sounds keep the one-open-type rule.
