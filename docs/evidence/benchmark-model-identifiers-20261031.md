# Namespaced benchmark model metadata — 31 October 2026

Bounded compatibility correction for issue #7; broader quality acceptance
remains OPEN.

The actual previous basic and multimodal scorers rejected the fictional model
ID `fictional-org/fictional-vision-v1`, while the existing catalog grammar
accepted it. The shared 64-character case/tag validator had incorrectly also
been used for model metadata, preventing preserved namespace/longer spellings.
No real model/provider request was needed to reproduce this mismatch.

A shared **run-field-specific** validator now preserves the exact model ID in
both scorers and both report comparators, using the existing catalog character
vocabulary with its200-character bound, while retaining all previously valid
flat64-character model tags (including initial `_`, `-`, `.` or `:`). It does not normalize, flatten aliases,
trim, truncate, resolve, read or load the identifier. Other run fields, case IDs
and camera classes retain their strict64-character tag contract. V1/v2 schemas,
corpus identities, numerical metrics and input-bound comparison rules do not
change. There is no new dependency, network route, discovery or publisher.

Final combined basic/input-binding/multimodal/model-identity/documentation group:434passed. Checks
cover namespace/tag/multi-segment/plus spellings and exact200-character boundary,
legacy scoring, unchanged corpus digest across model identity changes, both
report sides, null/type/length/whitespace/control/Unicode refusals, unchanged
non-model identifiers and CLI round-trips/private-canary refusal. All input
records are fictional; no camera media, weights or real inference are involved.
Frozen full-suite/CI and source-derived outside checks are reported separately,
not preclaimed here. Model metadata is supplied by the producer and is not
independent canonical-route, model/weight, native-quality or reference proof.

This offline tool change does not require any production deployment, restart,
policy/permission/budget/key/image/index/fence/timer/journal/controller mutation.
Representative consented labels, pre-tuning targets and independent reference
measurements remain required. No release or independent human review is claimed.
