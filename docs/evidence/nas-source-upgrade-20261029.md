# NAS source upgrade and bounded native checks — 29 October 2026

The existing AI Key was upgraded from the inventory-only r30 overlay to the
reviewed source at `ad4fb8e18390c9aa3ff2aea3cd2bcff44272fe2b` (#123–#144).
This is a private deployment/continuity result, not a release approval, full
restore drill, controller reboot, quality qualification or sustained pilot.

## Candidate and preservation boundary

- Linux AMD64 candidate image:
  `sha256:52e564ef612ffb586f872beb083ad8db76791419a4e06022256e22f0335204e2`.
  Its source label and five actually imported module hashes match the reviewed
  source. The public-source archive contains only the Dockerfile, package/runtime
  metadata and source, not state, media, credentials or model weights.
- The candidate passed a network-less check with read-only existing state:
  configuration ready, adopted identity readable, state schema 1 unchanged,
  compatibility manifest `ai-key/2026-10-29.2`. Runtime versions read back as
  aiohttp 3.14.5, Pillow 13.0.0, cryptography 50.0.1 and psycopg 3.3.6.
- An initial checker omitted Docker stdin attachment and returned no JSON. That
  no-op harness error was corrected, not reported as an application failure.
- All nine configured model-role endpoints remain explicit private HTTP IPs.
  No destination, model, key, camera opt-in, budget, Deep policy or database-major
  change was required. A private IP alone is not a billing/privacy attestation.
- The existing archive's 120,337 replay markers were read with the candidate:
  no unsafe/malformed/stray/temp/reference problems. Their historical completed
  and failed counts are not recognition-quality or all-job-success evidence.

## Backups and transaction

The transaction admitted one attempt after a naturally empty worker queue and
used the old application's graceful drain. After stopping the Key, a logical
dump and before/after database counts matched. PostgreSQL was then stopped for
cold state, physical database-volume and database-secret copies.

| Private backup | Compressed/file bytes | Archive members / regular files |
| --- | ---: | ---: |
| Complete Key state | 14,238,170 | 121,449 / 121,185 |
| Physical PostgreSQL volume | 139,423,255 | 1,372 / 1,343 |
| PostgreSQL secret files | 1,559 | 8 / 7 |
| Logical search dump | 46,340,170 | Not a tar archive |

All three tar/gzip archives were consumed for CRC verification and every regular
member was fully read; none contains links. Independent read-back verified
recorded hashes, sizes, owner and mode 0600. These archives are private and
retained with the old images. They contain sensitive application data and are
not published. No models or original recording mounts were copied merely for
the test. The logical dump was not newly restored in this transaction.

A separate network-less constructor check used 766 selected core/active-journal
files from the state archive. Application construction and the active search
profile validated without changing identity/core files; services were not
started. This is explicitly **not** activation/restoration of the full journal,
archive, database or a second copy of the identity.

The effective Compose comparison permits exactly the Key image delta.
PostgreSQL shares the Key's network namespace, so **both** containers were
recreated together, using the same database image and persistent volume.
User/group, entrypoint/command, mounts, read-only/capability settings, restart
policy and network addresses were checked against the baseline; PostgreSQL's
namespace now references the new Key container. Clip and faces services remain
running; the four existing AI Ports were not redeployed.

No automatic rollback was needed. The prepared fallback preserves the current
compatible state/database/credentials while restoring the old image; it does
not blindly restore old credential files after a possible controller rotation.
That prepared path is not an executed production rollback qualification.

## Independent native results

- Same adopted identity, configuration, certificate, private key, device/database
  credentials and pinned search profile; all core hashes match the baseline.
- All database table counts are exactly equal immediately after the cutover.
  Counts prove only their measured scope, not every row's semantic equality.
- Native Protect 7.3.74 bootstrap: one AI Key and four AI Ports adopted/connected;
  11 cameras, 10 connected. Key inventory fresh with 10 eligible cameras.
- Key control/search connected, no OOM or restart loop. Deep remains disabled
  and both processor and persisted mode remain Basic.
- Native session statistics retain 13,399 sessions. One bounded session search
  returns 30 results in 3,523 ms, HTTP 200. This is availability of stored
  sessions, not labeled relevance or fresh Deep processing.
- A later journal read bound to the **new container's start** observes 16 speech
  and 12 image-description completions. These are callback-completion records,
  not independent transcript/caption persistence or quality checks.

## Image-query observations, not a quality PASS

The configured CLIP endpoint maps to the existing local CLIP image with its
local model mount. Normal authenticated native image-upload/search calls use
the response CSRF header inside the browser; no token, private ID, media,
caption or embedding is exported to the agent or public report.

- A generated 320×240 landscape drawing uploads and searches successfully in
  1,098 ms. At `minSimilarity=70`, 50 results have similarities 70–80. The earlier
  September zero-hit contrast is **not** reproduced on this index/corpus.
- One existing result thumbnail is re-uploaded only within the native
  controller/browser/local-encoder path. Upload/search return 200 in 827 ms;
  the source object is included with similarity 94 and API `order_idx=2`.
  The first result has similarity 77. `order_idx` is ordered, but neither
  similarity nor event-start time is monotonic; ranking semantics remain
  unresolved. This is not silently converted into a top-one PASS.
- Read-only diagnostic requests at 90 return zero drawing results and 32
  positive results with similarities 90–94. This verifies filter behavior,
  **not** a new default, a tuned acceptance threshold or general quality.
- All 6,176 inspected stored image vectors have norms within about 0.000002 of
  one. Non-unit storage is therefore not supported as an explanation here.

No threshold/model/index was changed to make these examples pass. The two
query uploads have controller-managed lifetime; deletion/expiry of those
temporary copies was not verified. Their IDs stay inside the private browser
context. See [retention boundaries](../data-retention.md).

Still open: representative pre-tuning numeric quality gates, complete native
restore/index association and rollback checks, host/controller recovery,
72-hour soak, seven-day pilot and the other intelligence/legacy requirements.
ONVIF remains open by the owner's instruction because no camera is available.
