# Complete public-person adapter matrix — 2 November 2026

Part of [#7](https://github.com/Olli0103/unifi-ai-key-emulator/issues/7) and
[#14](https://github.com/Olli0103/unifi-ai-key-emulator/issues/14), not their
full acceptance. [Frozen public case summary](public-person-structured-30s-20261102.json).

## Prospective configuration and conservation

A new separately frozen run used source `130f2aab1df5d53fee8b4b35bbb36fdcfcbb776e`
(PR242), the same exact twelve public COCO JPEGs and original reviewed labels as
the [October pilot](public-person-pilot-20261029.md), and the primary local
fraction-coordinate adapter. Structured output was enabled. The explicit
request budget was **30 seconds**, helper cap40 seconds and admission window600
seconds; threshold0.5, output-token limit1,024, prompt/model/input order,
zero warmups and zero retries were declared before requests. The shipping
request default remains15 seconds; production profiles were not changed.

The existing local OVMS2026.4.1 route used official model revision
`8640be5dd2a92ddfe6ea1620c794d20a54ce3d51`, static INT4 weights, FP16 KV cache
and dynamic activation quantization disabled. This is the NAS Core Ultra7 255H /
Intel Arrow Lake-P Arc Pro130T/140T host, x86_64, kernel6.18.15,16 logical CPUs,
65,375,476KiB RAM. The running server command requested GPU; actual per-graph
or fallback-device attribution is **unknown**, not inferred from that request.
Provider-returned model identities matched the expected local alias.

All12 cases started exactly once and have terminal records. There are no
unrun/unsettled cases or automatic retries. Before/after checks preserved all29
official model files, eight process identities, five profile/TLS sets and six
work-fence links. The owned CPU helper settled and was removed. Existing shared
model-work admission and natural idle windows were used; no production restart,
provider/camera permission, threshold, model configuration or schedule changed.

## Results include the error

| Scope | Scheduled | Usable correct | Errors |
| --- | ---: | ---: | ---: |
| Person | 6 | 5 | 1 |
| No person | 6 | 6 | 0 |
| All | 12 | 11 | 1 |

**All-scheduled usable correctness and valid coverage:11/12 (91.7%).** Six
negative cases produced zero false alarms. Do not turn the usable-only11/11
into100% all-scheduled accuracy, or the usable positive5/5 into six-case recall.

The error is a strict benchmark normalization refusal of a reply containing a
zero-height box. An offline replay of the exact saved answer reproduces one
box rejection. The unchanged runtime parser also retains one valid person
sibling in that answer; the benchmark's predeclared stricter rule rejects the
whole case when any detection is invalid. The error was neither converted to
`none` nor cherry-picked into a success. This is not evidence of a native
camera false negative, nor a reason to weaken geometry checks.

The [valid-only input-bound v2 report](public-person-structured-valid-only-20261102.json)
covers **11 usable cases**, not the complete12-input corpus. Its confusion
counts are TP5, TN6, FP0, FN0. Answered-non-none calibration covers only five
usable person answers: ECE0.034 and Brier0.00122. These tiny selected confidence
statistics are not installation-wide probability calibration.

Nearest-rank API-plus-raw-receipt-fsync-plus-parser p50/p95 over all12 terminal
attempts, including the normalization error: **9,776/27,098ms**. These are
shared-server request-boundary durations, not cold/native-event/model-only or
hardware-isolated timing. They exclude admission waits and model hashing.
Provider-reported charge is unknown, not zero; electricity/API-theoretical costs
were not invented. Missing cached-token information remains unknown.

## Review, retained history and limits

A separate clean model context had inspected and labelled all12 JPEGs before
seeing gold, agreeing12/12. A later separate-context arithmetic review verified
exact media/gold hashes, all12 conservation, the error denominator, valid-only
confusion/calibration and all12 nearest-rank latency:151 checks, no disagreement.
The coordinator separately checked all eight digest bindings. This is **not
independent human annotation** or authentication of private provider receipts.
The coordinator's native producer checks are separate evidence.

Both preceding15-second runs remain terminal partial records: unstructured
3attempted/1usable-correct/2errors/9unrun; structured
3attempted/2usable-correct/1generic no-body error/9unrun. Their original transport
subtypes/remote causes remain unknown. The historical October7/12 run is not
rewritten. This new complete public matrix is not a controlled hardware-only,
schema-only or general quality/speed causal comparison: source, converted
checkpoint and/or runtime/protocol configuration differ across the history.

The corpus is tiny, public, not attested held-out and contains no representative
private/night/IR/weather/occlusion installation evaluation or real AI Key
reference. It tests person presence with strict adapter-output validity, not box
quality, other classes, the separate deployed fallback's grid prompt, native
zones/events/alarms or product parity. No predeclared installation acceptance
threshold is supplied by these results. The broader issues remain OPEN.
