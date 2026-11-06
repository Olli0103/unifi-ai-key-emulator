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
4. Both formats must agree on package names, versions and present purls. The
   **same pinned Syft converter** independently re-encodes the native report to
   CycloneDX; the complete public result must match except freshly generated
   UUID/timestamp. Syft labels conversion experimental, so this exact pinned
   path is qualified and future converter/schema changes fail closed. This
   keeps license expressions, grouping, names and URLs intact rather than
   introducing a home-made SPDX interpreter or treating different encodings
   as different obligations.
   OS entries must agree with the native Debian distribution record. File
   entries must reference a native file with matching path and SHA-256; unknown
   component types, duplicate references and missing/extra packages fail.
   File metadata selection is explicitly **all**, not Syft's default
   `owned-by-package`: the latter omits Python extensions and bundled native
   libraries even when their package RECORD lists them. Every native payload
   listed by the Python cataloger must have a matching actual file hash in both
   formats. A declared RECORD hash alone is not a read-back of installed bytes.
5. All exact runtime/build-lock Python versions and the local distribution must
   be present without contradictory versions. The OS inventory must contain
   FFmpeg, OpenSSL and CA certificates. Missing packages cannot become zero counts.
6. After all eight install cases and fresh zero-resource cleanup checks, a final
   inventory summary rereads both profile receipts and verifies the actual
   CycloneDX bytes against their recorded SHA-256. Wrong source/nonce/profile,
   altered reports or unfinished cleanup cannot publish a successful summary.

Scanning uses squashed installed-image scope. Remote license enrichment, local
Go/Maven home caches, update checks and persistent scanner cache are disabled;
the scanner receives only the minimal host environment. License content is
requested with Syft's `license.content: all`. Its pinned CycloneDX converter
does not carry those original texts, so a separate allowlisted package/license
companion retains available UTF-8 content, deduplicated by SHA-256. A hash-named
license must have exact matching text; missing or changed content refuses
collection. Declared license names may have no original text and remain visibly
distinct from retained text. No SPDX alias or distribution approval is invented.
Full file metadata includes image paths and digests, not arbitrary file content.
Only the existing notice allowlist below requests original contents. These are
controlled clean build images, scanned before private state is mounted; a
production device filesystem is not an authorized input to this public workflow.
Native-payload hashes enable exact wheel/image comparisons, but do not identify
the corresponding source version, establish reproducible upstream builds or
resolve missing license notices by themselves.
The owned temporary
native report/configuration is removed after the scan. A child scan has a
five-minute deadline; the disposable VM and job deadline remain the hard
process/disk containment boundary, not a new general-purpose supervisor.

## Public metadata and honest limitations

Each architecture's existing `container-install-*` artifact contains exactly:

- `summary.json`: the complete original install/cleanup result;
- `image-inventory.json`: source, image IDs, architecture/profile, scanner
  version, package counts, missing-SPDX/PURL counts, SBOM/license-attachment hashes,
  retained text counts/bytes and cleanup binding;
- `aikey-sbom.cdx.json` and `aiport-sbom.cdx.json`: the standard image inventories;
- `aikey-licenses.json` and `aiport-licenses.json`: the selected package declarations
  and available original license texts/files, bound to source, image, native-report
  digest, architecture, run, profile and matching CycloneDX digest.

Inventory and summary receipts use schema version 2; old text-free receipts
are not silently accepted as evidence for this new handoff. Finalization reads
the attachments back and verifies bytes, subject, content hashes and references.
Release-candidate preparation also compares the package/text catalogue against
the qualified installation artifact before admitting a rebuilt image. This is
evidence preservation, not completion of the maintainer's license review.
The license companion now uses schema version 2. Its `notice_files` array retains
exact original bytes (canonical base64, size, SHA-256 and absolute image path),
including non-UTF-8 notices that cannot be represented as license-text strings.
The pinned scanner selects `/app/LICENSE`, `/usr/local/LICENSE`, the standard
`/usr/local/lib/python*/LICENSE.txt` interpreter notice, Debian
`copyright`/common-license files and Python
site-packages files named LICENSE, LICENCE, NOTICE, COPYING, COPYRIGHT or AUTHORS (case
insensitive, optionally with a dot suffix). This includes nested wheel/vendor
license directories, not arbitrary filesystem or state paths. Syft's silent
size skip is disabled: an individual notice above 2 MiB or a complete report
above 32 MiB refuses publication instead. The disposable CI job remains the
scanner's process/memory/disk containment boundary. Known matching native file
records and installed Python RECORD entries must have captured contents.
Direct notice symlinks are resolved through a bounded graph of the scanned
image's own metadata to an already captured allowlisted notice; their effective
bytes are retained at the alias path too. Missing/cyclic/private targets fail.
Empty notices require a regular-file size-zero record and the scanner's actual
SHA-256 of empty bytes; they remain visibly empty, not an inferred license grant.
Finalization validates the original file bytes again, and release preparation
refuses notice additions/removals/content changes against the qualified build.
These selected paths are not proof that every dependency ships all required
notices, nor a corresponding-source or redistribution clearance. Packages
with missing notices and nonstandard paths still require human review.
The [original-file qualification](evidence/image-notice-files-20261105.md)
records the separate pinned-tool controls.
The [pinned-tool reproduction](evidence/image-license-text-20261105.md) records
why the companion is needed and separates synthetic qualification from real
image review.

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
items in [issue 16](https://github.com/Olli0103/unifi-protect-local-ai/issues/16)
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

## Declared project license files

Both Docker build contexts copy the root `LICENSE` and
`THIRD_PARTY_NOTICES.txt`. The latter contains the existing Silero VAD
attribution; it is not a certificate of complete third-party clearance.
The source manifest uses schema `aikey-container-install-source/2` and records
every explicit root filename declared by `project.license-files`, with its
SHA-256. Missing, duplicate, globbed or escaping declarations are refused; the existing
main `LICENSE` remains mandatory.
Before importing the application, the installed-package probe checks the exact
set of installed license files and their original bytes, not just `LICENSE`.
Older version-1 source receipts remain historical and are not upgraded in place.

The image-content allowlist explicitly includes this root attribution file and
its `local_aikey` dist-info license copy. It does not admit arbitrary files
merely because their names contain the word "notice". This repairs a concrete
Docker COPY omission introduced when the VAD attribution was declared; it does
not establish corresponding sources or redistribution approval for other code.
