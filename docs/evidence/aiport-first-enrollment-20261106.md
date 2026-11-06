# AI Port first enrollment — 6 November 2026

## Failure and correction

Two earlier isolated native attempts on Protect 7.3.74 failed: management arrived
before credential rotation, returned 503, and Protect nevertheless displayed
adoption. A subsequent tokenless process restart stayed locally unadopted and
passive. The tests were removed, not relabeled successful.

The correction adds explicit `factory_enrollment_until`, default absent. It is
bounded to ten minutes, checks both clocks and actual controller peer, and grants
factory credentials only to first management, not login. Existing credential,
pending state or adopted state prevents fallback. Controller pin, token/payload
validation, atomic state persistence and token-bearing reconnect remain required.
No camera/stream policy is enabled by this field.

## Source and regression evidence

The actual captured installed-module baseline reproduces HTTP 503 for the new
first-enrollment regression. The corrected focused group passes 167 tests:
35 adoption/credential cases and 132 candidate/credential-boundary cases. Tests
cover opt-in/off, time bounds, expiry, monotonic expiry, wrong peer/password,
rotated/pending/adopted state, login refusal, token reconnect and ordinary rotated
credential management. Ruff and diff checks pass. This is same-agent work, not an
independent security review.

## Native observation

One fresh locally administered identity, no cameras/models, separate private
state, distinct owner-authorized temporary address, non-restarting container,
read-only root, dropped capabilities, ten-minute execution limit. Before startup,
the wrapper verified all 179 module hashes. The installed base image was retained;
one frozen corrected `aiport_candidate.py` was mounted read-only. This is an
exact-module candidate experiment, **not a rebuilt-image qualification**.

Normal native UI adoption produced:

- Management entered with no credential and zero rotations; returned 200.
- Pinned token reconnect confirmed local adoption; the saved token was removed.
- Controller credential rotation succeeded, native device CONNECTED/adopted,
  paired-camera set empty; zero stream starts.
- Removed the temporary field, stopped/started the same container. First new
  WebSocket connection succeeded; local adoption loaded, hello and parameter
  agreement completed, native device again CONNECTED/adopted, no management
  request and zero stream starts.

The test container and native registration were removed. Fresh **uncached**
controller reads verified absence; a cached GET after DELETE had still shown the
old object and was not treated as removal proof. Original eleven camera-setting
projections, one Key/four Port identities/states and nine pairings matched.
All 80 pre-existing container identities, images, PIDs, restart counts and statuses
matched exactly; temporary network binding absent. Private receipts and failed
attempts are retained outside the repository.

## Limits

No existing deployment was upgraded or paired/unpaired, no recording or model
request was introduced, and no private identity/media/credential is published.
This removes the observed fresh-enrollment blocker; it does not complete the
compound Mac/NAS automatic-reconciler acceptance in issue #45 or release gates.
