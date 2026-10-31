# Redacted diagnostic export — 31 October 2026

A bounded part of issues #13 and #17; their wider scope remains OPEN.

The new authenticated download is a second strict projection of the existing
pinned runtime observer, not a serialization of private admin/configuration
responses. It has a separate versioned schema, static attachment filename,
per-file profile ordinals and aggregate scalar counters only. Registered handles
are distinct from setup/adoption proof. No caller-supplied path/URL, config, model,
camera record, identifier, revision, credential, host log, raw error or media is
included. No inventory/controller/provider call, model startup, saved policy or
core-service change occurs in collection.

The single collector refuses concurrent exports; at most 32 known profile kinds
are admitted. Observation awaits have a six-second asyncio deadline. Reader tasks
remain owned through cancellation and drain, including the ordinary gather-error
case where surviving siblings would otherwise remain running. Physical threads
blocked below asyncio are not claimed abortable. Authentication is rechecked after
awaited work, and errors are fixed non-attachment JSON. Null/false/zero and
unconfigured/unavailable/verified observation states are kept distinct. The file
is bounded to 64 KiB; private API errors and raw source strings never become file
contents.

Twenty-five new cases and 112 focused checks passed during candidate verification:
real own TLS peers and no credential-forwarding, private-field canaries, malformed
counters/flags/kinds, profile-count bound, query/method/no-collection refusal,
logout during collection, busy/deadline/retry, direct cancellation and failed-
reader sibling cleanup. Actual synthetic Chrome desktop/mobile/keyboard fixtures
have downloaded and byte-validated the real attachment, without application-state
writes or foreign requests. Frozen-source full suites/CI, wheel/native Admin
activation are pending. Final decoded desktop/mobile disclosure pages were
visually inspected, and the qualified download fixtures match all 125 source
module hashes. This is same-agent evidence,
not independent review or native compatibility/quality acceptance.
