"""Explicit saved local Face endpoint changes; no grant, inference or migration."""

import asyncio
import hmac
import secrets
import time

from aiohttp import web

from .config_store import (ConfigurationStoreError, FaceRouteOutcomeUncertain,
                           RevisionConflict, UnsafeConfigurationChange)
from .control_site import _page, _safe
from .face_identity_control import FaceIdentityControl, _HEX

_ERRORS = (ValueError, KeyError, OSError, ConfigurationStoreError, TimeoutError)
_LIMITS = ('Only the saved local face-service address changes. Existing camera permissions, '
           'templates, models, native groups and running services stay unchanged. Earlier '
           'settings remain in private configuration history; there is no automatic Undo or restart.')
_MODEL = ('Choose a local service you control; a local address does not guarantee that data '
          'stays local. A different face model cannot reuse existing templates. Coordinate the '
          'Face service, Key and admin tools before new enrollment. New admin enrollment uses '
          'the saved address immediately; the running Key keeps its old address until restarted separately.')



class FaceRouteControl:
    def __init__(self, api):
        self.api, self.site = api, api.site
        self.store = self.site.aikey
        self.grants = {}
        self.clock = time.monotonic
        self.read_task = None
        self.closed = False

    def register(self, app):
        app.router.add_get('/face-route', self.page)
        app.router.add_post('/face-route/preview', self.preview)
        app.router.add_post('/face-route/apply', self.apply)
        app.on_cleanup.append(self.cleanup)

    async def cleanup(self, _app):
        self.closed = True
        self.grants.clear()

    def _expire(self):
        self.grants = {k: v for k, v in self.grants.items() if self.clock() < v['deadline']}

    def _audit(self, result):
        if self.site.audit is None:
            return True
        try:
            return self.site.audit.record('face_route', result, profile='aikey')
        except (OSError, ValueError):
            return False

    async def _read(self):
        return await asyncio.to_thread(self.store.face_route_status)

    async def _status(self):
        if self.read_task is None or self.read_task.done():
            self.read_task = asyncio.create_task(self.api.file_operation(self._read))
            self.read_task.add_done_callback(FaceIdentityControl._observed)
        async with asyncio.timeout(3):
            return await asyncio.shield(self.read_task)

    async def _preview(self, revision, server):
        return await asyncio.to_thread(self.store.preview_face_route, revision, server)

    async def _fields(self, request, expected):
        if request.query or request.content_type != 'application/x-www-form-urlencoded':
            raise ValueError
        fields = await request.post()
        if (set(fields) != expected or any(len(fields.getall(k)) != 1
                or not isinstance(fields[k], str) or len(fields[k]) > 512 for k in fields)):
            raise ValueError
        return fields

    @staticmethod
    def _refused(*, unavailable=False):
        return _page('Local face route not saved', '<h1>Local face route not saved</h1>'
            '<p>The saved role, destination, session, confirmation or private audit is '
            'unavailable or changed. Reload and review a new preview; no automatic retry occurs.</p>'
            "<p><a href='/face-route'>Reload local face service</a></p>", status=503 if unavailable else 409)

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther('/login')
        if request.query:
            return self._refused()
        self._expire()
        body = ("<h1>Saved local face service</h1><p><a href='/'>Settings</a> · "
                "<a href='/face-identities'>Local identities</a></p>"
                f'<p>{_LIMITS}</p><p>{_MODEL}</p>')
        try:
            status = await self._status()
        except _ERRORS:
            return _page('Local face service', body + '<p>Saved route unavailable; no live state is asserted.</p>', status=503)
        csrf = self.site.security.csrf_token(cookie)
        if csrf is None or self.site._session(request) != cookie:
            raise web.HTTPSeeOther('/login')
        if not status['configured']:
            return _page('Local face service', body + '<p>No local face role is saved. This page cannot create it or add camera permission.</p>')
        body += (f"<p>Cameras in saved policy: {status['camera_count']}. Running settings may differ.</p>"
            "<form method='post' action='/face-route/preview'>"
            + FaceIdentityControl._hidden({'csrf': csrf, 'revision': status['revision']})
            + f"<label>Local face service address<input name='server' required maxlength='512' value='{_safe(status['server'])}'></label>"
            '<p>Use a separately verified local HTTP root address. Viewing and previewing never contact it.</p>'
            '<button>Preview saved face route</button></form>')
        return _page('Local face service', body)

    async def preview(self, request):
        try:
            fields = await self._fields(request, {'csrf', 'revision', 'server'})
            cookie = self.site._session(request, mutate=True, csrf=fields['csrf'])
            if cookie is None:
                raise web.HTTPForbidden()
            self._expire()
            if self.closed or len(self.grants) >= 16 or not _HEX.fullmatch(fields['revision']):
                raise ValueError
            result = await self.api.file_operation(self._preview, fields['revision'], fields['server'])
            if self.site._session(request) != cookie:
                raise web.HTTPForbidden()
            if not result.changed_fields:
                return _page('Local face route unchanged', '<h1>Local face route unchanged</h1>'
                    "<p>No saved change or confirmation is needed.</p><p><a href='/face-route'>Local face service</a></p>")
            if not self._audit('previewed'):
                return self._refused(unavailable=True)
            if self.closed or self.site._session(request) != cookie:
                raise web.HTTPForbidden()
            self._expire()
            if len(self.grants) >= 16:
                raise ValueError
            token = secrets.token_hex(32)
            server = result.configuration['face_recognition']['server']
            self.grants[token] = {'session': FaceIdentityControl._session_digest(cookie),
                'revision': fields['revision'], 'server': server,
                'resulting_revision': result.resulting_revision, 'deadline': self.clock() + 120}
            return _page('Confirm saved face route', '<h1>Confirm saved face route</h1>'
                f'<p>New local destination: <strong>{_safe(server)}</strong>.</p>'
                f'<p>{_LIMITS}</p><p>{_MODEL}</p>'
                '<p>One-use confirmation expires after two minutes. No endpoint has been contacted.</p>'
                "<form method='post' action='/face-route/apply'>"
                + FaceIdentityControl._hidden({'csrf': fields['csrf'], 'grant': token})
                + "<label><input type='checkbox' name='confirm' value='yes' required>"
                'I verified this local destination and want to save only this route.</label>'
                '<button>Save local face route</button></form>'
                "<p><a href='/face-route'>Cancel and return</a></p>")
        except _ERRORS:
            return self._refused()

    async def apply(self, request):
        try:
            fields = await self._fields(request, {'csrf', 'grant', 'confirm'})
            cookie = self.site._session(request, mutate=True, csrf=fields['csrf'])
            if cookie is None:
                raise web.HTTPForbidden()
            self._expire()
            token = fields['grant']
            grant = self.grants.get(token)
            if (not _HEX.fullmatch(token) or grant is None or fields['confirm'] != 'yes'
                    or not hmac.compare_digest(grant['session'], FaceIdentityControl._session_digest(cookie))):
                raise ValueError
            result = await self.api.file_operation(self._preview, grant['revision'], grant['server'])
            if result.resulting_revision != grant['resulting_revision']:
                raise ValueError
            if self.site._session(request) != cookie:
                raise web.HTTPForbidden()
            if not self._audit('admitted'):
                return self._refused(unavailable=True)
            if (self.closed or self.site._session(request) != cookie
                    or self.clock() >= grant['deadline'] or self.grants.get(token) is not grant):
                raise ValueError
            self.grants.clear()
            self.api.face_enrollment_control.invalidate()
            return await self.api.file_operation(self._commit, grant)
        except _ERRORS:
            return self._refused()

    async def _commit(self, grant):
        try:
            saved = await asyncio.to_thread(self.store.apply_face_route, grant['revision'], grant['server'])
            if saved.revision != grant['resulting_revision']:
                return self._uncertain()
        except (RevisionConflict, UnsafeConfigurationChange):
            self._audit('conflict')
            return self._refused()
        except (FaceRouteOutcomeUncertain, OSError):
            return self._uncertain()
        except ConfigurationStoreError:
            self._audit('refused')
            return self._refused(unavailable=True)
        if not self._audit('saved'):
            return self._uncertain()
        return _page('Local face route saved', '<h1>Local face route saved</h1>'
            f'<p>{_LIMITS}</p><p>{_MODEL}</p>'
            "<p><a href='/face-route'>Local face service</a></p>")

    def _uncertain(self):
        self._audit('uncertain')
        return _page('Face route outcome uncertain', '<h1>Face route outcome uncertain</h1>'
            '<p>The route may already have been saved. Do not automatically retry or restore '
            'older state. Reload and reconcile the saved address before deciding on a new action.</p>'
            "<p><a href='/face-route'>Reload saved route</a></p>", status=503)
