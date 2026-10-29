# Complete Key archive restore and offline component restart — 29 October 2026

This new test restores the **complete** cold Key archive with its physical
PostgreSQL snapshot and database-secret copy into separate private storage.
It extends the earlier selected-766-file constructor check and separate
[database restore](nas-cold-restore-20261029.md), not full native controller/host
recovery. Original failures and receipts remain preserved.

## Isolation and bytes

- All three original archive hashes still match. Archive paths are relative;
  members are regular files/directories, not links. No live state/volume is mounted.
- **121,449** Key members / **121,185** regular files restored. Before any
  initialization, GNU tar compares all full-state contents and recorded metadata
  against the archive, retaining original ownership/modes.
- PostgreSQL: new volume, `--network none`, no host ports/restart policy,
  read-only root, 1 CPU / 1,536 MiB; database process becomes non-root.
- Temporary Key processes: the same network-less namespace, existing non-root
  UID/GID, dropped capabilities, read-only root, bounded tmpfs, 1 CPU / 1,024 MiB,
  restored state and already deployed Key image. No second controller-connected
  device is started.
- Copies are private: directory 0700, receipts/environment files 0600. No raw
  credentials, media, captions, templates or native IDs leave private clients.

## What actually starts

The real `Application` constructor and pinned search-profile validation run
against all restored state. Its idle worker starts, and its actual device HTTP
application serves HTTPS using the restored certificate/private key. The client
connects over isolated loopback with normal certificate/hostname verification.
Health is adopted with zero queued/active/pending jobs.

**`Application.start()` is not called.** Controller, search, discovery and
camera-registry background connections remain off. No identity advertisement,
re-adoption, new job submission, model request, camera opt-in or native management
mutation occurs. Copied adopted state is not native controller acceptance.

The component process exits, the isolated database restarts, and a second new
process repeats against the same restored state/database. Results are identical;
configuration, adopted identity fields, certificate/key and search profile match.

## Replay protection and normal initialization

- **120,378** original frozen tombstones remain byte-exact after initialization;
  independent archive-to-restored-file comparison checks every original marker.
- Startup normally archives **600** old terminal journals. Each added marker's
  job identity, fingerprint, state and timestamp exactly matches its original
  journal. No original marker is missing.
- All **120,978** resulting tombstones validate, with zero unsafe, malformed,
  stray, interrupted-temp or unreadable-reference entries. Thirty-two actual
  worker replay-record lookups match those inventory records.
- The second process produces the same result. This proves recovered replay
  state, not inference success, duplicate-charge prevention under power loss,
  recognition quality or replay of every possible native command.

## Database association

Public table counts, embedding counts/dimensions and tokenizer catalogs match
the cold snapshot. Catalog rows: model 1, tokenizer 1, text analyzer 1, stopwords 3,
synonym 0. Synthetic `session_tok` output cardinality is **384**; stored-vector
self-distance is **0.0**. No rows/vectors are exported.

The password from the full restored Key state authenticates over **verified
TLS**; a wrong synthetic password is refused. The test alone connects numerically
to loopback while retaining the original configured logical host for TLS, and
the cloned database permits loopback instead of its production peer. Config,
password and CA files are not edited. These explicit network differences do not
prove native Protect reauthentication.

PostgreSQL restart preserves counts, catalogs, tokenizer result and vector
self-distance. A second offline Key component process also passes afterward.
No application row or password is changed by the test probes.

## Independent final read-back and limits

Test PostgreSQL is **stopped**, restart disabled, separate volume retained.
Temporary extraction/comparison/auth/Key helpers are collected; private
copies and receipts remain available. All six production container IDs are
unchanged/running and live Key core files match the original baseline.

Authenticated Protect bootstrap still shows the same one Key, four Ports and
nine pairing strings, all five adopted/connected; unchanged 11 cameras / ten
connected. No second native identity appears.

This completes full archive restoration plus offline application-component,
replay-state and database-association/restart checks. Native attachment to the
restored copy, fresh search relevance, production state replacement/rollback,
host power loss, controller reboot, Mac recovery, 72-hour soak and seven-day
pilot remain open. The [Port old-image/current-state rollback](aiport-source-upgrade-20261029.md)
does not substitute for them. ONVIF remains deliberately open.
