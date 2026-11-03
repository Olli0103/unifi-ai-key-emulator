"""Explicit photo-to-local-template enrollment; never camera or cloud inference.

Bounded image bytes are transient. One owned analyzer call survives HTTP waiter
cancellation; only a separate one-use, revision-bound confirmation may persist.
"""

import asyncio
import hmac
import secrets
import time

from aiohttp import BodyPartReader, web
from aiohttp.http_exceptions import BadHttpMessage

from .config_store import ConfigurationStoreError, RevisionConflict
from .control_site import _page, _safe
from .face_identity_control import FaceIdentityControl, _HEX
from .face_policy import recognition_policy
from .faces import (FaceStoreOutcomeUncertain, FaceStoreRevisionConflict,
                    _MAX_IDENTITIES, _MAX_TEMPLATES, _NAME, embed_single_face)
from .multipart_input import read_part
from .model_work_fence import ModelWorkFence, ModelWorkFenceError

_MAX_IMAGE = 10 * 1024 * 1024
_UPLOAD_TIMEOUT = 30
_ERRORS = (ValueError, KeyError, OSError, TimeoutError, ConfigurationStoreError)
_LIMITS = ("Only an image you explicitly supply is sent to the already configured local face "
           "service. No camera video, cloud provider, native Protect group, permission or service "
           "is changed. This creates a private local Key template, not a native Protect identity. "
           "Upgrade all local face tools before enrollment; older tools cannot read new templates.")


class EnrollmentUnavailable(ValueError):
    def __init__(self, reason):
        super().__init__('local_enrollment_unavailable')
        self.reason = reason


class FaceEnrollmentControl:
    def __init__(self, api):
        self.api, self.site = api, api.site
        self.identity = api.face_identity_control
        self.store = self.identity.store
        self.grants = {}
        self.clock = time.monotonic
        self.read_task = None
        self.busy = False
        self.closed = False

    def register(self, app):
        app.router.add_get('/face-identities/enroll', self.page)
        app.router.add_post('/face-identities/enroll/preview', self.preview)
        app.router.add_post('/face-identities/enroll/apply', self.apply)
        app.router.add_post('/face-identities/enroll/cancel', self.cancel)
        app.on_cleanup.append(self.cleanup)

    def _audit(self, result):
        if self.site.audit is None:
            return True
        try:
            return self.site.audit.record('face_identity_enroll', result, profile='aikey')
        except (OSError, ValueError):
            return False

    def _drop(self, token):
        grant = self.grants.pop(token, None)
        if grant is not None:
            grant['timer'].cancel()
            grant['template'].clear()

    def invalidate(self):
        for token in list(self.grants):
            self._drop(token)

    def revoke_session(self, cookie):
        session = self.identity._session_digest(cookie)
        for token, grant in list(self.grants.items()):
            if hmac.compare_digest(grant['session'], session):
                self._drop(token)

    async def cleanup(self, _app):
        self.closed = True
        self.invalidate()

    def _expire(self):
        for token, grant in list(self.grants.items()):
            if self.clock() >= grant['deadline']:
                self._drop(token)

    def _read_sync(self):
        config = self.site.aikey.snapshot()
        policy = recognition_policy(config.configuration.get('face_recognition'))
        return {'config_revision': config.revision, 'policy': policy,
                'store': self.store.enrollment_status()}

    async def _read(self):
        return await asyncio.to_thread(self._read_sync)

    async def _status(self):
        if self.read_task is None or self.read_task.done():
            self.read_task = asyncio.create_task(self.api.file_operation(self._read))
            self.read_task.add_done_callback(FaceIdentityControl._observed)
        async with asyncio.timeout(3):
            return await asyncio.shield(self.read_task)

    @staticmethod
    def _available(status, *, name=None):
        store = status['store']
        if status['policy'] is None:
            raise EnrollmentUnavailable('role_missing')
        if store['migration_required']:
            raise EnrollmentUnavailable('legacy_store')
        if name is not None:
            counts = store['identities']
            if (counts.get(name, 0) >= _MAX_TEMPLATES
                    or name not in counts and len(counts) >= _MAX_IDENTITIES):
                raise EnrollmentUnavailable('capacity')

    @staticmethod
    def _refused(*, unavailable=False, reason=None):
        message = {
            'role_missing': 'No saved local face role is configured. This page cannot enable '
                'recognition or add cameras.',
            'legacy_store': 'The legacy store has no proven encoder identity. Its templates cannot '
                'be reused or relabelled here. Review local identities and plan explicit removal '
                'and fresh consented enrollment.',
            'capacity': 'The local identity or template limit is reached. Review existing local '
                'identities before deciding on a new request. No additional template was saved.',
            'model_mismatch': 'The local service returned a different model space from the saved '
                'templates. Restore the matching local model or plan an explicit migration. '
                'Do not relabel or automatically replace existing templates.',
        }.get(reason, 'The local service, saved policy, identity store, consent, session or '
            'confirmation is unavailable or changed. Reload before deciding on a new request.')
        return _page('Local enrollment not completed', '<h1>Local enrollment not completed</h1>'
            f'<p>No template was saved by this request. {message}</p>'
            "<p><a href='/face-identities/enroll'>Reload enrollment</a> · "
            "<a href='/face-identities'>Local identities</a></p>", status=503 if unavailable else 409)

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther('/login')
        self._expire()
        if request.query:
            return self._refused()
        try:
            status = await self._status()
            self._available(status)
        except EnrollmentUnavailable as exc:
            return self._refused(unavailable=True, reason=exc.reason)
        except _ERRORS:
            return self._refused(unavailable=True)
        csrf = self.site.security.csrf_token(cookie)
        if csrf is None or self.site._session(request) != cookie:
            raise web.HTTPForbidden()
        body = ('<h1>Enroll a local face identity</h1>'
            "<p><a href='/face-identities'>Local identities</a></p>"
            f'<p>{_LIMITS}</p><p>Supply one JPEG with exactly one face, at most 10 MiB. '
            'The image is not saved by this administration flow. A temporary vector is kept '
            'in memory for at most two minutes pending your separate confirmation. '
            'Memory cleanup is not physical erasure; original files and server/backup copies '
            'have their own retention.</p>'
            "<form method='post' enctype='multipart/form-data' action='/face-identities/enroll/preview'>"
            + self.identity._hidden({'csrf': csrf, 'revision': status['store']['revision'],
                                     'config_revision': status['config_revision']})
            + "<label>Local identity label<input name='name' maxlength='64' required></label>"
            "<label><input type='checkbox' name='consent' value='yes' required>"
            'I have permission to process this supplied image locally and create a private template.</label>'
            "<label>One face JPEG<input type='file' name='image' accept='image/jpeg' required></label>"
            '<button>Analyze locally and review</button></form>')
        return _page('Local face enrollment', body)

    async def _upload(self, request):
        if request.query or request.content_type != 'multipart/form-data':
            raise ValueError
        reader = await request.multipart()
        fields, image = {}, None
        while (part := await reader.next()) is not None:
            if not isinstance(part, BodyPartReader) or len(fields) + int(image is not None) >= 6:
                raise ValueError
            if part.name == 'image' and image is None:
                if part.headers.get('Content-Type') != 'image/jpeg':
                    raise ValueError
                image = await read_part(part, _MAX_IMAGE)
            elif part.name in {'csrf', 'revision', 'config_revision', 'name', 'consent'} and part.name not in fields:
                if part.filename is not None:
                    raise ValueError
                fields[part.name] = (await read_part(part, 256)).decode('utf-8')
            else:
                raise ValueError
        if (set(fields) != {'csrf', 'revision', 'config_revision', 'name', 'consent'}
                or image is None or not image.startswith(b'\xff\xd8\xff')):
            raise ValueError
        return fields, image

    async def _analyze(self, image, server):
        fence = lease = None
        try:
            fence = await asyncio.to_thread(ModelWorkFence.from_state, self.site.aikey_state_dir)
            lease = await fence.acquire(deadline=time.monotonic() + 60)
            return await asyncio.to_thread(embed_single_face, image, server)
        finally:
            if lease is not None:
                lease.close()
            if fence is not None:
                fence.close()
            # The owned operation, never its disconnected HTTP waiter, releases
            # admission only after the real synchronous helper has ended.
            self.busy = False

    async def preview(self, request):
        cookie = self.site._session(request)
        if cookie is None or request.headers.get('Origin') != self.site.origin:
            raise web.HTTPForbidden()
        self._expire()
        if self.closed or self.busy or len(self.grants) >= 8:
            return self._refused(unavailable=True)
        self.busy = True
        handed = False
        vector = None
        transferred = False
        try:
            async with asyncio.timeout(_UPLOAD_TIMEOUT):
                fields, image = await self._upload(request)
            if self.site._session(request, mutate=True, csrf=fields['csrf']) != cookie:
                raise web.HTTPForbidden()
            name = fields['name'].strip()
            if (fields['consent'] != 'yes' or not _NAME.fullmatch(name)
                    or not _HEX.fullmatch(fields['revision']) or not _HEX.fullmatch(fields['config_revision'])):
                raise ValueError
            status = await self._status()
            self._available(status, name=name)
            if (status['config_revision'] != fields['config_revision']
                    or status['store']['revision'] != fields['revision']):
                raise ValueError
            if self.site._session(request) != cookie:
                raise web.HTTPForbidden()
            if not self._audit('analysis_requested'):
                return self._refused(unavailable=True)
            if self.site._session(request) != cookie:
                raise web.HTTPForbidden()
            handed = True
            async with asyncio.timeout(65):
                vector, space = await self.api.file_operation(self._analyze, image, status['policy']['server'])
            image = None
            current = await self._status()
            self._available(current, name=name)
            if (self.closed or current['config_revision'] != fields['config_revision']
                    or current['store']['revision'] != fields['revision']):
                raise ValueError
            if current['store']['embedding_space'] not in (None, space):
                return self._refused(reason='model_mismatch')
            if self.site._session(request) != cookie:
                raise web.HTTPForbidden()
            if not self._audit('previewed'):
                return self._refused(unavailable=True)
            if self.site._session(request) != cookie:
                raise web.HTTPForbidden()
            self._expire()
            if len(self.grants) >= 8:
                raise ValueError
            token = secrets.token_hex(32)
            grant = {'session': self.identity._session_digest(cookie), 'name': name,
                'revision': fields['revision'], 'config_revision': fields['config_revision'],
                'template': vector, 'space': space, 'deadline': self.clock() + 120}
            grant['timer'] = asyncio.get_running_loop().call_later(120, self._drop, token)
            self.grants[token] = grant
            transferred = True
            hidden = self.identity._hidden({'csrf': fields['csrf'], 'grant': token})
            return _page('Confirm local enrollment', '<h1>Confirm local enrollment</h1>'
                f'<p>Local label: <strong>{_safe(name)}</strong>. One template will be '
                'added; existing templates are preserved.</p>'
                '<p>The supplied image is not displayed or saved. This one-use confirmation '
                'expires after two minutes. Nothing has been enrolled yet.</p>'
                "<form method='post' action='/face-identities/enroll/apply'>" + hidden
                + "<label><input type='checkbox' name='confirm' value='yes' required>"
                'Save this private local face template.</label><button>Save local template</button></form>'
                "<form method='post' action='/face-identities/enroll/cancel'>" + hidden
                + '<button>Discard and return</button></form>')
        except EnrollmentUnavailable as exc:
            return self._refused(unavailable=True, reason=exc.reason)
        except (ModelWorkFenceError, TimeoutError):
            return self._refused(unavailable=True)
        except (BadHttpMessage, UnicodeError, *_ERRORS):
            return self._refused()
        finally:
            if vector is not None and not transferred:
                vector.clear()
            if not handed:
                self.busy = False

    async def _grant(self, request, *, confirm):
        fields = await self.identity._fields(request, {'csrf', 'grant', 'confirm'} if confirm else {'csrf', 'grant'})
        cookie = self.site._session(request, mutate=True, csrf=fields['csrf'])
        if cookie is None:
            raise web.HTTPForbidden()
        self._expire()
        token = fields['grant']
        grant = self.grants.get(token)
        if (not _HEX.fullmatch(token) or grant is None
                or not hmac.compare_digest(grant['session'], self.identity._session_digest(cookie))
                or confirm and fields['confirm'] != 'yes'):
            raise ValueError
        return cookie, token, grant

    async def cancel(self, request):
        try:
            _, token, _ = await self._grant(request, confirm=False)
        except _ERRORS:
            return self._refused()
        self._drop(token)
        self._audit('cancelled')
        raise web.HTTPSeeOther('/face-identities')

    async def apply(self, request):
        try:
            cookie, token, grant = await self._grant(request, confirm=True)
            status = await self._status()
            self._available(status, name=grant['name'])
            if (status['config_revision'] != grant['config_revision']
                    or status['store']['revision'] != grant['revision']):
                raise ValueError
            if status['store']['embedding_space'] not in (None, grant['space']):
                return self._refused(reason='model_mismatch')
            if self.site._session(request) != cookie:
                raise web.HTTPForbidden()
            if not self._audit('admitted'):
                return self._refused(unavailable=True)
            if (self.site._session(request) != cookie or self.closed
                    or self.clock() >= grant['deadline'] or self.grants.get(token) is not grant):
                raise ValueError
            commit = {**grant, 'template': list(grant['template'])}
            self.invalidate()
            self.identity.grants.clear()
            return await self.api.file_operation(self._commit, commit)
        except EnrollmentUnavailable as exc:
            return self._refused(unavailable=True, reason=exc.reason)
        except _ERRORS:
            return self._refused()

    async def _commit(self, grant):
        try:
            await asyncio.to_thread(self.site.aikey.enroll_local_face, grant['config_revision'],
                self.store, grant['name'], grant['template'], grant['space'], grant['revision'])
        except (FaceStoreRevisionConflict, RevisionConflict):
            self._audit('conflict')
            return self._refused()
        except (FaceStoreOutcomeUncertain, OSError):
            return self._uncertain()
        except _ERRORS:
            self._audit('refused')
            return self._refused()
        finally:
            grant['template'].clear()
        if not self._audit('saved'):
            return self._uncertain()
        raise web.HTTPSeeOther('/face-identities')

    def _uncertain(self):
        self._audit('uncertain')
        return _page('Enrollment outcome uncertain', '<h1>Enrollment outcome uncertain</h1>'
            '<p>The template may already have been saved. Do not automatically retry or '
            'restore older data. Reload and reconcile the saved identity count before '
            'deciding on a new action.</p>'
            "<p><a href='/face-identities'>Reload identities</a></p>", status=503)
