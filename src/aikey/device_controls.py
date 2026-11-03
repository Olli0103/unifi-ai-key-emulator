"""Typed device reviews, private one-use authority and durable write admission.

Only configured server-owned profiles are addressable. Draft certificates and
configuration bytes stay server-side, never in confirmation hidden fields,
query strings, audit records or browser storage. No runtime is restarted.
"""
import asyncio
import re
import secrets
import threading
import time

from aiohttp import web

from .admin_api import PREFIX, error, reply
from .control_site import _page, _safe
from .device_workflow import DeviceOutcomeUncertain, DeviceWorkflowStore
from .protocol import _json_object
from .controller_trust_probe import probe

_TOKEN = re.compile(r'[0-9a-f]{64}\Z')
_OPERATIONS = ('controller-preview', 'rollback-preview', 'reconcile-preview', 'apply', 'probe')
_CONSENTS = {
    'controller_destination_and_pin_reviewed': 'I reviewed the controller destination and all certificate fingerprints.',
    'explicit_certificate_pin_instead_of_hostname': 'I approve the explicit certificate pin instead of hostname matching.',
    'restore_prior_saved_device_settings': 'Restore the reviewed prior saved controller settings.',
    'reconcile_saved_device_transaction': 'Inspect the saved bytes and settle this transaction without rewriting configuration.',
    'reviewed_camera_capture_destination_and_cost': 'I approve the listed local capture cameras, object-processing destination and potential cost; removed cameras lose their saved feature permissions.',
    'native_pairing_is_separate': 'This is only the local relay allowlist. Native camera pairing and zones must be configured in Protect separately.',
    'restore_prior_camera_capture_and_feature_permissions': 'I approve restoring the reviewed prior capture and face, plate, audio or sound permissions, where present.',
    'explicit_remote_camera_frames_and_cost': 'I approve sending these cameras’ selected frames to the named remote provider and its retention/charging policy after activation.',
    'onvif_metadata_only_no_ingest': 'Save ONVIF registration metadata only. Ingest, discovery, PTZ and recording are not implemented or tested; do not enable camera processing.',
    'onvif_private_credential_references': 'Store independent private ONVIF credential references for this profile; never reuse the AI Key or another Port’s credentials.',
    'onvif_unencrypted_credentials_if_future_ingest': 'The selected ONVIF transport is unencrypted. Any future ingest would need separate explicit approval before sending credentials.',
    'restore_onvif_saved_credentials': 'Restore the reviewed prior ONVIF metadata and its retained private credential references only; do not start ingest.',
    'disable_saved_local_feature':'Disable this role in saved settings only. Running jobs are not cancelled and private data is retained.',
    'local_service_destination_and_role_contract':'I reviewed the local destination and fixed role/model contract. Save configuration only; do not load a model or send media.',
    'face_recognition_named_camera_scope':'I approve local named recognition only for the listed cameras, subject to the displayed native-face policy.',
    'native_face_camera_processing':'Also allow local recognition for the selected cameras that report native face detection.',
    'controller_requested_deep_or_enhancement_scope':'I approve the displayed controller-requested crop/text scope; this role does not provide a local per-camera allowlist.',
    'restore_saved_local_feature_scope':'Restore the reviewed prior local feature destinations and processing permissions; do not alter templates or running jobs.',
    'disable_saved_port_feature':'Disable this saved feature only. Running capture is unchanged until separate activation; private assets and model-space guards are retained.',
    'port_named_feature_camera_scope':'I approve this one feature for the listed cameras only; do not change pairing, other features or running jobs.',
    'port_face_processing':'I approve local 512-value face processing for native grouping using the selected fixed model set. This is not Key named-identity enrollment.',
    'port_speech_processing':'I approve audio capture and speech-presence events for these cameras and the displayed existing hourly policy.',
    'port_sound_processing':'I approve local sound classification for these already audio-permitted cameras, the selected sound types and existing hourly policy.',
    'restore_saved_port_feature_scope':'Restore the reviewed prior feature permissions and model references. Retain an established face-model compatibility guard; do not start capture.',
}
_FIELD_LABELS={
    'controller.host':'Controller destination','controller.expected_fingerprint':'Controller certificate fingerprint',
    'controller.trust_ref':'Controller certificate trust','controller.verify_hostname':'Certificate hostname matching',
    'controller.search_trust_ref':'Search-channel certificate trust',
    'controller.search_expected_fingerprint':'Search-channel certificate fingerprint',
    'controller_ip':'Controller destination','controller_pin':'Controller certificate fingerprint',
    'controller_trust_ref':'Controller certificate trust','paired_streams':'Allowed relay cameras',
    'live_pool_detector':'Object detector and camera policy','live_face':'Face permissions',
    'live_sound':'Sound permissions','live_speech_cameras':'Audio permissions',
    'live_speech_max_events_per_hour':'Speech-event policy',
    'face_model_space_ref':'Retained face-model compatibility guard',
    'onvif_catalog_ref':'ONVIF registrations and private credential references',
}


class DeviceControls:
    def __init__(self, api):
        self.api, self.site = api, api.site
        self.stores = ({'aikey': DeviceWorkflowStore('aikey', self.site.aikey, self.site.aikey_state_dir)}
                       if self.site.aikey is not None else {})
        self.stores.update({name: DeviceWorkflowStore(name, store, store.path.parent)
                            for name, store in self.site.aiports.items()})
        self.clock = time.monotonic
        self.grants, self.epochs = {}, {}
        self.lock = threading.RLock()
        self.probe_runner = probe
        self.review_enricher = None
        self.mutation_validator = None

    def register(self, app):
        app.on_cleanup.append(self.close)
        app.router.add_get('/devices', self.page)
        app.router.add_get(PREFIX + '/device-workflows', self.status)
        for operation in _OPERATIONS:
            app.router.add_post('/devices/' + operation, self.handle, name='device-' + operation)
            app.router.add_post(PREFIX + '/device-workflows/' + operation, self.handle,
                                name='device-api-' + operation)

    def revoke_session(self,cookie):
        with self.lock:
            self.grants={token:grant for token,grant in self.grants.items() if grant['cookie']!=cookie}

    async def close(self,_):
        # Registered after AdminAPI.drain: admitted commits settle first.
        with self.lock:
            self.grants.clear()

    def _live(self, grant):
        with self.lock:
            return (self.clock() < grant['deadline']
                    and self.clock() < grant.get('fresh_inventory_deadline', float('inf'))
                    and self.epochs.get(grant['profile'], 0) == grant['epoch']
                    and self.site.security.csrf_token(grant['cookie']) is not None)

    def _issue(self, cookie, profile, action, preview, *, epoch, details=None):
        with self.lock:
            self.grants = {token: row for token, row in self.grants.items()
                           if self.clock() < row['deadline']
                           and self.site.security.csrf_token(row['cookie']) is not None}
            if (len(self.grants) >= 64 or self.site.security.csrf_token(cookie) is None
                    or epoch != self.epochs.get(profile, 0)):
                raise ValueError('device_review_unavailable')
            token = secrets.token_hex(32)
            self.grants[token] = {'cookie': cookie, 'profile': profile, 'action': action,
                'preview': preview, 'deadline': self.clock() + 600,
                'epoch': self.epochs.get(profile, 0), 'details': details}
            return token

    def _take(self, cookie, token):
        # An identifiable authentic confirmation is spent before semantic
        # validation or any await. A different session cannot burn its owner.
        with self.lock:
            if not isinstance(token, str) or not _TOKEN.fullmatch(token):
                raise ValueError
            grant = self.grants.get(token)
            if grant is None or grant['cookie'] != cookie:
                raise ValueError
            live = self._live(grant)
            self.grants = {key: row for key, row in self.grants.items() if row['profile'] != grant['profile']}
            epoch = self.epochs.get(grant['profile'], 0) + 1
            self.epochs[grant['profile']] = epoch
            if not live:
                raise ValueError
            return dict(grant, epoch=epoch)

    def _admission(self, grant):
        # Called by the store AFTER waiting for its OS lock and validating
        # current bytes, immediately before its first write. Log only codes.
        with self.lock:
            if not self._live(grant) or self.site.audit is None:
                return False
            if not self.site.audit.record('device_configuration', 'admitted', profile=grant['profile']):
                return False
            return self._live(grant)

    async def _statuses(self):
        rows = []
        for profile, store in self.stores.items():
            try:
                current = await asyncio.to_thread(store.controller_status)
                current['transaction'] = await asyncio.to_thread(store.transaction_status)
                current['available'] = True
            except (OSError, ValueError, KeyError, RuntimeError):
                current = {'profile': profile, 'available': False, 'reason': 'profile_not_ready'}
            rows.append(current)
        return rows

    async def status(self, request):
        if not self.api.authenticated(request):
            return error('authentication_required', 401)
        if request.query:
            return error('invalid_request', 400)
        rows = await self._statuses()
        if not self.api.authenticated(request):
            return error('authentication_required', 401)
        return reply({'devices': rows, 'durable_audit_configured': self.site.audit is not None,
                      'activation': 'saved_not_active_runtime'})

    @staticmethod
    def _hidden(fields):
        return ''.join(f"<input type='hidden' name='{_safe(key)}' value='{_safe(value)}'>"
                       for key, value in fields.items())

    async def page(self, request):
        cookie = self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther('/login')
        rows = await self._statuses()
        csrf = self.site.security.csrf_token(cookie)
        if csrf is None or self.site._session(request) is None:
            raise web.HTTPSeeOther('/login')
        body = ("<h1>Device configuration</h1><p><a href='/'>Settings</a> · "
                "<a href='/camera-pairing'>Camera pairing and capture</a> · "
                "<a href='/onvif-cameras'>ONVIF references (ingest unsupported)</a> · "
                "<a href='/port-features'>AI Port feature permissions</a> · "
                "<a href='/runtime-status'>Active runtime</a></p>"
                "<p>Review controller trust independently for each existing device. "
                "Device certificates, private keys and adopted identities are retained. "
                "Saving does not restart a service or prove native adoption.</p>"
                "<p>A controller test makes only an anonymous TLS handshake: no device "
                "credentials, HTTP request or model inference. Review the independently "
                "obtained certificate fingerprint before saving.</p>")
        if self.site.audit is None:
            body += "<p>Saving unavailable: the host must configure the durable audit log.</p>"
        if request.query.get('saved') == '1':
            body += "<p class='notice'>Saved bytes read back. Running service unchanged.</p>"
        for row in rows:
            profile = row['profile']
            body += f"<section><h2>{_safe(profile)}</h2>"
            if not row['available']:
                body += "<p>Profile not ready; no device identity is created or replaced here.</p></section>"
                continue
            base = self._hidden({'csrf': csrf, 'profile': profile, 'revision': row['revision']})
            txn = row['transaction']
            if txn['pending']:
                body += "<p>A prior write needs reconciliation. No new device edits or activation are admitted.</p>"
                if txn['reconcile_available']:
                    body += ("<form method='post' action='/devices/reconcile-preview'>" + base
                             + "<button>Review saved-byte reconciliation</button></form>")
            else:
                body += ("<form method='post' action='/devices/controller-preview'>" + base
                    + f"<label>Controller host<input name='host' value='{_safe(row['host'])}' maxlength='253' required></label>"
                    "<label>Controller public certificate (PEM)<textarea name='certificate_pem' "
                    "maxlength='16384' required autocomplete='off'></textarea></label>"
                    "<label>Reviewed SHA-256 fingerprint<input name='fingerprint' maxlength='95' required autocomplete='off'></label>")
                if profile == 'aikey':
                    checked = ' checked' if row['verify_hostname'] else ''
                    body += (f"<label><input type='checkbox' name='verify_hostname' value='yes'{checked}>"
                        "Match certificate hostname as well as its exact pin</label>"
                        "<fieldset><legend>Separate search certificate, if replacing it</legend>"
                        "<p>Leave both fields blank to retain existing search trust, if any.</p>"
                        "<label>Search public certificate (PEM)<textarea name='search_certificate_pem' maxlength='16384' autocomplete='off'></textarea></label>"
                        "<label>Search SHA-256 fingerprint<input name='search_fingerprint' maxlength='95' autocomplete='off'></label></fieldset>")
                body += "<button>Review controller change</button></form>"
                if txn['rollback_available']:
                    body += ("<form method='post' action='/devices/rollback-preview'>" + base
                             + "<p>Undo normally restores exact prior bytes. A first face-model binding retains its "
                             "vector-space guard to protect existing native groups.</p>"
                             + "<button>Review prior saved settings</button></form>")
            body += '</section>'
        return _page('Device configuration', "<div class='device-workflow'>"+body+'</div>')

    def _review(self, cookie, profile, action, preview, *, json_mode, epoch, probe_result=None, details=None):
        token = self._issue(cookie, profile, action, preview, epoch=epoch, details=details)
        public = preview.public() if action != 'reconcile' else {
            'profile': profile, 'current_revision': preview,
            'required_consents': ['reconcile_saved_device_transaction'],
            'activation': 'saved_not_active_runtime'}
        if json_mode:
            return reply({'review': public, 'review_token': token, 'expires_in_seconds': 600,
                          'probe': probe_result, 'details': details['public'] if details else None})
        csrf = self.site.security.csrf_token(cookie)
        if csrf is None:
            raise ValueError
        body = (f"<h1>Review device configuration</h1><p>Profile: {_safe(profile)}.</p>"
                "<p>Saved configuration only; no identity replacement, restart or native adoption.</p>")
        if action != 'reconcile':
            if preview.operation.startswith('controller'):
                body += (f"<p>Controller: {_safe(public['controller_destination'])}</p><ul>"
                    + ''.join(f"<li>{_safe(row['channel'].title())} certificate SHA-256: {_safe(row['fingerprint'])}</li>"
                              for row in public['controller_channel_pins']) + '</ul>')
            elif preview.operation.startswith('camera_pool'):
                body += f"<p>Protect relay: {_safe(public['controller_destination'])}. Existing controller trust unchanged.</p>"
            body += ('<p>Changed settings: ' + _safe(', '.join(_FIELD_LABELS.get(name,name.replace('_',' ').replace('.',' '))
                       for name in public['changed_fields']) or 'none') + '</p>')
        if probe_result is not None:
            body += '<p>Anonymous TLS test: ' + ('matched' if probe_result['matched'] else 'not matched') + '.</p>'
        if details:
            body += '<h2>Saved settings review</h2><dl>'
            for name, value in details['public'].items():
                body += f'<dt>{_safe(name.replace("_", " "))}</dt><dd>{_safe(value)}</dd>'
            body += '</dl>'
        hidden = self._hidden({'csrf': csrf, 'profile': profile, 'review_token': token})
        body += "<form method='post' action='/devices/apply'>" + hidden
        for consent in public['required_consents']:
            body += (f"<label><input type='checkbox' name='{_safe(consent)}' value='yes' required>"
                     f"{_safe(_CONSENTS[consent])}</label>")
        body += '<button>Confirm saved configuration</button></form>'
        if action == 'controller':
            body += ("<form method='post' action='/devices/probe'>" + hidden
                     + "<button>Test reviewed anonymous controller TLS</button></form>")
        return _page('Review device configuration', "<div class='device-workflow'>"+body + "<p><a href='/devices'>Cancel</a></p></div>")

    @staticmethod
    def _refused(json_mode, *, uncertain=False):
        if json_mode:
            return error('outcome_uncertain' if uncertain else 'device_review_refused', 503 if uncertain else 409)
        return _page('Device change not confirmed', '<h1>Device change not confirmed</h1><p>'
            + ('The outcome needs saved-byte reconciliation. Do not retry the old request.' if uncertain else
               'Review expired, changed, missing confirmation or unavailable. Start a fresh review; do not replay this form.')
            + "</p><p><a href='/devices'>Inspect current device state</a></p>", status=503 if uncertain else 409)

    async def handle(self, request):
        json_mode = request.path.startswith(PREFIX + '/')
        operation = request.match_info.route.name.removeprefix('device-api-' if json_mode else 'device-')
        if json_mode and not self.api.authenticated(request, mutate=True):
            return error('forbidden', 403)
        try:
            if request.query:
                raise ValueError
            if json_mode:
                if request.content_type != 'application/json':
                    raise ValueError
                source = request.clone(client_max_size=65536) if operation == 'controller-preview' else request
                fields = _json_object(await source.read())
                cookie = self.api.authenticated(request)
            else:
                if request.content_type != 'application/x-www-form-urlencoded':
                    raise ValueError
                source = request.clone(client_max_size=65536) if operation == 'controller-preview' else request
                form = await source.post()
                if any(len(form.getall(key)) != 1 or not isinstance(form[key], str) for key in form):
                    raise ValueError
                fields = dict(form)
                cookie = self.site._session(request, mutate=True, csrf=fields.pop('csrf', None))
            if cookie is None:
                if json_mode:
                    return error('forbidden', 403)
                raise web.HTTPForbidden()
            if operation in ('apply', 'probe'):
                grant = self._take(cookie, fields.get('review_token'))
                if fields.get('profile') != grant['profile']:
                    raise ValueError
                if operation == 'probe':
                    if set(fields) != {'profile', 'review_token'} or grant['action'] != 'controller':
                        raise ValueError
                    return await self._probe(grant, json_mode)
                consents = (set(grant['preview'].required_consents) if grant['action'] != 'reconcile'
                            else {'reconcile_saved_device_transaction'})
                if json_mode:
                    supplied = fields.get('acknowledgements')
                    if (set(fields) != {'profile', 'review_token', 'acknowledgements'}
                            or not isinstance(supplied, list) or any(not isinstance(v, str) for v in supplied)
                            or len(set(supplied)) != len(supplied) or set(supplied) != consents):
                        raise ValueError
                elif set(fields) != {'profile', 'review_token'} | consents or any(fields[c] != 'yes' for c in consents):
                    raise ValueError
                return await self.api.file_operation(self._commit, grant, consents, json_mode)
            profile = fields.get('profile')
            if not isinstance(profile, str) or profile not in self.stores:
                raise ValueError
            store = self.stores[profile]
            with self.lock:
                epoch = self.epochs.get(profile, 0)
            if operation == 'controller-preview':
                if json_mode:
                    if set(fields) != {'profile', 'revision', 'selection'}:
                        raise ValueError
                    selection = fields['selection']
                else:
                    required = {'profile', 'revision', 'host', 'certificate_pem', 'fingerprint'}
                    allowed = required | ({'verify_hostname', 'search_certificate_pem', 'search_fingerprint'} if profile == 'aikey' else set())
                    if not required <= set(fields) <= allowed:
                        raise ValueError
                    selection = {key: fields[key] for key in ('host', 'certificate_pem', 'fingerprint')}
                    if profile == 'aikey':
                        if fields.get('verify_hostname') not in (None, 'yes'):
                            raise ValueError
                        selection['verify_hostname'] = fields.get('verify_hostname') == 'yes'
                        search = {key: fields.get(key, '').strip() for key in ('search_certificate_pem', 'search_fingerprint')}
                        if any(search.values()):
                            selection.update(search)
                status = await asyncio.to_thread(store.transaction_status)
                if status['pending']:
                    raise ValueError
                preview = await asyncio.to_thread(store.preview_controller, fields['revision'], selection)
                action = 'controller'
            else:
                if set(fields) != {'profile', 'revision'}:
                    raise ValueError
                if operation == 'rollback-preview':
                    preview = await asyncio.to_thread(store.preview_rollback, fields['revision'])
                    action = 'rollback'
                else:
                    status = await asyncio.to_thread(store.transaction_status)
                    if not status['reconcile_available'] or status['revision'] != fields['revision']:
                        raise ValueError
                    preview, action = fields['revision'], 'reconcile'
            if self.site._session(request) is None:
                raise ValueError
            details = await self.review_enricher(preview) if self.review_enricher and action != 'reconcile' else None
            if self.site._session(request) is None:
                raise ValueError
            return self._review(cookie, profile, action, preview, json_mode=json_mode, epoch=epoch, details=details)
        except web.HTTPRequestEntityTooLarge:
            return error('request_too_large', 413) if json_mode else self._refused(False)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            return self._refused(json_mode)

    async def _commit(self, grant, consents, json_mode):
        store = self.stores[grant['profile']]
        def admission():
            return self._admission(grant)
        try:
            if (grant['action'] != 'reconcile' and grant['preview'].operation.startswith(('camera_pool','key_services','port_features'))
                    and (self.mutation_validator is None or not await self.mutation_validator(grant))):
                raise ValueError
            if grant['action'] == 'reconcile':
                result = await asyncio.to_thread(store.reconcile, grant['preview'], admission=admission)
            else:
                method = store.apply if grant['action'] in ('controller','camera_pool','onvif_catalog','key_services','port_features') else store.rollback
                result = await asyncio.to_thread(method, grant['preview'], acknowledgements=consents, admission=admission)
        except DeviceOutcomeUncertain:
            self.site._audit('device_configuration', 'uncertain', grant['profile'])
            return self._refused(json_mode, uncertain=True)
        except (OSError, ValueError, KeyError, RuntimeError):
            self.site._audit('device_configuration', 'refused', grant['profile'])
            return self._refused(json_mode)
        # A lost completion audit cannot turn read-back saved bytes into an
        # alleged failed/no-effect write or justify automatic retry.
        recorded = self.site.audit.record('device_configuration', 'read_back', profile=grant['profile'])
        result.update(completion_audit_recorded=recorded, automatic_retry=False)
        if json_mode:
            return reply(result)
        if not recorded:
            return _page('Saved device configuration', '<h1>Saved bytes read back</h1>'
                "<p>Completion audit unavailable. Do not retry. Running service unchanged.</p><a href='/devices'>Inspect saved state</a>")
        raise web.HTTPSeeOther('/devices?saved=1')

    async def _probe(self, grant, json_mode):
        store = self.stores[grant['profile']]
        if not self._live(grant) or self.site.audit is None:
            raise ValueError
        spec = await asyncio.to_thread(store.probe_spec, grant['preview'])
        if not self._live(grant) or not self.site.audit.record('controller_probe', 'admitted', profile=grant['profile']):
            raise ValueError
        report = await self.probe_runner(**spec, admission=lambda: self._live(grant))
        # Revalidate bytes and identity after network I/O. An expired lease or
        # competing mutation cannot acquire fresh Save authority from a test.
        await asyncio.to_thread(store.probe_spec, grant['preview'])
        if not self._live(grant):
            raise ValueError
        self.site._audit('controller_probe', 'matched' if report['matched'] else 'not_matched', grant['profile'])
        return self._review(grant['cookie'], grant['profile'], 'controller', grant['preview'],
                            json_mode=json_mode, epoch=grant['epoch'], probe_result=report)
