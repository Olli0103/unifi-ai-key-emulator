# Preparing and verifying an experimental release

The project is an independent experimental local AI processor compatible with
UniFi Protect. A signed package is **not** native feature parity, a quality
approval, an independent-installation result or a sustained-operation result.
There is no completed signed public release established by this document.

## Versions, compatibility and support

Changes are recorded in [the changelog](../CHANGELOG.md). Development version
`0.1.0` is not evidence of a published/supportable binary release. Use increasing
`N.N.NrcN` candidates and matching public `vN.N.NrcN` tags; a final `vN.N.N` tag
requires the corresponding non-RC project version and actual maintainer release
decision. Version spelling never upgrades experimental behavior into parity.

| Surface | Present qualification | Support boundary |
| --- | --- | --- |
| Python source/package | Python 3.12+ declared; exact CI/test interpreter recorded in each run | No promise for every interpreter/platform combination |
| Linux AMD64 / ARM64 | Native clean installation of both default images; source/version/license, negative controls and state reopening | Container isolation checks, not native Protect/end-user acceptance |
| Apple-container host | Source requires supported Apple Silicon macOS/container runtime; guarded host-actor fixtures | Actual Apple runtime, host networking, adoption and recovery require separate native evidence |
| NAS host | Linux AMD64 image path and source upgrade contracts | No universal NAS OS, reboot, storage or restore guarantee |
| Protect | Bounded evidence is version-specific (including 7.3.60), recorded in individual native reports | Not all firmware/controllers; test the exact target before deployment |
| ONVIF | Local metadata references only | No implemented ingest/PTZ/recording acceptance |
| Models/providers | Explicit operator configuration; no bundled weights | Quality, model rights and all-camera endurance remain separate |

There is **no prior supported public release or LTS window yet**. Future candidate
support is limited to its published compatibility record and reported known
limitations; do not infer a stable API/migration guarantee from `0.1.x`. Before
calling a version supported, the maintainer must record its upgrade/migration,
rollback and platform acceptance and identify the retained predecessor artifact.
Source-predecessor fixtures do not supply that missing public baseline. Review
dependency/model changes on a new release snapshot, retaining lock versions,
actual inventories, original failures and refreshed legal/quality evidence.

## Maintainer decisions, not generated approvals

The [community release gate](legal/release-readiness.md) applies before **any**
package/image upload or signing, including candidate packages in CI artifacts.
Its non-audit check, the reviewed source fingerprint and every required evidence
entry must pass. Existing employer/license-selection statements do not establish
the remaining origin, redistribution, controller, dependency, model or legal facts.

The `publication` object in [release-evidence.json](legal/release-evidence.json)
is deliberately pending. Only the maintainer may record a substantiated decision:

- Exact existing public tag, matching `pyproject.toml` and the dispatched main
  commit. Supported spelling is `vN.N.N` or `vN.N.NrcN` (for example, a hypothetical
  `v0.1.0rc1` requires project version `0.1.0rc1`). No helper creates or moves tags.
- Explicit package destination `github-release` and two distinct image package
  names `ghcr.io/olli0103/<approved-name>`, one for each profile. This source path
  does not implement PyPI publication or choose registry names for the owner.
- Signing method `github_oidc_sigstore`, reviewer `Olli0103`, and a content-free
  reference to the actual retained decision. Automation cannot establish its truth.
- Existing protected GitHub environments `release-stage` and `release`, each
  requiring the actual owner account as reviewer. The gate checks account ID as
  well as login; a name that would silently create an unprotected environment is
  not sufficient. No helper creates environments or changes repository/package
  permissions, visibility, reviewers or protection rules.

Keep private permissions and agreements outside Git. Any tracked release-input
change invalidates the reviewed fingerprint (the evidence record itself is
excluded to avoid self-reference). Complete the actual review on the final tree,
then create the public tag at that commit and wait for the main-push CI runs.
Seven actual jobs must succeed: five security/quality jobs and two native
container-install jobs. A bot status is not an eighth workflow job or independent
review. The current main commit and tag must agree: building an older tag while
attesting a newer workflow commit is refused.

## Concrete workflow

[Owner-gated signed release](../.github/workflows/publish-release.yml) has only a
manual `workflow_dispatch` trigger, restricted again in the gate to the owner,
GitHub-hosted Linux, main, this repository and run attempt 1. It has no push/tag
publication trigger, static registry key, model download or controller action.

1. Select the exact tag with `publish: false` (default) for a read-only readiness
   check. Missing evidence is a failure, not an approval flag that can be bypassed.
2. For an approved request, `publish: true` first waits at `release-stage`.
   Approving this deployment authorizes the dispatch's staging operations:
   draft reservation, candidate CI package uploads, attempt-specific registry
   pushes and OIDC signing. Every stage still rechecks the source/legal/tag/CI
   and environment policy; a JSON handoff is not reusable authorization.
3. The workflow reserves one draft, then makes two clean package builds and
   compares their normalized bytes. The package includes its runtime CycloneDX
   inventory. AMD64 and ARM64 images are built and exercised on native runners
   through the [same clean-install path](container-clean-install.md). All eight
   installed-source/state/negative-control cases per architecture must complete.
   Pinned Syft inventories retain unknown licenses; no legal gate is advanced.
   Their complete package/OS version and raw license declaration catalogs must
   match the digest-checked inventory artifact from the exact qualifying main
   run before any image push. New/different declarations fail closed. This is
   not byte-identical image rebuilding or a license interpretation; configuration,
   layer/location IDs and fresh timestamps differ between builds. Expired or
   missing qualification artifacts must be refreshed before publication.
4. Before registry writes, create-only intents are retained as CI metadata.
   Registry tags include version, architecture/index and GitHub run ID, never
   `latest` or a reusable version-only tag. Registry read-back must identify the
   tested configuration bytes. Standard GitHub/Sigstore actions attest package,
   image and multiarchitecture subjects; no home-made signing scheme is used.
5. The assembly job checks upstream bundles, shipped package source, exact
   image/configuration digests and cleanup proofs, creates two dual-native
   indices, and retains the **complete** candidate before release upload.
   `release-candidate` normally contains 22 files: two distributions, package and four
   image SBOMs, standard signature bundles, `SHA256SUMS`, and `release.json`.
   The workflow summary exposes hashes, including the manifest's own hash.
   The manifest binds every normal asset; the root provenance covers both the
   manifest and checksum file without circular hashing. Verified SBOM predicates
   must equal the distributed SBOM files, not merely carry the right subject.
   If required by the actual distribution review, add reviewed, nonempty tracked
   `THIRD_PARTY_NOTICES.txt` and/or `CORRESPONDING_SOURCE.json` at the source root.
   They are fingerprinted, included in the signed asset set and checksums, and
   never replaced by generated empty placeholders. Source-delivery sufficiency
   remains the reviewer's responsibility, not an assumed generic upstream link.
6. Only then does the final `release` environment request approval. Inspect the
   concrete candidate, immutable image references in `release.json`, hashes and
   workflow identity first. New GHCR packages are normally private: the maintainer
   must explicitly make the two approved packages public and associate them with
   this source repository before approving. This is an owner UI operation, never
   an automatic visibility/rights change. Existing public packages can expose
   stage tags before the final GitHub release; staging approval covers that upload.
7. The final job downloads that same run's digest-checked candidate, takes a
   private byte snapshot, re-verifies signatures/source/visibility and uploads
   create-only assets. Provider IDs, uploaded states, byte sizes and server-side
   SHA-256 digests must all match. Only the same original draft can become public.
   Publication notes and prerelease status are positively read back afterward.

The workflow uses short-lived GitHub credentials, pinned maintained actions and
GitHub OIDC Sigstore. Final publication has no OIDC signing permission: the signed
candidate already exists for review. OCI image archives, native scan intermediates,
fixture configuration, keys, environment/history and model weights are never CI
artifacts. Metadata/candidates have 14-day CI retention; successful public release
assets remain attached to the release. Ephemeral runner teardown is separate from
an observed application cleanup result.

## Verify before installation

Obtain the public tag's full commit and `release.json` from the completed release.
Do not trust a tag spelling, an unsigned checksum file or a green workflow alone.
For example, substituting the actual approved commit:

```sh
gh attestation verify release.json \
  --bundle release-provenance.sigstore.json \
  --repo Olli0103/unifi-protect-local-ai \
  --source-digest ACTUAL_FULL_COMMIT --signer-digest ACTUAL_FULL_COMMIT \
  --source-ref refs/heads/main \
  --signer-workflow Olli0103/unifi-protect-local-ai/.github/workflows/publish-release.yml \
  --cert-oidc-issuer https://token.actions.githubusercontent.com \
  --deny-self-hosted-runners
```

Apply the same policy to `SHA256SUMS`, both distributions, image indices and their
immutable architecture digests, using each bundle named by `release.json`.
SBOM verification additionally uses `--predicate-type https://cyclonedx.org/bom`;
check that the verified predicate is the distributed file. The repository's
`release_publication.py` consumer implements these complete checks, but its write
commands are for the owner workflow, **not** an installation command. SHA-256
asset hashes and OCI configuration IDs are different identities: an image SBOM
identifies the configuration ID, while provenance subjects use registry manifest
or index digests. Never substitute one for the other.

## Interrupted attempts and support boundaries

No publisher deletes public state, overwrites assets, repeats an uncertain push
or automatically retries an interrupted workflow. Run attempt 2 is refused.
A positive exact provider read-back can reconcile a lost acknowledgement without
a second write; incomplete/ambiguous effects remain failed. Preserve the existing
draft, run ID, intent artifacts and any observed registry digests. Do not delete
and recreate them to make a retry look clean. Review the exact stored payload and
owned resources before a deliberate maintainer recovery or a new version/tag.
An existing complete matching upload is read back without another upload;
partial/mismatched assets are refused. Changing file bytes, source, identity,
version, tag, destination or release ID requires a new review, not edited receipts.

Current upgrade/recovery source contracts are described in
[Apple upgrade recovery](apple-upgrade-recovery.md) and
[uninstall/restore](uninstall-restore.md). Source-predecessor fixtures are not a
prior publicly supported release or proof of native Mac/NAS power-loss recovery.
Actual first signed publication, redistribution/legal clearance, native pairing,
quality and long-running/independent-installation acceptance remain separate open
requirements. Report failures through the project's existing issue tracker with
sanitized metadata, not private keys, model weights or camera data.
