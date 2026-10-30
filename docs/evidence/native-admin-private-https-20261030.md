# Native private-VPN administration HTTPS — 30 October 2026

This supersedes the transport state, not the limitations, of the earlier
[loopback checkpoint](native-admin-loopback-20261030.md) and
[Port-permission readback](aiport-permission-restrictions-20261030.md).
Only the separate NAS administrator service changed. Its qualified image still
contains source `da2b27515122ae395afa2f3e517bf788532f4594`; the six existing
processor/database containers remain on their earlier qualified images.

## Certificate and listener

The existing NAS Tailscale client issued one certificate for its own official
node DNS name. Certificate/key public components match, the SAN names that node,
the four-certificate chain passed the Mac's native SSL hostname/trust check
without an installed root or trust override, and the leaf expires on
**28 January 2027 at 00:29:54 UTC**. The private key and chain are current-user
owned, mode 0600, in a mode-0700 directory. The private endpoint and key bytes
are deliberately absent from this public evidence.

The administrator now binds directly to its existing private VPN IPv4 interface
on port 8765, using the source listener's explicit certificate, key and HTTPS
origin options. No reverse proxy, forwarded-header trust, new DNS record,
firewall/ACL/Serve policy, Gateway configuration, system root or scheduler was
changed. The old loopback HTTP endpoint and the NAS LAN address do not accept
connections on that port. The previous administrator image remains available.

The original internal Caddy root did **not** pass this Mac's existing trust
check. Its private key was not read, copied or used; no new root was installed.
The later default-trusted node certificate is a different, narrower path, not a
claim that the Caddy trust problem was repaired.

## Actual HTTPS proof

The NAS-side client connected to the configured numeric VPN peer, with default
CA verification and SNI/hostname verification for the official node DNS name.
It did not disable TLS verification or claim a DNS/browser test. It verified:

- unauthenticated redirect, sign-in with unchanged persistent authority,
  five authenticated profiles and eight saved Face/Sound permission panels;
- a Secure, HttpOnly, SameSite=Strict, Path=/, no-Domain `__Host-` session cookie;
- wrong Host and bad CSRF refusal, logout, then refusal of protected reads;
- a healthy strict-TLS container health check, zero automatic restarts/no OOM,
  unchanged administrator image, five configuration bytes, three authority
  files and the six processor/database container identities/images/restarts.

No valid permission/provider/model operation was admitted. TLS material and
administrator authority were byte-verified in private NAS and separate SSD
recovery archives before the change. The read-only final transport check again
found the correct image healthy, the VPN socket reachable, old loopback/LAN
ports closed and the previous image retained. A fresh Protect read **after**
the HTTPS change matched the original one-Key/four-Port identities, nine
pairings and five adopted/connected processors. There was no new pre-change
Protect capture for this transport step; it is not represented as a paired
before/after observation.

## Preserved failed attempt and remaining acceptance

The first transport attempt passed TLS readiness, then the helper incorrectly
expected the login page not to contain the shared main heading. It stopped
before submitting the password and restored only the administrator's prior
HTTP compose/transport. Five profiles and six core containers remained
unchanged. The second helper corrected that heading expectation, retained the
authentication assertions and completed the proof above. This was a helper
failure, not an observed source TLS/authentication failure. Earlier loopback
candidate failures remain recorded in their original evidence.

The executing Mac has no Tailscale client/private node DNS resolution, and no
paired controllable client was connected at this checkpoint. Consequently this
is **not** a remote-client connection, real browser/mobile/keyboard/screen-reader
acceptance, completed owner password-manager handoff or complete native apply/
rollback workflow. Do not infer controller/model compatibility or quality.

Certificate renewal, expired-certificate recovery and independent monitoring
are **not yet qualified**. The validity date is a renewal deadline, not proof of
unattended maintenance. No host reboot, power-loss/full restore, endurance or
independent installation/review result is supplied. The broad issues stay open.
