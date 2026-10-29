# Index rebuild: recovery after the profile swap (#18)

29 October 2026. Based on `4acc58264ea2b534ff3d1600c080f4d7f862c995`.
This is synthetic implementation evidence, not native acceptance or approval
to run a migration.

## Reproduced failure

An interrupted cutover could leave the new vectors and new live search profile
in place while the journal still said `verified`. A fresh `Rebuild` rejected
this state as an already-live target revision. The original rollback snapshot
still existed, but the normal generation API could neither finish the cutover
nor start rollback.

The new regression test first failed on the unchanged implementation with
`The target revision is already the live revision`. It interrupts the process
boundary after profile replacement and before the final journal update; it
does not claim to simulate filesystem power-loss durability.

## Change

A verified generation can reopen at its target revision only when its saved
previous profile matches the journal, the current profile exactly matches the
target identity and endpoint, the original snapshot exists, and the live
vectors match the staged vectors. Cutover checks the evidence again before
finalizing the journal. This recovery path rewrites neither the previous
profile nor the snapshot.

The normal source/readback approval gates, a quiesced Key and a matching
verified backup remain required. Neither approval gate is enabled by this
change. An incomplete or contradictory state fails without writes.

## Verification

- One new positive regression reopens from the interrupted state, finalizes
  cutover, verifies that the original snapshot was preserved, then rolls back
  to the exact old synthetic vectors and profile.
- Six new negative cases cover missing/wrong previous profile, missing
  snapshot, changed live vectors, changed live profile and changed endpoint.
- Four new boundary cases recheck Key quiescence, backup verification,
  withdrawn readback approval and a snapshot lost after opening the generation.
  Each negative case compares file bytes and the in-memory store before/after.
- `pytest tests/test_index_migration.py tests/test_index_rebuild.py
  tests/test_index_rebuild_gate.py tests/test_index_rebuild_pg.py
  tests/test_search_backup.py -q`: **71 passed, 2 skipped**. The skips are the
  existing opt-in PostgreSQL tests; no PostgreSQL execution is claimed here.
- Ruff for the two changed Python files and `git diff --check`: passed.

The first expanded test command named a nonexistent
`tests/test_index_migration_plan.py` and collected no tests. The corrected
command above uses the existing `tests/test_index_migration.py`.

## Remaining boundaries

No database, deployment, running image, controller, credential or approval was
changed. No camera media was read. Issue #18 stays open: an approved image
source, native post-cutover readback and real recovery acceptance are still
separate requirements. This fixes one specific process-interruption window,
not every possible cutover/rollback interruption or concurrent-operator race.
