# Remove-only saved Port audio scope — 30 October 2026

The existing Face/Sound permission page did not manage the Port's separate
`live_speech_cameras` audio/speech-presence allowlist. It now exposes that third
already configured role using the same private configuration parser,
revision/lock/history/atomic-save primitives and session/profile/feature-bound
opaque camera labels. There is no new framework, discovery, model call or grant
mechanism. Absent roles cannot be enabled and lists can only shrink.

## Dependent sound scope is not silently removed

The existing runtime schema requires Sound cameras to remain within the audio
allowlist. The first implementation's three failures/60 controls exposed that
dependency. It was not weakened: removing audio still required by Sound refuses
before lock/history/save and instructs the operator to remove the affected saved
Sound permission first. The page never alters the other role automatically.
Removing Sound alone still leaves audio permissions unchanged. Removing Port
audio does not change the separate Key speech-to-text configuration.

Partial restrictions retain the existing speech event limit, other roles,
pairings, identity/trust, provider/cost settings, keys/models and omitted raw
defaults. Whole audio removal also removes only its orphaned speech event limit.
Raw retained camera spelling/order is preserved. A no-op keeps exact config
bytes; provider Undo cannot regrant an earlier removed audio scope, and later
provider-only Undo retains the reduced scope.

HTTP changes retain actual Host/origin, current session, CSRF, scalar-field,
explicit-confirmation, revision and opaque-label checks. A post-commit error is
uncertain, not a claim that nothing changed; no automatic retry/undo is added.
Audit records fixed action/result/profile metadata, not identifiers, audio,
transcripts, keys or selection tokens. Inventory failure does not remove the
whole-role option, but the Sound dependency must still be satisfied.

## Verification and scope

Three old-source regressions established the missing store/UI operation. Existing
unknown-feature tests now use the still-unsupported `transcript` label rather
than the newly supported `speech`; their rejection assertions remain. A later
expanded fixture run had 81 passes/two failures because it omitted the synthetic
provider key required by existing Undo validation. Providing that explicit
private fixture key fixed the setup without weakening the validator/assertions.

Full-suite and exact-head external gate counts are recorded at integration. All
fixtures use fictional profiles/names/identifiers and synthetic local HTTP, not
actual private audio or a live model. These source changes/tests do not modify
any production permission or restart a processor.

**Saved configuration only:** the selected Port must be restarted separately to
change its running audio processing. This is not synchronous live revocation,
camera audio/recording opt-out everywhere, erasure of existing recordings,
transcripts/templates, expiry of controller/provider/backup copies, or new
browser/mobile/native-quality/deletion/independent-human acceptance. Existing
private history remains. Broad privacy/administration issues stay open.
