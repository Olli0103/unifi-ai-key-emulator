# Offline browser preparation of one fresh AI Port

`local-aikey-control setup-port` prepares private **local files** for one new
passive Port from a server-selected, reviewed deployment plan. It reuses the
existing provisioning library and administrator listener/session/CSRF boundary.
It does not start a container/device, contact Protect or a provider, adopt, pair
cameras, reserve an address or enable inference. No model, audio, face/plate or
camera policy is created. It is not the independent full-install/native-result
acceptance for #17/#25 or an ONVIF implementation.

## Server operator inputs

Use an existing private administrator authority, as described in the
[control-site guide](control-site.md). Do not initialize/reset an existing
administrator or device identity just to use this mode. Prepare/review an
[addressed Port plan](evidence/ai-port-firmware-contract.md), an existing
owner-only public controller-certificate file and its independently verified
SHA-256 fingerprint. This is not a private-key upload or an Integration-key
import. Plan and certificate files must be private, same-owner, regular and
bounded; links, nonfinite/duplicate plan JSON, unaddressed/unsupported selections
and ONVIF slots are refused.

The intended new directory must not exist, including as an empty directory,
file or link. Its parent must already be private and owned by the server user.
The new child is made private and its actual type/owner/mode verified **before**
private identity material is created; a host's inherited ACL/mode behavior is
not assumed from a mkdir request alone. Existing installations use their current
control site and reviewed deployment workflow, not this wizard.

Example with fictional private paths and an already reviewed slot:

```sh
local-aikey-control setup-port --state-dir /private/admin --directory /private/ports/new-slot-1 --plan /private/reviewed-addressed-plan.json --slot 1 --controller-cert-file /private/verified-controller.pem
```

The default is loopback HTTP. The existing explicit private/VPN bind, exact
origin and direct-TLS arguments remain available; a LAN bind does not silently
permit plaintext administration. The CLI never takes a password/key value on
the command line. Use the already configured administrator password to sign in.
No settings are discovered from another service or browser account.

## Review and prepare

The authenticated page shows the selected slot, planned device address/count
and new local directory. The plan's camera entries are **not** permissions and
are not copied into the passive configuration. The wizard cannot detect existing
LAN address use, reserve it or move an already-adopted Port. Do not use it to
replace an adopted identity. Keep paths/mounts in the eventual runtime namespace;
it does not translate host paths for a container.

Enter the controller's private IPv4 address and independently verified public
certificate fingerprint. Confirm the named trust input and fresh, local-only
preparation. The fingerprint is checked against the selected file and its
validity dates, not fetched from or authenticated against a real controller.
Changes to the selected directory/slot/plan/certificate invalidate the form.
The browser cannot choose arbitrary plan, certificate or output paths.

Successful preparation creates one random local identity, private certificate/
key, pinned public controller certificate and passive base configuration. The
compatibility-version label is not installed vendor firmware or native feature
qualification. The status checks the exact prepared files locally; it never
claims the device is currently started/adopted or the planned cameras processed.
Private-key bytes/hashes are not rendered. Externally changed/unavailable state
is unknown/refused, not silently accepted, repaired or reloaded as success.

Deployment, image choice, address/network setup, adoption and camera pairing
remain separate reviewed steps; see the [NAS plan/deployment guide](aiport-nas-compose.md).
This feature adds no current-installation restart/rollout authority or controller
write. There is no run/adopt/reset/provider/Key-trust action on this listener.
Sign-out is an authenticated CSRF-protected POST.

## Interruptions and competing writers

Repeated creation, existing/partial targets and stale forms refuse. The shared
provisioner has an explicit create-only mode here; its ordinary CLI can still
verify an existing identity without rewriting it. Two independent HTTP
instances competing for one target admit one actual filesystem creator, never
verify/reuse the other writer's identity as a fresh success.

An HTTP cancellation cannot stop an already running private-file thread. New
operations stay fenced, and shutdown waits for that thread. A cancelled waiter
or failed post-write check can therefore leave complete or partial files without
a verified success in the UI. Preserve and inspect them; there is no automatic
retry, deletion, identity rotation or repair. A restarted wizard also refuses an
existing target. Never remove uncertainty by reinitializing adopted state.

The [source/fixture evidence](evidence/first-run-aiport-browser-20261102.md)
separates synthetic browser and local-file qualification from a real independent
installer, native adoption/result, actual-service GUI and broader acceptance.
