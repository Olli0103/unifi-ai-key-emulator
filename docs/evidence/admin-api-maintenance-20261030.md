# Private API, host maintenance and admin-only qualification — 30 October 2026

Tracking issue: #13. Status: **OPEN**. Source [PR #201](https://github.com/Olli0103/unifi-ai-key-emulator/pull/201),
tested commit `e26deca9d866821df2758d81e1c4b45a68d4f3ca`, merge
`0268f9e722fafcec18a151847e995007c5e90bf1`; the complete merged tree matched
the tested tree. See [the API and maintenance contract](../admin-api.md).

## Implemented and source-qualified

- Existing private authenticated listener, versioned Key/Port JSON views and
  strict preview/apply/exact rollback with OCC and shared mutation admission.
  Generic patches cannot change native identities, trust, bindings or retained
  credential destinations. Admitted file operations outlive HTTP disconnects.
- Saved identity/adoption/trust remain separate from current runtime observations.
  Pinned HTTPS health and fresh validated inventory have bounded reads. Camera
  metrics join saved cameras only after the runtime identity/order digest matches.
  Missing recording/PTZ/channel evidence is not invented from configuration.
- Rollout config/registration share bounded writer locks and a retained private
  last-known-good journal. Uncertain or externally changed outcomes block replay.
- The Admin admits only host-published one-use plans, never uploaded commands,
  files or caller destinations. Host authority stays outside its mounts; inherited
  leases and a global unresolved-effect barrier prevent a second admission after
  owner loss. Queue admission is not execution or worker-liveness proof.
- Maintained search operations verify a scratch restore, not a live-index restore.
  Apple update plans retain exact images/inputs and rollback boundaries. An
  uncertain create acknowledgement must not restart an old instance beside a
  potentially existing replacement. Complete device-file export requires cold
  writers; file restore accepts only a fresh offline directory, never activation.

Exact source: **3,504 Mac tests passed, three skipped; 3,506 Linux tests passed,
one skipped; 56 subtests passed**. All four required CI jobs succeeded: test/build,
secret scan, dependency/SBOM and reproducible release. Existing warning categories
remain recorded in the full logs; they are not represented as new maintenance
failures. Ruff and diff checks passed. A separate same-agent executable passed
75 assertions, checked all 421 tracked file byte/mode pairs, made four owned
loopback connections and no unowned connection attempts, and collected its
fixtures. This is not independent human review or a child-process network sandbox.

Preserved regressions include four rollout rollback/registration failures, the
uncertain Apple-create refusal and TAR extension headers bypassing the public
`frombuf` override on a maintained Python version. Initial process-exit fixtures
also exposed Darwin's missing `waitid` and zombie-only process-group `EPERM`;
the qualified implementation uses its native non-reaping exit observation and
requires pipe EOF before treating a completed group as settled. A detached
process with missing EOF remains **uncertain**, not falsely contained or killed.

## Native Linux synthetic file-scale qualification

Read-only metadata inventory found roughly 125,000 entries in the actual Key
profile, exceeding the first 20,000-entry implementation limit. The corrected
bounded snapshot supports up to 200,000 combined file/directory entries, 1 GiB
file content and a 64-MiB manifest, without TAR member caching or quadratic
directory lookup. Capacity refusal is explicit; nothing is silently omitted.

A separate NAS run exercised the frozen host worker, one-use bridge, full export,
fresh offline restore and complete backend read-back using **125,000 generated
markers / 125,007 archived files** and synthetic factory material only:

| Observation | Result |
| --- | --- |
| Archive bytes | 151,910,400 |
| Owned export time | 9.760 s |
| Owned restore time | 56.294 s |
| Total fixture construction/qualification time | 118.608 s |
| Maximum observed child RSS | 304,656,384 bytes |
| All seven existing containers | Unchanged |
| Model calls / native container mutations / active profile edits | 0 / 0 / 0 |
| Admitted jobs / owned synthetic fixture root | Terminal / removed |

This stdlib-only host-worker probe used NAS Python 3.11.2; it does not extend
the packaged application's Python requirement. The fixture used a synthetic
read-only engine inventory, not production container controls. It is not a real
device/database restore, power-loss drill, encryption claim or full-installation
disaster-recovery acceptance. No real media, biometric templates or credentials
were placed in this fixture or public evidence.

## Native admin-only activation

The clean-source wheel and a network-none AMD64 candidate contained all **109**
expected source modules plus the byte-identical project license. Candidate image
`sha256:9ad800e39c4064698f6defb13835f907dbda41ba3d4cd8742a0b4d7b9273d18d`
was qualified against read-only mounts of the actual Key and four Port profiles.

The isolated candidate read five JSON configurations/identities and their saved
adoptions, completed five no-op JSON previews, refused bad-CSRF applies with all
five config files byte-identical, read 25 model-registry rows and 12 permission
panels, and retained the shared 60-admission/next-request-429 boundary. No apply,
model request, permission grant or processor restart occurred. Its own server
and temporary container were collected; this is HTTP acceptance, not visual or
mobile browser acceptance.

Before replacement, the six-member admin-authority/TLS/Compose recovery archive
was fully read back, SHA-256 verified and copied privately to the external SSD;
the existing certificate-maintenance config was separately retained and checked.
Only the Admin image changed. The existing certificate timer was fenced while
its worker was idle; after qualification only its image/source pins changed.
Its first new-pin run returned `not_due`, zero issuer calls and zero reloads,
then its previous active/enabled state was restored.

Actual authenticated HTTPS JSON reads verified all five profiles and persisted
adoptions using normal CA/SNI validation and secure host-only session cookies.
Signed-out and logged-out requests returned 401. The five config files,
authority/TLS bytes, hardening, listener, mount descriptors and healthcheck were
retained. All six Key/PostgreSQL/Port containers were unchanged. A fresh native
Protect read retained one Key, four Ports, nine pairings and five adopted/connected
processors against the **fresh pre-201 baseline**. The older lost browser-tab
baseline was not recovered or rebased. Key/Admin monitors were UP with heartbeat
ages below 120 seconds at the final read; this is not an endurance measurement.

## Explicit remaining scope

The native Admin has no published maintenance bridge or configured runtime peer
yet: its API correctly reports those as unconfigured, not available operations,
execution or offline devices. Its inventory source is not configured; the route
returns 503 rather than an empty successful list. Persistent native recording,
PTZ and channel-status access remains missing: the existing integration key
returns camera inventory/detail but not those fields. No account, password or
permission was created to work around that gap; access has been requested.

No new Key/Port runtime, live restore, database replacement, provider/cost/camera
scope, standalone inference or public release was activated. Native playback,
quality, in-flight recovery, visual/mobile UX and the independent installations
remain separate gates. ONVIF remains deliberately open without a test camera.
Tests and this admin-only deployment do not complete all of #13.
