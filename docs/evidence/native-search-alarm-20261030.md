# Native search and existing-alarm observations — 30 October 2026

This is a bounded observation on Protect 7.3.74, not hardware parity, a retrieval
benchmark, a new test-alarm configuration or notification-delivery acceptance.
The [aggregate record](native-search-alarm-20261030.json) contains no native IDs,
linkable event fingerprints, private descriptions, media or credentials.

## Runtime boundary

PR #209 was merged and activated privately on the NAS for **Key only**, with its
coupled PostgreSQL container recreated against the same database image/volume.
The installed source is `f17c4d1d73e0b9fdc617bb776bc9e2efacea6e21`; the private
image digest is `sha256:657d92d32d7dc97bc9a9882a1a4d71dbd9a671eceb0a9fe8685553c658bb94ec`.
All 111 installed modules and the license matched the frozen source/package.
Four Ports, the separate administration process and their configurations were
unchanged by that transaction. Port slot 1 already ran the separately qualified
PR #208 detector correction; the other three Ports retained source #193.

Qualification: 3,591 Linux tests passed, one skipped; 3,587 Mac tests passed,
three skipped, followed by all 13 final window/decoder tests separately passing
on Mac. The full Mac run preceded two additional decoder tests; Linux CI included
the final test file. Five CI jobs passed. A separate same-agent harness passed
96 cases/748 assertions; it is not independent human review.

The controlled update completed once without rollback or old-state restoration.
A separate read-only terminal execution checked running bytes, all five profile
and immutable Key-core hashes, unaffected container identities/images/PIDs,
effective Key username/secret preservation, certificate renewal and fresh healthy
monitor observations. Complete cold Key, PostgreSQL and secret archives were
read/hash/CRC-verified on the actual NAS. The logical dump's table of contents was
validated with the same PostgreSQL image; this is not a new restore drill.
The Key archive includes historical verification fixtures: its 125,103 regular
files are **not** a count of user objects.

After the update and again after the search observation, the original one-Key,
four-Port identities and nine pairings matched; all five processors were adopted
and connected. No public image release, new camera scope, provider change,
standalone model probe, old-job replay or complete-long-event quality claim.
The separate live Admin remains source #201: merging #205/#206 did not activate
their new maintenance/browser interface there.

## New basic-search observation after restart

This is a new observation, not a reset or extension of the terminal three-GET
[earlier search observation](native-search-filters-20261030.md). It admitted two
native UI submissions with a camera filter selected through Protect. The existing
private local CLIP basic-search route was used; Deep remained disabled.

The planned threshold was 35, but the native UI submitted its default **50**.
This discrepancy was retained, not silently corrected or described as a
same-threshold rerun of the earlier observation. The operator did not lower it.

| Query | Observed native behavior |
| --- | --- |
| `person at a desk` | HTTP 200 at 50; UI reported 83 results. All 32 nonempty rendered card labels named the selected camera. This does not inspect all 83 records or visually label their relevance. |
| `a purple elephant surfing on the moon` | UI GET at 50 returned HTTP 200, then Protect automatically issued another GET at **0**, changed its URL threshold and displayed `99+ Results` (100 card nodes). This is not negative-query rejection in the UI. |

One additional, explicitly bounded read of the negative query at fixed 50
returned HTTP 200 and **zero detections**, in 74 ms browser request-to-text/JSON
elapsed. It had a 12-second client deadline, redirect refusal and 1-MiB response
text bound. That isolates the threshold boundary; it does not prove every native
UI internal reason or justify changing the encoder/vector contract to conceal
the UI broadening. Across this observation there were **four search GETs**:
two submissions, one automatic broadening, one fixed-threshold boundary read.

The active Key's content-free search counters changed from zero queries/failures
to two queries/zero failures; image-query count remained zero. Four HTTP
GETs must not be relabeled four encoder invocations: underlying encoder
counts, caching and charges were not independently measured. The selected camera
parameter was present on each captured native request. Camera/date-filter
completeness and human relevance/recall remain unaccepted. A previously matched
G5 snapshot's object ID was not found among the positive result card IDs; no
alternative query or threshold was tried to turn that into a pass.

No model, index, camera, alarm or zone configuration was changed. Owned raw search
result-ID references were released after aggregation, not controller/browser-cache
or backup erasure. This does not repair the frozen 7/12 public detector pilot.

## Separate native image-upload path check

After the preceding observation was terminal, returning to Find Anything
restored its last text query and caused one additional automatic request: the
Key query counter rose from two to three. This re-entry was not a new UI search
submission or a reset of the terminal four-GET observation. The restored query
was cleared before the image test.

One new 128×128, four-color synthetic PNG (414 bytes, no camera/person content)
was then uploaded through Protect's actual Search by Image control. Two tool-side
setup refusals (required inbound directory and a stale ref) preceded the actual
upload and did not become controller uploads or encoder failures. The browser's
required inbound staging copy was byte-verified against the external fixture.

The native `POST /detection-search/by-image/upload` and resulting
`GET /detection-search/by-image/{private-id}` both returned HTTP 200. The active
Key's image-query counter changed **0→1**, with no image failure category;
text-query count remained three. The UI rendered `99+ Results`/100 card nodes,
with all 32 nonempty visible card labels naming the selected camera. This proves
the exercised upload→existing Key image encoder→native results path, **not**
semantic relevance, a positive/negative image benchmark or every result's camera
metadata. A synthetic color pattern is not human ground truth for these results.

Exactly one native image upload was observed. No camera image was exported or
uploaded, no new model/provider/camera scope was introduced, and no standalone
encoder endpoint was called. The UI query reference was cleared afterward;
controller-side deletion/retention of the uploaded synthetic fixture was **not**
verified. Clearing a query is not file deletion. No private upload identifier
or result IDs appear in this record.

## Existing Person alarm: stored execution association

The existing Alarm Manager Person rule was inspected **read-only**. Its history
API returned a capped 500-record sample; this is not a complete alarm inventory
or proof of notification delivery. No alarm was created, edited, enabled, paused,
duplicated or removed, and the agent sent no notification or consequential action.

Four previously wire-sent Port fingerprints had already been matched to saved
native event/snapshot tuples. They describe **three** native events: one event
combines animal and person snapshots. They must not be counted as four separate
events or used as an animal-only negative control for a Person alarm.

The initial join required the complete current smart-type set to equal the
alarm's stored source-event set. It matched two G3 event tuples but missed the G5
tuple. A subsequent exact read showed that the current G5 event contains
`face, person`, while the historical alarm source contains only `person`.
The initial miss was retained. A derived join instead required exact camera,
start timestamp and event type plus presence of the original Port class in both
records. This accounts for the differing class sets without claiming that
native event-ID equality was proved.

Under that rule, each of the three native event tuples (two G3 Instant, one G5
Flex) matched **one distinct stored Person-alarm execution**. Each execution had
two matched-event entries, not two executions. This is stronger than a total-hit
counter, but remains tuple association, not an alarm-ID/native-event-ID bridge,
an exactly-once guarantee outside this sample, a controlled nonmatch test,
query-alarm acceptance or a phone/email delivery receipt. Existing original-camera
player verification is recorded separately with the Port routing evidence.

## Remaining acceptance

Issues #6/#14/#21/#26/#79 remain open. Controlled Flur recovery confirmation,
outside-zone/nonmatch controls, query-alarm outage/recovery, image-search quality,
representative relevance and comparative quality are not inferred from these
observations. No capability flag or compatibility status was broadened.
