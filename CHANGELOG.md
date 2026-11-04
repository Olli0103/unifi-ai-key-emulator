# Changelog

No completed signed public release is established yet. `0.1.0` remains an
experimental source/package version, not a supported public-release claim.

## Unreleased

- Reworked public installation/deployment documentation and removed installation-
  specific camera labels, addresses, user paths and private migration notebooks.
- Generalized readiness output and newly generated LaunchAgent labels. Existing
  loaded agents are not renamed automatically; inspect/unload the predecessor
  before installing one replacement, as described in the Apple guide.

- Added complete local controller-trust, configuration, camera-reference and
  rollback workflows for Key and Port profiles. ONVIF references do not provide
  ONVIF media ingest, PTZ or recording.
- Added guarded index export/rebuild/rollback tooling and explicit benchmark
  attempt accounting. Actual benchmark target failures remain visible; completing
  the benchmark does not certify model quality.
- Added native Linux AMD64/ARM64 installed-source/state checks and whole-image
  CycloneDX inventories with checksum-pinned Syft. Unknown licenses/PURLs remain
  unknown and do not grant redistribution rights.
- Added fenced host-upgrade intent, observed transitions, command-lifetime
  supervision and explicit bounded recovery. Isolated process/source compatibility
  is separate from native Mac/NAS power-loss, restore and sustained-operation proof.
- Added the owner-gated manual publisher, standard GitHub OIDC/Sigstore bundles,
  exact candidate verification and create-only publication/reconciliation. Human
  evidence, destinations, environments and first publication remain pending.

For versioning, actual platform boundaries, verification and interrupted attempts,
see the [release procedure](docs/releasing.md). The open issue tracker remains
the acceptance record; this changelog does not close its native/product criteria.
