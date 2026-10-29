# Synchronous DNS admission and resolution deadline — 29 October 2026

The [earlier transfer deadline](provider-response-deadline-20261029.md) bounded
HTTP/TLS I/O but could not interrupt its caller's native `getaddrinfo`. A local
synthetic reproduction with a 50-ms connection budget returned after 359 ms when
the resolver stalled for 350 ms; it correctly started no HTTP, but still held
the caller beyond its budget.

## Implemented boundary

Hostname resolution now uses the same native system resolver in a short-lived
isolated Python stdlib process (`-I -S`), not a third-party DNS service. One
remaining transport budget covers admission, child startup and resolution. At
most four children are admitted per application process. Deadline/cancellation
kills and collects the child, closes pipes and returns its slot; there is no
uncancellable resolver thread or automatic resolution retry. Ordinary literal
IPs still need neither DNS nor a helper.

The child receives only hostname/port on stdin, a minimal PATH/C locale
**without application/provider/proxy credential environment**, and no inherited
application file descriptors. It starts no device, provider or configuration
service. It returns at most 64 address records/32 KiB. The parent validates record
shape, port and IPv6 scope before the existing complete-answer policy and exact
numeric socket connection. Verified original TLS identity/SNI/Host, role keys,
proxy/redirect refusal and remaining header/body budget are unchanged.

AI Port's separate pre-paid-request DNS probe now has a five-second cap. Its
existing retry delay and paid allowance remain intact. A DNS deadline has the
fixed subtype `api_detection_dns_timeout`, separate from an actual resolver
outage. Failure to start/run the helper is instead
`api_detection_resolver_unavailable`, not invented DNS-outage evidence or a
reason to select local fallback. Address-policy refusal still cannot fall back.
The existing local fallback policy may handle the distinct DNS-timeout case;
no provider request has started at the preflight boundary.

This is a DNS/transport boundary, not a combined preflight-plus-model/fallback,
arbitrary CPU/body-iterator or universal kernel-preemption deadline. Process
creation and OS kill/collection are still operating-system operations. The
asynchronous aiohttp resolver is unchanged; its surrounding asynchronous request
budget remains separate.

## Checks and limits

- Targeted tests exercise real localhost resolution and real child processes,
  forced stalls, cancellation, four-child saturation, pipe/process collection,
  stripped environment, malformed/excess replies, numeric-IP bypass and fixed
  helper/outage/timeout subtypes without an external provider request.
- Existing complete-answer, DNS-change, TLS/SNI/role-key, slow HTTP/TLS header/body,
  proxy/redirect, detection-budget and model tests remain applicable.
- Test DNS seams now inject the bounded resolver rather than monkeypatching a
  parent-process system call that no longer performs the production resolution.
- No new dependency, public DNS provider, paid/model request, native camera,
  scope, credential, budget, running NAS image or configuration change.

Source/fixture checks are not a live-provider outage, sustained operation or
complete security audit. Exact-head CI and separate process-harness results are
reported on the associated PR before integration.
