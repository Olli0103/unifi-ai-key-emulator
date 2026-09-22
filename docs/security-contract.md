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

The administration API returns secret references and replacement status, never saved values. The browser must not store secrets in local storage, history or diagnostic state. Configuration writes use revision checks, validation, atomic replacement and rollback. Audit events record the actor, action, result and configuration revision without request bodies, secret values or camera identifiers.

Backup, restore and update actions require separate authorization and an explicit preview. Restores must validate identity, schema and integrity before changing active state. An update must retain the last working artifact and configuration until the new version passes its startup checks.

## Egress and untrusted input

An operator must see the destination and data class before enabling a remote provider. Provider selection never falls back silently. The service must fail closed if a required credential file disappears or becomes unreadable.

DNS rebinding protection for user-configured hostnames is `needs_evidence`. Before the administration site accepts arbitrary provider hosts, issue #13 must define whether it pins resolved addresses, restricts address ranges or requires explicit approved destinations. The chosen rule must cover IPv4 and IPv6, redirects and address changes between validation and connection.

Model text is data. It never becomes a command, URL, path, query or configuration update. Camera media and model output cannot expand the configured network allowlist. Future OCR, speech and recognition adapters inherit the same rule.

## Secrets and recovery

Use different credentials for controller management, database access, vision providers, embeddings and administration. Rotation of one credential must not replace another. Keep device identity files stable through normal updates. If identity or adoption credentials are lost, stop and use a documented recovery procedure instead of silently creating a new identity.

Diagnostic bundles use a field allowlist. Do not export raw configuration, environment variables, exception strings, request bodies, headers, URLs, frames or database contents. Tests use canary values to prove that controller and provider credentials do not cross clients.

## Required release checks

The security gate for an experimental release includes the full test suite, dependency and secret scanning, an SBOM, container configuration checks and review of all outbound destinations. Malformed media, oversized responses, redirect attempts, credentialed URLs and cross-client credential leakage require negative tests.

Private security reporting, dependency-scanner selection, signed update verification, model file hashing and restore testing remain `needs_evidence`. Track them in issues #11, #16, #24 and the relevant provider/model work rather than treating this document as completion evidence.
