"""Reviewed saved processing bounds; no worker/process/provider actions."""

import asyncio
import hashlib
import hmac
import re
import secrets
import time

from aiohttp import web

from .config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from .control_site import _page, _safe
from .worker_limits import LIMITS, proposed_limits

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_NOTICE = (
    "<p><strong>Saved settings only.</strong> A separate AI Key restart is required. "
    "This page never restarts or drains a processor, cancels or replays a job, "
    "or sends media or model requests. Saved values are not running values; "
    "<a href='/runtime-status'>compare runtime configuration</a>.</p>"
    "<p>More slots or longer deadlines can increase load, provider charges and waiting. "
    "A larger queue does not speed up inference. Job deadlines include queue waiting; "
    "a caption is also bounded by the general deadline and any shorter controller budget. "
    "Speech has a separate request deadline, not a guaranteed total completion time. "
    "One worker slot leaves no separate worker reserved for urgent summaries. "
    "These settings do not repair a stalled backend or prove performance.</p>"
    "<p>Camera permissions, caption cost policy and usage, providers, credentials, "
    "identity, search indexes and AI Ports stay unchanged. A finite model-request gate "
    "is required here; this editor cannot remove it. Speech and other separate model paths are not bounded by this vision gate. "
    "Other existing worker settings are preserved.</p>"
)


class WorkerLimitsControl:
    def __init__(self, site):
        self.site, self.grants, self.clock = site, {}, time.monotonic

    def register(self, app):
        app.router.add_get("/worker-limits", self.page)
        app.router.add_get("/api/admin/v1/worker-limits", self.json)
        app.router.add_post("/worker-limits/preview", self.preview)
        app.router.add_post("/worker-limits/apply", self.apply)

    @staticmethod
    def _fields(fields, expected):
        return set(fields) == expected and all(
            isinstance(v, str) and len(fields.getall(k)) == 1 for k, v in fields.items()
        )

    @staticmethod
    def _hidden(fields):
        return "".join(
            f"<input type='hidden' name='{_safe(k)}' value='{_safe(v)}'>" for k, v in fields.items()
        )

    def _expire(self):
        for token, grant in list(self.grants.items()):
            if self.clock() >= grant["expires"]:
                del self.grants[token]

    def _refused(self):
        self.site._audit("worker_limits", "rejected", "aikey")
        return _page(
            "Processing limits not saved",
            "<h1>Processing limits not saved</h1>"
            "<p>Reload saved settings and review a fresh preview. This request changed no settings.</p>"
            "<p><a href='/worker-limits'>Processing limits</a></p>",
            status=409,
        )

    async def json(self, request):
        from .admin_api import error, reply

        if not self.site.admin_api.authenticated(request):
            return error("authentication_required", 401)
        if request.query:
            return error("invalid_request", 400)
        try:
            status = await asyncio.to_thread(self.site.aikey.worker_limits_status)
        except (ValueError, OSError, ConfigurationStoreError):
            status = None
        if not self.site.admin_api.authenticated(request):
            return error("authentication_required", 401)
        return (
            error("worker_limits_unavailable", 503)
            if status is None
            else reply({"saved_only": True, "restart_required_for_changes": True, **status})
        )

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther("/login")
        if request.query:
            raise web.HTTPBadRequest()
        body = (
            "<h1>AI Key processing limits</h1><p><a href='/'>Settings</a> · <a href='/processing'>Processing status</a></p>"
            + _NOTICE
        )
        try:
            status = await asyncio.to_thread(self.site.aikey.worker_limits_status)
        except (ValueError, OSError, ConfigurationStoreError):
            status = None
        if self.site._session(request) is None:
            raise web.HTTPSeeOther("/login")
        if status is None:
            return _page(
                "Processing limits",
                body
                + "<p>Saved values unavailable or outside this editor’s bounds. Nothing can be saved here.</p>",
            )
        body += "<form method='post' action='/worker-limits/preview'>" + self._hidden(
            {"csrf": self.site.security.csrf_token(cookie), "revision": status["revision"]}
        )
        for name, (label, _, low, high) in LIMITS.items():
            value = status["limits"][name]
            body += (
                f"<label for='limit-{name}'>{_safe(label)}</label>"
                f"<input id='limit-{name}' name='{name}' type='number' min='{low}' max='{high}' step='1' required "
                f"value='{'' if value is None else value}'>"
            )
            if value is None:
                body += "<p>No model-request gate is saved. Choose a finite limit no greater than worker slots; leaving it empty cannot save.</p>"
        return _page(
            "Processing limits", body + "<button>Preview processing limits</button></form>"
        )

    async def preview(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (
                request.query
                or not self._fields(fields, {"csrf", "revision"} | set(LIMITS))
                or not _HEX.fullmatch(fields["revision"])
                or any(not re.fullmatch(r"[0-9]{1,4}", fields[k]) for k in LIMITS)
            ):
                raise ValueError
            values = proposed_limits({k: int(fields[k]) for k in LIMITS})
            before = await asyncio.to_thread(self.site.aikey.worker_limits_status)
            if before["revision"] != fields["revision"]:
                raise ValueError
            result = await asyncio.to_thread(
                self.site.aikey.preview_worker_limits, fields["revision"], values
            )
        except (ValueError, OSError, ConfigurationStoreError):
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        self._expire()
        if len(self.grants) >= 32:
            return self._refused()
        token = secrets.token_hex(32)
        self.grants[token] = {
            "session": hashlib.sha256(cookie.encode()).hexdigest(),
            "revision": fields["revision"],
            "values": values,
            "resulting_revision": result.resulting_revision,
            "expires": self.clock() + 600,
        }
        self.site._audit("worker_limits", "previewed", "aikey")
        rows = "".join(
            f"<tr><th scope='row'>{_safe(spec[0])}</th><td>{'No gate' if before['limits'][k] is None else before['limits'][k]}</td><td>{values[k]}</td></tr>"
            for k, spec in LIMITS.items()
        )
        return _page(
            "Review processing limits",
            "<h1>Review processing limits</h1><p>Nothing has been saved.</p>"
            + _NOTICE
            + "<div class='scroll' role='region' aria-label='Reviewed processing limits' tabindex='0'><table><caption>Saved limits before and after</caption>"
            "<thead><tr><th scope='col'>Setting</th><th scope='col'>Before</th><th scope='col'>After</th></tr></thead><tbody>"
            + rows
            + "</tbody></table></div>"
            "<form method='post' action='/worker-limits/apply'>"
            + self._hidden({"csrf": self.site.security.csrf_token(cookie), "grant": token})
            + "<label><input type='checkbox' name='acknowledge_load' value='yes' required>I accept the reviewed load, waiting and possible provider-cost consequences.</label>"
            "<label><input type='checkbox' name='confirm' value='yes' required>Save these reviewed limits only; the running processor stays unchanged.</label>"
            "<button>Confirm saved processing limits</button></form><p><a href='/worker-limits'>Cancel</a></p>",
        )

    async def apply(self, request):
        fields = await request.post()
        cookie = self.site._session(request, mutate=True, csrf=fields.get("csrf"))
        if cookie is None:
            raise web.HTTPForbidden()
        try:
            if (
                request.query
                or not self._fields(fields, {"csrf", "grant", "acknowledge_load", "confirm"})
                or not _HEX.fullmatch(fields["grant"])
                or fields["confirm"] != "yes"
                or fields["acknowledge_load"] != "yes"
            ):
                raise ValueError
            self._expire()
            grant = self.grants.get(fields["grant"])
            if grant is None or not hmac.compare_digest(
                grant["session"], hashlib.sha256(cookie.encode()).hexdigest()
            ):
                raise ValueError
            result = await asyncio.to_thread(
                self.site.aikey.preview_worker_limits, grant["revision"], grant["values"]
            )
            if (
                result.resulting_revision != grant["resulting_revision"]
                or self.clock() >= grant["expires"]
                or self.grants.get(fields["grant"]) is not grant
            ):
                raise ValueError
        except (ValueError, OSError, ConfigurationStoreError):
            return self._refused()
        if self.site._session(request) is None:
            raise web.HTTPForbidden()
        del self.grants[fields["grant"]]
        return await self.site.admin_api.file_operation(self._commit, grant)

    async def _commit(self, grant):
        try:
            result = await asyncio.to_thread(
                self.site.aikey.apply_worker_limits, grant["revision"], grant["values"]
            )
            if result.revision != grant["resulting_revision"]:
                raise ConfigurationStoreError("Worker limits acknowledgement uncertain")
        except (RevisionConflict, UnsafeConfigurationChange):
            return self._refused()
        except (OSError, ConfigurationStoreError):
            self.site._audit("worker_limits", "uncertain", "aikey")
            return _page(
                "Processing limits outcome uncertain",
                "<h1>Processing limits outcome uncertain</h1>"
                "<p>Reload and reconcile saved settings before trying again. The save may have succeeded. No automatic retry or reversal was attempted.</p>"
                "<p><a href='/worker-limits'>Read saved processing limits</a></p>",
                status=503,
            )
        self.site._audit("worker_limits", "saved", "aikey")
        return _page(
            "Processing limits saved",
            "<h1>Processing limits saved</h1>"
            "<p>The running AI Key is unchanged. A separate processor restart is required. No existing usage was reset or job replayed.</p>"
            "<p><a href='/worker-limits'>Read saved processing limits</a></p>",
        )
