# Plate camera controls — 30 October 2026

Partial feature progress for #17/#19, not native accuracy or full parity.

## Source behavior

The new `/plate-cameras` page operates only on existing API Port streams. It
shows the actual saved OCR route (separate OCR or current detector), vehicle
class status and a before/after permission table. Every save requires explicit
camera/crop-destination/cost/restart acknowledgement. No provider/key, camera
pairing, detection class, score gate, fallback, budget, other recognition scope
or running processor is changed by viewing/previewing this form.

Typed `plate_camera_status`, `preview_plate_cameras` and `apply_plate_cameras`
reuse the existing full schema validator, private writer lock, revision check,
history and atomic persisted readback. Only plate scope changes; removing the
last camera also removes any separate OCR assignment, with an explicit warning
because the runtime forbids an orphan OCR route. Private key files stay.
Semantic set no-ops retain raw spellings/order/default omissions and exact bytes.

A separate server-private control key, ten-minute full-selection signature and
random draft bind the login/profile/revisions and fresh validated camera
ID/name choices. Each draft is consumed once before admitted work and older
same-profile drafts are invalidated. This includes uncertain outcomes and
exact-byte revocation/ABA. Pending authority is bounded to 256 and resets on
Admin restart. Admitted writes reuse the tracked/drained AdminAPI operation;
there is no automatic retry, restart, rollback or privacy regrant.

## Focused and browser observation

48 focused cases passed: initial explicit grant, scope restriction, last-camera
removal with/without separate OCR, retention of private credential files,
refusal of unknown or duplicate cameras, no-op/raw formatting/sibling preservation,
stale revisions, route/CSRF/
session/signature/camera-selection constraints, consumed/superseded drafts,
exact-byte revocation, lost acknowledgement and disconnected-waiter drain.
The initial shared test helper wrongly assumed vehicle classes were disabled;
its 18 failures were all that prerequisite assertion, not a product admission
failure. The fixture actually enables vehicle detection. The assertion was
corrected and an explicit vehicle-class-off warning case added. An initial
replay fixture replayed only removal; it was corrected to actually grant first
and then restore exact revoked bytes before the replay attempt.

Maintained real sandboxed Chrome passed desktop 1200×900/mobile 390×844. It
revokes the fictional permission, then performs a **new explicit** grant with
required acknowledgement and Space/Tab/Enter. Four actual decoded confirmation/
saved screenshots were viewed. There were no feature model calls, external
page requests, overflow or page errors; owned browser profiles/server collected.
The harness separately makes one existing fictional provider-probe request per
viewport. After asserting full semantic equality it explicitly restores fixture
formatting; this is not a product permission Undo.

Full configured Mac, exact-head Linux/CI and the separate outside-repository
same-agent gate are recorded in the PR when complete. No new dependency,
production permission change, model call, real camera grant or binary release is
implied. Production remains on Admin #217 until separately qualified activation.
Independent human review, native accuracy and real-client acceptance remain
separate evidence requirements.


## Full-history documentation false positive

The first CI secret scan reported the natural-language test enumeration at
line 32 of commit `841408e53e93073ded55297ffc486e7a1884e5c7` as a generic
credential. The pinned local Gitleaks 8.30.1 reproduces the exact single finding;
that source line is English prose, not a credential. The current sentence is
rephrased. One exact historical commit/path/rule/line fingerprint is documented
in `.gitleaksignore`, following the existing scoped exception contract. No
whole-file exclusion, rule change or current/future credential allowance is
introduced; history is retained, not rewritten. The original failed CI receipt
and redacted local report are preserved.
