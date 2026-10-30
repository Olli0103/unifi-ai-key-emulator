# Native administration monitoring — 30 October 2026

This is a later, narrow follow-up to the
[private HTTPS checkpoint](native-admin-private-https-20261030.md) and
[read-only audio-control activation](aiport-audio-permissions-20261030.md).
It supersedes their earlier monitoring-unqualified status only for the checks
below, not certificate renewal, browser or broader native acceptance.

## Reuse the actual monitor's supported path

The existing processing-readiness monitor uses a push path because Kuma could
not reach that Key's macvlan address. That finding was not transferred to the
new host/VPN-bound admin origin: an actual request from Kuma's container
resolved its existing node DNS and returned HTTP 200 with exactly
`{"service":"control-site"}`. Default TLS and hostname verification passed,
with 89 days remaining. No DNS, network attachment, firewall, Serve policy,
system root or certificate was changed.

A preliminary local observer prototype was not installed. Kuma 2.5.6's built-in
HTTPS/JSON-query/certificate features cover this target, so no additional push
checker, user unit/timer, loopback push credential or competing recovery loop was
introduced. The existing Key monitor and model-server watchdog remain separate.

| Observation | Existing processing check | New administration check |
| --- | --- | --- |
| Client/path | Existing pinned health read inside Key, then push | Native Kuma HTTPS client to private admin origin |
| Readiness | Adoption/control/database/fresh inventory | Exact `service == control-site` JSON result |
| TLS | Existing deployment certificate trust | Default CA and origin-hostname verification |
| Model/media request or restart | None from readiness check | None |

## Actual configuration and provider readback

One dedicated native `json-query` monitor was added through the supported Kuma
API. It uses GET `/healthz`, accepts only HTTP 200, disables redirects, retains
strict TLS, and enables the existing certificate-expiry notifications. The
interval is 60 seconds with a 30-second retry interval, one retry and a
10-second timeout. Existing global expiry thresholds **21/14/7 days** were
preserved. The existing operator phone-notification definition was linked;
no new notification definition or forced delivery test was introduced.

The installed query evaluator passed an expected identity and rejected a
fictional different identity without creating a false DOWN heartbeat. Exact API
readback verified the new target and all declared query/TLS/status/interval/
notification fields. Independent query-only database readback then found two
stored heartbeats including actual **UP** and a successful identity query,
alongside valid, hostname-matching native TLS metadata. This is more than a save
acknowledgement. The prior **28 complete monitor records** matched their private
before-copy; only one new monitor was added. No existing monitor record or global
expiry threshold was changed.

Private locators, operator credentials, before-records, intent and raw TLS data
remain outside this public repository. Kuma credentials used their existing
protected host-bound route and were not copied into the NAS checker/service.
No new administration credential, camera permission, processor/container image,
model setting or native pairing was changed by adding this observation.

## Remaining limits

This verifies initial direct monitoring, not real-browser/mobile accessibility,
authenticated end-to-end operations, native camera/event correctness, model
quality, certificate rotation, expiry-notification delivery or owner credential
handoff. Notification linkage is not evidence that a phone received an alert.
Kuma is on the same NAS, not a hardware-independent failure domain. A complete
host outage cannot be qualified from this monitor alone. Initial stored UP does
not establish 72-hour/7-day endurance, reboot/power-loss/full recovery or an
unattended future renewal. Automatic certificate renewal remains open; broad
security/deployment issues remain open.
