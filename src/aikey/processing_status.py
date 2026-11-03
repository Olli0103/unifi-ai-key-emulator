"""Read-only operating counters, never native acceptance or billing evidence."""

import asyncio
from collections import Counter
from datetime import datetime, timezone
from html import escape
import re

from aiohttp import ClientError, web

from .admin_inventory import inventory_view
from .camera_inventory import InventoryError


SCHEMA = "aikey-processing-status/1"
_KEY = ("adopted", "connected", "search_connected", "queued", "active", "pending",
        "capacity", "ledger", "captions", "speech", "reverification", "deep",
        "inference_gate", "failure_notes", "caption_policy")
_PORT = ("adopted", "control_connected", "active_streams", "streams_with_decoded_frames",
         "detector_frames_attempted", "detector_frames_succeeded", "snapshot_uploads", "inference")
_CAMERA = ("ordinal", "stream_active", "policy_received", "enabled_types", "disabled", "pending",
           "attempts", "successes", "api_failures", "api_requests_remaining", "api_request_budget_healthy",
           "policy_smart_zones", "policy_exclude_zones", "policy_secondary_lens_zones",
           "stream_geometry", "stream_points", "stream_restart_attempts", "stream_restart_successes",
           "stream_deferred_starts", "stream_full_frames")


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

    @staticmethod
    def _names(report):
        rows = inventory_view(report, {})["cameras"]
        if len({row["mac"] for row in rows}) != len(rows):
            raise InventoryError("Ambiguous camera inventory")
        counts = Counter((row["name"] or "").strip().casefold() for row in rows)
        names = {}
        for row in rows:
            name = (row["name"] or "").strip()
            compact = re.sub(r"[:.\-\s]", "", name).casefold()
            if not name or row["mac"].casefold() in compact or row["id"].casefold() in name.casefold():
                names[row["mac"]] = (None, "name_unavailable")
            elif counts[name.casefold()] != 1:
                names[row["mac"]] = (None, "name_ambiguous")
            else:
                names[row["mac"]] = (name, "runtime_order_and_inventory")
        return names

    async def view(self):
        profiles = self.site._backup_profiles()
        observations = await asyncio.gather(*(
            self.api.runtime_view(profile) if kind == "aiport" else self.site.runtime_observer.read(profile)
            for profile, (kind, _) in profiles.items()))
        names, fetched_at = None, None
        if any(o.get("saved_order_matches_reported_runtime_order") is True
               and (o.get("runtime") or {}).get("cameras") for o in observations):
            try:
                # Include waiting for the shared observation lock in the bound.
                async with asyncio.timeout(12):
                    report = await self.site.inventory_snapshot()
                names = self._names(report)
                fetched_at = report["fetched_at"]
            except (InventoryError, ClientError, OSError, ValueError, TimeoutError):
                pass  # Names are optional; healthy runtime counters remain useful.
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
                    matches = observation.get("saved_order_matches_reported_runtime_order")
                    for camera, published in zip(cameras or [], selected["cameras"] or [], strict=True):
                        if matches is not True:
                            name, status = None, ("runtime_order_mismatch" if matches is False
                                                  else "runtime_order_unavailable")
                        elif names is None:
                            name, status = None, "inventory_unavailable"
                        else:
                            name, status = names.get(camera.get("saved_camera_mac"),
                                                     (None, "camera_not_in_inventory"))
                        published.update(camera_name=name, camera_name_observation=status)
            rows.append({"profile": profile, "kind": kind, "observation": observation["observation"],
                         "observed_at": observation.get("observed_at"), "runtime": selected})
        return {"processing_schema": SCHEMA, "read_only": True,
                "counter_scope": "current_process_not_event_or_charge_counts",
                "camera_name_scope": "runtime_declared_saved_order_and_inventory_not_native_pairing_proof",
                "camera_inventory_fetched_at": fetched_at,
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
                "<a href='/processing'>Refresh</a> · <a href='/caption-preflight'>Caption preflight</a> · "
                "<a href='/caption-policy'>Saved caption cost policy</a> · "
                "<a href='/caption-cameras'>Saved caption cameras</a></p>"
                "<p>Queues, streams and processing counters from your configured processors. "
                "Refresh to check again; this page changes no settings or services.</p>"
                "<details><summary>How to read these counters</summary><p>"
                "Each profile has its own observation time; health may be cached for two seconds. "
                "Unknown is not zero, offline or disabled. Counters reset with the processor process. "
                "Attempts and successes are not event, object, paid-request or quality counts: "
                "a successful attempt can return no detection.</p>"
                "<p>This page may read the configured camera inventory for names. It sends no model "
                "or media request, makes no controller changes, changes no settings "
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
        if result["camera_inventory_fetched_at"] is not None:
            observed = datetime.fromtimestamp(result["camera_inventory_fetched_at"], timezone.utc).isoformat()
            body += "<p>Camera names observed (UTC): " + _value(observed) + ".</p>"
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
                 "inference. Admission counters alone do not report the active policy or remaining "
                 "hourly permits. The running-policy observation below is separate from these counters. "
                 "Older workers do not report it. Caption preflight separately reads the saved policy and journal; "
                 "saved settings may differ from the running processor. Failed or uncertain "
                 "metered attempts are not refunded. Speech and Port requests use separate policies.</p>")
        policy = r.get("caption_policy") or {}
        modes = {"metered": "Metered continuous captions", "unmetered": "Explicit unmetered continuous captions",
                 "one_use": "One-use test scopes (not continuous)", "disabled": "Continuous captions disabled"}
        budget = policy.get("budget") or {}
        sampled = budget.get("sampled_at_ns")
        sampled = (datetime.fromtimestamp(sampled / 1_000_000_000, timezone.utc).isoformat()
                   if sampled is not None else None)
        body += _table("Running caption policy and sampled budget", [
            ("Active worker policy", modes.get(policy.get("mode"))),
            ("Durable journal observation", budget.get("observation") if policy.get("mode") == "metered"
             else "Not applicable" if policy else None),
            ("Journal sample time (UTC)", sampled),
            ("Global rolling window (seconds)", budget.get("window_seconds")),
            ("Global new-reservation limit", budget.get("limit")),
            ("Reservations in sampled hour", budget.get("used_reservations")),
            ("Unreserved slots at sample time", budget.get("remaining_reservations"))])
        body += ("<p>A sampled unreserved slot is not an admission promise or a monetary balance. "
                 "Fair sharing may hold slots for other cameras; concurrent work can consume them. "
                 "The processor may cache its journal sample for two seconds, in addition to this "
                 "page's health cache. An unavailable journal is not zero usage or permission to retry. "
                 "Loading this view never reserves, refunds, repairs or resets a budget.</p>")
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
                 "not saved inventory names or stable camera identities. Names are added only "
                 "when the running order agrees with saved camera settings and the current "
                 "inventory has an unambiguous name; this is not proof of native pairing.</p>"
                 "<p>Zone counts and types below are reported running policy state, not geometry, "
                 "proof of enforcement or recognition quality, and not an inside/outside-zone test. "
                 "Zone configuration belongs in Protect; this page cannot change it.</p>"
                 "<p>Open a camera for its counters. Stream size and frame rate are what Protect "
                 "requested, not measured camera throughput. Reserved capacity includes a stalled "
                 "decoder; recovery counters belong to this processor run and do not prove "
                 "recording playback or successful native detections. Full-frame samples belong "
                 "to the current decoder session and can reset on reconnect.</p>")
        reasons = {
            "runtime_order_and_inventory": "Name from the camera inventory; running order matches saved cameras.",
            "runtime_order_mismatch": "Name unavailable: saved camera order differs from the running processor.",
            "runtime_order_unavailable": "Name unavailable: this running processor does not provide a verified camera order.",
            "inventory_unavailable": "Name unavailable: camera inventory is not configured, unavailable or stale.",
            "camera_not_in_inventory": "Name unavailable: this saved camera is absent from the current inventory.",
            "name_unavailable": "Name unavailable: the inventory does not provide a usable camera name.",
            "name_ambiguous": "Name unavailable: the inventory contains duplicate camera names.",
        }
        for camera in r["cameras"]:
            title = "Runtime camera " + str(camera["ordinal"])
            if camera.get("camera_name") is not None:
                title += " · " + camera["camera_name"]
            body += ("<details class='processing-camera'><summary>" + escape(title)
                     + "<span class='muted'> — Stream " + _value(camera.get("stream_active"))
                     + " · Policy received " + _value(camera.get("policy_received"))
                     + " · Pending " + _value(camera.get("pending")) + "</span></summary>")
            body += "<p>" + reasons[camera["camera_name_observation"]] + "</p>"
            geometry = camera.get("stream_geometry")
            body += _table("Stream diagnostics · " + title, [
                ("Requested stream size", f"{geometry['width']} × {geometry['height']} px" if geometry else None),
                ("Requested frame rate", geometry["fps"] if geometry else None),
                ("Reserved capacity points", camera.get("stream_points")),
                ("Decoder restart attempts", camera.get("stream_restart_attempts")),
                ("Decoder restart successes", camera.get("stream_restart_successes")),
                ("Deferred starts", camera.get("stream_deferred_starts")),
                ("Full-frame samples (current decoder)", camera.get("stream_full_frames"))])
            types = camera.get("enabled_types")
            body += _table(title, [(label, camera.get(key)) for label, key in (
                ("Stream active", "stream_active"), ("Policy received", "policy_received"),
                ("Smart zones (reported)", "policy_smart_zones"),
                ("Exclusion zones (reported)", "policy_exclude_zones"),
                ("Secondary-lens zones (reported)", "policy_secondary_lens_zones"),
                ("Inference disabled", "disabled"), ("Pending inference", "pending"),
                ("Attempts", "attempts"), ("Successes", "successes"), ("API failures", "api_failures"),
                ("API budget healthy", "api_request_budget_healthy"),
                ("API attempts remaining", "api_requests_remaining"))]
                + [("Enabled types (reported)", None if types is None else ", ".join(types) or "None")])
            body += "</details>"
        return body
