# Additional named speech-camera consent — 2 November 2026

Issues [#13](https://github.com/Olli0103/unifi-protect-local-ai/issues/13)
and [#17](https://github.com/Olli0103/unifi-protect-local-ai/issues/17).

## Feature

Initial speech setup and remove-only permissions already existed. There was no
supported form for adding a microphone to an existing role without editing JSON.
The new separate additions page preserves all existing cameras and role settings,
shows the selected microphones and existing processing destination, and requires
renewed audio, cost and save consent. Initial setup and removal keep their
existing authority; neither is repurposed to expand permissions.

Fresh supported-version metadata must match the saved version. Candidates need
distinct IDs/MACs, an unambiguous non-identifier name, connected state and reported
`alrmSpeak` support. Tokens bind those fields, model and audio capabilities to
the current session/revision. Confirmation repeats the inventory and exact
resulting-revision checks, then consumes a bounded monotonic ten-minute grant
before the existing drained file-operation owner admits the write.

The store retains raw unrelated values and omitted defaults. Under its existing
writer lock it rechecks the current bytes, validates the resulting configuration,
identity, search profile and credential routes, archives the original bytes,
and verifies the atomic replacement. Only `speech_to_text.camera_ids` may change.
No provider Undo is created. A stale earlier provider Undo refuses; a later
provider-only Undo retains the added audio scope. Lost write acknowledgement is
uncertain, without retry or automatic reversal.

## Qualification boundary

The new fixture suite covers exact preservation/history, malformed or repeated
selections, missing/legacy/full roles, stale revisions, changed camera identity,
name/state/audio support/version, fresh inventory, session expiry/logout and
cross-session grants, missing consent, CSRF/origin/query refusal, concurrent
one-use confirmation, separate store writers, provider Undo, fixed audit codes,
and logout during awaited validation. All cameras/providers/credentials are
fictional. No real audio or controller/model request is used.

The committed browser runner exercises actual sandboxed Chrome at desktop and
mobile sizes, including unchecked choices/consents, escaped camera names,
changed-microphone refusal, keyboard confirmation, persisted exact camera-list
addition and expired-session refusal. It checks private storage and foreign
requests, binds all source-module hashes and collects owned profiles/servers.
The browser CI invokes it and uploads result JSON/screenshots; a runner result
does not itself claim visual review.

This is source/fixture acceptance of one missing administration workflow, not
an independent human review or installation. Live Admin activation, native
speech persistence, model quality and the broader device/ONVIF/zone controls
remain separate. No real permission, service, camera, credential, audio recording,
cost policy, caption budget or GPU configuration changes in this work.
