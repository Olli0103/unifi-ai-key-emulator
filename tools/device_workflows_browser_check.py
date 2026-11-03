"""Owned fictional device workflows in isolated sandboxed installed Chrome."""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import ssl
import sys
import tempfile
import time
from urllib.parse import urlsplit

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))

from aiohttp.test_utils import TestServer  # noqa: E402
from aikey.admin_security import AdminSecurity  # noqa: E402
from aikey.config import atomic_private,initialize  # noqa: E402
from aikey.control_site import ControlSite  # noqa: E402
from aikey.tls import ensure_identity_certificate  # noqa: E402
from aikey.port_feature_models import publish  # noqa: E402

PASSWORD='fictional-device-workflow-admin'
PROVIDER_KEY='fictional-never-issued-device-browser-provider-key'
ONVIF_USER='fictional-ONVIF-browser-user'
ONVIF_PASSWORD='fictional-never-issued-ONVIF-browser-password'


async def clean_document(page):
    state=await page.evaluate('''() => ({html:document.documentElement.outerHTML,
        values:Array.from(document.querySelectorAll('input,textarea')).map(x=>x.value),
        local:JSON.stringify(localStorage),session:JSON.stringify(sessionStorage),url:location.href})''')
    for secret in (PROVIDER_KEY,ONVIF_USER,ONVIF_PASSWORD):
        assert all(secret not in str(value) for value in state.values()),'private value in delivered document/storage/URL'
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'),'horizontal overflow'


async def confirm(page):
    form=page.locator("form[action='/devices/apply']")
    for checkbox in await form.locator("input[type='checkbox']").all():
        assert not await checkbox.is_checked()
        await checkbox.focus()
        await page.keyboard.press('Space')
        assert await checkbox.is_checked()
    async with page.expect_navigation():
        await form.get_by_role('button',name='Confirm saved configuration',exact=True).click()


async def scenario(output,name,viewport,executable,*,port_only=False):
    from playwright.async_api import async_playwright,expect
    with tempfile.TemporaryDirectory(prefix='owned-device-workflows-',dir=output) as temporary:
        root=Path(temporary)
        controller=root/'controller'
        controller.mkdir(mode=0o700)
        cert,_=ensure_identity_certificate(controller,'020000000099')
        pem=cert.read_text()
        pin=hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
        key=root/'key'
        if not port_only:
            key.mkdir(mode=0o700)
            config=initialize(key/'config.json',key,controller_host='127.0.0.1')
            config['controller']['expected_fingerprint']=pin
            atomic_private(key/'controller-ca.pem',pem)
            atomic_private(key/'config.json',json.dumps(config))
        port=root/'port'
        port.mkdir(mode=0o700)
        ensure_identity_certificate(port,'020000000088')
        atomic_private(port/'identity.json',json.dumps({'mac':'020000000088'}))
        atomic_private(port/'controller-ca.pem',pem)
        atomic_private(port/'config.json',json.dumps({'mac':'020000000088','controller_ip':'192.168.50.1',
            'device_ip':'192.168.50.20','controller_pin':pin,'firmware_version':'5.1.12'}))
        directories={'port':port}
        if not port_only:
            directories['key']=key
        original={label:(directory/'config.json').read_bytes() for label,directory in directories.items()}
        identities={(label,leaf):(directory/leaf).read_bytes() for label,directory in directories.items()
                    for leaf in ('device.crt','device.key','identity.json')}
        inventory={'schema':'aikey-camera-preflight/1','protect_version':'7.3.74','fetched_at':int(time.time()),
            'cameras':[{'id':f'{i+1:024x}','mac':f'2A11223344{i+1:02X}','name':f'Fictional room {i+1}',
                'model':'UVC G3 Instant','state':'CONNECTED','smart_detect_types':['person'],
                'audio_detect_types':[]} for i in range(2)]}
        async def load():
            return deepcopy(inventory)
        bindings=[]
        for kind,fields in [('face',('detector','embedder')),('sound',('model','class_map'))]:
            files={}
            for field in fields:
                source=root/('fictional-'+field)
                atomic_private(source,b'Fictional fixture, not a loadable model')
                files[field]=(source,'/models/fictional-'+field)
            binding=root/(kind+'-binding.json')
            publish(binding,kind=kind,name='Fictional '+kind+' set',files=files)
            bindings.append(binding)
        site=ControlSite(None if port_only else key/'config.json',aiport_instances={'fixture':port/'config.json'},
            camera_pool_decoders={'aiport:fixture':sys.executable},inventory_loader=load,
            port_model_catalogs={'aiport:fixture':bindings},
            signing_key=b'v'*32,password_record=AdminSecurity.create_password_record(PASSWORD),port=8765,
            audit_log=root/'audit.jsonl')
        server=TestServer(site.app(),host='127.0.0.1')
        await server.start_server()
        site.origin=str(server.make_url('')).rstrip('/')
        site.security=AdminSecurity(b'v'*32,site.origin,allow_loopback_http=True)
        probe_calls=[]
        async def fixture_probe(**kwargs):
            assert kwargs['admission']()
            probe_calls.append(True)
            return {'matched':True,'scope':'fictional_UI_result_only','device_credentials_sent':False,'http_requests_sent':0}
        site.admin_api.device_controls.probe_runner=fixture_probe
        try:
            async with async_playwright() as playwright:
                browser=await playwright.chromium.launch(executable_path=executable,headless=True)
                try:
                    context=await browser.new_context(viewport=viewport)
                    unexpected=[]
                    allowed=urlsplit(site.origin).netloc
                    async def owned(route):
                        if urlsplit(route.request.url).netloc!=allowed:
                            unexpected.append(True)
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route('**/*',owned)
                    page=await context.new_page()
                    page.set_default_timeout(10000)
                    errors=[]
                    page.on('pageerror',lambda _:errors.append('page_error'))
                    await page.goto(site.origin+'/login')
                    await page.get_by_label('Password',exact=True).fill(PASSWORD)
                    async with page.expect_navigation():
                        await page.get_by_role('button',name='Sign in',exact=True).click()
                    await page.get_by_role('link',name='Device configuration',exact=True).click()
                    print(json.dumps({'viewport':name,'phase':'controller_form'}),flush=True)
                    profile='aiport:fixture' if port_only else 'aikey'
                    selected=port if port_only else key
                    label='port' if port_only else 'key'
                    section=page.get_by_role('heading',name=profile,exact=True).locator('..')
                    await section.get_by_label('Controller public certificate (PEM)',exact=True).fill(pem)
                    await section.get_by_label('Reviewed SHA-256 fingerprint',exact=True).fill(pin)
                    async with page.expect_navigation():
                        await section.get_by_role('button',name='Review controller change',exact=True).click()
                    await expect(page.get_by_role('heading',name='Review device configuration',exact=True)).to_be_visible()
                    assert (selected/'config.json').read_bytes()==original[label]
                    await page.get_by_role('button',name='Confirm saved configuration',exact=True).click()
                    assert (selected/'config.json').read_bytes()==original[label]
                    async with page.expect_navigation():
                        await page.get_by_role('button',name='Test reviewed anonymous controller TLS',exact=True).click()
                    assert probe_calls==[True]
                    await clean_document(page)
                    await page.locator('main').screenshot(path=str(output/(name+'-controller-review.png')))
                    await confirm(page)
                    await expect(page.get_by_text('Saved bytes read back. Running service unchanged.',exact=True)).to_be_visible()
                    section=page.get_by_role('heading',name=profile,exact=True).locator('..')
                    async with page.expect_navigation():
                        await section.get_by_role('button',name='Review prior saved settings',exact=True).click()
                    await confirm(page)
                    assert (selected/'config.json').read_bytes()==original[label]
                    await page.get_by_role('link',name='Camera pairing and capture',exact=True).click()
                    print(json.dumps({'viewport':name,'phase':'initial_capture_form'}),flush=True)
                    await page.get_by_label('Fictional room 1',exact=True).check()
                    await page.get_by_label('Provider',exact=True).select_option('openai')
                    await page.get_by_label('Object model ID',exact=True).fill('fictional-object-model')
                    await page.get_by_label('Exact API base URL',exact=True).fill('https://api.openai.com/v1')
                    await page.get_by_label('New private API key, if needed',exact=True).fill(PROVIDER_KEY)
                    await page.get_by_label('Allow this non-loopback destination',exact=True).check()
                    async with page.expect_navigation():
                        await page.get_by_role('button',name='Review local capture cameras',exact=True).click()
                    await clean_document(page)
                    await expect(page.get_by_text('Fictional room 1',exact=True)).to_be_visible()
                    await page.locator('main').screenshot(path=str(output/(name+'-capture-review.png')))
                    await confirm(page)
                    await clean_document(page)
                    assert len(json.loads((port/'config.json').read_bytes())['paired_streams'])==1
                    history=[]
                    print(json.dumps({'viewport':name,'phase':'back_navigation_privacy'}),flush=True)
                    for _ in range(2):
                        await page.go_back(wait_until='domcontentloaded')
                        await clean_document(page)
                        history.append(urlsplit(page.url).path)
                    await page.goto(site.origin+'/onvif-cameras')
                    print(json.dumps({'viewport':name,'phase':'onvif_form'}),flush=True)
                    await page.get_by_label('Camera label',exact=True).fill('Fictional ONVIF entrance')
                    await page.get_by_label('Camera host, no URL',exact=True).fill('192.168.50.80')
                    await page.get_by_label('Independently reviewed HTTPS certificate SHA-256',exact=True).fill('d'*64)
                    await page.get_by_label('New camera username',exact=True).fill(ONVIF_USER)
                    await page.get_by_label('New camera password',exact=True).fill(ONVIF_PASSWORD)
                    async with page.expect_navigation():
                        await page.get_by_role('button',name='Review metadata and private references',exact=True).click()
                    await clean_document(page)
                    await page.locator('main').screenshot(path=str(output/(name+'-onvif-review.png')))
                    await confirm(page)
                    await clean_document(page)
                    await page.get_by_role('link',name='ONVIF references (ingest unsupported)',exact=True).click()
                    await expect(page.get_by_role('heading',name='Fictional ONVIF entrance',exact=True)).to_be_visible()
                    await clean_document(page)
                    assert json.loads((port/'config.json').read_bytes()).get('onvif_catalog_ref')
                    if not port_only:
                        services=[('Local named recognition','recognition'),('Local face enhancement','enhancement'),
                                  ('Local deep-session services','deep')]
                        for title,kind in services:
                            print(json.dumps({'viewport':name,'phase':'local_'+kind+'_form'}),flush=True)
                            await page.goto(site.origin+'/')
                            await page.get_by_role('link',name='Local feature services',exact=True).click()
                            section=page.get_by_role('heading',name=title,exact=True).locator('..')
                            if kind=='deep':
                                await section.get_by_label('Local person re-identification service',exact=True).fill('http://127.0.0.1:8183')
                                await section.get_by_label('Optional local text reranking service',exact=True).fill('http://127.0.0.1:8184')
                            else:
                                await section.get_by_label('Local service address',exact=True).fill('http://127.0.0.1:'+('8181' if kind=='recognition' else '8182'))
                                if kind=='recognition':
                                    await section.get_by_label('Fictional room 2',exact=True).check()
                            async with page.expect_navigation():
                                await section.get_by_role('button',name='Review local feature policy',exact=True).click()
                            await clean_document(page)
                            if kind=='recognition':
                                await expect(page.get_by_text('Fictional room 2',exact=True)).to_be_visible()
                            else:
                                assert 'no local per-camera allowlist' in await page.locator('main').inner_text()
                            await page.locator('main').screenshot(path=str(output/(name+'-local-'+kind+'-review.png')))
                            await confirm(page)
                            section=page.get_by_role('heading',name='aikey',exact=True).locator('..')
                            async with page.expect_navigation():
                                await section.get_by_role('button',name='Review prior saved settings',exact=True).click()
                            await confirm(page)
                            assert (key/'config.json').read_bytes()==original['key']
                    # Role dependencies are explicit: create speech first, then
                    # sound; Undo sound before Undo speech. Face Undo keeps its
                    # vector-space guard instead of permitting a silent reset.
                    port_roles=[('speech','Audio capture and speech presence'),('face','Native face embeddings'),
                                ('sound','Local sound classification')]
                    for kind,title in port_roles:
                        print(json.dumps({'viewport':name,'phase':'port_'+kind+'_form'}),flush=True)
                        if kind=='sound':
                            # The earlier speech case was undone; enroll audio
                            # again with the same ordinary reviewed form below.
                            await page.goto(site.origin+'/port-features')
                            section=page.get_by_role('heading',name='Audio capture and speech presence',exact=True).locator('..')
                            await section.get_by_label('Fictional room 1',exact=True).check()
                            async with page.expect_navigation():
                                await section.get_by_role('button',name='Review feature permissions',exact=True).click()
                            await confirm(page)
                        before_port=json.loads((port/'config.json').read_bytes())
                        await page.goto(site.origin+'/')
                        await page.get_by_role('link',name='AI Port feature permissions',exact=True).click()
                        section=page.get_by_role('heading',name=title,exact=True).locator('..')
                        await section.get_by_label('Fictional room 1',exact=True).check()
                        if kind!='speech':
                            await section.get_by_label(title+' model set',exact=True).select_option(label='Fictional '+kind+' set')
                        if kind=='sound':
                            await section.get_by_label('Dog barking',exact=True).check()
                        async with page.expect_navigation():
                            await section.get_by_role('button',name='Review feature permissions',exact=True).click()
                        await clean_document(page)
                        await page.locator('main').screenshot(path=str(output/(name+'-port-'+kind+'-review.png')))
                        await confirm(page)
                        assert ('live_'+kind if kind!='speech' else 'live_speech_cameras') in json.loads((port/'config.json').read_bytes())
                        section=page.get_by_role('heading',name='aiport:fixture',exact=True).locator('..')
                        async with page.expect_navigation():
                            await section.get_by_role('button',name='Review prior saved settings',exact=True).click()
                        await confirm(page)
                        restored=json.loads((port/'config.json').read_bytes())
                        if kind=='face':
                            assert restored.pop('face_model_space_ref')
                        assert restored==before_port
                    assert not key.exists() if port_only else (key/'config.json').read_bytes()==original['key']
                    assert all((directories[label]/leaf).read_bytes()==raw
                               for (label,leaf),raw in identities.items())
                    audit=(root/'audit.jsonl').read_text()
                    assert all(secret not in audit for secret in (PROVIDER_KEY,ONVIF_USER,ONVIF_PASSWORD))
                    assert not unexpected and not errors
                    await context.close()
                finally:
                    await browser.close()
        finally:
            await server.close()
        assert not site.admin_api.operations and not site.admin_api.device_controls.grants
        result={'profile_mode':'port_only' if port_only else 'key_and_port',
            'no_placeholder_key_identity':not key.exists() if port_only else None,
            'controller_preview_probe_fixture_save_exact_undo':True,'unchecked_consent_enforced':True,
            'local_role_setup_and_exact_undo':['recognition','enhancement','deep'] if not port_only else [],
            'port_feature_setup_and_policy_undo':['speech','face','sound'],
            'face_undo_retains_model_space_guard':True,
            'independent_port_initial_provider_and_capture':True,'onvif_metadata_only_saved':True,
            'keyboard_confirmation':True,'identity_files_unchanged':True,'private_values_absent_after_save_and_back':True,
            'back_navigation_documents':history,'unowned_browser_requests':0,'model_or_camera_requests':0,
            'controller_probe':'fixture_only; actual loopback TLS tested separately','visual_review':'not_performed_by_runner',
            'owned_servers_browser_and_grants_collected':True}
    assert not root.exists()
    return result


async def main(args):
    args.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    def hashes():
        return {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((REPO/'src/aikey').glob('*.py'))}
    before=hashes()
    results={}
    runner=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for mode in ('dual','port-only'):
        for name,viewport in [('desktop',{'width':1366,'height':1000}),('mobile',{'width':390,'height':844})]:
            label=name if mode=='dual' else mode+'-'+name
            results[label]=await asyncio.wait_for(scenario(args.output,label,viewport,args.browser_executable,
                port_only=mode=='port-only'),180)
    assert hashes()==before
    (args.output/'report.json').write_text(json.dumps({'schema':1,'source_module_hashes':before,
        'source_unchanged':True,'runner_sha256':runner,'browser':'sandboxed installed Chrome','scenarios':results},indent=2)+'\n')
    print(json.dumps({'scenarios':list(results),'all_passed':True,'source_modules':len(before),'real_service_actions':0}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--browser-executable',default='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    asyncio.run(main(parser.parse_args()))
