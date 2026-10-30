# Startup file provenance — 30 October 2026

## Scope

The Key CLI and Port CLI now retain a digest of the very same bytes they validate
at process start. The Key uses a bounded, no-follow, nonblocking regular-leaf
snapshot before validation; the Port uses its existing private 4 KiB snapshot
and pure parser. Neither reopens the configuration path to calculate a supposedly
loaded revision. Normalization and referenced-secret hydration happen separately.
Programmatically constructed services without provenance explicitly report null.

The versioned health observation is `aikey-startup-configuration/1`, scoped to
`file_bytes_at_process_start`. The authenticated runtime API validates its exact
shape, revision, schema and scope. Existing CA-plus-leaf pinning, bounds and
single-flight two-second health caching remain. Saved bytes are read freshly,
even when health is cached. The read-only Runtime configuration page uses that
same API implementation and distinguishes match, mismatch and unknown.

This is a prerequisite for a future verified restart, **not a NAS restart
adapter**, effective-policy attestation or proof that new settings are active.
No maintenance purpose or host execution authority is added. Matching bytes do
not attest referenced keys, trust files, model weights or current controller
state. Formatting and controller-managed saves can also change bytes. Missing
legacy provenance remains unknown. No adoption, privacy scope, provider,
threshold, pairing, recording or PTZ setting is changed by these reads.

## Qualification

Focused configuration, CLI, runtime, TLS, API, saved-camera binding and page
checks cover descriptor/path replacement (including FIFO and symlink), frozen
startup reports after saves, malformed/future/extra-field claims, fresh saved
reads under cached health, null legacy reports and expired/revoked sessions.

A source-bound actual Chrome run passes desktop 1200×900 and mobile 390×844:
match, formatting-only mismatch, explicit legacy unknown, keyboard-focusable
comparison region, no document overflow, no settings POST, empty browser storage
and session expiry. Four actual captures were inspected (desktop match/different,
mobile different/unknown). All profiles and loopback servers were collected.
These are synthetic fixtures, not the actual NAS-client GUI or human acceptance.

A separate outside-repository same-agent harness starts two actual passive Port
CLI processes with fictional configuration and own TLS. Seven checks verify
exact startup health, unchanged reports after later saves, changed startup digest
on a new owned process and unchanged identity/TLS bytes. Both processes are
collected. A transport guard blocks two non-loopback control-connection attempts;
none completes. Passive/unadopted does not mean no connection attempt. There is
no real controller, model or media operation and no claim of a live Port restart.

Initial fixture failures are retained privately: the new Key fixture lacked
required CA/origin/model setup; the browser fixture lacked its explicit port;
the process harness initially assumed passive meant zero connection attempts.
Fixture setup/assertions were corrected, not product TLS or validation guards.

Full Mac and exact-head CI results are recorded separately in the PR receipt.
No public binary/image or running-service update follows from a source merge.

## Separate native status source limitation

A bounded read using the existing scoped Integration credential, pinned before
all credential headers, observed Protect 7.3.74: metadata 200, camera list 200
with 11 entries, one observed-camera detail 200. Neither the list nor that detail
contained recording/PTZ fields. The native camera list refused that credential
with 401; an earlier native bootstrap returned 500, not a proved login failure.
No new account or copied cookies were used. The saved key stayed inside the
Admin container. No recording/PTZ changes or media reads occurred.
This does not establish that every native account or other
possible source is unavailable. The broader native recording/PTZ criteria in
#13 and #17 remain OPEN.
