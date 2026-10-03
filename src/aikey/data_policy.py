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
# "not_stored" (no local copy). No row may claim automatic expiry.
EXPIRY_KINDS = ("manual", "none", "per_job", "not_stored")

ROWS = (
    {"key": "search_backups", "category": "Search-index backups (captions, embeddings, search rows)",
     "local": "Full index dumps with a manifest, private files",
     "protect": "Protect references the live search rows; their lifetime follows Protect's events",
     "expiry": "manual",
     "detail": "Dry-run prune; with --apply it keeps the newest verified backup. Nothing schedules it.",
     "controller_decides": True,
     "owner_decision": "How many backups and how many days to keep, and whether to schedule prune",
     "evidence": ("aikey.search_backup:prune",)},
    {"key": "worker_archive", "category": "Worker archive markers (hashed job and input IDs)",
     "local": "One small marker per finished job; no media, captions or camera IDs",
     "protect": None,
     "expiry": "none",
     "detail": "Kept as the replay guard. The dry-run plan expires nothing until Protect confirms "
               "an event can no longer be dispatched.",
     "controller_decides": True,
     "owner_decision": "Minimum age for markers, once controller evidence of event absence exists",
     "evidence": ("aikey.worker_archive:inventory", "aikey.worker_archive:plan")},
    {"key": "face_store", "category": "Face identities (enrolled names and face templates)",
     "local": "Private identity file on this machine; never sent to a provider",
     "protect": "Protect keeps its own face groups and names",
     "expiry": "none",
     "detail": "No time-based expiry. An identity is removed with delete, or all with purge.",
     "controller_decides": True,
     "owner_decision": "Whether identities expire, who may enroll, and how long Protect keeps faces",
     "evidence": ("aikey.faces:FaceStore.delete", "aikey.faces:FaceStore.purge")},
    {"key": "transcripts", "category": "Speech transcripts and decoded event audio",
     "local": "No transcript copy: the job journal keeps only a segment count",
     "protect": "Protect saves the transcript on the event",
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
    """Read-only counts per row; None where the location is not configured."""
    from .faces import FaceStore, FaceStoreError
    from .worker import AUDIO_TEMP_PREFIX
    from .worker_archive import inventory
    status: dict[str, dict | None] = {"search_backups": None, "worker_archive": None,
                                      "face_store": None, "transcripts": None,
                                      "provider_keys": None}
    backups = _count_backups(backups_dir)
    if backups is not None:
        status["search_backups"] = {"backups": backups}
    if state_root is None or not Path(state_root).is_dir():
        return status
    root = Path(state_root)
    report = inventory(root)
    status["worker_archive"] = {"markers": report["tombstones"],
                                "problems": sum(report["problems"].values())}
    try:
        status["face_store"] = {"identities": len(FaceStore(root).names())}
    except (FaceStoreError, OSError, ValueError):
        status["face_store"] = {"identities": None, "problem": "unreadable"}
    jobs = root / "worker-jobs"
    leftovers = (sum(1 for path in jobs.iterdir() if path.name.startswith(AUDIO_TEMP_PREFIX))
                 if jobs.is_dir() and not jobs.is_symlink() else 0)
    status["transcripts"] = {"audio_leftovers": leftovers}
    config = root / "config.json"
    if config.is_file() and not config.is_symlink():
        text = config.read_text()
        files = [path.name for path in root.glob("provider-key-*") if path.is_file()]
        status["provider_keys"] = {"files": len(files),
                                   "superseded": sum(1 for name in files if name not in text)}
    return status
