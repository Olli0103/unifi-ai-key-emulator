"""Read-only preflight for continuous AI Key captions (#12).

Reconciles, for the configured Key, the camera scope, the one-use permits,
the global rolling 12-caption budget, the restart journal, the Key's natural
task counters and any operator-supplied native caption readback, and lists
what still blocks calling continuous captions working. It only reads: no
media, no model call, no configuration change. Output is counts and fixed
blocker texts; no camera, event or job identifiers.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import time

from .caption_budget import HOUR_NS, LIMIT, RETENTION_NS
from .worker import rollover_due

_DEFAULT_LEDGER_CAP = 1024
DAILY_CEILING = 24 * LIMIT          # 288: the most a full day of rolling hours can admit
_NON_CAPTION = frozenset({"speechToText", "recognizeFaces", "indexKeyFrames", "indexImages", "reverify"})
# The report is advisory: no runtime path reads ``ready``. Each blocker is
# labelled with when it can be cleared, so an activation review is not
# mistaken for a deadlock. "precondition" must clear before activation;
# "activation" is cleared by the reviewed config change itself; "acceptance"
# can only be shown after activation, from native Protect readback.
PHASES = {"uncertain_callbacks_pending": "precondition", "ledger_above_80_percent": "precondition",
          "budget_journal_needs_review": "precondition", "key_health_missing": "precondition",
          "continuous_not_configured": "activation", "one_use_scopes_configured": "activation",
          "native_readback_missing": "acceptance"}


def _json(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise ValueError(f"{path.name} is not a small regular file")
    return json.loads(path.read_text())


def _scope(config: dict) -> dict:
    worker = config.get("worker") or {}
    scopes = worker.get("test_scopes") or ([worker["test_scope"]] if "test_scope" in worker else [])
    return {"continuous_configured": "continuous" in worker,
            "one_use_scopes": len(scopes),
            "scope_kinds": sorted(collections.Counter(s.get("kind", "unknown") for s in scopes)),
            "camera_models_policy": len((worker.get("continuous") or {}).get("camera_models", [])),
            "camera_ids_pinned": len((worker.get("continuous") or {}).get("camera_ids", []))}


def _permits(state: Path) -> dict:
    total = consumed = unreadable = 0
    directory = state / "worker-test-scopes"
    if directory.is_dir() and not directory.is_symlink():
        for path in directory.glob("*.json"):
            total += 1
            try:
                consumed += bool(_json(path).get("consumed_at"))
            except (OSError, ValueError, AttributeError):
                unreadable += 1
    return {"total": total, "consumed": consumed, "unconsumed": total - consumed - unreadable,
            "unreadable": unreadable}


def _budget(state: Path, now_ns: int) -> dict:
    path = state / "caption-budget.json"
    if not path.exists() and not path.is_symlink():
        return {"journal": False, "last_hour": 0, "remaining": LIMIT, "retained_24h": 0,
                "clock_behind_journal": False}
    try:
        value = _json(path)
        times = [item["at_ns"] for item in value["reservations"]]
        high_water = value["high_water_ns"]
    except (OSError, ValueError, KeyError, TypeError):
        return {"journal": True, "unreadable": True}
    recent = [t for t in times if now_ns - HOUR_NS < t <= now_ns]
    return {"journal": True, "last_hour": len(recent), "remaining": max(0, LIMIT - len(recent)),
            "retained_24h": sum(now_ns - t < RETENTION_NS for t in times),
            "clock_behind_journal": now_ns < high_water}


def _reserved_jobs(state: Path) -> set[str]:
    try:
        value = _json(state / "caption-budget.json")
        return {item["job_id"] for item in value["reservations"]}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def _abort(state: Path, config: dict, health: dict | None, budget: dict, now: float) -> dict:
    """Live abort checks for an active rollout; read-only, counts and fixed codes only."""
    continuous = (config.get("worker") or {}).get("continuous") or {}
    pinned = len(continuous.get("camera_ids", [])) or None
    unreserved = 0
    if continuous and not continuous.get("unmetered"):
        reserved = _reserved_jobs(state)
        directory = state / "worker-jobs"
        for path in directory.glob("*.json") if directory.is_dir() and not directory.is_symlink() else []:
            try:
                record = _json(path)
                if (record.get("operation") not in _NON_CAPTION and now - record["updatedAt"] < 86400
                        and path.stem not in reserved):
                    unreserved += 1
            except (OSError, ValueError, KeyError, TypeError):
                continue
    registry = (health or {}).get("camera_registry") or {}
    eligible = registry.get("eligible_cameras")
    checks = {"budget_last_hour": budget.get("last_hour"), "hourly_limit": LIMIT,
              "budget_24h": budget.get("retained_24h"), "daily_ceiling": DAILY_CEILING,
              "caption_jobs_without_reservation": unreserved,
              "eligible_cameras": eligible, "pinned_cameras": pinned}
    codes = []
    if (budget.get("last_hour") or 0) > LIMIT:
        codes.append("hourly_budget_exceeded")
    if (budget.get("retained_24h") or 0) > DAILY_CEILING:
        codes.append("daily_ceiling_exceeded")
    if unreserved:
        codes.append("caption_without_reservation")
    if pinned is not None and isinstance(eligible, int) and eligible > pinned:
        codes.append("scope_wider_than_pin")
    return {"checks": checks, "codes": codes}


def _ledger(state: Path, cap: int, continuous: bool, now: float) -> dict:
    states, rollover, per_day = collections.Counter(), 0, collections.Counter()
    directory = state / "worker-jobs"
    unreadable = 0
    if directory.is_dir() and not directory.is_symlink():
        for path in directory.glob("*.json"):
            try:
                record = _json(path)
                states[record["state"]] += 1
                rollover += rollover_due(record, now, continuous=continuous)
                # Only records rollover never removes grow the ledger for good.
                if record["state"] == "callback_uncertain" and now - record["updatedAt"] < 86400:
                    per_day[0] += 1
            except (OSError, ValueError, KeyError, TypeError):
                unreadable += 1
    size = sum(states.values())
    after = size - rollover
    daily = per_day.get(0, 0)
    return {"entries": size, "cap": cap, "percent": round(100 * size / cap, 1),
            "states": dict(sorted(states.items())), "unreadable": unreadable,
            "due_for_rollover": rollover, "entries_after_rollover": after,
            "uncertain_callbacks": states.get("callback_uncertain", 0),
            "uncertain_added_last_24h": daily,
            # Completed and failed records leave by rule; uncertain ones stay for review.
            "days_to_full_at_uncertain_rate": (round((cap - after) / daily, 1) if daily else None)}


def preflight(state_root: Path, config: dict, *, health: dict | None = None,
              native: dict | None = None, now: float | None = None,
              check_health: bool = True) -> dict:
    now = time.time() if now is None else now
    state = Path(state_root)
    scope = _scope(config)
    cap = (config.get("worker") or {}).get("max_ledger_entries", _DEFAULT_LEDGER_CAP)
    report = {"schema": "aikey-caption-preflight/1", "scope": scope, "permits": _permits(state),
              "budget": _budget(state, int(now * 1e9)),
              "ledger": _ledger(state, cap, scope["continuous_configured"], now)}
    report["abort"] = _abort(state, config, health, report["budget"], now)
    if health is not None:
        device, worker = health.get("device", {}), health.get("worker", {})
        commands = device.get("control_commands", {})
        report["key"] = {"adopted": device.get("adopted"), "connected": device.get("connected"),
                         "requestai_commands": commands.get("RequestAI", {}).get("count"),
                         "recognize_key_frames_commands": commands.get("recognizeKeyFrames", {}).get("count"),
                         "queued": worker.get("queued"), "active": worker.get("active"),
                         "pending": worker.get("pending")}
    if native is not None:
        report["native"] = {key: native.get(key) for key in
                            ("saved_captions", "camera_families_with_captions", "read_after_reload")}
    blockers: list[tuple[str, str]] = []           # (stable code, fixed text with counts)
    if not scope["continuous_configured"]:
        blockers.append(("continuous_not_configured",
                         "Continuous mode is not configured (an owner activation step)"))
    if scope["one_use_scopes"]:
        blockers.append(("one_use_scopes_configured",
                         "One-use test scopes are configured; they are mutually exclusive with "
                         "continuous mode and must be removed at activation"))
    if report["ledger"]["uncertain_callbacks"]:
        blockers.append(("uncertain_callbacks_pending",
                         f"{report['ledger']['uncertain_callbacks']} uncertain callbacks await "
                         "operator review in the job journal"))
    if report["ledger"]["entries_after_rollover"] >= 0.8 * cap:
        blockers.append(("ledger_above_80_percent",
                         "The job journal would stay above 80% of its cap after rollover"))
    if report["budget"].get("unreadable") or report["budget"].get("clock_behind_journal"):
        blockers.append(("budget_journal_needs_review",
                         "The caption budget journal needs review (unreadable or clock behind it)"))
    if check_health and (health is None or not (report["key"]["adopted"] and report["key"]["connected"])):
        blockers.append(("key_health_missing", "No adopted, connected Key health was supplied"))
    families = (native or {}).get("camera_families_with_captions") or 0
    if families < 2 or not (native or {}).get("read_after_reload"):
        blockers.append(("native_readback_missing",
                         "Native Protect readback of continuous captions on two camera families "
                         "after a reload is missing"))
    if not check_health:
        report["key"] = {"checked": False}
    report["ready"] = not blockers
    report["blocker_codes"] = [code for code, _ in blockers]
    report["blocker_phases"] = {code: PHASES[code] for code, _ in blockers}
    report["ready_to_activate"] = not any(PHASES[code] == "precondition" for code, _ in blockers)
    report["blockers"] = [text for _, text in blockers]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aikey-caption-preflight",
                                     description="Read-only continuous-caption preflight.")
    parser.add_argument("--state", required=True, type=Path, help="AI Key state directory")
    parser.add_argument("--config", type=Path, help="AI Key config (default: <state>/config.json)")
    parser.add_argument("--health", type=Path, help="saved /healthz JSON of the Key")
    parser.add_argument("--native", type=Path,
                        help="operator readback counts: saved_captions, camera_families_with_captions, "
                             "read_after_reload")
    args = parser.parse_args(argv)
    try:
        config = _json(args.config or args.state / "config.json")
        health = _json(args.health) if args.health else None
        native = _json(args.native) if args.native else None
        report = preflight(args.state, config, health=health, native=native)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
