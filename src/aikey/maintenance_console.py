"""Server-rendered maintenance forms over the constrained, shared API backend."""

import asyncio
from html import escape
import hashlib
import hmac
import json

from aiohttp import web

from .admin_maintenance import MaintenanceError, _identifier, _REQUEST
from .config_store import ConfigurationStoreError


_DESCRIPTIONS = {
    "device_file_backup": ("Back up device files", "Exports private device files. The host must arrange a cold window first. External models and the search database are not included."),
    "device_file_restore": ("Restore device files offline", "Restores into a new inactive directory. It does not overwrite or activate a device."),
    "search_index_backup": ("Back up and verify the search index", "Creates a private search backup and verifies a restore in an isolated scratch database."),
    "search_index_restore": ("Verify a search-index restore", "Restores only into an isolated scratch database, never over the live index."),
    "apple_image_update": ("Update the Apple-hosted device", "Uses the host-approved image and retained recovery plan. Review the target digest below."),
}
_STATES = {
    "not_reported": "No host result reported",
    "started": "Host reports started; outcome not settled",
    "succeeded": "Host reported success",
    "rejected": "Host rejected the operation",
    "rolled_back": "Host reported rollback",
    "uncertain": "Outcome uncertain — host reconciliation required",
}
_UNRESOLVED = {"not_reported", "started", "uncertain"}


def _profile(value):
    return "AI Key" if value == "aikey" else "AI Port" + (" · " + value[7:] if value.startswith("aiport:") else "")


def request_id(cookie, operation, revision):
    """Stable per session/plan: double-clicks reuse the same correlation ID."""
    return hmac.new(cookie.encode(), ("maintenance-form/1\0" + operation + "\0" + revision).encode(),
                    hashlib.sha256).hexdigest()[:32]


def _page(body, status=200):
    from .control_site import _page as site_page
    result = site_page("Maintenance", "<div class='maintenance'><h1>Maintenance</h1>"
        "<p><a href='/'>Settings</a> · <a href='/maintenance'>Refresh</a></p>" + body + "</div>")
    result.set_status(status)
    return result


def _error(message, status, identifier=None):
    link = (f"<p><a href='/maintenance/requests/{identifier}'>Check this request</a></p>"
            if identifier is not None else "")
    return _page("<p role='alert'>" + message + "</p>" + link, status)


def _redirect(identifier):
    return web.Response(status=303, headers={"Location": "/maintenance/requests/" + identifier,
                                            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


class MaintenanceConsole:
    def __init__(self, api):
        self.api = api
        self.site = api.site
        self._admission_lock = asyncio.Lock()

    def register(self, app):
        app.router.add_get("/maintenance", self.index)
        app.router.add_post("/maintenance", self.submit)
        app.router.add_get("/maintenance/requests/{request_id}", self.receipt)

    async def snapshot(self):
        if self.site.maintenance is None:
            return None
        def read():
            catalog = self.site.maintenance.catalog()
            revisions = {row["profile"]: self.api.view(row["profile"], self.api.store(row["profile"]))["revision"]
                         for row in catalog if self.api.store(row["profile"]) is not None}
            return catalog, self.site.maintenance.history(), revisions
        return await asyncio.to_thread(read)

    def supported(self, row):
        return (self.site.audit is not None and row.get("purpose") in _DESCRIPTIONS
                and self.api.store(row["profile"]) is not None
                and (row["purpose"] != "apple_image_update" or "target_image" in row))

    async def index(self, request):
        cookie = self.api.authenticated(request)
        if not cookie:
            raise web.HTTPSeeOther("/login")
        if request.query:
            return _error("This page does not accept a target or other query options.", 400)
        try:
            view = await self.snapshot()
        except (MaintenanceError, ConfigurationStoreError, OSError, ValueError):
            return _error("Maintenance records are unavailable. Viewing this page has not submitted an operation.", 503)
        if not self.api.authenticated(request):
            raise web.HTTPSeeOther("/login")
        if view is None:
            return _page("<h2>No host maintenance connection</h2><p>No operations can be submitted "
                         "from this installation. Nothing is queued by viewing this page.</p>")
        catalog, history, revisions = view
        catalog = [row for row in catalog if self.api.store(row["profile"]) is not None]
        blocked = any(row["state"] in _UNRESOLVED for row in history)
        body = ("<p>Only operations reviewed and published by the host are offered. "
                "Submission does not start a host worker; its availability is not known here. "
                "A submitted request is not proof of execution or success.</p>")
        if self.site.audit is None:
            body += "<p role='alert'>Admission auditing is unavailable. No new operations are offered.</p>"
        if blocked:
            body += ("<p role='status'>An earlier request has no settled outcome. New submissions "
                     "are disabled here; check its result and have the host reconcile it. "
                     "No automatic retry is performed.</p>")
        body += "<h2>Host-approved operations</h2>"
        if not catalog:
            body += "<p>No plans have been approved for these profiles. Backup, restore and update are not currently offered.</p>"
        csrf = self.site.security.csrf_token(cookie)
        for row in catalog:
            operation, revision = row["operation"], row["revision"]
            title, detail = _DESCRIPTIONS.get(row.get("purpose"), (row["kind"].title(),
                "This older or custom plan has no reviewable built-in scope. It cannot be submitted from this console."))
            used = [r for r in history if r["operation"] == operation]
            body += ("<fieldset><legend>" + escape(title) + " — " + escape(_profile(row["profile"])) + "</legend>"
                     "<p>" + detail + "</p><p>One use. Maximum host job time: " + str(row["timeout"]) + " seconds.</p>"
                     "<details><summary>Reviewed plan and configuration revision</summary><dl>"
                     "<dt>Plan</dt><dd><code>" + operation + "</code></dd><dt>Revision</dt><dd><code>" + revision + "</code></dd></dl></details>")
            if "target_image" in row:
                body += "<p>Target image: <code>" + row["target_image"] + "</code></p>"
            if used:
                body += "<p>Already submitted. This one-use plan is not offered again.</p>"
                for previous in used:
                    body += (f"<p><a href='/maintenance/requests/{previous['request']}'>"
                             + escape(_STATES[previous["state"]]) + "</a></p>")
            elif not self.supported(row):
                body += "<p>Not available here: a fully described built-in plan is required.</p>"
            elif revisions.get(row["profile"]) != revision:
                body += "<p>Saved settings no longer match this plan. A freshly reviewed host plan is required.</p>"
            elif blocked:
                body += "<p>Unavailable until the earlier request is reconciled.</p>"
            elif csrf is not None:
                identifier = request_id(cookie, operation, revision)
                body += ("<form method='post' action='/maintenance'>"
                         f"<input type='hidden' name='csrf' value='{escape(csrf, quote=True)}'>"
                         f"<input type='hidden' name='operation' value='{operation}'>"
                         f"<input type='hidden' name='revision' value='{revision}'>"
                         f"<input type='hidden' name='request' value='{identifier}'>"
                         "<label><input type='checkbox' name='confirm' value='queue' required> "
                         "I reviewed this one-use operation and its scope.</label>"
                         "<p><button type='submit'>Submit " + escape(title.lower()) + "</button></p></form>")
            body += "</fieldset>"
        body += "<h2>Submitted requests</h2>"
        if not history:
            body += "<p>No requests have been submitted. Nothing is queued by viewing this page.</p>"
        else:
            body += ("<div class='scroll' role='region' aria-label='Submitted maintenance requests' tabindex='0'>"
                     "<table><thead><tr><th scope='col'>Request</th><th scope='col'>Host result</th></tr></thead><tbody>")
            for row in history:
                body += (f"<tr><td><a href='/maintenance/requests/{row['request']}'><code>"
                         + row["request"] + "</code></a></td><td>" + escape(_STATES[row["state"]]) + "</td></tr>")
            body += "</tbody></table></div>"
        return _page(body)

    async def receipt(self, request):
        if not self.api.authenticated(request):
            raise web.HTTPSeeOther("/login")
        try:
            identifier = _identifier(request.match_info["request_id"], _REQUEST)
            if request.query:
                raise ValueError
        except (MaintenanceError, ValueError):
            return _error("Use a valid submitted request identifier without query options.", 400)
        try:
            rows = (await asyncio.to_thread(self.site.maintenance.history)
                    if self.site.maintenance is not None else [])
        except (MaintenanceError, OSError, ValueError):
            return _error("Maintenance records are unavailable. No result is inferred.", 503)
        if not self.api.authenticated(request):
            raise web.HTTPSeeOther("/login")
        row = next((row for row in rows if row["request"] == identifier), None)
        if row is None:
            return _error("No submitted request with this identifier is recorded here. Do not infer success or retry it automatically.", 404)
        return _page("<h2>Request result</h2><p><code>" + identifier + "</code></p>"
                     "<p role='status'>" + escape(_STATES[row["state"]]) + "</p>"
                     "<p>This page only reads the retained host result. It does not start, repeat, "
                     "cancel or reconcile an operation. Host-worker availability is not asserted.</p>"
                     "<p>Automatic retry is disabled. An uncertain result requires host reconciliation; "
                     "a one-use plan is not reset here.</p>")

    async def submit(self, request):
        cookie = self.api.authenticated(request)
        if not cookie:
            return _error("The session is invalid. No request was submitted here.", 403)
        if request.query or request.content_type != "application/x-www-form-urlencoded":
            return _error("Use the reviewed maintenance form. No request was submitted here.", 400)
        try:
            fields = await request.post()
            expected = {"csrf", "operation", "revision", "request", "confirm"}
            if (set(fields) != expected or any(len(fields.getall(k)) != 1 for k in expected)
                    or any(not isinstance(v, str) for v in fields.values()) or fields["confirm"] != "queue"):
                raise ValueError
            if not self.site._session(request, mutate=True, csrf=fields["csrf"]):
                return _error("The form authorization expired. Refresh before choosing an operation.", 403)
            payload = {k: fields[k] for k in ("operation", "revision", "request")}
            _identifier(payload["operation"])
            _identifier(payload["revision"])
            _identifier(payload["request"], _REQUEST)
            if not hmac.compare_digest(payload["request"], request_id(cookie, payload["operation"], payload["revision"])):
                return _error("This review belongs to another session or plan. Refresh the page.", 409)
            view = await self.snapshot()
        except web.HTTPRequestEntityTooLarge:
            return _error("The maintenance form is too large. No request was submitted here.", 413)
        except (MaintenanceError, ConfigurationStoreError, OSError, ValueError):
            return _error("The reviewed maintenance plan is unavailable. No new request was submitted here.", 400)
        if not self.api.authenticated(request):
            return _error("The session expired before admission. No request was submitted here.", 403)
        if view is None:
            return _error("No host maintenance connection is configured.", 503)
        catalog, history, revisions = view
        row = next((r for r in catalog if r["operation"] == payload["operation"]), None)
        if (row is None or not self.supported(row) or row["revision"] != payload["revision"]
                or revisions.get(row["profile"]) != payload["revision"]):
            return _error("This plan is no longer offered. Refresh before choosing an operation.", 409)
        existing = next((r for r in history if r["request"] == payload["request"]), None)
        if existing:
            if all(existing[k] == payload[k] for k in ("operation", "revision")):
                return _redirect(payload["request"])
            return _error("This request identifier is already in use. No new request was submitted here.", 409)
        if any(r["operation"] == payload["operation"] or r["state"] in _UNRESOLVED for r in history):
            return _error("A prior submission must be checked or reconciled. This form will not repeat it.", 409)
        result = await self.api.file_operation(self.queue_once, payload, row["profile"], request)
        if result.status == 202:
            return _redirect(payload["request"])
        code = json.loads(result.text)["error"]
        if code == "outcome_uncertain":
            return _error("Submission has an uncertain outcome. Check this exact request; do not resubmit automatically.", 503, payload["request"])
        if code == "audit_unavailable":
            return _error("The admission audit could not be recorded. No new request was submitted here.", 503)
        return _error("The reviewed request was not accepted. Check existing results before trying another plan.", result.status)


    async def queue_once(self, payload, profile, request):
        # Keep the check and durable publication owned together even if an HTTP
        # waiter disconnects. Two different forms cannot both pass an empty view.
        async with self._admission_lock:
            try:
                history = await asyncio.to_thread(self.site.maintenance.history)
            except (MaintenanceError, OSError, ValueError):
                from .admin_api import error
                return error("maintenance_unavailable", 503)
            if not self.api.authenticated(request):
                from .admin_api import error
                return error("forbidden", 403)
            existing = next((r for r in history if r["request"] == payload["request"]), None)
            if existing and all(existing[k] == payload[k] for k in ("operation", "revision")):
                from .admin_api import reply
                return reply({"state": "already_submitted"}, status=202)
            if any(r["operation"] == payload["operation"] or r["state"] in _UNRESOLVED for r in history):
                from .admin_api import error
                return error("maintenance_plan_conflict", 409)
            return await self.api.queue_maintenance(payload, profile)
