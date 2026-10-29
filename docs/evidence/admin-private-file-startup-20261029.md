# Administrator private-file startup refusal — 29 October 2026

The administration CLI checked signing-key/password-record file type only
**after** a blocking `open`. A named pipe at either configured filename could
therefore block startup waiting for a writer, instead of being refused. This
is a local startup/availability bug, not demonstrated remote authentication
bypass or an observed production incident.

The private reader now uses `O_NONBLOCK` along with `O_NOFOLLOW`. The same
opened descriptor must still be a regular file owned by the effective user,
with private permissions and nonzero size within its limit. No file is
rewritten, no secret is changed and no permission check is relaxed. The TLS
listener's separate credential reader already uses non-blocking opens.

## Checks

Two real CLI subprocess tests create synthetic private administrator state and
replace either the signing key or password record with a FIFO, without a
writer. Both fail on baseline after a bounded five-second timeout; the test
runner kills and collects those subprocesses. With the fix, both exit with
code 2 and the existing generic `Control site unavailable: ValueError` output,
without a listener, application config, audit write or disclosed file value.

The additional positive/negative test confirms a regular private file still
reads exactly, while an over-limit file, group-readable mode and symlink are
refused without changing source bytes. All three new tests pass. Separately,
73 existing authentication/session/control/secret/HTTPS tests passed against
the corrected source. The initial fixed-source run also exposed a test-only
expectation mismatch: the CLI intentionally prints the error **type**, not
the private reader's message. The final regression preserves that generic
output rather than changing the CLI to expose more detail.

Ruff and diff checks pass. No production/Gateway/administration service,
configuration or credential is changed by this source fix. This is CLI/unit
evidence, not browser/mobile, external security review or a complete #3/#13
administration acceptance result.
