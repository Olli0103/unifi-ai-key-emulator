# PostgreSQL search host

This image prepares PostgreSQL 14 and pgvector for a dedicated emulator database. It creates the `unifi-protect` owner/login using the official image's initialization, which grants superuser privileges. Those privileges match the inspected device and permit controller migrations. Do not share this database with unrelated NAS services.

The controller owns its tables and migrations. The image installs only `vector`, `intarray`, and `pg_trgm`. No copied vendor migration or sample table is installed.

Required environment values:

| Variable | Value |
|---|---|
| `CONSOLE_IP` | Exact UDM console IPv4 address, without a subnet suffix |
| `EMULATOR_IP` | Optional exact emulator IPv4 peer for database credential rotation |
| `POSTGRES_PASSWORD_FILE` | Mounted password secret path |
| `POSTGRES_TLS_CERT_FILE` | Mounted PEM certificate path |
| `POSTGRES_TLS_KEY_FILE` | Mounted PEM private key path |
| `PGDATA` | Optional data directory, use a dedicated persistent volume |

The password must match the emulator's negotiated device credential. Changing the bootstrap secret after initialization does not rotate an existing database role. The optional [credential handler](../../docs/database-contract.md) performs that rotation before management credentials change. Its mutable password file must be writable by the emulator.

The entrypoint copies TLS files with PostgreSQL-compatible permissions. It permits SCRAM over TLS from the configured console IPv4 `/32` and the optional emulator `/32`. Other TCP sources and unencrypted TCP are rejected. Unix socket access is trusted inside the container. Restrict container access and publish port 5432 only on the emulator's dedicated LAN identity. If networking rewrites the source address, resolve that network design rather than broadening the HBA rule.

`pg_tokenizer`, `vchord_bm25`, the E5 tokenizer database integration, `plpython3u`, and the reranking sidecar are not implemented. This is a dense-search preparation profile. Controller modes that require hybrid retrieval or reranking may return no results. No successful controller migration or NAS container run is claimed.

The pinned image tag is listed in the [upstream pgvector installation documentation](https://github.com/pgvector/pgvector#docker). Docker was unavailable during authoring. Shell parsing and rejection cases can be checked locally; image build, TLS connection, HBA enforcement, migration, restart and actual search still require the isolated NAS test.
