# Detection-quality benchmark (issue #7)

`python -m aikey.benchmark score <cases.json>` scores labelled predictions. It never reads media and never calls a provider. The owner produces predictions locally from consented or synthetic material, and only the labelled records are scored. Private footage stays out of the repository.

The [29 October public person adapter pilot](evidence/public-person-pilot-20261029.md)
records twelve real local predictions: seven usable/correct and five errors. It
is separate from owner-camera quality and the deployed fallback route.
The [2 November structured30-second matrix](evidence/public-person-structured-30s-20261102.md)
completed all twelve cases: eleven usable correct and one normalization error.
Its valid-only report has eleven cases; it is not a general quality or hardware
acceptance, and earlier partial runs remain unchanged.

The [3 November isolated CPU cartoon matrix](evidence/synthetic-person-cpu-20261103.md)
completed 24/24 valid, correct binary person-presence cases, with prospectively
frozen same-corpus regression targets. These related drawings are not field,
night/IR, weather, audio, native-alarm or hardware-equivalence acceptance. The
earlier GPU timeout and refused CPU admissions remain separate failures.

The [complete synthetic multimodal baseline](evidence/synthetic-multimodal-cpu-20261103.md)
adds actual OCR, Whisper, CLIP ranking and model-to-owned-HTTP-sink measurements.
Speech negatives and positive retrieval **fail** their initial engineering
targets. A completed benchmark is not a quality promotion or native acceptance.

### Owned synthetic evaluation helpers

`tools/benchmark_fixture_corpus.py` creates a **new** private output directory
with 24 deterministic cartoon PNGs, unlabelled `inputs.json`, separate
`gold.json`, an ID-labelled contact sheet and two negative PCM audio inputs.
It refuses an existing output directory. No camera, provider or model is read.

```sh
python tools/benchmark_fixture_corpus.py /private/evaluation/corpus
```

The drawings need independent visual label review before inference. Freeze the
exact encoded inputs, reviewed gold, application source, adapter/report bytes,
model/runtime/device, schedule, warm-up, timeouts and stop/target policies first.
Keep gold and the contact sheet outside the model's unlabelled input bundle.
PNG byte reproducibility across different Pillow/zlib environments is not
promised: retain and compare the actual frozen input bytes, never silently
substitute freshly encoded images into an existing corpus binding.

`tools/benchmark_person_adapter.py` executes exactly one explicitly selected
case from such a prepared bundle. It records a start marker, any received body,
raw response and terminal outcome using exclusive writes. It checks the exact
returned model, strict JSON and every detection sibling; it does not rescue a
valid sibling from a malformed case. It has no automatic retry or fallback.
This deliberately local-only low-level helper does **not** provision a server,
acquire production admission, select or download weights, pause cameras or
attest the controller's manifest. Use an isolated admitted evaluation route or
the existing enforced shared-model protocol, never an unguarded live endpoint.

`tools/benchmark_person_report.py` retains all 24 planned slots, including
errors and unrun cases, and separately reports valid-only binary calibration.
Its target function must be frozen before the initial predictions; targets
are emitted only for a complete matrix and apply to future identical-corpus
regression, not production promotion. It performs no inference:

```sh
python tools/benchmark_person_report.py /private/evaluation/corpus/gold.json /private/evaluation/results --manifest /private/evaluation/bundle/execution-manifest.json
```

Only the image person-presence task was evaluated in the dated CPU receipt.
Generated silence/noise inputs are **not** a speech/audio-model evaluation.

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

- Field-quality predictions from consented footage remain owner-local. The dated
  synthetic baseline above supplies actual synthetic-model measurements, not
  representative camera footage or a field false-alarm rate.
- New corpus/profile targets come from its first measured baseline before
  tuning. The dated synthetic corpus has frozen numeric targets; they do not
  automatically transfer to another installation, corpus or model.
- An independently measured real AI Key reference on the identical corpus before a hardware-equivalence claim.

## Complete attempt matrices (`aikey-multimodal-attempts/1`)

Use this wrapper for new measured plate/speech/retrieval/operational trials.
It adds a frozen plan with exact input/truth, model/runtime SHA-256s, per-kind
model/provider/profile/source identity, prospective policy digest and explicit
latency/CPU scopes to the canonical multimodal metrics below. Reports include
overall, camera-class and condition slices without per-case text or identities.

Every planned slot is either `valid`, `error` or unrun. An error has a fixed
error code, **not** an empty prediction; it cannot earn correct-negative credit.
Each slot has one client attempt, nullable observed latency/CPU/provider charge
and an artifact digest. A compound operational unit binds both temporal samples
in its input manifest; those are distinct samples, not automatic retries.
`complete` means all slots terminal, not all correct or quality targets passed.
WER/CER/ranking remain explicitly valid-only; coverage, all-slot positive-hit,
negative-rejection and exact-match denominators retain errors and unrun slots.

Operational plan `camera_days` is the **maximum** planned exposure. Each valid
attempt supplies its separately observed `camera_days`, bounded by that maximum.
No unused timeout budget or failed/unrun window becomes measured exposure.
Synthetic held-scene windows must be labelled synthetic, not field camera-days.
No emitted event means no measured alert latency, never a fabricated zero.
CPU is owned-process-group or owned-server-cgroup time; it is not a monetary bill.
No reported charge means `null`, distinct from a genuinely reported zero charge.

```sh
python -m aikey.benchmark multi-attempt-score /private/evaluation/attempts.json
python -m aikey.benchmark multi-attempt-targets /private/evaluation/attempts.json
```

The second command requires a complete valid baseline and derives numeric
no-regression floors (error metrics at most baseline; success/ranking at least
baseline; complete timing p95 at most 110% of baseline, rounded up). Freeze that
output **before tuning**. It does not waive failed initial engineering targets:
a baseline with zero silence rejection still has a silence problem. Missing
timers remain `null`/`needs_evidence`; timing comparisons require the same
independently checked host/resource envelope. Matching declared corpus hashes
are necessary, not independent attestation or permission for a hardware claim.

### Actual-model helpers

`tools/benchmark_multimodal_corpus.py` renders owned fictitious text cards,
locally synthesizes German TTS using an explicitly selected installed Mac voice
and FFmpeg, and creates an unlabelled cartoon search index with separate gold.
Its encoded bytes and generation environment must be frozen, not regenerated
silently for an existing run. Never include gold/contact sheets in a model bundle.
It performs no recognition, writes only a new directory and downloads nothing.

`tools/benchmark_modal_adapter.py` executes one frozen OCR slot through the real
crop/normalization adapter, preserving a failed request separately from no plate.
`tools/benchmark_local_modal.py` runs a frozen local CPU Whisper or ONNX CLIP
model through the real loopback server/client/parser routes. It records loading,
warmup and index construction separately from measured inputs, preserves actual
input transformations, and stops after an uncertain failure without retrying.
Whisper does not echo model identity: its binding comes from the exact admitted
artifact/runtime, never a fabricated server-reported model match.

`tools/benchmark_operation_adapter.py` executes two actual inference samples
of a held generated scene through the tracker, smart-event encoder and an owned
loopback HTTP sink. It records real monotonic exposure and sink arrival, not a
virtual clock, copied predictions, motion-gated ONVIF ingress or Protect/mobile
notification. Labels are joined to observed event counts outside the worker.

These helpers are explicit evaluation entrypoints, not automatic deployments or
admission attestations. The external owner must verify exact source/model/runtime
digests, private networking, read-only inputs, CPU/RAM/process limits, a whole-run
deadline, no prior active owner, and terminal cleanup. They read no production
profiles, credentials or camera state. Raw outputs stay private; publish redacted
aggregate metrics and scope only. Unit-test transcribers/embeddings are fixture
checks and must never be reported as actual model measurements.

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
| `identity` | `expected`, `predicted`: exact opaque IDs or JSON `null` for unknown/rejected | Known correct/misidentified/rejected decisions, unknown false accepts/correct rejections, identification precision and denominator-specific rates |
| `operation` | `camera_days`, `false_alarms`, `critical_events`, `missed_critical`, nullable `latency_ms` and `provider_charge_usd` | False alarms/camera-day, critical misses, nearest-rank latency p50/p95/p99, known/unknown provider-charge accounting |

Recognition/retrieval kinds require both positive and negative cases. All kinds
are optional; an absent kind is not claimed as evaluated. Plate/speech labels and predictions
normalize whitespace only: case, punctuation and uncertain plate characters are
preserved. `?` is not a correct character. Empty predictions remain misses; empty
references remain negative controls. Text errors are micro-aggregated (total edit
operations / total reference units), so silence insertions cannot disappear in an
average over only nonempty references. A zero denominator is `null`, not success.

### Identity decisions, not a face-recognition experiment

The optional `identity` kind scores **supplied decisions**, not photographs,
embeddings, pairwise verification scores, face grouping or cross-event tracking.
The existing CLI accepts the [fictional example](../examples/benchmark-identity.json):

```sh
python -m aikey.benchmark multi-score examples/benchmark-identity.json > identity-report.json
```

Its six labels/predictions and non-media input strings/digests are invented only
to exercise arithmetic. No model produced them. They are not a real false-match
measurement, enrollment/deletion/restart test, migration or native-quality PASS.
No inference package, edit-distance library or checkpoint is needed for an
identity-only file. Other kinds retain their existing dependency requirements.

Identity labels are exact opaque ASCII tokens (1–64 characters: letters,
digits, `_ . : -`). Only JSON `null` means unknown in ground truth, or a rejected
prediction. Empty strings/Booleans/whitespace are invalid; literal `unknown` is
an ordinary identity ID, not an alias for `null`. No case-folding, fuzzy matching,
silent aliasing or name inference. Use locally assigned tokens, not personal
names. Images, vectors, confidence and threshold fields are not part of this
decision-only schema. Preserve model/space/decision-policy provenance in the
existing run/profile identity and local source notes; do not mix SFace/Port
spaces or infer threshold equivalence from a comparison.

Every identity dataset needs at least one known and one unknown truth trial.
For each overall/camera/condition group, the report separates:

| Measure | Numerator / denominator |
| --- | --- |
| Known identification accuracy | Correct identity / known trials |
| Known misidentification rate | Non-null wrong identity / known trials |
| Known rejection rate | Null prediction / known trials |
| Unknown false-accept rate | Non-null prediction / unknown trials |
| Unknown rejection rate | Null prediction / unknown trials |
| Identified precision | Correct known identity / all non-null predictions |
| Overall accuracy | Correct known identities plus correctly rejected unknowns / all trials |

Counts accompany these rates. A single-class slice or no non-null predictions
can have a missing denominator: `null`, never a fabricated zero/perfect score.
Overall accuracy can hide class imbalance; read the known/unknown counts and
separate error rates. These are open-set **identification-decision** metrics,
not pairwise-verification FAR/FRR, detection recall or confidence calibration.

Expected IDs and exact input digests are bound into corpus identity; predicted
IDs are not, allowing like-for-like model comparisons on the same source/truth.
Reports and comparison deltas omit raw expected/predicted IDs, individual case
IDs, images and vectors. The corpus digest is a binding, **not anonymization or
independent authentication of low-entropy/private labels or source files**.
Reports still include operator-supplied run/camera/condition metadata. Keep
private inputs and sensitive slice labels local. Real quality requires a
consented, representative source corpus and actual independently checked
predictions; this scorer supplies neither and sets no acceptance target.

This is an additive optional kind under the existing version-1 multimodal
schema. Files/reports without it keep their existing behavior; older releases
that do not recognize the kind refuse it rather than silently score it as
another modality. [Source qualification and limits](evidence/identity-decision-benchmark-20261103.md)
are separate from future real recognition measurements.

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

## Structured VLM startup smoke check

The separate [caption-backend check](caption-backend-check.md) tests the worker
request shape with generated red/blue images and retains failed raw responses.
It is an explicitly admitted isolated-candidate check, not a recognition corpus,
quality score, automatic health probe or proof of a cold start/native persistence.
