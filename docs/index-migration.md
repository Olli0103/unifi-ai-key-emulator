# Search-index migration

This is a **host-operated** migration, not a model picker or an unattended
service manager. The admin page shows both index spaces, coverage, historical
qualification and an explicit cancellation form. It cannot stop the Key, change
encoder routes, cut over, restart a controller or claim a fresh quality check.

| Space | Source / live storage | State and staging |
| --- | --- | --- |
| `clip-basic-v1` | Authorized original Protect object crops; `ramDetections.embedding`, 768 values | Key state; `aikey_rebuild` |
| `e5-session-v1` | Existing authorized descriptions; `smartDetectSessionsSearch.descEmbedding`, 384 values | Key state `/session-search`; `aikey_session_rebuild` |

Each profile binds model, exact checkpoint/tokenizer-file revision, declared
preprocessing/tokenizer contract, modality, dimensions, distance metric and
endpoint. A profile fingerprint binds the complete identity. The metadata is a
supported-protocol declaration, not independent attestation of a remote server.
A preprocessing/endpoint change can require migration even with identical
weights. Visual analysis/caption-model selection does not itself migrate either
index. Unknown old E5 revisions remain explicitly unknown; they are never
relabeled as a known checkpoint.

## Before admission

1. Review the candidate source, current configuration, native controller version
   and existing camera permissions. Use the optional `database` dependency.
   No service, controller, provider or camera-scope changes are implicit.
2. Upgrade to the reviewed participating Key runtime **before** scheduling a
   swap. It holds `.search-index.lock` throughout query/index-worker lifetime
   and publishes a device/inode-bound participation marker. Run the operator
   command and runtime against the **same qualified local filesystem/namespace**.
   A legacy runtime, missing/replaced lock, failed health GET, container listing
   or manually supplied `true` is not proof of quiescence. Never create the
   marker by hand or delete/recreate the lock. Do not run an old nonparticipating
   runtime alongside this workflow.
3. Provision a separate target encoder yourself; retain the old checkpoint and
   its complete encoding contract. The tool neither downloads weights nor
   starts/stops services. CLIP must report the exact revision and encoding;
   the local E5 HTTP server must report its exact artifact revision in health
   and embedding replies. `vision_server --text-dir` reports a digest of
   `e5.xml`, `e5.bin` and `tokenizer.json`. A legacy unversioned E5 service is not
   an admissible target. Keep target service access private.
4. Use an explicit local Unix-socket peer connection or private-IP PostgreSQL
   with verified TLS and a password **file**, never a password in argv/DSN.
   Native export/readback requires dedicated private local-account username
   and password files plus the existing reviewed native trust/pin files.
   Integration API keys and copied browser cookies are not login credentials.
5. Keep dumps, source archives and readback plans private. Crops, descriptions,
   query labels and identifiers must not be added to a public repository.

## Commands and ordering

Start with `python -m aikey.index_rebuild status --state-dir <key-state>`
from the reviewed installed source; use its `--help` option for all flags.
All commands take `--state-dir` (the **Key root**, including for E5) and
`--space clip-basic-v1|e5-session-v1`. `status` and `plan` are read-only and need
no database/model/native connection. Other commands refuse incomplete admission
and emit fixed error categories without private payloads. An uncertain outcome
is never retried or automatically rolled back.

1. **Export:** `export --config <existing-key-config> --output <new-private-directory>`
   plus the explicit database transport. Image export also needs
   `--native-host`, `--native-username-file`, `--native-password-file`,
   `--native-trust-file`, `--native-cert-file`. It reads exact event/object/camera
   joins and only original stored crop endpoints, not enhanced images.
   Session export reads existing descriptions and requires every member camera
   of each session to be in the existing explicit `worker.continuous.camera_ids`.
   “Original” means the unenhanced Protect-held crop, not a promise of byte-identical
   pixels to the earlier worker's key-frame region. The changed source distribution
   still has to meet the frozen quality targets.
   It does not infer caption permission from visual-index permission. The visual
   scope is the existing `find_anything.index_camera_ids`.
2. **Freeze source:** for continuously growing history, stop/drain the
   participating Key **before the final export/stage** and keep it stopped
   through quality, backup and cutover. Staging while active is permitted only
   while the frozen source remains unchanged; it is not a live catch-up loop.
   Retain the returned `manifest_sha256`, exported/unavailable/
   unauthorized counts and private archive. Retention gaps, missing thumbnails,
   blank descriptions and unauthorized history are reported, never replaced
   with arbitrary frames, newly generated captions or old-model vectors.
3. **Stage:** `stage --config … --source-archive … --source-manifest-sha256 …`
   with the database transport, `--target-revision <full-sha256>` and
   `--target-server <separate-local-endpoint>`. Optional `--batch 1..256` and
   `--max-batches` bound progress. Retain its returned 12-hex **generation**.
   Every subsequent command must select that same `--generation`; do not guess
   the newest directory. A fresh export/new source needs a new generation;
   cancelled and failed attempts remain available for reconciliation.
4. **Resume/verify:** repeat stage with the exact source/target/generation.
   Previously finished rows are not re-embedded. `verify` checks complete
   coverage, finite nonzero vectors, width and target revision. Missing source
   blocks a swap: history is not silently erased, and mixed old/new vectors are
   not published. Existing unindexed E5 rows remain unindexed; visual vectors,
   descriptions, BM25 and other session fields are not rewritten by text swaps.
5. **Freeze quality/readback:** author a private plan **before evaluating the
   target**, with known positive labels, negative queries and fixed thresholds.
   Its source hash, old/target revisions and (when supplied) full fingerprints
   must match this generation. Use `qualify --readback-plan <private-plan.json>`
   with the same target/database/generation. It queries the isolated staged
   index, not the public live vector column. No threshold is lowered to rescue
   a failed result. A failed quality target blocks cutover.
6. **Stop and back up:** coordinate maintenance outside this tool; stop/drain the
   participating Key. Take a matching private database dump with
   `python -m aikey.search_backup --container <container> backup --out <private-backups> --profile <key-state/search-profile.json>`
   and run its `verify <manifest>` scratch-restore check. The helper's `container`
   command is its existing host transport, not an automatic Docker alias.
   Validate that transport and the scratch-restore path on your host first.
   Hybrid backups include the separate session profile. The cutover command
   rehashes the exact recorded dump bytes and prior profile and requires a
   matching verified record no older than one hour; this is not a new restore.
7. **Cut over:** `cutover` with the same source, plan, config, database, target,
   generation and `--backups`. Exclusive runtime authority, frozen source and
   qualified-stage digests are checked, including under SQL table locks.
   One transaction preserves a snapshot and swaps only the selected vector
   column. The profile handoff is recoverable; a failed profile write restores
   the previous vectors/profile, and interrupted success retains the original
   snapshot. E5-only installations hand off both root and child profiles;
   hybrid installations leave the CLIP root untouched.
8. **Switch and start:** explicitly update only the reviewed route/revision:
   `find_anything.clip_server` for CLIP, or `embeddings.endpoint` and
   `embeddings.revision` for E5. Preserve the rest of the configuration. Start
   the participating Key and verify its pinned health/encoder identity. Keep
   the old encoder/checkpoint and configuration for recovery.
9. **Native readback:** `readback --readback-plan … --runtime-url <pinned-key-https-health>`
   with the same database/target/generation and explicit native credentials/trust.
   It uses a fresh pinned runtime observation before and after the fixed native
   queries, reloads positive results, and requires acknowledged session logout.
   Full-fingerprint plans require the running encoder's full fingerprint too.
   Until this succeeds the generation stays `awaiting_native_readback`; an old
   successful attempt cannot finalize after a later failed attempt. No success
   is inferred from callback HTTP 200, a status label or a stale page.
10. **Rollback when needed:** stop/drain the participating Key again, then
    `rollback` with database/target/generation. It restores snapshot vectors and
    the exact original profile bytes. Rows added since cutover (or session source
    text/associations changed since the snapshot) require a new authorized
    archive and `--previous-server` serving the **exact old checkpoint and
    complete old encoding contract**. Missing old source/checkpoint refuses
    rather than mixing models. An unknown old checkpoint permits exact snapshot
    restoration only while no new/changed indexed rows require re-embedding.
    Restore the prior route/configuration explicitly, restart and verify old
    native results. No automatic cleanup deletes the retained generation.

If the source changes while staging, pause/retire that attempt, export again and
use a new generation. Do not overwrite a frozen archive or reset its journal.
If a cutover, logout, profile write or recovery is uncertain, keep the Key stopped
where appropriate and inspect durable journal, profiles and snapshot before
choosing a supported recovery. The tool does not automate controller restarts.

## Private quality plan schema

CLIP schema is `aikey-index-readback-plan/1`; E5 is
`aikey-session-index-readback-plan/1`. Both require `from_revision`,
`target_revision`, `source_archive_sha256` and 2–8 queries. Use paired
`from_profile_fingerprint` / `target_profile_fingerprint` for same-weight
identity changes. Unknown-source E5 additionally requires
`from_revision: null`, `unversioned_source: true` and both full fingerprints.

| Query field | CLIP | E5 |
| --- | --- | --- |
| `text`, `min_cosine`, `k` | Required, fixed before evaluation | Same |
| Native score | `min_similarity`, integer 1–100 | `min_confidence`, integer 1–100 |
| Positive labels | `expected_objects` | `expected_sessions` and `description_sha256` map |
| Camera constraint | `camera_id`, or null | `camera_ids`, empty list means no constraint |
| Time constraint | `start_ms`, `end_ms`, both null or interval | Same keys, session overlap semantics |

At least one positive and one negative is mandatory for nonempty history. An
explicit `empty_index: true` allows only negatives, only for an actually empty
selected vector index; it proves empty operation, not positive relevance.
Identifiers, hash values and labels must be exact, not fabricated examples from
this document. Plan objects are deep-frozen; duplicate keys, invalid vectors,
ambiguous responses and mismatched source associations refuse the operation.

The native session API has only the observed `query` parameter. The checker
**does not invent a native confidence filter**: it reports raw returned rows
and those above the frozen score separately. Raw low-score negatives remain
visible in the evidence. This is not a native UI negative-rejection claim.
Likewise, Basic native time ordering is not misrepresented as similarity sorting.

## Evidence boundary

See [source and isolated qualification](evidence/index-migration-20261103.md).
The synthetic SQL/TLS lifecycle is not a production migration, a hardware/model
quality result, power-loss qualification or proof of current controller UI.
Native rollout remains with the integration owner under the issue's handoff
boundary. No code path silently upgrades a live model or grants camera access.
