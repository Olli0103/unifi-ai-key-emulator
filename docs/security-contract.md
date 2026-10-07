# Security contract

This contract defines the security boundaries for the current processor and the planned administration site. It separates implemented controls from design requirements. A checkbox or configuration field does not prove that a future feature is safe.

## Trust boundaries

| Boundary | Data crossing it | Current rule |
| --- | --- | --- |
| Protect controller to device service | Adoption credentials, commands and task metadata | Verified TLS, optional leaf pin, bounded protocol messages and fixed command handling |
| Controller media service to worker | Images, exported video and response headers | Exact controller-origin allowlist, no redirects, size limits, fixed media types and bounded `ffmpeg` execution |
| Worker to vision provider | Selected image frames, fixed prompt and provider key | Separate client, explicit provider and model, explicit remote opt-in, no redirects, bounded response and no controller credentials |
| Search to embedding provider | Description or query text and optional embedding key | Separate client, explicit remote and insecure-HTTP opt-ins, no redirects, bounded response and no controller credentials |
| Processor to search database | Embeddings and device-managed database credential | Separate credential file, TLS configuration and restricted PostgreSQL peers |
| Browser to planned admin service | Configuration changes and secret replacement | `needs_evidence`; issue #13 must implement the requirements below before LAN exposure |
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
- The production container runs as UID/GID 10001, uses a read-only root filesystem, drops all capabilities and sets `no-new-privileges`.

## Administration requirements

Issue #13 must keep the administration listener separate from both emulated device profiles. LAN administration requires HTTPS, an administrator created during first-run setup and no default password. State-changing requests require an authenticated session, a same-origin check and a CSRF token. Login and mutation endpoints need bounded request bodies and rate limits.

The framework-independent `AdminSecurity` module implements password hashing, signed expiring sessions, exact-origin enforcement, CSRF checks, bounded login-rate state and allowlisted audit decisions. The future HTTP adapter must create and persist its random signing key, store the password record, and set the `__Host-aikey_admin` cookie with `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/` and no `Domain`. HTTP routing and first-run enrollment remain `needs_evidence` until issue #13 integrates and tests them.

The framework-independent `ConfigurationStore` exposes redacted snapshots and previews, uses content revisions for optimistic concurrency, validates before writing, archives immutable versions, replaces files atomically and supports rollback to an archived revision. It refuses identity and credential-location changes, direct secret replacement and changes to an active embedding profile. The future HTTP adapter still needs separate write-only secret operations and must never put secrets in local storage, history or diagnostic state. Audit events record the actor, action, result and configuration revision without request bodies, secret values or camera identifiers.

Backup, restore and update actions require separate authorization and an explicit preview. Restores must validate identity, schema and integrity before changing active state. An update must retain the last working artifact and configuration until the new version passes its startup checks.

## Egress and untrusted input

An operator must see the destination and data class before enabling a remote provider. Provider selection never falls back silently. The service must fail closed if a required credential file disappears or becomes unreadable.

DNS rebinding protection for user-configured hostnames is `needs_evidence`. Before the administration site accepts arbitrary provider hosts, issue #13 must define whether it pins resolved addresses, restricts address ranges or requires explicit approved destinations. The chosen rule must cover IPv4 and IPv6, redirects and address changes between validation and connection.

Model text is data. It never becomes a command, URL, path, query or configuration update. Camera media and model output cannot expand the configured network allowlist. Future OCR, speech and recognition adapters inherit the same rule.

## Secrets and recovery

Use different credentials for controller management, database access, vision providers, embeddings and administration. Rotation of one credential must not replace another. Keep device identity files stable through normal updates. If identity or adoption credentials are lost, stop and use a documented recovery procedure instead of silently creating a new identity.

Diagnostic bundles use a field allowlist. Do not export raw configuration, environment variables, exception strings, request bodies, headers, URLs, frames or database contents. Tests use canary values to prove that controller and provider credentials do not cross clients.

## Required release checks

The security workflow runs the full test suite, static checks, a package build, a locked-runtime dependency audit, a validated CycloneDX SBOM and a full-history secret scan. Third-party actions use immutable commit SHAs with read-only repository permissions. Malformed media, oversized responses, redirect attempts, credentialed URLs and cross-client credential leakage require negative tests.

Private security reporting, signed update verification, model file hashing and restore testing remain `needs_evidence`. Track them in issues #11, #16, #24 and the relevant provider/model work rather than treating this document as completion evidence.

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
