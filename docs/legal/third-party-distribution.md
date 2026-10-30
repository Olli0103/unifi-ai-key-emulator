# Third-party distribution inventory

This is a scope inventory, not a completed license audit. The existing runtime
CycloneDX SBOM covers its isolated Python environment, not OS packages, optional
extras, model weights, external services or the complete image. Passing a
vulnerability scan is not license clearance. The [core license observations](runtime-license-observations.json)
record 14 historical lock versions from the isolated CI SBOM at run 36674537757,
with the artifact hash and source commit. These are upstream metadata
declarations, not license grants or a reviewed notice/source bundle. Missing
or ambiguous declarations remain open; none of those rows is an approval.

That historical SBOM declares `LGPL-3.0-only` for both psycopg and psycopg-binary 3.3.6.
Those components must retain their own terms; the project's Apache license does
not replace them. Binary wheels also need their bundled native-library review.
Its multidict row contains no license declaration, and aiosignal is represented
by a generic Apache classifier without an exact SPDX expression. The later
exact-image file observation below resolves the missing **file evidence**, not
all distribution obligations.

| Distribution | Declared input | Still required |
| --- | --- | --- |
| Python wheel/sdist | pyproject.toml; requirements-runtime.lock | Exact resolved packages, licenses/notices, source origin and obligations |
| AI Key image | python:3.12-slim-bookworm, Debian FFmpeg/CA certificates, runtime lock | Immutable digest, complete OS/Python SBOM and license/source record |
| AI Port image | Same base plus OpenSSL; optional PyTorch CPU, torchvision and RF-DETR | Audit each build argument/profile separately, including transitive packages |
| Search database image | pgvector:0.8.6-pg14-bookworm plus local scripts | Base, PostgreSQL, pgvector and OS licenses/notices and exact digest |
| Optional local models | sentence-transformers, RF-DETR, ONNX runtimes, psycopg extras | Exact resolved packages, platform/native-library licenses and notices |
| Operator checkpoints | E5, RF-DETR and any face, speech or Qwen artifacts selected by the operator | Model-specific source/version/hash, weights license, use/redistribution conditions |
| External services | Operator-selected API or local model endpoint | Service/API terms; no assumption that an API license permits weight redistribution |

Code and weights can have different licenses. Names such as "Qwen" or
"RF-DETR" are not sufficient evidence for every version, checkpoint or model
family. This PR downloads no model and publishes no image. Operator-supplied
weights remain outside source packages and Docker build contexts.

## Image evidence procedure

For each prospective public image, retain its exact digest and build inputs.
Use an isolated release environment, not production, to collect:

- OS and Python dependency manifests and licenses, including all enabled extras.
- `ffmpeg -version`, `ffmpeg -buildconf` and `ffmpeg -L` from that exact image.
  Establish whether GPL or nonfree components are enabled; do not assume LGPL
  from the package name. Retain corresponding source and build/patch information
  and review notice/source delivery for the included binary and libraries.
- Upstream license texts, required copyright notices, and exact corresponding
  source locations/archive hashes where applicable. A generic upstream link
  alone does not establish delivery of the corresponding build's source.
- A separate model-weight bill of materials if weights are ever distributed.
  A user download instruction still needs accurate terms and provenance.

Prepare a per-artifact third-party notice bundle after this review. Do not add
an empty `NOTICE` that appears to certify a complete inventory. Resolve any
nonredistributable component by changing the public build or withholding that
artifact. Source-only and image releases need separate distribution decisions.

See [FFmpeg's primary guidance](https://ffmpeg.org/legal.html) and each exact
upstream distribution's license. All distribution approvals remain
`needs_evidence` in [release evidence](release-evidence.json).

## Actual Linux runtime license files — 29 October 2026

[Current file observations](runtime-license-files-20261029.json) bind all **15**
current lock versions to the already-built AMD64 image from source `ad4fb8e`,
the lock digest, metadata digests and 20 installed license/notice file digests.
The earlier 14-row SBOM remains historical (including aiohttp 3.14.3); it must
not be described as the current 15-package runtime (aiohttp 3.14.5).

Collection used a temporary network-none read-only container of the exact
retained image, with no state/secret mounts, processor startup or download.
Complete upstream license-file texts are retained in the external receipt whose
digest is recorded. Public JSON contains file identities/hashes, declarations
and observations, not a fabricated reviewer's approval or a complete public
notice/source bundle.

The actual multidict 6.9.1 and aiosignal 1.4.0 license files contain Apache 2.0
text and attribution, even though their metadata lacks an exact SPDX expression.
The same file observation applies to frozenlist; propcache and yarl also carry
separate NOTICE files. aiohappyeyeballs carries PSF and historical Python terms;
do not reduce its full file to an assumed single license. Existing LGPL
psycopg terms are unchanged. These observations do not rewrite upstream metadata.

Still missing: bundled native-library terms and corresponding-source/notice
obligations, optional extras, OS/FFmpeg/base-image inventory, model-weight rights,
complete package/image delivery review and the independent origin/legal gates.
All release clearance fields remain `needs_evidence`; no package/image is
published by this inventory update.

## Optional browser test tooling — 30 October 2026

The `browser` development extra pins `playwright==1.64.1` (Microsoft's
[Python source](https://github.com/microsoft/playwright-python), Apache-2.0
upstream declaration). Its resolved local test environment also contains pyee
14.0.0 and greenlet 3.5.6. These optional tools are not added to the runtime lock,
runtime images or runtime SBOM; this declaration is not a complete optional-
wheel/native-library license audit. Local E2E uses an already installed Chrome
executable with a new empty profile; CI likewise records its installed Chrome
version. No browser binary, profile or model is redistributed by the package.
A separately downloaded Playwright Chromium remains a distinct artifact with
its own exact version, sources and notice obligations if ever redistributed.
