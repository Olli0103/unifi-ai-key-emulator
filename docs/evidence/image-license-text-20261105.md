# Image-license text retention — 5 November 2026

This is evidence preparation for issues #4 and #16, not redistribution approval
or completion of either issue.

## Reproduced evidence loss

The existing image scanner explicitly used `license.content: none`. The reviewed
AMD64 inventory therefore included 64 packages with hash-only license names;
the ARM64 inventory included 61. Other unresolved declarations and packages
without license metadata were separate, not evidence of prohibited licenses.

The pinned, checksum-verified Syft 1.56.0 binary was exercised against one
disposable OCI-layout fixture containing 24 invented package records. One
package had a deliberately unrecognized, clearly fictional copyright notice.
With `none`, the native report and CycloneDX contained only its SHA-256 name.
With `all`, the native report retained its exact text, but the real CycloneDX
converter still omitted it. Merely changing the scanner setting would thus
leave the uploaded review artifact incomplete.

## Correction and boundaries

- Scan with the existing tool's `all` policy; no remote enrichment or new scanner.
- Keep standard CycloneDX unchanged and retain a separate selected package and
  license-text attachment. Do not upload the native image environment/history.
- Deduplicate texts by their SHA-256, preserve declarations without text, and
  refuse a hash-named license whose exact text is missing or changed.
- Bind attachments to source, immutable image, architecture, run, profile and
  both report digests. Finalization rechecks attachment bytes and text references.
- Compare original texts as well as the existing CycloneDX package catalogue
  when a release candidate is rebuilt. A text change cannot hide behind the
  pinned converter's omission.
- Keep small control-receipt bounds unchanged. Larger catalogue files use the
  existing 32-MiB inventory bound with create-only writes and byte read-back.
- Inventory/summary schema version 2 rejects old text-free handoffs. Approval
  flags remain false. No image, model, tag, release or production service is
  published or changed by this correction.

The actual pinned-tool fixture passed full existing subject/format validation,
retained the exact original notice, rejected the old content-disabled report
and excluded native image configuration/history. This is not a review of all
real package obligations. Fresh native AMD64/ARM64 CI artifacts are required
for the actual image review; optional model/detector images remain separate.
