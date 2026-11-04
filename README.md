# Local AI processor for UniFi Protect

[![Security and quality](https://github.com/Olli0103/unifi-ai-key-emulator/actions/workflows/security.yml/badge.svg)](https://github.com/Olli0103/unifi-ai-key-emulator/actions/workflows/security.yml)
[![Container installation](https://github.com/Olli0103/unifi-ai-key-emulator/actions/workflows/container-install.yml/badge.svg)](https://github.com/Olli0103/unifi-ai-key-emulator/actions/workflows/container-install.yml)

Run an independent AI processor alongside UniFi Protect, with your choice of
local or hosted inference providers. The project implements experimental AI Key
and AI Port compatibility profiles. It is unofficial and is not affiliated with
or endorsed by Ubiquiti. No vendor firmware or model weights are included.

## Status and compatibility

This is development software, currently version `0.1.0` in source. **There is no
published stable release, supported release line or official prebuilt project
image yet.** Build from a reviewed source revision; do not assume a registry tag
or PyPI package with a similar name belongs to this project.

Native observations cover selected Protect 7.3.x versions, including 7.3.74,
with different results per feature and profile. They do not establish support
for every controller, camera, firmware or network layout. Full feature parity,
recognition quality, independent first-install acceptance and sustained-operation
qualification remain open. Read the [compatibility matrix](docs/evidence/compatibility-matrix.md)
and [known limitations](docs/compatibility.md) before connecting a device.

| Profile | What it does | Deployment path |
| --- | --- | --- |
| AI Key | Processes controller-requested descriptions and optional search/recognition tasks | [Linux / Docker Compose](docs/nas-deployment.md), [Apple container](docs/apple-container.md) |
| AI Port | Presents a separate processor identity for paired camera streams and explicitly configured detectors | [AI Port Linux installation](docs/aiport-install.md), [networking reference](docs/aiport-nas-compose.md) |
| Control site | Authenticated local setup, provider/model settings, camera scope and reviewed configuration changes | [Operator workflows](docs/device-workflows.md), [control-site reference](docs/control-site.md) |

AI Key and AI Port are separate identities and configurations, not interchangeable
startup modes. ONVIF metadata planning exists; working ONVIF ingest, PTZ and
recording control are not implemented.

## Install and deploy

Start with the [deployment guide](docs/deployment.md). It covers source selection,
requirements, a non-container AI Key installation, trust, adoption, verification,
updates and rollback. It also links the Docker, AI Port and Apple-specific paths.

You need Python 3.12+ for a host install, a trusted private network, persistent
private storage, independently verified controller TLS trust and an explicitly
chosen vision-capable model. Linux containers support AMD64 and ARM64 builds.
Optional detectors, speech, face recognition and search need additional models
or services. They are not installed or enabled by the base image.

The service can receive camera media and send it to the configured provider.
Use a local model server if you want to keep inference within your network;
verify that it does not forward requests elsewhere. Hosted services may charge
for requests and retain data under their own policies. No provider fallback is
enabled automatically. See [provider configuration](docs/providers.md).

Try the credentials-free [synthetic control-site demo](docs/synthetic-demo.md)
if you want to inspect the interface before configuring a controller. Its data
is fictional and it cannot adopt devices or run real inference.

## Run the local lab

The lab uses synthetic loopback controller, media, model and embedding services.
It does not contact Protect or send images to an external model.

```sh
git clone https://github.com/Olli0103/unifi-ai-key-emulator.git
cd unifi-ai-key-emulator
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev,database]'
.venv/bin/local-aikey lab --output lab-results.json
.venv/bin/python -m pytest -q
```

This exercises real local TLS, HTTP and WebSocket paths with synthetic responses.
A passing lab is not native adoption, detection-quality or end-user acceptance.

## Capabilities and current limits

| Area | Available implementation | Important limit |
| --- | --- | --- |
| Management | Persistent identity, TLS management, adoption/control, discovery options | Controller-version and network-layout dependent |
| Vision | OpenAI Responses, Anthropic Messages, Ollama and OpenAI-compatible providers | Explicit model/endpoint required; quality is model and scene dependent |
| AI Port detection | Camera-isolated stream handling, object/zone tracking, optional local/API detection | Pairing stays in Protect; capacity depends on actual stream dimensions |
| Search | E5, CLIP and hybrid-search paths with separate profile/model contracts | Matching database extensions, model space and credential synchronization required |
| Speech, faces and plates | Optional model services, camera scopes and enrollment controls | Not enabled by default; native parity and quality are not blanket-qualified |
| Administration | Authenticated setup, revision-bound review/save, maintenance and reconciliation flows | A saved setting is not evidence that a running device has loaded it |
| Installation | Native Linux AMD64/ARM64 image CI, source/package checks | CI fixtures do not prove every NAS OS or Apple runtime works |

A successful callback does not prove that Protect indexed or displayed its result.
Verify the original camera/event in Protect after refresh. Adoption, persistent
results, search, restart recovery and quality are separate acceptance steps.

## Documentation

- [Documentation index](docs/README.md)
- [Deployment and first connection](docs/deployment.md)
- [Provider configuration](docs/providers.md)
- [Control site](docs/control-site.md) and [operator workflows](docs/device-workflows.md)
- [Updates, pause, uninstall and restore](docs/uninstall-restore.md)
- [Compatibility and dated evidence](docs/evidence/README.md)
- [Roadmap](PLAN.md), [changelog](CHANGELOG.md) and [open issues](https://github.com/Olli0103/unifi-ai-key-emulator/issues)
- [Contributing](CONTRIBUTING.md), [maintenance](MAINTAINERS.md) and [security policy](SECURITY.md)

Project-authored code is licensed under [Apache-2.0](LICENSE). Third-party code,
images and models retain their own terms. Public binary/image publication has
separate [release-readiness requirements](docs/legal/release-readiness.md);
this README does not approve a release or firmware/model redistribution.

## Support the project

If you find this project useful, consider starring the repository or supporting
its development via [GitHub Sponsors](https://github.com/sponsors/Olli0103).

## Star history

[![Star history for unifi-ai-key-emulator](https://api.star-history.com/svg?repos=Olli0103/unifi-ai-key-emulator&type=Date)](https://star-history.com/#Olli0103/unifi-ai-key-emulator&Date)
