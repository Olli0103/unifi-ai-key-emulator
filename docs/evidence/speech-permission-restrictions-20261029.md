# Remove-only saved speech permissions — 29 October 2026

## Implemented

The authenticated control site now has a separate existing-speech-role permission
page. It can retain a subset of that exact saved camera allowlist or remove the
saved speech role. It cannot enroll a new camera, select a new provider/model,
change clip limits, replace a key or change another feature/identity.

The scoped store operation reuses the existing generic preview/apply validation,
private archive, atomic replacement, immutable-identity/search-profile and
credential-route checks. Revision is checked again under the apply lock.
Retaining the same set preserves its original order and exact file bytes.

The page uses existing inventory only for names, never as authority to expand
scope. Names are escaped. Inventory failure hides ambiguous individual selection
but does not block disabling all saved speech. Selected values are opaque
session/revision-bound HMAC labels; membership is recomputed from the current
server-side allowlist. Session, exact Origin/Host, CSRF and revision remain the
authorization boundary; the labels are not independent bearer grants.

No raw camera ID is placed in a selection value, URL or audit entry. Audit uses
one fixed action plus outcome/profile only. Provider keys remain server-side;
no key file is created/deleted. Private configuration history remains retained.
Permission changes do not create provider-Undo records; stale provider Undo
cannot silently restore the old audio allowlist. A later provider-only Undo
preserves the reduced scope.

## Verification

**113 focused tests pass** across permissions, speech provider, role models,
secret handling, audit, general control site and direct HTTPS. Cases include:

- subset preview/apply with all other config fields preserved; full disable with
  key/history retention; order-independent byte-exact no-op;
- forbidden new cameras, duplicate/malformed input and a real intervening save;
- old provider Undo refused and later provider Undo retaining restricted scope;
- unauthenticated, expired, cross-origin and wrong-CSRF refusals;
- unknown/duplicate/stale/cross-session selections, duplicate/contradictory mode,
  missing confirmation and unrelated secret fields refused;
- escaped camera names, opaque selections, no fixture key/camera-ID echoes;
- inventory outage with usable full disable, official-provider default destination,
  fixed-category audit and honest uncertain-after-commit handling.

All inputs are invented fixtures and HTTP clients are loopback test servers.
No real camera, audio, model server, credential or deployment is used.

## Saved is not live

The form explicitly requires acknowledgement that an AI Key restart is needed.
Saving does **not** stop active audio processing or in-flight work. It does not
delete local/Protect/provider/backup copies, revoke keys or prove live worker
state. No native configuration or service was changed for this feature.

Initial enrollment, broader camera/feature permissions, real browser/mobile
acceptance and native live revocation remain open in #5/#13/#17. This does not
close those issues or establish speech quality/retention completion.
