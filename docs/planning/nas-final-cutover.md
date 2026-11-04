# Move an existing processor to another host

This is a general migration checklist, not a private installation inventory or a
fresh-install recipe. For new deployments use [the deployment guide](../deployment.md).
Migration must preserve identity and current controller-managed credentials.
Never bring up the same adopted identity on two hosts at once.

## Inventory

Record your own current source/image digest, runtime, device identity, advertised
address, ports, paired camera count, effective configuration and all state mounts.
List optional database, embedding, speech, face and discovery/relay services.
Keep addresses, identifiers, screenshots and raw manifests outside public Git.

## Gates before cutover

- Qualify the destination platform and replacement image without activating the identity.
- Reserve the intended network address and verify it does not conflict with the
  source host, another processor, controller or camera. An unanswered ping is not proof.
- Stop new processing and wait for bounded jobs/events to finish. Resolve
  uncertain writes before retrying them or taking over another process's state.
- Verify backups and a state-compatible rollback path before stopping anything.
- Keep the old runtime/image available, but prevent its supervisor from starting
  it after the destination becomes active.

## Backup before any change

Back up private identity/key/certificate, controller trust and pins, current
credential/adoption state, configuration, job journals, model-space bindings,
enrollment templates and host authority/operation records. Capture a consistent
PostgreSQL dump when search is used. A database dump does not preserve role
passwords; preserve and reconcile that credential contract separately.

Keep backups encrypted or on access-controlled storage. Verify bytes and restore
readability without exposing the keys or activating a copied identity. A successful
copy or an old backup filename is not a restore test. See [restore boundaries](../uninstall-restore.md).

## Target layout

Use a dedicated private state root owned by the destination service account.
Generate the new service/network definition from the reviewed source, then compare
it with the source layout. Map every absolute state, secret and executable path
into the actual runtime namespace. Container loopback is not the old host.

Optional search needs the same model space, dimensions, extension/tokenizer
registrations and current database-role credential. Changing a backend URL can
change a saved search-profile fingerprint even when model bytes are identical.
Use the [index migration contract](../index-migration.md), not a hand-edited
fingerprint or an unverified replacement embedding model.

## Address handover

Stop the original device and its restart/discovery/relay actors first. Positively
observe it stopped. Release/reassign the address through the host/router's supported
controls. Never give a container an address still owned by the source host.
Changing an adopted device address may require separate native compatibility
qualification; retaining the identity files alone does not prove it will work.

## Cutover sequence

1. Verify idle state, hold the original supervisor and take the final consistent backup.
2. Stop the original device and dependent services in their reviewed order.
3. Perform the deliberate address/network handover.
4. Copy private state, verify its bytes/ownership and restore the database in isolation.
5. Verify the exact database/profile/credential prerequisites, not just `pg_isready`.
6. Start the destination dependencies and then one processor with its retained identity.
7. Confirm adopted/control-connected state and expected pairings in Protect.
8. Check an original-camera result, matched positive/negative search when enabled,
   and reconnect after a planned restart. Preserve failures and uncertain outcomes.
9. Keep the old runtime stopped as rollback until acceptance is complete.

## Rollback

Stop the destination and positively verify that its identity is no longer active
before restarting the source. Reconcile any credential, journal, template or index
changes made on the destination. Do not restart an old snapshot with a stale role
password or replay completed callbacks. Restore address ownership deliberately,
then verify the original device and native results again.

If the state schema changed or an operation remains pending, use its explicit
recovery procedure. An image rollback is not a state rollback, and an unknown
outcome is not permission to create another replacement.

## What must be preserved

- Stable identity and current controller-managed credentials.
- Intended camera pairings, processing scopes, provider destinations and cost policy.
- Journals/deduplication state and consistent database/model/template bindings.
- Exact provenance and failures, without public private-network or media records.

## Historical observations

Dated, anonymized source-upgrade and isolated-restore observations remain in the
[evidence index](../evidence/README.md). They do not qualify a new host, full native
disaster recovery or a sustained installation. This page no longer publishes a
particular operator's network topology, camera locations or handover instructions.
