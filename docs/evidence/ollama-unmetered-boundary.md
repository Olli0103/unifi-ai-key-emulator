# Unmetered caption endpoint boundary (#12)

29 October 2026, synthetic configuration checks only. No service was contacted,
no inference was run, and no deployed provider or budget changed.

The locality helper previously returned true for every `provider: ollama`, before
looking at `base_url`. With `allow_remote: true`, an HTTPS public Ollama endpoint
therefore accepted `continuous.unmetered: true`. Switching a local configuration
to a public Ollama endpoint could retain the budget waiver unintentionally.

Three negative cases reproduce the old acceptance: a public hostname, public
IPv4 and public IPv6. The correction checks endpoint location for both Ollama and
OpenAI-compatible adapters. Only loopback/localhost, RFC1918 or IPv6 unique-local
addresses qualify. Unknown hostnames are not guessed to be local. IPv4-mapped
IPv6 addresses are judged by the mapped IPv4 address. Ollama's omitted URL still
uses its documented loopback default.

Public Ollama remains supported with the ordinary rolling budget: the fix does
not ban that provider, change model selection, fall back to another destination,
or impose the paid-caption ceiling on explicitly unmetered local processing.
Existing private-backend policies remain valid.

A private endpoint is **not proof of zero cost**: it may proxy a paid upstream,
and a local runtime may route a model remotely. `unmetered` is an explicit owner
waiver, not automatic billing evidence. Keep such charges and routing visible;
never use a provider name or private address to assert a measured zero cost.

This is a source/configuration boundary fix. Native sustained caption quality,
crash/budget evidence and the remaining #12 acceptance are separate.
