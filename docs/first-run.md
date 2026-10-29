# Offline first-run preparation

`local-aikey setup` prepares **a fresh AI Key directory**, using the existing
identity generator, configuration schema and provider validators. It is not
the complete browser wizard or independent fresh-tester acceptance for #25.
AI Port profiles use their separate provisioning workflow. Existing installations
must use their existing configuration/control site, not this command.

## Prepare without editing JSON

Install the package as described in the [README](../README.md). Choose a new
directory beneath an existing private parent. Example addresses and model names
below are fictional: supply the actual controller, advertised device address,
operator-reported Protect version and an installed vision-capable model.

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
controller-channel certificates/trust and deployment-specific policies remain
separate configuration work; this command does not promise every network layout
is ready without further configuration.

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
