# Private worker failure categories

Three already archived native caption failures exposed an observability gap:
the existing five-field replay tombstone correctly blocked repeat work but no
longer retained a failure reason. This change cannot reconstruct those causes
and does not repair or replay those events.

The worker now attempts a separate `worker-failure-notes/` publication **after**
its existing durable failure/callback handling. This is not a new request,
retry, paid inference, model fallback, callback or permission. Successful jobs
do not create the directory. Cancellation/admission refusals are not covered.
Notes neither authorize work nor participate in replay/archive decisions. The
original error messages and five-field schema-1 tombstones remain unchanged.
An older worker ignores the separate directory; no journal migration is needed.

## Meaning and limits

Each versioned record contains only opaque SHA-256 job/input identifiers, a
fixed exception category, the journal's last-updated timestamp and state at
publication, and (only for controller-media/callback HTTP failures) a numeric
HTTP status. The timestamp is **not an exact exception/native-event time**.
Hashes are linkable private metadata, not anonymization or encrypted storage.
No camera/event identifier, caption, frame, URL, credential, provider body or
exception text is copied into a note. Existing active journals/logs have their
own separately documented content and lifetime.

The first successfully recorded category per job/input wins and is immutable.
A later successful retry does not erase it: a historical exception is **not a
current outage**. An on-demand error callback can be accepted and the journal
state be `completed` while this historical note still describes the original
processing exception. If that error callback also fails, this note retains the
first processing exception, not an exhaustive chain of every later failure.

Categories are type-based: timeout, controller-media HTTP, controller-callback
HTTP, vision provider, speech provider, local CLIP, face store, HTTP transport,
OS, general worker or unclassified exception. At most four explicit exception
causes are inspected; strings are never parsed for a guessed root cause. A
provider category is not proof of provider downtime, and timeout is not proof
that the whole-job deadline rather than a nested operation expired.

The directory is same-user/private 0700, with private regular 0600 leaves,
bounded reads and descriptor-anchored access. A non-waiting kernel writer lock,
atomic no-overwrite publication and fsync protect cooperative writers. There
is no filesystem-I/O deadline. Capacity is 1,024 notes (at most 2 KiB each),
plus the lock and an in-progress publication. Full capacity, a busy lock,
unsafe state, interrupted stray files or I/O failure can prevent a note. Notes
are not automatically deleted or rotated. Preserve them in private full-state
backups; do not delete replay markers to retry work. This is not a secure-erasure
or universally crash-/power-loss-tested guarantee.

The worker's `failure_notes.current_process` health counters count publication
results (`written`, `already_recorded`, `busy`, `capacity`, `unavailable`) since
process start. They do not scan stored notes, count current failed jobs or prove
complete coverage. A failed final fsync may report `unavailable` even if a note
is readable. No diagnostic fault is allowed to replace the original job error.
The native queue reply still exports only its existing fixed queue fields.

## Bounded operator read

Use the two opaque hashes from the matching private journal/tombstone, not raw
camera/event IDs:

```sh
python -m aikey.worker_failure_notes --state /private/state \
  --job OPAQUE_JOB_HASH --fingerprint MATCHING_INPUT_HASH
```

The command reads one exact private note without directory creation, network
traffic or journal writes. Exit 0 means recorded; exit 1 means not recorded or
unavailable. Missing legacy notes mean **unknown, not reconstructed**, never
success or an inferred failure reason. This is not a public diagnostics export.

## Qualification boundary

Owned loopback HTTP fixtures cover real media-404, provider-500, callback-503
and deadline paths, unchanged error/uncertain outcomes, archived failure replay
refusal across worker restart, and diagnostic I/O/invalid-return faults.
Filesystem fixtures cover schema/privacy bounds, malformed records, FIFOs,
symlinks, descriptor/path replacement, first-note preservation, kernel lock
contention and two independent writer processes under a one-note capacity.
These are synthetic checks, not independent human review or proof that a real
native caption failure is fixed. No service update follows from source alone.
