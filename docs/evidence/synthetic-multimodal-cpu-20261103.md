# Completed synthetic multimodal baseline — 3 November 2026

Issue #7 is a **benchmark** acceptance, not a promise that every measured
feature meets its quality target. This record completes actual synthetic input,
model-output, negative-case, measurement and prospective-target evidence for
its evaluation-fixtures/runner boundary. **Speech negatives and positive CLIP
retrieval fail the initial engineering targets.** No output was repaired,
discarded, retried or used to retune a prompt, model, threshold or label.

The [earlier 24-case person baseline](synthetic-person-cpu-20261103.md) separately
supplies actual precision/recall and confidence calibration. It remains frozen:
TP 8, TN 16, FP/FN 0; binary valid-only ECE 0.01708. That is a bounded cartoon
person-presence result, not a calibrated field posterior. Its earlier failed
admissions/timeouts and the separate public-photo pilots remain preserved.

## Actual measurements

| Modality | Planned / valid | Quality result | Measured latency p50 / p95 | Initial target outcome |
| --- | --- | --- | --- | --- |
| OCR | 12 / 12 | Exact 10/12; positive 6/8; blank negatives 4/4; CER 0.031250 | 6,991 / 7,315 ms | PASS on this corpus only |
| German speech | 8 / 8 | Six spoken positives exact; **2/2 negative hallucinations**; WER 0.069767, CER 0.073276 including negative insertions | 2,561 / 2,599 ms | **FAIL silence/noise rejection** |
| CLIP search | 10 / 10 | Six positive queries: recall/MRR/nDCG@5 each 0.166667; four negative queries all empty | 12.60 / 56.30 ms | **FAIL positive MRR/nDCG** |
| Held-scene event pipeline | 4 / 4 | Two expected person events received; no false alarms; no missed critical events | 17,588 / 17,653 ms, **two emitted-event samples only** | PASS for these tiny synthetic windows only |

All **34 planned evaluation units** have terminal valid observations; no error
or unrun slot is hidden as a correct negative. Each operational unit contains
two distinct temporal model samples of the same held scene, not a retry.
The explicit schedule additionally contains five excluded warmup model samples
(one OCR, one speech, one CLIP text, two operational), and 24 CLIP index-image
requests. Loading, warmup and index construction are not query latency samples.
Backend request totals were not independently metered.

Operational exposure is **73.634042639 real monotonic seconds**, or
0.000852245864 synthetic camera-days, across four closed-loop held generated
scenes. The planned maximum of 1,200 seconds is **not** the denominator.
Zero false alarms divided by this tiny measured exposure is zero; it is not a
field false-alarm estimate, day-long soak, continuous camera ingest or evidence
that rare false alarms do not occur. Two negative windows emit no event and
therefore have **no** alert latency, never a fabricated zero.

CPU totals for measured units are OCR **333,292.769 ms** (owned server cgroup),
speech **81,965.258 ms** (owned process group), retrieval queries **898.433 ms**
(owned process group; index construction separate), operational inference
**294,205.989 ms** (owned server cgroup). Parallel model threads make CPU time
larger than elapsed time. These are measured compute costs, **not bills**.
Every provider-reported monetary charge is unknown/null, not a claimed free
response or theoretical API price. No paid provider was called.

## Corpus, identity and execution

- Twelve owned 640×320 fictitious plate cards: day, darkening, glare, drawn
  rain, blur, occlusion, skew, multiple drawn objects and four blank negatives.
  The generated label is retained even when partially occluded. OCR spaces and
  uncertainty markers are not silently corrected against the expected label.
- Six locally synthesized German utterances using an installed Mac voice,
  including faster speech and added noise; two bounded silence/noise WAVs.
  All eight actual 16-kHz mono PCM inputs were decoded and checked. TTS labels
  are the synthesis text, not an independently heard human transcript.
- Twenty-four owned drawings form the CLIP index; six positive and four absent
  queries use fixed cosine cutoff 0.25 and k=5. The exact PNG inputs and their
  actual JPEG92 transformations are bound separately. Stable ID tie-breaking
  never uses gold. These drawings do not establish real-image retrieval quality.
- Four held scenes: day person, darkened person, empty blurred scene, cat.
  Real inference responses go through the actual camera policy/tracker and
  smart-event encoder to a new owned loopback HTTP sink. The first-to-sink
  timer includes both model samples and processing. It is **not** the deployed
  motion gate, ONVIF stream, Protect persistence/Alarm Manager or mobile push.
- Gold and input bytes were frozen separately before calls. No expected plate,
  speech transcript, relevance list or operational truth was in a model bundle.
  Source controls preserve every client start, raw response and terminal result
  locally. Public files contain only redacted aggregate metrics and bindings.
- Same-agent decoded visual/PCM review and **23 separate-code arithmetic
  checks** passed. This is not independent human review, natural-weather
  representativeness, audio listening acceptance or an AI Key reference.

The source base was `e6ee47ecde49c6fa50d9eb0380a7ef69cd52190f` plus individually
sealed then-uncommitted benchmark helpers/source modules. Those trials are
**not** claimed to have run the final PR commit or a production deployment.
Final scoring-source hashes and redacted file hashes are in the
[evidence binding](synthetic-multimodal-cpu-20261103/binding.json).

| Role | Exact model/artifact | Exact runtime |
| --- | --- | --- |
| OCR / operational vision | OpenVINO Qwen3-VL-8B INT4 snapshot at HF revision `8640be5dd2a92ddfe6ea1620c794d20a54ce3d51`; same verified 29-file artifact as the earlier person run | OVMS 2026.4.1, OCI config `6348136997e05a7ee2c2b08e47bb15deb6e5096c62ab3fb821907ded7e2afd7c`, CPU, 4 CPU equivalents / 12 GiB / 128 processes, two REST workers |
| Speech | Existing `large-v3-turbo-int8` artifact; exact 19-file tree `bda1d8db4345ead87114912ab9b53d2d8a1fe7160f2159e84f4e7d06acf2c0b5` | Image config `0c614db98e9809684d119ff04506ff75ccbe11122386f2fc0c24fdf36d26116a`; actual OpenVINO GenAI CPU / 4 CPU equivalents / 8 GiB / 128 processes |
| Retrieval | Existing CLIP ViT-L/14 paired ONNX artifact; exact five-file tree `77bc153b1b2808d41863a795a4b8558586694029ae6804af65964e170e484343` | Image config `515943bdcc65f01be8e6833b653de3a764b37a7d0de666abd1b8f9e4d1e486f1`; ONNX CPU / four encoder threads / 4 CPU equivalents / 4 GiB / 128 processes |

File-tree identities are SHA-256 of canonical sorted filename→size/SHA-256
records (the vision run separately binds its verified filename→SHA-256 map).
CLIP also verifies its native paired-encoder revision and encoding contract.
Whisper's HTTP route does not echo model/revision: its identity is the exact
controller-admitted runtime/artifact, **not invented response metadata**.

Fresh task-only CPU containers ran serially, non-root, capability-free, with
read-only models/source, no extra swap or GPU devices, isolated network
namespaces and only loopback addressed. Source/model hashes, process/task
identity, resource limits, host boot/mount identity and whole-run deadlines
were checked. Request timeouts were 120 seconds; the two-sample operational
helper had 250 seconds, not two undocumented retries. No production model,
pairing, provider, permission, alarm rule, service or GPU workload was changed.
All four owned runtimes/rootfs mounts are gone; model bytes, existing Comfy
process, host boot and normalized Kubernetes process snapshots are unchanged.

The speech controller's **post-exit total-cgroup read failed** because containerd
had already removed that cgroup. The original failure and eight completed
observations remain preserved. Per-input process CPU timers and cleanup proofs
are intact; total run cgroup CPU is unknown, and no model call was repeated.
The later controller handles that unavailable total explicitly. Refused export
preflights (insufficient staging headroom/unreadable excluded cache), initial
untagged-image import and a refused operational preflight remain recorded;
they were not inference attempts or relabelled successful trials.

## Targets, reports and original acceptance

The initial numeric engineering policy was frozen **before inference**. After
this measured baseline and before any tuning, the runner froze prospective
same-corpus no-regression thresholds. They retain the poor baseline where it
is poor; **they do not waive the failed initial speech/retrieval targets**.
Unknown timing/charge evidence never becomes a zero threshold.

| Evidence | Versioned aggregate report | Prospective numeric no-regression thresholds |
| --- | --- | --- |
| OCR | [Report](synthetic-multimodal-cpu-20261103/plate-report.json) | [Targets](synthetic-multimodal-cpu-20261103/plate-regression-targets.json) |
| Speech | [Report](synthetic-multimodal-cpu-20261103/speech-report.json) | [Targets](synthetic-multimodal-cpu-20261103/speech-regression-targets.json) |
| Retrieval | [Report](synthetic-multimodal-cpu-20261103/retrieval-report.json) | [Targets](synthetic-multimodal-cpu-20261103/retrieval-regression-targets.json) |
| Synthetic operation | [Report](synthetic-multimodal-cpu-20261103/operation-report.json) | [Targets](synthetic-multimodal-cpu-20261103/operation-regression-targets.json) |

[Initial target PASS/FAIL](synthetic-multimodal-cpu-20261103/initial-target-status.json)
is separate from benchmark completeness. The [runner contract](../benchmark.md#complete-attempt-matrices-aikey-multimodal-attempts1)
explains exact schemas, all-slot denominators, measured-vs-planned exposure,
actual-model helpers, redaction and rerun/target-freeze commands.

- **Implemented / actual synthetic-model measured:** versioned repeatable
  metrics per model/provider/profile/camera class, positives, negatives,
  confidence calibration from the frozen person baseline, actual initial
  multimodal baseline and numeric pre-tuning targets.
- **Fixture-tested:** strict/failed/unrun attempt accounting, measured exposure,
  actual crop/HTTP/parser/tracker/sink boundaries with explicitly injected unit
  responses, and unknown-vs-zero costs. Such fixture responses are not model
  measurements. Qualification results are linked from the PR.
- **Needs evidence, not silently claimed:** representative field quality,
  native Protect/mobile event-to-alert latency, sustained field exposure and an
  identical-corpus **real AI Key** run before any equality/best-quality claim.
  These are not prerequisites for claiming this original issue's synthetic
  benchmark runner is implemented and actually exercised.
