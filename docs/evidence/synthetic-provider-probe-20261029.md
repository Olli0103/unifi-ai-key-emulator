# Synthetic provider test — 29 October 2026

Implementation/fixture progress for #8, #13 and #17. The parent issues retain
their broader role, activation, device and browser/native requirements.

The control site exposes an explicit synthetic vision test for the configured
AI Key caption or AI Port API-detection role. It accepts only the profile,
role, revision, CSRF, one-use grant and cost acknowledgement, never a caller's
URL, model, key or media. The role/model/destination is disclosed before an
operator sends one generated 320×96 image with eight random hexadecimal
characters. The expected answer is not in the prompt. Existing adapters build
normal OpenAI, Anthropic, Ollama or compatible requests; no new SDK is added.

The ephemeral grant is session/profile/role/revision-bound, expires after
120 seconds, and is consumed before key loading or transport. Restart loses
all outstanding grants, so replay is refused rather than replaying a possibly
charged request. Capacity is 64 pending forms, one in-flight test and a
30-second cooldown. Missing acknowledgement, unexpected/duplicate fields,
invalid session/origin/CSRF, stale role or wrong/expired/reused grant fail
before contact. Speech tests remain explicitly unavailable.

Transport uses the guarded provider connector and role-only private key reader,
no controller certificate/credential or cookie, no environment proxy,
redirect/decompression/retry/fallback. There is a 20-second deadline with a
monotonic late-result check, a 64-KiB strict-JSON reply cap, and a request limit
of 128 output tokens. Only a boolean match and elapsed time are returned;
provider text, keys, image, prompt and expected characters are not displayed
or persisted. A match is one synthetic API-path result, not general modality,
internal model identity, recognition/retrieval quality, deployment or cost.

No config, camera, budget, job/index or adoption identity changes. Results are
not activation prerequisites or stored model capability attestations. Missing
keys or known address-policy refusals have a proven no-request result; other
unverified post-request outcomes remain potentially charged. No failed action
is automatically retried/refunded. A configuration change or disappearance
during the test prevents presenting the old result as current.

## Checks and native boundary

Loopback adapter and signed-form tests cover credential/role separation,
missing/wrong answers, API errors, redirects, compression, stream/incomplete/
oversized/ambiguous replies, deadline and external cancellation; session/grant
expiry and restart/replay, parallel calls, stale/unreadable settings, missing
keys and byte-identical configurations. The actual generated PNG has no
metadata or personal input. HTTP/HTML checks do not establish visual/mobile
browser acceptance; the earlier loopback browser fixture remains policy-blocked.

A separate local NAS candidate preflight identifies the existing official
OpenVINO Model Server and verifies its local model directory/mount, no provider
credential, and unchanged configuration/source bytes. Its first admission
stopped before creating the one-shot start marker. **No model request occurred
in that attempt.** The wrapper did not retain the exact first failure stage;
subsequent health reads separately observe active caption work, not proof of
the original exception. The device services were
not stopped or reconfigured to obtain an idle window. That first admission is
not a native inference success; its failure is retained, not relabeled.

Later on the same day, a separate health read observed naturally idle workers.
After independently confirming that no start/result marker existed, the same
exact candidate was admitted once. The existing local OpenVINO Model Server
returned the generated eight-character image challenge correctly in 3,111 ms.
The native receipt records one client attempt, no retries, no provider
credential, no camera media, source/configuration byte checks, and the same
adopted/search-connected device with zero active/queued/pending workers before
and after. The durable start marker and result were independently read back;
the temporary isolated test container had exited. They prevent repeating this
completed test as a new admission.

The tested `provider_probe.py` SHA-256 is
`cc48f2fe80634898d09e1cfcd6f4b89ad7ef4af51c023503cc1fed5a94462f48`,
from source commit `dcacb4f7c1bf1aef2d623b9979ecfa8e34614607` (#139).
The candidate ran as an unprivileged, read-only, capability-dropped container
with bounded resources, only the required read-only config/certificate and
private receipt directory mounted; no device private key or camera-state
directory was mounted. No deployed processor/control-site code was replaced.

This establishes one native synthetic vision API-path result, not a complete
control-site browser flow, model-internal identity, general modality, quality,
ongoing performance or provider billing guarantee. No real photo, caption,
transcript, face template or other camera data was used or exported.
