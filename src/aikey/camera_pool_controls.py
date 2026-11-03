"""Fresh opaque camera choices for local capture, not native Protect writes."""
import asyncio
from fractions import Fraction
import hashlib
import hmac
import json
import re
import secrets

from aiohttp import ClientError, web

from .admin_api import PREFIX, error, reply
from .admin_inventory import inventory_view
from .aiport_deployment import _eligible
from .control_site import _page, _safe
from .protocol import _json_object


class CameraPoolControls:
    def __init__(self, api):
        self.api, self.site = api, api.site
        self.devices = api.device_controls
        self.key = secrets.token_bytes(32)
        self.devices.review_enricher = self.enrich
        self.devices.mutation_validator = self.validate

    def register(self, app):
        app.router.add_get('/camera-pairing', self.page)
        app.router.add_get(PREFIX + '/camera-pools', self.status)
        app.router.add_post('/camera-pairing/preview', self.preview)
        app.router.add_post(PREFIX + '/device-workflows/camera-pool-preview', self.preview)

    async def _inventory(self, *, fresh=True):
        if self.site.inventory_loader is None:
            raise ValueError
        async with asyncio.timeout(12):
            try:
                report = await self.site.inventory_loader() if fresh else await self.site.inventory_snapshot()
            except ClientError:
                raise ValueError('camera_inventory_unavailable') from None
        rows = inventory_view(report, {})['cameras']
        if (len({row['mac'] for row in rows}) != len(rows)
                or any(not row['name'] or row['mac'].casefold() in re.sub(r'[:.\-]', '', row['name']).casefold() for row in rows)
                or len({row['name'].casefold() for row in rows}) != len(rows)):
            raise ValueError
        canonical = {row['id']: row for row in rows}
        report = dict(report, cameras=[dict(row,
            processing_class=canonical[row['id']]['processing_class'], mac=canonical[row['id']]['mac'])
            for row in report['cameras']])
        return report, rows

    async def _bindings(self):
        result = {}
        for profile, store in self.site.aiports.items():
            cameras = await asyncio.to_thread(store.configured_camera_macs)
            for camera in cameras:
                result.setdefault(camera, []).append(profile)
        return result

    def _token(self, cookie, profile, revision, row):
        value = ['camera-pool-choice/1', cookie, profile, revision,
                 row['id'], row['mac'], row['name'], row['model']]
        return 'camera_' + hmac.new(self.key, json.dumps(value, separators=(',', ':')).encode(), hashlib.sha256).hexdigest()

    async def _choices(self, cookie, profile, status, observation=None):
        report, rows = observation if observation is not None else await self._inventory()
        bindings = await self._bindings()
        # Reuse the existing capacity/source classifier. ONVIF and unsupported
        # AI-series cameras never become Protect relay choices by inference.
        selected_report = dict(report, cameras=[row for row in report['cameras']
            if row.get('state') == 'CONNECTED' and isinstance(row.get('model'), str)
            and re.match(r'UVC G[345](?:\s|\Z)', row['model'])
            and row.get('source_kind') in (None, 'protect')])
        groups, _, _ = _eligible(selected_report, 'legacy-and-g3-g5')
        eligible = {identifier for identifier, _, _ in groups['protect']}
        return {self._token(cookie, profile, status['revision'], row): row for row in rows
                if row['id'] in eligible and not set(bindings.get(row['mac'], [])) - {profile}}

    def _decoder(self, profile, status):
        value = self.site.camera_pool_decoders.get(profile)
        if value is None and len(status['decoders']) == 1:
            value = status['decoders'][0]
        if value is None:
            raise ValueError('host_decoder_binding_required')
        return value

    async def _rows(self, cookie):
        rows = []
        try:
            observation = await self._inventory(fresh=False)
        except (OSError, ValueError, KeyError, RuntimeError, TimeoutError):
            observation = None
        for profile in self.site.aiports:
            status = None
            try:
                status = await asyncio.to_thread(self.devices.stores[profile].camera_pool_status)
                if observation is None:
                    raise ValueError
                choices = await self._choices(cookie, profile, status, observation)
                self._decoder(profile, status)
                rows.append({'profile': profile, 'revision': status['revision'], 'available': True,
                    'reason': None, 'initial_detector_required': not status['detector_configured'],
                    'choices': [{'choice': key, 'name': row['name'], 'selected': row['mac'] in status['cameras']}
                                for key, row in choices.items()]})
            except (OSError, ValueError, KeyError, RuntimeError, TimeoutError):
                rows.append({'profile': profile, 'available': False, 'reason': 'fresh_inventory_or_host_binding_required',
                    'choices': [], 'revision': status['revision'] if status else None,
                    'disable_available': bool(status and status['cameras'] and (status['decoders'] or profile in self.site.camera_pool_decoders))})
        return rows

    async def status(self, request):
        cookie = self.api.authenticated(request)
        if not cookie:
            return error('authentication_required', 401)
        if request.query:
            return error('invalid_request', 400)
        rows = await self._rows(cookie)
        if not self.api.authenticated(request):
            return error('authentication_required', 401)
        return reply({'profiles': rows, 'native_pairing_changed': False})

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther('/login')
        rows = await self._rows(cookie)
        csrf = self.site.security.csrf_token(cookie)
        if csrf is None or self.site._session(request) is None:
            raise web.HTTPSeeOther('/login')
        body = ("<h1>Cameras and AI Port pairing</h1><p><a href='/devices'>Devices</a> · "
            "<a href='/cameras'>Inventory</a> · <a href='/camera-status'>Native camera status</a></p>"
            "<h2>1. Native Protect pairing and zones</h2><p>In Protect, open the existing AI Port and "
            "select its supported cameras. Edit each camera's smart/exclusion zones in Protect. "
            "Reload Protect and verify the same adopted devices and pairings. This site does not "
            "have native write credentials and does not fabricate zone IDs or duplicate cameras.</p>"
            "<h2>2. Local relay capture permission</h2><p>This separate saved allowlist admits only "
            "reviewed Protect relay streams to the selected Port after an explicit service activation. "
            "It is not native pairing. Maximum five cameras and the existing model-resolution capacity "
            "limit apply. Adding a camera does not enroll face, plate, speech or sound processing. "
            "Removing one revokes its dependent saved permissions; private files remain retained.</p>")
        for row in rows:
            body += f"<section><h3>{_safe(row['profile'])}</h3>"
            if not row['available']:
                body += (f"<p>Editing unavailable: {_safe(row['reason'])}. Configure the detector and "
                         "host-owned decoder binding, then obtain a fresh inventory.</p>")
                if row.get('disable_available'):
                    body += ("<form method='post' action='/camera-pairing/preview'>" + self.devices._hidden({
                        'csrf': csrf, 'profile': row['profile'], 'revision': row['revision']})
                        + '<button>Review disabling all saved local capture</button></form>')
                body += '</section>'
                continue
            body += ("<form method='post' action='/camera-pairing/preview'>" + self.devices._hidden({
                'csrf': csrf, 'profile': row['profile'], 'revision': row['revision']}))
            for choice in row['choices']:
                checked = ' checked' if choice['selected'] else ''
                body += (f"<label><input type='checkbox' name='{choice['choice']}' value='yes'{checked}>"
                         f"{_safe(choice['name'])}</label>")
            if row['initial_detector_required']:
                body += self._setup_form()
            body += '<p>Uncheck all cameras to disable all saved local capture and dependent feature settings.</p><button>Review local capture cameras</button></form></section>'
        return _page('Cameras and AI Port pairing', "<div class='device-workflow'>"+body+'</div>')

    @staticmethod
    def _setup_form():
        return ("<fieldset><legend>Initial object detector</legend><p>Choose the real provider/model for this "
            "new camera pool. No request is made by setup. Credentials are independent of the AI Key.</p>"
            "<label>Provider<select name='setup_provider' aria-label='Provider' required><option value=''>Choose</option>"
            "<option value='ollama'>Ollama</option><option value='openai-compatible'>OpenAI-compatible</option>"
            "<option value='openai'>OpenAI</option><option value='anthropic'>Anthropic</option></select></label>"
            "<label>Object model ID<input name='setup_model' maxlength='128' required></label>"
            "<label>Exact API base URL<input name='setup_base_url' maxlength='512' required></label>"
            "<p>OpenAI: https://api.openai.com/v1; Anthropic: https://api.anthropic.com/v1. "
            "Ollama uses its server root (for example http://127.0.0.1:11434); compatible APIs use their /v1 base.</p>"
            "<label>New private API key, if needed<input name='setup_api_key' type='password' "
            "maxlength='4096' autocomplete='new-password'></label>"
            "<label><input type='checkbox' name='setup_allow_remote' value='yes'>Allow this non-loopback destination</label>"
            "<label><input type='checkbox' name='setup_allow_insecure_http' value='yes'>Allow unencrypted private-network HTTP</label>"
            "<label>Score threshold<input name='setup_threshold' type='number' min='0.01' max='1' step='any' value='0.8' required></label>"
            "<label>Maximum response tokens<input name='setup_max_output_tokens' type='number' min='1' max='4096' value='256' required></label>"
            "<p>Initial object class: person. Add other object classes and role-specific models through "
            "the provider settings after setup. No face, plate, speech or sound permission is granted.</p></fieldset>")

    @staticmethod
    def _setup(fields):
        required={'provider','model','base_url','allow_remote','allow_insecure_http','max_output_tokens','threshold','api_key'}
        if (not isinstance(fields,dict) or set(fields)!=required
                or type(fields['allow_remote']) is not bool or type(fields['allow_insecure_http']) is not bool
                or any(not isinstance(fields[key],str) for key in ('provider','model','base_url','api_key'))
                or type(fields['max_output_tokens']) is not int or not 1<=fields['max_output_tokens']<=4096
                or type(fields['threshold']) not in (int,float) or not 0<fields['threshold']<=1):
            raise ValueError
        provider={key:fields[key] for key in ('provider','model','base_url','allow_remote','allow_insecure_http','max_output_tokens')}
        selection={'detector':{'provider_config':provider,'threshold':fields['threshold'],'smart_types':['person']}}
        if fields['api_key']:
            selection.update(api_key=fields['api_key'],key_slot=secrets.token_hex(16))
        return selection

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
                fields = _json_object(await request.clone(client_max_size=32768).read())
                if (not {'profile', 'revision', 'choices'}<=set(fields)<= {'profile','revision','choices','setup'}
                        or not isinstance(fields['choices'], list)):
                    raise ValueError
                selected = fields['choices']
            else:
                if request.content_type != 'application/x-www-form-urlencoded':
                    raise ValueError
                form = await request.clone(client_max_size=32768).post()
                cookie = self.site._session(request, mutate=True, csrf=form.get('csrf'))
                if cookie is None:
                    raise web.HTTPForbidden()
                if any(len(form.getall(key)) != 1 or not isinstance(form[key], str) for key in form):
                    raise ValueError
                fields = dict(form)
                selected = [key for key in fields if key.startswith('camera_')]
                setup_fields={key for key in fields if key.startswith('setup_')}
                if set(fields) != {'csrf', 'profile', 'revision'} | set(selected) | setup_fields or any(fields[key] != 'yes' for key in selected):
                    raise ValueError
                if setup_fields:
                    required={'setup_provider','setup_model','setup_base_url','setup_api_key','setup_threshold','setup_max_output_tokens'}
                    optional={'setup_allow_remote','setup_allow_insecure_http'}
                    if not required<=setup_fields<=required|optional or any(fields[key]!='yes' for key in optional if key in fields):
                        raise ValueError
                    fields['setup']={key:fields['setup_'+key] for key in ('provider','model','base_url','api_key')}
                    fields['setup'].update(threshold=float(fields['setup_threshold']),max_output_tokens=int(fields['setup_max_output_tokens']),
                        allow_remote='setup_allow_remote' in fields,allow_insecure_http='setup_allow_insecure_http' in fields)
            profile = fields['profile']
            if not isinstance(profile, str) or profile not in self.site.aiports:
                raise ValueError
            with self.devices.lock:
                epoch = self.devices.epochs.get(profile, 0)
            store = self.devices.stores[profile]
            status = await asyncio.to_thread(store.camera_pool_status)
            if (fields['revision'] != status['revision'] or len(selected) > 5
                    or any(not isinstance(value, str) for value in selected) or len(set(selected)) != len(selected)):
                raise ValueError
            # An inventory outage must not block an explicit disable-all.
            choices = await self._choices(cookie, profile, status) if selected else {}
            if not set(selected) <= set(choices):
                raise ValueError
            selection = {'cameras': [choices[key]['mac'] for key in selected], 'decoder': self._decoder(profile, status)}
            if 'setup' in fields:
                if status['detector_configured']:
                    raise ValueError
                selection.update(self._setup(fields['setup']))
            preview = await asyncio.to_thread(store.preview_camera_pool, status['revision'], selection)
            details = await self.enrich(preview)
            if self.site._session(request) is None:
                raise ValueError
            return self.devices._review(cookie, profile, 'camera_pool', preview, json_mode=json_mode,
                                        epoch=epoch, details=details)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, TimeoutError):
            return self.devices._refused(json_mode)

    async def enrich(self, preview):
        if not preview.operation.startswith('camera_pool'):
            return None
        target = _json_object(preview._encoded)
        cameras = [row['camera_mac'] for row in target.get('paired_streams', [])]
        from .aiport_ingest import normalize_mac
        cameras = [normalize_mac(value) for value in cameras]
        witness = []
        if cameras:
            report, rows = await self._inventory()
            by_mac = {row['mac']: row for row in rows}
            if not set(cameras) <= set(by_mac):
                raise ValueError
            selected_rows = [row for row in report['cameras'] if normalize_mac(row['mac']) in cameras]
            if len(selected_rows) != len(cameras) or any(
                    row.get('state') != 'CONNECTED' or row.get('source_kind') not in (None, 'protect')
                    or not isinstance(row.get('model'), str) or not re.match(r'UVC G[345](?:\s|\Z)', row['model'])
                    for row in selected_rows):
                raise ValueError
            groups, _, _ = _eligible(dict(report, cameras=selected_rows), 'legacy-and-g3-g5')
            if (len(groups['protect']) != len(cameras) or groups['onvif']
                    or sum((weight for _, _, weight in groups['protect']), Fraction()) > 1):
                raise ValueError
            bindings = await self._bindings()
            if any(set(bindings.get(camera, [])) - {preview.profile} for camera in cameras):
                raise ValueError
            witness = [(by_mac[camera]['id'], camera, by_mac[camera]['name'], by_mac[camera]['model']) for camera in cameras]
        detector = target.get('live_pool_detector', {})
        route = detector.get('provider_config', {})
        public = {'capture_cameras': ', '.join(row[2] for row in witness) or 'None — all local capture disabled',
            'object_processing': route.get('model') or ('local pinned detector' if detector else 'disabled'),
            'media_destination': route.get('base_url') or ('local detector' if detector else 'none'),
            'face_camera_permissions': len(target.get('live_face', {}).get('cameras', [])),
            'plate_camera_permissions': len(detector.get('plate_cameras', [])),
            'speech_camera_permissions': len(target.get('live_speech_cameras', [])),
            'sound_camera_permissions': len(target.get('live_sound', {}).get('cameras', [])),
            'native_pairing': 'unchanged; configure and verify separately in Protect'}
        return {'public': public, 'witness': witness}

    async def validate(self, grant):
        details = await self.enrich(grant['preview'])
        if details != grant['details'] or not self.devices._live(grant):
            return False
        # Lock waiting cannot turn a fresh named-camera observation into an
        # indefinite capture grant. Expiry never renews the original review.
        grant['fresh_inventory_deadline'] = self.devices.clock() + 5
        return True
