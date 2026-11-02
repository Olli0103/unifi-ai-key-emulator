# Structured caption-backend smoke check

Progress for [benchmark acceptance](planning/issues.md) (#7) and deployment
qualification (#24). This is a development/operator tool, not a new health
probe, production watchdog, quality benchmark or release-acceptance shortcut.

A model API can list its model and answer a single-image question while giving
incorrect answers through the structured multi-image path. In the 2 November
2026 GPU investigation, a red/blue pair sometimes returned black/blue after
startup. A single-image success therefore did not qualify that candidate.

## Run on an already isolated candidate

Use `tools/caption_backend_check.py` from the source checkout with the existing
runtime dependencies installed. There is no new model/SDK dependency. The tool
uses `VisionProvider.build_structured_request`, the same adapter as the deep
session worker: system role, two images before the user text, a
`session_description` JSON schema, temperature zero and 128 output tokens.
The images are generated 512×512 solid red and blue PNGs; no camera input,
controller credential, saved device profile or provider key is loaded.

**First isolate the candidate from production and obtain any required shared
model-work admission.** Run inside its network namespace with source and a
private writable receipt parent available. The tool cannot prove isolation,
acquire the deployment's maintenance lock, restart a model or establish that a
restart occurred. Do not point it at a production loopback listener as a way to
bypass admission. Use the exact frozen image/model/runtime settings you intend
to qualify. Avoid warming the model before the first request when investigating
a startup defect; record start/identity/configuration evidence separately.

For an already isolated candidate listening on loopback port 8000:

```sh
python tools/caption_backend_check.py \
  --base-url http://127.0.0.1:8000/v3 \
  --model candidate-model \
  --output /private/receipts/new-caption-check \
  --acknowledge-load
```

Replace the model ID with the candidate's actual ID. The receipt parent must
already exist and the output directory must not exist, even empty. Execution
requires explicit load acknowledgement and an HTTP **numeric loopback** address;
DNS names, remote/private-network addresses, HTTPS, URL credentials and query
strings are refused before contact. HTTP is limited to loopback; this is not
permission to disable production TLS. No request URL/key is accepted from
response data. No redirects, environment proxies, cookies, automatic retries,
fallback, service start/stop, activation or configuration writes are used.

## Gate and failure evidence

At most **three identical sequential structured requests** are made. Each must
return HTTP 200, a bounded uncompressed JSON object, one complete (`stop`)
non-refused/non-tool text answer, and exactly:

```json
{"first_color":"red","second_color":"blue"}
```

Extra keys, reversed/wrong colors, code fences, duplicate keys, nonfinite JSON,
truncation and malformed replies cannot pass. A mismatch or transport failure
stops the sequence immediately, rather than retrying until it passes. Each
request has a 100-second budget (including connect/body reads), a three-second
connect limit and a 64-KiB reply limit. The diagnostic prefix may include one
extra byte to prove oversize; it is explicitly marked truncated and never
parsed as a successful response. A failed/partial run is never resumed.

The new output directory is private (0700), with exclusive private files
(0600):

- `request.json`: exact bytes sent, identical for all repetitions.
- `started.json`: fixed suite size and request digest before client creation.
- `admitted-N.json`: written before HTTP admission; it means a request **may**
  have started, not a server-confirmed execution.
- `response-N.bin`: bounded raw bytes, including partial/error bytes, committed
  **before** answer validation. These can contain server-generated diagnostics;
  inspect locally, never publish blindly.
- `result-N.json`: fixed outcome, HTTP status if known, observed elapsed time,
  request/response SHA-256 and truncation flag. No raw answer is echoed.
- `report.json`: terminal aggregate. It is also written on normal task
  cancellation, retaining incomplete admission. Abrupt process/power loss may
  leave only the earlier receipts; that is an uncertain run, not success.

Files are fsynced along with directory entries. This is diagnostic durability,
not a tested power-loss/filesystem guarantee. Existing output refuses before
contact, preserving prior failures and preventing accidental replay of that
run. An intentional new output is a new request authorization; it does not
make an old failure disappear. Receipt write failure aborts, never retries.
Exit status is 0 only for three matches, 1 for a completed failed gate, and 2
for input/output refusal. External cancellation or unexpected errors can exit
otherwise; inspect the saved receipts before deciding on a new attempt.

## What a match does not prove

The report explicitly leaves cold start, internal model identity, native
Protect persistence and recognition quality **unverified**, and provider
charges **unknown**, not zero. Request/response hashes bind stored bytes, not
server truth. Record the candidate process/image/model hashes and externally
verified restart separately. Three successful synthetic color responses are
not a corpus benchmark, useful-caption evaluation, native event test, throughput
or sustained-operation proof. This tool does not validate native session
prompts, embeddings, callbacks, search, CPU/GPU performance or hardware parity.

One Intel-GPU recovery on 2 November used the unchanged INT4 model export with
`KV_CACHE_PRECISION=f16` and `DYNAMIC_QUANTIZATION_GROUP_SIZE=0` on OVMS 2026.4.1.
Those are runtime options, not a weight conversion or universal GPU remedy.
FP16 cache alone still failed this structured test. Candidate configuration and
actual worker/native-event readback must be qualified independently; this tool
does not install or activate those settings.

## Automated coverage

Disposable loopback doubles exercise all three matches and each failure
position, actual generated pixels and worker-adapter request shape, wrong and
reversed colors, JSON ambiguity/truncation/refusal, response byte limits,
redirect/compression refusal, timeout with retained partial bytes, task
cancellation, private/exclusive receipts and repeat refusal. A regression
explicitly shows the existing single-image probe matching while the same
fixture backend fails this structured path. No real model, GPU, production
service or provider is contacted by these tests.
