# Cryptography Rust notice supplement — 6 November 2026

## Concrete delivery gap

The actual PR307 image-bound Cryptography 50.0.1 Linux binaries contain source
path markers naming nine AMD64 and ten ARM64 registry crate versions. Every
observed version matches the published source distribution's Cargo.lock. Their
17/19 original notice files are absent verbatim from all selected Debian/Python
notice contents in each corresponding image, including the PR307 supplement.
The original Cryptography project LICENSE/APACHE/BSD files alone are not the
complete selected dependency attribution.

Path markers are partial static evidence, not a complete linked-library list,
proof of every build feature or a reproducible binary/source build. No downloaded
binary, crate build script or Rust code was executed for this comparison.

## Original inputs

The published Cryptography source distribution SHA-256 is
`5dd9bda1c12b4162f6ff568eeb5e0ff956c28d14406e875cfe8a63a2d414ff20`.
Its original Cargo.lock SHA-256 is
`081ae57e95dabac9f47e2f070d2a3f0e8638ce9a560101b7d459b67cf4481fec`.
All 32 registry archives match the lock's exact SHA-256 checksums; 4,121,466
archive bytes and 62 original notice/attribution files were independently read
back. Eight local workspace crates remain covered by Cryptography's own source
and project notices, not mislabeled as additional downloaded registry packages.

The [component inventory](../legal/cryptography-rust-components-20261106.json)
retains archive URLs/checksums, upstream license expressions, notice identities
and the architecture-specific binary observations. This conservative source
inventory includes build-only and other-target dependencies; it does not assert
that all 32 crates are linked into Linux. Original source-package documentation
notices are retained without claiming those documents are part of the runtime.

## Delivered supplement

[THIRD_PARTY_NOTICES.txt](../../THIRD_PARTY_NOTICES.txt) now represents 97 original
source files as 51 distinct byte streams, with complete source associations and
[byte-range provenance](../legal/native-library-notices-20261106.json). The entire
338,252-byte PR307 notice file remains an unchanged prefix. The new bundle is
482,923 bytes, SHA-256
`d137386494493bc927e8d7d2bc1d6db69bec29edeea9ea9425f2472c458e2dbf`.

The original alternatives are retained, not silently simplified: self_cell is
`Apache-2.0 OR GPL-2.0-only`, not a new GPL-only project requirement;
unicode-ident includes its compound Unicode terms; target-lexicon includes the
LLVM exception. The project license is unchanged. This supplements notices and
does not claim a completed compatibility/legal decision or source offer.

The existing declared-license package/image/verification path is used unchanged.
No runtime code, dependency version, model, service, camera policy, registration,
release-approval field or publication destination changes. Original issue #4
remains OPEN for its remaining actual scope.
