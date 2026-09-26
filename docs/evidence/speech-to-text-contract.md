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
