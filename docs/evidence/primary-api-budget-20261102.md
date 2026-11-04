# Primary API request budget and safe error subtypes — 2 November 2026

Part of [#14](https://github.com/Olli0103/unifi-protect-local-ai/issues/14),
[#17](https://github.com/Olli0103/unifi-protect-local-ai/issues/17) and the
bounded measurement prerequisites of [#7](https://github.com/Olli0103/unifi-protect-local-ai/issues/7).

## Observed boundary, not an inferred root cause

The prior [primary-schema evidence](primary-detection-schema-20261102.md)
records a terminal partial unstructured run: 12 planned, 3 terminal, 1 usable
correct, 2 errors, 9 unrun. A separate schema-enabled run froze source
`b702a2143b4945e2ec15f92eb6e79f30393e4364`, the same twelve public images,
labels, model snapshot, threshold, prompt and 1,024-token budget. No retries,
private camera requests or server/model reconfiguration occurred.

| Schema-enabled pilot | Count |
| --- | --- |
| Planned | 12 |
| Started and terminal | 3 |
| Usable and correct | 2 |
| Errors | 1 |
| Unrun / unknown | 9 |
| Unsettled attempts | 0 |

The first two cases returned valid correct answers (10,057 and 9,687 ms).
The third yielded no response-body receipt at 15,029 ms; its retained fixed
code is `api_detection_request_failed`, without an original subtype. Admission
stopped under the predeclared uncertain-transport rule. Its cause is **unknown**:
elapsed time alone does not establish a server, GPU, driver or connection cause.
Neither run is a complete matrix, full quality PASS, calibrated target, general
speed result or hardware-only comparison. Both original partial records remain
unchanged. The original 29 October pilot remains unchanged too.

A separate clean model context inspected all twelve public JPEGs, froze its
labels before reading gold and agreed on 12/12. It checked 33 partial-count and
arithmetic invariants. This is a model-context review, **not independent human
annotation** or an authentication of private provider records. Source-summary
and review hashes were separately checked by the coordinator. Exact public
inputs/labels were reused; public COCO data is not attested model-held-out data.

All 29 official model files, eight existing process identities, five saved
profile/TLS sets and six work-fence links matched before/after the schema pilot.
The owned helper settled and was removed. Production profiles, services,
permissions and model configuration were not changed. This does not replace
native end-to-end event/zone, private/night/IR/weather/occlusion or reference
hardware acceptance.

## Saved primary-only option

`live_pool_detector.request_timeout_s` is an optional **integer 2–60 seconds**,
default **15** when absent. It reaches the real detector factory and the existing
HTTP transport's absolute monotonic deadline. The HTTP deadline implementation
was inspected and deliberately left unchanged: no new timer, retries, client
library or per-read timeout reset. No default timeout increase is hidden here.

Only the built-in primary object-detection request uses this option. Separate
OCR/crop/fallback budgets and injected/custom transport ownership stay unchanged.
The request payload, image order, sampling, model, parser, score gates, request
allowance and fallback policy are unchanged. A longer chosen budget can occupy
the single worker longer; it is not an event/queue-wide deadline or a quality fix.

The provider form shows a numeric choice and its restart requirement. Saving
only changes the saved profile, not the running process. Older provider clients
omitting the new field preserve an existing nondefault value. Invalid types,
bounds, repeated/markerless new fields and malformed values refuse before an
admitted write; provider rollback restores exact saved bytes. No camera consent,
endpoint/key scope or real deployment is added.

## Type-only transport diagnostics

The existing public failure codes (including DNS, HTTP and 429 quota categories)
and their backoff/fallback decisions are preserved. Future generic transport
errors additionally carry only a fixed type-derived `subtype`:
`timeout`, `tls`, `connection`, `io` or `unknown`. Exception messages are not
parsed. A timeout subtype reports a local exception type, **not** its remote
root cause. Raising outside the catch handlers avoids retaining private raw
provider exceptions through `__cause__` or `__context__`.

This diagnostic does not retroactively supply missing subtypes for old runs.
Malformed/oversized replies retain their existing fixed errors. Score-server
multipart transport and other provider boundaries are outside this change.

## Verification scope

Source tests cover strict early validation, default and explicit endpoints of
the range, the real primary call, custom transport replacement, unchanged
auxiliary callable ownership, content-free diagnostics, actual runtime-factory
forwarding, old-client preservation, exact rollback, authenticated form refusal
and type-only transport/DNS/HTTP/quota errors without private exception chains.
The existing deadline tests remain applicable to unchanged `provider_http.py`.
The existing owned desktop/mobile/keyboard schema browser scenario also checks
default 15, explicit 30, preservation through a provider edit, reset to 15, private
audit records and session expiry. It performs no real provider/model request.

No live rollout or broad issue acceptance is claimed. A future different-budget
model run must have its own frozen configuration and receipt, not resume or
relabel either partial pilot.
