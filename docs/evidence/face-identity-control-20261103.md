# Private local face identity control — 3 November 2026

This source checkpoint adds a signed-in list and targeted deletion for the
**AI Key's existing local 128-value SFace identity store**. It builds on the
[transaction foundation](face-store-transactions-20261103.md). The broader
[face lifecycle issue](https://github.com/Olli0103/unifi-ai-key-emulator/issues/20),
[GUI issue](https://github.com/Olli0103/unifi-ai-key-emulator/issues/13) and
[configuration issue](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17)
remain OPEN; no native acceptance box is completed here.

## Operator flow and scope

Settings links to **Local face identities** (`/face-identities`). The list reads
one exact saved record and displays only identity labels and template counts.
An unavailable/unsafe record is unknown, not empty or a zero count. Missing
read-only state remains empty without creating a directory, lock or file. This
does not establish whether a running worker uses this root or enables faces.

**Review deletion** opens a preview of exactly one identity and its saved count.
No deletion occurs on GET or preview. A separate initially unchecked consent
checkbox and POST removes all saved templates for only the selected identity.
The other identities and all Key/Port configurations remain untouched. A saved
success redirects to the refreshed list; no identity or confirmation is in the
redirect URL. There is no automatic undo copy or restore/retry operation.

This is not face enrollment, native Protect group management/search, 512-value
AI Port recognition, cross-event ReID, model-space migration or permission
activation. Existing CLI enrollment remains separate. No images, vectors,
created timestamps, paths or model data are rendered by the new endpoints.
The authenticated read-only JSON route `/api/admin/v1/face-identities` declares
`local_aikey_sface128` and `live_worker_state: not_asserted`; it returns only
the revision, labels and counts, never vectors. These labels are private
operator data, not a public diagnostic/export.

## Authority, races and uncertain outcomes

- Reuses the listener's Host/session and mutation Origin/CSRF/shared-rate checks;
  forms accept only exact single-valued URL-encoded fields and no query options,
  arbitrary root, profile or direct user-supplied identity name.
- Choices are opaque HMACs bound to session, exact record revision and identity.
  Preview issues a private server-side, session-bound one-use grant for at most
  ten monotonic minutes. At most 32 live grants exist; live grants are not evicted
  to make room. Process restart loses grants rather than reviving them.
- Apply rereads the exact revision/count and rechecks session, expiry and grant
  identity after the awaited read and private audit. All sibling grants are
  consumed before admitting one operation, with no intervening await. Restoring
  old exact bytes cannot revive an admitted preview.
- `FaceStore.delete(expected_revision=...)` rechecks the same record inside the
  stable writer lock. Another enrollment between HTTP observation and write is
  a conflict, not permission to discard the new enrollment.
- Configured audit failure before admission refuses the deletion. Fixed audit
  action/result/profile fields never include labels, vectors, paths, CSRF or
  grant tokens. A post-commit audit/readback/filesystem failure is an explicit
  uncertain outcome, never a success or automatic retry/restore.
- Reads use one retained, shielded file-operation task with a three-second HTTP
  wait. A timed-out/disconnected waiter does not queue more snapshot threads.
  The existing AdminAPI owns and drains reads/admitted writes; cancellation of
  a client does not pretend a running filesystem commit was cancelled.

The audit is optional under the existing application policy. This change does
not make a new always-on audit service or claim protection against a privileged
host owner, noncooperating/old writers or filesystem stalls during shutdown.
Deleting local saved templates does not erase recordings, historical Protect
events, native groups, backups, enrollment-server copies or an in-flight match
that already read the templates. The UI states these limits before consent.

## Verification and preserved corrections

All identities, vectors, state roots and audit rows used here are fictional.
Loopback HTTP tests cover targeted deletion and exact sibling/configuration
preservation, escaped labels, metadata without vectors, missing/unsafe state,
strict forms, session/Origin/CSRF/Host checks, stale/cross-session/expired grants,
post-await authority changes, concurrent admission, exact-byte restoration,
under-lock concurrent enrollment, configured audit failures, uncertain commit,
single-flight read timeout/cancellation and detached admitted-write completion.

The isolated Chrome runner tests desktop 1200×900 and mobile 390×844, sandbox
enabled. It exercises keyboard review/consent, cancel, actual target deletion,
stale concurrent enrollment refusal, 64-character unbroken labels without body
overflow, unchanged configuration, empty browser storage and redacted audits.
Every browser request is restricted to its owned loopback server; resources and
temporary profiles are collected. Four actual identity/confirmation captures
were inspected, not just DOM assertions. No real mobile-device, screen-reader,
native worker, biometric quality or full-product accessibility acceptance is
claimed. The runner itself records visual review as not performed by runner.

An initial test selection named a nonexistent generic admin test module and
ran no tests; the corrected selection uses discovered actual module names.
The initial 43 HTTP cases passed. Follow-up checks cover session expiry during
audit, shared mutation limits and an actually refused symlinked audit target
whose unrelated fixture bytes remain unchanged. The final **49 HTTP cases**
pass. A combined focused/store/admin/docs selection passed **584 cases** before
the final two audit-target cases were added; it is not a whole-tree Mac run.
Whole-tree Ruff and diff checks pass. Linux results are recorded in the
associated PR, not inferred from an earlier head.

No production identity, photo, template, controller/camera/provider request,
model server, credential, configuration, permission, service/image, scheduler,
deployment or public release changed. Broader native enrollment/grouping/search,
retention, false-match/unknown quality and model migration remain unqualified.
