# Administrator authority after logout — 29 October 2026

## Reproduced failure

Logout previously expired only the browser cookie. A copy made before logout
still authorized a later read and an actual configuration mutation. Recreating
the security policy with the same persisted signing key also accepted that
old signed session. Three synthetic regression cases fail on baseline source.
This is not evidence of access to a real deployment or a stolen real credential.

## Correction

Signed cookies retain the existing HMAC/expiry/Origin/CSRF checks and must also
belong to the current server's issued-session registry. Logout, after successful
mutation authorization, revokes just that session before expiring its cookie.
A fresh server/policy has no issued sessions, so restart cannot revive old
authority. No signing/password/device/provider key is rotated or copied.

The registry stores only random session IDs and expiry, defaults to 128 entries
and is bounded to at most 1,024 by the policy constructor. A full registry denies
new login without evicting another valid session; the UI distinguishes this from
a bad password. Expired entries are pruned under a lock, including on rejected
expired-cookie checks, so a later clock rollback cannot resurrect them.
Registration is locked against concurrent overbooking.

## Checks

The combined authentication/session/control/secret/HTTPS/audit/speech/role run
passes **140 tests** after integrating the saved-speech-permission change. New cases
cover copied-cookie read and mutation after logout, same-key policy restart,
scoped/idempotent revocation, no eviction at capacity, slot reuse after logout,
expiry/clock rollback, simultaneous valid logins, rejected Origin/CSRF logout
and an honest capacity error without leaking a cookie. The three original
regressions now pass. A separately reproduced main-form expiry assertion (500)
now redirects to login; four async audio-form/snapshot cases refuse or redirect
when authority expires or is revoked during an awaited read, without writes or
new form tokens. Inputs/credentials are invented; HTTP is loopback only.

## Scope

This is source/fixture evidence; no running administration service, Gateway,
native device, permission or provider configuration changed. Cookies must be
newly authenticated after a control-site restart. Logout affects **future**
authorization, not an already admitted save, model test, callback or processor
job. In-flight results still need reconciliation. Broader threat-model,
deployment, browser/mobile and native acceptance in #3/#13/#17 remain open.
