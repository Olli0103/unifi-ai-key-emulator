# Saved AI Key face camera restriction — 3 November 2026

This source checkpoint fills a remove-only control-site gap for an **existing
AI Key `face_recognition` policy**. It is not initial recognition enrollment,
native Protect grouping, Port 512-value recognition, an instant revocation or a
template-deletion operation. The broader
[control-site](https://github.com/Olli0103/unifi-protect-local-ai/issues/17),
[administration](https://github.com/Olli0103/unifi-protect-local-ai/issues/13) and
[face lifecycle](https://github.com/Olli0103/unifi-protect-local-ai/issues/20)
issues remain OPEN.

## Implemented flow

Settings links to **Saved Key face cameras** (`/key-face-cameras`). A private
read-only JSON view reports saved configuration revision, configured/not
configured and camera count, never camera IDs, names, server address, templates
or images. Both views explicitly distinguish saved policy from live authority.
An invalid/ambiguous record is unavailable, not a zero count or editable empty
policy.

Individual controls require fresh validated inventory with usable, unambiguous
names for every saved camera. Choices are session/revision/camera/model/name
HMACs, not editable raw identifiers. Removing checkmarks retains only a subset
of the existing list. A separate **disable all** preview remains available even
when inventory is unavailable; it removes the entire existing saved policy,
not another feature's policy or an unconfigured role.

Both actions require a preview, then separate initially unchecked consent.
The server-side grant is one-use, session-bound, at most ten monotonic minutes
old, with at most 32 live grants and no live-grant eviction. Apply rechecks
revision/result, fresh named choices, expiry, session and grant identity after
awaited work and after configured audit. Siblings are all consumed before
admission, even for a no-op or uncertain outcome. Exact-byte restoration cannot
revive an already consumed sibling. Normal Host/Origin/CSRF/shared mutation
admission and exact URL-encoded fields remain required.

The configuration store rechecks the same revision under its existing private
writer lock, preserves saved camera order, and allows only a subset or removal
of `face_recognition`. Other raw values, omitted defaults, server/max-faces/native
camera behavior on a retained policy, identities, credentials, search profile
and other roles remain unchanged. Earlier private configuration is archived;
there is no provider-Undo entry that silently regrants this scope. The actual
config bytes are read back after replacement; a possible post-commit failure
is uncertain, with no automatic retry or rollback.

Configured audit failure before admission refuses the write; failure after a
possible commit is uncertain. The new fixed `key_face_scope` audit action logs
only result/profile, not camera labels/IDs, endpoint, token or setting payload.
Audit remains optional under the existing application policy. Metadata reads
have one retained shielded operation and a three-second caller wait, including
after timeout/disconnect; the existing owner drains admitted operations.
This is not a kernel/filesystem deadline or a claim against hostile host owners.

## Runtime and retention boundary

Saving does **not** stop the running Key, cancel jobs/in-flight matches, change
Protect camera permissions, restart a service or issue a model/media request.
A separate AI Key restart activates the saved restriction. Native camera/Port
face behavior and speech/caption/search/enhancement keep their own policies.

The flow does not read or delete templates, footage, historical events, native
groups, model files, server copies or backups. Earlier policies remain in
private configuration history. Local identity deletion is the separate
[identity-control flow](face-identity-control-20261103.md), not a side effect
of restricting cameras. No automatic Undo or restart is offered.

## Source/fixture qualification

- 22 pure store cases cover raw preservation/order/no-op/defaults, unchanged
  template canary, subset/disable, missing policy, invalid/new/duplicate camera
  selections, stale revisions, duplicate JSON, actual concurrent writers and
  post-commit uncertainty. Network/model access is forbidden in these cases.
- The private HTTP group covers counts without identifiers/server, escaped
  names, saved-only semantics, exact field/content types, Origin/Host/CSRF,
  stale/cross-session/expired grants and inventory, unavailable individual
  editing plus inventory-free explicit disable, sibling/no-op/restored-byte
  replay refusal, concurrent admission, configured audit failures, late session
  changes, shared mutation limit, unavailable versus empty state, and one real
  timed-out/cancelled read retained until completion. The final combined new
  store/HTTP group passed **62 cases**.
- An earlier combined store/admin/identity/caption selection passed **160
  cases** before the final four empty/unavailable/single-flight cases. It is not
  a whole-tree Mac or native-media qualification. Full CI is recorded per exact
  head in the PR, not inferred from an earlier commit.
- The sandboxed fictional Chrome flow covers desktop/mobile, keyboard review
  and consent, exact cancel preservation, selected-only restriction, stale
  no-op refusal, explicit disable without usable inventory, unchanged template
  canary/unrelated roles, empty browser storage and collected owned resources.
  Requests are restricted to its owned loopback server; no real camera/model/
  media call occurs. Actual component captures are inspected separately; the
  runner does not claim it performed visual review.

One initial mobile full-page capture was offset after keyboard focus. The test
runner now checks the entire heading in the viewport and captures the main
component as well. This was a capture/verification correction, not a changed
application authorization or form behavior. An image-inspection attempt used
system Python without Pillow; the existing development interpreter supplied
read-only inspection, with no package/runtime change.

The [installed Admin256 checkpoint](admin256-native-20261103.md) precedes this
feature. No real configuration, face policy, camera/identity, model/credential,
service/image/scheduler or native permission was changed for these fixtures.
Native restriction plus restart enforcement, biometric quality, real private
browser/mobile/accessibility and wider lifecycle acceptance remain unqualified.
