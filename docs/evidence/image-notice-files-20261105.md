# Original image notice files — 5 November 2026

## Observed gap

The previous image-bound companion preserves Syft license-object contents.
That is not the same as retaining original LICENSE/NOTICE files: Syft 1.56.0
omits a declared MIT package's original file from CycloneDX even when its
installed Python RECORD lists the file. An official wheel with the same
name/version is not sufficient evidence of the image's original notice bytes.

## Pinned producer qualification

The existing Syft 1.56.0 binary (commit
`173055c39e9752f7b450ea404cd1801695277426`) scanned two nonbootable, wholly
fictional OCI fixtures. No real processor firmware or camera data was used.

- Normal control: three selected notice files retained, including the Python
  package notice. That original file remained absent from CycloneDX.
- Python original SHA-256:
  `0cc6202df870363f1f3351176ba3d25a8d86309fed0ea6b0ce72f24a140018fe`.
- A `/state/LICENSE` canary and package `private.env` were not selected.
- Oversize control: a 2 MiB + 1 byte matching notice was read by Syft and refused
  by the consumer (`image_inventory_notice_files_refused`), not silently omitted.
- Both fixtures had the original notice in Python RECORD, independently
  exposing missing captures to the consumer.

Native report hashes (private reports also contain scanner/image configuration):

| Fixture | SHA-256 |
| --- | --- |
| Normal | `d25f9e8e3cea6bd9fdd0cf496b180ea91d63e9edd59e312ff06648c2253fb550` |
| Oversize | `5740e16801ab5fae58d92d8bf750c41c415989f1746422f1f23183c9d0e923a2` |

The pinned producer's serialized configuration does not accurately echo the
file-content glob settings. The wrapper supplies its own fixed config and
validates actual captured paths/bytes; it does not infer capture from that
misleading descriptor. Setting `skip-files-above-size: 0` disables silent
omission in this exact producer. The bounded report reader, per-file rejection,
and disposable CI job remain in force.

Two additional real-scanner controls passed: a Debian documentation-directory
symlink resolves to the original notice without duplicate-path errors; exact
`/app/LICENSE` and `/usr/local/LICENSE` entries are retained alongside the three
package notices. The final control's native report SHA-256 is
`8062e7e33d0add6d49677fd417e0eb3189a5f6c5dfb2dd98ad5ff4011e7dbc9b`.

Local verification: 984 relevant tests passed before adding the two exact root
paths; the final focused group passed 145 tests, including those paths. Ruff and
diff-whitespace checks passed. These are distinct test runs, not additive totals.

## First real image review and attribution correction

Native installation run `42126473380` passed both architectures at head
`0d5e83a780aa83c2e4d7ba932c9a803473b32eb1`. Independent artifact SHA-256,
six-file membership, source/merge-tree, install/cleanup receipts, companion
byte validation and existing CycloneDX file-hash comparisons passed. Each
AMD64 profile retained 348 notices; each ARM64 profile retained 345. The exact
root project license matched the checkout; all four images retained 40 Python
notice files, including nested setuptools vendor notices.

The official-wheel comparison exposed a remaining selection omission:
pip's MIT notice explicitly refers to AUTHORS.txt, which was not selected.
The correction adds AUTHORS names and the standard Python interpreter notice
path, `/usr/local/lib/python*/LICENSE.txt`. A fresh real-scanner fictional
control retained all seven expected notices; native report SHA-256
`05830d19f6725cca7aa204801067513401cb9d0cbd1b20775e7be6e12084df10`.
The final focused plus documentation group passed 778 tests. Fresh image CI
is required for this corrected source; the first build is not relabeled.

## Scope and limitations

The companion changes to version 2; old companions cannot pass as original-file
evidence. File identities and exact bytes participate in the release candidate's
comparison before any registry action. Legal approval booleans remain false.

This fixture qualification is not a real AMD64/ARM64 image-build result, legal
clearance, complete bundled-library notice review or source-offer proof. Real
image artifacts must be inspected separately after CI. No production service,
model, camera, registry or adoption changed.
