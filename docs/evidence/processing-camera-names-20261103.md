# Named processing cameras and reported zones (3 November 2026)

Source/UI work within #13/#17. This is a read-only operational feature, not
native event pairing, zone enforcement or recognition-quality acceptance.

## Missing surface reproduced

On parent `9d65da93f5a6afeb1e0385c186ad1e42296847af`, the processing page/API
publishes runtime ordinals only and drops already-normalized zone counts.
Two fictional HTTPS/HTTP feature tests fail there on the absent `camera_name`
and `policy_smart_zones` fields. This does not assert an observed production
mislabeling or new native protocol discovery.

## Implementation

Reuse the existing pinned health observer, `AdminAPI.runtime_view`, saved-file
reader, versioned processor/ordered-camera binding and shared inventory snapshot.
No new model, database, transport, credentials or configuration schema.

A name is joined by exact normalized saved MAC only when the processor's
reported identity/order digest agrees with the saved configuration and the
reported camera-row count agrees. An inventory sorted differently cannot
relabel the rows. Validated inventory must be no older than five minutes.
Duplicate MACs/IDs or invalid reports fail the optional name observation;
blank, identifier-like and duplicated case-insensitive names never become a
camera label. There is no MAC/ID fallback or guessed ordinal-to-inventory join.
Other valid names and runtime counters remain available when one name is
unusable. Names and fixed reasons are private authenticated output, never
HTML markup, native identifiers, logs or frontend storage.

The API adds `camera_name`, `camera_name_observation`, the fixed
`camera_name_scope` and separate `camera_inventory_fetched_at`. Schema version
remains additive `/1`. UI shows the inventory time separately from each health
sample. It also displays the already-reported smart/exclusion/secondary-lens
zone counts and enabled classes, preserving zero, false, empty and Unknown.
No geometry or source payload is returned. Configuring zones remains Protect's
job; a count is not native enforcement proof.

No optional inventory request is introduced when all runtime orders are
unverified. One existing inventory snapshot serves all eligible profiles;
existing two-second cache, validation, failure suppression and cancellation
rules remain. The full optional lookup, including waiting for the shared lock,
is bounded to twelve seconds. A failure yields unknown names, not zero counters
or a disabled processor. Authentication is checked again after the awaits.
The configured loader may do its existing read-only Protect inventory GET;
this is explicitly disclosed, not described as no controller request.

## Qualification boundary

Source tests cover reordered inventory, changed device/order, omitted declaration,
count mismatch, unreadable saved config, stale/unsupported/duplicate/invalid
inventory, optional source failure, missing/ambiguous/identifier names, escaped
markup, logout while loading, strict counters, raw MAC formatting and multiple
matching profiles sharing one observation. Real owned TLS peers are used;
models/controllers and household footage are not.

The fictional sandboxed Chrome desktop/mobile runner now checks named camera
rows, reported counts/classes, keyboard navigation and a mismatched-order
state with no name/ID/MAC leak. A stale inventory keeps attempts visible without
names. No setting forms, storage, provider/media/controller calls or fixture
configuration writes. Four decoded named/unknown screenshots were inspected;
this is same-agent inspection, not independent human accessibility acceptance.

The first multi-profile fixture omitted its second controller CA. The ordinary
saved-profile parser correctly refused it. The corrected fixture validates the
complete saved profile before testing name sharing; no guard was weakened and
that fixture failure is not counted as a product regression. An initial docs
edit used the workspace instead of the worktree and refused before writing;
a test selection then named a nonexistent module and collected no tests. The
corrected commands use explicit worktree/existing modules.

Final exact-source checks and CI are recorded in the PR. No new full Mac suite,
live deployment, runtime activation, native camera association, zone negative,
private/night quality, human review or full product completion is inferred.
Broader issues remain OPEN.
