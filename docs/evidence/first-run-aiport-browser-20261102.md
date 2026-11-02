# Fresh AI Port browser preparation — 2 November 2026

Source-only preparation via the existing private provisioning library and shared
administrator authentication/session/CSRF/retained-operation lifecycle. Current
running Admin/Key/Ports/model services are not updated by this source work.
[Usage and boundaries](../first-run-aiport.md).

A server-selected private addressed Protect plan/slot, private public-certificate
file and fresh private target are required. Only the controller address, public
independent pin, current revision and two local-only/trust acknowledgements are
accepted from the browser. No new native identity is advertised, camera paired,
provider/model/audio policy created or controller/model request submitted.
Unsupported ONVIF, stale/malformed/unauthorized inputs and existing/partial
state refuse. Create-only admission never reads/verifies another existing
identity as fresh success. New directory privacy is checked before key creation.

Two actual parent regressions demonstrated the missing create-only mode and
NAS-style inherited mode 0777 reaching the identity factory. Existing CLI verify
behavior remains supported. A fixture initially simulated inheritance even for
an already-existing directory's mkdir call inside the TLS helper; that inaccurate
fixture was corrected to apply only to actual directory creation. A further
ignored-chmod fixture verifies refusal before any private material when the
requested mode does not take effect.

Actual loopback HTTP fixtures cover stale plan/certificate/directory revisions,
private input/refusal boundaries, two independent authorities competing for one
root, proper Port audit labels, no key material in DOM/logs, file drift, retained
post-write failure and cancelled-waiter/shutdown drainage. The ordinary Key
wizard is regression-checked without changing its source. A separate actual CLI
factory check observes the Port-only mode before any listener or profile writes.

Synthetic sandboxed Chrome exercises desktop/mobile/Tab-Space-Enter forms,
wrong pin/stale plan refusals and a successful passive profile, CSRF POST logout,
local-only page traffic, source module hashes and owned-resource collection.
New form input/button/consent-label targets are measured at least 44 CSS pixels;
this is not a human screen-reader or whole-site accessibility PASS. Its fixture
banner is test-only. Some full-image tool previews omitted it, initially suspected
as a capture fault. Direct decoding of all original/final PNGs and a cropped
diagnostic disproved that hypothesis: the stored images already contained the
banner. No browser/product root cause is inferred. The runner now also checks
decoded PNG marker pixels, not just DOM visibility; prior captures are retained.

All profiles, credentials and images in the fixture are fictional. No real
controller, installed device, private camera media, model call, adoption, pairing,
service restart or permissions/settings change is used for this feature. New
local-file/browser checks are not a fresh independent native install or legal/
firmware/distribution clearance. Broader issues #13/#17/#25 remain OPEN.

## Local qualification scope

- 542 targeted Port/Key/CLI/documentation tests passed. Ruff and diff checks passed.
- Actual sandboxed Chrome desktop/mobile/keyboard scenarios passed; six saved
  views were inspected for layout/focus/overflow. Decoded PNG bytes confirm all
  fixture markers; tool-preview discrepancies are not product defects.
- A separate outside-repository source comparison verified the existing Key
  wizard byte-identical, four legacy CLI namespaces equal, four existing
  plan/trust/verify/CLI functions AST-identical and 129 other runtime modules
  byte-identical. New mode routes are only login/logout/health/index/create.
- Browser results bind 132 source-module hashes before/after. They exercise the
  actual HTTP app, not a newly installed native service. The separate CLI
  factory fixture does not itself prove a live CLI listener.

Full-suite/CI outcomes belong to the exact PR-head check receipts. Optional
model-backed platform skips are not model qualification. No Node package,
plugin, shell/deployment helper or runtime dependency changed.
