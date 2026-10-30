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

## Integrated checks and native administrator rollout

Exact source `95bd4a86dd3da82339e40497bd8c97f93e557719` passed 182 focused
checks and the full Mac and Linux suites: **3,065 passed, three skipped**, with
22 existing warnings and 56 passing subtests. All four required CI jobs passed;
the merged main passed them too. A separate same-agent source-derived gate
passed 24 assertions on that clean exact commit, with zero external transport
attempts, no owned fixture residue and unchanged tracked bytes/modes. The first
gate invocation supplied an incorrect SHA and refused before testing; the
observed exact SHA was then used. These are not independent human reviews.

A separate administrator image was built, and all 92 installed modules plus
the license matched that source. The initial non-isolated wheel build lacked
setuptools in the test environment and produced no wheel; the isolated builder
completed without changing the runtime dependency set. A netless temporary
candidate read all four actual Port profiles and eight saved recognition-scope
panels. Anonymous, bad-CSRF and unknown-profile operations refused; no valid
permission/provider/model operation was admitted. The candidate/server were
collected and all seven existing containers, five configs and administrator
authority stayed unchanged.

Only the administrator image was then replaced, retaining direct private-VPN
HTTPS, exact command, healthcheck, mounts, resource/hardening configuration,
existing authority and TLS files. Six private recovery inputs (authority, TLS
and compose) were byte-verified on the NAS and separate SSD beforehand. The old
image remains. The completed service is healthy with zero automatic restarts
or OOM, rejects the old session, accepts the **same** persistent credential,
reads eight panels and signs out. Five profile bytes and six processor/database
container identities/images/restarts remained unchanged. Fresh Protect reads
before, before retry and after match the original one-Key/four-Port identities,
nine pairings and five adopted/connected processors.

The first replacement passed TLS/authenticated reads, then the helper refused
its combined healthcheck/mount comparison and restored only the prior admin
image/compose, against current state. It did not capture which subcomparison
differed. A later never-started disposable-container probe found equal
healthchecks and complete mount descriptors but a different mount-list order;
this is a later observation, not a reconstructed first-attempt payload. That
probe's first create was refused because the copied compose still declared the
production container name; no existing container was changed. Its corrected
unique-name probe was collected without being started. The second replacement
compared complete mount descriptors by destination, retained all other assertions
and passed. First-attempt failure/recovery evidence remains separate.

No processor restart, live permission revocation, new camera/enrollment scope,
credential replacement, model request, private identifier publication or index
cutover occurred. Real browser/mobile/accessibility, owner password-manager
handoff, automated certificate renewal, sustained operation and broader issue
acceptance remain open. This is a narrower rollout than upgrading all processors.
