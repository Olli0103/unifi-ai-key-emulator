# UGREEN NAS deployment plan

Target platform: UGREEN NAS and UDM Pro Max with Protect 7.3.56. No NAS or controller changes occurred during the local build. Verify the NAS paths, account UID/GID and free ports before deployment. Choose a vision provider below.

The example addresses `192.0.2.1` and `192.0.2.110` are documentation placeholders. Replace them with the actual console and NAS addresses in every command and configuration file.

## Prepared deployment

`compose.yaml` runs the emulator on Linux host networking. A separately enabled `search` profile adds a dedicated PostgreSQL 14/pgvector service. Compose 2.20 or newer is required for the [optional dependency declaration](https://docs.docker.com/reference/compose-file/services/#depends_on). The emulator runs as the configured NAS UID/GID; PostgreSQL prepares its files as root and then uses its own unprivileged account.

Expected ports are HTTPS management 8080, UDP discovery 10001 when enabled, and PostgreSQL 5432 when the search profile is enabled. Plain HTTP management is disabled outside the loopback lab. Do not start the profile if an existing NAS service owns those ports. Choose a separate network identity if needed; changing the advertised PostgreSQL port would not match the inspected controller.

The emulator initiates verified TLS connections to the UDM's control 7442, search 7443 and media/callback 7444 ports. Its media and callback allowlists contain only the configured controller origins. The model and embedding APIs use separate clients without the device certificate.

## First startup sequence

These are deployment instructions, not a record of commands already run.

1. Copy the project to a dedicated NAS directory. Copy `examples/nas.env` to `.env`; set `AIKEY_UID` and `AIKEY_GID` to the directory owner's numeric IDs. Set `CONSOLE_IP` to the exact UDM IPv4 address.
2. Create `state/` owned by that account, with mode 700. Verify the ports above and sufficient storage for media, job journals and the search volume.
3. Build and initialize, without starting controller connections:

```sh
docker compose build emulator
docker compose run --rm --no-deps emulator init --config /state/config.json --state-dir /state --controller 192.0.2.1 --device-ip 192.0.2.110
docker compose run --rm --no-deps emulator check --config /state/config.json
```

4. Edit `state/config.json`. Keep the generated identity and private paths. Confirm that the controller and NAS addresses match the deployment. In this image use `/usr/bin/ffmpeg` and opt into `worker.request_mp4_exports=true`. Leave discovery, automatic deep-mode features and search disabled for the first connection test.

Select the vision provider. For OpenAI, create a private `state/openai-key` file through the NAS's trusted local editor, then configure its path without putting its value in shell history:

```sh
docker compose run --rm --no-deps emulator provider openai --config /state/config.json --model VISION_MODEL_ID --api-key-file /state/openai-key
```

For a later Ollama installation on the NAS, use:

```sh
docker compose run --rm --no-deps emulator provider ollama --config /state/config.json --model INSTALLED_VISION_MODEL
```

These configuration commands make no model calls and install no model. They require an explicit vision-capable model name. Other OpenAI-compatible servers use `provider openai-compatible` with a base URL. See [provider configuration](providers.md). The E5 embedding service remains separate; replacing it with an arbitrary provider's embedding model would not match this search profile.
5. Import the controller certificate with a fingerprint verified independently. The CLI handshake only reads the certificate:

```sh
docker compose run --rm --no-deps emulator trust --config /state/config.json --fingerprint VERIFIED_SHA256
```

6. Review `check` output. A passed local configuration check still reports native compatibility as `needs_evidence`. The first `run` may create a candidate processor record. After that specific live test is authorized, start only the emulator:

```sh
docker compose up -d emulator
```

7. For discovery, explicitly enable it, use the NAS interface address and set `allowed_controller_ips` to the exact UDM IPv4. Multicast is optional and restricted to device mode on Linux. Before clicking Adopt, prepare the database section below if search is intended. Normal admin adoption uses the generated `management_username` and the initial private `state/management-password` file. Protect may rotate the management password after adoption; that initial file is not updated to the controller-managed password. Do not paste credentials into chat or logs.

## Prepare search before adopting the processor

The initial database-password file is private and starts with the same value as the initial management password. The controller can rotate the device password during adoption. Start PostgreSQL and enable database synchronization before that rotation when search is intended.

If the processor was already adopted with database synchronization disabled, its database-password file may be stale. Do not initialize a new search volume from it and assume that it matches Protect. That case requires an explicit credential reconciliation against the controller-managed device credential. An existing database volume requires a tested rotation, not a replaced secret file or volume deletion.

Set `database.enabled=true`. Keep the database host at `127.0.0.1` for the host-network sidecar, database/user `unifi-protect`, and SSL root certificate `/state/device.crt`. The PostgreSQL entrypoint allows SCRAM/TLS from the exact UDM address and emulator loopback address only. It uses the persistent `aikey-postgres` volume. The emulator's rotation callback changes the role password before acknowledging the management credential change.

```sh
docker compose --profile search build
docker compose --profile search up -d
```

Enable `worker.description_embeddings` and the E5 query responder together only after the model backend and database profile are confirmed. Deep/VLM capability flags are explicit overrides because the inspected controller couples them. Do not enable face, plate, audio or legacy image-search flags.

The controller applies its own migrations. This build supplies only the documented dense-search database preparation. If the installed version requires BM25 extensions or reranking, this profile is insufficient. Resolve that requirement before claiming native search.

## Acceptance and rollback

Verify ordinary adoption, reconnect without replaying the token, one controller-created inference job, one persisted description and positive/negative native search cases separately. Check the correct event after refresh and after a planned service restart. Save sanitized errors and exact firmware versions.

Stop the emulator with `docker compose stop emulator`. Stop the optional database separately if started. Keep the state directory, private key and database volume for diagnosis and recovery; do not use `down -v` or regenerate identity as routine troubleshooting. Removing the test processor from Protect is a separate console change.

Back up state and the database together before upgrades. Protect firmware changes can invalidate this private protocol. Pending jobs are not durably resumed, and an uncertain callback needs inspection before any retry. Longer runtime and load behavior have not yet been measured.
