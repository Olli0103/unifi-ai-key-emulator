"""Typed local-role setup using shared validators and device transactions."""
import asyncio
import hashlib
import hmac
import json
import secrets

from aiohttp import ClientError,web

from .admin_api import PREFIX,error,reply
from .admin_inventory import inventory_view
from .control_site import _page,_safe
from .key_service_config import ROLES
from .protocol import _json_object
from . import deep_mode

_LABELS={'face_recognition':'Local named recognition','face_enhancement':'Local face enhancement',
         'deep_understanding':'Local deep-session services'}
_CONTRACTS={'face_recognition':'SFace-compatible 128-value embeddings; existing private templates retained',
    'face_enhancement':'Server-managed bounded JPEG face derivative; original image retained',
    'deep_understanding':f'{deep_mode.REID_MODEL}: {deep_mode.REID_SOURCE_DIMENSIONS} source / {deep_mode.REID_DIMENSIONS} stored values; optional text reranking'}
_FAILURES=(OSError,ValueError,TypeError,KeyError,RuntimeError,ClientError,TimeoutError)


class KeyServiceControls:
    def __init__(self,api):
        self.api,self.site=api,api.site
        self.devices=api.device_controls
        self.store=self.devices.stores['aikey']
        self.key=secrets.token_bytes(32)
        self.previous_enricher=self.devices.review_enricher
        self.previous_validator=self.devices.mutation_validator
        self.devices.review_enricher=self.enrich
        self.devices.mutation_validator=self.validate

    def register(self,app):
        app.router.add_get('/local-feature-services',self.page)
        app.router.add_get(PREFIX+'/key-services',self.status)
        app.router.add_post('/local-feature-services/preview',self.preview)
        app.router.add_post(PREFIX+'/device-workflows/key-service-preview',self.preview)

    async def _cameras(self,*,fresh):
        if self.site.inventory_loader is None:
            raise ValueError
        async with asyncio.timeout(12):
            report=await self.site.inventory_loader() if fresh else await self.site.inventory_snapshot()
        rows=inventory_view(report,{})['cameras']
        if any(not row['name'] for row in rows) or len({row['name'].casefold() for row in rows})!=len(rows):
            raise ValueError
        return [row for row in rows if row['state']=='CONNECTED']

    def _choices(self,cookie,revision,rows):
        return {'face_camera_'+hmac.new(self.key,json.dumps(['initial-face-policy/1',cookie,revision,
            row['id'],row['name'],row['model']],separators=(',',':')).encode(),hashlib.sha256).hexdigest():row for row in rows}

    async def status(self,request):
        cookie=self.api.authenticated(request)
        if not cookie:
            return error('authentication_required',401)
        if request.query:
            return error('invalid_request',400)
        try:
            value=await asyncio.to_thread(self.store.key_service_status)
        except _FAILURES:
            return error('local_services_unavailable',503)
        try:
            labels=self._choices(cookie,value['revision'],await self._cameras(fresh=False))
            retained=(value['roles'].get('face_recognition') or {}).get('camera_ids',[])
            choices=[{'choice':key,'name':row['name'],'selected':row['id'] in retained} for key,row in labels.items()]
        except _FAILURES:
            choices=None
        if value['roles'].get('face_recognition'):
            policy=value['roles']['face_recognition']
            policy['camera_count']=len(policy.pop('camera_ids'))
        if not self.api.authenticated(request):
            return error('authentication_required',401)
        return reply(dict(value,camera_choices=choices,network_requests=0,models_loaded=False))

    async def page(self,request):
        cookie=self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther('/login')
        try:
            status=await asyncio.to_thread(self.store.key_service_status)
        except _FAILURES:
            return self.devices._refused(False)
        try:
            choices=self._choices(cookie,status['revision'],await self._cameras(fresh=False))
        except _FAILURES:
            choices=None
        csrf=self.site.security.csrf_token(cookie)
        if csrf is None or self.site._session(request) is None:
            raise web.HTTPSeeOther('/login')
        body=("<h1>Local feature services</h1><p><a href='/'>Settings</a> · <a href='/devices'>Devices</a> · "
            "<a href='/model-registry'>Saved model contracts</a></p>"
            "<p>Configure existing local roles without editing files. Services must already be installed "
            "and match the fixed model/vector contract. No weights are downloaded, model loaded, media "
            "sent or quality tested here. Only literal private/loopback HTTP roots are supported. A LAN "
            "address is not proof of on-host processing or encryption.</p>"
            "<p>Saved settings only; separate activation is required. Disable does not cancel running "
            "jobs or delete data. Face templates, keys, other roles and search encoders are retained.</p>")
        for role in ROLES:
            current=status['roles'][role] or {}
            base={'csrf':csrf,'revision':status['revision'],'role':role}
            body+=f"<section><h2>{_safe(_LABELS[role])}</h2><p>{_safe(_CONTRACTS[role])}</p>"
            if role=='face_recognition' and choices is None:
                body+='<p>Initial/replacement scope needs a fresh, unambiguous named camera inventory. Saved role disable remains available.</p>'
            else:
                body+="<form method='post' action='/local-feature-services/preview'>"+self.devices._hidden(dict(base,mode='configure'))
                fields=(('reid_server','Local person re-identification service'),('rerank_server','Optional local text reranking service')) if role=='deep_understanding' else (('server','Local service address'),)
                for field,label in fields:
                    required=' required' if field!='rerank_server' else ''
                    body+=f"<label>{label}<input name='{field}' value='{_safe(current.get(field,''))}' maxlength='256'{required}></label>"
                if role=='face_recognition':
                    body+='<fieldset><legend>Named camera permissions (one to eight)</legend>'
                    for token,row in choices.items():
                        checked=' checked' if row['id'] in current.get('camera_ids',[]) else ''
                        body+=f"<label><input type='checkbox' name='{token}' value='yes'{checked}>{_safe(row['name'])}</label>"
                    checked=' checked' if current.get('native_face_cameras')=='process' else ''
                    body+=(f"</fieldset><label>Maximum faces per image<input name='max_faces' type='number' min='1' "
                        f"max='16' value='{current.get('max_faces',8)}' required></label>"
                        f"<label><input type='checkbox' name='process_native_faces' value='yes'{checked}>"
                        "Also process selected cameras reporting native face detection</label>")
                else:
                    body+=('<p>Scope: authenticated controller-requested crops or session text. These existing '
                        'roles have no local per-camera allowlist; this is not a named-camera restriction.</p>')
                body+='<button>Review local feature policy</button></form>'
            if current:
                body+=("<form method='post' action='/local-feature-services/preview'>"+self.devices._hidden(dict(base,mode='disable'))
                       +'<button>Review disabling this saved role</button></form>')
            body+='</section>'
        return _page('Local feature services',"<div class='device-workflow'>"+body+'</div>')

    async def preview(self,request):
        json_mode=request.path.startswith(PREFIX+'/')
        try:
            if request.query:
                raise ValueError
            if json_mode:
                cookie=self.api.authenticated(request,mutate=True)
                if cookie is None:
                    return error('forbidden',403)
                if request.content_type!='application/json':
                    raise ValueError
                fields=_json_object(await request.read())
            else:
                if request.content_type!='application/x-www-form-urlencoded':
                    raise ValueError
                form=await request.post()
                cookie=self.site._session(request,mutate=True,csrf=form.get('csrf'))
                if cookie is None:
                    raise web.HTTPForbidden()
                if any(len(form.getall(key))!=1 or not isinstance(form[key],str) for key in form):
                    raise ValueError
                fields=dict(form)
                fields.pop('csrf')
            base={'revision','role','mode'}
            if not base<=set(fields) or fields['role'] not in ROLES or fields['mode'] not in ('configure','disable'):
                raise ValueError
            with self.devices.lock:
                epoch=self.devices.epochs.get('aikey',0)
            role=fields['role']
            selection={'role':role,'mode':fields['mode']}
            if fields['mode']=='disable':
                if set(fields)!=base:
                    raise ValueError
            else:
                names=({'server','choices','max_faces','process_native_faces'} if role=='face_recognition'
                    else {'server'} if role=='face_enhancement' else {'reid_server','rerank_server'})
                if json_mode:
                    if set(fields)!=base|{'settings'} or not isinstance(fields['settings'],dict) or set(fields['settings'])!=names:
                        raise ValueError
                    settings=fields['settings']
                else:
                    chosen=[key for key in fields if key.startswith('face_camera_')]
                    fixed=names-{'choices','process_native_faces'} if role=='face_recognition' else names
                    optional={'process_native_faces'} if role=='face_recognition' else set()
                    if not (base|fixed)<=set(fields)<=(base|fixed|optional|set(chosen)) or any(fields[key]!='yes' for key in chosen):
                        raise ValueError
                    settings={key:fields[key] for key in fixed}
                    if role=='face_recognition':
                        if fields.get('process_native_faces') not in (None,'yes') or not fields['max_faces'].isascii() or not fields['max_faces'].isdecimal():
                            raise ValueError
                        settings.update(choices=chosen,max_faces=int(fields['max_faces']),process_native_faces='process_native_faces' in fields)
                if role=='face_recognition':
                    chosen=settings['choices']
                    if (not isinstance(chosen,list) or not 1<=len(chosen)<=8 or any(not isinstance(v,str) for v in chosen)
                            or len(set(chosen))!=len(chosen) or type(settings['process_native_faces']) is not bool):
                        raise ValueError
                    choices=self._choices(cookie,fields['revision'],await self._cameras(fresh=True))
                    if not set(chosen)<=set(choices):
                        raise ValueError
                    selection.update(server=settings['server'],max_faces=settings['max_faces'],camera_ids=[choices[key]['id'] for key in chosen],
                        native_face_cameras='process' if settings['process_native_faces'] else 'skip')
                else:
                    selection.update(settings)
            preview=await asyncio.to_thread(self.store.preview_key_service,fields['revision'],selection)
            details=await self.enrich(preview)
            if self.site._session(request) is None:
                raise ValueError
            return self.devices._review(cookie,'aikey','key_services',preview,json_mode=json_mode,epoch=epoch,details=details)
        except _FAILURES:
            return self.devices._refused(json_mode)

    async def enrich(self,preview):
        if not preview.operation.startswith('key_services'):
            return await self.previous_enricher(preview) if self.previous_enricher else None
        role=(_json_object(preview._selection)['role'] if preview.operation=='key_services'
              else next((key for key in preview.changed_fields if key in ROLES),None))
        if role is None:
            raise ValueError
        target=_json_object(preview._encoded).get(role)
        witness=[]
        if target and role=='face_recognition':
            rows={row['id']:row for row in await self._cameras(fresh=True)}
            if not set(target['camera_ids'])<=set(rows):
                raise ValueError
            witness=[(identifier,rows[identifier]['name'],rows[identifier]['model']) for identifier in target['camera_ids']]
        scope=(', '.join(row[1] for row in witness) if witness else
            'authenticated controller-requested jobs; no local per-camera allowlist' if target else 'disabled in saved configuration')
        return {'public':{'feature':_LABELS[role],'model_contract':_CONTRACTS[role],
            'saved_destinations':'; '.join(str(value) for key,value in (target or {}).items() if key in ('server','reid_server','rerank_server')) or 'none',
            'processing_scope':scope,'native_face_camera_policy':(target or {}).get('native_face_cameras','not applicable'),
            'models_loaded_or_network_requests':0,'running_jobs_or_identity_changed':False},'witness':witness}

    async def validate(self,grant):
        if not grant['preview'].operation.startswith('key_services'):
            return await self.previous_validator(grant) if self.previous_validator else False
        details=await self.enrich(grant['preview'])
        if details!=grant['details'] or not self.devices._live(grant):
            return False
        if details['witness']:
            grant['fresh_inventory_deadline']=self.devices.clock()+5
        return True
