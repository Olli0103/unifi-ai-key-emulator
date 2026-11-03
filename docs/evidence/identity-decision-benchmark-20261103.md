# Offline identity-decision metrics (3 November 2026)

Optional extension of the existing [multimodal scorer](../benchmark.md) toward
the known/unknown/false-match evidence in #7/#20. This is arithmetic on supplied
opaque decisions, not a face detector/recognizer, enrollment, grouping, ReID,
model migration, privacy erasure or a native quality test.

## Implemented boundary

The existing `multi-score`/`multi-compare` CLI accepts optional `identity` cases
with exact opaque `expected` and `predicted` IDs or JSON `null`. Ground-truth
null means unknown; a null prediction means rejected. Strict nonempty ASCII
tokens use the existing 1–64-character identifier rule. Empty/Boolean/list/
mapping/whitespace/overlong labels and unspecified image/vector/confidence/
threshold fields are refused. The literal string `unknown` remains an identity
token, not an implicit alias. Both known and unknown truth trials are required
globally; neither missing/unknown input nor an unavailable denominator is zero.

Known correct, non-null wrong and rejected identities are counted separately
from unknown false accepts and correct rejections. Denominator-specific rates,
identified precision and overall accuracy are emitted for overall, camera-class
and condition slices. A class-empty slice or no non-null predictions yields
`null` for its undefined rate/precision; comparison preserves that uncertainty.
Counts accompany rates so class imbalance is not hidden by overall accuracy.
These are open-set identification decisions, not pairwise-verification FAR/FRR,
detection recall, threshold selection or confidence calibration.

Source/case identity, expected opaque labels and exact producer-declared input
digests bind the corpus; predictions do not, allowing like-for-like comparisons.
The scorer does not open/authenticate the corresponding media, establish
ground truth, verify consent, run a model or identify a person. Reports omit raw
expected/predicted/case IDs, images and vectors. Their corpus digest is not
anonymization or independent source authentication; run/slice metadata remain
operator-supplied and must be nonsensitive for publication.

## Source and fictional checks

Seven new positive/metric tests fail on the earlier source because the new kind
is intentionally unsupported; eighteen initial negative checks already pass.
This is a missing-feature control, not a claim of seven old bugs.

The hand-calculated six-case fixture has four known trials (two correct, one
wrong, one rejected) and two unknown trials (one false accept, one rejection).
Known identification accuracy is 0.5, misidentification/rejection rates 0.25
each, unknown false-accept/rejection rates 0.5 each, identified precision 0.5
and overall accuracy 0.5. Every label/prediction and non-media source string in
the [example](../../examples/benchmark-identity.json) is invented. These values
are not measured recognition quality or a baseline for tuning.

Tests cover strict nullable labels/exact fields, hand arithmetic, missing
denominators, rejection-only decisions, slices, caller preservation/order
determinism, report/CLI label omission, invalid-label CLI refusal, exact source/
truth binding versus changed predictions, nullable comparison, coexistence with
existing operational metrics and the actual CLI. An identity-only calculation
refuses any attempt to import JiWER or model/inference libraries in its fixture.

Existing plate/speech/retrieval/operation metric functions, numeric helpers and
comparison implementation are AST-exact. The full canonical JSON report from
the old four-kind fictional example remains byte-identical. The new kind adds
no dependency, model/encoder, runtime endpoint, index write or configuration/
permission change. Optional version-1 support is additive; old releases refuse
unknown identity cases, not silently reinterpret them.

One source-gate helper initially used system Python without the optional JiWER
needed to recompute the **old plate/speech** example. It failed without writing
a successful gate. The already installed project development interpreter
passed the comparison; no package install or product guard change. Identity-
only scoring never required JiWER. This operator correction is retained, not
misreported as a scorer defect or native failure.

Exact local/CI counts are recorded at the qualified head in the PR. Source and
fixture review here are same-agent, not independent review. No real face, name,
template, footage, model request, provider charge, native service or index was
read/changed. Real representative consented measurements, numeric targets set
before tuning, native persistence/restart/false-match/migration/deletion quality
and independent hardware comparisons remain separate; broader issues stay OPEN.
