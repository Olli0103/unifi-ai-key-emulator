# AI Key Find Anything (basic mode) contract (issues #2, #21)

**Status, 26 Sep 2026 15:55: verified natively on Protect 7.3.68** (Mac search host, see [Live verification](#live-verification-26-sep-2026)). The blocker section further down is kept as history.

This is static evidence from the Protect 7.3.60 controller bundle (`service.js`, SHA-256 `9364cc9e…`), plus read-only live checks on Protect 7.3.68 on 26 Sep 2026. Nothing was changed on the controller for this record.

## How Protect answers a Find Anything text search

1. **Query.** `searchDetections` calls `analyzeNaturalLanguage(text)`. That sends the device request `NL_PARSE` over the AI Key's `wss://…:7443/wss/nl-search/v1` connection with `{querySentence, model}`, where `model` defaults to `clip-ViT-L-14`. The reply must match `{keyTags:[{matchedWord, tags[]}], txtEmbed:number[], startTime?, endTime?, timeTag?, objectTypes?, model?, dim?, exact_match?}`.
2. **Search.** It runs vector SQL over `ramDetections` (`embedding vector(768)`) through `aiprocessor.sql.query.transaction`, **on the search host's PostgreSQL**. `getFirstAnalyzedAt` already reads `ramDetections` there.
3. **Index.** A full RAM callback (`POST /internal/aiprocessors/recognize-anything`, part `ram`) indexes images through `saveEventTagging`:
   - `keyMomentsTags[]`: `{keyMomentMs, tags:[{confScore, tag}], searchSnapshots?, confidence?, imgEmbed?(768)}`;
   - `thumbnailTags[]`: `{keyMomentMs, tags, imgEmbed?(768), reidEmbed?(1024), trackerID}`. Each is matched to an existing `smartDetectObject` by `attributes.trackerId` and exact `detectedAt = keyMomentMs`. Then `ramDetections` gets that object's embedding.
   - With an embedding and **no built-in search host**, the row goes to the AI Key's database (`saveRamDetectionToAiprocessor`). If that search host is missing, the row is only cached for a retry.

## Where the search host comes from

On every external AI processor connect, `onAiprocessorConnected` does four things:
- connects to **PostgreSQL on the AI Key's address, port 5432**, as `unifi-protect`, with the device's current credential (TLS, no server verification);
- runs the controller's `aiprocessor` migrations;
- adds session columns and optional hybrid-search objects;
- runs `chooseSearchHost`.

A built-in console AI processor would take precedence, but this console has none. No capability flag is checked on this path.

## Live state (Protect 7.3.68, 26 Sep, read-only)

- **AI Key:** `host: 192.168.0.98`, `isBuiltIn: false`, **`isSearchHost: false`**, `aiMode: basic`. It is the only AI processor.
- **Nothing listens on `192.168.0.98:5432`**, so every connect-time PostgreSQL attempt fails and Protect has no search host.
- **Emulator config:** `search.enabled: false`, `database.enabled: false`. The existing query profile answers only E5 (`multilingual-e5-small`, 384 values), which is the deep-mode session path, not the basic CLIP path.
- **Caption callbacks:** they send `keyMomentsTags: []` and no `thumbnailTags`, so nothing could be indexed even with a host.

## Blocker: the search host on this Mac

The repo's PostgreSQL profile (`deployment/postgres`: pgvector PG14, `unifi-protect` superuser, SCRAM over TLS) admits only the console's exact `/32`. On this Mac:

- **A container can't do this.** Apple `container` port publishing rewrites every peer to `192.168.64.1`; I measured this for both a LAN-side and a container-side connection. The HBA rule would then have to admit the NAT address, which in practice means any LAN host with the password. The profile forbids that broadening.
- **An unsigned host binary can't either.** A host-native PostgreSQL would see real source addresses. But the macOS application firewall (stealth mode) blocks unsigned Homebrew servers, as it did for `whisper-server`. Changing firewall settings is not done here.

Options for the owner:

1. **Postgres.app on the Mac (recommended).** It is Developer-ID signed, so the firewall setting "automatically allow downloaded signed software" admits it. It has pgvector, listens on `192.168.0.98:5432`, and uses HBA `hostssl … 192.168.0.1/32 scram-sha-256` exactly as in the profile. Data stays on the Mac.
2. **Container with HBA for `192.168.64.1/32`.** SCRAM over TLS with a long random device credential, but any LAN host can attempt to log in.
3. **Move the AI Key to the NAS.** There the macvlan network preserves source addresses, as for the AI Port slots. This is a larger identity move.

## Once a search host exists, the smallest compatible path

1. **Query:** answer `NL_PARSE` with a CLIP ViT-L/14 text embedding (768, L2-normalized), with `model: clip-ViT-L-14`, `dim: 768`, `keyTags: []` and `exact_match: false`.
2. **Index:** for recognition tasks, crop each `thumbnailMeta` object at its timestamp and embed it with the *same* CLIP image encoder. Post `thumbnailTags` with that `trackerID` and `keyMomentMs`, with no description and no external upload.
3. **Encoder:** a local CLIP ViT-L/14 model (OpenAI weights, MIT), text and image from one checkpoint, in a sidecar on the host-only container bridge like Whisper and faces.
4. **Acceptance:** Protect shows `isSearchHost: true` after its migrations, `ramDetections` rows exist for the indexed objects, and a text search returns a known positive result and misses a known negative, read back in Protect.

## Live verification (26 Sep 2026)

**Setup on the Mac (no firewall change, no NAS move yet).**
- **PostgreSQL:** `local-postgres-search` runs the `deployment/postgres` profile (pgvector 0.8.6, PG14) on a named volume. It is published only on the host bridge, `192.168.64.1:55432`, with TLS from a private CA whose server certificate names `192.168.0.98`.
- **Relay:** `aikey.pg_relay` listens on `192.168.0.98:5432` under the signed `/usr/bin/python3`. It admits only `192.168.0.1/32` (the console) and `192.168.64.0/24` (the host-only container bridge), and splices bytes to PostgreSQL. TLS and SCRAM stay end to end.
- **CLIP:** `local-clip-vitl14` runs ONNX CLIP ViT-L/14, pinned to Hugging Face revision `c3077901`, on `192.168.64.1:8180` only.
- **AI Key config:**
  - `search.profile: clip-basic-v1`;
  - `database.enabled: true`, host `192.168.0.98`, `verify-full` against that CA;
  - `find_anything.index_camera_ids`: the 10 connected cameras;
  - `controller.search_ca_file` and `controller.search_expected_fingerprint` hold the separate 7443 certificate pin (`CN=unifi.local`; 7442 serves `CN=localhost`).

**Credential.** Postgres was initialised with a random secret of our own. On the next connect Protect sent `changeUserPassword` with the device's current password as the new one; its generic `updatePassword` confirms the credential once per connection. The existing `PgCredentialRotator` applied it to the role (result 0). Protect's connect-time Postgres login then succeeded. No vendor default credential is used.

**Readbacks (content-free).**

| Check | Result |
|---|---|
| Protect `aiprocessors` | AI Key `192.168.0.98` `isSearchHost: true` (was `false`) |
| Relay | console connections accepted; host and other sources refused |
| Search host tables | 6 tables created by Protect's own migrations |
| AI Key search WebSocket | `connected: true`, 0 failures; the `NL_PARSE` counter follows Protect queries |
| Index jobs (15:44–15:52) | 7 `searchSnapshots` callbacks, all HTTP 200; local CLIP only, 0 provider requests |
| `ramDetections` | 7 rows, 7 with a 768-value embedding, 7 events, 2 cameras (Flur, Haustür) |
| Protect `detection-nls`, `minSimilarity=35` | "person": 6 hits (the six person objects; the vehicle excluded); "a person walking in a hallway": 4 Flur hits, top similarity 49; "a sailing boat on the ocean": 0; "an airplane in the sky": 0 |
| Event readback (top hit) | `smartDetectZone`/person event lists 1 detected thumbnail of type person with an object ID; the thumbnail is served (HTTP 200, not viewed) |

**Contract details found live.**
- **`thumbnailMeta` is usually empty.** It was empty on 6 of 7 key-moment tasks, so `thumbnailTags` alone indexes almost nothing.
- **`roi` is a list.** Every `thumbnailMeta`, `roiMeta` and `personMeta` entry carries a list of objects (`{ts, roi: [...]}`), with 0-1000 xywh boxes.
- **Search snapshots:** `keyMomentsTags[].searchSnapshots` (Protect's `snapshotSchema`) plus an image part named by tracker ID make Protect create the thumbnail and smart-detect object, then attach the key moment's `imgEmbed` (`saveEventTagging` → `w()` → `D()`). The Key answers each person, vehicle, animal or package region from `roiMeta`, `personMeta` or `vehicleMeta` this way, one per tracker.
- **Echo error body:** Protect's UCP4 error replies (such as the answer to the Key's `echo`) carry an empty *string* body, which must not drop the query channel.

**Gaps that remain.**
- **Retrieval quality:** discrimination is modest on low-resolution night crops. "a car" did not rank the one vehicle first. No benchmark (#7) exists yet.
- **Coverage:**
  - Audio-event image tasks (`ramType: image`, one cropped thumbnail each) are not indexed yet.
  - Face-camera recognition tasks go to local faces and are not indexed.
  - Key moments come from a console-local service, not from cameras or AI Ports. Protect's `/ai-feature-console/v1` socket admits only local addresses; Protect streams it `SmartDetectTrack` raw tracks, and that service sends back `FrameSelection`. AI Port-paired cameras are covered: Flur, a G3 on our Mac AI Port, was indexed and returned by search. When Protect selects a key moment is outside the Key's control.
- **Durability:** CLIP and Postgres are pinned in the Mac supervisor. Since 26 Sep 16:35 the relay runs as the launchd agent `com.olli.local-aikey-pg-relay` (`KeepAlive`, `RunAtLoad`, 10 s throttle), generated by `pg_relay.py --launch-agent`.
  - **Location:** the agent runs a copy of the script in `~/Library/Application Support/local-aikey-relay/`, which also holds its status file. macOS privacy protection refuses launchd-started processes access to `~/Documents` (`Operation not permitted`, exit 2 on the first attempt).
  - **Restart tests:** `kill -9` brought a new listener back in 1 s (`runs = 2`). A login-style `bootout` then `bootstrap` returned one listener.
  - **Readback after both:** Protect `detection-nls` at `minSimilarity=35`: "person" 9 hits, "a person walking in a hallway" 6, "a sailing boat on the ocean" 0, "an airplane in the sky" 0. The AI Key stays `isSearchHost: true`, and the restarted relay counted the console connections (0 upstream failures).
  - **Not done:** a real Mac reboot.
  - **Rollback:** `launchctl bootout gui/$UID/com.olli.local-aikey-pg-relay` and remove `~/Library/LaunchAgents/com.olli.local-aikey-pg-relay.plist`.
- **Deep mode** (E5 session search), image search and hybrid search: not implemented.
- **NAS:** the search host moves to the NAS only at the final cutover ([plan](../planning/nas-final-cutover.md)).
