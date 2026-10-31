# Optional native camera status reader

The public Integration camera inventory does not supply the current recording
flag, complete native channel metadata or a reliable PTZ capability flag on the
observed controller. Missing is not false. Its existing credential is not
silently reused as a native account password.

The separate optional reader supplies `/camera-status` and the authenticated
`GET /api/admin/v1/cameras/native-status`. It does not add a listener, permission
grant, pairing, PTZ action, recording control, media fetch or model call. Normal
inventory, processing and configuration continue without it. An unconfigured
reader explicitly reports `not_configured`, with `cameras: null`, not an empty
success. Viewing the ordinary inventory or home page does not log into Protect.

## Operator opt-in

Use the existing complete pinned inventory settings and both additional Admin
CLI arguments:

```text
--native-status-username-file /private/existing-protect-reader-username
--native-status-password-file /private/existing-protect-reader-password
```

These are file paths, never literal secret values on a command line. Files must
be same-user, private regular files; symlinks, FIFOs, controls and oversized
values are refused before HTTP. The CLI does not create accounts, grant rights,
write credential files, import/export browser cookies or copy the Integration
key. Use only an explicitly authorized existing account permitted to read the
native metadata. No configured credentials are discovered from another service.
Omitting either file, or the complete pinned inventory settings, refuses startup.
The default with neither file remains unchanged.

## Fixed transport and session boundary

Only a literal private IPv4 controller, the existing exact certificate pin and
port 443 are accepted. Pin validation precedes any HTTP credential header/body.
Environment proxies, caller-selected URLs/paths, redirects and browser-cookie
imports are not used. Each uncached explicit read creates its own in-memory
session and makes only these fixed requests:

1. `POST /api/auth/login`, with `rememberMe: false`.
2. `GET /proxy/protect/api/bootstrap`.
3. `POST /api/auth/logout`, also on parse/read failure or ordinary cancellation
   after a possibly accepted login. A definitive rejected login without a
   returned cookie does not need a logout.

The bootstrap is streamed with a two-MiB maximum, strict duplicate/nonfinite JSON
rejection and a supported-version/identity/shape contract. Login/logout responses
have separate bounded drains. One read has a 15-second exchange budget plus a
three-second cleanup bound; filesystem I/O is not forcibly preempted. MFA and
interactive challenge completion are not implemented. Credentials/cookies/CSRF
tokens, raw replies, URLs, aliases and private diagnostics never enter the result.

There is no automatic login retry or background poll. Concurrent callers share
one serialized observation; successful data is cached only within the original
30-second attempt window and is age-checked at publication. Failure does not
serve expired good data. A failed login is latched for the same credential bytes
in that process, including after the ordinary backoff. Uncertain logout prevents
another session in that process. Reconcile server-side session uncertainty before
an operator restart; no force-clear/recovery HTTP operation is supplied.

`session_logout_acknowledged: true` means the fixed logout request received
HTTP 200/204 and local cookies were discarded. It does **not** prove server-side
token revocation or expiry. Cancellation/process or power loss cannot guarantee
remote session cleanup. No cookie/token is persisted for later reuse.

## Observation semantics

- `recording_active` is the native momentary `isRecording` flag: true, false or
  unknown. It is not configured recording mode, retained footage, playback or
  compatible edge-recording proof.
- `ptz_capable` is the native `featureFlags.isPtz` flag: true, false or unknown.
  Missing cannot be inferred from model name or an available command route.
  A capability flag is not movement status, authorization or a successful PTZ
  command. This reader never sends such commands.
- Channels contain bounded IDs, dimensions, fps and an optional reported RTSPS
  enable flag, not stream URLs/aliases. They do not prove which channel is
  currently selected, decoded, recorded or usable by an AI Port.
- IDs/MACs must be valid and unique; names are bounded and HTML-escaped. Only
  validated allowlisted fields are published, with an observation timestamp and
  fixed source. This does not replace original native acceptance checkpoints or
  configure new camera/recognition/privacy scope.

## Evidence and limits

The existing owner browser read on Protect 7.3.74 observed eleven recording flags
and no positive PTZ capability flags. That manual observation is not a persistent
Admin source or proof of this new credential-based session adapter. Native
activation with authorized credential files, actual-client GUI, positive PTZ,
recording playback/edge recording, ONVIF and full feature parity remain separate
acceptance. No real native account/session was created to test this source.

The existing public API gap is also documented by the maintained
[uiprotect capability inventory](https://github.com/uilibs/uiprotect/issues/1154);
the [official 7.3.74 camera-detail schema](https://developer.ui.com/protect/v7.3.74/get-v1camerasid)
does not supply a current recording flag. This adapter reuses the project's
existing aiohttp/pinned transport instead of adding a second full device-control
library and its broader authentication/control/dependency surface.
