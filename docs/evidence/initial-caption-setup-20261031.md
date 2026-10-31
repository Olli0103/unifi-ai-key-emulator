# Initial automatic captions — 31 October 2026

Bounded work for #13/#17/#25; the broader issues remain OPEN. Existing
caption list additions deliberately required an existing explicit list. This
adds a separate initial-policy operation, not a broader generic patch or
conversion of existing/model-wide/one-use policies. Existing maintained
configuration transactions, pinned inventory, forms/authentication, grants,
shared mutation admission and owned/drained file work are reused; no dependency,
new listener or custom inference/secret transport is added.

## Authority and consequences

The host explicitly opts in with `--allow-initial-caption-setup`, off by
default. All existing inventory arguments are required; a frozen host binding
contains the exact private IPv4 controller and three fixed absolute private
file references. Same-controller matching is checked against the saved Key
configuration. Paths must already be mounted identically in Admin and Key.
The browser cannot submit paths, controller, keys, trust, model or cost mode.
The new store reads no referenced key/certificate, copies no credential and
attests no runtime mount or credential validity. Existing bounded pinned
inventory GETs remain responsible for their own TLS/credential checks. This
is not complete controller credential onboarding.

No existing continuous policy or one-use scopes, a configured caption model,
existing enabled callbacks/MP4 export permission and an evidenced supported
Protect version are required. Unmet prerequisites are not changed by setup.
Only fresh connected smart-event candidates with unambiguous non-identifier
names are offered. Duplicate native IDs/MACs refuse inventory. Choices bind
session/revision/id/MAC/name/model using structured HMAC; initial preview and
confirmation each re-read inventory and match the saved Protect version.
Metadata eligibility is not event delivery, physical camera, quality or native
caption persistence proof.

One to sixteen explicit cameras and only their model families are saved.
Later same-family cameras are not implicitly authorized. The existing global
12-reservation rolling hour limit applies across all cameras; setup exposes
no unmetered bypass. It is not a bill/price/per-camera cap. Consumed usage is
not cleared/refunded, and failed/uncertain work may already consume it.
Provider/model/destination and possible image/video transfer are disclosed
before separately unchecked processing/cost and save confirmations. Other
feature permissions/costs remain separate.

## Transaction and failure boundary

Only raw `worker.continuous` is added. All other raw fields/omitted defaults
are preserved. The existing lock/OCC/old-byte history/identity/search-profile/
credential-route guards apply. Preview is read-only. Expiring ten-minute
monotonic one-use grants are bounded to32 without evicting valid grants and
bind the exact resulting revision and unchanged host binding/loader. Current
session, expiry and grant identity are rechecked after awaited work.

Lost post-publication acknowledgement is uncertain, not an automatic retry,
rollback or claim nothing changed. Admitted writes are shielded/drained.
The page never starts/stops/restarts a processor, invokes a model/media test,
changes native recording/microphone/controller permissions, replaces a
provider key, edits a budget journal or rebuilds an index. A separately
controlled Key restart and natural native readback are still required.

## Evidence limits

All inputs are fictional; source-derived tests by the same agent are not
independent human review. Desktop/mobile keyboard fixtures use the actual
HTTP routes and real sandboxed Chrome, refusing foreign requests. Their
owned servers/profiles are collected. Qualification receipts bind the exact
frozen source; full suite/CI and installation are distinct gates. No real
household camera or existing production host option is enabled by this work.
