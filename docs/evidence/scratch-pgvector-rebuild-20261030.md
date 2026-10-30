# Disposable PostgreSQL rebuild qualification — 30 October 2026

Two existing `PgStore` integration cases had remained skipped without a scratch
DSN. They were executed on the NAS against a new, empty PostgreSQL 14/pgvector
instance, not the production database. Source commit
`95bd4a86dd3da82339e40497bd8c97f93e557719` passed both cases:

- interrupted staging resumes without changing live fixture vectors, a changed
  coverage set refuses cutover, then the verified swap and rollback restore the
  expected vectors;
- a committed database swap is recognized after a simulated journal-blind crash
  and remains recoverable by rollback.

The server used only tmpfs, no production mounts or host-published ports, with
Docker networking disabled. The test client shared only that isolated network
namespace and mounted public source/test wheels read-only. Both containers were
collected after the run. All seven existing containers' identities/images/
restart states and five live profile bytes were unchanged. There were no model
calls or non-fixture database contacts. Tests explicitly substitute synthetic
native-approval fixtures; **no actual index migration was approved or applied**.

## Failures retained

The first preparation stopped before starting containers because the NAS's older
Python does not support `TarFile.extractall(filter=...)`. The replacement helper
validated every archive member and exclusively wrote regular, bounded files
inside its new owned stage; it did not fall back to unchecked extraction.

The first actual two-test run failed: `initdb --no-locale` without explicit
encoding created SQL_ASCII, so psycopg returned byte IDs to the text fixture.
The owned database was destroyed and all production checks passed. A distinct
UTF8 fixture then passed **two tests, zero skips**, with original source and
assertions unchanged. This is not a claim to support SQL_ASCII databases or an
observed production encoding failure. Failed receipts remain distinct from the
successful receipt; no failed run was relabeled.

## Continuous verification and safety

The normal CI test job now starts an empty `aikey_rebuild_test` UTF8 database from
the existing maintained pgvector image pinned by digest. Only that disposable
service uses trust authentication. The test DSN uses the runner's dynamically
assigned loopback port; there is no production credential, host, database or
model endpoint. The service is health-checked before tests. Other jobs and
release/secret/dependency checks remain required.

Local integration remains explicit opt-in through `AIKEY_TEST_PG_DSN`. Before
any test DDL, the fixture requires the exact dedicated database name and UTF8
encoding and refuses pre-existing rebuild schema/table state instead of
dropping it. It creates and cleans its own objects. Do not reuse a populated
database or treat the naming check as authorization to access another system.

This proves the exercised synthetic SQL paths, not private media availability,
native cutover approval, retrieval quality, controller/host crash recovery,
power-loss durability, full database restore or independent human review.
Real-CLIP weight-dependent acceptance is separate. The broad migration and
deployment issues remain open.
