# Optional primary detection schema — 2 November 2026

Part of [#14](https://github.com/Olli0103/unifi-ai-key-emulator/issues/14)
and [#7](https://github.com/Olli0103/unifi-ai-key-emulator/issues/7), with
the saved operator choice in [#17](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17).

## Original baseline remains partial

A new bounded run reused the exact twelve reviewed public COCO images/labels
from the [29 October pilot](public-person-pilot-20261029.md), without new downloads,
private camera footage, label changes, retries or threshold/prompt tuning. It
used public source `8e811c57f9457b36e52ff7abda73bcfd60cdc105`, the primary fraction
prompt, threshold 0.5 and 1,024 output tokens on the existing local OVMS 2026.4.1
GPU server, official converted snapshot revision
`8640be5dd2a92ddfe6ea1620c794d20a54ce3d51`, FP16 KV cache and disabled dynamic
activation quantization. No server/model configuration changed for this run.

| Scheduled | Terminal attempts | Usable correct | Errors | Not run |
| --- | --- | --- | --- | --- |
| 12 | 3 | 1 | 2 | 9 |

The first person case was valid/correct (11,420 ms). The next negative case
returned malformed JSON (9,775 ms). The third had no reply receipt after about
15 seconds (15,030 ms), so the predeclared uncertain-transport stop ended
admission. The original runner saved only the generic transport-or-invalid-output
category: the exact error subtype and origin/driver cause are **unknown**, not
inferred from elapsed time. Nine unrun cases are unknown, not fabricated rejects.
There is no full-matrix accuracy, calibrated quality, latency-percentile or
hardware-parity result from these three attempts. The original 7/12 October
result is unchanged; checkpoint/parser/runtime configuration differ, so this is
not a controlled GPU-only comparison.

All 29 official model files, eight existing process identities and five saved
profile/TLS sets matched before/after. The owned public-input helper was removed.
Inputs/labels were separate from the model mount, source hashes were frozen,
and provider-reported charges remain unknown. Pre-model preparation refusals
(unsealed byte caches; case-sensitive Docker missing-object diagnostic) are
retained separately; neither made a model call. This is a terminal partial run,
not a retry target or completed twelve-case benchmark.

## Source change

The primary detector previously requested free text with an exact JSON example;
the separate local fallback already requested a schema. An explicit default-off
`structured_output` option now adds the primary **fraction-coordinate** schema
through the existing Ollama/compatible request adapter. The existing prompt,
image order, destination, sampling, token floor, parser and score/confirmation
gates stay unchanged. No schema is added to OpenAI/Anthropic or legacy requests.

The schema bounds count/shape/classes/scores/coordinates and requests optional
plate text only in existing authorized primary plate scope. The parser still
checks kind/label relationships, geometry, ambiguity and finite values; server
schema support does not attest those semantics. A server rejecting the schema
is not retried as free text. Existing explicitly configured fallback/backoff
and cost rules are not expanded.

The validated saved setting reaches the actual runtime detector factory. The
provider form shows an explicit choice, preserves it for older clients omitting
the field and supports explicit disabling and exact provider rollback. Saving
does not test a model, create scope or activate/restart any service. Null/non-boolean
or unsupported-provider modes and malformed new-form fields refuse before an admitted write.

## Evidence and remaining limits

Source tests cover unchanged legacy payloads, exact schema/route/prompt/image
preservation, malformed replies and grid rejection, no unconstrained retry on
schema refusal, plate permission/separate-OCR behavior, invalid configuration,
runtime-factory forwarding, saved preservation/rollback and malformed browser
fields. Real fictional desktop/mobile/keyboard browser flows cover default off,
enable, provider edit preservation, explicit disable, audit and session expiry,
with no foreign or real provider/model/media/processor requests.

This is an opt-in interoperability feature, **not** a completed detector-quality
fix or native deployment. The malformed saved answer still fails the parser.
Any future schema-enabled model run needs a separately frozen configuration and
complete planned matrix; it must not overwrite the preceding partial run.
Representative private/night/IR/weather/occlusion footage, native event/zone
quality, performance and identical-corpus hardware reference remain unproved.
