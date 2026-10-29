# Deep input validation — 29 October 2026

Source/fixture progress for #3, #8 and #22. No native quality or full Deep
acceptance is claimed; those issues remain open.

The controller's prompt message contains another JSON document encoded as
base64. The outer protocol's strict JSON parser cannot validate that inner
document. Previously a non-object, duplicate-key, NaN or overflowed-number
schema could replace the saved prompt file and its advertised hash. The inner
schema now uses the existing strict protocol object parser before either
changes. Prompt selection checks it again. This is strict JSON-object
validation, not complete JSON Schema meta-validation or a capability test.

Sampling fields must be finite representable numbers. The outer wire already
refused non-finite numbers; direct/local prompt validation now agrees rather
than relying on that caller. The label-bounding helper does not crash when a
schema has an unrelated, non-map `properties` value.

Deep video boxes now obey the same finite 0–1000 xywh bounds as the existing
region contract: x/y are below 1000 and width/height are positive and at most
1000. Bad boxes fail before worker startup, durable admission or media fetch.
No new coordinate interpretation or camera permission is introduced.

Re-ID responses are checked before normalization, and normalization scales
components before computing the norm. Previously very large finite values
could silently become an all-zero callback vector; tiny subnormal values
could fail through underflow. Both now preserve direction and the existing
512-value padded format. Invalid scalars and zero vectors produce fixed
per-object failures instead of vectors or an uncaught numeric overflow. This
does not attest the model identity or semantic quality from vector width.

## Checks

- On the unchanged baseline, 22 of 28 selected boundary/transport regressions
  fail; six existing rejection cases pass. Synthetic inputs only.
- Tests cover wire-level rejected prompt replacement with byte-identical
  saved prompts/state/hash, admission ordering, finite scalar handling and
  actual loopback Re-ID response/callback transport.
- Full local suite: **2,284 passed, three skipped**, 22 existing warnings and
  56 passed subtests. The skipped PostgreSQL integrations remain unproved.
- Static checks and diff whitespace checks pass. No dependency was added.

No deployment, provider change, camera enrollment, index migration or native
media test is part of this source fix. The separately documented current
[native observations](live-protect-7.3.74.md) retain their limits.
