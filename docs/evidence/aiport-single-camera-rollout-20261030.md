# Generated one-camera slot validation — 30 October 2026

Tracking issue: #45. Status: **OPEN**.

The maintained rollout can provision a new addressed slot containing one
eligible camera. Its generated `paired_streams` configuration was nevertheless
rejected by the runtime parser, which required two streams even though ingress,
camera policy and fair inference already accept one through five cameras.

An actual synthetic rollout from a valid two-camera template reproduced the
failure: registration succeeded, but loading the generated new slot raised
`Invalid live camera pool`. The added regression failed at that same boundary
before the correction. Only the live-pool minimum changes from two to one;
empty/over-five lists, duplicates, source/decoder constraints, diagnostic mixing,
provider/cost policy and per-camera authorization remain guarded. Diagnostic
multi-stream rules do not change.

**314 targeted tests passed**, covering rollout, runtime candidate, ingress,
inference, camera policy, transactional registration and NAS reconciliation.
The generated slot now loads, constructs ingress/inference and reopens twice
with identical config/identity/certificate/key bytes. Construction performs no
model load or socket connection; those attempts fail the fixture. Empty and
six-camera configurations remain refused.

This is offline source/fixture evidence, not an actual new adopted container,
stream decode or native camera reassignment. The generated Linux decoder path
is validated offline, then mapped to a real host executable for constructor-only
testing; that executable is never launched. Initial local harness preconditions
(missing synthetic template certificate and absent Linux executable path) were
corrected without weakening production guards. No camera media, credential
replacement, model request, live slot or recording was changed.

The broader Mac/NAS reconciler creation/removal, native pair-set read-back,
recording continuity and failure/recovery gates in #45 remain open. Existing
four-Port continuity evidence is not a new-slot acceptance result.
