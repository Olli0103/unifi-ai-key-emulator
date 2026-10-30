# Four-Port transport activation — 30 October 2026

## Scope and source binding

The existing four NAS AI Ports were individually upgraded to reviewed source
`901ea18675d2d49908242f930704e6b073b7b22a` ([#193](https://github.com/Olli0103/unifi-ai-key-emulator/pull/193)).
The merged tree at `168392a5bb572cb5b8c8f6a80bc7996e33763ee4` is identical to that tested tree.
This installs the strict outer provider/score JSON decoder in the actual Port
processors; updating the separate administration image alone would not do so.

Exact-head qualification: 103 focused tests; 3,155 Mac tests passed with three
skips; 3,157 Linux tests passed with one CLIP skip. All four required CI jobs
passed. A separate same-agent harness passed 119 checks including 30 owned
loopback HTTP requests. The old-source regression had 24 failures and six valid
controls. These are synthetic transport checks, not an observed malicious reply,
a diagnosis of the frozen public quality pilot, or independent human review.

The private image was built without network access from the retained previous
Port image. All 92 installed Python modules and the license matched the frozen
source/package manifest, both before deployment and inside every final running
Port. The image is not published as a release. The existing Key/PostgreSQL and
HTTPS administration images were deliberately not changed.

## Admission and serialized lifecycle

Before any stop, four isolated network-disabled, read-only candidates validated
the actual existing profiles, adopted state, readable management credentials,
TLS pairs, detector construction and existing Face/Sound model graphs. No
candidate service, provider request or new model download was started.

Four staged Compose proposals were compared as complete effective configurations:
each changes only the selected service image. The original producer helper and
management-credential guard were hash-verified before reuse. The existing old
image remains available for a return using **current** state, not a stale state
restore. A fresh original-controller identity/pairing check passed before the
first stage, after each stage and at the terminal read-back.

For each Port, only its owned frame producers were quiesced under a bounded
owner-bound recovery watchdog. Empty decoder pipes, settled in-flight accounting
and stable frames were checked before a graceful stop. Its complete private
state was cold-archived, every archive member read, gzip CRC and digest verified,
and immutable configuration/identity/trust/adoption bytes rechecked. Only that
Port was recreated, without pulling and without restarting dependencies.

All four stages completed on their first attempt; no image rollback or old-state
restore was used in this batch. The earlier batch-165 failed attempt and recovery
remain documented separately and are not erased by this successful batch.

## Terminal observations

| Observation | Result |
| --- | --- |
| Updated Port instances | 4/4 |
| Running source modules and license | 92 modules plus license match on each Port |
| Configured/active cameras | 3 + 2 + 2 + 2 = 9 |
| Native identity/pairing continuity | Original one Key, four Ports and nine pairings; five adopted/connected |
| Pool state | Every camera processed; all streams/policies active; no failed cameras or model-load failure |
| Read-back API failure counters | Zero on each new Port at the recorded observation |
| Producers/restarts | No paused owned producers, OOMs or automatic restarts |
| Provider credentials | Four referenced key files byte-identical to their verified cold archives |
| Physical/runtime configuration | Mounts, environment, user, hardening, declared addresses and commands preserved |
| Unaffected processes | Key, PostgreSQL and HTTPS admin retain container IDs, images and PIDs |
| Key/database coupling | Existing PostgreSQL container network namespace retained |
| Existing monitoring | Key and admin Kuma monitors UP; fresh observations within one minute |
| Certificate maintenance | Existing timer active; unchanged matching admin image/source pin; oneshot idle |

Docker's **unconfigured, generated bridge MAC** is the sole normalized runtime
address field, not an adopted identity. Declared/LAN addresses and native device
identities were compared exactly in private.

All four Port management records changed with acknowledged controller-origin
updates after startup. Private-file/type/owner/mtime and native counters passed
the existing guard; no operator credential replacement occurred. **Effective
Port password equality remains unknown.** This is separate from the four
provider keys, which were byte-compared unchanged.

Each complete cold archive was additionally copied to the external SSD and
independently reread there, including all file bytes, CRC and digest. Private
archives, configuration, credentials, camera identities and media are not part
of this public evidence. Backup-byte verification is not a full restore or
power-loss drill.

## Limits and remaining acceptance

Normal processing resumed for already approved cameras with their existing
providers, permissions, keys and cost policies. No standalone paid/model probe,
new camera scope, enrollment, model retuning, provider/budget save, controller
restart, database migration, certificate issuance, or public image publication
was introduced. Observed successful requests are not billing or quality proof.

This is source-bound same-agent deployment evidence. It does not establish
native malformed-provider injection, in-flight shutdown under a live model,
recording playback/event attribution, zone/recognition ground truth, image-search
relevance, sustained operation, Mac host qualification, independent human/browser
acceptance, future certificate renewal or full recovery. Related broad issues
remain open; ONVIF #28 is deliberately open because no test camera is available.
