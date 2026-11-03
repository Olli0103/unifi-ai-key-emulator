# Local face-server input and model ownership (3 November 2026)

Narrow source work toward #20. Owned loopback HTTP, invented byte strings and
fake analyzers only: no real face, template, camera, model, enrollment,
controller session, deployment or running recognition service is used.

## Reproduced parent behavior

Eight new cases fail on parent `ea6a91c8f69c016fc53f7b9f42a41f1aec99a65f`:

- Repeated image/region fields and an unknown field are accepted.
- Oversized regions with a valid prefix are truncated and accepted.
- A missing multipart boundary and malformed part header produce HTTP 500.
- With the image limit temporarily reduced to 1 KiB, a synthetic 32-KiB
  streamed image is not rejected until the sender finishes the part. The
  test withholds its closing boundary: it does not allocate a huge image.
  `client_max_size` does not itself account for these streamed multipart reads.
- Explicitly cancelling the handler while its fake model thread is blocked
  releases the old handler-owned lock; a second analyzer thread starts before
  the first finishes. This is an explicit cancellation test, **not** evidence
  that the installed server cancels ordinary disconnected handlers by default,
  nor an observed native concurrent-model incident.

## Source change

Use the already installed aiohttp `BodyPartReader.read_chunk` in 8-KiB chunks,
checking cumulative length before retaining each chunk. The original inclusive
10-MiB image and 64-KiB regions limits remain. Exactly one image and at most one
regions field are accepted, in either order; duplicates, unknown/nested parts,
encoded parts and malformed multipart inputs receive fixed `invalid_request`
HTTP 400 responses. A partial oversized body is rejected before its sender
finishes. No truncation, image re-encoding or implicit model input conversion.
This bounds retained recognized part content, not every network/parser buffer,
decoded image allocation, connection count, upload duration or queued request.

The model operation owns a separate retained task. Its lock spans the actual
`to_thread` completion; an HTTP wait is shielded, so even repeated cancellation
of that waiter does not cancel the owner or admit overlapping model calls.
Queued operations recheck their request transport before starting a model.
Completions are observed after clients leave, and normal application cleanup
drains retained operations. This reuses the project's existing owned/shielded
task approach and existing asyncio/aiohttp, without a new package or executor.
It is not a way to stop a running native thread, a model timeout, persistent
queue, forced-process shutdown guarantee or global resource-admission limit.

OpenCV/YuNet/SFace loading, detector parameters, decoded pixels/crops, 128-value
embeddings, matching/enrollment/store/worker behavior and CLI defaults are
unchanged. Valid HTTP response rounding, error codes and health schema stay
unchanged. The module description now states the actual boundary: this server
does not make outbound service calls or persistent image/template writes;
embeddings **are returned to its requesting client**. That is not a guarantee
about the client's later forwarding or deployment logging.

## Qualification and remaining acceptance

Twenty new cases pass, including exact inclusive limits, original input bytes,
regions in either order, response values/count-only health, malformed/encoded/
nested parts, fixed private-error responses and recovery, repeated handler
cancellation, disconnected default-handler waiting work, and cleanup waiting
for an abandoned failing thread. The initial eight parent failures are retained
separately. Combined local face-server/worker selection: **28 passed, 17
media-dependent cases skipped** on the decoder-less Mac; Linux CI exercises the
existing dedicated media path. No new media skip or weakened test guard.

The broader face/store/Port/transport/documentation group passed **586 cases**,
with 17 existing media skips and four existing Pillow deprecation warnings;
whole-tree Ruff and diff checks passed. This is same-agent source qualification,
not an independent human review.

The broader issue remains OPEN. Native grouping/enrollment/search/restart,
false-match/unknown/migration/retention/deletion quality and separate AI Port
recognition acceptance are not established by these transport/thread fixtures.
The saved-only Key face scope in [the preceding change](key-face-scope-20261103.md)
also does not activate this server change or stop an already-running model.
