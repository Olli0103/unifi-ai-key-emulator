# Research provenance

The implementation is independently written from observed contracts. These records document the static investigation, before any emulator code existed. They contain source references and hashes, not a redistributed vendor runtime.

- [Adoption investigation](adoption-evidence.md)
- [Controller callbacks and saving](controller-evidence.md)
- [Search and storage](search-evidence.md)
- [AI Key compatibility matrix](compatibility-matrix.md), generated from the versioned [compatibility manifest](compatibility-manifest.json)

## Compatibility manifest

The manifest records the currently inventoried AI Key profile features as `native-verified`, `fixture-tested`, `implemented`, `unsupported` or `needs_evidence`. Live results are recorded separately for each live trial (Protect 7.3.56 adoption and control, Protect 7.3.60 captions). Static Protect 7.2.105 and AI Key 2.2.8 references never count as native evidence. A feature is `native-verified` only when a named live trial recorded that behavior, and only for that version and condition. A capability flag or a callback HTTP 200 is never sufficient.

`tests/test_compatibility_manifest.py` checks structural consistency: cited tests and records exist, live and static sources stay separate, and the runtime version table in `aikey.protocol` matches the manifest. It cannot prove a claimed live observation. It also scans public fixtures for credentials, real addresses and non-synthetic identifiers. After editing the manifest, regenerate the matrix with `python tests/test_compatibility_manifest.py --write`. Bump `manifest_version` and `aikey.protocol.COMPATIBILITY_MANIFEST_VERSION` together when statuses change.

Synthetic protocol fixtures live in `tests/fixtures/compatibility/`. They contain invented values only and are replayed over real loopback TLS and UCP4 framing by `tests/test_compatibility_fixtures.py`. They validate this emulator's local contract, not Protect's behavior. Record live observations from the integration owner's trials in the manifest's live sources, never as fixtures copied from a controller.

Public source packages were AI Key 2.2.8 and Protect 7.2.105 with its matching services. Live trials covered selected behaviors on Protect 7.3.56 and 7.3.60. Three public metadata queries for 7.3.56 returned empty results on 22 September 2026. Do not treat the inspected package as either live-tested version.

The records identify package-relative paths and local analysis artifacts. Vendor binaries and extracted source are not included in the distribution. Use the linked official metadata and source hashes to repeat the static investigation.
