"""Named Port feature enrollment with host-bound local model references."""
import asyncio
import hashlib
import hmac
import json
import secrets

from aiohttp import ClientError,web

from .admin_api import PREFIX,error,reply
from .aiport_ingest import normalize_mac
from .aiport_sounds import SOUND_TYPES
from .control_site import _page,_safe
from .port_feature_config import ROOTS
from .port_feature_models import load,current_bindings,fingerprint
from .protocol import _json_object

_FAILURES=(OSError,ValueError,TypeError,KeyError,RuntimeError,ClientError,TimeoutError)
_LABELS={'face':'Native face embeddings','speech':'Audio capture and speech presence','sound':'Local sound classification'}
_SOUNDS={'alrmSmoke':'Smoke alarm','alrmCmonx':'Carbon-monoxide alarm','alrmSiren':'Siren','alrmBabyCry':'Baby crying',
         'alrmBark':'Dog barking','alrmBurglar':'Burglar/car alarm','alrmCarHorn':'Vehicle horn','alrmGlassBreak':'Glass breaking'}


class PortFeatureControls:
    def __init__(self,api):
        self.api,self.site=api,api.site
        self.devices=api.device_controls
        self.key=secrets.token_bytes(32)
        self.previous_enricher,self.previous_validator=self.devices.review_enricher,self.devices.mutation_validator
        self.devices.review_enricher,self.devices.mutation_validator=self.enrich,self.validate

    def register(self,app):
        app.router.add_get('/port-features',self.page)
        app.router.add_get(PREFIX+'/port-features',self.status)
        app.router.add_post('/port-features/preview',self.preview)
        app.router.add_post(PREFIX+'/device-workflows/port-feature-preview',self.preview)

    def _token(self,cookie,profile,revision,feature,value):
        raw=json.dumps(['port-feature-choice/1',cookie,profile,revision,feature,value],separators=(',',':')).encode()
        return hmac.new(self.key,raw,hashlib.sha256).hexdigest()

    def _models(self,profile,config,feature):
        candidates=[load(path) for path in self.site.port_model_catalogs.get(profile,[])]+current_bindings(config)
        unique={}
        for value in candidates:
            if value['kind']==feature:
                identity=json.dumps(value['settings'],sort_keys=True,separators=(',',':'))
                unique.setdefault(identity,value)
        return list(unique.values())

    async def _inventory(self,*,fresh):
        return (await self.api.camera_pool_controls._inventory(fresh=fresh))[1]

    def _choices(self,cookie,profile,status,feature,rows):
        config=status['configuration']
        allowed={normalize_mac(row['camera_mac']) for row in config.get('paired_streams',[])}
        if feature=='sound':
            allowed&={normalize_mac(value) for value in config.get('live_speech_cameras',[])}
        return {'port_camera_'+self._token(cookie,profile,status['revision'],feature,
                [row['id'],row['mac'],row['name'],row['model']]):row
                for row in rows if row['mac'] in allowed and row['state']=='CONNECTED'}

    async def _rows(self,cookie):
        try:
            inventory=await self._inventory(fresh=False)
        except _FAILURES:
            inventory=None
        result=[]
        for profile in self.site.aiports:
            try:
                status=await asyncio.to_thread(self.devices.stores[profile].port_feature_status)
                features=[]
                for feature in ROOTS:
                    current=status['features'][feature]
                    selected=(current if feature=='speech' else (current or {}).get('cameras',[])) or []
                    selected={normalize_mac(value) for value in selected}
                    choices=self._choices(cookie,profile,status,feature,inventory) if inventory is not None else {}
                    models=self._models(profile,status['configuration'],feature) if feature!='speech' else []
                    features.append({'feature':feature,'configured':bool(current),
                        'choices':[{'choice':key,'name':row['name'],'selected':row['mac'] in selected} for key,row in choices.items()],
                        'models':[{'choice':self._token(cookie,profile,status['revision'],feature,fingerprint(value)),
                                   'name':value['name'],'publication_check':value['publication_check']} for value in models],
                        'eligible':bool(choices) and (feature=='speech' or bool(models)),
                        'types':list((current or {}).get('types',SOUND_TYPES)) if feature=='sound' else [],
                        'event_limit':status['configuration'].get('live_speech_max_events_per_hour',60) if feature=='speech'
                            else (current or {}).get('max_events_per_hour',20) if feature=='sound' else None})
                result.append({'profile':profile,'revision':status['revision'],'available':True,'features':features})
            except _FAILURES:
                result.append({'profile':profile,'available':False,'features':[]})
        return result

    async def status(self,request):
        cookie=self.api.authenticated(request)
        if not cookie:
            return error('authentication_required',401)
        if request.query:
            return error('invalid_request',400)
        rows=await self._rows(cookie)
        if not self.api.authenticated(request):
            return error('authentication_required',401)
        return reply({'profiles':rows,'activation':'saved_not_active_runtime','models_loaded':False})

    async def page(self,request):
        cookie=self.site._session(request)
        if cookie is None:
            raise web.HTTPSeeOther('/login')
        rows=await self._rows(cookie)
        csrf=self.site.security.csrf_token(cookie)
        if csrf is None or self.site._session(request) is None:
            raise web.HTTPSeeOther('/login')
        body=("<h1>AI Port feature permissions</h1><p><a href='/'>Settings</a> · <a href='/camera-pairing'>Capture cameras</a> · "
            "<a href='/devices'>Devices</a></p><p>Enroll already allowed relay cameras in one explicit feature at a time. "
            "Saving never starts capture, loads weights, sends media or changes native pairings. Model paths are "
            "host-bound choices, not browser-supplied files. A published SHA-256 is not model-format, quality, "
            "license or native compatibility proof.</p>"
            "<p>Face embeddings use the Port's fixed 512-value native-group workflow, not Key local 128-value named "
            "identities. The face vector space is retained across disable and Undo; switching encoder/preprocessing "
            "requires separate migration. Audio is required before sound enrollment; remove sound permissions before "
            "reducing parent audio scope. Existing speech/sound hourly policies are shown explicitly. This page "
            "does not introduce an object-detection event cap.</p>")
        for row in rows:
            body+=f"<section><h2>{_safe(row['profile'])}</h2>"
            if not row['available']:
                body+='<p>Profile/model references unavailable; inspect current state. No automatic repair.</p></section>'
                continue
            for feature in row['features']:
                kind=feature['feature']
                base={'csrf':csrf,'profile':row['profile'],'revision':row['revision'],'feature':kind}
                body+=f"<section><h3>{_safe(_LABELS[kind])}</h3>"
                if feature['eligible']:
                    body+="<form method='post' action='/port-features/preview'>"+self.devices._hidden(dict(base,mode='configure'))
                    for choice in feature['choices']:
                        checked=' checked' if choice['selected'] else ''
                        body+=f"<label><input type='checkbox' name='{choice['choice']}' value='yes'{checked}>{_safe(choice['name'])}</label>"
                    if kind!='speech':
                        body+=f"<label>Installed model set<select name='model_choice' aria-label='{_safe(_LABELS[kind])} model set' required>"
                        body+="<option value=''>Choose a host-bound model set</option>"+''.join(
                            f"<option value='{model['choice']}'>{_safe(model['name'])}</option>" for model in feature['models'])+'</select></label>'
                    if kind in ('speech','sound'):
                        maximum=3600 if kind=='speech' else 120
                        body+=(f"<label>Existing {kind} events per camera/hour<input name='event_limit' type='number' min='1' "
                               f"max='{maximum}' value='{feature['event_limit']}' required></label>")
                    if kind=='sound':
                        body+='<p>Smoke/CO alarms retain their separate four-times hourly share.</p>'
                        for value in SOUND_TYPES:
                            checked=' checked' if feature['configured'] and value in feature['types'] else ''
                            body+=f"<label><input type='checkbox' name='sound_{value}' value='yes'{checked}>{_safe(_SOUNDS[value])}</label>"
                    body+='<button>Review feature permissions</button></form>'
                else:
                    body+='<p>Setup needs fresh named cameras and an existing capture pool. Face/sound also require a host-published model binding; sound cameras need audio permission first.</p>'
                if feature['configured']:
                    body+="<form method='post' action='/port-features/preview'>"+self.devices._hidden(dict(base,mode='disable'))+'<button>Review disabling this saved feature</button></form>'
                body+='</section>'
            body+='</section>'
        if not rows:
            body+='<p>No AI Port profile is attached.</p>'
        return _page('AI Port feature permissions',"<div class='device-workflow'>"+body+'</div>')

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
            base={'profile','revision','feature','mode'}
            if (not base<=set(fields) or fields['profile'] not in self.site.aiports
                    or fields['feature'] not in ROOTS or fields['mode'] not in ('configure','disable')):
                raise ValueError
            profile,feature=fields['profile'],fields['feature']
            with self.devices.lock:
                epoch=self.devices.epochs.get(profile,0)
            store=self.devices.stores[profile]
            status=await asyncio.to_thread(store.port_feature_status)
            if status['revision']!=fields['revision']:
                raise ValueError
            selection={'feature':feature,'mode':fields['mode']}
            if fields['mode']=='disable':
                if set(fields)!=base:
                    raise ValueError
            else:
                required={'choices'}|({'event_limit'} if feature=='speech' else {'model_choice'})
                if feature=='sound':
                    required|={'event_limit','types'}
                if json_mode:
                    if set(fields)!=base|required:
                        raise ValueError
                    selected=fields['choices']
                else:
                    selected=[key for key in fields if key.startswith('port_camera_')]
                    types=[key.removeprefix('sound_') for key in fields if key.startswith('sound_')]
                    fixed=required-{'choices','types'}
                    allowed=base|fixed|set(selected)|{'sound_'+kind for kind in types}
                    if set(fields)!=allowed or any(fields[key]!='yes' for key in set(selected)|{'sound_'+kind for kind in types}):
                        raise ValueError
                    if types and feature!='sound':
                        raise ValueError
                    fields['types']=types
                    if 'event_limit' in required:
                        if not fields['event_limit'].isascii() or not fields['event_limit'].isdecimal():
                            raise ValueError
                        fields['event_limit']=int(fields['event_limit'])
                if (not isinstance(selected,list) or not 1<=len(selected)<=5 or any(not isinstance(value,str) for value in selected)
                        or len(set(selected))!=len(selected)):
                    raise ValueError
                choices=self._choices(cookie,profile,status,feature,await self._inventory(fresh=True))
                if not set(selected)<=set(choices):
                    raise ValueError
                selection['cameras']=[choices[key]['mac'] for key in selected]
                if feature!='speech':
                    bindings={self._token(cookie,profile,status['revision'],feature,fingerprint(value)):value
                        for value in self._models(profile,status['configuration'],feature)}
                    if fields['model_choice'] not in bindings:
                        raise ValueError
                    selection['model_settings']=bindings[fields['model_choice']]['settings']
                if 'event_limit' in required:
                    selection['event_limit']=fields['event_limit']
                if feature=='sound':
                    selection['types']=fields['types']
            preview=await asyncio.to_thread(store.preview_port_feature,fields['revision'],selection)
            details=await self.enrich(preview)
            if self.site._session(request) is None:
                raise ValueError
            return self.devices._review(cookie,profile,'port_features',preview,json_mode=json_mode,epoch=epoch,details=details)
        except _FAILURES:
            return self.devices._refused(json_mode)

    async def enrich(self,preview):
        if not preview.operation.startswith('port_features'):
            return await self.previous_enricher(preview) if self.previous_enricher else None
        feature=(_json_object(preview._selection)['feature'] if preview.operation=='port_features' else
            next((kind for kind,roots in ROOTS.items() if set(preview.changed_fields)&(roots|({'face_model_space_ref'} if kind=='face' else set()))),None))
        if feature is None:
            raise ValueError
        target=_json_object(preview._encoded)
        policy=target.get('live_'+feature if feature!='speech' else 'live_speech_cameras')
        cameras=(policy if feature=='speech' else (policy or {}).get('cameras',[])) or []
        witness=[]
        model=None
        if cameras:
            labels={row['mac']:row for row in await self._inventory(fresh=True) if row['state']=='CONNECTED'}
            normalized=[normalize_mac(value) for value in cameras]
            if not set(normalized)<=set(labels):
                raise ValueError
            witness=[(labels[mac]['id'],mac,labels[mac]['name'],labels[mac]['model']) for mac in normalized]
            if feature!='speech':
                matches=[value for value in current_bindings(target) if value['kind']==feature]
                if len(matches)!=1:
                    raise ValueError
                model=matches[0]
                if preview.operation=='port_features':
                    status=await asyncio.to_thread(self.devices.stores[preview.profile].port_feature_status)
                    actual=[value for value in self._models(preview.profile,status['configuration'],feature)
                            if value['settings']==model['settings']]
                    if not actual:
                        raise ValueError
                    model=actual[0]
        public={'feature':_LABELS[feature],'named_cameras':', '.join(row[2] for row in witness) or 'none; saved feature disabled',
            'model_set':model['name'] if model else 'none','model_availability_and_native_quality':'not tested by this configuration flow',
            'camera_media_requests_or_models_loaded':0,'face_vector_space_guard':'retained across disable and Undo' if target.get('face_model_space_ref') else 'not configured'}
        if feature in ('speech','sound') and policy:
            public['existing_events_per_camera_hour']=target.get('live_speech_max_events_per_hour',60) if feature=='speech' else policy.get('max_events_per_hour',20)
        if feature=='sound' and policy:
            public['sound_types']=', '.join(_SOUNDS[kind] for kind in policy.get('types',SOUND_TYPES))
        return {'public':public,'witness':witness,'model_fingerprint':fingerprint(model) if model else None}

    async def validate(self,grant):
        if not grant['preview'].operation.startswith('port_features'):
            return await self.previous_validator(grant) if self.previous_validator else False
        details=await self.enrich(grant['preview'])
        if details!=grant['details'] or not self.devices._live(grant):
            return False
        if details['witness']:
            grant['fresh_inventory_deadline']=self.devices.clock()+5
        return True
