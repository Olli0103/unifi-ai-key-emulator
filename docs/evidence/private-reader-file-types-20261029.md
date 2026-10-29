# Nonblocking refusal of nonregular private/state files — 29 October 2026

Twelve existing readers opened their input read-only and then checked its
file type using `fstat` on the opened descriptor. A named pipe without a
writer blocks in `open` before that validation. Depending on the reader,
this could stall configuration, detector/inventory initialization, credential
loading, event-budget access or replay-marker inspection. This is a local
filesystem availability defect, not a demonstrated remote exploit or a
production incident.

The readers now add `O_NONBLOCK` to their existing read-only/no-follow open.
They still refuse nonregular files using the same descriptor before reading.
Regular-file reads retain existing type, size, permissions/ownership (where
previously checked), parsing and error behavior. No owner/mode boundary is
relaxed and no new general file-cleanup or state-recovery policy is added.
POSIX ignores the nonblocking flag for ordinary regular-file reads.

## Covered paths

- API detector provider-key reader; inventory credential/trust reader;
  candidate configuration/state reader; PostgreSQL credential reader.
- AI Port adoption and management-credential stores; virtual-hardware state;
  administration Port configuration reader.
- Durable Port event-budget state; worker archive dry-run reader;
  uncertain-result resolution reader; worker admission replay-tombstone reader.

Directory opens/fsyncs, lock ownership, atomic writes, adopted identities,
credential rotation protocol, budget accounting and callback/replay policy
are unchanged. A refused tombstone is not silently discarded or retried.
The separate earlier administration startup/audit fix remains separate evidence.
This is not a claim that every filesystem consumer or path race is covered.

## Reproduction and checks

All twelve new regression cases fail on the prior source: a bounded child
process reaches the actual reader (a durable marker verifies that imports
finished), then times out waiting for a nonexistent pipe writer. The full
baseline is preserved; no production file or credential is involved.

With the correction, all twelve calls immediately report their existing
expected domain error. The pipe remains a pipe; no target, credential,
configuration, budget record, identity or tombstone is replaced or removed.
The only extra test file is the synthetic process-entry marker. Existing
positive/negative domain suites separately exercise regular data and safety
boundaries. This is source and synthetic-file evidence, not native recovery,
private credential rotation or a new deployed service result.

The corrected focused domain run passes **336 tests**, including synthetic
credential-rotation/recovery, adoption, inventory, budget, configuration,
worker/archive and resolution cases. Ruff, public-source audit and diff checks
pass. The first broader local run is retained separately: nine failures were
the macOS system OpenSSL's unsupported `passwd -6`, with FFmpeg absent from the
inherited PATH. Using the already documented OpenSSL/portable-FFmpeg tools
resolved those environment failures; no test expectations were weakened.
