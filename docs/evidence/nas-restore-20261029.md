# Isolated NAS restore — 29 October 2026

Partial operational evidence for #24 and #18. Neither issue is complete.
This is a restore of an existing backup into a separate test database, not a
production rollback, live replacement or host-reboot test.

## Isolation and startup

The test used the existing PostgreSQL 14 search image with pgvector 0.8.6,
pg_tokenizer 0.1.1 and vchord_bm25 0.3.0. It had a fresh, separate data volume,
`network=none`, no published ports, an unprivileged PostgreSQL UID/GID,
read-only root, no capabilities and `no-new-privileges`. Only the selected
backup dump was mounted read-only. No second emulated device was started.

The first harness attempt failed before restoring any data: `pg_isready`
accepted the entrypoint's temporary initialization server before the target
database existed. That failure receipt remains retained. It is not evidence of
a corrupt backup. The corrected check waits for both the final PostgreSQL
process and a successful `SELECT 1` against the exact target database. A later
metadata probe also initially ran too early after restart; its stopped-instance
failure is separate from restore success. Neither intermediate failure was
relabeled a passing attempt.

## Observed results

`pg_restore --exit-on-error --single-transaction --no-owner --no-acl` completed
successfully against the isolated instance.

| Restored item | Count |
| --- | ---: |
| Public tables | 6 |
| Image detections / non-null image vectors | 5,745 / 5,745 |
| Search sessions | 13,399 |
| Session objects | 20,930 |

The pgvector distance operator returned zero for a restored vector compared
with itself. This tests vector readability/operation, not retrieval quality.
All four row counts above remained equal after a subsequent instance restart.
They describe the backup snapshot, not an assertion that a later live database
has identical counts. No rows, labels, camera identifiers, media or vector
contents were exported into this report.

Nine selected state files were extracted separately with mode 0600 beneath a
mode-0700 test directory: configuration, controller CA, database credential,
device state, certificate, private key, identity, management credential and
search profile. Each read-back matched its archive member and the corresponding
live file at the time of comparison. Only booleans/counts were reported; no
credential or private identity values were exported. This was **not a complete
state/journal restore**, and the copied identity was never activated.

## Hybrid-search recovery is still incomplete

The restored extension catalog contains zero tokenizer, model and analyzer
registrations. A read-only synthetic `session_tok` tokenization query fails.
Therefore a successful logical restore does **not** establish recovered hybrid
search. The dated 7.3.70 evidence describes controller-owned bootstrap after
reconnect; that bootstrap was not run against this isolated restore and must not
be assumed from extension presence or a WebSocket/SQL connection alone.

The test instances are stopped and their separate volumes and private receipts
are retained. The live Key remained adopted/connected, with connected search
and fresh camera inventory on read-back. No live database, adopted identity,
processor image or camera setting was replaced by this drill.

## Remaining acceptance

- Full state/journal and index-association restore, one active identity only.
- Current-version controller bootstrap plus basic/deep native queries after
  restored deployment reconnect and restart.
- Controlled production rollback/migration, failure injection and host recovery.
- The required 72-hour soak and seven-day pilot with measured quality/resource
  gates. Do not begin them by treating this partial drill as full acceptance.

Private receipts retain backup hashes, exact paths, commands and the unsuccessful
attempts. No production credential or state archive belongs in public fixtures.
