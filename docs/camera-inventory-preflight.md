# Read-only camera inventory preflight

`local-aikey inventory` reads the local Protect integration API and writes a private report. It does not alter camera settings, enable inference, grant processing scope or send footage to a provider. This is the first, read-only part of [issue #9](https://github.com/Olli0103/unifi-ai-key-emulator/issues/9), not completion of automatic all-camera operation.

The client uses exactly two GET routes from the [official Protect 7.3.60 OpenAPI contract](https://developer.ui.com/protect/v7.3.60/openapi.json): `/v1/meta/info` and `/v1/cameras`, under the local `/proxy/protect/integration` prefix. It requires the version to be exactly 7.3.60. A different version, authentication failure, redirect, invalid JSON, duplicate ID, unexpected camera state or expired/mismatched certificate stops the read. It does not reuse an old report as fresh processing authority.

Protect's web certificate may be a self-signed leaf that is not a CA certificate. The command requires both a private trust record with the approved SHA-256 leaf fingerprint and a private copy of that leaf certificate. It checks that they match and checks the certificate's validity dates. For each TLS connection it verifies the peer leaf against the pin before aiohttp can send the `X-API-Key` header. The host must be an explicit private IPv4 address; redirects and environment proxies are disabled. The key and trust files must be regular files readable only by the owner.

Use an existing Protect integration API key kept in a private file. Generate one through the Protect integration settings if needed; never put it on the command line or in the repository. The trust record has `host`, `port` (443) and `sha256` fields. Obtain the fingerprint through an independent verification step before creating the record. The certificate file is the pinned web leaf, distinct from the device-service certificate on port 7442.

```sh
.venv/bin/local-aikey inventory \
  --config /private/state/config.json \
  --api-key-file /private/state/protect-api-key \
  --web-trust-file /private/state/protect-web-trust.json \
  --web-cert-file /private/state/protect-web-cert.pem
```

The command writes `camera-inventory-preflight.json` and a static `camera-inventory-preflight.html` beside the API key with mode 0600 and prints counts only. Open the HTML file locally to review names, models, onboard smart types and reasons. It has no JavaScript or external resources. `smart_event_candidate` means Protect reports onboard smart detection types; it does not prove that the AI Key emulator will receive or persist an event from that camera. `legacy_ingress_needed` means the camera reports no onboard smart detection types, so an AI Port profile or another native-verified ingress path is needed before legacy enhancement. `offline` means no processing can be inferred while the camera is disconnected. All report entries have `processing_enabled: false` at the report level.

The 7.3.60 controller used for the first local preflight returned `smoke_cmonx` in `smartDetectAudioTypes`, although the published 7.3.60 OpenAPI enum omits it. The parser preserves that observed value. No audio processing is enabled. Camera names and identifiers stay in the private report; synthetic tests contain invented values only.

Next, compare the report to Protect's Devices page, then integrate a fresh registry with admission and the global budget. Until that boundary is tested, this inventory must not be used to switch on all-camera processing.
