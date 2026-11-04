"""Read-only backup and restore preflight for AI Key and AI Port state (#13).

``inventory`` lists, per profile, what a safe backup must hold: the config,
the adopted identity and TLS key, controller trust, the secret files the
config references, and the config journals. ``validate`` checks a candidate
backup (``manifest.json`` plus one directory per profile) against live state
for identity, a stale revision, missing secrets, a partial archive and
cross-slot contamination.

Both report counts and fixed reason codes only: never paths, file names of
secrets, keys, MACs or values. Neither exports, restores or writes anything.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

MANIFEST_SCHEMA = "aikey-state-backup/1"
_MAX = 1024 * 1024
_MAC = re.compile(r"[^0-9A-F]")


def _mac(value) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _MAC.sub("", value.upper())
    return normalized if len(normalized) == 12 else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX:
        raise ValueError("not a small regular file")
    return json.loads(path.read_text())


def _host(reference, directory: Path, runtime: str | None) -> Path | None:
    """Map a config file reference (possibly a container path) to the host file."""
    if not isinstance(reference, str) or not reference:
        return None
    path = Path(reference)
    if runtime and str(path.parent) == runtime.rstrip("/"):
        return directory / path.name
    return path if path.is_absolute() else directory / path


def _profile_spec(kind: str, directory: Path) -> dict:
    """Required files, secret references and journals for one profile."""
    config = _json(directory / "config.json")
    from .device_workflow_fence import check as check_device_intent, path as device_journal, read as read_device_journal
    journal=device_journal(directory/'config.json')
    check_device_intent(directory/'config.json',durable=False)
    if kind == "aikey":
        runtime = (config.get("runtime") or {}).get("state_dir")
        controller = config.get("controller") or {}
        required = {"config": directory / "config.json",
                    "device_identity": directory / "device-state.json",
                    "tls_certificate": directory / "device.crt", "tls_key": directory / "device.key",
                    "controller_trust": _host(controller.get("ca_file"), directory, runtime)}
        if controller.get("search_ca_file"):
            required["search_trust"] = _host(controller["search_ca_file"], directory, runtime)
        if (config.get("search") or {}).get("enabled"):
            required["search_profile"] = directory / "search-profile.json"
        secrets = {"inference_key": (config.get("inference") or {}).get("api_key_file"),
                   "embedding_token": (config.get("embeddings") or {}).get("bearer_token_file"),
                   "speech_key": (config.get("speech_to_text") or {}).get("api_key_file")}
        if (directory / "database-password").exists():
            secrets["database_password"] = str(directory / "database-password")
        secrets = {name: _host(ref, directory, runtime) for name, ref in secrets.items() if ref}
        journals = {"config_history": directory / ".aikey-config-history",
                    "worker_jobs": directory / "worker-jobs", "worker_archive": directory / "worker-archive",
                    "one_use_permits": directory / "worker-test-scopes",
                    "caption_budget": directory / "caption-budget.json"}
        sensitive = {"face_store": directory / "faces" / "identities.json"}
        identity = _mac(_json(directory / "device-state.json").get("mac")
                        if (directory / "device-state.json").is_file() else None)
    else:
        provider = (config.get("live_pool_detector") or {}).get("provider_config") or {}
        adoption = next((directory / name for name in ("aiport-adoption.json", "identity.json")
                         if (directory / name).exists()), directory / "aiport-adoption.json")
        required = {"config": directory / "config.json", "adoption_state": adoption,
                    "tls_certificate": directory / "device.crt", "tls_key": directory / "device.key",
                    "controller_trust": directory / "controller-ca.pem"}
        secrets = {"provider_key": _host(provider.get("api_key_file"), directory, "/state")} \
            if provider.get("api_key_file") else {}
        journals = {"config_history": directory / ".aiport-config-history"}
        sensitive = {}
        identity = _mac(config.get("mac"))
    from .controller_trust import DIRECTORY, selected, _root as trust_root, _read as trust_read
    retained = {}
    from .host_activation import read as host_activation,path as activation_path
    reservation=host_activation(directory/'config.json')
    if reservation is not None:
        if reservation['phase']=='prepared':
            raise ValueError('pending host activation blocks coherent backup')
        reserved=activation_path(directory/'config.json')
        retained[reserved.relative_to(directory).as_posix()]=_sha256(reserved)
    if kind=='aiport' and config.get('face_model_space_ref'):
        from .port_feature_models import load_face_space
        load_face_space(directory,config['face_model_space_ref'],_mac(config.get('mac')),config.get('live_face'))
        required['port_face_model_space']=directory/config['face_model_space_ref']
    from .port_feature_models import valid_space_ref
    for path in directory.glob('port-face-space.*'):
        if not valid_space_ref(path.name):
            raise ValueError('invalid retained face model-space resource')
        from .device_workflow import _private
        retained[path.relative_to(directory).as_posix()]=hashlib.sha256(_private(path,8192)).hexdigest()
    if kind=='aiport' and config.get('onvif_catalog_ref'):
        from .onvif_catalog import load as load_onvif
        catalog=load_onvif(directory,config['onvif_catalog_ref'],_mac(config.get('mac')))
        required['onvif_metadata_catalog']=directory/config['onvif_catalog_ref']
        for index,row in enumerate(catalog['entries']):
            for role in ('username','password'):
                secrets['onvif_'+role+'_'+str(index)]=directory/row[role+'_ref']
    from .onvif_catalog import valid_reference as onvif_reference, _SECRET as onvif_secret
    from .device_workflow import _private
    for path in directory.glob('onvif-*'):
        if not (onvif_reference(path.name) or onvif_secret.fullmatch(path.name)):
            raise ValueError('invalid retained ONVIF resource')
        raw=_private(path,8192)
        retained[path.relative_to(directory).as_posix()]=hashlib.sha256(raw).hexdigest()
    for channel in (('control','search') if kind=='aikey' else ('control',)):
        trust=selected(config,kind=kind,state_dir=directory,channel=channel)
        if trust is not None:
            required[channel+'_trust_object']=directory/DIRECTORY/trust.reference
    if (directory/DIRECTORY).exists() or (directory/DIRECTORY).is_symlink():
        journals['controller_trust_history']=directory/DIRECTORY
        descriptor=trust_root(directory/DIRECTORY)
        try:
            names=os.listdir(descriptor)
            if len(names)>4096:
                raise ValueError('retained trust inspection limit')
            for name in names:
                # Historical certificates may be expired. Preserve their exact
                # private digest-bound objects without authorizing their use.
                retained[(Path(DIRECTORY)/name).as_posix()]=hashlib.sha256(trust_read(descriptor,name)).hexdigest()
        finally:
            os.close(descriptor)
    if journal.exists():
        required['device_workflow_journal']=journal
        record=read_device_journal(directory/'config.json',durable=False)
        if record['state']=='saved':
            history=directory/('.aikey-config-history' if kind=='aikey' else '.aiport-config-history')
            archived=history/(record['before']+'.json')
            if hashlib.sha256(_private(archived,_MAX)).hexdigest()!=record['before']:
                raise ValueError('device rollback archive mismatch')
            required['device_rollback_revision']=archived
        retained[journal.relative_to(directory).as_posix()]=_sha256(journal)
    from .model_work_fence import FILENAME
    fence = directory / FILENAME
    enrolled = fence.exists() or fence.is_symlink()
    if enrolled:
        required["model_work_fence"] = fence
    return {"config": config, "required": required, "secrets": secrets, "journals": journals,
            "sensitive": sensitive, "identity": identity, "model_work_fence": enrolled,
            "retained_device_state": retained}


def _present(path: Path | None) -> str:
    if path is None:
        return "unset"
    try:
        info = path.lstat()
    except OSError:
        return "missing"
    if stat.S_ISLNK(info.st_mode):
        return "symlink"
    if path.name == "model-work.lock" and not stat.S_ISREG(info.st_mode):
        return "not_regular"
    if stat.S_ISREG(info.st_mode) and info.st_mode & 0o077 and path.name in {"device.key", "model-work.lock"}:
        return "unsafe_mode"
    return "present"


def inventory(profiles: dict[str, tuple[str, Path]]) -> dict:
    """``profiles`` maps a label to (kind, host directory); kind is aikey or aiport."""
    report, identities = {}, {}
    for label, (kind, directory) in profiles.items():
        try:
            spec = _profile_spec(kind, Path(directory))
        except (OSError, ValueError, TypeError, AttributeError):
            report[label] = {"kind": kind, "readable": False, "problems": ["config_unreadable"]}
            continue
        problems = []
        states = {name: _present(path) for name, path in spec["required"].items()}
        problems += [f"{state}:{name}" for name, state in states.items() if state != "present"]
        secret_states = {name: _present(path) for name, path in spec["secrets"].items()}
        problems += [f"secret_{state}:{name}" for name, state in secret_states.items() if state != "present"]
        if spec["identity"] is None:
            problems.append("identity_unreadable")
        else:
            identities.setdefault(spec["identity"], []).append(label)
        report[label] = {
            "kind": kind, "readable": True,
            "required": len(states), "required_present": sum(s == "present" for s in states.values()),
            "secret_references": len(secret_states),
            "secrets_present": sum(s == "present" for s in secret_states.values()),
            "journals_present": sum(p.exists() for p in spec["journals"].values()),
            "sensitive_present": sum(p.exists() for p in spec["sensitive"].values()),
            "problems": problems}
    for labels in identities.values():
        if len(labels) > 1:
            for label in labels:
                report[label]["problems"].append("identity_shared_with_another_profile")
    ready = all(item.get("readable") and not item["problems"] for item in report.values())
    return {"schema": "aikey-state-backup-preflight/1", "ready_for_backup": ready, "profiles": report}


def validate(backup: Path, profiles: dict[str, tuple[str, Path]]) -> dict:
    """Check a candidate backup against live state; never restores anything."""
    backup = Path(backup)
    try:
        manifest = _json(backup / "manifest.json")
        if manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(manifest.get("profiles"), dict):
            raise ValueError
    except (OSError, ValueError, TypeError, AttributeError):
        return {"restorable": False, "problems": ["manifest_invalid"], "profiles": {}}
    live = {}
    for label, (kind, directory) in profiles.items():
        try:
            live[label] = _profile_spec(kind, Path(directory))
        except (OSError, ValueError, TypeError, AttributeError):
            live[label] = None
    results, problems = {}, []
    backup_identities: dict[str, list[str]] = {}
    for label, entry in manifest["profiles"].items():
        codes = []
        files = entry.get("files") if isinstance(entry, dict) else None
        root = backup / str(label).replace(":", "_")
        if not isinstance(files, dict) or label not in profiles:
            results[label] = {"problems": ["unknown_profile" if label not in profiles else "entry_invalid"]}
            continue
        kind = profiles[label][0]
        # Partial archive: every listed file present, regular and matching its digest.
        for name, digest in files.items():
            path = root / name
            if ".." in Path(name).parts or path.is_symlink() or not path.is_file():
                codes.append("partial_archive")
                break
            if _sha256(path) != digest:
                codes.append("digest_mismatch")
                break
        try:
            spec = _profile_spec(kind, root)
        except (OSError, ValueError, TypeError, AttributeError):
            results[label] = {"problems": codes + ["backup_config_unreadable"]}
            continue
        def listed(path):
            try:
                return path.relative_to(root).as_posix() in files
            except ValueError:
                return False                     # outside the backup: cannot be restored from it
        for name, path in spec["required"].items():
            if path is None or _present(path) != "present" or not listed(path):
                codes.append(f"missing:{name}")
        if spec["model_work_fence"]:
            # Content/digest equality does not restore shared inode identity or
            # settle old maintenance intent. No generic green restore claim.
            codes.append("model_work_fence_reenrollment_required")
        for name, path in spec["secrets"].items():
            if path is None or _present(path) != "present" or not listed(path):
                codes.append(f"missing_secret:{name}")
        identity = spec["identity"]
        if identity is None:
            codes.append("identity_unreadable")
        else:
            backup_identities.setdefault(identity, []).append(label)
            current = live.get(label)
            if current and current["identity"] and current["identity"] != identity:
                others = [other for other, value in live.items()
                          if value and value["identity"] == identity and other != label]
                codes.append("cross_slot_contamination" if others else "identity_mismatch")
        current = live.get(label)
        if current is None:
            codes.append('live_profile_unreadable')
        if current:
            # A complete manifest of an incomplete copy is not sufficient.
            # Include all live retained trust versions and its last Undo intent.
            for name, digest in current['retained_device_state'].items():
                if name not in files or name not in spec['retained_device_state']:
                    codes.append('retained_device_state_missing')
                elif files[name]!=digest or spec['retained_device_state'][name]!=digest:
                    codes.append('retained_device_state_changed')
            live_digest = _sha256(Path(profiles[label][1]) / "config.json")
            backup_digest = _sha256(root / "config.json")
            if backup_digest != live_digest:
                history = Path(profiles[label][1]) / (".aikey-config-history" if kind == "aikey"
                                                      else ".aiport-config-history")
                codes.append("stale_revision" if (history / f"{backup_digest}.json").exists()
                             else "revision_not_in_history")
        results[label] = {"files": len(files), "problems": sorted(set(codes))}
    for labels in backup_identities.values():
        if len(labels) > 1:
            problems.append("identity_duplicated_across_profiles")
            for label in labels:
                results[label]["problems"] = sorted(set(results[label]["problems"]) | {"cross_slot_contamination"})
    missing = sorted(set(profiles) - set(manifest["profiles"]))
    if missing:
        problems.append(f"profiles_missing:{len(missing)}")
    restorable = not problems and all(not r["problems"] for r in results.values())
    return {"restorable": restorable, "problems": problems, "profiles": results}
