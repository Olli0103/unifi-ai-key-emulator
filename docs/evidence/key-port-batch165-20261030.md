# Reviewed-source Key/four-Port follow-up — 29–30 October 2026

The Key and four existing NAS Ports now run reviewed source
`5a459230c68809835f1781d006b62030d9694059` (PR #165, merged after all four required
checks; **2,765 Linux tests passed, three skipped**). Later #166 changes only
documentation. Runtime/Dockerfile/lock/license trees match. This installs the
#163 detection JSON and #164/#165 file-loading corrections; it is not native
quality, full disaster recovery or sustained-operation qualification.

## Image and existing-model checks

Two private, separate AMD64 candidates were built from a public-source-only
archive, one CPU / 4 GiB, without state or model mounts. Archive SHA-256:
`5cfdce7b836f77eca5b717a23fddfb93b3aa66ce783223f9781369644a6f605b`.
No weights were downloaded, no public image was pushed and no new cloud or
standalone paid test was run. Ordinary already-configured camera processing
resumed after each restart; its processing counters are **not** paid-call or
native-detection counts.

| Role | Private image ID |
| --- | --- |
| Key | `sha256:7bebeb58f784e86959b53d60bbf8cbaa6fb3b6e7155712d726fbf78b9e29cc1f` |
| Port | `sha256:c0a73a23cef04d241459b4c6979feb52104532ae83ad81eb5e0a39dc91bc3726` |

All **90 installed Python modules and the project license text** matched each
source candidate. Runtime versions remained aiohttp 3.14.5, Pillow 13.0.0,
cryptography 50.0.1 and psycopg 3.3.6; Ports also use NumPy 2.5.4 / ORT 1.28.0.
The Key image has neither NumPy nor ORT.

All four Port configurations, adopted state and existing Face/Sound graphs
passed network-none, read-only preflights, without service startup or model
requests. A new separate full copy of the previously verified stopped restore
state, with a current live device-state overlay, passed the new Key constructor
and search-profile checks. Original restore evidence was unchanged; live state
was mounted read-only. This is component compatibility, not a new native full
restore. Twelve intentionally changed runtime-module hashes were compared to
the new source, not falsely required to equal the previous image.

## Serialized Key/PostgreSQL transaction

The image-only Compose proposal was compared semantically before applying.
The Key reached natural idle and stopped with exit 0. A logical dump was made
with stable table counts, then PostgreSQL stopped for complete cold copies.
Archive digests, full gzip CRC consumption and every regular member's byte count
were checked before cutover.

| Retained Key/PG artifact | Bytes |
| --- | ---: |
| Complete Key state archive | 14,291,088 |
| PostgreSQL volume archive | 140,240,317 |
| Database-secret archive | 1,559 |
| Logical index dump | 46,696,147 |

A separate selected-core constructor check used the fresh cold copy before
starting the candidate. Key and PostgreSQL were recreated **together** because
the database shares the Key network namespace. PostgreSQL image/volume stayed
unchanged. Adopted identity, core files, same effective Key management/database
password and username, declared LAN MAC and all IP/routes passed read-back;
only the independently verified unconfigured Docker bridge MAC may vary.
Table counts remained equal. Four Ports and two model sidecars stayed untouched
during this transaction; no old-state restore was performed.

## Preserved first Port refusal and current-state recovery

The first Port-1 attempt reached its new image and readiness, then failed an
added byte-equality check in the immutable-file loop. The operator restored
**the old image with current state**, not a stale credential/state snapshot.
The failed attempt, cold backup, failure, recovery and watchdog receipts remain.
Native original identities/pairings and recovered streams were verified before
any further Port admission.

The failure did not capture the exact failing loop field or intermediate new
credential record. Subsequent read-back found only the management-record digest
different: username equal, a changed SHA-512-crypt salt. **Neither this nor a
successful control connection proves the plaintext password stayed equal.**
The effective Port password comparison remains unknown and is not promoted to
a continuity claim.

The initial extra gate had treated controller-managed state as immutable. The
existing implementation accepts `UpdateUsernamePassword` only on its pinned
control path after parameter agreement, validates/persists the private record,
acknowledges it and increments an allowlisted counter. The recovered process
reported one accepted update / zero rejections. Configuration, certificate/key,
controller trust and adoption state remain immutable checks; the managed
credential record is checked separately.

A fresh transaction namespace and frozen guard were used. Its 20 synthetic
checks include 16 negative cases: malformed/extra fields, username changes,
unsafe files/symlinks, wrong counter types, rejections, unhealthy/unadopted
state, changes without an acknowledged update and old file timestamps refuse.
Changed records require valid private format, same username, healthy adopted
control, accepted native-update counters without rejection and a file timestamp
after container start. This is source/counter evidence, **not cryptographic
attestation of the plaintext or a one-to-one log of the lost intermediate
record**. No operator command wrote, restored or replaced a management secret.

## Four successful individual Port transactions

Each fresh stage used a private image-only proposal and current baseline.
Exact owned producer PID/start identities, empty pipes, stable decoded-frame
counts and settled model work were checked under a bounded recovery watchdog.
Only that Port stopped, exited 0, was completely cold-backed-up and replaced.
Other instances and the post-Key Key/PG container IDs stayed unchanged.

| Instance | Configured streams | Verified cold archive bytes | Regular files |
| --- | ---: | ---: | ---: |
| Port 1 | 3 | 7,574 | 32 |
| Port 2 | 2 | 335,560,992 | 65 |
| Port 3 | 2 | 9,828 | 61 |
| Port 4 | 2 | 9,969 | 64 |

Every stage passed physical settings, immutable config/cert/trust/adoption
hashes and native original Key/Port IDs, MACs and nine pairing strings before
the next stage. On each successful restart, one native management update and
zero rejections were observed; changed record hashes carry **unknown plaintext
equality**, not a fabricated unchanged-password result. Each Port's declared
provider-key file was independently byte-compared with its verified cold archive;
all four matched, with private type/owner/mode/size checks and no value export.
Provider targets, cost rules, camera scope and normal processing policy stayed
unchanged. The uncapped OpenAI-primary/private-local-fallback policy was retained.

## Separate final read-back and limits

A separate read-only execution by the **same agent**, not an outside human
reviewer, verified all **90 running modules on the Key and each Port**, image
identity/source, backup SHA/private modes, core/adoption and declared physical
settings, no stopped producers, four completed watchdogs, zero restarts/OOMs and
healthy nine streams/policies. A second Key check rehashed all four Key/PG
backup artifacts, proved the same effective Key credential again, and confirmed
two sidecar IDs/images/restart counts unchanged. Native read-back after the Key
and every Port still matched the original five adopted/connected processors and
nine pairings; the baseline was never rebased.

This does not establish recording playback, zone/detection ground truth, event
issuer attribution, named-face acceptance, new in-flight-model shutdown
acceptance, full native/host/controller/power-loss restore, 72-hour soak or a
seven-day pilot. No new approval/feature flags, model/index cutover, public
release or separate administration-service activation follows. The public
person pilot remains frozen at seven usable/correct of twelve with five errors;
it was not rerun or reclassified by this deployment. ONVIF remains intentionally
open because the owner has no test camera.

Private artifacts retain credentials, backups and local records. This public
record includes no camera media, native IDs, raw model replies, credential values
or biometric templates. The [retention](../data-retention.md) and
[uninstall/restore](../uninstall-restore.md) boundaries remain applicable.
