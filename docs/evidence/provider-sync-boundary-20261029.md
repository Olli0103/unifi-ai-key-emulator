# Sync provider and enrollment boundary — 29 October 2026

Source/fixture progress for #3, #5, #8, #13 and #20, not native identity,
quality, deployment or whole-issue acceptance.

AI Port model/JPEG-score clients check every IPv4/IPv6 answer before creating
any socket, then connect to the exact checked numeric sockaddr. An approved
preflight is not transport authorization: the actual dial checks its own
answer too. HTTPS retains certificate/hostname verification and original
SNI/Host, without a device certificate. Environment proxies, redirects,
unsupported URL protocols and proxy tunnels are refused.

Known disallowed answers also fail before the AI Port claims its paid request
allowance. Address-policy failures have a distinct fixed code and cannot select
the configured local fallback. Genuine DNS outages keep their fixed subtype
and backoff behavior. Request counts do not establish exact upstream billing.

Candidate connection attempts share a remaining monotonic budget. A socket
completing after that deadline is closed before HTTP can start. Failed and
interrupted attempts close provisional sockets; interrupts are not retried.
**Limits:** blocking libc DNS cannot be forcibly interrupted here; elapsed
time is checked when it returns. Existing HTTP socket/read timeouts are not a
proven total response deadline. This is not sustained-operation qualification.

## Face enrollment

The CLI helper formerly used the environment-aware default opener and followed
a 302 to another loopback server in a synthetic trial. It now uses an isolated
guarded opener and a validated private/loopback root endpoint without userinfo,
query or fragment. Special/link-local addresses, redirects and proxies fail.

Responses are limited to 256 KiB and strict, finite, duplicate-free JSON. One
face with a usable 128-value template is required before storage. Failures
preserve existing identity bytes and emit fixed CLI errors, not URLs, provider
text, images, names or vectors. Scaling before normalization preserves extreme
finite directions instead of storing zero templates or underflowing. Existing
identity files are not rewritten or repaired automatically. No real photo,
template, name or enrollment was used.

## Checks

On the unchanged enrollment baseline, **17 of 20 new cases fail**; three already
pass. Synthetic loopback tests cover redirects, environment proxies, bounded
and malformed replies, unchanged existing identity bytes after CLI failure,
redacted errors, and successful single-face transport. Socket fixtures cover
private/mixed/transition answers, checked numeric peers without another lookup,
shared deadlines, late success, interrupt cleanup and zero sockets on policy
failure. Real loopback TLS tests retain verified SNI/Host and reject an
untrusted certificate before HTTP.

The older DNS-recovery fixture used an incomplete two-field `getaddrinfo`
tuple. It now supplies a realistic complete global-answer tuple; its recovery
and budget assertions are unchanged. No external service or paid inference is
contacted. No deployment, host proxy/DNS setting, camera scope, controller
permission, adoption identity or actual stored template changed.
