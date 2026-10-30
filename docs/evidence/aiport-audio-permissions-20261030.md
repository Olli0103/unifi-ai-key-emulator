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

## Final checks and native administrator activation

Frozen source `13d3554c1146d47743c4dc6ef348631542702ac0` passed 85 focused
scope/store/Undo/accessibility cases and 280 audit/session/uncertainty/document
checks. The full native Mac run passed **3,094 tests, three skipped** with 22
existing warnings and 56 passing subtests. Linux CI for the head, including the
current merged documentation context, passed **3,097 tests, one skipped**, with
22 existing warnings and 56 passing subtests; the remaining skip requires real
CLIP weights. All four required PR and integrated-main jobs passed, and the
integrated main's complete production-module bytes equal the frozen source.

A separate same-agent source-derived gate passed 103 assertions against the
clean exact commit. Three owned numeric-loopback TCP connections were observed,
with zero unowned transport/DNS attempts and no fixture/task/thread or tracked
byte/mode residue. It exercised explicit Sound removal before audio removal,
dependency/stale/cross-profile/cross-feature refusals and retained the earlier
scope/authentication/uncertainty cases. It is not independent human review.

A separate candidate image was built without a network and all 92 installed
modules/license matched. Its owned network-disabled read-only profile test
returned twelve configured panels:

| Port slot | Face | Sound | Audio / speech presence |
| --- | ---: | ---: | ---: |
| 1 | 3 | 3 | 3 |
| 2 | 2 | 2 | 2 |
| 3 | 2 | 2 | 2 |
| 4 | 2 | 2 | 2 |

These are **saved permitted-camera counts**, not people, sounds, transcripts,
events or quality. Inventory names were not configured, so individual selection
was not qualified there. Anonymous/bad-CSRF/unknown-profile operations refused;
no valid policy/provider/model operation was admitted. The proof signed out and
collected its server/container. All seven existing containers, five profiles
and administrator authority remained unchanged during that qualification.

Before activation, six administrator authority/TLS/compose inputs were privately
byte-verified on the NAS and separate SSD. The first rollout helper stopped
before transport and before publishing compose: a broad numeric replacement had
changed its internal expected port from 8765 to 12765 while the real configured
port stayed 8765. No service operation was admitted; the old image stayed healthy
and compose matched its verified before-copy. The failed helper/staged inputs
remain separate. The corrected helper changed only that expectation and used
distinct receipt/stage names, retaining the twelve-panel checks and full
destination-keyed mount comparison.

Only the separate administrator image was then replaced. Private-VPN direct
HTTPS/default-CA/SNI verification, secure host-only session cookie, exact command,
healthcheck, complete mount descriptors, resource/hardening configuration,
authority and TLS bytes remained. It became healthy with zero automatic
restarts/no OOM, refused the old cookie, accepted the **same administrator
credential**, read twelve panels and signed out. The previous image remains.
Five profile bytes and six processor/database container identities/images/
restarts stayed unchanged; no valid policy/provider/model operation was admitted.

Fresh Protect reads before, before retry and after retained the original
one-Key/four-Port identities, nine pairings and five adopted/connected processors.
A separate final read found the expected image healthy, its private VPN socket
reachable and the old HTTP-loopback/NAS-LAN ports closed. No processor restart or
actual audio opt-out/live enforcement test was performed. Browser/mobile,
owner credential handoff, automated certificate renewal, deletion, recovery,
quality and broader acceptance remain open.
