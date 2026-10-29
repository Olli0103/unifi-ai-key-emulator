# Detection-quality benchmark (issue #7)

`python -m aikey.benchmark score <cases.json>` scores labelled predictions. It never reads media and never calls a provider. The owner produces predictions locally from consented or synthetic material, and only the labelled records are scored. Private footage stays out of the repository.

## Case file (`aikey-benchmark-cases/1`)

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
- Calibration over answered cases: expected calibration error (10 bins) and the Brier score.
- The output is deterministic, so the same records give the same report in any case order.

`python -m aikey.benchmark compare <report> <reference>` reports differences only when both reports scored the same corpus (equal `corpus_sha256`). Otherwise it refuses. This is how "no comparative claim without comparative evidence" is enforced.

## Not covered yet

- Producing predictions from footage: an owner-local step.
- Plate accuracy, speech error rate, search ranking, event-to-alert latency and inference cost.
- Numeric targets. Those are set from a first baseline on the owner's consented corpus, before any tuning.
