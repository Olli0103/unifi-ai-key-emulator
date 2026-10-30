# Separate NAS loopback administration — 30 October 2026

## Observed result

A separate administration container now runs reviewed merged source
`8bb64ef0a4f1a6b8f0ffd213c46f01f183058f72`. All 92 installed Python modules and
the installed project license matched the reviewed package. The image was built
and checked without a network, retaining the existing pinned dependency image;
no public image or binary release was made. The merged-main CI passed all four
required jobs: **3,013 tests passed, three skipped**. The same functional archive
change passed the native Mac suite and a separate same-agent 46-assertion gate.
This is not independent human security review.

The listener is **HTTP on NAS host loopback only**, port 8765. Kernel socket
inspection confirmed only IPv4 loopback; a credential-free connection to the NAS
LAN address on that port was refused. No DNS, Caddy, firewall, Gateway or scheduler
change was made. This is not a LAN-accessible HTTPS site or a browser E2E result;
`127.0.0.1` here refers to the NAS, not another computer's browser.

## Authentication and actual profiles

The new service reads the actual mounted Key and four Port profiles, not old Mac
copies. An authenticated HTTP client displayed all five profiles, five provider
forms and the separate speech form. Six new-key inputs were empty/write-only.
Five authenticated maintenance reads returned HTTP 200:

| Page | Observed response time |
| --- | ---: |
| Data flow and retention | 139 ms |
| Audit | 1 ms |
| Caption preflight | 10 ms |
| Backup preflight | 2 ms |
| Index-migration status | 1 ms |

These are one-run HTTP timings, not latency percentiles or sustained-operation
targets. Retention explicitly displayed incomplete archive counts as unknown.
The read-only pages did not perform a backup, restore, migration, index cutover,
provider test, model activation or configuration apply. A rejected-CSRF provider
POST was refused before apply; wrong Host/Origin and absent-session reads were
also refused. Logout revoked the authorized session.

A new, separate administrator was initialized once; no fixture credential or
existing operator/device/provider credential was reused or replaced. The three
new authority/bootstrap files were private and byte-verified in a private NAS
recovery archive and a separate private recovery copy. No credential value,
cookie or signing-key material is in these artifacts. This is neither encryption
proof nor a completed owner password-manager handoff; the private bootstrap file
has not been represented as deleted or stored in a Secret Store.

## Restart and native continuity

Exactly one graceful restart of the new admin container was tested. Its image
and container identity were retained; the old session cookie was refused after
restart. The same persistent administrator credential allowed a fresh sign-in
and another five-profile read. The proof signed out afterwards. A later read-back
reported the admin container **healthy**, running, with no OOM or automatic
restart. This is not a host reboot, power-loss or restored-state test.

Across installation and that restart, all five profile bytes, the three new
administrator authority files and six existing processor/database container
identities/images/restart states were unchanged. No processor restart was used.
Fresh Protect reads before/after retained the **original one-Key/four-Port
identities, nine pairings and five adopted/connected processors**. The processors
remain on their previously qualified batch-165 runtime; only the separate admin
uses this newer source. There was no new camera permission, enrollment, standalone
model request or credential replacement. Existing camera processing continued;
this does not claim that its normal model usage or charges were zero.

## Preserved failures and limits

Three temporary netless HTTP candidates preceded the managed service. The first
authenticated and displayed all profiles, then failed a helper expectation of
five key inputs: the separate speech form makes six. The second used the corrected
expectation but exceeded a 12-second read limit and eight-second clean-shutdown
limit; its owned process was killed/collected. The original failing page was not
retained. A bounded metadata check then counted 20,000 archive entries in the
first 43 shards, not a complete archive total. Source inspection and a synthetic
regression independently found the unbounded retention scan. See the
[bounded scan change and its limitations](retention-scan-budget-20261030.md).

The third candidate used the bounded scan, retained per-page diagnostics and
passed all five reads, auth refusals, logout and clean shutdown with all profile
mounts read-only and no network. The later managed-service success is not proof
that the scan was the sole cause of the earlier timeout. No archive was deleted
or truncated to improve the result.

The managed service has a read-only root filesystem, no added capabilities,
no-new-privileges, a non-root runtime user, resource/process limits, bounded logs
and its own container health check. Its profile mounts permit future explicitly
authorized settings saves; these checks performed no admitted save. Independent
monitoring, LAN TLS/renewal, browser/mobile/accessibility acceptance, real apply/
rollback, full restore, host reboot and sustained-operation qualification remain
open. Do not turn this narrow deployment result into completion of #3, #13, #17,
#24 or #25. Private runtime locators/recovery material are intentionally omitted.
