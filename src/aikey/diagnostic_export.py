"""Authenticated small allowlist export, never a raw private support bundle."""

import asyncio
from datetime import datetime, timezone
import json

from aiohttp import web

from .admin_api import _HEADERS, error
from .control_site import _page

SCHEMA = "aikey-redacted-diagnostics/1"
FILENAME = "aikey-redacted-diagnostics.json"
_KEY_COUNTS = ("queued", "active", "pending", "capacity", "ledger")
_PORT_COUNTS = ("active_streams", "streams_with_decoded_frames", "detector_frames_attempted",
                "detector_frames_succeeded", "snapshot_uploads")
_INFERENCE_COUNTS = ("attempts", "successes", "dropped_frames", "failed_cameras",
                     "api_failures", "pending_cameras", "api_provider_failures", "api_backoff_skips")
_CAPTION_COUNTS = ("admitted", "exhausted", "deferred_fair_share")
_CAPTION_SCOPE = "continuous_captions_not_speech_port_or_all_paid_calls"


def _flag(value, field):
    selected = value.get(field)
    if selected is not None and type(selected) is not bool:
        raise ValueError
    return selected


def _counts(value, fields):
    if not isinstance(value, dict):
        raise ValueError
    result = {}
    for field in fields:
        selected = value.get(field)
        if selected is not None and (type(selected) is not int or not 0 <= selected <= 2**63-1):
            raise ValueError
        result[field] = selected
    return result


def _profile(kind, ordinal, observation):
    """Second strict projection; private labels/revisions/nested extras never pass."""
    row = {"kind": kind, "ordinal": ordinal, "observation": "unavailable",
           "tls_ca_and_leaf_pin_verified": None, "runtime": None}
    if not isinstance(observation, dict):
        return row
    state = observation.get("observation")
    if state == "not_configured":
        row["observation"] = state
        return row
    if state != "runtime_health" or observation.get("tls_ca_and_leaf_pin_verified") is not True:
        return row
    runtime = observation.get("runtime")
    try:
        if not isinstance(runtime, dict):
            raise ValueError
        selected = {"adopted": _flag(runtime, "adopted")}
        if kind == "aikey":
            selected.update(connected=_flag(runtime, "connected"),
                            search_connected=_flag(runtime, "search_connected"))
            selected.update(_counts(runtime, _KEY_COUNTS))
            captions = runtime.get("captions")
            selected["captions"] = _counts(captions, _CAPTION_COUNTS) if captions is not None else None
            policy = runtime.get("caption_policy")
            mode = policy.get("mode") if isinstance(policy, dict) and policy.get("scope") == _CAPTION_SCOPE else None
            selected["caption_policy_mode"] = (mode if isinstance(mode, str) and mode in
                {"metered", "unmetered", "one_use", "disabled"} else None)
        else:
            selected["control_connected"] = _flag(runtime, "control_connected")
            selected.update(_counts(runtime, _PORT_COUNTS))
            inference = runtime.get("inference")
            selected["inference"] = _counts(inference, _INFERENCE_COUNTS) if inference is not None else None
    except ValueError:
        return row
    return {**row, "observation": "runtime_health", "tls_ca_and_leaf_pin_verified": True, "runtime": selected}


class DiagnosticExport:
    def __init__(self, api):
        self.api, self.site = api, api.site
        self.collecting = False
        self.deadline_seconds = 6

    def register(self, app):
        app.router.add_get("/diagnostics", self.page)
        app.router.add_get("/api/admin/v1/diagnostics", self.download, allow_head=False)

    async def page(self, request):
        if not self.api.authenticated(request):
            raise web.HTTPSeeOther("/login")
        if request.query:
            raise web.HTTPBadRequest()
        return _page("Redacted diagnostics", "<h1>Redacted diagnostics</h1>"
            "<p><a href='/'>Settings</a> · <a href='/processing'>Private processing status</a></p>"
            "<p>Download a small JSON snapshot of registered Admin profile kinds, pinned runtime availability "
            "and allowlisted runtime-observed counters. Profiles use anonymous ordinals within the file, "
            "not your saved names or native identifiers.</p>"
            "<p>No camera records or names, device identities, model names, URLs, addresses, paths, credentials, "
            "configuration contents, revisions, logs, raw errors or media are included. Only the existing "
            "configured own HTTPS health readers are used: no controller or provider request, model job, "
            "restart, host command or saved-setting change.</p>"
            "<p>Unknown is not zero; an unconfigured or unavailable health reader does not prove a device "
            "is offline. Counters are not event persistence, model quality, billing or native compatibility "
            "evidence. This is not a host/dependency audit or a file/log collection.</p>"
            "<p>Collection waits at most six seconds for observations. A concurrent export is refused "
            "instead of queueing another collection. No automatic upload or external sharing.</p>"
            f"<p><a href='/api/admin/v1/diagnostics' download='{FILENAME}'>Download redacted JSON</a></p>")

    async def download(self, request):
        if not self.api.authenticated(request):
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        if self.collecting:
            response = error("diagnostics_busy", 429)
            response.headers["Retry-After"] = "2"
            return response
        # No await between observing and claiming the one event-loop-owned slot.
        self.collecting = True
        try:
            profiles = self.site._backup_profiles()
            if (not isinstance(profiles, dict) or len(profiles) > 32
                    or any(not isinstance(v, tuple) or len(v) != 2
                           or not isinstance(v[0], str) or v[0] not in {"aikey", "aiport"}
                           for v in profiles.values())):
                raise ValueError
            tasks = []
            try:
                async with asyncio.timeout(self.deadline_seconds):
                    for profile in profiles:
                        tasks.append(asyncio.create_task(self.site.runtime_observer.read(profile)))
                    observations = await asyncio.gather(*tasks)
            finally:
                # gather's ordinary failure does not cancel its surviving siblings.
                # Keep ownership until every admitted reader task has settled.
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            counts, rows = {"aikey": 0, "aiport": 0}, []
            for (_, (kind, _)), observation in zip(profiles.items(), observations, strict=True):
                counts[kind] += 1
                rows.append(_profile(kind, counts[kind], observation))
            value = {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc).isoformat(),
                "read_only": True,
                "scope": "registered_admin_profiles_current_runtime_observations_not_native_acceptance",
                "counter_scope": "runtime_observations_not_event_or_charge_counts", "runtime_cache_seconds": 2,
                "profile_counts": counts, "profiles": rows}
            body = (json.dumps(value, allow_nan=False, sort_keys=True, indent=2) + "\n").encode()
            if len(body) > 65536:
                raise ValueError
        except (ValueError, OSError, TimeoutError, TypeError):
            if not self.api.authenticated(request):
                return error("authentication_required", 401)
            return error("diagnostics_unavailable", 503)
        finally:
            self.collecting = False
        if not self.api.authenticated(request):
            return error("authentication_required", 401)
        return web.Response(body=body, content_type="application/json", headers={**_HEADERS,
                            "Content-Disposition": f'attachment; filename="{FILENAME}"'})
