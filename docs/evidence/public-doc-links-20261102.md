# Public documentation link observations — 2 November 2026

This is a bounded, dated documentation-availability check of source
`88eae79f1c18114b39f6431b950ea78dcd3a4569`, not native feature, install,
security, legal-clearance or broad #25 acceptance. No private runtime or
application source changed for this check.

## Existing maintained tool, isolated use

I used the official Apache-2.0 lychee v0.24.2 Mac arm64 release in a disposable
external directory, not a custom parser or installed application dependency.
The archive matched its publisher's SHA-256 and the binary ran on this host.
That same-provider checksum is not independent artifact authentication. A valid
local anchor passed; a missing local anchor and missing file produced two
refusals before the repository scan. No system/Gateway/scheduler configuration
or CI job was added.

On all 144 public Markdown input files from that source, the offline/cache-off
anchor check reported 551 occurrences: 302 successful, 249 excluded, zero
errors. This is not 302 distinct links or a successful external-URL audit.

## Bounded public HTTPS HEAD results

I extracted public links locally, required explicit public HTTPS origins and
supplied no credentials. Six firmware targets were excluded. The unique-target
HEAD pass used concurrency four, zero retries, zero followed redirects, an
eight-second request limit and a 90-second process bound. It completed in
16.672 seconds; no firmware/model artifact or media body was requested.

| Observation | Unique targets | Meaning |
|---|---:|---|
| Successful HEAD | 92 | Endpoint responded successfully to this request, not content/anchor quality |
| Techspecs 307 redirects | 3 | Not broken links; same-site canonical paths were recorded but not followed |
| GitHub advisory-form 302 | 1 | Unauthenticated form/login boundary, not a broken reporting route |
| Historical OpenAPI 404 | 1 | A separate bounded GET also returned 404; original citation is no longer served |
| FFmpeg legal-page timeout | 1 | Eight-second HEAD timeout; availability and original cause remain unknown |

The five non-2xx responses and one timeout are not a six-broken-link claim.
The old OpenAPI URL is retained as a historical literal in the
[inventory guide](../camera-inventory-preflight.md), with a clearly separate
current documentation entry point. Its obsolete single-version statement is
corrected to the actual reader's four exact supported metadata versions. No
version guard, schema, endpoint, credential policy or historical native receipt
was changed or replaced with a newer-version claim.

[Redacted observations](public-doc-links-20261102/observation.json) and
[digest](public-doc-links-20261102/manifest.json) retain the HTTP methods, source,
tool provenance and exclusions. The follow-up GET read only the 21-byte public
404 body, with a 64-KiB bound and no redirect; it was not a schema/firmware fetch.
No automatic repeat followed the legal-page timeout or redirects. These are
same-agent, one-off observations, not configured CI, independent review,
complete shell-command execution, external fragment validation or a fresh
installation. The broader issue remains OPEN.
