# Compact per-camera stream diagnostics (3 November 2026)

Follow-up to the named processing view in #13/#17. This is read-only source/
fictional browser work; no processor, camera, policy or decoder is started.

## Missing feature

Parent `0b0fc17bd6d409a36615b0a5e0ccf6a8a4f05bce` still drops normalized stream
geometry/recovery fields and renders every camera's complete counter table.
Two fictional HTTP feature cases fail before this change: stream fields are
absent and native collapsed camera sections do not exist. These are feature
reproductions, not production outage or recording observations.

## Implementation

- Reuse existing strict normalized fields for requested width/height/FPS,
  capacity points, decoder restart attempts/successes, deferred starts and
  full-frame count. No new producer, parser, capability flag or transport.
- Use native initially collapsed `details/summary` per camera. The summary
  shows the safely bound name/runtime number and stream/received-policy/
  pending flags, retaining Unknown vs false. Keyboard navigation works without
  JavaScript, form actions or extra frontend storage.
- Expanded views separate requested stream/recovery diagnostics from detection
  and budget counters. A received policy is not silently called successful
  inference or permission for every class.
- Requested resolution and FPS are not measured throughput. A stalled decoder
  can retain capacity. Recovery counters belong to the processor run; full-frame
  samples belong to the current decoder session and may reset on reconnect.
  No recordings, URLs, stream aliases, polygons, error bodies or raw IDs.

## Qualification

121 focused processing/runtime/binding cases and 437 documentation/compatibility
checks pass, including strict measurement refusal, inconsistent capacity,
unknown/zero/false, markup escaping, native collapsed semantics, identity
binding and the predecessor's actual twelve-second blocked-lock boundary.
Whole-tree Ruff/diff pass. No new whole-tree Mac run is claimed.

Both sandboxed fictional Chrome desktop/mobile/keyboard scenarios pass on the
final source. They inspect the seven stream measurements, focus/Enter expansion,
initially collapsed state, no body horizontal overflow, unavailable mapping,
stale inventory with preserved counters, session expiry, no action/storage/
foreign requests, unchanged fixture config and owned-resource cleanup. Four
actual component captures (collapsed/expanded at both widths) were decoded
and inspected. Initial browser receipts remain separate after the current-
decoder reset semantics were clarified; the final browser run binds new module
hashes and labels. Not independent human review or all assistive technologies.

Final exact-source Linux/PostgreSQL/media CI is recorded in the PR. Existing
runtime images and all actual camera/provider/permission/model/index/service/
scheduler state remain untouched. Native pairing, recordings, recognition,
zone enforcement, ONVIF, quality and full broader issues remain OPEN.
