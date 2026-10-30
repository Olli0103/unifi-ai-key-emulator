"""Data-flow and retention table for the control site (#5).

Each row states where one output type is kept, whether Protect keeps its own
copy, and how it expires today. Every claim names the code that makes it
true (``evidence``), and ``tests/test_data_policy.py`` checks those claims
against the code, so a removed mechanism or an overstated expiry fails CI.

Live status is counts only: never names, text, embeddings or paths.
Retention periods are not chosen here; ``owner_decision`` lists what the
operator must decide for their location.
"""

from __future__ import annotations

from pathlib import Path

# expiry values: "manual" (an operator command, never scheduled), "none"
# (kept until an operator removes it), "per_job" (removed when the job ends),
# "not_stored" (no local copy), "on_admission" (opportunistic rollover, not a
# timer), "size_rotation" (bounded file rotation), "process" (volatile buffers).
EXPIRY_KINDS = ("manual", "none", "per_job", "not_stored", "on_admission",
                "size_rotation", "process")

ROWS = (
    {"key": "media_frames", "category": "Downloaded clips, decoded frames and image crops",
     "local": "In-memory media plus per-job private video decode directories",
     "protect": "Protect retains original recordings, query-image uploads and search/face snapshots; "
                "their deletion/expiry is independent and not fully verified",
     "destination": "Configured caption, face, CLIP or other selected model service; "
                    "only the role's selected input. Protect receives callback snapshots.",
     "default": "No model selected; no continuous camera scope. Features require explicit setup.",
     "expiry": "per_job",
     "detail": "Normal completion/error/cancellation removes temporary video files. A hard crash "
               "can leave decode directories; leftovers are counted, not automatically swept. "
               "Memory release is not verified secure erasure.",
     "controller_decides": True,
     "owner_decision": "Recording/snapshot lifetime in Protect, model service retention and "
                       "offline cleanup of crash leftovers",
     "evidence": ("aikey.worker:JobProcessor._video_frame", "aikey.worker:JobProcessor._fetch")},
    {"key": "aiport_buffers", "category": "AI Port live frames, tracks and pending snapshots",
     "local": "Bounded volatile inference queues, tracks and snapshot buffers",
     "protect": "Protect retains published detections and uploaded snapshots",
     "destination": "Configured local/API detection and optional face/plate service; Protect uploads",
     "default": "Live detection, audio, face and plate scopes require explicit per-camera setup.",
     "expiry": "process",
     "detail": "Frames are replaced/consumed and snapshots pruned by the running service; buffers "
               "are lost on process exit. No secure-erasure or provider-deletion promise.",
     "controller_decides": True,
     "owner_decision": "Which cameras/features may send frames, model retention and Protect event lifetime",
     "evidence": ("aikey.aiport_inference:FairInference", "aikey.aiport_candidate:CandidateService._prune_snapshots")},
    {"key": "captions", "category": "Captions and active worker result journals",
     "local": "Basic captions/event identifiers may remain in private active job records; "
              "Deep descriptions use count-only journal summaries. Private failure notes keep "
              "opaque job/input hashes, fixed exception categories and optional controller HTTP status",
     "protect": "Protect saves descriptions on events/sessions; search copies can also persist",
     "destination": "Selected vision service receives images/prompts; configured embedding "
                    "service may receive description text; Protect receives descriptions",
     "default": "Continuous mode absent; vision model empty. No caption retention timer is configured.",
     "expiry": "on_admission",
     "detail": "On startup/new admission, completed/failed continuous records roll over after "
               "one hour; non-continuous completed records after one day, failures after seven. "
               "Completed local-index records roll over after one minute. Archive markers omit "
               "text. Callback-uncertain records remain active for review; no timer guarantees deletion. "
               "Separate best-effort failure notes retain the first observed exception per job/input "
               "up to 1024 notes, without automatic deletion. Active journal counts exclude those notes.",
     "controller_decides": True,
     "owner_decision": "Caption/search/controller/backup lifetime and review of uncertain outcomes; "
                       "do not delete deduplication records to retry a possibly charged job",
     "evidence": ("aikey.worker:rollover_due", "aikey.worker:JobProcessor._archive_terminal",
                  "aikey.worker:JobProcessor._execute", "aikey.worker_failure_notes:FailureNotes")},
    {"key": "plates", "category": "License-plate readings and vehicle metadata",
     "local": "Optional plate strings in volatile AI Port vehicle tracks",
     "protect": "Protect may retain plate text with the native vehicle event and search metadata",
     "destination": "Configured API detector receives selected frames; Protect receives vehicle descriptors",
     "default": "Plate cameras are opt-in; a vehicle result without a reading has no invented plate.",
     "expiry": "process",
     "detail": "No separate local plate database or deletion API. Local buffers end with the "
               "process; Protect, provider and backups have independent lifetimes.",
     "controller_decides": True,
     "owner_decision": "Per-camera plate use, provider retention and deletion of controller/backup copies",
     "evidence": ("aikey.aiport_tracking:TemporalTracker", "aikey.aiport_smart_events:smart_event_payload")},
    {"key": "live_search", "category": "Live search indexes, embeddings and model-space metadata",
     "local": "Search database volumes plus pinned encoder/profile and migration metadata",
     "protect": "Protect references database rows and keeps its own event/session copies",
     "destination": "Configured search database; selected image/text encoder and reranker "
                    "receive their role inputs, including query/description text",
     "default": "Search and database disabled. Enabling a role is not an expiry policy.",
     "expiry": "none",
     "detail": "No independent application-wide TTL. Controller row cleanup and rebuild/migration "
               "have separate semantics; deleting an event is not proven deletion of every index copy.",
     "controller_decides": True,
     "owner_decision": "Index, database-volume, controller and backup lifetimes; rebuild and deletion verification",
     "evidence": ("aikey.search:SearchService", "aikey.embedding_profile:ensure_embedding_profile")},
    {"key": "enhancements", "category": "Enhanced face images (separate derived evidence)",
     "local": "Original crop and derived JPEG in memory; journal stores only outcome/byte counts",
     "protect": "Protect stores the uploaded derived image separately from the recording",
     "destination": "Explicitly configured face enhancer receives a crop; Protect receives the result",
     "default": "Enhancement is unconfigured and refused until explicitly enabled.",
     "expiry": "not_stored",
     "detail": "No persistent local enhanced-image file. This does not delete a model-server, "
               "controller, original recording, snapshot or backup copy.",
     "controller_decides": True,
     "owner_decision": "Whether enhancement is appropriate and lifetime of original/derived/controller copies",
     "evidence": ("aikey.worker:JobProcessor._execute_enhance",)},
    {"key": "admin_audit", "category": "Administration audit and operational logs",
     "local": "Private allowlisted admin-action log; separate service/runtime logs may exist",
     "protect": None,
     "destination": "No application audit export; host log shipping is separately administered",
     "default": "When configured, admin audit rotates at 256 KiB to one prior file; no time-based expiry.",
     "expiry": "size_rotation",
     "detail": "Admin audit excludes settings, URLs, names, media and keys. This size bound "
               "does not cover container/system logs or copies made by the host.",
     "controller_decides": False,
     "owner_decision": "Host/container log rotation, access and backup retention; no sensitive debug exports",
     "evidence": ("aikey.admin_audit:AuditLog.record", "aikey.admin_audit:AuditLog._rotate_if_needed")},
    {"key": "device_state", "category": "Device identity, credentials and configuration history",
     "local": "Private identity/certificate/key/password files, config revisions, migration journals "
              "and camera-specific quota/admission ledgers",
     "protect": "Protect retains the adopted-device record and its own credential references",
     "destination": "Device credentials only to their controller/database role; "
                    "configuration history remains local",
     "default": "Stable identity retained; no automatic history or credential-file expiry.",
     "expiry": "none",
     "detail": "Private permissions are not application encryption. Revoke/rotate the matching "
               "credential before removing unused files; rollback may need an archived reference. "
               "Budget entries have their own rolling windows, not expiry of this identity. "
               "Deleting these files can break adoption and is not a remote factory reset.",
     "controller_decides": True,
     "owner_decision": "Host/disk protection, access, credential recovery/revocation and history/backup lifetime",
     "evidence": ("aikey.config:initialize", "aikey.config_store:ConfigurationStore.rollback",
                  "aikey.state_schema:upgrade_state_file")},
    {"key": "state_backups", "category": "Full device/state backups and external replicas",
     "local": "Operator-created state/config/journal/identity copies, potentially including crash media",
     "protect": "Controller backups are independent and not managed here",
     "destination": "Operator-selected disk/NAS/backup service, not automatically enumerated here",
     "default": "No full-state backup scheduler or expiry provided by this page.",
     "expiry": "none",
     "detail": "Backup preflight validates identity/schema/references, not encryption or expiry. "
               "Deleting live data does not purge backup versions, snapshots or replicas.",
     "controller_decides": True,
     "owner_decision": "Encrypted backup target, access, maximum age, replicas, restore recovery and deletion evidence",
     "evidence": ("aikey.state_backup_preflight:inventory", "aikey.state_backup_preflight:validate")},
    {"key": "search_backups", "category": "Search-index backups (captions, embeddings, search rows)",
     "local": "Full index dumps with a manifest, private files",
     "protect": "Protect manages its live copies; complete index cleanup policy is not verified",
     "destination": "Operator-selected private backup directory; separate from provider storage",
     "default": "Manual only; prune dry run considers backups beyond the newest 3 and older than 30 days.",
     "expiry": "manual",
     "detail": "Dry-run prune; with --apply it keeps the newest verified backup. Nothing schedules it.",
     "controller_decides": True,
     "owner_decision": "How many backups and how many days to keep, and whether to schedule prune",
     "evidence": ("aikey.search_backup:prune",)},
    {"key": "worker_archive", "category": "Worker archive markers (hashed job and input IDs)",
     "local": "One small marker per finished job; no media, captions or camera IDs",
     "protect": None,
     "destination": "Local state and any operator-created backups",
     "default": "Replay markers are retained without an expiry timer.",
     "expiry": "none",
     "detail": "Kept as the replay guard. The dry-run plan expires nothing until Protect confirms "
               "an event can no longer be dispatched.",
     "controller_decides": True,
     "owner_decision": "Minimum age for markers, once controller evidence of event absence exists",
     "evidence": ("aikey.worker_archive:inventory", "aikey.worker_archive:plan")},
    {"key": "face_store", "category": "Face identities (enrolled names and face templates)",
     "local": "Private identity file on this machine; never sent to a provider",
     "protect": "Protect keeps its own face groups and names",
     "destination": "Matching names/crops may reach Protect; enrolled templates stay in the local store. "
                    "Enrollment/recognition sends a selected image to the configured private face service.",
     "default": "No enrolled identities or face camera scope; file permissions, not application encryption.",
     "expiry": "none",
     "detail": "No time-based expiry. An identity is removed with delete, or all with purge.",
     "controller_decides": True,
     "owner_decision": "Whether identities expire, who may enroll, and how long Protect keeps faces",
     "evidence": ("aikey.faces:FaceStore.delete", "aikey.faces:FaceStore.purge")},
    {"key": "transcripts", "category": "Speech transcripts and decoded event audio",
     "local": "No transcript copy: the job journal keeps only a segment count",
     "protect": "Protect saves the transcript on the event",
     "destination": "Configured speech provider receives decoded audio; Protect receives the transcript",
     "default": "Speech role/camera allowlist absent. Remote audio needs explicit opt-in.",
     "expiry": "not_stored",
     "detail": "Decoded audio sits in a temporary directory removed when the job ends. A hard crash "
               "can leave one behind; it is counted here, not swept.",
     "controller_decides": True,
     "owner_decision": "How long Protect keeps transcripts, and whether crash leftovers are swept "
                       "automatically",
     "evidence": ("aikey.worker:AUDIO_TEMP_PREFIX", "aikey.worker:JobProcessor._audio")},
    {"key": "provider_keys", "category": "Provider API key files (write-only secrets)",
     "local": "Private key files next to each profile config; never shown in the control site",
     "protect": None,
     "destination": "Only the configured role's approved provider; never controller credentials",
     "default": "No provider key created automatically; old references are retained for rollback.",
     "expiry": "none",
     "detail": "A replaced key keeps its previous file, because the archived configuration "
               "revision still references it for rollback. Nothing removes superseded keys.",
     "controller_decides": False,
     "owner_decision": "When superseded keys may be deleted, which gives up rolling back to "
                       "the revision that used them",
     "evidence": ("aikey.config_store:ConfigurationStore.rollback",
                  "aikey.control_site:ControlSite.save_provider")},
)


def _count_backups(directory: Path | None):
    if directory is None or not Path(directory).is_dir() or Path(directory).is_symlink():
        return None
    return sum(1 for path in Path(directory).glob("search-*.json") if path.is_file())


def live_status(state_root: Path | None, backups_dir: Path | None = None) -> dict:
    """Read-only counts per row; None where unmeasured or not configured."""
    from .faces import FaceStore, FaceStoreError
    from .worker import AUDIO_TEMP_PREFIX
    from .worker_archive import inventory
    status: dict[str, dict | None] = dict.fromkeys(row["key"] for row in ROWS)
    backups = _count_backups(backups_dir)
    if backups is not None:
        status["search_backups"] = {"backups": backups}
    if state_root is None or not Path(state_root).is_dir():
        return status
    root = Path(state_root)
    report = inventory(root, max_entries=2048, max_seconds=.25)
    status["worker_archive"] = {"markers": report["tombstones"],
                                "problems": sum(report["problems"].values())}
    if report.get("complete", True) is not True:
        status["worker_archive"] = {"markers": None, "problems": None,
                                    "scan": "incomplete; entry or time limit reached"}
    try:
        status["face_store"] = {"identities": len(FaceStore(root).names())}
    except (FaceStoreError, OSError, ValueError):
        status["face_store"] = {"identities": None, "problem": "unreadable"}
    jobs = root / "worker-jobs"
    leftovers = (sum(1 for path in jobs.iterdir() if path.name.startswith(AUDIO_TEMP_PREFIX))
                 if jobs.is_dir() and not jobs.is_symlink() else 0)
    status["transcripts"] = {"audio_leftovers": leftovers}
    status["media_frames"] = {"video_leftovers": sum(
        1 for path in jobs.iterdir() if path.name.startswith("aikey-video-"))
        if jobs.is_dir() and not jobs.is_symlink() else 0}
    config = root / "config.json"
    if config.is_file() and not config.is_symlink():
        text = config.read_text()
        files = [path.name for path in root.glob("provider-key-*") if path.is_file()]
        status["provider_keys"] = {"files": len(files),
                                   "superseded": sum(1 for name in files if name not in text)}
    return status
