# Synchronous provider transfer deadline — 29 October 2026

Progress for #3/#24; the earlier connection-boundary work remains intact.

A socket timeout per receive is not an overall request deadline: a server can
keep sending individual header/body bytes before each idle timeout expires.
Independent loopback HTTP/HTTPS fixtures reproduce this against the prior
source: three of four slow-drip cases return after their intended budget
instead of timing out; one TLS header case already times out. This is the
recorded baseline, not a claim that every old transport always overruns.

The guarded stdlib connections now establish one monotonic budget when a
request begins. DNS/connect/TLS elapsed time is included; checked numeric
sockaddrs, hostname/SNI verification and all-answer policy are unchanged.
Every request send and raw response read gets only the remaining budget.
Header parsing and buffered response reads share it; a late cached result is
refused rather than returned after expiry. Socket/makefile ownership and file
references are retained so ordinary response cleanup closes the transport.
Failures are not retried by this layer, including an uncertain late send.

This applies to the existing AI Port API detector/score and local enrollment
opener. It does not change providers, destinations, permissions, credentials,
camera scope, paid-call accounting, fallback policy or the deployed NAS image.
No remote/model/photo request was used for verification.

## Limits

Blocking libc name resolution still cannot be forcibly cancelled here. A late
resolver may hold its caller longer, but cannot start a connection/HTTP transfer
after the original budget expires. This is not an interruptible DNS or universal
whole-process deadline. Caller CPU work and arbitrary user-supplied body
iterators are not preempted; current provider/enrollment requests use bounded
already-prepared payloads. No live slow-provider or sustained-operation proof
is claimed, and no controller/private media is used in the tests.
