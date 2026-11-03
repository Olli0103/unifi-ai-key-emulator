# CLIP upload and shared encoder ownership (3 November 2026)

Source work toward stable local query/image embedding in #21/#18 and bounded
operation in #24. Loopback HTTP, invented byte strings and fake encoders only;
no real image/query, checkpoint, tokenizer, index, controller, provider or
running CLIP/Face service is used or changed.

## Reproduced parent behavior

Nine regressions fail and two valid controls pass on unchanged parent
`04d284281ff7e82512dbd9d84f38616bcf223a6c`:

- repeated image/region fields, unknown fields and oversized regions with a
  valid truncated prefix are accepted;
- missing multipart boundaries and malformed part headers return HTTP 500;
- with a temporary 1-KiB image limit, a fictional 32-KiB streamed upload is not
  rejected before its sender supplies the withheld closing boundary;
- explicitly cancelling a blocked encoder's HTTP handler releases the old
  shared text/image lock before its thread finishes. Text-then-image and
  image-then-text both admit overlapping fake encoder threads.

This is not a claim that the installed service cancels ordinary disconnected
handlers by default, nor an observed native overlap, memory exhaustion,
checkpoint corruption or wrong search result.

## Reuse and source boundary

The existing bounded face-server part reader from
[the preceding source change](face-server-boundary-20261103.md) is extracted
into `aikey.multipart_input.read_part`. Its AST is identical apart from its
function and domain-error class names; the face module is otherwise AST-exact.
Both handlers catch that ValueError-derived error as the same fixed HTTP 400.
This is a small reuse of installed aiohttp, not another upload service/library.

CLIP now uses that 8-KiB chunk reader with its original inclusive 10-MiB image
and 64-KiB region limits. One image and optional single regions field, in either
order, are accepted. Duplicate/unknown/nested/encoded/malformed parts receive
fixed `invalid_request` responses. Oversized content is rejected before it is
retained in full or a still-uploading sender finishes; no region truncation,
JPEG decoding/re-encoding or byte substitution in the transport adapter.

One retained owned task holds the shared text/image lock through actual
`to_thread` completion. HTTP waiters are shielded; repeated cancellation cannot
cancel that owner or release its lock early. Queued work checks its transport
before starting an encoder, completions are observed after clients leave and
normal application cleanup drains retained work. No new package/executor,
checkpoint, preprocessing, model identity, vector normalization, revision pin,
index write/migration, worker policy, inference permission or retry is added.

CLIP region validation, image preprocessing, weight hashing, ONNX encoder
construction and CLI entry are AST-exact. Valid original image bytes and
normalized 768-value response/model/revision/health schema remain unchanged.
Counter values are observations, not proof of native retrieval or live threads.

## Qualification

Twenty-two new cases pass: nine reproduced failures, valid text/image controls,
inclusive image/region limits, unchanged image bytes, nested/encoded parts,
fixed input/CLIP/unexpected encoder errors and cross-modal recovery, default
handler disconnected waiting work, and cleanup waiting for an abandoned failing
thread. The face-server's twenty existing boundary cases also pass after the
reader extraction, including original error/health/value contracts.

The first expanded group passed **111 cases with 57 existing media skips**.
The broader CLIP configuration/client/Find Anything/local face/documentation
selection passed **581 cases with 74 existing media skips** on the decoder-less
Mac. No new test skip or weakened production guard. Linux CI supplies existing
dedicated media coverage; results are recorded per exact head, not inferred
from these local groups. Source/AST review is same-agent, not independent review.

## Limits and operation

Retained recognized parts are bounded, not every network/parser buffer,
decoded-image allocation, text-JSON allocation, connection/queue count, upload
duration or encoder runtime. Thread ownership is cooperative process-local
serialization, not forced thread cancellation, durable queuing or guaranteed
drain after a process is killed. The JSON text adapter is not redesigned here.

The shared helper does not automatically update other local model servers.
Running CLIP, Faces, Key, Ports and indexes remain unchanged; installed
[Admin258](admin258-native-20261103.md) is a different source/installation scope.
Native query/index compatibility, retrieval quality, controlled disconnect/
restart/overload/endurance and real camera acceptance remain unqualified by
these fixtures. The broader original issues remain OPEN.
