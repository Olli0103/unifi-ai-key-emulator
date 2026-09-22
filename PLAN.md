# Build plan

Target: UDM Pro Max, Protect 7.3.56, UGREEN NAS deployment.

OpenAI, Ollama and compatible providers are configurable. Provider selection and key-file configuration are included in the build; no hosted model request is part of verification. Deployment examples use documentation-only IP addresses that must be replaced with the actual console and NAS addresses.

Build an independent Python service and NAS container package. Keep the original public firmware outside the project. Use an ordinary new-device adoption flow and an emulator-owned certificate. Develop against a loopback controller simulator before any real adoption.

## Milestones and acceptance

1. **Runnable foundation.** Package, strict configuration, persistent device identity and TLS keys, CLI, readiness report and graceful shutdown. Initialization must not contact Protect or overwrite an existing identity.
2. **Device control.** Management endpoints, ordinary credentialed adoption, verified controller TLS, UCP4 command handling, honest capability advertisement, job admission and reconnect state. Real local TLS/WebSocket tests must exercise wrong credentials, certificate rejection and command correlation.
3. **Descriptions.** Bounded job queue, approved media origins, configurable vision provider, image/MP4 handling, version-specific callbacks, timeout and deduplication. Exercise real HTTP requests against synthetic local services. Unsupported UBV inputs must produce an explicit failure.
4. **Search.** E5 document/query embedding adapter and model-aware natural-language socket. Supply PostgreSQL deployment preparation for the observed dense-search profile. Reject incompatible dimensions and unsupported legacy search. Hybrid search requirements remain explicit.
5. **Integrated lab.** Run the assembled service against an independent simulator through TLS management, adoption, control commands, a media download, synthetic inference and result callback. Retain a machine-readable report. This is local interoperability testing, not a real Protect acceptance claim.
6. **NAS handoff.** Container build files, configuration example, secret-file handling, persistent volumes and operational instructions. Validate what can run locally. Docker-image build and NAS operation require a Docker host and remain separate checks when unavailable here.
7. **Real Protect validation.** Inspect the exact 7.3.56 contract, adopt one test processor, enable only one selected test camera, receive an authentic task, verify persistence and search, then test restart behavior. No console changes are part of the local build run.

## Build outcome

Milestones 1 through 5 are implemented and covered by local tests. Milestone 6 has deployment files and instructions, with static checks only. Milestone 7 remains open, `needs_evidence`. The [verification report](BUILD-RESULT.md) records the final checks and runtime limits.

## Source boundary

The statically inspected controller is 7.2.105, while the target version is 7.3.56. Public metadata queries for 7.3.56 returned no package in this run. The implementation must disclose this compatibility gap and must not label the target as tested. The AI Key 2.2.8 and receiver contracts already differ in some fields.

All generated descriptions in automated tests are labeled synthetic. Real inference requires an explicitly configured vision provider and model. There is no silent fallback to invented prose or random embeddings.

## Live-test boundary

Local tests use synthetic loopback services. Starting a configured device can create a candidate processor record in Protect. Run native acceptance checks on an explicitly selected test processor and camera, with access authorized by the system owner.
