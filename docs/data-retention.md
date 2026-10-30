# Data flow, retention and deletion

The signed-in control site's **Data flow and retention** page inventories
fourteen output/state categories, their other destinations, product defaults,
actual lifecycle mechanisms and remaining operator decisions. Defaults are not
the current activation state. An unavailable live count means **not measured or
not configured**, not zero records. The page is read-only and sends no model
request, changes no setting and deletes nothing.

This describes the existing implementation, not a new universal retention
policy. Configured model destinations are shown in their provider settings
before saving or explicitly testing a role. Other model/camera activation
controls remain limited as documented in [the control site](control-site.md).

## Copies are independent

- Downloaded MP4s and decoded frames/audio use private per-job temporary
  directories. Ordinary completion, exceptions and cancellation remove them;
  a hard crash can leave directories behind. The page counts leftovers without
  reading media. It does not sweep them or prove secure memory/disk erasure.
- Basic worker journals can contain captions and event identifiers. Terminal
  records roll over on startup/new admission, not a background deletion timer:
  one hour in continuous mode; completed local-index records after one minute;
  otherwise completed records after one day and failed records after seven.
  The archive omits caption/event details but preserves hashed replay markers
  indefinitely. `callback_uncertain` is not automatically archived or retried.
- Protect events, transcripts, names/face groups, plates, enhanced images and
  uploaded crops/query images have their own lifetime. Native search-by-image
  creates a controller-side temporary upload even if the input was already a
  native thumbnail. Its deletion/expiry was not verified by the bounded search
  test. Deleting a local identity or journal
  does not remove those copies. Controller acceptance is not deletion proof.
- Live databases, search-profile/migration state and logical index dumps are
  distinct copies. There is no application-wide TTL that proves their complete
  deletion when a Protect event disappears. Backup prune is manual/dry-run by
  default and preserves the newest verified backup; its defaults do not create
  a scheduler or expire full-state backups.
- Full-state backups may include secrets, identities, journals and crash media.
  Host snapshots, backup versions, replicas and system/container logs are outside
  this page's enumeration/deletion authority. Backup preflight checks integrity,
  identity and references, not encryption or remote expiry.

## Protection and recovery boundary

Identity, key and state files rely on private filesystem permissions and the
host/container access boundary. The application does **not** encrypt these
files at rest. An operator must protect the host, mounted volumes and backup
targets with suitable access and disk/backup encryption, and verify recovery.
Do not advertise an encrypted installation merely because a file is mode 0600.

Provider key replacement is role-scoped and write-only in the site. Old key
files can be needed by active or archived configurations; an uncertain save
can also retain a new file. A file counted as superseded is not proven safe to
delete. Reconcile both active and archived references, revoke the corresponding
provider credential when appropriate, and separately expire backups. Rotation
does not revoke remote credentials automatically or rotate device adoption
identity. Lost adoption credentials require explicit recovery, not silent
identity recreation.

Face `delete`/`purge` removes live local identity entries, not historical
controller names/crops, previous backups or provider inputs. No secure-overwrite
or cryptographic-erasure claim is made. Removing active databases, replay
markers or identity files can break recovery or permit duplicate work; this
page deliberately offers no blanket delete button.

## Remote processing and local decisions

An explicit private IP or local proxy does not prove where processing ultimately
occurs or how inputs are retained. A provider API storage flag does not establish
all account/service retention terms. Check the exact provider, account settings,
subprocessors and current terms before enabling remote image/audio/text roles;
no blanket provider privacy guarantee is recorded here. Audio, face and plate
camera scopes remain explicit opt-ins, not enabled by visiting the page.

The operator must decide permitted locations/cameras, notice/consent or other
applicable basis, access, retention/deletion of originals and derived data,
backup expiry and response to requests. The product makes no claim that one
default satisfies every location. This is the policy/mechanism boundary, not a
statement that an installation, controller or provider has passed erasure or
legal-compliance qualification.

## Opt-out is not retrospective deletion

Saved scope controls are deliberately labeled **saved settings only**. Remove
the selected permission, review the resulting revision and restart that
processor using the normal bounded drain procedure. An already running process
continues using its old configuration; saving is not live revocation. Work
already accepted must settle, including uncertain provider/callback outcomes.
The reconstructed worker/device then refuses absent/unlisted speech and face
scope before media/model work. Port face/audio/sound additionally retain their
existing runtime per-camera/Protect feature gates; plate OCR is limited to its
explicit plate-camera set. Sound depends on audio: remove Sound first, rather
than silently changing a second permission. A provider-only Undo cannot restore
an earlier removed camera permission.

For features without a dedicated initial configuration/control form, use the
validated configuration workflow and explicit restart, not an invented UI
action. Broader no-JSON camera/feature enrollment remains #17. To stop processing
immediately, pause the selected processor and its restart owner; a saved form
cannot make that guarantee. See [pause/uninstall/recovery](uninstall-restore.md).

## Deletion semantics for every retained copy

There is **no delete-everywhere command**. A deletion request must identify each
copy and finish with a separate read-back or an explicit unresolved result.
The table covers the same fourteen categories as the control site. "Not stored"
and "manual" are policies, not promises of secure overwrite or automatic TTL.

| Category | Local expiry/deletion operation and surviving copies |
| --- | --- |
| Downloaded clips/frames/crops | Per-job directory removed on normal return, error and cancellation; decoder killed/reaped on interruption. Hard process/host failure can leave directories. Pause/drain the owning processor before operator inspection/removal of its confirmed orphan directories; do not sweep another active job. No provider/Protect original is erased. |
| Port frames/tracks/snapshots | Volatile bounded buffers end with that processor; removing a scope does not delete earlier native events/uploads. Memory release is not secure erasure. |
| Captions/active journals | Existing opportunistic rollover removes eligible terminal records and their caption/event details while preserving a text-free replay tombstone. Uncertain callbacks are retained, not falsely reported deleted. No timer deadline; controller/index/backup copies are separate. |
| Plates/vehicle metadata | No separate persistent plate database here. End local tracks/buffers by stopping the selected process; native vehicle text, provider inputs and backups are independent. |
| Live search/indexes | No application-wide per-person/event erasure or independent TTL is implemented. A controller delete/rebuild must be verified against the actual affected rows/index copies; if unavailable, record **unresolved**, not "deleted". Do not drop a live database to satisfy a single-record request. |
| Enhanced derivatives | No local persistent output file; release per-job bytes. Delete native derivative and original/backup copies separately; removing the derivative is not deletion of the recording. |
| Admin audit/host logs | Product audit rotates by size to one prior file, without private payloads. Operator handles host/container log retention and any replicas; absence from the current audit file is not whole-history deletion. |
| Device/config/credential state | Retain while adopted or needed for recovery. Remove unused history/credential files only after reconciling all active/archived consumers and deciding the rollback trade-off. A full retirement must stop the sole identity and reconcile controller adoption first. Removing identity files during normal operation is not a privacy shortcut. |
| Full-state backups/replicas | Operator expiry at each named backup/snapshot/replica destination. No product scheduler or blanket erase. Record access/retention and verified expiry independently; restoration can reintroduce data removed from the live store. |
| Logical search backups | `python -m aikey.search_backup prune --out <private-directory>` previews the bounded plan; `--apply` is the explicit local delete. Defaults keep newest three and require age over 30 days; newest verified restore is protected. Unsafe/ambiguous sets fail without deleting. Neither live indexes nor external replicas are erased. |
| Replay archive | Read-only inventory/dry-run expiry plan only. Age alone cannot authorize deletion while Protect can redispatch a charged job. Keep markers unless current controller non-redispatch evidence and a reviewed removal procedure exist; unavailable evidence is a blocked deletion, not a successful purge. Full retirement is separate from continued processing. |
| Named-face templates | `local-aikey-faces delete --state-dir <private-state> --name <identity>` removes that live entry atomically; `purge` removes that live identity file. A fresh store must no longer match the removed identity. Other identities/device state remain. Names/templates in prior backups and Protect face groups/crops are not touched. No time-based identity expiry is configured. |
| Transcripts/decoded audio | Local journal retains segment count only; per-job audio directory cleanup as above. Protect transcript/event, speech-provider retention and crash leftovers need their own handling. No local transcript database is claimed. |
| Provider keys | Role-scoped write-only replacement changes the active reference, not remote revocation. Retained old/new files may serve archived or uncertain outcomes. Revoke at the actual provider, reconcile references, then separately remove eligible local/backup copies; do not delete a file merely because it is absent from the current config. |

If a legal/operational requirement demands deletion that an external system or
the replay-safety boundary cannot currently prove, **do not enable that feature
under that requirement**. Record the unresolved copy and responsible system;
this policy does not turn an unsupported erasure into a pass. Product native
deletion/recovery acceptance remains in the corresponding feature issues.

## Provider-specific facts, checked 30 October 2026

These are dated documentation/request-shape facts, **not** read-back of an
operator's account, project contract, region, actual retention or deletion.
Check again before enabling or changing a destination/model. A private proxy
inherits its real upstream's policy, not an assumed "local" guarantee.

| Adapter | What this client sends | Provider policy boundary |
| --- | --- | --- |
| Official OpenAI vision | Foreground `/v1/responses`, inline images, explicit `store: false`; no Files/background/MCP operation | [Official data controls](https://developers.openai.com/api/docs/guides/your-data) distinguish application state from abuse monitoring. Default abuse logs can contain content and are retained up to 30 days, with legal/safety exceptions; approved organization/project controls and model/feature eligibility matter. Image/file safety retention and model prompt-cache exceptions still apply. `store: false` is **not** Zero Data Retention or proof of no provider copy. |
| Official OpenAI speech | One multipart `/v1/audio/transcriptions` request; no separate Files upload | The same official endpoint table lists no application-state or abuse-monitoring retention for transcriptions, subject to the page's applicable limitations/account terms. This is not a claim about ChatGPT/other audio endpoints, controller copies, a compatible proxy or a measured account. |
| Anthropic vision | `/v1/messages`, inline base64 image; no client flag claiming erasure/ZDR | [Official commercial/API retention](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data) describes standard backend input/output deletion within 30 days with exceptions, and separate requirements for Covered Models. Flagged-policy content can be retained up to two years and classification scores up to seven; legal/contract provisions also matter. No operator contract/ZDR status is inferred. This adapter is not Anthropic speech/embedding support. |
| Ollama / OpenAI-compatible vision, local speech/E5/CLIP/face/Re-ID/rerank/enhancement | Role-specific inputs to the explicitly selected endpoint only | Protocol and IP location do not establish logging, forwarding, cloud use, backup expiry or terms. Operator must inventory the actual server/process/upstream. Unknown policy remains unknown; model availability or a successful synthetic test is not retention evidence. |

## Acceptance checks and remaining native limits

The [cross-component lifecycle checks](../tests/test_privacy_lifecycle.py)
exercise owned real OS processes running a synthetic decoder script (not actual
FFmpeg conversion) and actual temporary directories under success/error/cancellation,
durable named-identity deletion and separate backup
survival, provider-key switch/Undo with an existing adopted fixture and immutable
identity/certificates, acknowledged/refused management credential rotation with
adoption preserved after recreation, and saved audio opt-out followed by reconstruction. They
do not touch a real enrollment, credential, camera, controller or provider.

Existing [speech](../tests/test_speech_to_text.py),
[face](../tests/test_local_faces.py), [Port face](../tests/test_aiport_faces.py),
[plate](../tests/test_aiport_plates.py) and [sound](../tests/test_aiport_sounds.py)
tests check per-camera/type refusal before corresponding processing; saved
scope/Undo tests check removal and prevent silent regrant. Archive/budget tests
cover actual rollover, expiry windows and replay refusal. Search-backup tests
exercise dry-run/apply and unsafe-set refusal. Access/secret-loading/audit/demo
tests use canaries to check private files, authenticated boundaries, allowlisted
diagnostics and synthetic-only output. Credential rotation/recovery tests keep
adoption distinct from provider credentials; native Port effective management
password equality from earlier rollout is still **unknown**, not overwritten by
fixture success.

This completes neither a live erasure request nor provider/controller retention
verification, power-loss cleanup, browser/mobile access, native recognition
quality, host encryption/restore or independent security review. The policy
explicitly defines which actions exist, retained copies and blocked cases;
those observations must not be promoted into installation-wide privacy proof.
