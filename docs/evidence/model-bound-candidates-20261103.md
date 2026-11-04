# Coordinated model-bound candidates — qualified, not installed (3 November 2026)

Existing-installation candidate evidence toward #20, #17 and #24. No production
activation was attempted. The broader issues remain OPEN; this record does not
complete native grouping, enrollment, quality, recovery or release acceptance.

## Why these three roles must agree

The [model-space contract](face-model-space-20261103.md),
[explicit enrollment flow](face-enrollment-control-20261103.md) and
[CLI shared model lease](face-cli-model-fence-20261103.md) are now integrated.
Their frozen [#266](https://github.com/Olli0103/unifi-protect-local-ai/pull/266)
source is `7320b5c1dbd6251bfc25d3dcdad7bd6b2fc2531e`, merged as
`51347cf96320bcfb4de34b8f528c52bec1851c0d`. Exact-head Linux CI passed 5,370 tests,
one optional skip and all five actual workflow jobs. CodeRabbit skipped review;
it is not an independent review or a sixth job.

New Face responses carry an encoder-space fingerprint; Key, CLI and Admin readers
must understand it and the version 2 private store. Installing only the new Admin
photo flow against the old readers/server would not qualify the combined path.
The earlier [Face262 candidate](face262-candidate-20261103.md) lacks these later
changes. Its completed, zero-effect activation attempts were not replayed.

## Exact candidate images

A locally built application wheel contains all 136 runtime modules and LICENSE,
byte-equal to the qualified Git source. Wheel SHA-256:
`c27a04eaff8c999e8a3a532dbba27682f908ab704fc09b4811215c7413e54e7b`.
The maintained release-content verifier passed. This is a candidate artifact,
not a published, signed or independently reproduced release.

| Role | Qualified image | Production replacement |
|---|---|---|
| Face | `sha256:94edf1521d6686bb20e95b47302442780d82f50436494b6f63aa45665cdad93b` | None |
| Key | `sha256:8485094e3860a03de682af8bf9ff0e7d1e2848c8645c418c030570be8cadd105` | None |
| Admin | `sha256:7c8e6326f82382e35423aca1f29183591aa98cda35dd2e78ea67b3b08eb66a6a` | None |

Each final build used an existing local base tag after verifying its exact image
ID, with no pull and network-none build steps. Only the application wheel changes
in Key/Admin; Face also installs the two previously official-hash/size/non-yanked/
CPython/architecture/glibc-qualified aiohttp 3.14.5 and Pillow 13 wheels. Existing
Face NumPy 2.5.3, headless OpenCV 5.0.0.93 and cryptography 50.0.1 remain exact. No
model weights were downloaded. Base image User/entry/command/environment/volumes/
working directory/health configuration remain exact, including absent optional
Cmd fields. Face's image default user and existing runtime override are distinct
and both retained; qualification uses the real existing non-root UID/GID.

## Isolated runtime qualification

Each owned test container was network-none, read-only, capability-dropped,
no-new-privileges and resource-bounded. No real profile was mounted. Only Face
received the existing generic YuNet/SFace graphs read-only; it loaded them and
derived the actual encoder-space fingerprint **without image inference**. Key
and Admin consumed that same qualified fingerprint with fictional encoder doubles.

Actual installed module/LICENSE bytes and core/optional versions passed in every
candidate, rather than being inferred from image labels. Checks included:

- All roles: fictional private-store model binding and CLI/shared-lease behavior.
- Face: owned loopback HTTP namespace response, malformed/duplicate input refusal.
- Key: actual per-response worker callback construction and mismatched-space
  refusal with fictional media/encoders, not real controller callbacks.
- Admin: actual authenticated consent, preview, separate save, discard and
  mismatched-space refusal, using only invented JPEGs/vectors. Fixture
  configuration bytes stayed exact; no camera permission was granted.

Every owned qualification container and fictional resource was collected.
Terminal readback verified all ten original production process IDs, images,
PIDs, start times and restart counts stayed exact, generic model bytes stayed
exact, and the local identity file was absent by metadata-only inspection.
Zero real model inferences, biometric record reads, real profile mounts or
production service replacements occurred. The three candidates are **not running
as production services**. These are same-agent qualification results, not
independent human acceptance or biometric quality measurements.

## Preserved preparation failures and bounded corrections

1. The original staging-only entry rejected an inherited world-writable parent
   before creating a candidate root. A read-only reconciliation confirmed the
   root absent. Existing permissions were not changed. A separate location under
   a verified root-owned sticky directory was used, with a private owned root,
   complete file/hash readback and the final success receipt written last.
2. The first Face build used BuildKit, which interpreted a naked sha256 reference
   as a registry image name and failed source-metadata resolution. All candidate
   tags were absent and all ten processes unchanged in separate reconciliation.
   The failed attempt and log remain. A versioned build actor used verified
   existing local tags and the already-qualified legacy no-pull builder, with
   separate contexts/logs/receipts. The successful Face image was not rebuilt.
3. The first qualification guard did not recognize this Docker daemon's exact
   missing-container message prefix. No qualification-start marker or test
   container existed. Fifteen exact-message/exit-code positive and refusal cases
   qualified a separately versioned correction: only known complete absence
   messages with exit 1 are accepted, never permission/connection/unknown errors.
   The same built image then passed; the original helper failure is retained.

The original staged public-wheel/helper contract remains checked. Corrections do
not remove production exclusion, fairness, no-waiter or identity-migration guards.
No live operator, scheduler, camera policy, credential or existing permission was
changed. There is no activation command in this candidate-only preparation.

## Public record and remaining work

The [allowlisted summary](model-bound-candidates-20261103/summary.json) contains
three build/qualification projections plus terminal facts; its
[manifest](model-bound-candidates-20261103/manifest.json) binds public bytes.
Raw private receipts, host paths/addresses, device/camera/identity identifiers,
Compose/environment, credentials, model files, images and templates are excluded.
Hash/structure consistency cannot independently authenticate the observation.

A coordinated upgrade still needs its own fresh private backup and guarded
multi-role forward/rollback contract, actual service-idle evidence and a newly
admitted maintenance window. This record does not exercise those actuators or
permit replaying a terminal attempt, draining consumers or forcing a window.
Native named recognition, Protect identity grouping, consented representative
quality, restored production identity/index association, cold recovery, reboot,
sustained operation and independently tested release remain unproved.
