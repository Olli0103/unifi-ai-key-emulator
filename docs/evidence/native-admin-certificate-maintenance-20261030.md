# NAS administrator certificate maintenance — 30 October 2026

This is installation-specific operational evidence for #24, not broad release,
portable-installer or future renewal qualification. It follows the
[native same-certificate TLS reload](native-admin-tls-reload-20261030.md).
No private hostname, address, credential, certificate/key bytes or native device
identifier is published here.

## Existing solution and frozen local maintenance source

The installed native **Tailscale 1.104.2** command already supports explicit
certificate/key outputs and minimum validity. It remains the own-node issuer
tool; no new CA, ACME daemon, root privilege, sudo rule, proxy, DNS, firewall or
trust exception was added. The existing lingering user manager is reachable.
Only the pre-existing Key observation timer was found, not a certificate timer.

The deployment-specific wrapper and units are maintained separately in the
owner's private infrastructure repository, frozen as
`d1f41fd35b409a668c9551b94073c670d41b744f`, parent
`1718329a8142d0604220311ed1faef8066b0886f`. This is not source shipped in the
emulator wheel or a public image release. It pins the qualified administrator
image/source, exact HTTPS command/origin, read-only TLS mount and same healthy
runtime. It never reads an administrator password or adopted-device identity.

At 30 or more remaining days it performs no issuer call, HUP or staged/live
key replacement. Below that threshold it uses the existing own-node command
once with staged outputs and 720-hour minimum validity. Before publication,
in-memory strict-default-CA TLS checks verify the candidate chain, hostname,
dates and matching key; the leaf must differ and expire later. Retained private
old files and a durable journal precede individual atomic file replacements.
Only the same verified protocol-1 CLI can receive HUP; its acknowledgment,
expected HTTPS leaf and unchanged process must pass before completion.

Failed/interrupted known old/new publication can restore the still-valid old
pair and context. A later admission recovers an incomplete journal before
another issuer call. Unknown file/runtime drift or an invalid retained pair
is refused rather than overwritten. An advisory lock serializes this job only;
operators must pause it before other administrator/certificate maintenance and
update its exact image/source expectation after a qualified admin upgrade.

## Qualification

- Clean nonshallow frozen checkout, exact parent, 100644 modes, Ruff/diff checks.
- **24 Mac tests and 24 network-disabled Linux tests passed**, no skips. The
  owned Linux fixture had no production mounts and was collected; all seven
  existing containers remained unchanged.
- **72 source-derived external same-agent checks passed** without DNS, network
  or native process calls, fixture residue or tracked byte/mode changes. These
  include success/no-op, invalid candidates, uncertain reload rollback,
  interrupted mixed-file recovery, unknown-drift refusal and locking.
  This is not independent human/security review.
- The first external harness collided its case directory with a fixture named
  `wrong`; distinct case-directory prefixes fixed only the harness. Its failed
  receipt is preserved, not counted as success.
- Native NAS Python 3.11 verified the exact running image/command/mount/process,
  default-trusted hostname/key/date handshake entirely in memory and strict
  credential-free health at the inspected numeric peer. User-unit validation
  returned zero. No issuer or HUP was invoked by that preflight.

## Actual installation and observed result

Only two new private user-unit files, the wrapper/config and private journal
directory were installed after confirming those targets absent. All prior
unit files, symlinks and observation timers are unchanged. No root/system unit
was installed. The one-shot service succeeded and became inactive with a fresh
receipt: **`not_due`, 89 days remaining, issuer calls 0, HUP signals 0**.

The new user timer is enabled, active and waiting. It checks at 05:15/17:15 UTC
with up to ten minutes of jitter and persistent catch-up. The first observed
next due was 30 October 2026 at 06:22:26 CET. The service has 180-second start
and 60-second stop limits, with no automatic service retry. Those outer limits
are not a hard realtime guarantee for every filesystem operation.

All seven administrator/processor/database containers retain their original
IDs, images, process IDs, restart states and running state across installation.
Five profile files, persistent administrator authority, TLS pair and Compose
bytes are unchanged. No policy/provider/model save or media/model request was
performed. The separate administrator remains on the already qualified source;
no second image update was needed for this host-only maintenance job.

## Remaining evidence and recovery limits

The current certificate did **not** need renewal. No actual issuer request,
new-certificate production rotation, future unattended run, delivered phone
alert, expired-TLS repair, full restore or power-loss experiment is claimed.
The existing Kuma strict-TLS/identity and 21-/14-/7-day expiry observations are
separate from renewal success. Stored receipts have timestamps, not perpetual
validity. Native issuance and future renewal remain `needs_evidence`.

Two file replacements are not an atomic pair transaction. Abrupt host loss/KILL
can leave a journal requiring recovery; do not restart the admin against an
incomplete publication. This local single-owner mechanism is not a distributed
lock or protection against hostile same-owner/ancestor mutation. Mac scheduling,
browser/mobile reachability, independent testers, sustained operation and the
rest of #24 remain open. ONVIF remains intentionally open without a test camera.
