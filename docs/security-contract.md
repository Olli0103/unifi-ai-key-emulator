# Security contract

This contract defines the security boundaries for the current processor and administration site. It separates implemented controls from design requirements. A checkbox or configuration field does not prove that a future feature is safe.

## Trust boundaries

| Boundary | Data crossing it | Current rule |
| --- | --- | --- |
| Protect controller to device service | Adoption credentials, commands and task metadata | Verified TLS, optional leaf pin, bounded protocol messages and fixed command handling |
| Controller media service to worker | Images, exported video and response headers | Exact controller-origin allowlist, no redirects, size limits, fixed media types and bounded `ffmpeg` execution |
| Worker to vision provider | Selected image frames, fixed prompt and provider key | Separate client, explicit provider and model, explicit remote opt-in, no redirects, bounded response and no controller credentials |
| Search to embedding provider | Description or query text and optional embedding key | Separate client, explicit remote and insecure-HTTP opt-ins, no redirects, bounded response and no controller credentials |
| Processor to search database | Embeddings and device-managed database credential | Separate credential file, TLS configuration and restricted PostgreSQL peers |
| Browser to admin service | Configuration changes and secret replacement | Separate authenticated loopback HTTP or direct private/VPN HTTPS, exact transport/Host/Origin, CSRF, revision checks, bounded mutation admission and write-only keys; native HTTPS observations are linked below, browser E2E remains #17 |
| Release/update source to deployment | Images, packages and migration code | `needs_evidence`; signed artifacts, provenance and rollback belong to issues #4 and #16 |

Camera media, transcripts, face templates, plates, provider keys, controller credentials and private diagnostics are sensitive. Model responses, media files, protocol requests, URLs and headers are untrusted input.

## Implemented controls

- Device mode refuses plain HTTP management. Lab HTTP binds only to loopback.
- Controller clients require certificate validation. Disabling hostname validation requires an independently verified SHA-256 certificate pin.
- Controller requests use their own client certificate and headers. Vision and embedding clients do not receive them.
- Provider keys and embedding bearer tokens come from files. Inline provider keys and URL credentials are rejected.
- Vision and embedding endpoints require explicit permission for non-loopback access. Plain HTTP outside loopback needs an additional explicit opt-in.
- Media, model and embedding clients refuse redirects. Controller media URLs must match an exact configured origin.
- HTTP bodies, protocol messages, image counts, video duration, decoded output and queues have fixed limits.
- `ffmpeg` receives fixed arguments, no standard input and a `file,pipe` protocol allowlist. The service never constructs a shell command from media input.
- Health and diagnostics return fixed counters and categories. They do not serialize configuration, request bodies or exceptions.
- Repository device images declare non-root UID/GID 10001. The separately qualified NAS deployment uses existing non-root UID/GID 1000:10, a read-only root filesystem, dropped capabilities and `no-new-privileges`; deployment ownership is not inferred from the Dockerfile default.

## Administration requirements

Issue #13 must keep the administration listener separate from both emulated device profiles. LAN administration requires HTTPS, an administrator created during first-run setup and no default password. State-changing requests require an authenticated session, a same-origin check and a CSRF token. Login and mutation endpoints need bounded request bodies and rate limits.

`AdminSecurity` implements password hashing, signed expiring sessions, exact-origin enforcement, CSRF checks, bounded login-rate state and allowlisted decisions. The implemented [control site](control-site.md) persists a random signing key and password record. Direct HTTPS uses `__Host-aikey_admin` with `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/` and no `Domain`; local HTTP has a distinct cookie and must not be reverse-proxy-published. Sessions require the current runtime registry, so restart does not revive logged-out cookies. The authenticated [first-run wizard](first-run.md#authenticated-browser-preparation) inherits the same authorization gate and creates local files without starting processors or contacting a controller/model. Broader identity/maintenance UI and real browser/mobile acceptance remain #13/#17/#25, not unimplemented authentication adapters.

After successful Host/origin/session/CSRF checks, the shared control-site gate admits at most **60 mutating requests in a rolling 60 monotonic seconds per runtime**, across all sessions. Re-login or forged forwarding headers cannot reset it. Invalid authorization does not consume an authenticated quota. Rejected forms count once admitted; their file/key/provider work is still behind the gate. The first-run wizard inherits it. State is one bounded deque, not an unbounded per-address map. HTTP 429 includes `Retry-After`, no-store and the normal page security headers, with no request/secret echo or automatic retry. Reads and a fully authorized sign-out remain available. Denial audit entries are coalesced during a rejected burst and contain only a fixed action/result/time. This in-process operation throttle resets with a new runtime; it is **not** the durable caption budget, a paid-call reservation, provider-billing protection or an Internet-scale denial-of-service guarantee.

`ConfigurationStore` exposes redacted snapshots and previews, uses content revisions for optimistic concurrency, validates before writing, archives immutable versions, replaces files atomically and supports rollback to an archived revision. It refuses identity and credential-location changes, direct generic secret replacement and changes to a pinned embedding/index identity. The HTTP provider forms implement separate write-only replacement: a destination change cannot inherit its old role key, and an uncertain save retains any key potentially referenced by live/history state rather than deleting it or retrying. Pinned CLIP and E5 identities are compared correctly for ordinary settings/undo without encoder contact or a profile rewrite.

HTML pages return no saved secret values or script, use a script-blocking CSP and no-store, and do not write localStorage or console state. Synthetic HTTP canaries check responses, redirect locations, saved forms and audit output on success/refusal/uncertain outcomes. Audit files record fixed time/action/result and optional profile labels, **not** request bodies, configuration values, addresses, tokens or camera identifiers. Configuration revisions live in the versioned configuration history/journals, not a claimed extra audit field.

Backup, restore and update actions require separate authorization and an explicit preview. Restores must validate identity, schema and integrity before changing active state. An update must retain the last working artifact and configuration until the new version passes its startup checks.

## Egress and untrusted input

An operator must see the destination and data class before enabling a remote provider. Provider selection never falls back silently. The service must fail closed if a required credential file disappears or becomes unreadable.

The asynchronous Key model, embedding, CLIP, rerank and model-catalog clients,
and the synchronous AI Port model/score transports, check every DNS answer at
the connection boundary. Except for `localhost`
(which must resolve only to loopback), a hostname may resolve only to ordinary
global unicast addresses. Private backends use explicit LAN/VPN/loopback IPs;
special, link-local, multicast, reserved and supported translation/transition
prefixes are refused even when written as literals. IPv4-mapped IPv6 is
classified by its IPv4 address. Mixed public/private answers fail as a whole.
The checked address records are passed directly to aiohttp's transport, not
resolved again. Verified TLS still uses the original provider hostname.
Unicode hostnames must be supplied as ASCII IDNA A-labels; compatibility
Unicode cannot normalize into an approved loopback/private destination.

Face enrollment also uses the guarded, proxy-free, HTTP-only transport with
redirects disabled; its separately validated endpoint remains an explicit
private/loopback IP. Enrollment responses are bounded and validated before
identity storage. Failed requests expose fixed errors rather than provider
bodies or URLs. This does not encrypt identity storage or change retention.

This source boundary does not prove live DNS behavior or attest routing/billing
behind an approved endpoint. Synchronous native DNS now runs in a bounded, isolated stdlib child; deadline
expiry/cancellation kills and collects it without a resolver thread. Synchronous
connections share a monotonic request budget across
connection/TLS, sends, header parsing and body reads, including slow individual
bytes and already buffered late results. A late DNS/connect completion cannot
begin an expired HTTP transfer. The separate AI Port DNS admission probe is also
bounded; asynchronous resolution, arbitrary CPU work and universal kernel
preemption are not claimed. Sustained/live-provider qualification remains in #24 and the applicable feature acceptance; these bounded controls are not that qualification. See the
[async checks](evidence/provider-dns-boundary-20261029.md) and
[sync/enrollment limits](evidence/provider-sync-boundary-20261029.md) and the
[subsequent transfer-deadline checks](evidence/provider-response-deadline-20261029.md)
and [DNS-child deadline checks](evidence/provider-dns-deadline-20261029.md).

Model text is data. It never becomes a command, URL, path, query or configuration update. Camera media and model output cannot expand the configured network allowlist. Current speech/recognition/optional enhancement adapters and any future adapter inherit the same rule.

## Secrets and recovery

Key management/vision/speech and E5 embedding token loading requires a private
regular non-symlink leaf, bounded to 4,096 UTF-8 bytes with nonblocking type
validation. Invalid inputs fail before provider contact; no automatic repair
or rotation is performed. See the [loading checks and deployment
limits](evidence/bounded-secret-loading-20261029.md). Other file consumers retain
their separately documented rules; this is not a universal filesystem audit.

Use different credentials for controller management, database access, vision providers, embeddings and administration. Rotation of one credential must not replace another. Keep device identity files stable through normal updates. If identity or adoption credentials are lost, stop and use a documented recovery procedure instead of silently creating a new identity.

Diagnostic bundles use a field allowlist. Do not export raw configuration, environment variables, exception strings, request bodies, headers, URLs, frames or database contents. Tests use canary values to prove that controller and provider credentials do not cross clients.

## Required release checks

The security workflow runs the full test suite, static checks, a package build, a locked-runtime dependency audit, a validated CycloneDX SBOM and a full-history secret scan. Third-party actions use immutable commit SHAs with read-only repository permissions. Malformed media, oversized responses, redirect attempts, credentialed URLs and cross-client credential leakage require negative tests.

Private security reporting is defined in SECURITY.md (#11). Model-file hashes and bounded loaders have source/fixture checks; they do not clear distribution rights or quality. Signed public update/release verification remains #16, redistribution/origin clearance #4, and complete platform/restore/endurance qualification #24. None is claimed complete by this security foundation.

## Face-enhancement route boundaries (#3 review of #23, 27 Sep 2026)

The opt-in `enhanceImage` route (off unless `face_enhancement.server` is set) was reviewed with adversarial, synthetic-media tests in `tests/test_face_enhancement.py`.

| Boundary | Control | Evidence |
|---|---|---|
| SSRF (crop fetch) | The crop URL must be on a configured controller origin, and its path must be `/internal/aiprocessors/image/<imageId>`. Other hosts and protocol-relative URLs are refused. | `test_a_crop_on_another_host_is_refused` |
| SSRF (enhancer) | The enhancer must be a loopback or private `http://` address with no path. | `test_the_enhancer_must_be_local` |
| Redirects | Neither the crop fetch nor the enhancer call follows redirects; any non-200 (a 204 declines) fails the task without an upload. | `test_redirects_are_never_followed` |
| Credential forwarding | **Fixed:** the enhancer call used the controller session (device TLS identity and controller pin). It now uses the separate model-server session, like the face server. | `test_the_enhancer_is_called_without_the_controller_session` |
| Oversized or deceptive output | **Fixed:** declared dimensions are checked from the header before any pixel is decoded (JPEG only, no smaller than the source, at most 2048 px, PIL's bomb guard counts as a rejection). The output is **re-encoded**, so EXIF and bytes appended after the end marker never reach Protect. The response size is bounded by `max_media_bytes`. | `test_a_huge_declared_size_is_refused_before_decoding`, `test_metadata_and_appended_bytes_never_reach_protect`, and the decline cases |
| Cross-task substitution | **Fixed:** the crop URL's query (`type`, `camera`, `smartDetectObject`) must equal the task body, so one object's derivative can never be stored under another. The callback fields always come from the validated task, never from the enhancer. One job identity per object; a changed input for the same object is refused. | `test_a_crop_url_for_another_object_is_refused` |
| Originals | Protect stores the upload in its separate `enhancedImages` table; the source crop is never modified. | `test_an_enhanced_face_is_uploaded_as_a_separate_derivative` |

Native enhancement acceptance remains `needs_evidence` (#23): enhancement is not configured and has not been deployed.

## Direct administration TLS — 29 October 2026

The [control site](control-site.md#explicit-direct-https) has an opt-in direct TLS
listener with one explicit private/VPN address, existing private certificate/key,
TLS 1.2 minimum and no plaintext fallback. Secure host-only cookies and actual
transport/Host/Origin checks complement existing session/CSRF and login-rate
policy. Proxy headers are ignored; the loopback HTTP listener must not be
published through a reverse proxy. This source/fixture boundary does not claim
an installed LAN endpoint, certificate-renewal service, completed security audit
or signed release.


## Issue #3 acceptance evidence map — 30 October 2026

This maps the original foundation criteria, not a universal security audit or
approval of every future feature. The complete exact-head CI suite contains the
linked checks; missing model weights remain an explicit skip rather than a pass.

| Original criterion | Reviewable controls and evidence |
| --- | --- |
| Authentication/origin failures and no returned/stored secret values | [Authorization](../tests/test_admin_security.py), [HTTP/TLS](../tests/test_control_site_https.py), [session revocation](../tests/test_admin_session_lifecycle.py), [secret canaries](../tests/test_control_site_secrets.py), [write uncertainty](../tests/test_provider_key_save_recovery.py), [first-run](../tests/test_first_run_site.py), [shared operation admission](../tests/test_admin_mutation_limit.py), and [bounded audit fields/files](../tests/test_admin_audit.py) |
| Controller and provider credentials never cross their clients/routes | [Exact provider/client separation](../tests/test_providers.py), [controller/media boundaries](../tests/test_security.py), [guarded network/TLS peers](../tests/test_provider_network_tls.py), [speech role separation](../tests/test_speech_provider_control.py), [enhancement's separate model session](../tests/test_face_enhancement.py), and role-specific saved-key destination/undo tests |
| Hostile media/URL/path input and dependency/secret checks are release/CI gates | [Media/protocol refusal](../tests/test_security.py), [strict protocol](../tests/test_protocol.py), [private-file boundaries](../tests/test_secret_loading_boundaries.py), [DNS/HTTP deadlines](../tests/test_provider_response_deadline.py), [outer reply refusal](../tests/test_detector_transport_json.py), [workflow checks](../tests/test_security_workflow.py), and the [four-job pinned security workflow](../.github/workflows/security.yml) |

The authenticated private HTTPS admin is actually deployed, with strict
CA/hostname verification, separate persistent admin authority, secure cookies,
logout, retained processor identities and an image-bound certificate-maintenance
timer. [The CLIP guard qualification](https://github.com/Olli0103/unifi-ai-key-emulator/pull/196)
records the subsequent admin-only update, read-only real-profile checks and
limits. The mutation throttle has source/HTTP checks in the current change;
its production activation must be recorded separately before claiming it is
installed. The timer's first not-due check is not a new certificate issuance or
future unattended renewal proof.

Browser/mobile E2E (#17), independent installers (#25/#27), signed public
releases and rights (#4/#16), detection/retrieval quality (#7) and full
restore/endurance (#24) stay open on their own requirements. They must not be
silently declared complete, nor silently added as new prerequisites to this
foundation's original three criteria.
