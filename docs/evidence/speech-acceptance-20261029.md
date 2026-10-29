# Native speech transcription acceptance, 29 October 2026

Issue [#15](https://github.com/Olli0103/unifi-ai-key-emulator/issues/15). Scope is the native transcription adapter and its policy/failure behavior, not recognition accuracy or every microphone/model combination.

## Saved native readback

On Protect 7.3.74, a bounded 30-minute audio-event query selected two native speech events with `metadata.sttState=done` and the saved-transcript flag. Their exact-event native transcription resources returned seven and two rows respectively. Every returned row contained non-empty text and ordered numeric timestamps. Only row counts and booleans were returned to the operator; no transcript content or audio was retained.

After a complete browser reload, the same frozen query window selected the same exact event identities and returned the same seven and two segments, with the same successful state and timestamp checks. Identity equality was checked privately by a digest, not published event IDs. This proves saved native transcript persistence after reload, rather than merely a successful worker callback.

No camera audio policy, provider setting or speech control was changed for this readback. It uses the existing opted-in cameras and backend. Unselected cameras are not newly enabled.

## Synthetic contract and failure checks

The real FFmpeg export/extraction path and loopback provider fixtures passed **55 speech and AI Port audio tests**, with one existing subprocess-cleanup warning and no skips in that focused run. Coverage includes:

- Native callback shape, export timestamp anchoring and bounded 16-kHz mono extraction.
- Empty/silence, high no-speech probability, repeated hallucinated segments, synthetic hum/hiss and brief clicks.
- Unsupported cameras, absent backends, malformed replies and backend failures that publish no invented transcript.
- Slow backends, bounded export retries and too-long audio refusal before retrieval.
- Explicit language fields and Unicode text preservation for synthetic German, French, Japanese and Arabic replies. This tests adapter mapping, not multilingual model quality.
- Duplicate speech tasks before and after worker restart cause one export, one transcription and one callback.

The full local suite with the portable decoder passed **2,050 tests**, with three skipped, 56 subtests passed and 22 warnings. The additional warnings include previously skipped fixture deprecations and the existing asyncio cleanup warning; they were not suppressed.

CI now installs the pinned `imageio-ffmpeg==0.7.0` test tool, verifies that its bundled executable runs, and adds only a temporary `ffmpeg` symlink to the test process PATH. This avoids silently skipping media tests on a runner without a system decoder. The helper is not a runtime package dependency, image update or downloaded model, and no binary is committed.

## Limits

No speech-recognition error-rate benchmark, translation guarantee, all-language model validation or all-camera enablement is claimed. Those remain quality and owner-policy work. The separate camera-inventory availability problem is not resolved by successful speech dispatch or by this issue's acceptance.
