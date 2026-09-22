# Database credential rotation

`PgCredentialRotator(config, state_dir)` is the optional async `DeviceService` credential handler. Calling it with a management username and new password always changes PostgreSQL role `unifi-protect`, regardless of the management username. Construction makes no connection. `database.enabled` must be exactly `true`; otherwise the callback rejects the change without database or filesystem I/O.

```json
{
  "database": {
    "enabled": false,
    "host": "127.0.0.1",
    "port": 5432,
    "dbname": "unifi-protect",
    "user": "unifi-protect",
    "password_file": "/state/database-password",
    "sslrootcert": "/state/postgres-ca.pem",
    "timeout_s": 30
  }
}
```

Seed `password_file` with the actual initial database password. It must be a regular private file, mode 0600, in a directory writable by the emulator. A read-only Docker secret cannot serve as this mutable recovery file. The configured user and database are restricted to `unifi-protect`. Remote TCP requires a CA file and `verify-full` TLS. Loopback TCP uses `require` without a CA or `verify-full` with one. An explicit Unix socket directory is also supported. There is no plaintext remote fallback, default-password fallback, shell command or role selected from incoming text.

The callback stages the new password in `<password_file>.pending` before changing the role. It uses real [psycopg SQL composition](https://www.psycopg.org/psycopg3/docs/api/sql.html), with a quoted role identifier and password literal. PostgreSQL utility statements require this composition rather than ordinary server-side bind placeholders. The optional PostgreSQL dependency is loaded only when rotation executes.

After database commit, the callback atomically replaces the current private password file, then removes the pending file. File data and parent directories are flushed. An async lock and a nonblocking process lock serialize rotation. Passwords and raw driver exception text are never logged or returned. Statement, lock, connection and total-operation timeouts bound failures; total timeout defaults to 30 seconds, with up to four additional seconds for cleanup.

If a commit or local write is uncertain, keep both credential files. Retry tries only their current and pending values. If a different requested password arrives, the callback first reapplies and commits the previous pending value, persists it locally, then begins the new rotation. This also works with an explicitly configured trusted Unix socket, where successful connection does not prove that PostgreSQL checked the supplied password. A successful database callback must precede saving management credentials. Repeating a rotation after a later management-save failure is safe.

The PostgreSQL image accepts optional `EMULATOR_IP`, one exact IPv4 address. It adds a second `/32` SCRAM-over-TLS HBA rule for the emulator's management connection. `CONSOLE_IP` remains an exact `/32`, and all other TCP is rejected. Use the actual source address seen by PostgreSQL; do not use a broad container subnet. A shared local Unix socket is an alternative only when the deployment explicitly grants that access.

Tests inject an in-memory connection and use the real psycopg SQL composer. They check staging/commit/write order, quoted SQL, secret permissions, uncertain commits, disk failures, pending recovery, disabled mode and TLS configuration. They do not claim a live PostgreSQL transaction, NAS deployment or end-to-end adoption. Those checks remain outstanding.
