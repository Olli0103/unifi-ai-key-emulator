# Bounded Key and embedding credential-file loading — 29 October 2026

The Key's management/vision/speech hydration and the E5 HTTP embedding bearer
loader used plain `Path.read_text`. A pipe could block startup/readiness or
the embedding event loop; symlinks, group-readable inputs and unbounded files
could be read before later client validation. Invalid UTF-8 also escaped the
normal role-specific domain error. This is a local file-input boundary, not
a demonstrated remote incident or credential disclosure.

A small shared standard-library reader now opens the leaf read-only,
nonblocking and no-follow; verifies a regular file with no group/other mode
bits and a 1–4,096-byte size on the same descriptor; bounds the actual read
again to catch post-stat growth; and decodes nonempty UTF-8. Trailing/leading
whitespace stripping is preserved. It makes no parent-path, encryption,
secure-erasure or owner-identity attestation. It does not chmod, repair,
replace, rotate, revoke or remove any credential.

Hydration still returns a deep copy, with the same management minimum length,
independent vision/speech roles and absent optional-key behavior. Failures use
fixed `ConfigError` role messages. Embedding token failures use the existing
`EmbeddingError` message; internal newline refusal remains in place. The token
is checked before even creating a new HTTP session, and before any request.
No provider URL, key reference, permission, budget, identity or protocol changes.
This is additional loading validation, not a provider-authentication result.

## Synthetic checks

- Prior source fails 22 of 27 initial cases: symlink/private-mode/oversize/
  invalid-UTF8 refusal across roles, empty vision/speech files, and four
  actual-reader FIFO timeouts after confirmed process entry. Five existing
  positive/refusal controls pass. No provider was contacted: the embedding
  session double fails immediately if its `post` is reached.
- Fixed targeted run: **100 passed** across loader, configuration/TLS,
  embedding/search and real loopback credential-isolation/route tests.
  Additional controls verify the exact UTF-8 byte limit, growth after `fstat`,
  closed descriptors on refusal and directory refusal. The existing embedding
  credential-isolation fixture now explicitly creates its synthetic key with
  mode 0600; its endpoint/redirect/isolation assertions are unchanged.
- Caller configuration dictionaries and credential bytes remain unchanged;
  invalid-input errors contain neither the synthetic key canary nor its path.
- Ruff, public-source audit and diff checks pass. No third-party dependency
  or external inference was introduced; production services/configuration stay
  unchanged. No real credential value entered tests or reports.

This is a source fix. Before deployment, existing credential mounts must be
checked as private regular files. A previously permissive/symlink setup will
now fail closed rather than being silently repaired. The already running
Key/Port deployment is unchanged by merging this source.

## Read-only current Key preflight

A separate bounded check inside the existing Key container validated its one
currently referenced management credential against the new regular/private/
size/nonempty-UTF8 loading conditions. Other Key vision/speech/embedding
credential references were not configured; they were not imputed as passes.
The actual value stayed inside the container and was never returned. Config
bytes, container ID/image/restart count stayed unchanged, with zero writes or
model requests. This does not activate the fix, test provider authentication
or validate other installations, inactive roles or future file replacements.
