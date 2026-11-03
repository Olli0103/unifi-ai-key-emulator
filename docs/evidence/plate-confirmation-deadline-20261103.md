# Plate-permission confirmation deadlines (3 November 2026)

Follow-up within administration issues #13/#17, after the separate camera-score
confirmation correction. The broader native/product requirements remain OPEN.

## Reproduction

On parent `d35753d86c0f3f08b50a0e9b6e4b00e83c5ac0dd`, plate confirmation
already has one-use, profile-scoped grants, unlike the old score-gate form. It
still checks only wall-clock lifetime, and checks it before awaited validation.
Two fictional loopback HTTP regressions show expired monotonic authority
admitted both before validation and after validation runs across the deadline.
The tests inject an elapsed-time clock; they do not change host time or wait
ten real minutes. A third corrected fixture proves a full 256-review pool
silently evicts an unexpired review instead of refusing the next preview.

The initial pool fixture hit the existing 60-mutations/minute limiter and is
not counted as a capacity reproduction. The corrected fixture advances the
injected limiter/grant clock by 61 seconds every 50 reviews: 256 reviews fit
within 305 elapsed seconds, with the ordinary limiter preserved, no cleared
admission state and no real wait. The next preview then reproduces HTTP 200
instead of the expected refusal on unchanged production source.

## Correction

Keep the signed wall-clock fields, per-control key, explicit crop/destination/
cost confirmation, session/revision/inventory validation, one-use consumption
and profile-scoped sibling invalidation. Store a private monotonic deadline
and check it both before and after awaited validation. At the exact 600-second
boundary the grant is expired. A full pool refuses a new preview without
consuming or evicting another live review; the oldest still-valid form remains
usable. A fresh preview after expiry remains possible.

No typed-store writer, runtime parser, OCR route removal rule, private-key
retention, mutation limiter, configuration format or rendering changes. No
new dependency or capability. A saved permission still requires a separate
Port restart to affect running processing; this is not immediate revocation.

## Verification boundary

Three regressions are reproduced before the production edit. 89 focused
positive and negative plate, score-gate, concurrent/cancelled/uncertain write
and shared mutation-limit checks pass, plus 437 documentation/compatibility
checks. Whole-tree Ruff and diff checks pass. The unchanged fictional Chrome
desktop/mobile runner passes both scenarios, covering explicit permission removal and a fresh
regrant, keyboard confirmation, source/module hashes, no real provider/device
requests and resource cleanup. It is not private-camera, recognition-quality,
native Protect or independent human accessibility/security acceptance.
Four decoded plate confirmation/saved screenshots were visually inspected by
the coordinator; the runner itself does not claim visual review.

Final result counts and exact-source CI are recorded in the PR. The earlier
complete Mac score-gate suite is not relabeled as a new plate-source full run.
No live config, camera, credential, permission, model, controller, processor,
image, index, database, scheduler or release mutation is performed.
