# Search-index generation authority and cancellation (30 October 2026)

This work addresses lifecycle portions of #18 and their administration surface
in #13/#17. It does not approve a stored-object source, enable real rebuilding
or cutover, or establish native model-migration/retrieval quality.

## Reproduced boundary

On parent `b7ea3bc7869a65313bbef380234e201cca5db76f`, an older staging object
can overwrite a committed generation's journal with `staged`, preventing a
fresh process from recognizing the live generation for rollback. An older
verified object can also cut over again after another handle rolled back.
Concurrent staging reaches a second source/encoder path rather than refusing
ownership. Three additional initial regressions expose the absent cancellation
operation. The initial six-case parent run fails all six.

These reproductions open the engine's gates only with fictional test approvals.
No production index was observed corrupt or migrated by these tests; the real
staging/cutover gates remain closed.

## Implementation

- One persistent private state-directory writer lock serializes all generations
  and all library lifecycle mutations, including async staging and rollback.
  Busy calls fail immediately before source/encoder admission. Each owner
  refreshes the durable generation under the lock instead of trusting its
  old in-memory journal. The original source/store interruption propagates;
  task cancellation releases the authority.
- An authenticated, same-origin/CSRF-checked, mutation-limited POST can cancel
  only an existing idle pre-live generation. Its exact journal and live-profile
  revisions and explicit confirmation are required. Generation identifiers,
  schema, states, revision relationships and counts are validated. No request
  selects a pathname, database, model or source URL.
- Cancellation retains staged data and the old active profile/vectors. It
  cannot cancel a committed generation, resume a cancelled one, or replace
  explicit rollback. It refuses an active writer; it does not claim to abort
  already-running inference or terminate another process.
- Bounded no-follow/nonblocking record readers reject special files,
  ambiguous/nonfinite JSON and oversized records. The cancel writer retains
  directory descriptors through reads, replacement and fsync. Changed directory
  aliases before commitment refuse the operation; a change or I/O failure after
  a possible commit reports uncertainty. It does not retry, restore or delete
  an outside directory/canary.
- The browser view remains read-only. Cancelled generations retain visible
  status/coverage but no repeat form. Staging and cutover approval gates remain
  closed; cancellation has no database, image-source or model authority.

The advisory writer lock coordinates these library/API writers; it is not a
permission boundary against a host owner who directly edits files or the
database. Never remove a live lock inode to force a second writer.

## Qualification

Focused lifecycle, administration, auth, OCC, interruption, uncertainty and
filesystem cases, independent cross-process checks, isolated browser scenarios
and exact-source CI results are recorded in the associated PR. PostgreSQL
integration uses only the dedicated empty UTF8 scratch database, never an
adopted device/index. Fake encoders and fictional objects are not recognition
or retrieval-quality measurements. The existing real-encoder optional skip
is not acceptance.

The isolated Chrome flow uses fresh profiles and fictional Key/Port settings:
desktop and mobile width, stale-form refusal, keyboard confirmation/submission,
cancelled state/no repeat form, retained other generations, unchanged live
profile/vectors and closed owned fixture listener. Decoded screenshots are
checked separately from DOM assertions. It does not access the original UniFi
tabs, household camera media, production credentials, controllers or models.

## Remaining #18 acceptance

There is still no approved stored-object image source or native post-cutover
readback contract. Full production migration, real-encoder rebuilding, native
retrieval/quality, controller/host disaster recovery and independent human
installation remain unproved. This change alone does not close #18 or assert
complete AI Key/AI Port parity. No production migration or processor update is
performed by the qualification fixtures.
