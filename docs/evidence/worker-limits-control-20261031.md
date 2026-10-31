# Saved processing limits — 31 October 2026

Bounded increment for issues #13 and #17; their broader scope remains OPEN.

The separate administration UI manages six already implemented AI Key worker
options through typed store operations and preview/confirmation, using the
existing authentication, same-origin/CSRF, revision/file lock, private history,
immutable identity, credential route, search guard, shared admission window
and cancellation-drained writer. No worker, provider, model, permission,
callback, deadline implementation or native service is changed by this source
increment. Saved changes require a separate operator-controlled restart.

The editor cannot remove the vision inference gate. Existing out-of-editor
values are unavailable, never silently clamped. Other raw fields and omitted
unchanged defaults are preserved. Grants are session/revision/result-bound,
single-use, monotonic 600-second expiry and bounded to32. Separate initially
unchecked load/cost and save acknowledgements are required. Committed saves
with failed acknowledgements report uncertainty without retry or reversal.

The focused HTTP/store regression group passed128 checks. New checks include
six-field-only persistence, exact private history, no-op byte preservation,
invalid types/ranges/unknown fields, stale revisions, no gate removal,
confirmation shape/expiry/logout/session crossing, concurrent single-use,
post-commit uncertainty, cancellation/drain and the real shared mutation
limit. A disposable actual worker constructor verifies the offered defaults
and selected values, without starting its services or making a request.
Initial targeted invocation named a nonexistent test file and collected no
tests; the corrected existing test group produced the128-pass result.

`tools/worker_limits_browser_check.py` provides disposable Chrome desktop,
mobile and keyboard preview/required-consent/save checks. It changes only a
fictional queue value, blocks non-fixture browser traffic, compares all other
configuration and records module hashes/captures. Its successful execution,
visual review, frozen full-suite/CI results and outside source-derived checks
must be reported separately; the runner does not self-certify visual review.

No real household setting has been applied, processor restarted, model call
made or native performance/timeout cure claimed. The separate existing Key
upgrade admission refused before any stop due to absence of natural idle;
that terminal attempt is not resumed by this UI feature.
