# Isolated CPU synthetic-person baseline — 3 November 2026

**24 scheduled, 24 terminal, 24 valid and correct; no retries.** This is a
binary person-presence/response-contract baseline on 24 related, simple owned
cartoons, not representative camera quality or a product/native acceptance.
Issue #7 remains OPEN for the missing representative/modal/operational gates.

## Frozen inputs and configuration

- Eight depicted-person positives and sixteen person-absent negatives: empty
  scenes, pets, vehicles and packages. The named conditions are drawn/digital
  variants: day, night, glare, rain, blur, occlusion, false motion, multi-object
  and pets. A dark drawing is not an IR capture; a still drawing is not motion
  evidence. The generated two silence/noise PCM files were not sent to a model.
- An existing clean-context model review inspected all 24 individual images
  without gold or generator access, verified all media hashes and agreed on
  24/24 labels before inference. This is not independent human annotation.
- Exact application snapshot `d08301ffbc0e75c756a0865eb9a9b0e046d88eb9`:
  all **137** executed module files match that Git tree. Evaluation helpers
  were separately frozen by hash before calls; publishing them here does not
  pretend that they were already tracked in that earlier application commit.
- [OpenVINO/Qwen3-VL-8B-Instruct-int4-ov](https://huggingface.co/OpenVINO/Qwen3-VL-8B-Instruct-int4-ov/tree/8640be5dd2a92ddfe6ea1620c794d20a54ce3d51),
  revision `8640be5dd2a92ddfe6ea1620c794d20a54ce3d51`: 29 existing public
  snapshot files, 5,462,015,840 bytes, checked against official SHA-256/Git-blob
  digests. Static INT4 snapshot; CPU activation/KV precision is not separately
  claimed as FP16 or numerically equivalent to the production GPU route.
- Existing OpenVINO Model Server 2026.4.1 image config
  `6348136997e05a7ee2c2b08e47bb15deb6e5096c62ab3fb821907ded7e2afd7c`,
  imported OCI manifest
  `70688989701d47fd535da01e509350f001f25aa87fc2350b0d679901325d6fd9`.
  No new checkpoint/runtime download or production rollout.
- Intel Core Ultra 9 275HX, x86_64 Linux 6.14.0-35, 24 host logical CPUs,
  98,226,328 KiB host RAM. Actual cgroups constrain the trial to four CPU
  equivalents (`400000/100000`, not four dedicated physical cores), 12 GiB
  RAM, zero extra swap and 128 tasks. Containerd 2.1.3; client Python 3.12.3
  and Pillow 13.0.0. No GPU/NPU device is passed; the server reports CPU.
- One isolated private network namespace, no non-loopback address or IPv4/IPv6
  route, zero effective/permitted/bounding capabilities, no-new-privileges,
  non-root process, read-only runtime/model mounts and owned cache. An inert
  kernel IPIP fallback interface remains DOWN and unaddressed; it is not an
  external route. Existing GPU services and camera networks are not used.
- Explicit local `/v3/chat/completions`, requested/returned model
  `qwen3-vl-8b`, unchanged primary fraction-box prompt, JSON schema enabled,
  temperature 0, no seed control, 1,024 output tokens, person score threshold
  0.5, serial order, 120-second request/130-second helper deadline, 3,600-second
  run budget, one unscored black-image warm-up, no automatic retry. Invalid
  siblings, ambiguous JSON or wrong model identity reject the whole case.
  The 120-second evaluation budget does not change the production Port's
  default or its separately bounded configurable timeout.
- The successful new configuration explicitly sets `--rest_workers 2` for a
  serial trial. The preceding default-worker startup failure below is retained;
  no model output was available when the transport concurrency was changed.

## Complete measured result

| Measurement | Result |
| --- | --- |
| Planned / terminal / valid / errors / unrun | 24 / 24 / 24 / 0 / 0 |
| TP / TN / FP / FN | 8 / 16 / 0 / 0 |
| All-slot accuracy / valid coverage | 24/24 / 24/24 |
| Person precision / recall | 8/8 / 8/8 |
| Negative-case false-positive fraction | 0/16, not false alarms per camera-day |
| Always-none baseline accuracy | 16/24 |
| Binary person-score ECE, 24 valid cases | 0.01708333333333334 |
| Binary Brier, valid/all-slot | 0.0010458333333333342 / same |
| Warm API+receipt+parse p50 / p95 / p99 | 10,106 / 11,897 / 15,812 ms |
| Process-start to metadata-ready | 5.915 seconds, not a full cold-inference timer |
| Unscored black-image warm-up | 12,805 ms, excluded from scored percentiles |
| Provider charge / camera-day exposure / event-to-alert / hardware reference | Unknown / unmeasured / unmeasured / unavailable |

The request timer includes received-body/raw-response persistence and parsing,
not Python process startup or the final result-file write. It is not model-only,
native-event or alarm-delivery latency. Summed scored request timers are
203,497 ms, not total wall time or a throughput/SLA measurement.

The [all-slot binary report](synthetic-person-cpu-20261103.json) keeps failures
and unrun slots visible, assigning direct Brier loss one if either exists; here
neither exists. The unchanged canonical scorer also produces an input-bound
[version-2 report with camera/condition slices](synthetic-person-cpu-slices-20261103.json).
Its calibration has a **different denominator**: eight answered non-none
predictions, ECE 0.05125 and rounded Brier 0.003138. Do not confuse that with
the 24-case binary score above. Model-reported detection scores are not proven
calibrated probabilities.

Twenty-four easy related drawings saturating the score do not establish field
precision, night/weather/pet robustness, held-out generalization, false alarms
per day, plate/speech/retrieval quality, native event/zone/alarm behavior or
hardware parity. No CPU-versus-GPU winner is inferred from different hosts,
loads, startup paths and transport configurations.

## Prospective identical-corpus regression targets

The exact target function was frozen before any baseline output, not selected
after inspecting errors. It applies only to future runs on these same encoded
inputs and reviewed labels, before model/prompt/threshold tuning:

| Gate | Baseline-derived target |
| --- | --- |
| Valid coverage / person recall / precision | Each 1.0 |
| Negative false-positive / error fraction | Each 0.0 |
| Binary valid ECE | At most 0.03708333333333334 |
| All-slot binary Brier | At most 0.021045833333333333 |
| API+receipt+parse p95 | At most 13,087 ms |
| Complete accounting | Every one of 24 planned slots terminal |

These are small-corpus regression limits, not production acceptance thresholds
or a claim that future runs will pass. Unmeasured modalities/field metrics
remain `needs_evidence`; null cannot pass their gate.

## Preserved failed and refused predecessors

| Configuration/phase | Recorded disposition |
| --- | --- |
| Existing GPU, idle-only admission | No natural idle window; zero benchmark requests |
| Existing GPU, separately frozen shared-load run | First case timed out at 30 seconds; 23 unrun, no retry; original cause not established |
| Separate NAS CPU candidate | Fewer than required 20 GiB available; no candidate start or request |
| Olares runtime preparation | Rejected CLI/spec assumptions and empty-array JSON serialization guard, all before model calls; exact failures retained, no guard bypass |
| Olares default HTTP-worker CPU startup | Server exited on `pthread_create` EAGAIN before readiness/warm-up/scored requests; 24 unary plus 24 streaming workers observed. No direct `pids.events` root-cause attribution is claimed |
| New Olares two-HTTP-worker CPU configuration | One warm-up and all 24 scored cases complete; no retries |

The complete CPU matrix is not a relabelling or successful retry of the GPU
failure. Models, labels, prompts, parser and score gate were not tuned after
viewing predictions. Production model/profile configuration was not changed.

## Binding, audit and cleanup

| Artifact | SHA-256 |
| --- | --- |
| Unlabelled input manifest | `3ed26b75e2a7bbe61775e22d3cc0bfc77efa969733fbd792554ebecc5d5e633e` |
| Reviewed gold | `b47fd92fcd9b7f47b5c882f3267fe9c259fecdfa340d609a565c5af6eb958382` |
| Pre-inference label-review artifact | `72192e63b25e73476ab452eec0c2ec8b3d8aa69f29e634fe56c716ba6ce7d3ce` |
| Executed adapter | `9fc5088edf13c40db866e4ca2dc79a935c05dade226982e42553a614addd7c76` |
| Executed reporter/target function | `0bc80814ae7615539b654163b36a440dfccc3f61b05b9cbd9c2912658d08dd1c` |
| Pre-call successful configuration manifest | `c533be0ecc50fbbaf6e2c0b8f2eec78edb993a3eb911bebb9570ba457baed85e` |
| All-slot report | `9528aa98936b42215ab0a878342eebd61f88182f57b7da3098d9d4a9f8b455e6` |
| Version-2 slice report | `bfdbfa2fdc420e08dbbbc42e240a0d14ecfb48fabe5c721c162537b4829da448` |

The coordinator independently recomputed 166 count/hash/model/calibration/
percentile/target invariants using separate Decimal/bin arithmetic, without
importing the report implementation. That is same-agent verification, not an
independent human/security review. A no-network regeneration reproduced all
29 original corpus files byte-for-byte, including images, PCM and manifests,
in the verified local environment; it did not replace scored inputs.

All 311 selected native regular artifacts were sealed, transferred and
read-back verified, with a separate empty symlink inventory and detached
archive binding. Model/cache/rootfs storage is explicitly excluded from that
artifact transfer; the 29 model files have their separate verified binding.
The owned container/task and rootfs mount were removed. Original model bytes,
host boot, existing GPU service PID/state and every pre-existing Kubernetes
task ID/PID/state match before/after readback; raw task-list ordering is not state.
No production NAS mutation, camera change or public image/weight release.

Local qualification: 584 selected benchmark/documentation tests pass and the
whole-tree Ruff check passes. Exact-head full workflow results are recorded
separately in the integration PR, not inferred from this focused selection.

Private raw records, operational identifiers and host-specific launchers stay
outside this public source tree. These aggregate reports do not clear the
separate licensing, representative quality or native acceptance requirements.
