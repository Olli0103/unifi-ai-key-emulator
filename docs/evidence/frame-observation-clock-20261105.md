# Decoded-frame clock binding — 5 November 2026

A controlled owner passage produced a stored Person snapshot, but its later
AI-generated search crop showed an empty scene. The query result scored 40
against the unchanged alarm threshold of 50. This is a failed alarm test.
The precise original decoder receive timestamp was not retained, so its
historical offset cannot be reconstructed from the stored thumbnail alone.

Inspection of the deployed pool path identified a deterministic timing defect:
it stamped snapshots and enter/moving events at inference completion. A frame
could wait in the observer, fair model queue, and slow inference before that
stamp. The worker subsequently sought the reported time in recorded video,
which need not contain the originally observed object.

The pool path now retains wall and monotonic receive times alongside the exact
JPEG, through both bounded pending queues. Snapshot clocks and enter/moving
wall times use that observation. Leave/expiry keeps its current clock. Existing
untimed callers retain their interface. This is decoder receive time, **not**
verified camera PTS; RTSP transport and decoder latency remain separate limits.
No threshold, detection policy, model, budget, or alarm query is changed.

Four regression cases cover delayed observer delivery, both pending replacement
policies, and snapshot/event timestamp binding. They fail against the unmodified
baseline and pass on the change. The focused ingest/inference/candidate/face
suite passes 194 tests (four existing Pillow deprecation warnings).

These source tests do not prove a new native alarm or phone notification.
The historical failed passage remains failed; no fabricated person text,
manual alarm trigger, replayed positive, or lower threshold is used as a pass.
