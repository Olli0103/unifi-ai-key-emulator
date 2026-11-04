# CLIP and session-index migration — 3 November 2026

## Scope

This completes the host-operated source/lifecycle implementation for original
[#18](https://github.com/Olli0103/unifi-protect-local-ai/issues/18), within its
explicit public-source/synthetic-fixture handoff boundary. It does **not** claim
that a production index was migrated, that native search quality improved, or
that a host/controller power-loss experiment passed. Native rollout stays with
the integration owner. [Operator runbook](../index-migration.md).

| Original requirement | Implemented and exercised boundary |
| --- | --- |
| Complete model/index identity | Model/checkpoint, tokenizer/preprocessing, modality, dimensions, metric and endpoint fingerprints; separate visual and E5 profiles; runtime query/document identity guards |
| Authorized available source | Exact native object/event/camera/original-crop export; separate frozen session-description export; current explicit camera scope, content hashes, no source substitutions |
| Isolated rebuild and quality before publication | Separate SQL schemas per space; idempotent batches, source-change refusal, complete coverage and frozen staged positive/negative targets |
| Safe cutover | Participating runtime/migration exclusion, fresh matching scratch-verified backup with dump rehash; source/stage digests rechecked under SQL locks; selected vector-column transaction, retained snapshot, recoverable profile handoff |
| Resume, cancel, rollback | Interrupted batch resume; confirmation/OCC-bound idle cancellation; exact prior profile bytes and vectors restored; retained old encoder required for new/changed rows; unknown prior E5 checkpoint never guessed |
| Administration | Separate CLIP/E5 views and counts; progress/uncovered history and historical qualification; fixed-space confirmed cancellation; no model-change-as-index-migration or automatic HTTP apply |

The only supported target spaces here are the existing 768-value paired CLIP
and 384-value E5 session contracts. An arbitrary dimension/model change is
refused, not accepted merely because a vector can be padded. CLIP migrations
persist a mandatory encoding-contract requirement; both query and indexing
clients enforce it. A legacy profile can remain legacy, but cannot authorize a
new migrated runtime to accept an unversioned encoding response.

## Real read-only interface observation

An integration-owner-authorized authenticated controller session confirmed one
exact indexed object/event/camera/cropped-thumbnail association and an original
JPEG crop response. A separate native session-search query confirmed the existing
session response field names and description/cover/representative structure.
The original JPEG and private identifiers/text were not committed or used as
fixtures. No live vectors, model routes, camera grants or controller settings
were changed by this observation.

The implementation uses observed interface facts, not copied vendor functions:

- Basic search uses `searchText` and integer `minSimilarity`; result identifiers
  are `objectId`, `eventId`, `cameraId`, `eventStart` and `detectedAt`.
- A positive Basic result is rejoined to its exact native event on a fresh GET.
  Detection-time filtering is half-open; native time order is not claimed to be
  similarity sorting.
- Session search uses only the observed `query` parameter. It does not invent
  a native minimum-confidence parameter or an unobserved session-detail API.
  Positive sessions are re-read by a fresh search with the exact frozen source
  description hash and camera/time associations.
- Native session negatives report **raw returned rows** separately from those
  above the frozen target score. Low-score raw returns are retained, not hidden
  or represented as UI rejection. Graphical Protect acceptance is not claimed.

## Isolated SQL/TLS qualification

The final public-only candidate was hash-verified on a dedicated fixture host.
The PostgreSQL image was the same digest-pinned pgvector 0.8.6 / PostgreSQL 14
image as CI. A network-none, read-only, capability-dropped disposable container
used tmpfs storage only. Its test client shared only that disposable network
namespace, with public source read-only and no production mounts or exposed
host ports. Encoders, images, session text, account material and controller
responses were independently created **synthetic fixtures**. No model weights,
private media or camera inference were involved.

**13/13 real-SQL/TLS cases passed** on the final candidate, covering:

- image export, interruption/resume, staged positive/negative quality, vector
  swap, pinned-TLS synthetic native readback, exact-event reload and rollback;
- visual SQL coverage, rollback for newly indexed rows and source/rank checks;
- E5-only and hybrid CLIP/E5 migrations, both known and explicitly unknown prior
  E5 checkpoint identities, and a genuinely empty session vector index;
- unchanged visual-vector canary, descriptions, BM25 and unindexed null rows
  during text migration;
- profile-write failure after child publication with vector/root/child recovery;
- failed later native attempt invalidating an earlier pending success;
- post-cutover changed session descriptions blocking unsafe snapshot reuse;
- prospectively persisted quality targets that cannot be changed after a failed
  or successful first attempt;
- source changes requiring a fresh export/new generation; late source/stage
  mismatch refused before the atomic swap; actual session backup count SQL.

The final public source/test bundle SHA-256 was
`7d18aefb4103861ccc1699b89464c249b99562a9cd65ccda83aff40ca3f8dbae`.
Execution began at 20:20:29 UTC; 13 cases passed in 2.39 seconds. Candidate
source/test bytes were compared again after the run and matched. Both owned
containers were removed, no owned residue remained, and all eight selected
production container identity/image/PID/start/restart/config snapshots matched
before and after. This records **non-interference at those boundaries**, not a
universal production-health or independently conducted review claim.

The first six-case attempt is retained as **5 passed / 1 failed**: vectors were
restored, but the old profile was reserialized rather than restored byte-for-byte.
The engine now snapshots/restores the actual bytes; the assertion was not
weakened. Follow-up 6-, 12- and 13-case attempts passed, with isolated resources
collected each time. These were bounded corrections, not production admissions.

## Admin/browser and additional regressions

The checked-in `tools/index_migration_browser_check.py` exercised installed,
sandboxed Chrome at desktop and 390-pixel mobile widths using new temporary
profiles. It checked login, distinct index navigation/row counts, read-only GETs,
required unchecked confirmation, keyboard confirmation of a session-only cancel,
unchanged visual generation/config/profile bytes, no page errors and no
non-owned browser requests. Temporary profiles/servers were collected. Decoded
screenshots were visually inspected separately; this is the synthetic local
admin UI, not a native Protect UI result.

Unit/HTTP/process tests additionally exercise private/no-follow source files,
hash/association changes, scope denial, bounded JPEG decode, wrong checkpoint/
encoding/dimensions, finite vectors, exact profile rollback, same-weight full
identity migration, malformed plans/native sibling responses, stale CSRF/OCC
forms, process-level authority exclusion and fixed diagnostic output.

The first attempted full local suite was intentionally stopped after detecting
nine fixture failures from Apple's `openssl` lacking `passwd -6` (1,305 other
cases had passed). The existing Homebrew OpenSSL and existing portable media
test tools were selected for the subsequent run; authentication code was not
weakened to accommodate the host tool mismatch. Optional host-dependent skips,
complete CI results and release-audit status are recorded with the reviewed PR.
The complete early run then finished with 5,687 passing cases, 14 skips and
nine failures in old exact-response-schema, documentation-help and diagnostic
expectations. Those cases were repaired and the final focused regression group
passed 632 tests, including a failed-first-quality-attempt freeze case. A separate
comparison reproduces the parent configuration guard refusing a valid migrated
profile; the candidate permits unrelated settings without rewriting profiles,
while still refusing encoder changes. A fresh complete run and exact-head CI
are required before merge. A passing audit-only check is not publication/legal/release approval.

## Not established by these tests

- A real controller index migration or a change in recognition/retrieval quality.
- Host power loss, controller restart persistence, a full native device restore,
  or qualification on every Mac/NAS/filesystem combination.
- Independent user installation, third-party review, hardware parity, legal
  clearance, community alpha/beta or a public production release.

No global approval constants were switched on. Arbitrary source/readback
callbacks remain unavailable by default. The concrete reviewed archive/plan
pair, current scope, runtime authority and backup are required at actual
admission. Failed or uncertain native results do not auto-retry or erase the
old snapshot. This implementation does not silently satisfy the separate
native acceptance of Basic/Deep search or recovery issues.
