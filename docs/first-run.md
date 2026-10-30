# Offline first-run preparation

`local-aikey setup` prepares **a fresh AI Key directory**, using the existing
identity generator, configuration schema and provider validators. It is not
the complete browser wizard or independent fresh-tester acceptance for #25.
AI Port profiles use their separate provisioning workflow. Existing installations
must use their existing configuration/control site, not this command.

## Prepare without editing JSON

For the authenticated browser form, see [browser preparation](#authenticated-browser-preparation)
below. The CLI remains available and uses the same configuration/identity validators.

Install the package as described in the [README](../README.md). Choose a new
directory beneath an existing private parent. Example addresses and model names
below are fictional: supply the actual controller, advertised device address,
operator-reported Protect version and an installed vision-capable model.

For containers, execute setup in the eventual runtime filesystem namespace and
keep the same mounts/paths when starting the device. Absolute generated state and
secret references are not automatically translated from host paths to container
paths; setup does not rewrite an existing Compose/Apple-container recipe.

```sh
local-aikey setup --directory ./new-key --controller 192.0.2.1 --device-ip 192.0.2.110 --protect-version 7.3.74 --provider ollama --model INSTALLED_VISION_MODEL
local-aikey check --config ./new-key/config.json
```

The default listener is the advertised device IP, HTTPS port 8080. For a container
whose published address differs from its internal listener, explicitly use
`--bind 0.0.0.0` and choose `--https-port` to match the intended deployment. This
does not open a firewall or configure port forwarding. An explicit
`--ffmpeg-path /usr/bin/ffmpeg --request-mp4-exports` prepares the supported MP4
export setting; the executable must exist in the eventual runtime. No executable
or model is downloaded, run or tested by setup.

Provider selection shares the existing [provider command](providers.md):

```sh
local-aikey setup --directory ./new-hosted-key --controller 192.0.2.1 --device-ip 192.0.2.110 --protect-version 7.3.74 --provider openai --model VISION_MODEL_ID --api-key-file /private/path/openai-key
local-aikey setup --directory ./new-compatible-key --controller 192.0.2.1 --device-ip 192.0.2.110 --protect-version 7.3.74 --provider openai-compatible --model INSTALLED_VISION_MODEL --base-url http://127.0.0.1:1234/v1
```

Only a **file reference**, never a key value, belongs in the command. Provision
that file through a protected local editor or secret mechanism with owner-only
access before `check`/`run`. Setup does not read, validate, replace or rotate its
contents; subsequent readiness/runtime reads enforce the private-secret rules.
Hosted provider selection explicitly chooses that service; other non-loopback
endpoints require `--allow-remote`, and remote HTTP additionally requires
`--allow-insecure-http`. A private proxy may still forward media to a paid remote
service. Neither a local address nor a provider label proves privacy or no cost.

Output identifies the selected endpoint and untested model availability. No media
is sent by setup. Once real inference is explicitly started, selected camera
frames go to that endpoint; upstream retention, forwarding and charges must be
reviewed separately. OpenAI uses `store=false`, which is not a blanket deletion
or zero-retention guarantee. No fallback, search/database, discovery, continuous
camera policy, named recognition, audio or migration is enabled by this command.

## Trust and start remain explicit

Setup neither contacts the controller nor imports its certificate. Obtain and
independently verify the controller SHA-256 fingerprint, then use:

```sh
local-aikey trust --config ./new-key/config.json --fingerprint VERIFIED_SHA256
local-aikey check --config ./new-key/config.json
```

The `trust` command performs the explicit TLS handshake; it refuses existing
trust. Hostname verification stays enabled. If the certificate does not cover
the chosen controller address, supply both `--controller-fingerprint` and
`--pin-only-hostname` **during setup**. This selects certificate pinning instead
of hostname matching, never disables certificate verification. Additional
deployment-specific policies remain separate work; setup does not promise every
network layout is ready without further configuration.

Select `--control-profile device-service` when following the separately documented
device-service protocol recipe; the default is `ucp4`. The device-service choice
requires `--controller-fingerprint` and is validated before identity creation.
Selecting a profile does not establish its compatibility with your controller.

Protect's search endpoint may use a different certificate. During setup,
`--search-fingerprint VERIFIED_SEARCH_SHA256` prepares a **separate** CA path
and expected pin, without enabling search or the database. Independently verify
each endpoint's fingerprint; do not assume the control certificate covers search.
Import each explicitly, without editing JSON:

```sh
local-aikey trust --config ./new-key/config.json --channel control --fingerprint VERIFIED_SHA256
local-aikey trust --config ./new-key/config.json --channel search --fingerprint VERIFIED_SEARCH_SHA256
```

The selected channel defaults to its configured port (control 7442, search 7443);
`--port` can choose an explicit valid TCP endpoint. The legacy command without
`--channel` still selects control. Import refuses a missing search configuration
or a fingerprint differing from the selected channel's saved pin **before any
network access**. It writes only that channel's trust file, never rewrites the
configuration or copies a control certificate into the search slot. The verified
certificate is published as a complete owner-private file with exclusive
same-directory hard-link creation. A file/link appearing during the handshake
or a second concurrent importer is refused, not overwritten; a dangling target
link is refused before contacting the endpoint. Unsupported hard-link/filesystem
operations fail rather than falling back to replacement. This is not a full
ancestor-directory or hostile-same-user protection claim. Import is
certificate retrieval authenticated by the explicit hash, not a login, adoption,
new model call or a general certificate/hostname-verification bypass.

`check` reads local files only. An operator-reported version is not live version
verification, model capability, native compatibility, adoption or a result.
Missing trust/credentials keep readiness false. Review media destination,
camera processing scope and cost before explicitly running and adopting:

```sh
local-aikey run --config ./new-key/config.json
```

Unlike setup/check, `run` starts actual services and controller connections and
can create a candidate device. Use the [device contract](device-contract.md),
[NAS guide](nas-deployment.md) or [Apple guide](apple-container.md) for the
remaining platform/adoption steps. A fresh independent tester completing native
adoption and a real result remains **needs_evidence**. Browser/mobile accessibility,
role-specific model/camera/budget wizard flows and comprehensive command execution
are not established by this CLI slice.

## Existing paths and interrupted preparation

The requested directory must not exist, even if empty. Files, links (including
dangling links), partial preparations and adopted state are refused unchanged.
The parent must already exist; it is not recursively created. One exclusive
directory creation admits one concurrent setup. The new directory is required
to have the current owner and mode 700; generated private files use mode 600.
Invalid settings are rejected before identity generation.

After a certificate/filesystem failure, inspect and retain the partial directory.
Do not retry into it, delete private state automatically, or claim the directory
was safely rolled back. A successful command alone is not a backup/restore proof.
For retained/adopted state, follow the [state-preserving recovery guide](uninstall-restore.md).

## Verification scope

Synthetic tests run the real CLI with network/runtime/trust-import traps, verify
generated identity/certificates/private modes and disabled optional features,
and exercise invalid endpoint/pin/address settings, unchanged existing paths,
concurrent writers and retained certificate-failure state. They perform no
controller/provider call, new adoption or native test. Model availability and
runtime executable paths remain unverified by design.

## Authenticated browser preparation

The existing control-site executable has a separate, one-shot preparation mode:

```sh
local-aikey-control init --state-dir ./new-admin
local-aikey-control setup --state-dir ./new-admin --directory ./new-key
```

Use an already initialized private administrator state instead when available;
do not reinitialize it. The first command prompts for the password locally with
masked input. No default password is supplied. The setup server uses the same
authentication, session expiry/revocation, Host/Origin/CSRF controls, audit log,
no-script/no-store headers and listener rules as the normal site. Default is
loopback HTTP on port 8765; LAN use requires explicit direct HTTPS, never a
forwarded-header shortcut. See [listener setup](control-site.md#explicit-direct-https).

The directory is fixed by the server operator, not by an HTTP field, and must
not exist. Sign in and enter controller/listener addresses, reported version,
protocol/pins, vision provider/model/endpoint and optional MP4 runtime settings.
The required, initially unchecked acknowledgement describes future media
sharing/retention/cost. Preparation itself does not contact a controller,
provider, model catalog or camera, download weights, enable optional roles,
start a device or adopt anything. AI Port provisioning is separate.

An optional **new** vision key is write-only and belongs to that chosen
provider/destination in the new profile. Hosted providers require it. It is
stored as a private file referenced from the configuration, never returned in
a form, URL or audit record. No existing secret is copied, retargeted or rotated.
Secrets must not be pasted into command arguments, screenshots, public reports
or model prompts. File permissions are the declared platform boundary, not
encryption or provider-retention attestation.

After creation the page shows local file checks and the saved model/destination,
not a model-availability or native compatibility result. To import trust within
this netless wizard, paste **one public PEM certificate** obtained separately,
plus its independently verified SHA-256 fingerprint. Private keys, chains,
extra content, wrong pins and stale/cross-channel forms are refused. The existing
exclusive writer installs only the declared control/search trust slot and never
replaces existing trust. Grants are session/channel/revision-bound, expire in
120 seconds and share a 30-second cooldown. This verifies the certificate bytes
against the supplied pin, not native connectivity, hostname coverage or future
certificate renewal. The alternative `local-aikey trust` command above performs
the separately explicit TLS retrieval; the browser wizard does not.

Creation is one-directory-only; concurrent/replayed forms cannot generate a
second identity or change saved settings. An interrupted or uncertain write
retains private files and refuses reinitialization. File work already admitted
to a thread can finish after an HTTP waiter is cancelled: the server retains
those operations, blocks replacement attempts and waits for them during normal
shutdown. It does not report them as a zero-effect abort or auto-delete their
possible credentials. Filesystem I/O has no universal hard deadline, and this
is not host-power-loss, hostile-same-user/all-ancestor or distributed recovery
qualification. Inspect retained partial state explicitly.

Stop the preparation server before using the normal site with that configuration:

```sh
local-aikey-control run --state-dir ./new-admin --aikey-config ./new-key/config.json
local-aikey check --config ./new-key/config.json
```

Existing directories are refused on a new preparation-server start as well,
including empty, partial and adopted state. There is no HTTP start/adopt/reset,
inference/catalog, arbitrary-path or existing-profile mutation route. An
operator still checks deployment paths/ports, camera permissions and costs,
then starts/adopts the new device explicitly through the documented workflow.
Additional role/camera/budget wizard controls and independent fresh-tester native
results remain open.

Verification uses real loopback HTTP with fictional files/secrets/certificates,
not a claimed visual browser/mobile/keyboard/screen-reader result. No actual
private profile, controller, camera, hosted model or production service is used.
