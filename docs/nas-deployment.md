# AI Key on Linux with Docker Compose

This is a source-build recipe for a Linux server or NAS with Docker Engine and
Compose 2.20+. It is not specific to a NAS vendor. It deploys one AI Key, not AI
Port instances. For Ports, use [their network/Compose guide](aiport-nas-compose.md).
Read [deployment requirements](deployment.md) and [compatibility limits](compatibility.md)
first. No stable release or official prebuilt project image is available.

For a **fresh Key without editing JSON**, use the separate
[first-run Compose recipe](nas-first-run.md). It prepares a new child directory
through `setup`, selects the model explicitly and can prepare the bounded
factory-adoption window. It does not reuse the lower-level layout below or
enable search. Keep an existing installation on its existing recipe and paths.

## Network and storage

The root [compose.yaml](../compose.yaml) uses Linux host networking. Management
HTTPS defaults to TCP 8080. Optional discovery uses UDP 10001. The optional
`search` profile uses TCP 5432 on the same host address. Check that these ports
are free before starting anything. Restrict them to the controller and required
private peers. Docker Desktop host networking is not covered by this recipe.

The emulator connects to the configured console using verified TLS, normally
control 7442 and media/callback 7444, with search 7443 when enabled. Provider
clients have separate destinations and never receive the device certificate.
There is no bundled vision-model server.

`state/` holds identity, keys, trust, credentials and journals. Keep it private
and persistent. Compose runs the emulator as `AIKEY_UID:AIKEY_GID`, with a
read-only root, dropped capabilities and a temporary `/tmp`. PostgreSQL's
entrypoint prepares its files before switching to its own unprivileged account.

## Build and initialize without connecting to Protect

Use a new reviewed source checkout as described in [source installation](deployment.md#1-select-and-install-the-source).
Run these commands from that checkout as the intended state owner:

```sh
cp examples/nas.env .env
mkdir -m 700 state
```

Edit `.env` with the real numeric state-owner IDs and controller address before
continuing. Replace the documentation addresses in `init` with your real
controller and advertised host address. Then build and initialize:

```sh
docker compose config --quiet
docker compose build emulator
docker compose run --rm --no-deps emulator init --config /state/config.json --state-dir /state --controller 192.0.2.1 --device-ip 192.0.2.110
```

Before running the commands above, edit the copied `.env`: set `AIKEY_UID` and `AIKEY_GID` to the
numeric owner of `state/`, and `CONSOLE_IP` to the real controller address.
Replace the two documentation addresses in the initialization command with the
controller and this host's advertised address. The example subnet is not usable
on your LAN. If `state/` already exists, inspect it instead of rerunning this
fresh-install recipe. Never initialize over an adopted or partial identity.

`init` writes local private files only. It deliberately does not start a device
or complete provider/profile selection. Edit `state/config.json` with a trusted
local editor, preserving all generated identity and file paths:

| Setting | Choose |
| --- | --- |
| `controller.host` | The actual console name/address |
| `controller.protect_version` | The installed Protect version, not the initializer's historical default |
| `controller.control_profile` | The compatible protocol; `device-service` requires a verified `expected_fingerprint` |
| `controller.expected_fingerprint` | Independently verified control-certificate SHA-256 |
| `controller.verify_hostname` | Keep `true` if the certificate covers the chosen address; explicit pin-only mode otherwise |
| `device.ip` | This host's reachable LAN address |
| `runtime.bind` | This host's address, or a deliberately firewall-restricted bind |
| `worker.ffmpeg_path` | `/usr/bin/ffmpeg` in this image |
| `worker.request_mp4_exports` | `true` if the target supports the documented MP4 export route |
| `discovery.enabled`, `search.enabled`, `database.enabled` | Leave `false` for a first connection without search |

This lower-level recipe uses `/state/config.json` because that is Compose's
startup path and the database sidecar's mount contract. The separate
[offline setup command](first-run.md) creates a new child directory. Do not
substitute it into this layout without updating every mount/path deliberately.

## Select the provider and import trust

For an existing Ollama server on this Linux host:

```sh
docker compose run --rm --no-deps emulator provider ollama --config /state/config.json --model INSTALLED_VISION_MODEL
```

With host networking, loopback addresses refer to the Linux host. For another
model host or provider, use the explicit endpoint/remote permissions in
[provider configuration](providers.md). For OpenAI, create `state/openai-key`
with a trusted editor and mode 600, then save only its file reference:

```sh
docker compose run --rm --no-deps emulator provider openai --config /state/config.json --model VISION_MODEL_ID --api-key-file /state/openai-key
```

These commands save configuration, not a model or connectivity result. Real
inference sends selected frames to that endpoint and may incur charges.

Obtain and independently verify the controller certificate's fingerprint, then
import it. This step makes a TLS handshake but no adoption request:

```sh
docker compose run --rm --no-deps emulator trust --config /state/config.json --channel control --fingerprint VERIFIED_SHA256
docker compose run --rm --no-deps emulator check --config /state/config.json
```

Fix readiness errors before starting. A successful check is not native
compatibility or model availability. Keep certificate verification enabled.

## Start and adopt

Starting connects to Protect and may create a pending device:

```sh
docker compose up -d emulator
docker compose ps
docker compose logs --tail 50 emulator
```

Adopt it in Protect using the intended version-specific flow. New configurations
use management username `ui` and a private random initial password. Some
consoles require [bounded factory enrollment](factory-enrollment.md). The console
may rotate the credential; the original `management-password` file is not updated
to that controller-managed password. Do not print or share it in diagnostics.

For optional discovery, use the actual interface address and exact controller
allowlist. Do not broaden the allowlist to hide a routing problem. Follow the
[one-camera native verification checklist](deployment.md#4-start-adopt-and-verify-one-camera)
before enabling continuous processing or additional features.

## Optional search must be prepared before credential rotation

The included `search` profile is a dedicated PostgreSQL 14/pgvector dense-search
preparation. It is **not** the complete hybrid/BM25 deployment. If your Protect
version requires other extensions/tokenizers or a different search profile,
this image alone is insufficient. Read [database](database-contract.md),
[search](search-contract.md) and [migration](index-migration.md) contracts first.

The initial database-password file starts with the initial management password.
Before adopting a fresh installation with search, prepare the database and
credential synchronization so adoption's rotation can update the role. Set
`database.enabled=true`, keep the sidecar host at `127.0.0.1`, database/user
`unifi-protect`, and its TLS root at `/state/device.crt`. Configure the matched
embedding backend and separate search-channel trust. Import the search
certificate using its own independently verified fingerprint, not an assumed
copy of the control certificate.

```sh
docker compose run --rm --no-deps emulator trust --config /state/config.json --channel search --fingerprint VERIFIED_SEARCH_SHA256
docker compose --profile search build
docker compose --profile search up -d postgres
```

Confirm the final database server can execute a query against the intended
database, then start the configured emulator. `pg_isready` can also see a temporary
initialization server and does not establish schema/profile readiness. Enable
`worker.description_embeddings` and the E5 responder together only after the
correct model/database profile is ready. The controller applies its migrations.

An already-adopted device may have stale initial database credentials. Do not
initialize a new search volume or replace secret files and assume they match
Protect. Reconcile the controller-managed credential and test rotation without
removing the retained volume. This recipe does not automate that recovery.

## Stop, update and restore

```sh
docker compose stop emulator
```

Stop the optional database separately if needed. Retain state and volumes.
Never use `down -v`, identity regeneration or an old password file as routine
troubleshooting. Back up state and a consistent database dump together before
updates. Build a replacement from a reviewed revision, change one instance at
a time and repeat native adoption/result/search/restart checks. See
[uninstall/restore](uninstall-restore.md) and [host migration](planning/nas-final-cutover.md).

Host boot ordering and user-level service dependencies vary by NAS OS. The
[systemd boot-order guide](nas-boot-recovery.md) describes one class of mounted-home
failure; it is not a universal fix or proof of full reboot recovery.
