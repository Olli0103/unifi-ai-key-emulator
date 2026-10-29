# Key and Port reviewed-source deployment — 29 October 2026

This follow-up deploys source `9df78964480b3228e6724af4ce5d1b3e870d7e0e`
(through #159), after the earlier [Key](nas-source-upgrade-20261029.md) and
[four-Port](aiport-source-upgrade-20261029.md) transactions. It is a bounded
deployment/continuity check, not recognition-quality or sustained-operation
acceptance. The separate administration process is not upgraded merely by
replacing device-service images.

## Separate candidates and preflight

Both Linux AMD64 images were built from one public-only source archive,
SHA-256 `1572b0b2098e1dc28584194e8d3154c80cde9b1b7e3909bbe87e76c9a84efc27`:

| Service | Candidate image SHA-256 |
| --- | --- |
| Key | `a8a3ef677ba83660890a7c93e34c36544534fb9226e6cc85cfe7e0aefedbb47f` |
| Port | `13c45b5508a3a16412c280b181bb8ae29f5e0f7d14aaac3c99d9c3c591b38972` |

Build limits were one CPU and 4 GiB. Neither context contained private state,
media or weights. No model weights were downloaded. Installed source-module
digests and the actual installed project-license file matched the archive.
The Key image does not need NumPy or ONNX Runtime; the Port image uses the
existing CPU ONNX graphs without RF-DETR/Torch.

All four Port configurations passed network-less/read-only constructor,
credential/API-factory and existing face/sound graph-load checks. No service,
provider request, enrollment or configuration write was part of those checks.
Graph loading does not prove recognition quality.

The first Key preflight refused **before application construction** because a
completed offline restore's salted credential record differed from current
live state. Both records were independently verified against the **same**
stored database password and username; other core/identity fields matched.
The controller can reapply the same password and generate a fresh salt/hash.
That byte difference is not evidence of secret replacement. The failed receipt
and original completed restore were retained unchanged.

A separate fresh copy of the completed restore, with an optimistic-concurrency
checked current device-state overlay, then passed the network-less Key
constructor/search-profile check against read-only live core state. Eleven
native Key runtime modules matched the previous running image byte for byte;
this is not whole-image equivalence. No background service or model request
started during that check.

## Key transaction

A fresh proposal proved that only the Key image changes. Key and PostgreSQL
were recreated **together**, because PostgreSQL shares the Key network
namespace. PostgreSQL's image, persistent volume and secret mounts were kept.
The two project sidecars and all four Ports were left running and unchanged.

The Key reached an empty active/queued/pending window and exited cleanly.
Before replacement, the operator made a logical database dump and cold
archives of the complete Key state, PostgreSQL volume and database-secret
files. Every regular archive member was read, gzip CRC verified and archive
SHA-256 recorded. Links were refused. A constructor check using selected cold
core/journal files also passed; that is not another full-state restore drill.
The earlier [full offline component restore](nas-full-state-restore-20261029.md)
remains separate evidence.

After replacement, adoption, control connection, search connection and fresh
camera inventory were true. Database table counts matched the cold baseline.
Configuration, certificates, keys, search profile and effective adoption
credentials were unchanged. The transaction observed no salt refresh.
No old state snapshot was restored. The failure path retains the old image
with **current** state/database/credentials rather than overwriting them with
an older backup; it was not exercised in this transaction.

Address checks retain configured LAN MACs and all checked IP/route fields.
Only `aikey_backend.MacAddress` may vary after proving that its driver is
bridge and it has no declared MAC pin. No general MAC/IP exception applies.
Mounts, UID, entrypoint/command, capabilities, read-only root, restart and
resource/security settings were compared against the fresh baseline.

## Port transaction and final read-back

Port results are recorded only after each selected service's cold backup,
image-only change, application readiness and original native identity/pairing
comparison. The other three Ports and Key/PostgreSQL stay running. The old
Port image predates #155, so this operator still uses the previously tested
exact-PID/start-time producer pause, drain and bounded resume watchdog before
stopping it. Installing #155 is not itself a native in-flight-shutdown test.

The existing OpenAI primary, private-IP fallback, cost rules, camera scope and
permissions are preserved. No standalone paid/provider test is performed;
ordinary already-authorized existing-camera processing continues.

All four selected Ports exited cleanly and completed their individual upgrade
without rollback. Each was checked against the **original** native baseline
before admitting the next slot. A separate final read-back checked eight
installed source modules per Port and sixteen installed Key modules against
the public archive, exact candidate image IDs, immutable core hashes,
mounts/physical settings and retained backup hashes/mode 0600.

| Backup | Compressed bytes | Regular files | Uncompressed file bytes |
| --- | ---: | ---: | ---: |
| Key state | 14,285,600 | 121,693 | 26,958,804 |
| PostgreSQL volume | 140,167,396 | 1,343 | 287,642,865 |
| Database secret files | 1,559 | 7 | 2,091 |
| Port 1 | 7,577 | 32 | 22,255 |
| Port 2 | 335,560,925 | 65 | 366,316,847 |
| Port 3 | 9,837 | 61 | 29,631 |
| Port 4 | 9,962 | 64 | 31,353 |

The separate logical dump is 46,624,117 bytes. Its digest was rechecked; this
transaction did not restore that new dump. All four Ports were adopted and
control-connected, with all nine assigned streams active, policies enabled,
successful processing and no reported failed camera, model-load or API failure.
No current producer was paused. No OOM, restart loop or container restart was
observed; all four bounded watchdogs had finished. Key and PostgreSQL stayed
running with the same post-Key-upgrade IDs throughout the four Port changes.

Authenticated Protect 7.3.74 bootstrap after the Key and **each** Port change
matched the original in-browser identity/pairing baseline: one Key, four Ports,
nine pairing strings, all five adopted and connected, no replacement native
identity. This uses counts/booleans only outside the private browser.

The follow-up read-back is by the same agent, separated from the mutation
driver; it is not independent human/external security review. This round did
not execute a rollback. The earlier separately recorded rollback remains the
only native old-image/current-state rollback claim.

## Limits

Processing counters include motion-gated empty results; they are not counts
of paid requests, detections or proven model calls. Connected/adopted and
recording flags do not establish playback, native event issuer attribution,
inside/outside-zone ground truth, recognition quality or alarm operation.
No real model/index migration, full native controller restore, host reboot,
72-hour soak, seven-day pilot or public image/release publication is proved.
ONVIF stays open by owner instruction because no test camera is available.
Private IDs, credentials, recordings, templates and raw receipts are not
published; the original native identity baseline stays in the authenticated
browser and is not rebased after a deployment.
