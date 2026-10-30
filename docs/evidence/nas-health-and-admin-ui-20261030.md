# NAS health reader correction and Admin UI activation — 30 October 2026

The [aggregate record](nas-health-and-admin-ui-20261030.json) contains source
identifiers, sizes, counts and booleans only. No camera/event/processor IDs,
linkable event fingerprints, descriptions, media or credentials are included.

## Reproduced NAS reconciler health failure (#45)

The in-container `_HEALTH_SCRIPT` in `aiport_nas_reconcile.py` read 4,097 bytes
and immediately parsed them as a complete JSON document. A read-only execution
of that exact script failed with JSON decoding errors on **all four** existing
Ports. Separate bounded reads returned valid service JSON of these sizes:

| Existing slot | Response bytes |
| --- | ---: |
| 1 | 129,326 |
| 2 | 172,629 |
| 3 | 172,574 |
| 4 | 172,083 |

These are metadata-response sizes, not model requests, saved detections or
capacity/latency measurements. The reproduction introduced eight health GETs
(old probe plus full bounded read per Port); IDs/images/PIDs were unchanged.

The corrected reader uses the same **256-KiB** ceiling as the existing Admin
runtime reader, reads one byte beyond that ceiling and explicitly rejects an
oversized body before parsing. An early complete JSON object followed by a large
whitespace tail is therefore refused rather than silently accepted from its
prefix. The existing leaf pin, own-CA verification, fixed loopback HTTPS target,
HTTP-status/boolean checks, five-second socket timeout and twelve-second command
deadline remain unchanged. Output is still only service/adoption/control flags;
private telemetry is not returned. This is not a new universal Docker-exec
cancellation or host-recovery guarantee.

### Verification

- The new actual-script regression set fails **six** cases on the parent, with
  eleven passing controls: four legitimate bodies larger than 4 KiB and two
  oversized valid-JSON-prefix cases expose the old behavior.
- The corrected focused probe/Compose/planner/reconciler group passes **127
  tests**. Required flag/type, HTTP, pin and TLS refusals remain covered.
- A separate same-agent harness exercises **20 real loopback-TLS cases** in an
  owned netless NAS container: byte-boundary/Unicode bodies, oversized prefixes,
  wrong status, invalid flags/JSON, wrong pin/CA, unadopted/disconnected flags and
  a stalled-body socket timeout. Only fictional data and newly generated
  synthetic certificates are used. All cases passed; owned threads/container
  were collected, no ports published, no models/providers or live state mounted.
  This is not independent human review.
- One corrected-script GET per existing Port returns the real adopted/connected
  flags successfully, with all four IDs/images/PIDs unchanged. No reconciler
  apply, camera pairing, slot creation, image change or model request was made.

Fixing health parsing does **not** complete #45: a fresh matching inventory,
capacity, exact slot/manifest/image/network guards and Mac/NAS creation/removal
acceptance still apply. The running Ports have different reviewed image
revisions; this change does not bypass manifest checks or downgrade slot 1.

## Later activation of the existing #205/#206 Admin interface

After the [earlier search/alarm checkpoint](native-search-alarm-20261030.md),
the separate Admin was updated to reviewed runtime source
`f17c4d1d73e0b9fdc617bb776bc9e2efacea6e21` (through #209), including #205/#206.
Its image is
`sha256:a1a28d5559cb0bf44f19ed3a4d300c689e9ea4ac287332eb3f387851f7820efd`.
This deployment is distinct from the new health-reader source correction above.

The candidate matched all **111 modules and license**, then passed netless
read/refusal/preview checks against five real profiles: 25 registry rows, six
role-model previews, shared 60-admission/429 boundary and no settings/provider/
model applies. The current IPC catalog was separately read with the new code,
with all mounts read-only and no host plans or Docker socket: two legacy plans,
two completed requests, no submission.

The first image-only attempt rolled back when an outdated test expected zero
camera-selection controls despite the already configured inventory reader.
Its immediate rollback receipt still shows the old Admin's health starting;
a later separate check confirmed healthy, zero automatic restarts and timer
active. Six core containers and five profiles stayed unchanged. The failure
was not attributed to product code or hidden as success.

A fresh baseline confirmed **27** existing controls, twelve permission panels,
five TLS-verified runtime readers, nine streams and eleven inventory rows/nine
saved bindings. A new transaction, fresh NAS backup and strict preservation of
that actual baseline passed using the **same** candidate source/image. The old
attempt was not overwritten or replayed and no model qualification was repeated.

The actual authenticated maintenance page is readable; anonymous access is
refused. Its two completed legacy plans have no execution forms. No maintenance
request was submitted and no old backup/restore job replayed. Same-origin
Referrer-Policy/no-store and the existing reader/control counts were verified.
The app's host-worker liveness is not asserted; old manual launchers were not
replayed or converted into a daemon/schedule.

A separate same-agent terminal read checked all 111 running module bytes/license,
six NAS archive members/digest, unchanged persistent Admin authority/TLS bytes,
five profile hashes and Key/PostgreSQL/four-Port IDs/images/PIDs. Compose changed
only the Admin image; the certificate configuration changed only its reviewed
image/source pin. Its timer is active/enabled, first check not-due (89 days,
zero issuer/reload calls). The old image is retained; no old profile state was
restored. Monitors were fresh UP. At 14:13:25 UTC, Protect still retained the
original five adopted/connected processor identities and nine pairings.

This is actual HTTPS/API/profile qualification plus the earlier synthetic
Chrome tests, **not** a new visual/keyboard acceptance against the real Admin.
Full UI/native PTZ/recording/pairing, controlled detection/zone countertests,
retrieval quality and overall parity remain open in their own issues. No new
account, permission, camera scope or public image release was introduced.
