# Deployment from source

This guide describes the current development source, version `0.1.0`. No signed
stable release or official project image has been published. Choose a reviewed
commit and retain its exact SHA with your deployment. Do not install an unrelated
package or pull a similarly named image as a substitute.

## Choose a deployment

| Host / goal | Follow |
| --- | --- |
| Trusted Linux host, one AI Key, no search initially | Host installation below |
| Linux server or NAS with Docker Engine | [AI Key Compose guide](nas-deployment.md) |
| Linux server or NAS, one or more AI Ports | [AI Port Linux installation](aiport-install.md) |
| Apple silicon Mac | [Apple AI Key guide](apple-container.md) or [AI Port runtime and relay limits](aiport-candidate.md), with [instance reconciliation](aiport-apple-reconcile.md) |
| Interface preview, no controller or models | [Synthetic demo](synthetic-demo.md) |

Keep AI Key and AI Port state separate. The root `compose.yaml` is an AI Key
recipe, not a combined multi-Port installation. Do not run two copies of an
adopted identity at the same time.

## Requirements

- A trusted host and private network reachable by the Protect console. Do not
  expose management, discovery, search or the control site to the public internet.
- Python 3.12+ and a virtual environment for host tools. Install a compatible
  FFmpeg executable separately if processing MP4 exports or camera streams.
- Private persistent storage owned by the service account. Device keys,
  credentials, controller trust and job journals are not disposable cache.
- The exact controller address, Protect version, independently checked SHA-256
  certificate fingerprint and a free advertised device address/management port.
- A vision-capable model and reachable provider endpoint. No model is selected
  implicitly, downloaded or tested by setup. Read [provider configuration](providers.md).

Native observations are version-specific. Check the [compatibility guide](compatibility.md)
before choosing control/search profiles or enabling optional features.

## 1. Select and install the source

Run these commands as the dedicated non-root account that will own the state.
Use a new checkout, not a directory containing existing device state:

```sh
git clone https://github.com/Olli0103/unifi-ai-key-emulator.git
cd unifi-ai-key-emulator
git rev-parse HEAD
# Review this revision, or check out your chosen full commit SHA before continuing.
git switch --detach
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps -r requirements-build.lock -r requirements-runtime.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation .
.venv/bin/python -m pip check
.venv/bin/python -m pip show local-aikey
```

Record the displayed commit SHA and keep the checkout while it is installed.
The lock files pin the base package build/runtime dependencies. Optional extras
have their own requirements and model/image obligations. They are not installed
by this base procedure. Do not replace the lock with a bare `pip install` from a
public package index.

For Docker, build both application and any optional database images from the
same reviewed tree. The Dockerfiles use pinned base-image digests and lock files.
They do not contain secrets or models. Debian packages are resolved at build
time, so these are not promises of byte-identical future container rebuilds.

## 2. Prepare a fresh AI Key

The following example is for a host process, not a container. Create a private
parent directory and choose a new, nonexistent child for setup. Replace every
uppercase placeholder, including model IDs and the reported Protect version.
The addresses `192.0.2.1` and `192.0.2.110` are documentation-only examples and
will not work on your LAN.

```sh
mkdir -m 700 ../aikey-private
.venv/bin/local-aikey setup --directory ../aikey-private/key \
  --controller 192.0.2.1 --device-ip 192.0.2.110 \
  --protect-version YOUR_PROTECT_VERSION \
  --provider ollama --model INSTALLED_VISION_MODEL \
  --control-profile device-service --controller-fingerprint VERIFIED_SHA256
```

Use the protocol profile supported by your target version; `device-service` is
an explicit example, not auto-detection. The default bind address is the
advertised device address, which must belong to this host. For a container,
setup must run inside the eventual mount namespace and use the appropriate
internal bind address. Follow its platform guide rather than copying host paths
into a container configuration.

If the controller certificate does not cover the chosen address, setup supports
`--pin-only-hostname` together with the verified fingerprint. That selects
explicit pinning instead of hostname matching; it does not disable certificate
verification. Never accept an unverified fingerprint or turn TLS verification off.

For hosted providers or a model server on another host, follow the exact
[setup/provider flags](first-run.md). API keys belong in private files or the
control site's write-only key input, never shell arguments. A remote endpoint
needs explicit permission; unencrypted remote HTTP needs an additional opt-in.

Setup creates local identity/configuration files only. It does not contact the
controller, adopt a device, download a model, enable search or grant camera
processing. Existing, partial and adopted directories are refused. Do not
reinitialize an installation to fix a connection error.

## 3. Import trust and check readiness

Obtain the controller fingerprint through an independently trusted channel.
The following import performs a TLS handshake to the configured endpoint; it
sends no adoption request or HTTP credentials:

```sh
.venv/bin/local-aikey trust --config ../aikey-private/key/config.json --channel control --fingerprint VERIFIED_SHA256
.venv/bin/local-aikey check --config ../aikey-private/key/config.json
```

Investigate every readiness error before startup. `check` reads local state;
it does not prove model availability or native compatibility. Search has a
separate trust channel and database/model setup. Leave it disabled initially
unless you have completed [database preparation](database-contract.md) before
adoption's credential rotation.

## 4. Start, adopt and verify one camera

Review network exposure and provider destination first. Running starts real
controller connections and can create a pending processor in Protect:

```sh
.venv/bin/local-aikey run --config ../aikey-private/key/config.json
```

For this first test, keep the process in the foreground. Allow only the
controller and required private peers through the host firewall. The Key's
default HTTPS management port is 8080; outbound control, search and media use
7442, 7443 and 7444 respectively when enabled. Discovery is optional UDP 10001.
AI Ports instead need their own reachable address and HTTPS 443. Do not forward
these ports from the internet or loosen source allowlists to work around NAT.

In Protect, select the pending device and adopt it. Use the private generated
management credential when the console offers that flow. Some version-specific
flows require the separately documented [bounded factory enrollment](factory-enrollment.md).
Do not leave a factory-authentication window open or reset an adopted identity.
The console can rotate the management credential; the original password file
is not a source of truth for the rotated password.

Use the [control site](control-site.md) and [device workflows](device-workflows.md)
to review provider/model, one-camera scope and processing settings. Controller
pairing, zones and recording/AI settings remain in Protect. For an AI Port,
stream decoding alone does not mean a detector is enabled or an event was saved.

Verify separately:

1. Protect shows the intended identity online and, for a Port, the intended camera paired.
2. One explicitly chosen test produces the expected result on the original
   camera/event, still present after refreshing Protect.
3. A planned service restart reconnects using the same identity, credentials and
   pairings. Inspect uncertain jobs before retrying them.
4. Optional search returns the intended positive result and excludes a chosen
   negative query. A 2xx callback or database row count is not sufficient.

Only then widen camera scope or add models. Follow the documented queue,
capacity, retention and provider-cost limits for your selected profile.

## 5. Run unattended, update and recover

Use the platform's service manager only after the foreground test works. Keep
the service account, absolute executable/configuration paths and private state
mounts fixed. The Linux Compose recipe supplies a restart policy. Apple container
requires the separate supervisor and logged-in LaunchAgent described in its guide.
The control site is a separate service with separate credentials; loopback is the
default, and LAN administration requires its explicit HTTPS configuration.

Before an update, record the running source/image, effective configuration and
model versions. Take a verified, access-controlled backup of identity,
controller-managed credentials, configuration, journals, model/template bindings
and any search database. Use a consistent database dump, not a raw copy of a live
PostgreSQL directory. Keep the previous image and a state-compatible recovery path.

Build the replacement from a reviewed commit in a separate checkout. Change one
instance at a time, stop the predecessor before starting its replacement, reuse
its state and repeat the native checks. Do not use `git pull` inside a live
installation as an unreviewed upgrade. Do not regenerate identity, delete a
volume, use `docker compose down -v` or blindly replay an uncertain operation.
A failed upgrade may have changed credentials or index state; starting an old
image against an old snapshot is not automatically safe rollback.

Use [pause, uninstall and restore](uninstall-restore.md), [Apple upgrade recovery](apple-upgrade-recovery.md)
and [index migration](index-migration.md) for their distinct state contracts.
Redact addresses, identities, credentials and all media-derived content before
sharing diagnostics through the [issue templates](../CONTRIBUTING.md).
