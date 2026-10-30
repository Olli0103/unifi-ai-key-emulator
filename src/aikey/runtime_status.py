"""Read-only saved/startup comparison; never a service-activation control."""

import asyncio
from html import escape

from aiohttp import web


class RuntimeStatus:
    def __init__(self, api):
        self.api = api
        self.site = api.site

    def register(self, app):
        app.router.add_get("/runtime-status", self.index)

    async def index(self, request):
        from .control_site import _page
        if not self.api.authenticated(request):
            raise web.HTTPSeeOther("/login")
        if request.query:
            raise web.HTTPBadRequest(text="This read-only page accepts no options.")
        profiles = list(self.site._backup_profiles())
        results = await asyncio.gather(*(self.api.runtime_view(profile) for profile in profiles))
        if not self.api.authenticated(request):
            raise web.HTTPSeeOther("/login")
        body = ("<h1>Runtime configuration</h1><p><a href='/'>Settings</a> · "
                "<a href='/runtime-status'>Refresh</a></p>"
                "<p>This read does not restart a service or apply settings. Startup revisions "
                "describe only the configuration file bytes loaded at process start. They do not "
                "verify referenced keys, trust files, model weights, current controller settings "
                "or feature quality. Runtime observations may be cached for two seconds.</p>"
                "<div class='scroll' role='region' aria-label='Configuration comparisons' tabindex='0'>"
                "<table><thead><tr><th scope='col'>Profile</th><th scope='col'>Runtime observation</th>"
                "<th scope='col'>Saved file compared with startup</th></tr></thead><tbody>")
        for profile, row in zip(profiles, results, strict=True):
            label = "AI Key" if profile == "aikey" else "AI Port" + (
                " · " + profile[7:] if profile.startswith("aiport:") else "")
            matches = row.get("saved_revision_matches_runtime_startup")
            state = ("Matches startup snapshot" if matches is True else
                     "Different from startup snapshot — review before restarting" if matches is False else
                     "Unknown — no comparable startup observation")
            observation = ("Pinned HTTPS health" if row["observation"] == "runtime_health" else
                           "No runtime target configured" if row["observation"] == "not_configured" else
                           "Runtime observation unavailable")
            body += ("<tr><th scope='row'>" + escape(label) + "</th><td>" + observation + "</td><td>"
                     + state + "</td></tr>")
        body += ("</tbody></table></div><p>Different bytes can include formatting or controller-managed "
                 "updates; a mismatch alone does not prove that a feature is inactive. Legacy or "
                 "programmatically constructed runtimes may not report startup provenance. "
                 "Unknown is not a match or a failure.</p>")
        return _page("Runtime configuration", body)
