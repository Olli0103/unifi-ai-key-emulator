# Preserved AI Port policy during provider/model edits — 30 October 2026

Partial progress for #13 and #17. No native profile, provider, recognition scope,
camera identity or model was changed to test this fix. The NAS administrator
image was updated; processors and PostgreSQL were not restarted.

## Reproduced source fault and correction

The provider form kept fallback, plate-camera and held-package settings, but
dropped a previously saved `camera_thresholds` map and `score_server`. Provider
and role-model edits also started from parser-normalized data, changing accepted
camera spelling/default representation outside the requested edit. Six baseline
regressions failed; two unmanaged-field rejection controls passed.

[PR #191](https://github.com/Olli0103/unifi-ai-key-emulator/pull/191), tested head
`b83baa5047a5d229440ce16b1339f4333ec05f1a`, parent
`3b907930acfdf61a52709b9199f57d12840f9f1c`, fixes both paths. Provider preview,
apply and detection-model changes use the same captured original JSON bytes,
not another path read or parser-normalized camera IDs. Existing per-camera gates
and score routing join the previously retained non-form settings. Full runtime
validation remains mandatory; retired legacy event caps are still dropped by
provider saves. No form can inject/add/retarget the two unmanaged fields.
Static UI text explains that any existing gates and routing are kept.

## Frozen qualification

- **62 focused tests passed**, including both policy fields, provider/model
  edits, exact archived undo, accepted raw spelling, unmanaged-field refusal,
  authenticated/CSRF form save and unchanged other Port/Key profiles. The first
  HTTP fixture lacked the required listener port: 61 passed/one setup failure;
  adding its explicit fixture port fixed setup without weakening assertions.
- Native Mac: **3,124 passed, three skipped, 22 warnings, 56 subtests passed**,
  231.95 seconds.
- [Linux CI](https://github.com/Olli0103/unifi-ai-key-emulator/actions/runs/40999450372):
  **3,126 passed, one skipped, 22 warnings, 56 subtests passed**, 156.86 seconds.
  All four required jobs succeeded. Two PostgreSQL tests skipped only on Mac
  pass against CI's disposable database; missing real CLIP weights stay unproved.
- **38 source-derived external same-agent checks** pass without DNS, network,
  native processes, fixture residue or tracked byte/mode changes. Whole-payload,
  all five non-form fields and exact undo are checked independently of the new
  repository tests. A post-capture overwrite preserves the captured revision;
  an additional exact `os.replace` path-replacement control on the same frozen
  source also passes without touching the replacement file. The initial receipt
  used an in-place post-capture rewrite, not a rename; both receipts are kept.
  This is not independent human review or hostile same-inode/ancestor protection.

Merged commit `e57a8c15d32f6250e3e47788aa12498792324d77` has the entire same tree
as the tested head. The checkout was clean, nonshallow, with 100644 modes and
clean Ruff/diff checks. No Node or shell source changed.

## Native candidate and admin-only installation

A read-only inventory found **one of four** current profiles already has a
score server and none has a per-camera threshold map. The separate candidate
contains all **92 exact source modules and license**. Built with networking
disabled, it read the four actual profiles/twelve Face/Sound/Audio panels and
passed auth/CSRF/unknown-profile denials. Each profile also completed a pure
configuration preview with socket/DNS sentinels: all four API profiles, the
existing score route retained, zero transport attempts and zero applies.
The temporary server/container was collected. All seven existing containers,
five profiles and persistent administrator authority stayed unchanged.

Before replacement, the six administrator-authority/TLS/Compose inputs and a
separate copy of the certificate-maintenance config were fully read/hash-verified
on NAS and external SSD. The existing certificate timer was paused, its one-shot
confirmed inactive, and only the separate admin image replaced. Command, private
HTTPS origin, full mount descriptors, healthcheck, hardening, resources and
persistent credential remain unchanged. Old cookie refusal, same-credential
sign-in, twelve panel reads and logout passed; zero native setting changes.

The certificate config changed **only its qualified image and source pin**.
Native guards and strict-default-CA/hostname health passed against the new
runtime. Its first one-shot returned **not_due / 89 days / issuer 0 / HUP 0**;
the timer's prior enabled/active state was restored. The previous image remains.
The new administrator is healthy with zero automatic restarts/no OOM.

Five profiles, authority and TLS bytes and all six core containers retain their
original state. Fresh Protect before/after matches the original **five adopted,
connected identities and nine pairings**. Only the private-VPN listener remains
open; old loopback HTTP and NAS-LAN listeners stay closed. Prior Key/OVMS timer
states are unchanged; Key and admin have recent stored Kuma UP observations.

## Scope still unproved

This is not a real provider/model/policy save, native camera-gate calibration,
new score endpoint, model/score inference, quality/relevance pass, browser/mobile
test, independent installation, new-certificate rotation, future renewal,
endurance or full restore/power-loss result. The captured-byte test does not
fence arbitrary external writers that ignore the configuration lock. Broader
acceptance remains open; ONVIF stays intentionally open without a test camera.
