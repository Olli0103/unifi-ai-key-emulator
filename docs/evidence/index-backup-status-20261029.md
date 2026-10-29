# Backup status boundary for index migration — 29 October 2026

The read-only migration plan previously trusted too much of its newest local
backup record. A `counts` array raised an uncaught exception; boolean counts,
absolute/parent dump paths, symlinks, ambiguous duplicate fields and oversized
records could still report recorded verification or rollback readiness. A bad
newest record was silently skipped in favor of an older apparently good one.

## Corrected read boundary

- Open the configured directory without following its final symlink. Enumerate
  at most 512 backup manifests, retaining the directory descriptor.
- Examine the newest record only. Malformed/unsafe newest state is unavailable,
  not an excuse to silently substitute an older backup.
- Open the manifest relative to that descriptor with no-follow/non-blocking
  flags. Require a regular file, at most 64 KiB, unique JSON keys, an object
  shape, nested count objects and nonnegative integer counts (not booleans).
- Require the exact dated basename and its own matching `.dump` basename.
  Absolute paths, parent paths, symlink dumps and nonregular members cannot
  establish readiness. Non-blocking opens and type checks avoid FIFO hangs.
- Keep raw malformed values and outside names/paths out of the returned plan.
  The signed-in page displays unavailable status rather than a server error.

All operations remain read-only. No archive, live index, vector, identity,
configuration, restore approval or migration gate is changed.

## Verification

Thirteen negative cases fail on baseline source; an already handled top-level
JSON-list control passes. After correction all pass. Two additional FIFO cases
prove the corrected reader returns without waiting for a writer; they were not
run against the blocking old reader. The signed-in page also stays HTTP 200,
does not echo malformed private canaries and leaves fixture bytes unchanged.

The focused migration/rebuild/approval-gate/search-backup suite passes **87 tests**.
Ruff and diff checks pass. Inputs, dump headers, identities and state are invented
fixtures; no production file or database was read or changed for these tests.

## Recorded verification is historical

The page reports **scratch restore recorded**, not fresh dump verification.
It does not hash a potentially large dump or start PostgreSQL restoration on
every GET. Its eligibility/readiness remains conditional on the recorded restore
and live-row comparison; it cannot prove current byte integrity, tokenizer
catalog completeness, native index association or current relevance.

Before actual recovery, re-run the supported backup verification, which hashes
the dump against its manifest and performs the separate scratch restoration.
The [physical cold restore](nas-cold-restore-20261029.md) is separate evidence,
not a property inherited by any arbitrary logical backup. Native migration,
approved rebuild-source coverage and full restored-device acceptance remain open.
