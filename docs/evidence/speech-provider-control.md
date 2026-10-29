# Speech provider control — 29 October 2026

Source/UI progress for #8, #13 and #17, not completion of their broader scopes.

An already configured speech role can now change its own provider, endpoint,
model, optional language, local-network permission and write-only credential
without JSON edits. Existing per-camera audio opt-ins and clip policy are
preserved, not widened. OpenAI's UI path requires a fresh audio-sharing choice.
No provider request or connection test is made during preview/save.

The configuration operation uses optimistic concurrency, existing validation,
private atomic replacement, immutable history and exact one-step rollback.
A key is retained only for the same speech provider and endpoint, never copied
from vision. Changed destinations lose the old reference. The rollback checks
both prior vision and speech references. An uncertain replacement retains the
new speech file: a later archived revision may still need it for rollback,
even if the current config no longer references it. Only a proven pre-apply
or revision-conflict rejection removes an unused new key. See the
[provider-key recovery evidence](provider-key-save-recovery.md).

Synthetic tests cover endpoint/key changes, exact undo, preservation of camera
policy and other roles, absent camera opt-in, malformed/unsupported settings,
CSRF/origin/stale revisions, explicit cloud-audio permission, private files,
secret-free responses and simulated errors before/after replacement. They do
not process real audio, contact a provider or prove a live browser deployment.
The browser tool refused navigation to the disposable loopback fixture by
policy; no bypass was attempted. HTTP/HTML checks are not presented as visual
desktop/mobile acceptance. The fixture server was stopped afterward.

Missing: camera-permission management, embedding/model migration UI, full
provider connection testing, arbitrary-host DNS policy, LAN HTTPS administration,
and the remaining native/quality acceptance in the parent issues. No running
processor or controller is reconfigured by this source change.
