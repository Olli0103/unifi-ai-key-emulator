# Final cutover of the Mac AI Port and AI Key to the UGREEN NAS

**Status: planned, not executed.** On 26 Sep 2026 Olli chose the UGREEN NAS as the long-term host for the AI Key and its Find Anything PostgreSQL search host. The move of the remaining Mac workloads is the **last** step. It runs only after the open parity issues have their required behavior verified in native Protect readback. Until then the Mac AI Port and AI Key stay where they are, and all parity work, including the Find Anything search host (#2), is proven on the Mac at the existing addresses.

## Inventory (Protect private API, read-only, 26 Sep 2026 14:40)

| Device | Address | Host today | Paired cameras | State |
|---|---|---|---|---|
| AI Port | 192.168.0.135 | Mac, Apple `container` `local-aiport-mac-held-shadow`; published on the Mac's en0 address | Flur, Schlafzimmer, Büro | connected |
| AI Port | 192.168.0.136 | NAS, `local-aiport-nas` / `aiport_slot_2` (macvlan `caddy_lan`) | Esszimmer, Haustür | connected |
| AI Port | 192.168.0.137 | NAS, `aiport_slot_3` | Einfahrt, Giebel Vorn | connected |
| AI Port | 192.168.0.138 | NAS, `aiport_slot_4` | Garage, Giebel hinten | connected |
| AI Key | 192.168.0.98 | Mac, Apple `container`; published on the Thunderbolt Ethernet adapter en7 (DHCP reservation for that adapter's MAC) | — | connected, adopted |

The NAS runs three AI Ports and no AI Key. **Only the Mac AI Port (.135) and the Mac AI Key (.98) move**, together with the AI Key's local backends: Whisper (8178), faces (8179), CLIP (8180) and the PostgreSQL search host with its relay. Nothing on the NAS is duplicated.

## Gates before cutover

1. The parity issues (#1, #2, #6, #15, #19, #20, #28, #45 and linked features) have their required behavior verified in native Protect readback, or Olli explicitly accepts the remaining gaps.
2. Every NAS image is built on the NAS from its build context and a throwaway run passes: an isolated start with no LAN identity. The AI Key service stays stopped, so there is never a second live identity.
3. Fresh backups (below) exist and are verified by listing.
4. The AI Key worker is idle (queued, active and pending are all 0), and no AI Port has an open smart event (`smart_events_entered == smart_events_left`).
5. Olli is present for the address handover (step 3), which needs a physical or system network change on the Mac that the agent does not make.

## Backup (before any change)

- **Private state:** `state/apple` (identity, `device.crt`/`device.key`, controller pin and CA, credential hash, worker journal, permits, face templates, `postgres-ca.pem`, `database-password`), `state/aiport-mac`, `state/pg-search/secrets`, `state/faces-models`, `state/whisper`, `state/clip-models`. Save a dated `tar` into `~/aiport-support-private/backups/` with mode 600, and record its SHA-256.
- **Search index:** a `pg_dump -Fc` of `unifi-protect` from `local-postgres-search`, taken over the host bridge, plus its SHA-256. The index is derived data, but the dump avoids reindexing.
- **Controller view:** a read-only Protect export of `aiports` (host, pairedCameras), `aiprocessors` (host, isSearchHost, feature settings) and camera `isPairedWithAiPort`, reduced to IDs, names and states.
- **NAS:** a copy of the live `local-aiport-nas` Compose text taken from the editor, and a listing of `aiport-deployment`.
- **Mac runtime:** `state/supervisor/services.json`, and the exact `container inspect` output of every pinned container.

## Target layout on the NAS

- **New Compose project `local-aikey-nas`,** separate from `local-aiport-nas`, so the AI Port project is never redeployed for the AI Key.
  - `aikey` on `caddy_lan` at **192.168.0.98**, with `mac_address` **02:9d:90:a5:48:ca** (the adopted device MAC). It uses the same state directory contents, a non-root user, a read-only root filesystem and dropped capabilities. A second, `internal: true` network, `aikey_backend`, connects it to the sidecars.
  - `postgres` (the same `deployment/postgres` image, built for amd64) uses `network_mode: service:aikey`. It therefore listens on 192.168.0.98:5432 with the console's real source address. HBA: `CONSOLE_IP=192.168.0.1` and `EMULATOR_IP=127.0.0.1`, with no relay. Data goes on a dedicated NAS volume, restored from the dump.
  - `clip`, `whisper` and `faces` sit only on `aikey_backend`, with no LAN address. The AI Key config points at their service names instead of `192.168.64.1`; that is the only config change.
- **The AI Port .135** becomes `aiport_slot_1` in `local-aiport-nas`, using its existing slot state and MAC.

## Address handover (the one step that needs Olli)

- **.98** is the Mac's en7 DHCP address and also carries the Mac's default route. Olli disables or unplugs the Thunderbolt Ethernet adapter, or moves its reservation. The Mac keeps working on en0 (.135).
- **.135** is the Mac's own en0 address, so a NAS container cannot take it while the Mac is on the LAN. The Mac needs a new en0 address (Olli), or the AI Port moves to a new reserved address. The second option still needs evidence that Protect accepts an adopted AI Port reconnecting from a new host without re-adoption, which is not yet shown. The plan therefore defaults to re-addressing the Mac's en0 and keeping 192.168.0.135 for the AI Port.

## Cutover sequence (each step has a readback gate)

1. **Idle and hold:**
   - check the idle gates;
   - run `local-apple-supervise hold aikey-mac` and `hold aiport-mac`;
   - take the backups.
2. **Stop the Mac services:** stop the Mac AI Key, Postgres, CLIP, Whisper and faces containers. Stop, never delete; they are the rollback. Unload the relay agent with `launchctl bootout gui/$UID/com.olli.local-aikey-pg-relay`, but keep its plist for rollback.
   - Readback: Protect shows the AI Key disconnected.
3. **Address handover (Olli):** .98 is released from the Mac.
   - Readback: nothing on the LAN answers at .98.
4. **Copy state to the NAS:**
   - private state goes to `aiport-deployment/aikey/state` (0600 files; owner is the container UID);
   - restore the Postgres dump into the NAS volume;
   - compare SHA-256 values against the backup.
5. **Start the NAS project:** `local-aikey-nas`, with the AI Key last.
   - Readbacks:
     - the pinned `/healthz` inside the container shows adopted and connected;
     - Protect shows the AI Key `CONNECTED` at 192.168.0.98 with the same MAC;
     - `isSearchHost: true`, and the feature settings are unchanged.
6. **Verify natively:**
   - Find Anything: the known positive text query returns the same event as before cutover, and the known negative does not;
   - one speech transcript and one caption on the existing policy;
   - `changeUserPassword` result 0 on connect.
7. **AI Port .135:** after the Mac's en0 is re-addressed, add `aiport_slot_1` and start only that service.
   - Readback: Protect shows `.135` `CONNECTED` with Flur, Schlafzimmer and Büro still paired, and three streams decoding in `/healthz`.
8. **Clean up:** unpin the Mac services in the supervisor. Leave the stopped Mac containers and images in place for seven days as rollback, then remove them.

## Rollback (any failed gate)

1. Stop the NAS `local-aikey-nas` project, or only `aiport_slot_1`. The other three NAS AI Ports stay untouched.
2. Give .98 back to the Mac: re-enable en7 and restore its reservation. For .135, restore the Mac's en0 address.
3. Start the stopped Mac containers by name (`container start …`), run `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.olli.local-aikey-pg-relay.plist`, then run `local-apple-supervise release` for both services. The state directories on the Mac were never modified during cutover.
4. Readback: Protect shows the AI Key and AI Port `.135` connected from the Mac, with the same pairings and settings.
5. If the NAS AI Key ran and Protect rotated its credential there, copy `device-state.json` and `database-password` back from the NAS before starting the Mac AI Key, so the credential hash and the DB role stay consistent.

## What is preserved, by construction

- **Identity:** MAC, device certificate and key, credential hash, adoption state and controller pin all travel as the same files. No regeneration.
- **Address:** 192.168.0.98, now on macvlan with the device's own MAC, instead of the Mac adapter's MAC.
- **Pairings and settings:** they live in Protect and are not touched. The AI Port slot state moves unchanged.
- **Provider settings and private state:** they are copied, not re-entered. Nothing is printed, uploaded elsewhere or committed.
