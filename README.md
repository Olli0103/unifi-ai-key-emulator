# Local AI processor for Protect

An independent experimental AI processor for UniFi Protect. The implemented profile currently emulates a bounded subset of AI Key. The roadmap also targets a separate AI Port compatibility profile for G3, G4/G5 and ONVIF cameras. Native AI Key adoption has been tested with Protect 7.3.56 on a UDM Pro Max using Apple container 1.4.1 on an Apple silicon Mac. AI Port adoption and UGREEN NAS deployment remain planned. This is an unofficial project and is not affiliated with Ubiquiti.

The local build has device management/adoption, UCP4 control, UDP discovery, a bounded vision worker, version-specific description callbacks and an E5 query responder. Vision providers are configurable: OpenAI Responses, native Ollama and OpenAI-compatible APIs. Search embeddings are configured separately. No vendor firmware or model weights are bundled.

**Status: native adoption, control and a persisted automatic event caption verified.** The ARM64 image built and ran under Apple container 1.4.1. Protect 7.3.56 displayed the processor online; local checks confirmed adopted state, control time synchronization, management-password rotation, disabled factory authentication and reconnect after a planned restart. On Protect 7.3.60, both an on-demand description and one automatic G5 Flex event completed through OpenAI. The automatic event's saved caption remained available after a full Protect page reload. This was a single-event trial; continuous operation, search and NAS deployment have not been verified.

The inspected controller source is 7.2.105, alongside AI Key firmware 2.2.8. Public metadata did not return a 7.3.56 package during inspection. The live results above establish those specific behaviors, not full protocol or feature compatibility. The [bounded automatic-description path](docs/basic-descriptions.md) has separate persistence acceptance checks.

## Roadmap and product status

The [roadmap](PLAN.md) covers native AI Key and AI Port behavior, automatic camera discovery, AI Port processing for legacy and ONVIF cameras, a provider/model control site, security and detection-quality checks, and the path to a maintained open-source product. Work is tracked in the [issue index](docs/planning/issues.md), with [Claude/contributor handoff instructions](docs/planning/claude-handoff.md).

The repository currently has no license. Licensing, provenance and release governance are explicit product work; public source availability alone does not establish an open-source release. All-camera processing and the control site are planned, not deployed.

A [read-only camera inventory preflight](docs/camera-inventory-preflight.md) is available for Protect 7.3.60. It reads the local integration API with a private API-key file and pinned web certificate, then writes a private eligibility report. It does not enable processing. Automatic registry refresh, all-camera admission and the control site remain planned.

## Run the local lab

Requires Python 3.12 or newer. The lab uses synthetic loopback controller, media, model and embedding services. It does not contact Protect or send images to an external model.

```sh
git clone https://github.com/Olli0103/unifi-ai-key-emulator.git
cd unifi-ai-key-emulator
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev,database]'
.venv/bin/local-aikey lab --output lab-results.json
.venv/bin/python -m pytest -q
```

The integrated lab uses real TLS, client certificates, HTTP and WebSocket connections. It exercises credentialed adoption, device commands, media download, an explicitly synthetic vision response, a description callback, document/query embeddings and a process-service restart. Its simulated controller is independently written; neither it nor the emulator imports vendor code.

## Initialize a real configuration

```sh
.venv/bin/local-aikey init --config config.json --state-dir state
.venv/bin/local-aikey check --config config.json
```

Initialization generates a random local MAC identity, persistent private key/certificate and private credential files. It refuses to replace an existing configuration or silently repair an incomplete identity. `check` reads local files only and exits 2 until required settings are supplied.

Set `controller.host`, `device.ip`, a suitable `runtime.bind`, and a real vision provider/model. No model name is assumed. A remote model endpoint needs an explicit `inference.allow_remote`; unencrypted remote HTTP additionally needs `inference.allow_insecure_http`.

Select a provider without making an API request:

```sh
.venv/bin/local-aikey provider openai --config config.json --model VISION_MODEL_ID --api-key-file /private/path/openai-key
.venv/bin/local-aikey provider ollama --config config.json --model INSTALLED_VISION_MODEL
.venv/bin/local-aikey provider openai-compatible --config config.json --model VISION_MODEL_ID --base-url http://127.0.0.1:1234/v1
```

Each command replaces the prior provider configuration and preserves device identity. API key values never appear in command arguments. OpenAI mode uses the Responses API and requests `store=false`; starting real inference sends the selected camera frames to OpenAI. Ollama mode calls the configured Ollama server. No automatic fallback switches providers. See [provider configuration](docs/providers.md).

Controller connections require trusted TLS and the emulator client certificate. Import a controller certificate only after independently checking its SHA-256 fingerprint:

```sh
.venv/bin/local-aikey trust --config config.json --fingerprint VERIFIED_SHA256
```

That command makes a TLS handshake to the configured controller, sends no HTTP credentials or adoption request, verifies the fingerprint, and saves the certificate. It refuses to overwrite existing trust. Hostname checks remain enabled. If the console certificate does not cover its LAN name/IP, explicitly set `controller.verify_hostname=false` and `controller.expected_fingerprint` to the verified value. Every controller channel still requires a trusted certificate and checks the configured pin before sending credentials.

`local-aikey run --config config.json` starts real controller connections. A pre-adoption connection can create a candidate processor record in Protect. Run it only as part of the authorized device test, not as a passive status check.

## Capabilities and current limits

| Component | Implemented | Remaining native proof |
| --- | --- | --- |
| Management/control | HTTPS info/adoption, stable identity, UCP4, time sync, credential rotation and reconnect observed on 7.3.56 | Additional controller commands and longer recovery testing |
| Discovery | Read-only v1 queries, exact controller allowlist, optional Linux multicast and macOS host companion; native candidate and address observed | Other network layouts and NAS discovery |
| Descriptions | Bounded queue, configurable vision provider, image/MP4 input; native on-demand and one persisted automatic caption verified | Continuous operation and longer recovery testing |
| Search | Matched E5 document/query adapter, 384 dimensions, model guard, query socket | Matching actual controller profile and retrieval quality |
| Search database | PostgreSQL/pgvector preparation and credential rotation hook | Docker execution, controller migrations and restart recovery |

Unsupported capabilities default to disabled. The controller source inspected here couples VLM and deep-mode flags, so the build does not automatically advertise full intelligence. Face/plate recognition, audio, legacy CLIP image search, hybrid BM25/reranking and full legacy-camera enhancement are not implemented.

For video, set `worker.ffmpeg_path` to an absolute executable path. `worker.request_mp4_exports=true` changes only the verified AI export route's supported `format=ubv` parameter to MP4, retaining its other allowed parameters. Arbitrary UBV files are rejected. Protect 7.3.60 accepted this MP4 export in the native on-demand test.

Descriptions become candidates for semantic indexing only when the actual controller creates the required task/session and the compatible search database is ready. Set `worker.description_embeddings=true` and `search.enabled=true` together for the E5 profile. Use the same `embeddings` configuration for document and query encoding. A shape-correct embedding is not proof of model compatibility. The HTTP backend expects E5 text embeddings; the optional local backend loads an existing checkpoint without downloading or executing remote model code.

HTTP callback success is reported as `http_accepted`, not as successful indexing. A private job journal prevents duplicate completed callbacks across restart and stops automatic replay after an uncertain callback. Pending inference is not a durable queue. The journal is bounded and stops admission at its configured capacity; review and archive terminal entries as part of longer tests.

## NAS deployment

For the tested Mac deployment, use the separate [Apple container guide](docs/apple-container.md). It publishes management through the Mac's LAN address, uses an optional host discovery companion, and leaves container discovery and search disabled.

See [NAS setup](docs/nas-deployment.md). Compose uses Linux host networking so Protect can address management, discovery and the dedicated search database at the emulator's advertised NAS IP. This requires checking port availability on the NAS first. It adds no vision-model service, so an existing local inference server can be used.

The Linux ARM64 image was built and run with Apple's container runtime. Docker and Compose execution on the NAS, including the optional PostgreSQL deployment, remain unverified.

## Engineering records

- [Verification report](BUILD-RESULT.md)
- [Build plan](PLAN.md)
- [Device/adoption/discovery contract](docs/device-contract.md)
- [Worker contract](docs/worker-contract.md)
- [Vision providers](docs/providers.md)
- [Search contract](docs/search-contract.md)
- [Database credential contract](docs/database-contract.md)
- [Security policy](SECURITY.md) and [security contract](docs/security-contract.md)
- [Research source index](docs/evidence/README.md)

Use a separately identified test processor and one selected camera for the first real trial. Native adoption, persistent descriptions, search retrieval and recovery are separate acceptance checks.
