# Pause, uninstall and restore without discarding recovery state

Removing this project's application is not the same operation as forgetting a
processor in Protect, deleting recordings, resetting an adopted identity,
revoking credentials or purging backups. **Retain data by default.** This guide
explains the boundaries; it is not an automated uninstall/restore command or
a claim that full native disaster recovery has passed.

For install-specific commands use the [NAS guide](nas-deployment.md),
[Mac guide](apple-container.md), [AI Port guide](aiport-candidate.md) and
[control-site guide](control-site.md). Their service/container names are examples.
Inventory the actual deployment first: do not paste one installation's paths,
Compose project or supervisor names into another.

## Choose the intended operation

| Operation | What changes | What must remain |
| --- | --- | --- |
| Pause | Stop processing and its restart owner | Identity, credentials, config/history, journals/budgets, indexes and recordings |
| Remove application | After a verified backup, remove only selected stopped application/service definitions | All retained state and recovery artifacts unless a separate deletion decision is made |
| Restore/reinstall | Reuse a consistent verified state set with matching software/model/database | Existing adopted identity and effective credential association; never initialize replacements |
| Erase/revoke | A separate, explicit data/credential decision | Required copies/replay protection until their dependencies are reconciled |

A processor that is paused or removed supplies no new detections/captions.
Existing camera recording is managed by Protect, but neither a running camera
flag nor this guide proves playback continuity or alarm coverage.

## Before stopping anything

1. Record the exact current image/source versions, selected services, mounts,
   configured runtime paths, model/encoder profile, database major/extensions,
   network dependencies and restart owners. Keep this inventory private; full
   environment/config dumps can contain secrets. Record credential **references**
   and protect their files, never copy values into commands, issues or logs.
2. Identify the supervisor/LaunchAgent/Compose restart policy that could revive
   a stopped process. Inspect and save its actual definition before holding or
   disabling only the selected owner. Do not remove unrelated services, edit
   global schedules or assume that stopping a container disables its supervisor.
3. Quiesce work. For the Key, stop new admission and let active jobs reach a
   known terminal state. For Ports, also account for in-flight model work,
   producers and pending snapshot callbacks, not just open smart events.
   Use the deployment's bounded drain procedure; do not substitute a guessed
   sleep or kill timeout for observed idleness. If draining fails, preserve the
   refusal/current state and do not proceed to removal.
4. Reconcile any uncertain provider/callback outcome before a retry. Stopping
   does not prove a request was uncharged or a callback absent. Keep uncertain
   journals and durable budget/replay markers; deleting them can duplicate
   charges or native events. See [caption accounting](caption-budget.md).
5. Stop the separate administration process if removing it, after any explicit
   provider probe has a settled result. A device image replacement does not
   stop or update this independent listener. Its administrator state is also
   separate from processor adoption credentials.

## Make a consistent recovery set

Use private storage with adequate space and verified access. File mode 0600 is
not encryption; backups can contain camera media and credentials. Capture:

| Retained category | Why it matters |
| --- | --- |
| Config and complete immutable config history | Key references, scopes and valid revision/Undo relationships |
| Identity/certificate/private key, adoption/device/management state | Same native processor rather than a new identity; current controller-managed credential, not just the initial password file |
| Database credential and pending-rotation recovery files | Database/device effective password consistency after interrupted rotation |
| Worker journals, uncertain outcomes, permits, caption/request/event ledgers and archived replay tombstones | Duplicate-charge/callback prevention across restarts |
| Search database and its tokenizer/extension catalogs | Search rows, vectors and ranking dependencies; a dump exit code alone is not readiness |
| Search profile, migration journals, rebuild/rollback references | Correct encoder space and resumable transitions; equal vector width alone is insufficient |
| Named-face state, local models/config and relevant derived data | Their separate privacy and model-license obligations; do not publish them |
| Administrator signing/password state, audit history and deployment definitions | Restore the intended separate listener, settings and ownership rules |

Stop database writers before a cold physical copy, then stop PostgreSQL cleanly.
Do not copy a live PostgreSQL data directory as though it were a cold backup.
For a logical dump, retain its manifest/profile and verify a scratch restore;
separately inspect required tokenizer/extension catalogs. The [logical and
physical restore evidence](evidence/nas-full-state-restore-20261029.md) describes
observed differences and limits, not a universal restore script.

Some private installations place PostgreSQL in the Key container's network
namespace. Treat that pair as one lifecycle unit: replacing/removing only the
Key can leave the database attached to the old namespace. The repository's
host-network Compose recipe is different; inspect the actual topology.

Verify archive digests, decompression/CRC, complete members and file contents/
metadata, then an isolated component restore. Preserve failure receipts and
original backups instead of overwriting them with a later successful copy.
Do not use the sole working state as a disposable restore target.

## Remove only the selected application

After the recovery set is verified, remove only the selected **stopped**
container/application and its specific held startup definition. Keep retained
state mounts, database volumes, old working images and private recovery sets.
Do not use volume-removal flags, recursive state deletion, blanket container/
image/volume pruning, factory reset, or initialization as an uninstall shortcut.

Do not click Forget/Unmanage, reset passwords or re-pair cameras merely to stop
this application. Those are controller mutations with separate consequences;
this project has not established a general guarantee that historical search,
recordings or associations survive them. Never remove/reassign an adopted Port
identity just to release a camera slot.

Removing a shared model server, reverse proxy or PostgreSQL service can affect
other applications. Retain shared dependencies until their consumers are
identified. Keep stopped rollback containers stopped: never run two copies of
one identity or two databases against the same writable state.

## What uninstall does not delete

- Protect owns its recordings, indexed detections, captions, transcripts,
  identity names/crops, plates, enhancements and temporary query uploads.
  Their expiry/deletion requires separate verified controller behavior.
- Local deletion does not remove provider-retained inputs, remote account data,
  host/container logs, snapshots, replicas or older backups.
- Uninstalling does not revoke provider credentials or controller adoption.
  Review every active and archived reference before revocation/file removal;
  a role-specific key may still serve another retained configuration.
- A backup retention decision must cover secrets and derived media separately.
  Do not expire replay markers by age alone while Protect can re-dispatch their
  events. The archive inspection is a dry run, not blanket deletion permission.

See [data retention](data-retention.md) for the fourteen-category inventory and
copy/expiry limitations. This guide makes no secure-erasure claim.

## Restore into isolation first

1. Keep the original stopped state and backups immutable. Restore a separate
   copy with original ownership/modes and exact mount relationships. Retain the
   adopted identity; do **not** run first-run initialization to repair it.
2. Use compatible pinned application/model images and the recorded PostgreSQL
   major/extensions. Do not combine database-major upgrades, model/index
   migrations or credential replacement with recovery.
3. Keep the scratch environment disconnected from Protect/discovery/provider
   networks. Restore/test database catalogs, profile/dimensions, full member
   digests, credential consistency and local component construction there.
   Private credential checks must not print values. A salted hash byte change
   alone does not establish that the effective password changed.
4. Re-read pending migrations, credentials and uncertain journals. Preserve
   original failures and stop on inconsistency; do not reset approval flags,
   drop an index, erase a journal or retry a potentially charged operation to
   make the check pass.
5. After isolated checks, plan one native cutover with a rollback artifact and
   observed health/pairing baseline. Only one copy of the identity may connect.
   Resume its intended restart owner only after readiness; check adopted IDs,
   MACs, camera pairings, control/search, streams and actual persisted native
   functions. Do not treat component construction or HTTP 200 as that proof.
6. Reauthenticate to the control site after restart; old sessions must remain
   invalid. Restoring persistent administrator credentials does not authorize
   replaying an old session or automatically re-running a provider test.

The current evidence proves selected isolated component restores and bounded
image transactions. Full controller-associated restore, host/power-loss recovery,
recording playback and sustained-operation acceptance remain open. A fresh
independent installation still has separate acceptance under issues #24/#25.
