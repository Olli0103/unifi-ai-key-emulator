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
  uploaded crops have their own lifetime. Deleting a local identity or journal
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
default satisfies every location. This inventory closes a documentation/UI gap
in #5, not full privacy/deletion or native acceptance of that issue.
