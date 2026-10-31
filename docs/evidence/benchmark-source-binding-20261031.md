# Input-bound detection comparison — 31 October 2026

Bounded correctness increment for issue #7; its corpus, agreed targets and
independent reference/native-quality requirements remain OPEN.

## Reproduced boundary

The earlier basic detection comparator accepted two matching `null` corpus
hashes and returned numerical differences. Its v1 case/report format also
bound only case names, camera classes, conditions and expected labels: it could
not bind the exact input used to produce each prediction. Matching label-only
digests were insufficient evidence for the stronger comparison claim. The
multimodal format already requires input hashes; its schema is not migrated.

## Contract

- New detection case schema2 requires a producer-declared lowercase SHA-256 per
  exact input. The namespaced corpus identity also binds case/class/conditions
  and labels, but excludes predictions/confidence/run identity.
- Report2 omits individual hashes, case IDs and raw case records. Its binding
  explicitly means **producer-declared**, not independently verified media.
- Comparison requires validated report2 inputs, matching bound digests and
  consistent labelled membership. Invalid hashes, nonfinite/boolean/bounded
  scalar violations, inconsistent count/ratio/calibration/slice structures are
  refused with fixed errors before calculating differences. Unknown ratios or
  calibration are null, not zero or an invented improvement.
- Legacy case1 scoring retains its original numerical metrics and digest.
  Report1 is explicitly label-only and is no longer comparison-eligible. There
  is no automatic promotion, hashing of case names, default source digest or
  bypass flag. A trusted producer must retain its real source manifest, add
  true input hashes and rescore; historical reports need not be deleted.
- The common CLI uses the existing strict JSON-object reader, refusing duplicate
  keys/nonfinite records rather than silently selecting one interpretation.
  It does not add media discovery, inference, network, telemetry or a publisher.

The scorer cannot authenticate supplied hashes/labels or prove native quality,
model identity, a real AI Key reference or a previously agreed numeric target.
Those requirements cannot be replaced by passing synthetic records.

## Checks and limits

Focused basic/new/multimodal/command-documentation group:404passed. Cases cover
changed exact input vs changed prediction/run/order; unchanged legacy numerical
metrics; legacy/mixed comparison refusal; malformed/missing digests; corruption
on either report side; contradictory membership/counts/calibration; all-none
predictions retaining unknown calibration; thirty seeded writer/consumer
combinations and CLI duplicate-key/private-canary refusal. The first new run
had77passes/one assertion failure: a broad string search mistook the public
binding label for an exported per-case hash field. The corrected assertion
checks the actual field and every individual hash remains absent; no product
validation or privacy requirement was weakened.

Frozen full-suite/CI and source-derived outside checks are reported separately
on the PR; this note does not preclaim them. No live installation, actual
camera/cost setting, model/provider request, Key image, fence, timer, journal,
index or controller changed. No runtime rollout is necessary for this offline
scoring-tool increment. It is not independent human review.
