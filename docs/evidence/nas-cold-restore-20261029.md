# Isolated cold database restore — 29 October 2026

A subsequent [complete Key archive/component restore](nas-full-state-restore-20261029.md)
adds full-state byte comparison, idle-worker/HTTPS startup and replay-state checks
against a separate restored database, still without native controller attachment.

The physical PostgreSQL backup from the [current-source NAS upgrade](nas-source-upgrade-20261029.md)
was restored into a **new** disposable database volume. This is a completed
isolated database/credential test, not activation of a second adopted AI Key,
production rollback, controller/host reboot or full native restore acceptance.

## Isolation and actual procedure

- Same retained PostgreSQL image as the original backup; no database-major
  upgrade or extension installation. The active data volume was never mounted
  by the restore helper or test container.
- Archive hashes, allowed regular/directory members and safe relative paths
  were checked before extraction. The recorded database secret-file archive
  was restored into a separate private directory, not over active secrets.
- Test database: network `none`, no host ports, read-only root, 1 CPU and
  1.5-GiB memory cap, private volume and secret mounts. PostgreSQL actually ran
  as its non-root postgres user. The image's normal entrypoint was retained.
- The **test-only** peer allowlist allowed loopback inside the isolated network
  namespace so a short-lived client could authenticate. The production
  allowlist, controller trust, device identity and configuration were unchanged.
- The first extraction failed because the restricted helper lacked `FOWNER`
  to restore a directory's mode. PostgreSQL had not started. Its receipt and
  partial test volume are retained. A separately named second attempt added
  that capability only to the extraction helper over the new restore targets;
  the database itself did not receive a new capability override.

## Observed results

| Check | Result |
| --- | --- |
| All public table counts, embedding count and dimensions | Exact match to cold-snapshot manifest |
| `tokenizer_catalog.model` / `tokenizer` / `text_analyzer` | 1 / 1 / 1 |
| `tokenizer_catalog.stopwords` / `synonym` | 3 / 0 |
| Synthetic `session_tok` query | Succeeds; result array cardinality 384 |
| Stored vector compared with itself | Cosine distance 0 |
| Recorded database password and CA | Authenticated `SELECT 1` over verified TLS |
| Deliberately wrong synthetic password, isolated copy only | Authentication refused |
| Restart of isolated PostgreSQL | Counts, catalogs and synthetic query unchanged |
| Independent final read-back | Test stopped, restart disabled; active Key/PG running and config hash unchanged |

No credential value, verifier, private row, media, transcript, embedding or
controller identifier was exported. The client used only the recorded config,
database password and CA files, not an AI Key service or device private key.
A separate in-process comparison before this test also established that the
recorded database password matches the adopted management credential's existing
verifier; that is not a new controller-side password rotation.

The earlier [logical restore](nas-restore-20261029.md) restored public rows but
left tokenizer registrations empty. It remains an accurately recorded failure
for that different backup path. This physical backup **does** preserve those
registrations and execute the synthetic tokenizer query. A tokenizer query and
self-vector distance are not labeled search quality or end-to-end native search.

## Remaining acceptance

The test container, failed first-attempt artifacts, separate volumes, protected
archives and old images are retained; no model was invoked, no live database
was replaced and no second device identity was advertised. Full journal/archive
activation, native controller association after restore, production rollback,
power-loss/host recovery and soak/pilot qualification remain open. These results
do not turn a database-only drill into a full restore PASS.
