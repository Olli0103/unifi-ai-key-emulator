# Native administrator TLS-context reload — 30 October 2026

This is bounded progress for issues #3 and #24, not their closure. No private
hostname, address, credential, certificate/key bytes, device identifier or
camera content is included here.

## Frozen software qualification

[PR #188](https://github.com/Olli0103/unifi-ai-key-emulator/pull/188) tested head
`8e629925afb03f1e7f989b1afd7344721ee70aa1`, parent
`20d7e7f1204dfa9cb2fdb2df16f066812a2cab59`. It was merged as
`03042c001924cd498bd2f982a65dc4b2f511ba45`; the entire merged tree equals the
tested tree. The checkout was nonshallow, clean, and retained 100644 modes.

- Native Mac: **3,112 passed, three skipped, 22 warnings, 56 subtests passed**,
  222.17 seconds.
- [Linux CI](https://github.com/Olli0103/unifi-ai-key-emulator/actions/runs/40996712965):
  **3,114 passed, one skipped, 22 warnings, 56 subtests passed**, 125.81 seconds.
  All four required jobs succeeded: test-and-build, secret-scan,
  dependency-and-sbom and reproducible-release.
- 294 focused checks passed. Tests rotate fictional TLS pairs for SNI and
  no-SNI clients, preserve an old connection and session cookie, reject unsafe
  inputs, serialize reloads and exercise valid/refused HUP and clean TERM in
  an owned real CLI subprocess. Handshakes perform no certificate-file reads.
- A source-derived external same-agent harness passed **68 checks**: owned
  loopback TLS only, no unexpected DNS/transport, no remaining fixture,
  connection or handler, and byte/mode-identical clean tracked files. An exact
  fstat-boundary pathname replacement proves the checked open descriptors
  supply the loaded pair; a stop followed by a queued HUP cannot reload.
  This is not independent human review or a hostile ancestor/same-inode test.

The first external fixture gave two self-signed certificates the same issuer
name; OpenSSL refused the second trust path. Distinct fictional issuer names
fixed the fixture without implementation changes or weaker client checks.
An initially misspelled documentation-test filename ran no tests; the corrected
existing suite passed. The first non-isolated package build lacked setuptools
and refused before building; the isolated build passed. Those failed receipts
remain retained separately. Two PostgreSQL tests are skipped on Mac without
a scratch DSN and pass in the disposable Linux CI service. Missing real CLIP
weights remain an unproved branch; no model download/call was introduced.

## Native candidate and recoverable admin-only installation

The source wheel and installed candidate contain the same **92 modules** and
license. The candidate was built with networking disabled from the retained
previous administrator image. A separate network-disabled candidate read all
four real Port profiles and **12 Face/Sound/Audio panels**. Counts remain
3/2/2/2 for each feature, with no individual camera names available. Anonymous,
invalid-CSRF and unknown-profile requests were refused. No actual policy,
provider or model save was admitted; the owned server/container was collected.
All seven existing containers and five configuration bytes remained unchanged.

A six-member private administrator-authority/TLS/Compose recovery archive was
fully read and hash-verified on both NAS and external SSD before replacement.
Only the separate administrator image changed. Full mount descriptors,
command, healthcheck, private-VPN HTTPS binding, user/group, hardening,
resources and existing persistent authority were retained. The prior image
remains available; no previous processor state was restored. The updated admin
is healthy with zero automatic restarts and no OOM. Its pre-replacement cookie
is refused, the same persistent credential signs in, twelve panels read and
logout works. No permission/provider/model change was applied.

## Native same-certificate HUP result

Only after confirming the installed source/image, healthy process and
`admin_tls_reload_protocol: 1` marker was one HUP sent to that owned container.
The CLI acknowledged `ready`, generation 1, audit recorded. Fresh strict
default-CA, hostname-verifying connections retain the same leaf certificate
and accept the same existing session cookie. Container ID, PID, start time,
zero restart count and healthy state remain unchanged; logout then succeeds.

Five profile files, administrator authority, TLS pair and Compose bytes remain
unchanged. The six processor/database containers retain their original runtime
state. Fresh before/after Protect reads match the original **five identities
and nine pairings**, all five adopted and connected. Only the private-VPN
listener is open; former loopback HTTP and NAS-LAN listeners remain closed.
The native Kuma service/strict-TLS monitor configuration remains unchanged;
a recent stored UP observation is retained, without claiming a delivered alert.

## Not proved or changed

This reload used the **same existing certificate**, not issuer renewal or a
new-certificate production rotation. No certificate command, renewal schedule,
trust-store, DNS, firewall, Serve, processor/database restart, private footage,
identity replacement or new recognition permission was introduced. It does not
prove browser/mobile reachability, independent installations, full restore,
power-loss recovery, future renewal or 72-hour/seven-day endurance. Loading a
pair alone does not validate CA/hostname/expiry; clients still verify them.
Automatic certificate renewal and the broader issue acceptance remain open.
