# Explicit initial speech setup — 31 October 2026

Part of #13/#17; the broader original issue scope remains OPEN.

## Implemented

The signed-in Key speech panel now offers initial setup for an absent/null
speech role. Fresh validated Protect inventory supplies connected, uniquely
named speech-event-capable choices, with no camera selected. Preview binds
1–32 exact source-observed choices to the session, revision and ten-minute
monotonic expiry. Confirmation requires audio/destination and separate cost
acknowledgement; a new separate credential is entered only at confirmation.

No existing speech role is replaced or widened. The store adds only the speech
role with the existing private archive/atomic/OCC semantics; no provider-Undo
record is created that would silently regrant audio. Existing restriction and
provider flows are preserved. Vision credentials are not reused. The bounded
preview registry stores no provider credential value or raw session cookie.

Both preview and apply read fresh inventory; changed camera identity, name,
model, state or advertised audio support refuses prior consent. Unknown,
duplicate, cross-session, expired and already consumed choices/grants refuse
before key/config publication. Final authority is rechecked after awaited
work. A disconnected waiter cannot cancel an already admitted filesystem save;
normal shutdown drains it. An uncertain write is not called a rollback and
cannot be automatically retried; a possibly live/archived key is retained.

This is saved policy only. No actual microphone or Protect setting, camera
pairing, model/media/provider request, native operation or processor restart is
performed by the flow. Existing worker audio limits and other roles remain
unchanged. Reported speech support is not native task-delivery or quality proof.

## Verification

110 focused new-enrollment, existing speech/provider/restriction/role/key-recovery
and audit checks pass. Coverage includes exact new-role-only writes, private
separate credentials, canonical destinations, stale/revoked authority during
inventory reads, camera rename/removal, malformed/duplicated/unknown fields,
no implicit selection, absent/null initial roles, full preview capacity without
eviction, no existing-role overwrite and two overlapping confirmation requests
admitting one write. Simulated commit followed by failed acknowledgement keeps
the committed role/key and reports uncertainty without private exception text.

Real isolated Chrome on fictional loopback profiles passes desktop/mobile,
escaped camera names, zero initial selection, preview without write, changed
camera refusal, local/cloud destination disclosure, write-only credential,
keyboard confirmation, existing-role refusal, session expiry, no body overflow,
browser storage or foreign/model/controller/media request, and collected owned
servers/profiles/keys. The first run exposed an implicit select-label lookup;
the provider now has an explicit label/id. Test-fixture mistakes are retained
separately. Screenshots contain fictional inputs only.

Full frozen-suite, exact-head CI, outside-source review and private activation
results are recorded in the PR; a source merge does not itself deploy the page
or enroll a real household camera. Independent native/physical and broader
privacy/recognition/product acceptance are not advanced by synthetic checks.
