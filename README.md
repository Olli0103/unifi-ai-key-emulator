# Local AI Key emulator

An independent experimental AI processor for UniFi Protect. The current target is Protect 7.3.56 on a UDM Pro Max, with deployment on a UGREEN NAS. This is an unofficial project and is not affiliated with Ubiquiti.

The local build has device management/adoption, UCP4 control, UDP discovery, a bounded vision worker, version-specific description callbacks and an E5 query responder. Vision providers are configurable: OpenAI Responses, native Ollama and OpenAI-compatible APIs. Search embeddings are configured separately. No vendor firmware or model weights are bundled.

**Status: runnable lab implementation. Native Protect 7.3.56 compatibility and NAS deployment are unverified.** The inspected controller package is 7.2.105. Public metadata did not return a 7.3.56 package during this build. Passing the simulator does not establish adoption, native descriptions or Find Anything on the UDM.

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
| Management/control | HTTPS info/adoption, credential checks, stable identity, UCP4, reconnect, state, queue count, bounded commands | Discovery/adoption UI and all required commands on 7.3.56 |
| Discovery | Read-only v1 information/targeted queries, exact controller allowlist, optional multicast on Linux | Packet accepted by actual Protect discovery |
| Descriptions | Bounded queue, configurable vision provider, image/MP4 input, on-demand/task/legacy callback profiles | Correct real job and persistent native description |
| Search | Matched E5 document/query adapter, 384 dimensions, model guard, query socket | Matching actual controller profile and retrieval quality |
| Search database | PostgreSQL/pgvector preparation and credential rotation hook | Docker execution, controller migrations and restart recovery |

Unsupported capabilities default to disabled. The controller source inspected here couples VLM and deep-mode flags, so the build does not automatically advertise full intelligence. Face/plate recognition, audio, legacy CLIP image search, hybrid BM25/reranking and full legacy-camera enhancement are not implemented.

For video, set `worker.ffmpeg_path` to an absolute executable path. `worker.request_mp4_exports=true` changes only the verified AI export route's supported `format=ubv` parameter to MP4, retaining its other allowed parameters. Arbitrary UBV files are rejected. Acceptance of this MP4 request on 7.3.56 still needs a native test.

Descriptions become candidates for semantic indexing only when the actual controller creates the required task/session and the compatible search database is ready. Set `worker.description_embeddings=true` and `search.enabled=true` together for the E5 profile. Use the same `embeddings` configuration for document and query encoding. A shape-correct embedding is not proof of model compatibility. The HTTP backend expects E5 text embeddings; the optional local backend loads an existing checkpoint without downloading or executing remote model code.

HTTP callback success is reported as `http_accepted`, not as successful indexing. A private job journal prevents duplicate completed callbacks across restart and stops automatic replay after an uncertain callback. Pending inference is not a durable queue. The journal is bounded and stops admission at its configured capacity; review and archive terminal entries as part of longer tests.

## NAS deployment

See [NAS setup](docs/nas-deployment.md). Compose uses Linux host networking so Protect can address management, discovery and the dedicated search database at the emulator's advertised NAS IP. This requires checking port availability on the NAS first. It adds no vision-model service, so an existing local inference server can be used.

Docker is unavailable on the build Mac. The container and Compose files were prepared for the NAS, but a successful image build or NAS run is not claimed.

## Engineering records

- [Verification report](BUILD-RESULT.md)
- [Build plan](PLAN.md)
- [Device/adoption/discovery contract](docs/device-contract.md)
- [Worker contract](docs/worker-contract.md)
- [Vision providers](docs/providers.md)
- [Search contract](docs/search-contract.md)
- [Database credential contract](docs/database-contract.md)
- [Research source index](docs/evidence/README.md)

Use a separately identified test processor and one selected camera for the first real trial. Native adoption, persistent descriptions, search retrieval and recovery are separate acceptance checks.
