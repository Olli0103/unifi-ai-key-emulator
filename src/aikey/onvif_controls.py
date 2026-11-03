"""Typed ONVIF reference registration; no network test or processing authority."""
import asyncio
import secrets

from aiohttp import web

from .admin_api import PREFIX, error, reply
from .control_site import _page, _safe
from .onvif_catalog import load, public, valid_reference
from .protocol import _json_object


class OnvifControls:
    def __init__(self, api):
        self.api, self.site = api, api.site
        self.devices = api.device_controls
        self.previous_enricher = self.devices.review_enricher
        self.devices.review_enricher = self.enrich

    def register(self, app):
        app.router.add_get('/onvif-cameras', self.page)
        app.router.add_get(PREFIX + '/onvif-cameras', self.status)
        app.router.add_post('/onvif-cameras/preview', self.preview)
        app.router.add_post(PREFIX + '/device-workflows/onvif-preview', self.preview)

    async def rows(self):
        result = []
        for profile in self.site.aiports:
            try:
                row = await asyncio.to_thread(self.devices.stores[profile].onvif_status)
                row['available'] = True
            except (OSError, ValueError, KeyError, RuntimeError):
                row = {'profile': profile, 'available': False, 'configured_cameras': []}
            result.append(row)
        return result

    async def status(self, request):
        if not self.api.authenticated(request):
            return error('authentication_required', 401)
        if request.query:
            return error('invalid_request', 400)
        rows = await self.rows()
        if not self.api.authenticated(request):
            return error('authentication_required', 401)
        return reply({'profiles': rows, 'ingest': 'not_implemented', 'ptz': 'not_implemented',
                      'recording': 'not_implemented', 'network_requests': 0})

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther('/login')
        rows = await self.rows()
        csrf = self.site.security.csrf_token(cookie)
        if csrf is None or self.site._session(request) is None:
            raise web.HTTPSeeOther('/login')
        body = ("<h1>Configured ONVIF references</h1><p><a href='/devices'>Devices</a> · "
            "<a href='/cameras'>Protect inventory</a> · <a href='/camera-pairing'>Protect camera pairing</a></p>"
            "<p><strong>Metadata only — ONVIF ingest, discovery, PTZ and recording are not implemented.</strong> "
            "Saving references never connects to a camera, sends credentials, starts capture, creates a "
            "native camera identity or changes Protect adoption. This does not establish compatibility.</p>"
            "<p>Maximum eight local registrations per profile. Use independent camera credentials. "
            "After save, usernames/passwords and private reference paths are never returned. "
            "Leave both credential fields blank only to retain an existing entry at the same destination; "
            "a destination/TLS/pin change requires fresh credentials. Retained versions remain available "
            "for exact saved-state Undo; removing a registration does not erase private files.</p>")
        for row in rows:
            body += f"<section><h2>{_safe(row['profile'])}</h2>"
            if not row['available']:
                body += '<p>Metadata unavailable. Inspect the private profile state; no automatic repair or activation occurs.</p></section>'
                continue
            base = {'csrf': csrf, 'profile': row['profile'], 'revision': row['revision']}
            for camera in row['configured_cameras']:
                body += self._form(base, camera)
                body += ("<form method='post' action='/onvif-cameras/preview'>" + self.devices._hidden(dict(base,
                    operation='remove', camera=camera['id']))
                    + f"<button>Review removal of {_safe(camera['name'])}</button></form>")
            if len(row['configured_cameras']) < 8:
                body += self._form(base, None)
            if row['configured_cameras']:
                body += ("<form method='post' action='/onvif-cameras/preview'>" + self.devices._hidden(dict(base,
                    operation='clear', camera='all')) + '<button>Review removing all ONVIF metadata</button></form>')
            body += '</section>'
        if not rows:
            body += '<p>Attach an independently prepared AI Port profile before registering its ONVIF references.</p>'
        return _page('Configured ONVIF references', "<div class='device-workflow'>"+body+'</div>')

    def _form(self, base, camera):
        camera = camera or {'id': 'new', 'name': '', 'host': '', 'port': 443, 'tls': True, 'certificate_pin': ''}
        https = ' selected' if camera['tls'] else ''
        http = ' selected' if not camera['tls'] else ''
        return ("<form method='post' action='/onvif-cameras/preview'>" + self.devices._hidden(dict(base,
            operation='save', camera=camera['id']))
            + f"<h3>{_safe(camera['name'] or 'New ONVIF registration')}</h3>"
            f"<label>Camera label<input name='name' value='{_safe(camera['name'])}' maxlength='128' required></label>"
            f"<label>Camera host, no URL<input name='host' value='{_safe(camera['host'])}' maxlength='253' required></label>"
            f"<label>ONVIF port<input name='port' type='number' min='1' max='65535' value='{camera['port']}' required></label>"
            f"<label>Transport<select name='transport' aria-label='Transport'><option value='https'{https}>HTTPS</option>"
            f"<option value='http'{http}>Unencrypted HTTP (unsupported ingest)</option></select></label>"
            f"<label>Independently reviewed HTTPS certificate SHA-256<input name='certificate_pin' "
            f"value='{_safe(camera['certificate_pin'] or '')}' maxlength='95'></label>"
            "<label>New camera username<input name='username' type='password' maxlength='512' autocomplete='new-password'></label>"
            "<label>New camera password<input name='password' type='password' maxlength='2048' autocomplete='new-password'></label>"
            "<button>Review metadata and private references</button></form>")

    async def preview(self, request):
        json_mode = request.path.startswith(PREFIX + '/')
        try:
            if request.query:
                raise ValueError
            if json_mode:
                cookie = self.api.authenticated(request, mutate=True)
                if cookie is None:
                    return error('forbidden', 403)
                if request.content_type != 'application/json':
                    raise ValueError
                fields = _json_object(await request.read())
            else:
                if request.content_type != 'application/x-www-form-urlencoded':
                    raise ValueError
                form = await request.post()
                cookie = self.site._session(request, mutate=True, csrf=form.get('csrf'))
                if cookie is None:
                    raise web.HTTPForbidden()
                if any(len(form.getall(key)) != 1 or not isinstance(form[key], str) for key in form):
                    raise ValueError
                fields = dict(form)
                fields.pop('csrf')
            base = {'profile', 'revision', 'operation', 'camera'}
            if not base <= set(fields) or fields['profile'] not in self.site.aiports:
                raise ValueError
            profile = fields['profile']
            with self.devices.lock:
                epoch = self.devices.epochs.get(profile, 0)
            store = self.devices.stores[profile]
            status = await asyncio.to_thread(store.onvif_status)
            if status['revision'] != fields['revision']:
                raise ValueError
            camera = fields['camera']
            if (not (fields['operation']=='clear' and camera=='all') and camera != 'new'
                    and camera not in {row['id'] for row in status['configured_cameras']}):
                raise ValueError
            if fields['operation'] == 'clear':
                if set(fields)!=base or camera!='all':
                    raise ValueError
                selection={'operation':'clear','id':'0'*32}
            elif fields['operation'] == 'remove':
                if set(fields) != base or camera == 'new':
                    raise ValueError
                selection = {'operation': 'remove', 'id': camera}
            else:
                if (fields['operation'] != 'save' or set(fields) != base | {
                        'name', 'host', 'port', 'transport', 'certificate_pin', 'username', 'password'}
                        or fields['transport'] not in ('https', 'http')
                        or any(not isinstance(fields[key], str) for key in ('name', 'host', 'certificate_pin', 'username', 'password'))):
                    raise ValueError
                if json_mode and type(fields['port']) is not int:
                    raise ValueError
                if not json_mode and (not fields['port'].isascii() or not fields['port'].isdecimal()):
                    raise ValueError
                pin = fields['certificate_pin'].replace(':', '').lower()
                selection = {key: fields[key] for key in ('name', 'host', 'username', 'password')}
                selection.update(operation='save', id=secrets.token_hex(16) if camera == 'new' else camera,
                    port=int(fields['port']), tls=fields['transport'] == 'https', certificate_pin=pin or None,
                    credential_slot=secrets.token_hex(16) if fields['username'] or fields['password'] else None)
            preview = await asyncio.to_thread(store.preview_onvif, fields['revision'], selection)
            details = await self.enrich(preview)
            if self.site._session(request) is None:
                raise ValueError
            return self.devices._review(cookie, profile, 'onvif_catalog', preview, json_mode=json_mode,
                                        epoch=epoch, details=details)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            return self.devices._refused(json_mode)

    async def enrich(self, preview):
        if not preview.operation.startswith('onvif_catalog'):
            return await self.previous_enricher(preview) if self.previous_enricher else None
        config = _json_object(preview._encoded)
        from .aiport_ingest import normalize_mac
        mac = normalize_mac(config['mac'])
        staged = next((raw for name, raw in preview._secrets if valid_reference(name)), None)
        catalog = (_json_object(staged) if staged is not None else load(
            self.devices.stores[preview.profile].identity_directory, config.get('onvif_catalog_ref'), mac,
            verify_credentials=False))
        cameras = public(catalog)['configured_cameras']
        details = {'configured_ONVIF_metadata': '; '.join(
            f"{row['name']} — {row['host']}:{row['port']} ({'HTTPS, saved pin; untested' if row['tls'] else 'unencrypted HTTP'})"
            for row in cameras) or 'None',
            'reviewed_camera_HTTPS_fingerprints': '; '.join(f"{row['name']}: {row['certificate_pin']}"
                for row in cameras if row['tls']) or 'None',
            'credential_references': 'independent private profile files; never displayed',
            'ingest_discovery_PTZ_recording': 'NOT IMPLEMENTED', 'processing_or_native_identity_change': 'none',
            'network_requests': 0}
        return {'public': details}
