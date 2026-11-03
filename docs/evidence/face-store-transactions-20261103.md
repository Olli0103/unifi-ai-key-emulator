# Local named face-store transactions (3 November 2026)

Foundation for the private named-template lifecycle in #20 and administration
#13/#17. Synthetic names and 128-value vectors only; no real image, identity,
model, template store, controller or provider is read or changed.

## Reproduced defects

Four cases fail on the unchanged parent module from
`9d65da93f5a6afeb1e0385c186ad1e42296847af`: two overlapping enrollments both
report success but the first paused writer erases the second identity; a
symlinked `faces` directory is followed during names, enrollment and purge.
The latter can read, overwrite or unlink an external fixture record. These
are controlled temporary canaries, not claimed production compromise/data loss.

## Transaction and storage boundary

All new enrollment/delete/purge writers share a persistent private
`.face-store.lock` under the existing state root, using the maintained bounded
advisory lock (five seconds). Open records/directories are descriptor-anchored;
state/face roots must be owned directories, state must not be group/world
writable, faces must be private. Final roots, records and lock links/special
files/unsafe permissions are refused, never silently chmodded or repaired.
The state root must already exist for writes; a missing root is not silently
created as a new application state. Missing read-only state stays empty and
creates no directory or lock.

Writes use an exclusive private temporary under the opened face directory,
file fsync, descriptor-relative replacement, directory fsync and exact byte
readback. Purge verifies unlink by readback as well. Only a temporary actually
created by this writer is eligible for cleanup; a collision never deletes the
other file. Directory rebinding cannot redirect the descriptor-relative write
into the replacement target. A possible committed write/failed persistence
acknowledgement or post-write root change is explicitly uncertain, retained
for reconciliation, never automatically retried or restored.

`snapshot()` reads one captured validated record and returns private labels,
per-identity template counts/creation times and an exact byte revision, never
vectors. Existing enroll/delete/purge calls remain available; optional
`expected_revision` adds an under-lock stale-save refusal for administration.
The missing-store digest is distinct from a saved empty record. Exact-byte
state cycles can share a digest; a future GUI still needs its own one-use
confirmation authority. This is not a monotonic revision database.

The JSON schema, maximum identity/template counts, SFace dimension/normalization,
matching threshold, unknown behavior and enrollment transport remain unchanged.
This store is not the AI Port's separate 512-value recognition space. Explicit
CLI list remains an owner-invoked private-name output, not an ordinary log.

## Qualification

79 focused transaction/private-reader/enrollment-transport checks pass, plus
437 documentation/compatibility and whole-tree Ruff/diff. Tests include two
actual simultaneous Python processes retaining ten templates, real bounded
lock contention, stale enroll/delete/purge, no-vector snapshot, unsafe modes/
links/FIFO, missing root, corrupt-owned explicit purge, temporary collision,
pre-/post-commit fsync failures, changed-root descriptor anchoring and exact
write/unlink readback. Child stdout/stderr are empty and children collected.
No whole-tree Mac or real-encoder/native acceptance is claimed; Linux CI supplies
the existing dedicated media tests.

An existing growth-after-fstat fixture initially appended for every newly
opened directory descriptor too. It is now restricted to its intended exact
record inode; the original size/growth refusal remains. The first expanded run
was 58 passes/one fixture mismatch, not a new production defect. No production
validation guard was weakened to accept it.

## Limits and operation

Locks coordinate this version's cooperating local writers, not old tools that
ignore the lock or a privileged host owner. Never remove/replace a live lock
inode to force admission; upgrade every writer before relying on serialization.
No claim of audited ancestor traversal, hostile-owner isolation, physical erasure
or universal power-loss durability. In-flight recognition may retain an already
read match; historical Protect events, native face groups, original recordings,
backups/server copies and private abandoned temporaries after process death
need their own retention handling. Purge is not a recursive cleanup.

No live application image/configuration, recognition policy, camera permission,
index, service, model, credential, scheduler or legal/release gate is changed.
Full native face lifecycle, false-match/quality, model migration and broader
issues remain OPEN. Final source and exact CI results are recorded in the PR.
