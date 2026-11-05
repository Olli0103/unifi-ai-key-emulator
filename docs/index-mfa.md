# Interactive MFA for native index access

The opt-in `--native-mfa totp|push|email|sms` extends only native export/readback
login. No index/model/account configuration is changed by authentication.
Username/password remain owner-only files. A six-digit code is read only from
the controlling terminal with echo disabled; there is no code argv/env/file
option. Challenge, session cookies and code remain in process memory.

The selected method must match exactly one unblocked advertised authenticator.
Ambiguous/unsupported challenges fail closed. Code submissions happen once;
invalid/expired codes do not retry. Push approval polls only the fixed local
endpoint, at most 90 requests within a 180-second absolute MFA budget. All
requests retain exact-leaf TLS pinning, bounded bodies, no redirects, and the
original private controller. Login/bootstrap and logout retain their individual
time limits. Cancellation restores terminal flags and closes the owned session.
A subsequent export/readback command requires a fresh interactive login.

Protocol evidence (2026-11-04): the controller-delivered UniFi OS frontend's
LocalAuthLogin module posts username/password/rememberMe/token to
`/api/auth/login`, handles HTTP 499 `data.mfaCookie`/`authenticators`, sends
selected email/SMS challenges to `/api/sso/login/mfa/send`, and polls push at
`/api/auth/login/poll`. No browser session or cookie is imported. Modern
challenge metadata is required; old undocumented challenge shapes are refused.

Validation: real loopback pinned TLS tests cover success, invalid/expired code,
redirect/refusal, timeout/cancellation, ambiguity/blocked/unsupported methods,
email/SMS delivery order, and push outcomes. PTY tests prove echo suppression
and restoration on success, invalid input and cancellation. These are synthetic
protocol tests, not live-controller MFA or historical export acceptance.
