# Separate OCR provider control (30 October 2026)

Parts of #17, #13 and #19: the runtime already supports an independent
`ocr_provider_config`, role-model changes and synthetic connection checks, but
an operator could not initially set that assignment/destination/key through
the site. This change adds the missing setup/preview/confirmation surface for
already plate-authorized cameras. It does not grant new processing scope.

## Boundaries

- Only `live_pool_detector.ocr_provider_config` changes. Existing raw identity,
  streams, plate-camera list, detector provider/key, fallback, score gates,
  budget and unrelated roles remain unchanged. A fresh library mutation
  rechecks the source revision under the existing per-config writer lock.
- First explicit OCR setup does not copy the detector credential. Same-route
  edits may retain only an existing explicit OCR credential; a route/provider
  change drops it. Official cloud adapters require a separate key.
- Preview is non-mutating/no-network. A ten-minute server-private signature
  binds login, profile, source/proposed revisions, full selection and random
  owned credential leaf. New key bytes are supplied only on confirmation.
- Login, same-origin/CSRF checks and shared mutation admission precede file
  work. Duplicate/unknown fields, unconfirmed, modified, expired, cross-session,
  unsupported-scope and stale requests refuse. Exclusive credential creation
  prevents two identical confirmations from overwriting the winner's key.
- Admitted commits use the existing tracked/shielded admin operation lifetime.
  A possible commit failure keeps its key and reports uncertainty; no automatic
  retry, key pruning, settings rollback or service restart is performed.
- The existing revision-bound provider Undo restores exact prior bytes and
  refuses missing previous credentials or later changes. Old and superseded
  private keys remain for controlled recovery.

## Qualification and limits

The initial parent feature tests fail because the typed OCR setup operation is
absent. Current focused tests exercise only fictional profiles/credentials and
owned loopback forms. Positive cases cover local setup, explicit separate keys,
same/different-route credentials, exact Undo, raw-byte no-op, sibling-slot
isolation and unchanged plate/detector settings.
Failure cases cover stale/scope/secret boundaries, confirmation tampering,
parallel credential creation and uncertain post-commit acknowledgement.

The maintained real Chrome scenario covers desktop/mobile preview, stale-form
refusal, required acknowledgement, Space/Tab/Enter submission, separate key,
unchanged detector and exact Undo. Decoder-inspected screenshots and exact-head
Python/Linux CI results are recorded in the PR. Fixtures make no real device,
controller, model or provider call. The existing synthetic provider-probe
scenario is a separate deliberately fictional loopback request.

This is not independent human review, a live provider switch, native plate
accuracy, complete recognition enrollment, ONVIF acceptance or full AI Key/Port
parity. A real processor change still needs a separate controlled deployment;
the original household permissions/routes are not changed by qualification.
