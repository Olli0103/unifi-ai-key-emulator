# Remove-only saved caption camera controls — 31 October 2026

This is a bounded part of issues #13 and #17; their wider scope remains OPEN.

## Implementation boundary

The existing ConfigurationStore lock, exact old-byte history, optimistic revision
and immutable identity/search/credential-route guards protect new typed preview
and removal operations. Only an existing continuous camera allowlist may shrink;
full disable removes that continuous section and leaves other raw fields and
omitted defaults intact. A no-op retains exact bytes. No provider undo is created.
Post-publication failures remain uncertain rather than being reported as a
pre-write refusal. No journal read, reset, refund, repair or prune is performed.

The server-rendered UI uses fresh validated inventory and HMAC-bound opaque camera
choices. Preview authority is session-digest/revision/result-bound, single-use,
monotonic ten-minute expiry, and bounded to 32 live grants without evicting valid
ones. Restriction confirmation rechecks inventory identity/name/model and the
saved revision; full disable does not need inventory. Exact-field checks, existing
CSRF/Origin/session authority, shared admission and shielded/drained file writes
apply. No raw camera identifiers or protected paths are returned by the new form
or aggregate read-only JSON route. Errors do not echo untrusted diagnostics.

## Qualification

Candidate verification includes exact-one-field raw scope reduction, separately
confirmed full disable, byte-preserved full 12-reservation ledger, private exact
history, no-op/order, model-wide policy refusal for individual edits, stale and
malformed selections, fresh inventory/name/MAC changes, replay, expired/foreign
sessions, logout while awaiting preview, concurrent confirms, admitted-write
cancellation/cleanup, bounded grants and committed-but-lost acknowledgement.
Real sandboxed Chrome desktop/mobile/keyboard fixtures exercise retained named
cameras, missing consent, full disable, expiry and private DOM/storage/network
boundaries. No running Key, model/provider or controller is started by fixtures.

33 new boundary cases and 170 focused checks passed; Ruff and diff checks passed.
Actual sandboxed Chrome desktop/mobile/keyboard flows passed. Decoded mobile
initial/review and desktop saved states were inspected; browser-only scroll/paint
synchronization was corrected before the final capture. All application modules
match the source hashes in that browser result.

Frozen full suite, CI, package provenance and native Admin activation are pending;
fixture success is not deployed or real revocation evidence. Existing native
camera permissions and the household configuration have not been changed.
