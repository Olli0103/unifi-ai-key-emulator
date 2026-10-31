# Detection-quality benchmark (issue #7)

`python -m aikey.benchmark score <cases.json>` scores labelled predictions. It never reads media and never calls a provider. The owner produces predictions locally from consented or synthetic material, and only the labelled records are scored. Private footage stays out of the repository.

The [29 October public person adapter pilot](evidence/public-person-pilot-20261029.md)
records twelve real local predictions: seven usable/correct and five errors. It
is separate from owner-camera quality and the deployed fallback route.

## Input-bound comparison (`aikey-benchmark-cases/2`)

Use schema `aikey-benchmark-cases/2` for new detection evaluations. It has the
same fields and metrics as v1 below, plus **exactly one `input_sha256` per
case**: the lowercase SHA-256 of the exact input used for that prediction.
Hash the same evaluation unit (for example the exact encoded frame or a
canonical multi-frame input manifest) across compared runs. The producer must
retain the local source/manifest and annotation review. The scorer reads no
media and cannot attest that supplied hashes or labels are truthful.

The v2 corpus digest binds input hashes, case identity/class, sorted conditions
and expected labels in a separate namespace. Predictions, confidence and run
model/provider do not change corpus identity. Reusing labels and case names
with different inputs does not produce matching evidence. Aggregate reports
omit individual input hashes, case IDs and per-case labels.

`score` emits `aikey-benchmark-report/2` with
`corpus_binding=producer_declared_exact_input_sha256`. `compare` requires two
valid v2 reports, equal input-bound digests and consistent labelled membership.
It refuses invalid hashes, nonfinite/boolean/out-of-range or contradictory
counts/ratios/calibration and damaged slices, instead of producing a misleading
comparison or traceback. Undefined calibration/ratios remain null, not zero.
A comparison is not a native result, independent corpus verification or a
hardware-equivalence verdict.

**Compatibility:** v1 case files still score with their original numerical
metrics and label-only digest. Their v1 reports are explicitly unbound and are
no longer accepted by `compare`, even when their digests match. Keep historical
reports; do not relabel them as v2 or hash case names as a substitute for actual
inputs. The trusted producer must add true source hashes and rescore. Existing
multimodal case/report schemas already bind exact input hashes and are not
migrated; the common CLI now also refuses duplicate-key/nonfinite JSON records.

## Legacy case file (`aikey-benchmark-cases/1`)

```json
{"schema": "aikey-benchmark-cases/1",
 "run": {"model": "…", "provider": "…", "profile": "ai-key", "code_revision": "…"},
 "cases": [{"id": "c01", "camera_class": "outdoor", "conditions": ["night", "rain"],
            "expected": "person", "predicted": "none", "confidence": 0}]}
```

- `expected` and `predicted` are one of `person`, `vehicle`, `animal`, `package`, `licensePlate`, `face` or `none`. A `none` prediction carries confidence 0.
- Negative cases (`expected: "none"`) are required. A corpus of successful demos is refused.
- Case ids are unique. Conditions are lowercase tags, for example `day`, `night`, `glare`, `rain`, `motion_blur`, `occlusion`, `pets`, `multi_object`.

## Report (`aikey-benchmark-report/1`)

- The run identity, plus `corpus_sha256`: a digest of case ids, camera classes, conditions and expected labels.
- For the whole corpus, per camera class and per condition:
  - per-label TP, FP, FN, precision and recall. An undefined ratio is `null`, never 0 or 1.
  - false alarms and their rate on negative cases;
  - missed and misclassified positives.
- Calibration over answered non-`none` predictions only: expected calibration error
  (10 bins) and the Brier score. Report valid-response/class coverage and errors
  separately; valid-only metrics must not be presented as all-scheduled accuracy.
- The output is deterministic, so the same records give the same report in any case order.

`python -m aikey.benchmark compare <report> <reference>` reports differences only for validated input-bound v2 reports on the same declared corpus, as described above. Matching old label-only digests are not sufficient. No scorer can establish truthful labels, physical source identity or a real hardware reference merely from an aggregate report.

## Exact model identifiers in run metadata

Detection (legacy and v2) and multimodal runs retain all previously accepted
flat1–64-character model tags. An additional1–200-character form begins with
a letter/digit followed by ASCII letters, digits, `.`, `_`, `:`, `+`, `/` or
`-`. This matches the existing catalog character vocabulary and permits a
provider-supplied namespace/tag spelling such as
`fictional-org/fictional-model:tag`, instead of requiring a flattened alias.
The spelling is preserved exactly; leading/trailing whitespace, control
characters and oversized values are refused, never trimmed or truncated.

Only `run.model` has this vocabulary. Provider/profile/code revision, case IDs
and camera classes retain their existing strict 1–64-character tag rules.
This is metadata validation, not model discovery, identity/availability or
weight verification. The scorer never uses the identifier as an endpoint,
filename or load command. Different run/model identities on matching declared
inputs can still be compared; changing model metadata does not change corpus
identity or numerical metrics.

## What still needs evaluation material

- Producing predictions from footage: an owner-local step. The multimodal scorer below closes the metric-computation gap, not this input gap.
- Numeric targets. Those are set from a first baseline on the owner's consented corpus, before any tuning.
- An independently measured real AI Key reference on the identical corpus before a hardware-equivalence claim.

## Multimodal measurements (`aikey-multimodal-cases/1`)

Install the optional scorer with `python -m pip install '.[benchmark]'` (JiWER
4.0.0, Apache-2.0). It is a development/evaluation dependency, not a model or a
runtime inference dependency. JiWER uses RapidFuzz for edit distance. The runner
has been exercised on Python 3.12 / macOS ARM64; Linux coverage is reported by CI.

```sh
python -m aikey.benchmark multi-score examples/benchmark-multimodal.json > report.json
python -m aikey.benchmark multi-compare report.json reference.json
```

The checked-in example is **fictional and synthetic only**. Its predictions are
invented to exercise scoring, not produced by a model. It cannot establish model
quality, native feature acceptance or a baseline for real-camera tuning.

A document has exactly `schema`, `run` and `cases`. `run` carries model, provider,
profile and code revision, as above. Every case carries a unique `id`,
`camera_class`, lowercase `conditions`, exact `input_sha256`, and `kind`:

| Kind | Additional fields | Metrics |
| --- | --- | --- |
| `plate` | `expected`, `predicted` strings; empty expected means no plate | Positive exact accuracy, character errors/CER, false reads on negative cases |
| `speech` | `expected`, `predicted` strings; empty expected means silence | Word errors/WER, character errors/CER, hallucinations on silence |
| `retrieval` | Unique `relevant` and ordered `ranked` ID lists, `k` (1–100) | Macro recall@k, MRR@k, binary nDCG@k, negative-query result rate |
| `operation` | `camera_days`, `false_alarms`, `critical_events`, `missed_critical`, nullable `latency_ms` and `provider_charge_usd` | False alarms/camera-day, critical misses, nearest-rank latency p50/p95/p99, known/unknown provider-charge accounting |

Recognition/retrieval kinds require both positive and negative cases. All kinds
are optional; an absent kind is not claimed as evaluated. Labels and predictions
normalize whitespace only: case, punctuation and uncertain plate characters are
preserved. `?` is not a correct character. Empty predictions remain misses; empty
references remain negative controls. Text errors are micro-aggregated (total edit
operations / total reference units), so silence insertions cannot disappear in an
average over only nonempty references. A zero denominator is `null`, not success.

Operational inputs are **supplied measurements**, not automatically collected
telemetry. Use nonoverlapping, labelled exposure windows; `camera_days` is their
summed exposure, not elapsed wall time guessed by the scorer. Supply measured
end-to-end latency samples, not a decoder timer. Unknown latency is counted and
excluded from percentiles. A recorded critical miss cannot exceed the labelled
critical-event count.

`provider_charge_usd` accepts only a provider-reported charge attributable to that
case/window. Missing charge data is `null`, never assumed zero because inference
was local or a request failed. `known_provider_charge_usd` sums only known charges;
`total_provider_charge_usd` is null whenever any charge is unknown. This schema
does **not** accept or calculate theoretical API costs, electricity costs or token
estimates; retain those separately with exact token classes, dated rates and their
own provenance instead of passing them off as provider charges.

Every metric is also sliced by camera class and capture condition. Reports omit
raw text, plates, item IDs and per-case records. Keep the labelled input local;
reports still reveal aggregate operational information and are not automatically
published. The comparison binds exact source digests, expected labels, conditions,
retrieval cutoff and observation exposure. Changing predictions alone permits a
comparison; changing the corpus refuses it. Matching synthetic records are not a
real AI Key reference evaluation.

Input hashes and labels are supplied by the corpus producer. The scorer never
opens the media and cannot attest that a supplied hash or annotation is truthful;
retain the local source manifest and independent annotation review for that.
