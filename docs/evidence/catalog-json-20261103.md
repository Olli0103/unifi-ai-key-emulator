# Model catalog wire-JSON boundary — 3 November 2026

Source/fictional HTTP progress toward #13 and #17. The broader issues remain OPEN.
No real provider, saved configuration, credential, model selection or live service
was changed or contacted.

## Reproduced gap

The [existing catalog client](provider-model-catalog.md) bounds bytes, models,
pages and time, but decoded its wire body with the standard permissive JSON
loader before applying the envelope/pagination checks. Repeated object fields
therefore used their last value. A response with conflicting pagination fields
could appear complete; repeated model IDs, capability or remote-marker fields
could be silently interpreted rather than rejected. Non-finite values in ignored
metadata and auto-detected non-UTF-8 JSON were accepted too.

Owned loopback GET regressions on parent `51347cf96320bcfb4de34b8f528c52bec1851c0d`
record **13 failures and four valid controls passing**: twelve invalid first-page
bodies were accepted, and an ambiguous second page returned a catalog rather
than invalidating the whole read. All fields, keys, model names and servers are
fictional. An initial fixture omitted required synthetic credentials in three
cases; that setup failure was corrected before recording these parent results.
This is a source counterexample, not an observed real-provider incident.

## Narrow implementation

The client now reuses the existing maintained protocol JSON-object decoder,
already used by other administration/provider readers. It rejects duplicate
fields at every object level, non-finite constants and overflowing floats,
non-UTF-8 input, malformed JSON and non-object roots before catalog interpretation.
The existing fixed `catalog_response_invalid` error boundary remains; response
bodies, model labels and keys do not enter that error.

No new decoder, library, retry or fallback is added. Provider destinations,
credentials, verified TLS/DNS behavior, cookie/proxy/redirect/compression policy,
aggregate limits, deadlines, cursor handling, HTML escaping, rate limiting,
revision/session/CSRF checks and safe returned-field projection are unchanged.
A failed later page returns no accumulated list and causes no third request or
retry. Finite unique UTF-8 metadata remains compatible for all four adapters;
unused metadata is still omitted from results.

## Qualification and limits

- Seventeen real-loopback wire tests: twelve invalid bodies, one invalid second
  page, four valid provider controls (OpenAI, compatible API, Anthropic, Ollama).
- Combined catalog/client/control-site/provider-network/mutation-limit group:
  **134 passed** after the correction.
- No new browser flow or DOM change. Existing full CI/browser qualification is a
  separate exact-head gate, not inferred from these focused tests.

No real provider catalog/inference, native search, enrollment, production image
build/deployment, quality/capability/billing test or independent human review is
claimed. The previously qualified coordinated candidates use their frozen older
source and are not silently relabelled as containing this change.
