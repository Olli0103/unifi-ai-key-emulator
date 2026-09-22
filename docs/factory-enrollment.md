# Temporary factory enrollment

Factory enrollment is off by default. It supports the Protect adoption flow that submits the public factory credentials `ui` / `ui` without a custom-password field. That flow was observed on Protect 7.3.56 and completed native adoption using Apple container 1.4.1. It does not replace the generated private management password.

To enable a bounded attempt, set `device.factory_enrollment_until` to an integer Unix timestamp at most ten minutes ahead and restart the emulator. The controller must have an explicit trusted SHA-256 fingerprint in `controller.expected_fingerprint`. The configured management username must be `ui` for factory credentials to work. A missing field or zero disables factory enrollment; an expired timestamp stays inactive and does not prevent startup.

Print a deadline without changing configuration:

```sh
python3 -c 'import time; print(int(time.time()) + 600)'
```

Use that value promptly. The emulator also bounds the active window using its monotonic clock, so moving the wall clock backward does not extend the running process's enrollment window. Reopening an expired window requires another explicit configuration change.

During the window, an unadopted device accepts the exact factory pair for HTTPS `POST /api/info` and `POST /api/adopt`. Plain HTTP is allowed only in the loopback lab. The normal generated password continues to work. Adoption still requires a valid token and a hosts list containing the configured controller; this option does not change controller trust or address restrictions.

Successful use saves only a boolean `factory_enrollment_used` marker. It does not save a factory password or change the generated password file. Factory HTTP authentication stops as soon as local adoption is confirmed or the deadline expires. After any successful password rotation, factory authentication stays disabled even if the deadline has not passed.

The initial `changeUserPassword` command may use the factory password as its old password only when all of these conditions hold:

- The bounded window is still active and the factory-use marker exists.
- Local adoption is confirmed.
- The command arrived on the current, pinned control connection, and that same connection has completed matching time sync.
- No management-password rotation has already been recorded.

A rotation arriving before time sync can wait for up to five seconds without blocking the time-sync handler. The final checks still apply after that wait. A changed token, a superseded connection, a failed confirmation, or expiry cannot authorize rotation. The database credential hook, when configured, must succeed first. A successful rotation stores only the new password hash and removes the factory-use marker; it cannot retain `ui` as the new password. Failed rotation preserves the marker for a retry within the window.

`device.status.management` reports request counts and fixed outcomes. It never includes the supplied username, password, request headers, or token. `adopt_accepted` means that the local HTTP adoption request was saved; it does not prove that Protect completed adoption.

This path passed local HTTP and pinned TLS/UCP tests, including rotation arriving before time-sync confirmation. A native test with Protect 7.3.56 on a UDM Pro Max also confirmed adoption, control time synchronization, password rotation and reconnect after a planned restart. The post-restart check showed adopted/connected state, no pending token, a saved rotated-password hash, no factory-use marker and HTTP 401 for factory credentials. The enrollment deadline was disabled after pairing.

Those observations cover enrollment and control only. Native descriptions, persistence and search remain unverified. The static credential-selection contract comes from Protect 7.2.105; the live test provides separate evidence for this 7.3.56 enrollment path.
