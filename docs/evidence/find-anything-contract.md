# AI Key Find Anything (basic mode) contract (issues #2, #21)

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
