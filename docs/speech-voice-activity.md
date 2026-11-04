# Optional local speech-presence gate

The local Whisper server can reject non-speech before transcription with the
maintained [Silero VAD](https://github.com/snakers4/silero-vad) model. This addresses
the observed silence/noise hallucinations in the frozen synthetic speech corpus;
it is not a guarantee for quiet, distant, overlapping or arbitrary field speech.
The original ungated baseline remains a FAIL. No transcript words are blacklisted.

## Model and installation

Install the `speech-vad` extra into the **speech server's** environment, not the
AI Key environment: `python -m pip install '.[speech-vad]'`. Existing backend
requirements (OpenVINO GenAI or pywhispercpp) remain separate. ONNX Runtime is
pinned to1.28.0; inference uses one CPU thread and no network.

Download the model separately from the immutable upstream revision:

```sh
curl --fail --location --output silero_vad.onnx https://raw.githubusercontent.com/snakers4/silero-vad/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/data/silero_vad.onnx
```

Expected2327524bytes; SHA-256
`1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
The server validates the exact regular file and consumes its verified bytes,
not a later path lookup. Weights are not bundled and startup never downloads them.
Model/protocol upstream license is MIT; the pinned copyright/permission notice
is retained in `THIRD_PARTY_NOTICES.txt` and package license metadata. Existing
release provenance/signing/maintainer gates remain unchanged.

Add `--vad-model /models/silero_vad.onnx` to the existing `local-whisper-server`
command. Mount that model read-only in a container. A missing dependency, wrong
model hash or load failure aborts startup. Existing invocations without the flag
remain **ungated**; `/healthz` explicitly exposes `voice_activity.enabled` and
counts (`checked`, `no_speech`). Do not claim a deployment is protected until
its actual option, model, health and inference path are verified.

## Contract

- Input remains bounded16kHz, mono,16-bit PCM, at most120seconds.
-512-sample frames,64-sample context and zero-initialized recurrent state per
  audio file follow the pinned upstream ONNX protocol. Partial last frames are
  zero-padded only for the VAD; original audio is not modified.
- Fixed upstream default probability threshold0.5. Any positive frame keeps
  the **whole original clip** for Whisper, with original offsets. No minimum
  speech duration or cropping suppresses short utterances.
- No positive frame returns a successful empty transcript/segments and the
  original duration. A model exception or invalid output is an error, **not**
  an empty success or unfiltered fallback.
- The existing single-model lock spans VAD and transcription, including thread
  completion after cancellation. Only aggregate counters are exposed; no audio,
  probabilities, transcript, path or model error text is logged/retained.

A gate suppresses non-speech hallucinations; it does not repair words hallucinated
inside a clip containing real speech. Qualification must preserve positive
transcripts and separately measure rejection/latency, with original failed runs
retained. Changing production configuration requires its normal bounded rollout
and read-back; this source addition alone is not production activation.
