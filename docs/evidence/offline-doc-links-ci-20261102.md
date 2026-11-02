# Offline documentation-link CI gate — 2 November 2026

The existing Python documentation checks validate package CLI argument parsers
and relative file targets, but deliberately skip heading anchors and live
external URLs. A separately qualified one-off lychee audit already checked
local anchors; this change makes that boundary repeatable in the existing
Linux test/build job rather than inventing another Markdown parser.

## Tool and ordering

Maintained Apache-2.0 [lychee](https://github.com/lycheeverse/lychee), version
0.24.2, is installed only on the disposable CI runner. The exact Linux x86_64
GNU release archive is downloaded over HTTPS with bounded connection/total
request time, then checked against the pinned SHA-256 before extraction:

```text
1f4e0ef7f6554a6ed33dd7ac144fb2e1bbed98598e7af973042fc5cd43951c9a
```

The publisher API digest and downloaded 7,626,970 bytes match. This is publisher
checksum/provenance agreement, not independent authentication. The archive's
selected regular ELF member is
`lychee-x86_64-unknown-linux-gnu/lychee`; the first local preflight assumed a root
member and refused before extraction. The corrected workflow selects that exact
member and strips one component. Explicit Bash failure handling stops a failed
download/checksum/extraction before execution. No ignored verification failure
or mutable `latest` tool reference is used.

## Check and counterexamples

The command scans repository Markdown glob inputs, obeying the repository's
existing ignore rules. `--offline` blocks link-check requests; anchor-only
fragments are checked and request caching is disabled. No GitHub/provider token
is supplied to lychee. Downloading the pinned checker is separate from checking
linked content; no linked firmware, model, media or URL artifact is downloaded.

Outside-repository fixtures using the same 0.24.2 options on macOS arm64 verify:

- An existing local file/heading succeeds; two external links are excluded.
- A nonexistent heading fails with exit 2 and one error.
- A nonexistent local file fails with exit 2 and one error.
- A listening numeric-loopback HTTP trap receives zero requests.
- No cache is created; the temporary fixture root and HTTP server are collected.

A separate outside-repository install-order fixture runs the selected Bash
producer/checksum/tar prefix with a local curl transport double. Valid bytes
extract the expected ELF; a wrong digest and failed download stop before
extraction. macOS SHA-256 verification uses `shasum -a 256` in that fixture only;
its BSD `sha256sum` rejected GNU long flags in the preserved first attempt.
No Linux execution is inferred from this platform substitution.

The actual Linux binary/install command remains qualified by the exact-head
CI receipt, not by claiming the macOS binary ran on Linux. The source tree's
current local-only scan also passes; the normal parser/file-link tests remain
separate. No additional workflow job, runtime module, dependency/lockfile,
license, deployed image, service/configuration or camera/model permission changes.

This is not full shell/Docker-command execution, live external-URL monitoring,
remote anchor validation, a fresh installer/native result or human accessibility
acceptance. External URLs remain deliberately excluded from this offline gate.
The broader issue #25 remains OPEN.
