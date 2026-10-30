# Privacy/data lifecycle foundation (#5) — 30 October 2026

This is an acceptance/evidence change, **not a new retention service or live
deletion operation**. Reuse existing tempfile cleanup, face-store operations,
configuration history/Undo, worker rollover, archive safety, bounded backup
prune and existing per-camera gates. No custom encryption, filesystem sweeper,
remote-erasure facade or scheduler was added.

## Original criteria

| Criterion | Evidence |
| --- | --- |
| Data-flow/retention/default table, including backups, visible in the site | Existing fourteen-category authenticated table and native profile reads. Expanded [copy-by-copy contract](../data-retention.md) identifies each deletion operation, trigger, surviving copy and unavailable/blocked action. Platform/private-file boundary is explicit, not mislabeled encryption. |
| Opt-out, expiry/deletion, access and rotation without broken adoption | Existing speech/face/Port-face/plate/sound camera/type refusal, saved restriction/Undo non-regrant, one-hour/day/week journal rollover and 24-hour budget windows, replay-guard expiry refusal without controller evidence, manual verified-backup prune/dry-run/unsafe-set refusal, private-file/auth/session/audit checks. Ten new cross-component checks below connect cleanup, replacement/Undo and saved-to-runtime behavior. No new automatic TTL policy is invented. |
| Public diagnostics/demo do not expose private media/templates/credentials/identifiers | Existing allowlisted device/Port diagnostics, fixed error classes, private authenticated retention counts and audits, no-state/no-network synthetic demo, role/credential canaries and no form echo. Authenticated native protocol media callbacks and the signed-in private camera-name UI are not mislabeled public export. |

## New execution evidence

`tests/test_privacy_lifecycle.py` uses generated state only:

- Six cases run actual owned OS processes with a synthetic decoder executable
  through the real worker's audio/video temporary-file path. Success, decoder
  failure and cancellation remove the directory; cancellation kills and reaps
  the process. The temporary directory is private while active. This does not
  claim an actual FFmpeg media conversion or power-loss cleanup.
- Actual provider-key reference replacement and exact provider Undo, fresh
  bounded secret loading and adapter request construction retain existing
  synthetic adoption state, identity/certificates and management/database
  credential files. Old key and referenced history remain available; no remote
  credential revocation or obsolete-key deletion is inferred.
- A downstream management-rotation refusal leaves the old persisted credential
  and adoption intact. Success is acknowledged only after the handler succeeds;
  a recreated device accepts the new password, rejects the old one and keeps
  adoption/identity/certificates. Persisted state contains the hash, not the new
  password. The downstream handler is an owned fixture, not a real database or
  controller rotation.
- Saved audio removal does **not** revoke the old process. A newly constructed
  device no longer advertises speech; its worker refuses the task before creating
  a network session/worker tasks. Identity files remain unchanged. Restart/drain
  is an explicit operator boundary, not implied live revocation.
- Removing one fictional named identity survives fresh-store construction;
  another identity still matches. Purge leaves no live store; device identity is
  untouched. An independently retained backup still contains the removed entry
  and an isolated restore can match it again. Live deletion is not backup erasure.

Focused existing/new group: **426 passed**, 21 existing warnings. Additional
credential/secret/store/scan group after the tenth case: **113 passed**; these
groups overlap and are **not** summed as unique tests. Ruff and diff checks pass.
Initial fixture runs failed on a nonexistent adapter factory, omitted synthetic
device MAC and a tuple where byte images were required. Corrected fixture inputs
use the real API; no product behavior/assertion was weakened and no product
regression is claimed from those setup errors. Original local receipts retained.

Frozen full Mac/four required CI jobs and a separate same-agent execution gate
are recorded in the integration PR before merge. No independent human security
review is claimed. All runtime source modules are unchanged by this PR; no
native image replacement or model/credential/camera operation is needed.

## Provider facts and limits

The retention guide now records this client's OpenAI `store:false` foreground
Responses request, separate OpenAI transcription endpoint semantics, Anthropic
Messages and unspecified compatible/local-server policies. Official OpenAI and
Anthropic pages were actually fetched on 30 October 2026, not inferred from a
catalog/model name. Exceptions/account/model terms remain explicit. No actual
account/ZDR/region/contract or provider deletion was queried or changed.

This defines the current foundation's operations and complete copy boundaries;
it does not silently add an automatic face TTL, schedule backup expiry, delete
replay protection or promise a controller/provider erase API that does not
exist here. A requirement for unavailable/blocked deletion means **do not enable
the feature for that requirement**, not "privacy passed". Retention ages and
legal basis remain operator decisions. Feature-native deletion/recognition,
whole-installation restore/endurance and community acceptance stay in their
own issues; no private test subject or new real-media experiment was introduced.
