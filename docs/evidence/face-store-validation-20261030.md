# Private named-face store boundary — 30 October 2026

This source-only change hardens the AI Key's existing local SFace named-template
store. It does not enroll a real identity, load new weights, grant a camera
permission, modify deployed templates or establish recognition quality/native
grouping. AI Port's separate 512-value face adapter is not converted to SFace.

## Reproduced failures

The old loader used path existence/mode checks and unbounded text/ordinary JSON
reads. It did not validate stored template dimensions/norm/schema bounds before
`match` used `zip` over vectors. A short or oversized stored vector, a scaled
vector, Boolean/nonfinite components or malformed identity metadata could be
accepted, ignored or fail with a non-domain exception. JSON duplicate keys could
erase earlier records. A private FIFO blocked before type rejection.

The initial new group recorded **23 failures and four passing controls** on the
old source, including the FIFO timeout. Every name/vector/state in these tests
is fictional. This is a source regression finding, not an observed private
misidentification or disclosure in the deployed system.

## Read boundary and validation

- Open once with no-follow/nonblocking flags; use that descriptor's regular-file,
  current-owner and no-group/other-access checks. Missing stores remain empty
  without creating state. A symlink or wrong type/owner/mode is not repaired.
- Bound initial size and the actual read to **8 MiB**, including growth after
  `fstat`. This is a local safety bound, not a vendor protocol limit. It covers
  the existing 50-identity × 20-template × 128-value limits with normal serialized
  unit vectors and bounded names.
- Use the existing strict UTF-8 JSON decoder, rejecting duplicate/nonfinite/
  overflowed-float input. Require exactly schema 1 and an identity dictionary;
  do not accept a Boolean as the schema/version or timestamp.
- Validate bounded trimmed names, exact entry fields, nonnegative signed-64-bit
  `created`, one to twenty templates and exactly 128 finite real, non-Boolean
  values per template. Stored unit norm must be within 1e-6 relative/absolute
  tolerance; no hidden normalization, migration or template repair occurs.
- Refuse with fixed `face_store_invalid`, `face_store_not_private` or
  `face_store_unavailable` domain errors, not names, vector values or paths.
  Valid matching threshold/ranking and explicit delete behavior stay unchanged.

Existing permissive, damaged, noncanonical or custom-field stores will fail
closed. Inspect/recover the private original or use a separately approved
migration; do not discard names/templates or regenerate device identity as
automatic troubleshooting. No file is chmodded, rotated, replaced, purged or
rewritten by the loader.

## Verification scope

Synthetic checks cover dimensions/norm, Boolean/nonfinite/huge components,
duplicate keys, malformed/oversized dictionaries and entries, fixed errors,
symlink/permissive/foreign-owner rejection, a real FIFO in a bounded child
process, oversized reads and a file growing after `fstat`. Positive checks keep
matching, empty stores, explicit delete and fictional device-state bytes intact.
The existing local-face/AI Port/face-enhancement tests remain separate adapter
checks. No real photo, biometric template, native ID, camera media, enrollment
server call or production filesystem is used by the new tests.

Owner/file checks are not encryption or a complete ancestor-directory,
hostile-same-user, concurrent-enrollment, backup-expiry, automatic TTL or
whole-copy deletion audit. Explicit local deletion does not delete provider,
controller, logs, snapshots or backups. Native enrollment/grouping/search,
false-match calibration and safe model migration remain open for #20; broader
privacy/retention acceptance remains open for #5. See the
[data-flow and retention guide](../data-retention.md).
