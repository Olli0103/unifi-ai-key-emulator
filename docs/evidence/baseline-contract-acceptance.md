# Baseline contract acceptance (#2) — 29 October 2026

This closes the inventory/contract task, not all feature implementation or
compatibility qualification. The versioned manifest deliberately contains
`unsupported`, `fixture-tested` and `needs_evidence` entries. Full parity,
release and operational acceptance remain under their separate open issues.

| Issue #2 acceptance | Reviewable evidence |
| --- | --- |
| Sanitized accepted/rejected commands with real framing/TLS | `tests/fixtures/compatibility/ucp4-control-v1.json` and `tests/test_compatibility_fixtures.py`: independent loopback TLS controller, token then certificate authorization, byte framing, accepted/unsupported commands, malformed frames and reconnect. Fixtures are invented, not private captures. |
| Preserve the previously verified baseline | Dated live sources separate 7.3.56 adoption/password rotation/disabled post-adoption factory enrollment, 7.3.60 G5/G4 native captions after page reload, and 7.3.68 tokenless reconnect/persisted captions. Current 7.3.74 inventory-overlay restart preserves the adopted identity, config and key/certificate bytes. None of those dates is silently generalized to another feature or version. |
| Unknown versions/features fail clearly without destroying adoption | Fixed version categories, unsupported target/error codes, malformed-frame close then tokenless baseline reconnect. A newly known `live_partial` version still cannot admit continuous captions without the separate caption allowlist; the 7.3.74 case explicitly exercises rejected captions alongside successful baseline `getInfo`. |

Manifest `ai-key/2026-10-29.1`, its generated matrix, runtime version
classification and fixture metadata share one version. The focused manifest,
framing, caption-scope and control-profile checks passed (53 tests). Tests prove
the local contract; the separately dated native records prove only their stated
observations. They do not convert an HTTP 200 or a capability flag into native
persistence.

Review found that 7.3.70's source summary still said descriptions/deep mode were
unobserved, while later dated sections and feature entries already recorded
native audio-image descriptions and deep behavior. The summary now distinguishes
initial callback-only video observations from later readbacks. The existing
caption allowlist was **not removed or expanded** based on that stale summary.

Still missing: fresh adoption on every later version, new password changes with
search enabled, controller-upgrade/reboot recovery, video-caption acceptance
where specifically marked missing, benchmark/quality gates, complete basic/deep
native acceptance on 7.3.74, ONVIF hardware, and complete restored-device/soak
qualification. These remain visible in per-feature missing-evidence entries and
the open feature/security/operations issues; closing #2 is not permission to
publish a parity release.
