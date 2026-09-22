# Build verification

Built on 22 September 2026 for the target platform: UDM Pro Max, Protect 7.3.56, and eventual UGREEN NAS deployment.

The result is a runnable experimental implementation with source, tests, a Python package, Docker files and NAS instructions. Native on-demand analysis and one persisted automatic event caption are verified on Protect 7.3.60. Continuous operation and native search remain unverified. The automated local tests use fixtures; the separate live checks below contacted the authorized controller and provider.

## Observed checks

- The current Python suite passed: 442 tests and 56 subtests. Ruff also passed after the adoption, scoped-camera, basic-description, compatibility-manifest and camera-inventory changes.
- The standalone CLI lab passed all 12 checks in [lab-results.json](lab-results.json). It used real loopback TLS, client certificates, HTTP and WebSocket connections with synthetic controller and model services.
- The lab exercised credential rejection, normal adoption, control commands, media download, vision inference, a description callback, 384-dimensional document/query embeddings, restart with the same identity, callback deduplication and a health response without credentials.
- Provider tests exercised OpenAI Responses, native Ollama and compatible Chat Completions against loopback HTTP fixtures. They checked response errors, redirects, credential separation and explicit configuration. Invalid setup commands preserved existing settings, and readiness rejected invalid credentials. No real model output was evaluated.
- Ruff, Python compilation and installed dependency consistency passed. Compose structure, build context paths, PostgreSQL shell syntax and relative documentation links were checked locally.
- PostgreSQL credential tests used a simulated connection. Real PostgreSQL transactions, migrations and restart recovery were not run.

The host tests ran on macOS with Python 3.14.4. Apple container 1.4.1 built and ran the ARM64 image using Python 3.12. Docker and the NAS deployment have not been tested.

The lab intentionally rejects control/query connections before adoption. The two initial WebSocket handshake warnings are expected in this fixture. Its successful result verifies the subsequent adoption and reconnect sequence.

## Evidence and remaining acceptance

The static protocol evidence came from AI Key 2.2.8 and Protect 7.2.105. Public metadata queries did not return the target Protect 7.3.56 package. The emulator and simulator are independently written. No vendor runtime or model weights are distributed.

Native adoption on Protect 7.3.56 is now observed: the controller shows the AI Key online, the pinned control connection completed time synchronization, Protect rotated its management password, and the emulator reconnected after a restart with factory enrollment disabled. The former setup credentials returned HTTP 401. A bounded macOS discovery companion also supplied the advertised address to Protect.

A real OpenAI request using a synthetic image succeeded. A subsequent native test on Protect 7.3.60 used the already authenticated Safari session to request one on-demand camera description. Protect dispatched the clip to the emulator; the worker recorded a completed job and HTTP 200 callback; Protect returned the real description with HTTP 200. No password login or session-cookie export was needed. A separately created integration API key read documented camera metadata but received HTTP 401 on the private application API.

A later read-only preflight used that integration API key and the independently pinned web certificate against the documented Protect 7.3.60 local integration API. It returned 11 cameras: 8 connected cameras advertising onboard smart detections, 2 connected cameras without those detections, and 1 offline camera. The client saved a private JSON and static HTML report, with processing disabled. The response included the audio flag `smoke_cmonx`, absent from the published 7.3.60 OpenAPI enum. This inventory read does not establish AI Key event delivery on the other camera families or a legacy-camera ingress path.

On-demand analysis returns a description without persisting it. A separate fresh G5 Flex smart detection completed through the automatic `recognizeKeyFrames` path. Its full RAM callback returned HTTP 200. A subsequent exact-event GET returned `metadata.ramState: "done"` and the generated `metadata.ramDescription`; both remained present after a full Safari page reload. The native event summary panel displayed that caption. The emulator subsequently reconnected after a restart without processing another event. No other camera was processed. The single-use permit is consumed, so this result does not establish continuous operation.

Search retrieval and NAS operation remain `needs_evidence`. The optional database prepares dense search only. Hybrid BM25/reranking, face/plate recognition, audio and complete legacy-camera enhancement are unsupported. A saved caption alone does not make its words searchable in Find Anything. Capability defaults reflect those limits.

The successful Mac trial used an opt-in camera scope permitting one on-demand job for a verified Protect camera ID. That permit is consumed. Tests cover mismatched media, duplicate requests, consumed permits across restart, timeout and storage failure. The [automatic-description trial](docs/basic-descriptions.md) requires its own explicit scope and separate persistence verification.

The first native automatic event reached the worker but failed validation before admission. That build retained only the error class, so the exact rejection remains unknown. Source-backed compatibility fixes now accept both native video labels, optional recognition metadata, longer bounded clips and sampled key moments. Regression tests cover those forms, including a real decoder check after ten seconds. Fixed diagnostic counters distinguish future rejection causes without recording request values. The subsequent live event was admitted successfully and produced the persisted caption described above.

The next deployment trial uses one separately identified test processor and one selected camera. First verify the NAS directory, account and free ports. Then build the images, initialize identity, select a vision model, establish controller trust and verify ordinary adoption. Prepare database credential synchronization before adoption if search is intended. Check persisted descriptions and native search separately, including after a restart. See [NAS deployment](docs/nas-deployment.md) for the commands and rollback.
