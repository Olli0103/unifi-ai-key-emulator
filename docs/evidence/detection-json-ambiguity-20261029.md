# Reject ambiguous/nonfinite detection JSON — 29 October 2026

The public person pilot produced a reply with a duplicate `box` key. Python's
default JSON decoder silently keeps the last value. In an offline replay the
old fraction parser discarded its grid-scale box, but the existing fallback
normalizer erased the duplicate and accepted one normalized observation.
The saved public-input response digest is
`4d46917d083a628bcc1f45df71e72e0cd230e7ece53da294ea948972462e547a`.
No actual fallback model request or native event was made in this replay.

A shared decoder now rejects duplicate keys (including escaped spellings),
NaN/Infinity constants and numbers that overflow the supported finite floating
range **before** field selection or grid clamping. Both primary detection
parsing and fallback normalization use it; diagnostic classification uses the
same interpretation. Failures expose the existing fixed
`invalid_api_detection_response` code, not provider content or key names.
The diagnostic reason remains the existing allowlisted `not_json` category.

Unique finite JSON retains the same vocabulary, thresholds, coordinate
normalization and malformed-item/valid-sibling behavior. The fixed prompt,
request budget, provider routes, event policy and cost rules are unchanged.
This is input ambiguity handling, not a new detector or a quality improvement
claim. No runtime dependency was introduced.

## Evidence

- The initial baseline failed eleven regression cases: eight duplicate-key
  primary/fallback cases and three nonfinite fallback-coordinate cases.
  Five controls already passed.
- The expanded fixed tests cover exponent overflow, huge integer values,
  escaped duplicate labels, valid fraction/grid equivalence and continued
  retention of valid siblings next to uniquely encoded malformed items.
- All 157 targeted detection/fallback/tracking/camera-engine tests pass;
  Ruff, source audit and diff checks pass.
- Offline replay of the identical saved duplicate reply now refuses both
  primary and grid-normalizer paths. A network-denying audit hook was active;
  no model requests occurred. Frozen evaluation-file hashes stayed identical.
- Test fixtures are invented minimal JSON, not copied photos or model replies.

This is a source fix. The deployed Key/four-Port images remain the separately
verified source through #159 until an explicit later image transaction.
