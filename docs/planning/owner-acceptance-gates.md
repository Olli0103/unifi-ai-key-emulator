# Native acceptance checklist

Use this checklist for your own installation after [source deployment](../deployment.md).
It does not authorize unattended camera tests or claim product-wide parity.
Retain exact source/model/controller versions and failures. Publish only sanitized
results, never addresses, credentials, recordings, transcripts or identity data.

## Preconditions

Choose the exact feature, camera scope, test action and expected native result.
Check queue/resource limits, required models, current state and rollback first.
Do not use a simulated event, a model response or a 2xx callback as native ground
truth. Avoid unrelated policy changes while testing one boundary.

## Descriptions and summaries

Create one explicitly selected event, request the intended analysis and read the
result from the original event/player in Protect. Refresh the page and verify
that a persisted result is still there. An on-demand result is not automatically
a saved event caption. Repeat only after reconciling the first attempt's outcome.

## Detection, zones and alarms

For a paired AI Port, verify the camera/stream and actual detector are enabled.
Use an agreed controlled action with an observed time. Check the class and zone
on the original camera, then separately verify the configured alarm. Count-only
health or a subscription message is not saved event or notification evidence.
Include a negative case and preserve misses; do not alter confidence values to
manufacture an accepted event.

## Recognition and speech

Use consented inputs and the correct model-space binding. Check the saved result
in Protect, not just a local model's response. Repeat-visit grouping, named
identity accuracy, deletion and scope restriction each need distinct acceptance.
Speech callback success does not prove that a transcript is visible or correct.

## Reverification

Confirm the source and configured confidence window, then verify a native event's
before/after verdict. An uncertain or rejected verdict must leave the event
unchanged. A native-camera success is not proof of the AI Port-sourced path.

## Search and deep processing

Check the actual database profile, model identity, dimensions and controller
migration before testing. Use a known positive and negative query against the
intended original event/session. A row count is not retrieval relevance. New
Deep processing, old-event retrieval and index migration have separate gates.

## Recovery and installation

Verify the same identity, credential state, pairings and results after a planned
restart. Separately test host boot and restoration without duplicate identities.
A source fixture or image-only upgrade is not a full controller/host restore.
A new independent tester's installation is distinct from the maintainer's setup.

## Evidence and release boundaries

Record whether each result is implemented, fixture-tested, native-verified or
still `needs_evidence`. Use the [compatibility matrix](../evidence/compatibility-matrix.md)
and [dated evidence index](../evidence/README.md) as references, not as authority to
repeat old experiments. Public artifacts and signing retain the separate
[release-readiness gate](../legal/release-readiness.md).
