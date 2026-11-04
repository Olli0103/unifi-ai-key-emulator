# Speech-presence candidate — 4 November 2026

The [original synthetic baseline](synthetic-multimodal-cpu-20261103.md) remains
unchanged: six correct spoken positives, two hallucinated silence/noise negatives.
This candidate adds a maintained, pinned local Silero VAD gate before the existing
Whisper transcription path. It is a targeted quality repair for the operating
qualification in issue24, not a new whole-issue completion or production rollout.

## Actual isolated inference

The same eight byte-verified WAV inputs were submitted once each through the
candidate's actual loopback multipart HTTP endpoint. No gold text was mounted in
the model container. All eight returned200: six exact positive transcripts and two
empty negative transcripts, with zero transcript word/character errors on this
small corpus. Whisper was actually invoked six times; VAD checked eight clips.
No failure was relabeled an empty success, and no retry/threshold tuning occurred.

The fixed probability threshold0.5 and whole-clip retention rule were recorded
before an initial VAD-only pilot on these same eight inputs. That pilot classified
6/6 positive and2/2 negative correctly, then the actual server integration ran
once. The pilot is not an independent holdout; these are reused synthetic files,
not field-quality or unseen-corpus evidence. No original FAIL was overwritten.

The frozen19-file Whisper model-tree fingerprint and existing OpenVINO image
exactly match the earlier baseline. An initial comparison using `size` rather
than the original manifest's `bytes` field produced a different aggregate hash;
correcting the projection against the original stage code matched the immutable
baseline. Neither model nor inference output was changed or repeated.

Actual HTTP timings, including VAD, were approximately2.90–3.39seconds for the
six spoken clips and9.74–10.46milliseconds for the two negatives. The first
request was cold and there was no warmup. These are not comparable warmed
model-only percentile claims; CPU totals and provider charges were not measured.
The original corpus's weak search quality is unrelated and remains FAIL.

One network-none non-root CPU container used4CPU/8GiB, read-only model/source,
no GPU/NPU device and no camera/controller/credential mounts. It terminated
successfully without OOM and was removed. A separate offline wheel installer
was also removed. All73 pre-existing container identities, images, PIDs and
restart counts matched before/after. No production service, configuration,
recording, pairing or model was changed.

[Machine-readable bindings and aggregates](speech-vad-candidate-20261104.json)
identify the actual candidate source bytes, not a claim that a future merge ran
these measurements. Raw synthetic requests/responses and terminal receipts are
retained privately. [Operation and limitations](../speech-voice-activity.md)
explain the opt-in requirement, fixed artifact, cancellation and failure behavior.

## Qualification boundaries

34 targeted model/server cases pass, including non-speech suppression, original
positive audio/timestamps, fixed private failures, recurrent-state reset, final
padding, malformed/changed model refusal and shared-lock ownership after client
cancellation.111 combined model/server/compatibility/release checks pass.
Package/source/full-suite qualification is tracked in the associated PR.

The new flag is opt-in; old invocations remain ungated and health says so. A
production deployment and actual field/native speech acceptance remain open.
This eight-file correction does not satisfy every quality/recovery/soak gate in
issue24 and does not authorize any general accuracy or vendor-equivalence claim.
