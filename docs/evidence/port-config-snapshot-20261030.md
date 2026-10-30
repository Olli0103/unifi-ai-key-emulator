# Coherent Port administration configuration reads — 30 October 2026

The Port administration store read and checked a bounded private descriptor for
its revision, then called the runtime loader on the pathname again. An atomic
configuration replacement between those operations could return the old
revision with settings parsed from the new file. A synthetic two-valid-version
case reproduced this: revision A was returned with threshold B. Replacement by
a link, FIFO, invalid or oversized file could instead reject an already captured
valid snapshot.

The existing runtime validator now has a byte-snapshot entry point. The runtime
file loader still acquires its private input and delegates to that validator;
the administrator delegates its already checked bytes directly. The same bytes
now determine revision and parsed settings, without reopening the configuration
path. A later read sees the later version; revision checks still reject a stale
preview/apply. Decoder validation and identity/trust reference-file checks remain
in the same state directory. Provider previews validate encoded bytes directly,
so there is no temporary validation file or newly invented validation schema.

This uses the existing parser and file/transaction primitives, not a new library
or service. It does not change provider keys, identities, camera policies,
permission grants, normalization, model calls, history or apply locks. No live
configuration or container was changed to reproduce or fix it.

## Reproduction and limits

The initial race fixture accidentally used a one-camera pool rejected by the
existing two-to-five-camera schema. It was corrected to two valid configurations
differing only in threshold, retaining the original assertions. The corrected
old-source baseline had five failures: one mixed-revision/settings result and
four pathname-replacement refusals. No validation rule was weakened.

Tests cover replacement at acquisition boundaries, later revision visibility,
stale-preview refusal, unchanged canaries/history, bounded input rejection before
reference reads, continued private-reference checks and no validation-file
creation. Full-suite and exact-commit gate results are recorded at integration;
these fixtures do not establish a native deployment or broad issue completion.

The captured byte string is the unit of validation; this is not a filesystem
snapshot, protection against hostile same-inode writers/ancestor retargeting,
or a promise to synchronously revoke admitted work. Referenced identity/trust
files remain separate existing checks, not a multi-file atomic snapshot. No
new real-browser, native permission apply/restart, recovery, quality or independent
human acceptance is inferred. Broad security/administration issues remain open.
