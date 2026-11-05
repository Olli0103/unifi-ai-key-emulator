# Spacing the single empty-motion recovery

## Failure boundary

A confirmed crossing produced a native motion event but no person event. The
original recording contains a visible person. The port's corresponding minute
contains two clean empty model responses, no provider failure and no zone or
score rejection. The original inference images were not retained, so their
exact content cannot be reconstructed from those counters.

An offline run of the unchanged motion gate against the original recording
reproduced a sampling weakness: after a quiet scene, opening a translucent door
consumed the initial request and its only recovery on adjacent frames. The
person became clearly visible just afterwards, when no request remained.
This reproduction uses synthetic empty responses; it does not establish what
the live model saw or returned for any particular image.

## Change

After a clean empty first fresh-motion response, retain the existing ten-second,
one-recovery allowance, but require at least two seconds between the initial
image's observation time and the recovery image's observation time. A later
frame must still pass the unchanged motion gate. The delay is not a sleep and
causes no polling or extra provider requests. Positive first responses still
receive their ordinary confirming sample without this delay.

The live pool passes the decoder's monotonic reception timestamp to detectors
that opt into the observed-frame method. Queue or API latency cannot turn a
neighboring old image into a sufficiently later image. These timestamps are
not camera PTS. Legacy callers without timestamps use processing time and
cannot establish this same queue-age guarantee.

The recovery count, provider backoff, request budget, score threshold, zones,
and requirement for two positive observations remain unchanged. This is a
bounded sampling tradeoff, not a guarantee for every short or occluded passage.

## Evidence

- New synthetic regressions fail against the unchanged parent.
- Tests cover adjacent empty frames, delayed processing with capture timestamps,
  legacy processing-time spacing, two-positive tracking and pool handoff.
- Existing tests retain expiry, budget, backoff and cross-camera checks with
  explicit simulated elapsed time.
- Offline original-recording comparison selects a later image containing the
  visible person; no images or private identifiers are included in this repo.
- Native detection, query matching and push delivery after activation remain
  `needs_evidence`. A successful local replay is not a native alarm result.

All code and synthetic fixtures were authored for this project with AI
assistance. No third-party implementation or vendor fixture was copied.
