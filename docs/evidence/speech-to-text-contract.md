# AI Key speech-to-text contract (issue #15)

This is static evidence only; no firmware was run. The sources are the Protect 7.3.60 controller bundle (`usr/share/unifi-protect/app/service.js`) and the vendor AI Key 2.2.8 firmware (`ui-websocketd` and its `syswrapper.sh`, `ui-ai-infer-agent`, `ui-stt-agent`). Nothing from either package is redistributed here. The live console runs 7.3.68, and whether its speech path matches is `needs_evidence` until a transcript is read back there.

## Trigger

Protect's `pushAudioTask` runs a `SPEECH_TO_TEXT` task only for an audio event whose `metadata.detectedThumbnails` contains `alrmSpeak`. That is the camera's own "Speech" audio detection.

- **Processor choice:** `pickTargetAiProcessor` selects an adopted, connected AI processor whose `speechToTextSettings.enabled` is true and whose settings list the camera (or all cameras).
- **No capability flag:** unlike face enhancement, face recognition and license plates, speech has no feature-flag gate. The capability map names `supportTts` for the feature type, but `findPreferenceAiProcessor` checks only the settings.
- **The "Speech to Text Off" row:** Protect shows the AI Key's `speechToTextSettings.enabled` there, and it is a Protect setting.

On 26 Sep 2026 (read-only), none of the nine AI Port-paired legacy cameras reported any smart-audio types. Wohnzimmer (G6 Instant) supports `alrmSpeak` but has it off. Wohnzimmer alt, which has it on, has been disconnected since March 2025.

## Dispatch

`dispatchSpeechToText` sends the device request `speechToText` with:

```json
{"reqUrl": "/internal/aiprocessors/video/export?camera=…&event=…&channel=0&start=…&end=…&type=rotating&format=mp4&skipVideo=true&createEvent=false",
 "resUrl": "/internal/aiprocessors/speech-to-text",
 "camera": "…", "event": "…", "channel": 0, "start": 0, "end": 0,
 "type": "rotating", "format": "mp4", "skipVideo": true, "createEvent": false}
```

The vendor Key rewrites `format=mp4` to `ubv` and converts that itself. It then extracts 16 kHz mono audio and transcribes it with a local Whisper model through `SpeechToTextProc`. Segment times are offset by the export's `x-timestamp` or `x-start-timestamp` header, or else by `start`. The vendor drops unsupported-language, low-SNR, `[inaudible]` and `[blank_audio]` output, and treats three identical consecutive segments as silence.

## Result

The Key POSTs JSON to `resUrl`:

```json
{"camera": "…", "event": "…", "stt": [{"startMs": 0, "endMs": 0, "text": "…"}]}
```

Protect validates exactly that shape and answers `200 {"stt": n}`. It then publishes `aiprocessor.stt.uploaded`, and `saveSpeechToText` handles it:

- It writes one `transcriptions` row per segment, with `eventId`, `cameraId`, `start = startMs`, `end = endMs` and `text`. So the times are absolute epoch milliseconds.
- If there is at least one segment, it sets the event's `metadata.sttDetected` and `metadata.sttSearchable`.
- It completes the task; the event's `sttState` follows the task.
- Transcripts are read back through the event transcriptions route (`getTranscriptionsByEvent`). Access requires the playback-audio permission for the camera.

## This implementation

`speechToText` is refused (`ENOTSUP`) unless a `speech_to_text` backend is configured and the camera appears in its explicit `camera_ids`. The worker accepts only the exact dispatch above: the fixed callback, the AI processor export route, a query that matches the command exactly, `skipVideo: true`, and a bounded duration.

It extracts 16 kHz mono PCM with the configured ffmpeg and posts it to the configured backend's `/audio/transcriptions`. The backend is either the official OpenAI API or a loopback OpenAI-compatible Whisper server; no other host is accepted. The reply's segments become absolute times from the export start headers.

It never invents text:
- no speech, low-confidence segments or repeated hallucinations yield `stt: []`;
- a failing or malformed backend reply yields no callback, and the task fails in Protect.

Audio and transcripts are neither logged nor kept; the job journal records only the segment count.

## Live acceptance still needed

1. The user enables `alrmSpeak` on a supported camera (Wohnzimmer G6) and turns on the AI Key's Speech to Text for that camera in Protect.
2. The user chooses the speech backend. Audio leaves the network only if it is the OpenAI API.
3. A real speech event produces a transcription row that Protect shows after a reload.
4. For the nine AI Port-paired legacy cameras, Protect raises no `alrmSpeak`. Speech there would need an AI Port audio detection path, which does not exist yet (#28).

## Live result on Protect 7.3.68 (26 Sep 2026)

Setup:
- Wohnzimmer (G6 Instant) has `alrmSpeak` on.
- The AI Key's `speechToTextSettings` covers only Wohnzimmer.
- The backend is a local whisper.cpp (`large-v3-turbo`) container on the host-only container bridge. No audio left the Mac.

Result: between 10:41 and 10:48, four real speech events each dispatched `speechToText`.
- **Our side:** every job completed. The callbacks were accepted, posting 10, 13, 6 and 6 segments.
- **Protect readback:** `GET /proxy/protect/api/events/<event>/transcriptions?camera=<camera>` returned exactly 10, 13, 6 and 6 rows. Every row has text, and its start and end fall inside its event.
- **Event metadata:** `sttState: done`, `sttDetected: true`, `sttSearchable: true`.
- **Privacy:** only counts and time bounds were read; no transcript text was inspected.

## All-camera speech eligibility on Protect 7.3.68 (28 Sep 2026, #15 and #28)

This was a read-only pass in a signed-in console session. It read settings, capability flags and event counts only. No audio was fetched, no transcript text was read, and no setting was changed.

**Settings on both sides agree on one camera.** The AI Key's `speechToTextSettings` is enabled, `allCameras` is false, and it lists exactly one camera, Wohnzimmer. That is the per-row Speech to Text microphone under Intelligence › Basic Understanding. The deployed Key's `speech_to_text.camera_ids` holds the same single camera (hash-matched), with the local Whisper backend.

| Camera | Paired | Protect-advertised audio types | Speech to Text row | State |
|---|---|---|---|---|
| Wohnzimmer (G6 Instant) | no | 9 supported, `alrmSpeak` on | on | **eligible and active** |
| Wohnzimmer alt (G4 Instant) | no | 5 supported, `alrmSpeak` on | off | **supported but offline** (disconnected since March 2025) |
| Einfahrt, Flur, Garage, Büro, Esszimmer, Schlafzimmer, Giebel Vorn, Haustür, Giebel hinten | yes | none | off | **unsupported while paired** |

**Why the nine show no audio types.**
- **Pairing masks native detection.** For a paired camera, Protect takes the capability flags from the AI Port's per-camera flag map. On all four AI Ports, every map entry reports an empty `smartDetectAudioTypes` and `hasMic: false`, even though every camera reports a microphone of its own.
- **Garage shows it is pairing, not hardware.** Garage (G4 Dome) raised 109 native `alrmSpeak` events between 23 Sep 14:14 and 24 Sep 16:43 UTC, and none since. The NAS AI Port project was created about 40 minutes after the last one. Those events carried no speech task, because Garage was not in the speech settings.
- **Model hint for Esszimmer.** Esszimmer is the same model as Wohnzimmer alt, which advertises `alrmSpeak` unpaired.
- **So "no smart-audio types" is a pairing effect.** The earlier record said the nine paired cameras had none. Where hardware support exists, it is hidden by pairing.

**Recent native speech (7 days to 28 Sep 07:30 UTC):**
- Wohnzimmer: 664 `alrmSpeak` events, of which 529 have speech state `done` and 132 `failed`; 3 had no state yet.
- No other camera had any `smartAudioDetect` event.

**Route for the nine: found in the 7.3.60 controller code (28 Sep 2026).** The earlier `needs_evidence` is resolved statically. A static read of the local 7.3.60 `service.js` (not redistributed) shows the whole path:

1. **Capability.** `EventFeatureFlagsUpdated` from an AI Port is stored in `featureFlagsMap[<camera MAC>]`. `smartDetectAudioTypes` is derived from the **same** `smartDetect` list as the object types, filtered by the audio-type enum. It is one of the `overridableUiCameraFeatureFlags` copied onto the paired camera. So `smartDetect: [..., "alrmSpeak"]` makes Speech selectable on that camera.
2. **Settings.** Once `EventAIPortStatus` reports `isAudioEventReady: true` for a streaming camera, Protect sends `ChangeAudioEventsSettings` to the AI Port. The request carries `deviceID` and `enableAlrmSpeak` (and the other `enableAlrm*` flags) taken from the camera's `smartDetectSettings.audioTypes`. The camera's own `hasMic` must be true; all nine report it.
3. **Event.** Protect's message handler accepts `EventSmartAudio` from an AI Port and attributes it to the paired camera whose MAC equals `payload.deviceID`. The same handler drops a paired camera's **own** `EventSmartAudio`, which is why native speech stopped at pairing.
   - The payload must match `audioMessageSchema`: numbers for the clocks, `eventId`, `leveldB` and `levels`; strings for `loudNoise` and `soundLoss`; and each audio type as `enter`, `moving`, `leave` or `none`.
   - The event starts on the first active message. It ends only once **every** audio type reads `leave` or `none`.
4. **Transcription.** At the event end Protect saves a snapshot thumbnail as `detectedThumbnails[{type: "alrmSpeak"}]`. `pushAudioTask` then queues `SPEECH_TO_TEXT` for the AI Key, exactly as for a native camera.

**Implementation (this commit, opt-in).**
- **Config:** a pool config may list `live_speech_cameras`, which must be paired pool members.
- **Audio decoder:** for each listed camera the AI Port runs a second, isolated decoder on the relay stream's audio track (16 kHz mono PCM in memory). A missing or failing audio track backs off (5 s doubling to 5 min) and never touches video.
- **Detector:** an energy and zero-crossing speech-presence detector with hysteresis. It needs 0.6 s of voice within 1.5 s to enter, leaves after 2.5 s of quiet, and caps an event at 120 s.
- **Capability:** the AI Port advertises `alrmSpeak` for that camera. It reports `isAudioEventReady` once audio flows.
- **Settings:** it acknowledges `ChangeAudioEventsSettings` and sends `EventSmartAudio` enter and leave **only while Protect has Speech enabled for that camera**. Turning Speech off closes an open event. Other cameras' settings are refused (501).
- **Privacy:** no audio is stored, logged or sent anywhere by the AI Port, and it produces no text. The transcript still comes from the AI Key, which reads Protect's own export only for cameras in its speech allowlist and transcribes with the local Whisper container.
- **Health:** the `speech` block holds counts only.
- **Tests:** synthetic positive and negative tests in `tests/test_aiport_speech.py`.

**Still `needs_evidence` (live):**
- that Protect 7.3.68 behaves as 7.3.60 here;
- that the relay alias carries an audio track;
- the detector's real-world recall and false triggers.

**Acceptance per camera:** a new `smartAudioDetect` event on the original camera with `alrmSpeak`; `sttState` done; and an exact-event transcription row count equal to the Key's posted segment count. Count only, no text.

**What does not need that evidence** (owner decisions, not taken here):
- Unpairing a camera would bring back its own `alrmSpeak`, at the cost of its AI Port object detection.
- Wohnzimmer alt would become eligible if it came back online and its Speech to Text row were turned on.
