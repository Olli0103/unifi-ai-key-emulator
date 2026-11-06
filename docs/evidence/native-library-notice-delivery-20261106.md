# Native-library notice delivery — 6 November 2026

The four qualified PR305 images retain the Psycopg project license, but the
selected notice evidence lacks multiple original notices from libraries bundled
in the exact official psycopg-binary 3.3.6 AMD64/ARM64 wheels. For example, the
original Cyrus SASL, Kerberos and PCRE notice texts are absent even when searching
all retained Debian and Python notice contents, not just wheel metadata.
This is an observed notice-delivery gap, not a vulnerability finding.

## Supplement and provenance

[Original-file provenance](../legal/native-library-notices-20261106.json) records
35 source files represented by 26 unique byte streams in
[THIRD_PARTY_NOTICES.txt](../../THIRD_PARTY_NOTICES.txt). Exact duplicates are
printed once and all associated origins remain listed. The existing Silero
notice is an unchanged prefix. Each original has a byte offset, length and
SHA-256, allowing independent comparison without stripping whitespace or
reconstructing license text. Only upstream notices and attribution are added;
no upstream executable, model, private camera image or credential is included.

The supplement covers selected Cyrus SASL, Kerberos, libcom_err, keyutils,
libselinux, libxcrypt, PCRE/PCRE2, OpenSSL, PostgreSQL and OpenLDAP originals.
Both architecture-specific package versions are labeled. Distribution packages
sometimes include notices for unlinked components; preserving the full original
file does not assert those components are all shipped in a selected library.

The prior actual-image library hashes match official wheels. For 21 of 31
Psycopg bundled libraries, original package or historical manylinux library
code/data and build IDs were additionally matched. The remaining ten custom
libraries have original upstream build recipes, source revisions and original
notices; no reproducible binary build is asserted. OpenSSL 4.0.2 notices for the
cryptography wheel are similarly tied to declared-version/source evidence.

The separate source acquisition retained 212 exact Debian source versions
(679 files), 29 published Python sdists, the actual Psycopg build's Git archive,
CPython's pinned-base source and selected native-library sources. Acquisition
and hash verification are not a public source offer or completed legal review.

## Existing delivery path

The existing setuptools `license-files` declaration includes the supplement in
wheel and sdist. Both Dockerfiles copy it to `/app`; the installed distribution
retains a second copy. The source/install manifest binds its complete hash, and
the image scanner captures both copies. No alternate installer or new delivery
service is introduced.

The original 256 KiB generic input bound rejected the real 338,252-byte notice
bundle. Only declared license reads now use a finite 2 MiB bound, matching the
existing per-notice scanner limit. JSON, module, path, symlink, cardinality and
release-approval checks remain unchanged. Tests cover a larger valid installed
notice, a changed final byte and oversize rejection at both source and installed
boundaries.

## Scope

This supplement is not complete dependency/model clearance, a license
compatibility decision, source-delivery completion or authorization to publish
images. Existing in-image notices remain required. Optional distributions,
operator model rights and independent origin/release decisions remain separate.
Issue #4 remains OPEN; no production service or camera configuration is changed.

The final release consumer also had a separate integration mismatch: its wheel
allowlist admitted only `licenses/LICENSE`, although setuptools already emitted
the declared supplement. It now requires every explicitly declared, tracked,
bounded root license file in both wheel and sdist, compares complete bytes and
rejects extra undeclared wheel files. Missing/modified notice tests recompute a
consistent wheel RECORD, so self-consistent metadata cannot hide lost notices.
The existing staged release asset path already copies the supplement; its
end-to-end fixture now checks those bytes too. No legal approval or registry
write is granted by these fixture checks.

## Local qualification

The final targeted package/release/container/scanner/documentation group passed
1,005 tests. Actual setuptools 84.0.0 builds produced wheel and sdist; a separate
reader compared all 35 retained original files, 26 indexed byte ranges and the
unchanged Silero prefix against both archives. Each archive contains the exact
338,252-byte supplement, SHA-256
`4e2fa08b987140c5a76d388481b3c742ad2b50e214e91c1322ba64ea3a04f851`.
Ruff and non-notice whitespace checks passed. Original upstream whitespace is
intentionally preserved rather than edited to satisfy diff formatting checks.
Real image CI results must be inspected separately; local package checks are
not image-build or release-approval evidence.
