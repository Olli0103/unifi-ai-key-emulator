# Camera threshold confirmation authority (3 November 2026)

This is a source/fictional-fixture correction within administration issues
#13/#17. It is not native camera tuning or completion of those broader issues.

## Reproduced on parent

Parent `57f0a54ad57419b2b188ebeb84d296a83b9ba6ad` passes signature and
configuration-revision checks but keeps no consumed confirmation authority.
Four actual loopback HTTP regressions fail on that unchanged implementation:

- After a successful threshold save and exact-byte Undo, the used confirmation
  saves again instead of refusing. The hash of restored configuration is not
  proof of renewed operator approval.
- A sibling preview from before that save can also replay after Undo.
- Validation can outlive the ten-minute review lifetime. The wall-clock check
  happens before awaited inventory/configuration work and is not checked again.
- Two requests using the same confirmation both enter the store writer. OCC
  eventually rejects one, but it has not enforced one-use admission.

These are fictional saved profiles, not observed production replays. The expiry
case advances an injected monotonic clock during the real validation path;
no ten-minute sleep, clock change or live service operation is performed.

## Correction

Keep existing HMAC-bound session, profile, camera identities/names, values and
old/resulting configuration digests. Each preview adds a random draft nonce
and server-private, monotonic ten-minute grant. At most 32 unexpired grants
are held; a full pool refuses a new review rather than discarding a live one.
No dependency, database, configuration format or persistent token is added.

Apply checks authority before validation and again after its awaits. The final
session check, profile-scoped invalidation and shared file-operation admission
have no intervening await. A consumed confirmation, including a no-op or an
uncertain write, cannot be replayed. Same-profile sibling reviews are invalidated
at admission; Undo also invalidates pending reviews. A fresh review after Undo
still works. A server restart loses both the per-control signing key and pending
grants; an old browser form has no authority on the new instance.

The existing file writer, history, exact Undo, shared mutation limit,
CSRF/Origin/Host/session rules and interruption drain remain unchanged. The
plate-permission subclass keeps its own overridden preview/apply protocol;
its behavior is not represented as repaired by this correction. This is not
a redesign of Undo authorization, host-owner protection or general formal
verification of every administration form.

## Qualification and limits

- Four original regressions reproduced before the production edit.
- 80 focused threshold/store/plate-subclass and HTTP checks pass, including
  fresh review, no-op/uncertain consumption, bounded unique grants, exact
  monotonic expiry, late session revocation and overlapping requests.
- The existing fictional desktop/mobile browser runner now saves with
  Space/Tab/Enter, undoes, then submits the old form and requires HTTP 409 with
  unchanged configuration and zero threshold-provider requests. Both scenarios
  passed; the four threshold confirmation/saved screenshots were decoded and
  visually inspected. This is same-agent fixture evidence, not independent
  human accessibility acceptance.
- 437 documentation/compatibility checks and whole-tree Ruff passed.
- Complete platform/CI results are bound to the final commit in the PR. No
  independent human security review, production deployment or native
  controller/model/recording/zone/quality acceptance is claimed.

An initial browser command used the ordinary test environment, which lacks the
optional Playwright package. It failed before browser/fixture creation. The
existing separate browser environment is used for the actual browser check;
no production dependency or environment was changed to make that check run.
The first complete-suite attempt selected Apple's system `openssl`, whose
`passwd` lacks `-6`: seven unrelated existing credential/adoption tests failed
and 697 passed before the run was explicitly interrupted. Both initial adoption
failures reproduced on unchanged parent source. With the already-installed
Homebrew OpenSSL 4.0.3 first in the test PATH, all 20 credential/adoption cases
pass. The interrupted run is retained, not reported as a full-suite pass.
An initial documentation-test command named nonexistent files and collected no
tests; the corrected existing modules supply the 437-check result above.

No real config, camera, provider, credential, model, index, service, image,
scheduler or Protect setting is changed by this source qualification. Existing
runtime deployments keep their separately recorded revisions until an explicit
qualified rollout; broader issues remain OPEN.
