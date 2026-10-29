# Model and provider-undo acknowledgement uncertainty — 29 October 2026

The role-model and provider-rollback HTTP handlers could claim **“Nothing was
changed”** after their real storage operation had already committed and its
acknowledgement failed. The existing provider/speech save paths already
distinguish uncertainty; these two handlers did not.

Both handlers now distinguish proven revision/explicit unsafe-change refusals
from unclassified storage/IO errors. An uncertain result says that the model
may already be saved or the prior provider may already be restored, asks the
operator to reload/check the current settings, and forbids automatic retry or
undo. The allowlisted audit result is `uncertain`, not `rejected`/`refused`.
Known revision conflicts retain their refusal page and unchanged bytes.

This classification is conservative: an IO failure before a write can also be
uncertain to the caller. The page does not claim success, make an automatic
read-back equivalence decision, replay an action, remove keys, restore a
snapshot or overwrite a later save. Filesystem/transaction/identity/index,
session/CSRF/origin and credential rules are unchanged.

## Checks

- Eight baseline failures span Key/Port, model/undo and OS/store-domain errors.
  Each invokes the real storage operation, injects a failed acknowledgement,
  and verifies the persisted JSON **before** testing the erroneous old page.
  All eight fixed cases report uncertainty, retain provider-key bytes, call the
  operation once and emit only the allowlisted audit result.
- Four conflict controls already refuse without changing bytes and retain
  that behavior.
- Four pre-write IO-failure controls remain byte-identical and do not falsely
  claim either successful commit or proven non-commit.
- Two later-save controls establish that a newer Key/Port model survives an
  earlier request's uncertain acknowledgement without retry or automatic undo.
- Secret values and private exception details are absent from error pages and
  audit output.

The combined HTTP/store/role/undo/key-recovery/audit/HTTPS/session run passed
95 tests. A separate run passed all six added pre-write/later-save cases;
Ruff and diff checks passed. These are synthetic HTTP/filesystem tests with
no model calls or native configuration changes, not real browser/mobile,
external review or completion of the administration feature set.
