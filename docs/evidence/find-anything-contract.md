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
- **Deep mode** (E5 session search) and hybrid search: not implemented. Search by image: verified, see below.
- **NAS:** the search host moves to the NAS only at the final cutover ([plan](../planning/nas-final-cutover.md)).

## Search by image (26 Sep 2026, 16:33)

**Contract (7.3.60 bundle).**
- **Upload:** `POST /proxy/protect/api/detection-search/by-image/upload` (JPG or PNG, max 5 MB, needs `X-CSRF-Token`) stores a `RECOGNIZE_IMAGE` file and returns `{tempId}`.
- **Request:** `requestImageToVector` sends `IMAGE_SEARCH {imgUri}` over the UCP4 query socket. It goes **only to an AI processor whose `featureFlags.supportImageSearch.enabled` is true**; otherwise it falls back to a built-in controller that this console lacks.
- **Image location:** `imgUri` is `https://<console>:<cameraHttps>/internal/files/…`. `cameraHttps` is 7444, which serves the control-port certificate, not the 7443 search certificate.
- **Reply:** exactly `{imgEmbed: 768}`.
- **Results:** `GET /detection-search/by-image/<tempId>` runs the same vector search. Its image-mode similarity is `100 × (1 − cosine distance)`.

**Implementation.**
- **Flag:** the Key advertises `supportImageSearch` only when the CLIP search profile is enabled. An explicit `feature_flags.supportImageSearch.enabled: false` still wins.
- **Fetch:** only an `https` URL on the configured console host, on the media port, under `/internal/files/`. It uses the control trust and pin with the device headers, is capped at 5 MB, and accepts JPEG or PNG (PNG is converted locally).
- **Embed:** the whole image, through the same local CLIP encoder. Only fixed failure categories are counted; the image is not kept.

**Native readback.**
- **Flag:** Protect `aiprocessors` shows `featureFlags.supportImageSearch: {enabled: true}` after reconnect.
- **Positive:** the query was Protect's own stored thumbnail of a Flur person object, fetched and re-uploaded in the browser without being viewed. The top result is **that same object, similarity 92**, followed by other Flur persons at 91 and 89. At `minSimilarity=70`: 9 hits.
- **Negative:** a synthetic drawing (sky, sun, grass; no camera content). The best result is 67. At `minSimilarity=70`: **0 hits**.
- **AI Key health:** `image_queries: 2`, no failures, 0 vision-provider requests.

**First attempt, for the record.** The first attempt failed in `fetch`, because the Key used the 7443 search pin for the 7444 upload URL. Protect then reported `Failed to parse 'imageToVectorResponse'`.

## Retroactive processing (static contract and read-only history, 26 Sep 2026 18:05)

**Contract (7.3.60 `runRetroactiveProcessing`).**
- **Start:** `POST /aiprocessors/retroactive-processing/start {allCameras | cameraIds, numberOfEvents}` stores a run on the AI processors.
- **Gate:** the runner proceeds only while a connected AI processor has `featureFlags.supportRetroactiveProcessing.enabled`. Protect defaults that flag on for Key firmware ≥ 1.3.9 only when the Key omits it; ours sends an explicit false.
- **Event selection:** past `smartDetectZone`/`Loiter`/`Line`/`smartAudioDetect` events without an `aiprocessorTasks` row, newest first, up to 50 queued per processor.
- **Dispatch:**
  - smart events go out as Recognize Anything `ramType: multipleImages`, one entry per detected thumbnail with a tracker ID (`imageId`, `keyMoment` = `clockBestWall`, `trackerId`, `objectType`);
  - audio events go through `pushAudioTask` (speech and the audio image).
- **Caution:** while a run is active, `runTask` fails every *live* AI task with `NO_FREE_AIPROCESSORS`.

**Implementation (fe0e547).**
- **Crops:** `multipleImages` tasks for index cameras fetch each saved crop from `/internal/aiprocessors/image/<imageId>` and embed it with the **local** CLIP server only.
- **Reply:** `thumbnailTags` keyed by tracker ID and exact detection time, so Protect attaches the embeddings to the objects it already has.
- **No provider, no captions:** the vision provider is never contacted and no captions are produced.
- **Tests:** covered; 1191 pass.
- **Opt-in:** `supportRetroactiveProcessing` is advertised only with `find_anything.retroactive: true`, which is **off** in the live config.

**Read-only Protect history.** The AI Key already carries a stored run:
- `state: running`, started **22 Sep 2026 17:42 local**, `allCameras: true`, `numberOfEvents: 5000`;
- no progress recorded, because the flag was never on.

Enabling the opt-in would resume that run at once. That means backfilling up to 5000 recorded events on every camera, with live AI tasks paused for about 100 minutes by Protect's own estimate.

**Status (18:05):** the live retroactive run was `needs_evidence` pending Olli's decision. It is now on; see the next section.

## Retroactive processing on (Olli's decision, 26 Sep 2026 20:57)

Olli asked for retroactive processing on the current deployment, resuming the stored run. The run was **not** cancelled or replaced.

**Resume semantics (7.3.60 static).**
- **Start trigger:** Protect's `onConnected` starts the runner when the stored state is `running` and a connected processor advertises `supportRetroactiveProcessing`.
- **Cursor and order:** the runner resumes from `nextProcessedFrom` (else `startedAt`, 22 Sep 15:42 UTC). It walks events newest first and skips events that already have an `aiprocessorTasks` row.
- **Batches:** each 10 s cycle pushes `min(remaining, 50 − tasksInQueue)` events. `tasksInQueue` is the Key's `getTaskQueueInfo` `UI_TASK_NUM` (queued + active), polled every 5 s, so the Key's honest queue depth is the backpressure.
- **Pausing:** with the flag off, the loop stops and the state stays `running`. Turning the flag off is a pause, not a cancel.
- **Live AI Key tasks:** while the run is active, Protect fails every live AI Key task with `NO_FREE_AIPROCESSORS` and the retry flag. After the run, `retryFailTasks` re-runs them (30 per minute, no age limit for that reason). Live AI Key work is **deferred**, not dropped.
- **AI Ports:** AI Port detection does not use this task manager.

**Preparation (commit after b9eac9a, "Prepare the AI Key for Protect's retroactive backfill").**
- **Ledger rollover:** completed local index jobs now roll out of the 1024-entry worker ledger after a minute. Otherwise the ledger (364 entries) would have filled after about 660 events, and Protect would have counted every later rejected push as processed.
- **Queue and deadline:** worker `max_queue` went from 24 to 64, so Protect's 50-task window always fits. Crop jobs get a 600 s budget.
- **Refusals before any fetch:** backfilled audio thumbnails (`ramType: image`) and crops from cameras outside the index are refused before any fetch. Only the offline "Wohnzimmer alt" is outside the index.
- **Provider boundary:** backfilled crops go only to the local CLIP server. Backfilled speech goes to local Whisper. No old frame, crop or audio reaches the external provider.
- **Checkpoints:** search-index backup `search-20260926T205550` (71 rows, scratch restore verified) and `config.json.before-retroactive-20260926`.

**Live progress (AI Key `findanything13`, enabled 20:57:47 local).**

| Time (UTC) | Backfilled rows stored by Protect (event start before 22 Sep 15:42) | Oldest indexed event | Key tasks / crops / completed / failed |
|---|---|---|---|
| 18:57:30 | 0 (baseline, 74 rows total) | — | — |
| 18:59:19 | 106 (5 cameras) | 22 Sep 13:57 | 105 / 107 / 64 / 2 |
| 19:11:45 | 1005 (6 cameras) | 21 Sep 12:03 | 629 / 1013 / 575 / 10 |
| 19:13:45 | 1154 | 21 Sep 06:07 | 723 / 1161 / 675 / 10 |
| 19:15:59 | 1345 | 20 Sep 17:37 | 823 / 1352 / 768 / 11 |

- **Rate:** about 50 events per minute. The ledger stays near 430 entries because 747 completed jobs have rolled over.
- **Failures:** mostly HTTP 404 for crops of old events that no longer exist. There were also timeouts of face, speech and index jobs queued around the swap.
- **Refusals:** 13 audio thumbnails; no crops from unindexed cameras.

**Retrieval check (not native).** CLIP text queries ranked over only the backfilled rows, in the same vectors Protect ranks, separate cleanly by Protect's own labels:
- "a car": 19 of the top 20 carry label 679;
- "a person": 19 of 20 carry label 265;
- "a cat": 15 of 20 carry label 802.

**Resource impact.**
- **Whisper:** backfilled speech drove Whisper to about 9 CPUs, and the Mac AI Port dropped about 38 inference frames per minute (about 4 per minute before).
- **Whisper cap attempt:** capping Whisper at 4 threads failed (wrong arguments). The original container was restored after a 40 s speech outage, 21:10:56–21:11:35 local. The cap was dropped.
- **AI Port afterwards:** 0 dropped frames per minute, about 6 fps decoded, controller connected.

**Rollback.** Restore `config.json.before-retroactive-20260926` and restart the Key; Protect then pauses the run without cancelling it. Restore the search index from the verified backup if needed. The previous Key container `local-aikey-mac-findanything12` is kept stopped.

**needs_evidence**
- A native Protect search readback of a backfilled object: neither Chrome connection exposed the signed-in Protect tab group to this session.
- Protect's own `processedNumberOfEvents`, `nextProcessedFrom` and completion state (private API, browser).
- Completion of the run, and the re-run of deferred live tasks afterwards.

## Encoder pin and index backup (#18, 26 Sep 2026 20:16)

**Why.** Protect ranks stored `ramDetections.embedding` vectors against each new query vector. If the CLIP weights change, new queries and new rows come from a different vector space than the stored rows, and retrieval degrades silently. Before this change, `search-profile.json` recorded the model name and server address, not the weights.

**Encoder pin (b9eac9a).**
- The CLIP server reports `revision`: a SHA-256 over both ONNX encoders and the tokenizer, in every reply and in `/healthz`.
- The search profile records that revision. A profile written before pinning, and otherwise identical, is upgraded **once**; the old file is kept as `search-profile.json.before-upgrade`. A profile that differs in any other field is refused.
- Afterwards the Key refuses replies from other weights for text queries, image queries, indexing, retroactive crops and reverification. A search query then fails, and no mixed vector is stored. The only way forward is a rebuilt index with a new profile.

**Index backup (b9eac9a).** `python -m aikey.search_backup backup --out <dir> --profile <search-profile.json>` writes a `pg_dump -Fc` (mode 0600) with a manifest of row counts, embedding counts, vector dimensions, the dump digest and the pinned profile. If the index changed during the dump, the dump is discarded. `verify <manifest>` checks the digest, restores into a scratch database in the same container, compares counts, and drops the scratch database. The live database is only read.

**Live, Mac search host.**
- **Backup:** 53 rows, 53 embeddings, all 768 values, 152 KB; restored into a scratch database with identical counts, and the scratch database was dropped. The list of databases afterwards: `postgres`, `unifi-protect` and the two templates.
- **Deploy:** CLIP sidecar `local-clip-revision` (revision `77d5c9711edb…`), then AI Key `local-aikey-mac-findanything12`, swapped when the worker was idle. The previous containers are kept stopped for rollback.
- **Upgrade readback:** the profile now carries the revision and the pre-upgrade copy exists. The Key is adopted and connected; search is connected and reports the same revision.

**Rollback.**
- **Encoder or Key change:** stop the new containers, start `local-clip-vitl14` and `local-aikey-mac-findanything11`, and move `search-profile.json.before-upgrade` back into place. The old Key does not know the `revision` field, so it would otherwise refuse the upgraded profile.
- **Index contents:** with the relay stopped, so Protect is disconnected from the search host, drop and recreate `unifi-protect`, then `pg_restore --no-owner` the verified dump. Restart the relay and read back `isSearchHost` and a known positive search. This step is manual and has not been exercised on the live index.

**Still open (#18).**
- A staged rebuild for new weights: a new profile generation and a re-embed of the stored crops, cut over after native readback.
- Resumable rebuilds and admin-site controls.
- Native Protect search readback after this deploy (`needs_evidence`, N14). On 26 Sep two Chrome browsers were connected and none was selected, so no browser prompt was sent.
