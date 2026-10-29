# Research provenance

These records describe the interface observations used by the implementation. They contain package references and hashes. They are not proof of permission to analyze firmware, a complete code-origin audit, or an organizationally separate clean-room process. Those questions remain open in the [provenance record](../legal/provenance.md).

- [Adoption investigation](adoption-evidence.md)
- [Controller callbacks and saving](controller-evidence.md)
- [Search and storage](search-evidence.md)
- [AI Key compatibility matrix](compatibility-matrix.md), generated from the versioned [compatibility manifest](compatibility-manifest.json)
- [AI Port controller contract](ai-port-controller-contract.md) and [AI Port firmware/discovery evidence](ai-port-firmware-contract.md)

## Compatibility manifest

The manifest records the currently inventoried AI Key profile features as `native-verified`, `fixture-tested`, `implemented`, `unsupported` or `needs_evidence`. Live results are recorded separately for each live trial (Protect 7.3.56 adoption and control, Protect 7.3.60 captions). Static Protect 7.2.105 and AI Key 2.2.8 references never count as native evidence. A feature is `native-verified` only when a named live trial recorded that behavior, and only for that version and condition. A capability flag or a callback HTTP 200 is never sufficient.

`tests/test_compatibility_manifest.py` checks structural consistency: cited tests and records exist, live and static sources stay separate, and the runtime version table in `aikey.protocol` matches the manifest. It cannot prove a claimed live observation. It also scans public fixtures for credentials, real addresses and non-synthetic identifiers. After editing the manifest, regenerate the matrix with `python tests/test_compatibility_manifest.py --write`. Bump `manifest_version` and `aikey.protocol.COMPATIBILITY_MANIFEST_VERSION` together when statuses change.

Synthetic protocol fixtures live in `tests/fixtures/compatibility/`. They contain invented values only and are replayed over real loopback TLS and UCP4 framing by `tests/test_compatibility_fixtures.py`. They validate this emulator's local contract, not Protect's behavior. Record live observations from the integration owner's trials in the manifest's live sources, never as fixtures copied from a controller.

Public source packages were AI Key 2.2.8 and Protect 7.2.105 with its matching services. Live trials covered selected behaviors on Protect 7.3.56 and 7.3.60. Three public metadata queries for 7.3.56 returned empty results on 22 September 2026. Do not treat the inspected package as either live-tested version.

The records identify package-relative paths and local analysis artifacts. Vendor binaries and extracted source are not included in the distribution. Before any further investigation, establish authorized access, necessity, scope and applicable terms. A public download link and a matching hash do not establish those rights.
