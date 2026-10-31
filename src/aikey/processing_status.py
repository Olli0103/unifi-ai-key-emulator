"""Read-only operating counters, never native acceptance or billing evidence."""

import asyncio
from html import escape

from aiohttp import web


SCHEMA = "aikey-processing-status/1"
_KEY = ("adopted", "connected", "search_connected", "queued", "active", "pending",
        "capacity", "ledger", "captions", "speech", "reverification", "deep",
        "inference_gate", "failure_notes")
_PORT = ("adopted", "control_connected", "active_streams", "streams_with_decoded_frames",
         "detector_frames_attempted", "detector_frames_succeeded", "snapshot_uploads", "inference")
_CAMERA = ("ordinal", "stream_active", "policy_received", "enabled_types", "disabled", "pending",
           "attempts", "successes", "api_failures", "api_requests_remaining", "api_request_budget_healthy")


def _value(value):
    if value is None:
        return "Unknown"
    if type(value) is bool:
        return "Yes" if value else "No"
    return escape(str(value))


def _table(title, pairs):
    return ("<div class='scroll' role='region' aria-label='" + escape(title, quote=True)
            + "' tabindex='0'><table><caption>" + escape(title)
            + "</caption><thead><tr><th scope='col'>Measure</th><th scope='col'>Observation</th>"
            "</tr></thead><tbody>" + "".join("<tr><th scope='row'>" + escape(label)
            + "</th><td>" + _value(value) + "</td></tr>" for label, value in pairs)
            + "</tbody></table></div>")


class ProcessingStatus:
    def __init__(self, api):
        self.api, self.site = api, api.site

    def register(self, app):
        app.router.add_get("/processing", self.page)
        app.router.add_get("/api/admin/v1/processing", self.json)

    async def view(self):
        profiles = self.site._backup_profiles()
        observations = await asyncio.gather(*(
            self.site.runtime_observer.read(profile) for profile in profiles))
        rows = []
        for (profile, (kind, _)), observation in zip(profiles.items(), observations, strict=True):
            runtime = observation.get("runtime")
            selected = None
            if runtime is not None:
                selected = {key: runtime.get(key) for key in (_KEY if kind == "aikey" else _PORT)}
                if kind == "aiport":
                    cameras = runtime.get("cameras")
                    selected["cameras"] = ([{key: camera.get(key) for key in _CAMERA}
                                            for camera in cameras] if cameras is not None else None)
            rows.append({"profile": profile, "kind": kind, "observation": observation["observation"],
                         "observed_at": observation.get("observed_at"), "runtime": selected})
        return {"processing_schema": SCHEMA, "read_only": True,
                "counter_scope": "current_process_not_event_or_charge_counts",
                "runtime_cache_seconds": 2, "profiles": rows}

    async def json(self, request):
        from .admin_api import error, reply
        if not self.api.authenticated(request):
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        result = await self.view()
        if not self.api.authenticated(request):
            return error("authentication_required", 401)
        return reply(result)

    async def page(self, request):
        from .control_site import _page
        if not self.api.authenticated(request):
            raise web.HTTPSeeOther("/login")
        if request.query:
            raise web.HTTPBadRequest(text="This read-only page accepts no options.")
        result = await self.view()
        if not self.api.authenticated(request):
            raise web.HTTPSeeOther("/login")
        body = ("<h1>Processing and budgets</h1><p><a href='/'>Settings</a> · "
                "<a href='/processing'>Refresh</a> · <a href='/caption-preflight'>Caption preflight</a></p>"
                "<p>Queues, streams and processing counters from your configured processors. "
                "Refresh to check again; this page changes no settings or services.</p>"
                "<details><summary>How to read these counters</summary><p>"
                "Each profile has its own observation time; health may be cached for two seconds. "
                "Unknown is not zero, offline or disabled. Counters reset with the processor process. "
                "Attempts and successes are not event, object, paid-request or quality counts: "
                "a successful attempt can return no detection.</p>"
                "<p>This page sends no model, media or controller request, changes no settings "
                "and does not restart a service. Historical failures do not prove a current outage. "
                "A connected stream does not prove native recording, event delivery or recognition.</p></details>"
                "<style>.processing-overview{margin:1rem 0}"
                "@media(max-width:600px){.processing-overview thead{display:none}"
                ".processing-overview table,.processing-overview tbody,.processing-overview tr,"
                ".processing-overview th,.processing-overview td{display:block}"
                ".processing-overview tr{margin-bottom:1rem;border:1px solid #d9e2eb}"
                ".processing-overview td:before{content:attr(data-label);display:block;font-weight:600}"
                ".processing-overview th{font-size:1.1rem}}</style>"
                "<div class='scroll processing-overview' role='region' aria-label='Processing overview' tabindex='0'>"
                "<table><caption>Processor overview</caption><thead><tr><th scope='col'>Profile</th>"
                "<th scope='col'>Connection</th><th scope='col'>Work</th>"
                "<th scope='col'>Current consecutive provider failures</th></tr></thead><tbody>")
        for row in result["profiles"]:
            r = row["runtime"]
            label = "AI Key" if row["kind"] == "aikey" else "AI Port" + (
                " · " + row["profile"][7:] if row["profile"].startswith("aiport:") else "")
            if r is None:
                connection, work, failures = "Unknown", "Unknown", "Unknown"
            elif row["kind"] == "aikey":
                connection = _value(r["connected"])
                work = _value(r["queued"]) + " queued jobs; " + _value(r["active"]) + " active jobs"
                failures = "Not reported"
            else:
                connection = _value(r["control_connected"])
                work = _value(r["active_streams"]) + " active streams; " + _value(
                    (r.get("inference") or {}).get("pending_cameras")) + " pending cameras"
                failures = _value((r.get("inference") or {}).get("api_consecutive_provider_failures"))
            body += ("<tr><th scope='row'>" + escape(label) + "</th><td data-label='Connection'>" + connection
                     + "</td><td data-label='Work'>" + work
                     + "</td><td data-label='Consecutive provider failures'>" + failures + "</td></tr>")
        body += "</tbody></table></div>"
        for row in result["profiles"]:
            title = "AI Key" if row["kind"] == "aikey" else "AI Port" + (
                " · " + row["profile"][7:] if row["profile"].startswith("aiport:") else "")
            body += "<section><h2>" + escape(title) + "</h2>"
            if row["runtime"] is None:
                message = ("No runtime target configured. Status unknown." if
                           row["observation"] == "not_configured" else
                           "Runtime observation unavailable. Status unknown; check the processor and its trusted health route.")
                body += "<p>" + message + "</p></section>"
                continue
            body += ("<p>Observed: " + _value(row["observed_at"])
                     + "</p><details><summary>Processing details for " + escape(title) + "</summary>")
            r = row["runtime"]
            if row["kind"] == "aikey":
                body += self._key(r)
            else:
                body += self._port(r)
            body += "</details></section>"
        return _page("Processing and budgets", body)

    @staticmethod
    def _key(r):
        body = _table("AI Key work queue", [(label, r.get(key)) for label, key in (
            ("Adopted", "adopted"), ("Control connected", "connected"),
            ("Search connected", "search_connected"), ("Queued jobs", "queued"),
            ("Active jobs (worker-reported)", "active"), ("Pending jobs", "pending"),
            ("Queue capacity", "capacity"), ("In-memory journal entries", "ledger"))])
        gate = r.get("inference_gate") or {}
        body += _table("AI Key model queue", [(label, gate.get(key)) for label, key in (
            ("Inference capacity", "capacity"), ("Active inference", "active"),
            ("Waiting inference", "waiting"), ("Waiting on-demand summaries", "on_demand_waiting"),
            ("Active caption jobs", "captions_active"), ("Waiting caption jobs", "captions_waiting"))])
        captions = r.get("captions") or {}
        body += ("<h3>Caption admission</h3><p>When enabled, the metered continuous-caption policy "
                 "allows 12 new attempts per rolling hour globally, not per camera. An explicit "
                 "local unmetered override can bypass that cap; local routing is not proof of free "
                 "inference. These process counters do not report the active policy or remaining "
                 "hourly permits. Caption preflight separately reads the saved policy and journal; "
                 "saved settings may differ from the running processor. Failed or uncertain "
                 "metered attempts are not refunded. Speech and Port requests use separate policies.</p>")
        body += _table("Caption process counters", [(label, captions.get(key)) for label, key in (
            ("New metered attempts admitted", "admitted"), ("Budget-exhausted refusals", "exhausted"),
            ("Deferred for other cameras", "deferred_fair_share"))])
        notes = r.get("failure_notes") or {}
        counts = notes.get("current_process") or {}
        body += ("<h3>Failure-note publication</h3><p>Counts of attempts to save a historical "
                 "exception note, not failed jobs, current outcomes or unique events. No old "
                 "failure cause is reconstructed. Busy, full or unavailable storage can leave "
                 "a note missing; review private processor diagnostics without replaying work.</p>")
        body += _table("Failure-note storage counters", [(label, counts.get(key)) for label, key in (
            ("Notes written", "written"), ("Already recorded", "already_recorded"),
            ("Storage busy", "busy"), ("Storage full", "capacity"), ("Storage unavailable", "unavailable"))])
        speech, deep, verify = (r.get(k) or {} for k in ("speech", "deep", "reverification"))
        return body + _table("Additional Key processing counters", [
            ("Speech clips refused as too long", speech.get("refused_long")),
            ("Speech clips shortened", speech.get("clipped")),
            ("Deep description tasks", deep.get("describe_tasks")),
            ("Deep descriptions produced", deep.get("described")),
            ("Deep crops failed", deep.get("crops_failed")),
            ("Reverification confirmed", verify.get("confirmed")),
            ("Reverification unchanged", verify.get("unchanged"))])

    @staticmethod
    def _port(r):
        pool = r.get("inference") or {}
        cap = ("Not configured" if pool.get("api_request_cap_reported") and
               pool.get("api_request_cap") is None else pool.get("api_request_cap"))
        body = _table("AI Port processing counters", [(label, r.get(key)) for label, key in (
            ("Adopted", "adopted"), ("Control connected", "control_connected"),
            ("Active streams", "active_streams"), ("Streams decoding frames", "streams_with_decoded_frames"),
            ("Detector frames attempted", "detector_frames_attempted"),
            ("Detector frames succeeded", "detector_frames_succeeded"), ("Snapshot uploads", "snapshot_uploads"))]
            + [(label, pool.get(key)) for label, key in (
            ("Pool attempts", "attempts"), ("Pool successes", "successes"),
            ("Dropped/coalesced frames", "dropped_frames"), ("Failed cameras", "failed_cameras"),
            ("API failures", "api_failures"), ("Pending cameras", "pending_cameras"),
            ("Model load failed", "model_load_failed"), ("Inference closed", "closed"),
            ("API provider failures", "api_provider_failures"),
            ("Consecutive provider failures", "api_consecutive_provider_failures"),
            ("API backoff skips", "api_backoff_skips"))] + [("API attempt cap per camera / rolling hour", cap)])
        body += ("<p>The optional API-attempt cap is separate from the Key caption budget and "
                 "does not cap native object events. A missing cap is not proof of no cost. "
                 "Remaining permits are the runtime's latest per-camera snapshot, not a new "
                 "reservation or guaranteed admission.</p>")
        if r.get("cameras") is None:
            return body + "<p>Per-camera runtime observations unknown.</p>"
        if not r["cameras"]:
            return body + "<p>No per-camera rows reported by this runtime.</p>"
        body += ("<p>Camera numbers below are positions in this runtime's current stream order, "
                 "not saved inventory names or stable camera identities.</p>")
        for camera in r["cameras"]:
            body += _table("Runtime camera " + str(camera["ordinal"]), [(label, camera.get(key)) for label, key in (
                ("Stream active", "stream_active"), ("Policy enabled", "policy_received"),
                ("Inference disabled", "disabled"), ("Pending inference", "pending"),
                ("Attempts", "attempts"), ("Successes", "successes"), ("API failures", "api_failures"),
                ("API budget healthy", "api_request_budget_healthy"),
                ("API attempts remaining", "api_requests_remaining"))])
        return body
