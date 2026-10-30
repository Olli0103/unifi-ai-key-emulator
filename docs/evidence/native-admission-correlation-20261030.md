# Native admission refusal correlation — 30 October 2026

This adds bounded diagnostic metadata for the existing Key dispatcher. It does
not change admission, retry, model execution, native status or feature flags.

## Why aggregate counts were insufficient

A read-only, fixed half-hour native sample returned 29 events without reaching
the query cap. Among the eight events longer than 30 seconds selected because
they already had nonempty descriptions, all eight deterministic caption journals
were completed with accepted callbacks. Six native events had `ramState=done`,
two still had `ramState=failed`. The description of the 124.759-second event
matched its completed caption journal byte-for-byte. Its failed overall native
state is retained, not relabeled successful.

The corresponding four possible Face/Index journal paths were absent. Absence
does not show whether such a task was sent, refused before admission, or caused
the native state. Aggregate refusal counters cannot identify that event.
Likewise, native event duration is not the requested export interval: the
124.759-second event alone does not prove #209's window branch executed.
No footage, description text, private identifiers or hashes are published.
These selected observations are not a quality, completeness or hardware-parity
benchmark; no event was replayed or extra inference requested.

## Added diagnostic boundary

`device.recognize_rejection_samples` records only the existing
`recognizeKeyFrames` dispatcher's **WorkerError** refusal path after camera
policy checks. Success, wrong-camera/scope denials, timeout, cancellation,
unexpected exceptions and invalid handler responses are not recorded as worker
refusals. An internal diagnostic fault cannot replace the original refusal or
reply. The existing counters, active-admission cleanup and duplicate cache are
unchanged.

At most 64 distinct diagnostic tuples are visible for 900 monotonic seconds
since first observation. Identical repeats do not renew that visibility.
Pruning occurs on record/read; this is not scheduled physical erasure. Restart
loses all entries. Capacity eviction means an absent entry proves nothing.

Only these values are retained:

- `event_fingerprint`: SHA-256 of ASCII
  `local-aikey-native-rejection/1`, one NUL byte, then compact JSON
  `[camera_id, event_id]`. Camera must be 24 hexadecimal characters and event
  must contain 1–128 ASCII letters/digits/underscores/hyphens. Case is preserved.
  JavaScript uses `JSON.stringify([cameraId, eventId])` with the same prefix.
- An exact allowlisted worker-reason category, or
  `unclassified_worker_error`; never the exception text.
- A known RAM type or `other`; a boolean `postVLM` or null; presence bits for
  `personMeta`, `faceMeta`, `vehicleMeta`, `thumbnailMeta` in that order.
- A safe integer source-interval duration or null, and key-moment list length
  capped at 129 (meaning more than 128), not the timestamps or their values.

No bodies, raw camera/event IDs, media/callback URLs, tokens, object labels,
coordinates, face data or descriptions are stored in the ledger. Fingerprints
are linkable comparison metadata, **not anonymization or authentication**. The
schema's explicit scope is `worker_rejection_only_native_outcome_unknown`.

## Verification

The 33 new repository cases fail on the parent (32 main cases plus the added
cancellation case). The corrected combined group passes **156 tests** including
existing dispatcher/protocol checks. The initial
candidate's reason validator incorrectly excluded the existing `mp4_path`
category; the failed run (74 failures, 81 passes) is retained. The validator now
accepts the existing lowercase category grammar, not arbitrary error text.

A separate same-agent harness checks 16 Python/Node SHA-256 peer vectors,
20 malformed identity/reason cases, capacity and exact first-seen TTL boundary,
cached duplicate reply, original refusal and injected diagnostic fault. The
saturated 64-record snapshot is 14,684 bytes. Synthetic fixtures are collected;
no socket, model/provider or application-state write occurs. This is not an
independent human review or a native rejection cause already observed on the
new source. The existing failed native events are not retroactively diagnosed.
