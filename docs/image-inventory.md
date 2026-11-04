# Exact-image distribution inventory (not release clearance)

The native [container installation workflow](../.github/workflows/container-install.yml)
catalogues both default images on Linux AMD64 and ARM64 **before** mounting any
generated device state. It uses maintained [Anchore Syft 1.56.0](https://github.com/anchore/syft/releases/tag/v1.56.0)
(Apache-2.0), not a new package scanner. Its native Linux archives are pinned to
the [upstream checksums](https://github.com/anchore/syft/releases/download/v1.56.0/syft_1.56.0_checksums.txt).
The stdlib-only [coordinator](../tools/image_sbom_check.py) verifies the downloaded
archive bytes, extracts only its single regular `syft` member and checks the
tool's actual version/commit. There is no curl-to-shell, global installation,
third-party Node action, signing key or publishing permission.

## What is tied together

1. The own image tag must inspect as Linux, the requested native architecture,
   UID/GID 10001, the exact source revision, attempt nonce and processor profile.
2. Syft scans **`docker:sha256:...`**, the inspected immutable local image ID.
   The explicit daemon source cannot silently fall back to a registry image.
   Syft never executes the image or mounts a device-state volume.
3. One scan emits Syft native JSON and CycloneDX 1.7 JSON. The native record must
   agree with the inspected image ID, architecture, OS and labels. Its decoded
   image-configuration bytes must hash to that ID. This distinguishes the image
   config ID from a registry manifest/index digest; they are not interchangeable.
4. Both formats must agree on package names, versions, purls and license values.
   OS entries must agree with the native Debian distribution record. File
   entries must reference a native file with matching path and SHA-256; unknown
   component types, duplicate references and missing/extra packages fail.
5. All exact runtime/build-lock Python versions and the local distribution must
   be present without contradictory versions. The OS inventory must contain
   FFmpeg, OpenSSL and CA certificates. Missing packages cannot become zero counts.
6. After all eight install cases and fresh zero-resource cleanup checks, a final
   inventory summary rereads both profile receipts and verifies the actual
   CycloneDX bytes against their recorded SHA-256. Wrong source/nonce/profile,
   altered reports or unfinished cleanup cannot publish a successful summary.

Scanning uses squashed installed-image scope. Remote license enrichment, local
Go/Maven home caches, update checks and persistent scanner cache are disabled;
the scanner receives only the minimal host environment. The owned temporary
native report/configuration is removed after the scan. A child scan has a
five-minute deadline; the disposable VM and job deadline remain the hard
process/disk containment boundary, not a new general-purpose supervisor.

## Public metadata and honest limitations

Each architecture's existing `container-install-*` artifact contains exactly:

- `summary.json`: the complete original install/cleanup result;
- `image-inventory.json`: source, image IDs, architecture/profile, scanner
  version, package counts, missing-SPDX/PURL counts, SBOM hashes and cleanup binding;
- `aikey-sbom.cdx.json` and `aiport-sbom.cdx.json`: the standard image inventories.

No raw native scanner report (which includes image environment/history), image
archive, fixture state, keys, camera media or private logs are uploaded. Only
images built from public immutable source without build secrets are scanned.
The standard CycloneDX files retain public package/file/license metadata rather
than replacing a complete inventory with hand-selected Python dependencies.

`packages_without_spdx_expression` counts packages without a declared recognized
SPDX expression. A nonzero count is **unknown**, not a zero-license obligation,
an allowlist decision or an automatic release failure/approval. Even a declared
SPDX expression is not proof of rights, notice compliance or corresponding-source
availability. Both `licenses_reviewed` and `release_approved` remain **false**.
`packages_without_purl` separately counts missing package URLs. Syft can emit an
empty native PURL and omit the corresponding CycloneDX field; these represent
the same unknown, not a missing package. Such entries retain their exact
name/version/license metadata; no PURL is invented. Present PURLs must agree,
and missing/conflicting required runtime versions still fail closed.
APT versions are observed from each actual image, not asserted reproducible
across later Debian updates. Default-image evidence excludes optional detector,
ONNX and model variants; those need separate inventories and qualification.

## Maintainer's concrete release-review input

For an eventual candidate, retain the exact commit/tag and workflow-run identity,
download both architecture artifacts and verify each SBOM's bytes against the
matching summary. Review the actual OS/FFmpeg build, bundled binary libraries,
Python packages, notices, corresponding source and model exclusions using
[third-party distribution](legal/third-party-distribution.md). Bind that review
to the current source fingerprint in [release readiness](legal/release-readiness.md).
The same commit with later rebuilt images still needs its **actual image** review.

Before any package/image upload the existing **non-audit** community release
check and real maintainer approval are mandatory. Untrusted PR CI has read-only
permissions and no release secrets. The maintainer still chooses destinations
and keyless Sigstore/GitHub OIDC versus a maintainer-held signing key, and performs
the first signed publication. No key, tag, signature, provenance attestation,
publisher workflow or legal verification is fabricated by this inventory path.
An unprotected GitHub environment name would not by itself prove human approval.

The first tagged/signed candidate and interrupted-update/migration acceptance
items in [issue 16](https://github.com/Olli0103/unifi-ai-key-emulator/issues/16)
remain separate. This path supplies a missing engineering input to their review,
not native Protect acceptance or an independent user's installation.

## Reproduce the coordinator contracts

```sh
python -m pytest -q tests/test_image_sbom_check.py tests/test_container_install_check.py
python -m ruff check tools/image_sbom_check.py tests/test_image_sbom_check.py
```

Synthetic metadata/owned fake executables test refusal and finalization; they
are not real image execution. Actual platform inventories require completed
native CI jobs and read-back of their exact-commit artifacts.
