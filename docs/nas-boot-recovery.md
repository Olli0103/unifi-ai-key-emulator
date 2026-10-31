# NAS boot ordering and recovery

Use this optional host integration only after verifying that `/home` is a
separate mandatory mount and that the selected lingering user's units live
there. It does not apply to a host with an ordinary root-filesystem home.
It is not enabled by an application image update.

## The failure this addresses

A user manager can reach its default and timers targets before NAS storage
mounts `/home`. It then has no user timer definitions to load. Later,
`systemctl --user is-enabled` can say **enabled** because the symlinks now exist,
while `systemctl --user show timers.target -p Wants` is still empty and the
timers remain inactive. Lingering alone does not establish correct boot order.

The [31 October observation](evidence/nas-kernel-panic-recovery-20261031.md)
records that exact sequence. A daemon reload discovered the three existing
timers; explicitly starting those same timers recovered current operation.
No schedule or application setting changed.

## Optional next-boot guard

The reviewed fragment is [user-home-ready.conf](../deploy/systemd/user-home-ready.conf).
It uses the existing GNU `timeout`, util-linux `mountpoint` and POSIX shell,
not another polling service, application dependency or recurring job.

Before installation, inspect the selected `user@<uid>.service`, all its current
drop-ins, the mount, and the user's actual configuration directory. Confirm the
four absolute executables in the fragment exist. Do not assume that a vendor's
dynamically mounted `/home` has a mount-unit file that can be pulled in using
`RequiresMountsFor`; the observed host had no fragment for `home.mount`.

An administrator can install the fragment as a **new** named file under
`/etc/systemd/system/user@<uid>.service.d/`, preserving existing configuration.
Use the actual numeric UID, not the uninstantiated template: other accounts,
root access and recovery sessions must not acquire this dependency. Refuse an
existing different file rather than overwriting it. Validate the merged unit
with the host's `systemd-analyze verify` before a root `systemctl daemon-reload`.
No current user-manager restart is needed or recommended.

The pre-start check waits at most 300 seconds for the actual `/home` mount,
then lets the normal user manager start and discover its own units. TERM is
followed by a five-second kill bound; the enclosing start budget is 330 seconds.
If storage never mounts, startup fails: it must not claim that the timers are
running. The check does not mount storage, read credentials, start Docker,
change a network interface, log in to Tailscale, enable a timer or run a model.

This is a **next-boot** guard, not proof of a successful later boot. Do not
reboot merely to make a test counter green. At the next authorized or natural
boot, compare the mount and user-manager start timestamps, loaded target wants,
each timer's active state, and the downstream application observations.

### Recovering the current user manager

First verify the real home mount and read the enabled symlinks/unit contents.
Record current states. A user `systemctl --user daemon-reload` then reloads
definitions without changing their files. Re-read `timers.target` dependencies
before explicitly starting only the previously enabled project timers.
Preserve their original schedules and guards. A successful timer start is not
proof that its service or downstream monitor succeeded; inspect those separately.

For this deployment, those existing timers were `ovms-watchdog.timer`,
`aikey-monitor.timer` and `aikey-admin-cert-renewal.timer`. Do not use a wildcard
start, enable unrelated units, replay a maintenance plan or clear an unresolved
model-work fence to force recovery.

## Separate private-network dependency

An administrator bound to an explicit VPN address cannot start if the existing
VPN daemon failed. Keep that explicit bind and verified HTTPS; do not switch to
a wildcard, plain HTTP or a different address as a workaround. Diagnose and
recover the existing VPN service through its supported administrator access.
The home-order fragment neither repairs nor changes that service.

After access returns, qualify the actual authenticated HTTPS API, startup
provenance, camera inventory and monitor observations. Processor adoption and
ongoing camera recording are independent observations; neither proves that the
administration page or every inference backend is healthy.

## Removing the guard

Retain the installation record and exact fragment bytes. A root operator can
remove only that named drop-in using their normal recoverable configuration
process, then reload systemd. Do not delete the drop-in directory, reset the
vendor template, restart the active user manager or alter stored application
identities. Removal affects subsequent starts, not a storage outage already in
progress.
