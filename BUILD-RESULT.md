# Build verification

Built on 22 September 2026 for the target platform: UDM Pro Max, Protect 7.3.56, and eventual UGREEN NAS deployment.

The result is a runnable experimental implementation with source, tests, a Python package, Docker files and NAS instructions. Native Protect intelligence is not yet proven. No real controller, NAS or model provider was contacted by the local tests.

## Observed checks

- The Python suite passed: 198 tests and 56 subtests. The same suite passed again after preparing the public source tree.
- The standalone CLI lab passed all 12 checks in [lab-results.json](lab-results.json). It used real loopback TLS, client certificates, HTTP and WebSocket connections with synthetic controller and model services.
- The lab exercised credential rejection, normal adoption, control commands, media download, vision inference, a description callback, 384-dimensional document/query embeddings, restart with the same identity, callback deduplication and a health response without credentials.
- Provider tests exercised OpenAI Responses, native Ollama and compatible Chat Completions against loopback HTTP fixtures. They checked response errors, redirects, credential separation and explicit configuration. Invalid setup commands preserved existing settings, and readiness rejected invalid credentials. No real model output was evaluated.
- Ruff, Python compilation and installed dependency consistency passed. Compose structure, build context paths, PostgreSQL shell syntax and relative documentation links were checked locally.
- PostgreSQL credential tests used a simulated connection. Real PostgreSQL transactions, migrations and restart recovery were not run.

The local runtime was macOS with Python 3.14.4. The package declares Python 3.12 or newer; the Dockerfile uses Python 3.12. Docker and a separate Python 3.12 runtime were unavailable here, so neither a Docker build nor execution on Python 3.12 is claimed.

The lab intentionally rejects control/query connections before adoption. The two initial WebSocket handshake warnings are expected in this fixture. Its successful result verifies the subsequent adoption and reconnect sequence.

## Evidence and remaining acceptance

The static protocol evidence came from AI Key 2.2.8 and Protect 7.2.105. Public metadata queries did not return the target Protect 7.3.56 package. The emulator and simulator are independently written. No vendor runtime or model weights are distributed.

Native adoption, real jobs, description persistence, search retrieval, Docker startup and NAS operation remain `needs_evidence`. The optional database prepares dense search only. Hybrid BM25/reranking, face/plate recognition, audio and complete legacy-camera enhancement are unsupported. Capability defaults reflect those limits.

The next deployment trial uses one separately identified test processor and one selected camera. First verify the NAS directory, account and free ports. Then build the images, initialize identity, select a vision model, establish controller trust and verify ordinary adoption. Prepare database credential synchronization before adoption if search is intended. Check persisted descriptions and native search separately, including after a restart. See [NAS deployment](docs/nas-deployment.md) for the commands and rollback.
