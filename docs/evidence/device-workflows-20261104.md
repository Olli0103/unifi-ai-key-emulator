# Complete operator workflows — 4 November 2026

Original [issue #17](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17)
is a frontend/shared-API/browser task. This change connects the existing role,
camera, processing, privacy, index and host-maintenance pages to complete typed
device/initial-feature workflows. It does not promote synthetic source evidence
to native adoption, physical camera, biometric or model-quality verification.

## Original acceptance mapping

| Original requirement | Concrete implementation and check |
| --- | --- |
| Both profiles without JSON or shared credentials | Existing separate Key/Port first-run pages; Key-only, named/default Port-only and combined normal listeners. Missing explicit profile still refused; Port-only creates no Key or Key routes. Actual authenticated save/Undo and CLI compatibility tests. |
| Controller trust and non-destructive device settings | Typed public-certificate/pin review, separately bound Key control/search roots, anonymous TLS test, private resources + OCC/identity checks, durable intent, saved-byte readback, explicit Undo/reconcile. Real owned TLS peers test CA, wrong leaf/root, no client credential or application bytes and expiry. |
| Pairing, ONVIF references and zones | Guided native Protect pairing/zones followed by separately labeled local relay allowlist. Fresh inventory/capacity checks and scope pruning; independent write-only ONVIF credentials and immutable metadata. Unsupported ingest/PTZ/recording honestly remain unavailable, not native-tested. |
| Feature policies, thresholds, budgets, role models | Existing per-camera/detection/OCR/caption/speech/budget forms plus initial Key local services and Port face/audio/sound. Named-camera consent, host-bound opaque model choices, dependency checks and retained face-space guard. No silent all-camera permission or new object-event cap. |
| Test/preview/apply/rollback while preserving identities | Existing supported provider/catalog tests plus anonymous controller test; separate review and one-use apply, normal exact Undo (initial Port face retains guard explicitly), uncertain-save reconciliation. Model/media test unsupported is not success. Synthetic identity canaries and cross-profile preservation. |
| No secrets after save | Real isolated Chrome write-only provider/ONVIF forms, confirmation and Back traversal, live input/HTML, URL, local/session storage and audit checks; private drafts stay server-side and are cleared on logout/cleanup. |
| Invalid/expired/failed/mismatched/mobile cases | Existing browser/HTTP coverage retained; new HTTP stale/reidentified/offline/renamed camera, missing consent, changed binding and forbidden path cases, plus actual desktop/mobile combined and Port-only Chrome paths. |
| Maintenance, diagnostics, queue/errors, index consequences | Existing typed guarded pages and source tests remain in place. Host results and unknown/unsupported states are distinct. This work does not run backup, update, migration or restart in production. |

## Transactions and negative checks

The same private OS writer mutex fences generic config stores and dedicated
device writers. Admission is after lock acquisition and fresh validation but
before the first resource/history/config mutation. Session, epoch, one-use
token and configured durable audit must still hold. Identifiable attempts consume
sibling device grants even on missing consent/no-op, preventing ABA replay.
Named inventory is checked again before admission and has a five-second lease;
lock waiting cannot extend it. No grant renews its original ten-minute expiry.

Intent publication and acknowledgement loss after config/journal writes are
tested as uncertain. Startup and unrelated writes refuse unresolved intent.
Reconciliation reads exact retained before/after revisions rather than retrying
the write. Backup tests copy/manifest/read back synthetic state, restore the
consumer and execute Undo; omission of selected/retained trust, ONVIF resources,
face-space guard, journal or history cannot be hidden by editing the manifest.

Face guard tests cover disable/Undo retention, same-byte relocation, blocked
encoder/preprocessing change, generic-patch bypass, wrong MAC, missing resource,
corrupt settings and backup omission. Audio/sound dependency tests refuse sound
without audio and refuse removing its parent scope. No real model is loaded.

## Browser evidence boundary

`tools/device_workflows_browser_check.py` runs against its own fictional
loopback server in sandboxed Chrome: desktop/mobile, combined/Port-only. It
enters typed controller, initial capture/provider, ONVIF, Key local-role and
Port feature forms; uses keyboard confirmation and unchecked consents; verifies
saved/Undo bytes or the explicit face-guard exception, immutable identities,
no placeholder Key, private values absent after Back, bounded layout and
collected server/browser/grants. Outbound requests to every unowned origin are
blocked. Fictional model files deliberately are not loadable models.

The browser controller-probe response is a **fixture**, not native TLS evidence;
the separate owned loopback TLS tests exercise real handshakes. Captures contain
only invented settings. Runner checks do not constitute visual review, an
independent human review, actual NAS/VPN-client acceptance or a full accessibility
certification. Qualification counts and exact-head CI are recorded in the PR.

## Retained qualification failure and narrow correction

The first full Mac run had **5,939 passed, one failed, 14 skipped** (56 subtests).
The existing real two-process FaceStore writer test failed with the fixed
`face_store_unavailable` error; its original errno was not retained. A bounded
instrumented eight-trial reproduction separately observed two `ENOENT` failures
in concurrent `O_CREAT` lock lookups on macOS, not lost templates or a provider
failure. This later trace does not retroactively supply the original errno.

The store now makes exactly one existing-only, no-follow/nonblocking lock open
after that specific creation failure and confirms the state root binding.
It never repeats creation or relaxes private owner/mode/regular-file/link-count
checks, the bounded advisory lock or directory-swap guards. A deterministic
regression failed on the old source and passes with the correction; missing,
public, symlink and FIFO locks still refuse. The combined relevant face and new
role group passed 87 cases. The failed full run remains a failure, not a PASS;
final full-suite and exact-head CI results belong in the PR.

## Not claimed

No production Admin update, processor restart, real pairing/zone/credential
change, model installation or real media request occurs in this qualification.
Native face grouping/OCR/search/alarms/ONVIF quality and hardware parity retain
their own issues. The deferred controlled Camera A test remains paused. No legal,
contributor-rights, fresh independent tester or public release signoff is added.
