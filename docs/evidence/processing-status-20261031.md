# Read-only processing and budget observations — 31 October 2026

Part of #13 and #17. The broader issues remain OPEN.

## Implemented boundary

The separate Admin now has a compact processor overview, keyboard-expandable
details and an authenticated versioned JSON read. It reuses the existing pinned
HTTPS runtime observer. Key queue, inference waiters, caption-admission and
historical-note publication counters are distinct from Port streams, detector
attempts, provider failures and per-camera API-attempt allowances. It does not
confuse unknown/zero, missing/explicitly uncapped, or stored exception notes with
current failed jobs. It publishes no camera identifiers/correlation hashes,
model messages, provider endpoints, media or credentials.

There are no new mutation methods, host commands, provider/controller calls,
credentials, state-directory reads, dependencies, mounts or listener. A read
cannot spend a permit, replay a failed event, restart a model or change saved
configuration. The existing health cache and transport limits remain in force.

## Checks

The focused processing/runtime/configuration/binding group passes **86 tests**,
including owned real HTTPS peers, strict malformed counters/flags/scopes,
null versus zero, absent versus reported-null caps, rejection of URL/query
options and POST methods, no extra transport for cached views, independent
healthy/unavailable profiles, private payload canaries, unchanged configuration
bytes/files, and session revocation during an awaited observation. Existing
runtime tests cover bad pins, redirects, response bytes and slow peers. Ruff
passes. Full-suite, browser and integration results are recorded in the PR.

The fictional real-Chrome runner is `tools/processing_browser_check.py`. It
checks desktop/mobile, keyboard overview/detail access, horizontal overflow,
zero/unknown/uncapped presentation, stale-good-data refusal after a corrupt
peer, session expiry, no settings POSTs, no browser storage or foreign requests,
unchanged fixture configuration and collected owned profiles/servers. Its
screenshots contain fictional data only; automated layout assertions do not
replace visual inspection.

## Limits

Process counters reset; a successful attempt may contain no object. Failure-note
publication is neither a job-outcome aggregate nor reconstruction of a missing
legacy cause. The Key health source does not report active metered policy or
durable hourly remaining permits, and the page does not invent them. A reported
Port allowance is observational, not guaranteed future admission. Per-camera
ordinals are not stable identity bindings.

Native recognition/recording/quality, original-camera negative/outside-zone and
controlled physical tests, broader Admin editing and full issue acceptance are
not advanced by this source change. A source merge alone does not activate the
new page on an existing installation.
