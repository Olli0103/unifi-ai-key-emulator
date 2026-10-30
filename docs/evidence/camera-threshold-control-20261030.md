# Camera-local score controls — 30 October 2026

Scope: existing API detector Port cameras only; partial progress on #17. This
adds operator controls for the existing `camera_thresholds` runtime policy,
not a new detector, camera permission, zone or native acceptance claim.

## Implemented boundary

- Authenticated GET `/camera-thresholds`; POST preview/apply/undo.
- Full existing saved-stream set, matched to fresh validated inventory and
  unique MAC/ID/name labels. Choices are opaque, server-private session/profile/
  revision/identity-bound tokens. Missing, stale, renamed or ambiguous inventory
  refuses named editing; no guessed camera labels or raw IDs in forms.
- Empty value inherits unchanged global gate; finite explicit `0 < gate <= 1`.
  Confirmation table lists before/after effective values and inherited/override
  mode. Ten-minute server-private confirmation binds every choice and value.
- Store validates full runtime config and 4-KiB limit, preserves unrelated raw
  fields, replaces only the optional gate map and preserves byte-exact no-ops.
- One private writer lock and revision refresh; separate gate-change undo record
  restores exact prior bytes only if current revision and all non-gate values
  match. It never masquerades as provider rollback or overwrites later changes.
- Admin adapter owns admitted writes through waiter cancellation and cleanup.
  Uncertain post-commit/journal/acknowledgement failures do not retry or roll back.
  Initial lock creation is coordination, not a saved-policy/history mutation.
- No inference, credential replacement, scope addition, controller write or
  service restart. Settings need a separately managed Port restart to activate.

## Observed focused/browser evidence

66 focused cases passed, including invalid scores, raw MAC alias replacement,
local-backend refusal, semantic no-op, sibling preservation, stale revisions,
concurrent same-revision writers, unsafe undo, lost journal/acknowledgement,
expired/changed confirmations and admitted-operation drain. Both selected
feature cases fail on parent `85d3796` (missing store method / HTTP 404).

The maintained real Chrome fixture passed desktop 1200×900 and mobile 390×844:
preview, required acknowledgement, Space/Tab/Enter save, saved result and exact
undo. All four camera-gate confirmation/saved screenshots were actually viewed.
There were no camera-gate provider calls, no overflow, page errors, external
page requests or residual owned browser/profile/server. The harness's separate
existing synthetic provider test still performs one fictional loopback request
per viewport; it is not a real model/provider call or this feature's transport.

Initial focused failures are retained: a test incorrectly assumed no first-use
coordination lock and another called a nonexistent session logout helper. A
later lost-journal test expected raw OSError, while the existing lock context
wraps it as the store's safe configuration error. Tests were corrected without
weakening runtime admission. One local full-suite invocation lacked the known
media/OpenSSL PATH and was deliberately interrupted after 74 passed; that is
not a full pass. The corrected configured run and exact-head Linux/CI results
are reported separately in the PR, not inferred here.

## Limits

Source/fictional browser evidence is not deployment, native camera quality,
calibration or controlled Flur ground truth. No live threshold was changed.
Key/Port feature parity and complete #17 acceptance remain open. Native
multi-camera/zone evidence, recording/PTZ status, ONVIF and release provenance
retain their separate outstanding criteria. No new dependency, model or binary
release is introduced. The external source-derived gate is same-agent review,
not independent human approval.
