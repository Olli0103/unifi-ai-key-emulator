# Native routing and a missed first detection — 30 October 2026

## Reviewed source and actual runtime

[#207](https://github.com/Olli0103/unifi-ai-key-emulator/pull/207), source
`daabf77ce264673d0fd52022039e2f9ab78548ab`, adds a bounded in-memory ledger
of completed Port snapshot sends. Its qualification passed 156 focused tests,
3,563 Mac tests (three skips), 3,565 Linux tests (one skip) and five CI jobs.
Two canonical tuples were independently hashed with Python and Node; a separate
same-agent harness passed 20 checks. This is not independent human review.

Only the existing three-camera Port was upgraded in this transaction. Its
111 installed Python modules and license matched the reviewed package before
and after activation. The other three Ports, Key, PostgreSQL and administration
containers were unchanged. A private, network-disabled read-only candidate
validated real state/model constructors without starting a service or inference.

The guarded replacement drained only its owned producers, cold-archived its
34 state files on the NAS, read and verified every archived file and gzip CRC,
then changed only the selected Compose image. Original immutable configuration,
trust, adoption, five native identities and nine pairings were rechecked.
The old image remains available with current-state fallback. The existing
provider key matched the cold archive byte-for-byte. An acknowledged
controller-origin management update changed its private record: effective
management-password equality is **unknown**, not claimed unchanged.

An initial packaging attempt lacked a build dependency and was corrected with
an isolated build. Initial remote-root permission verification failed before
private data was written; explicit permission setting passed before staging.
The processor replacement itself completed once, without rollback or state
restore. No other processor replacement is implied by staged inventory files.

## Send → exact saved event → native player

On Protect 7.3.74, two distinct completed-send fingerprints matched two exact
native snapshot tuples on two original cameras: a G3 Instant and a G5 Flex.
The comparison used configured camera MAC, kind, tracker and best-wall time,
with the documented canonical SHA-256 protocol; no guessed identifier pattern
or mere event count was used as issuer evidence.

Both native grid objects had the expected original camera label and decoded
thumbnail. Opening each in Protect's own player decoded muted video, and the
selected playback time lay inside that exact persisted event's interval.
The G3 event and snapshot tuple remained readable on exact GETs 62 seconds
apart, with `ramState: done` and a stored description. A later exact G5 read
retained its tuple and description but had **`ramState: failed`**. This is not
successful Key processing or end-to-end parity for that event.

No tuple mapped to two different events in this bounded matched sample. This
does not establish global absence of duplicates, a negative cross-camera
control, sustained recording, visual class correctness or inference quality.
The G3 person-labelled thumbnail was not confirmed as a real person. These
were naturally occurring observations, not the operator's subsequent visit.

The fingerprints are private, linkable metadata, not anonymization or
authentication. They expire after 900 seconds, hold at most 64 entries and
disappear on restart. Saved exact-event readbacks, not continued ledger
presence, establish the limited persistence observation above.

## Operator-reported visit exposed a false-negative path

The operator reported walking through two different rooms from those initially
requested. The actual visit was evaluated, not relabelled as the requested
two-camera trial. One camera is a G6 with its own native detection; the other
is a G3 paired to the updated Port. Settings, zones and alarms were not changed.

Within the reported visit's minute the G6 saved Person events. The G3 saved
Motion but no Person event. Its content-free Port counters show:

- active decoding and an enabled Person policy;
- 20 motion-threshold frames, one Motion start and stop;
- 11 provider-gate-threshold frames and one admitted analysis request;
- one valid Person reply in the existing near-threshold band, rejected below
  the configured detector threshold; no accepted object or track;
- no API failure or zone rejection in that path.

This identifies the missed path without attributing it to routing or relaxing
the threshold. The first weak reply cancelled the pending confirmation pair,
leaving no chance to obtain two subsequent valid observations during that
motion episode. It does not prove that another view would have been accepted.
The visit was not a timed outside-zone negative control; no zone acceptance or
successful two-Port-camera human trial is claimed.

## Bounded recovery and remaining acceptance

The recovery keeps the existing acceptance threshold and two-observation
tracker. A first near-threshold Person, Vehicle or Animal can reserve at most
two further samples. The doubtful sample itself never reaches tracking. Two
new independently accepted observations are still needed. A second weak,
empty or invalid reply ends the episode. A weak ordinary confirmation cannot
extend its pair. Package and sub-0.5 replies do not start recovery.

This permits at most three rather than two requests for that episode. The same
optional hourly cap, failure backoff, approved provider and fair per-camera
scheduler still apply. No provider/model/budget or camera-scope change is made.
The counter `near_threshold_retries` records the decision without retaining
scores, replies or images. Synthetic old-source failures and positive/negative
controls qualify the code path; they do not retroactively repair this visit.

At this evidence checkpoint the recovery is **not deployed**. Native recall,
calibration and a post-fix visit remain `needs_evidence`, as do negative
cross-camera/zone controls and broader Key/Port parity. Existing #6, #14, #45,
#74 and #79 are not completed by this record. ONVIF #28 stays open for missing
hardware. No standalone model probe, enrollment, new privacy scope, alarm
action or public binary/image release occurred. Private native playback was
viewed; no camera media, descriptions, raw identities or linkable fingerprints
are included in this public record.
