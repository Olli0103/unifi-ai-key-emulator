# Third-party distribution inventory

This is a scope inventory, not a completed license audit. The existing runtime
CycloneDX SBOM covers its isolated Python environment, not OS packages, optional
extras, model weights, external services or the complete image. Passing a
vulnerability scan is not license clearance. The [core license observations](runtime-license-observations.json)
record declared lock versions and any matching local package metadata. Missing
or conflicting declarations remain open; none of those rows is an approval.

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
