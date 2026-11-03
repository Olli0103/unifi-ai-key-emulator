"""Owned fictional photo/template flow in sandboxed Chrome; no real encoder."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestServer  # noqa: E402
from aikey.admin_security import AdminSecurity  # noqa: E402
from aikey.config import initialize  # noqa: E402
from aikey.control_site import ControlSite  # noqa: E402
from aikey.faces import FaceStore  # noqa: E402

SPACE = 'a' * 64
VECTOR = [1.0] + [0.0] * 127
IMAGE = b'\xff\xd8\xff' + b'FICTIONAL-BROWSER-IMAGE' * 8
LABEL = 'Fictional <script> identity & "label"'


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect
    with tempfile.TemporaryDirectory(prefix='owned-face-enrollment-', dir=output) as temporary:
        root = Path(temporary)
        backend = {'space': SPACE, 'calls': 0}
        async def embed(request):
            parts = await request.multipart()
            image = await parts.next()
            assert image.name == 'image' and bytes(await image.read()) == IMAGE
            assert await parts.next() is None
            backend['calls'] += 1
            return web.json_response({'embedding_space': backend['space'], 'faces': [
                {'embedding': VECTOR, 'box': [.1, .1, .4, .4], 'score': .9}]})
        model_app = web.Application()
        model_app.router.add_post('/v1/faces', embed)
        model = TestServer(model_app, host='127.0.0.1')
        await model.start_server()
        admin = None
        try:
            key = root / 'key' / 'config.json'
            initialize(key, key.parent, controller_host='127.0.0.1')
            config = json.loads(key.read_text())
            config['face_recognition'] = {'server': str(model.make_url('')).rstrip('/'),
                                           'camera_ids': ['fictional-face-camera']}
            key.write_text(json.dumps(config) + '\n')
            key_before = key.read_bytes()
            password = 'fictional enrollment browser passphrase'
            site = ControlSite(key, signing_key=b'e' * 32,
                password_record=AdminSecurity.create_password_record(password), port=8765,
                audit_log=root / 'fictional-audit.jsonl')
            admin = TestServer(site.app(), host='127.0.0.1')
            await admin.start_server()
            site.origin = str(admin.make_url('')).rstrip('/')
            site.security = AdminSecurity(b'e' * 32, site.origin, allow_loopback_http=True)
            store = FaceStore(key.parent)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(executable_path=executable, headless=True)
                try:
                    context = await browser.new_context(viewport=viewport)
                    unowned = []
                    allowed = urlsplit(site.origin).netloc
                    async def owned(route):
                        if urlsplit(route.request.url).netloc != allowed:
                            unowned.append('blocked')
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route('**/*', owned)
                    page = await context.new_page()
                    await page.goto(site.origin + '/login')
                    await page.get_by_label('Password', exact=True).fill(password)
                    async with page.expect_navigation():
                        await page.get_by_role('button', name='Sign in', exact=True).click()
                    await page.goto(site.origin + '/face-identities')
                    await page.get_by_role('link', name='Enroll a local face identity', exact=True).click()
                    await expect(page.get_by_role('heading', name='Enroll a local face identity', exact=True)).to_be_visible()
                    async def input_image():
                        await page.get_by_label('Local identity label', exact=True).fill(LABEL)
                        await page.get_by_label('One face JPEG', exact=True).set_input_files({
                            'name': 'fictional.jpg', 'mimeType': 'image/jpeg', 'buffer': IMAGE})
                    await input_image()
                    consent = page.get_by_label('I have permission to process this supplied image locally and create a private template.', exact=True)
                    await expect(consent).not_to_be_checked()
                    await page.get_by_role('button', name='Analyze locally and review', exact=True).click()
                    assert backend['calls'] == 0 and not store.path.exists()
                    await page.get_by_label('Local identity label', exact=True).focus()
                    await page.locator('main').screenshot(path=str(output / (name + '-form.png')))
                    await consent.check()
                    async with page.expect_navigation():
                        await page.get_by_role('button', name='Analyze locally and review', exact=True).click()
                    await expect(page.get_by_role('heading', name='Confirm local enrollment', exact=True)).to_be_visible()
                    await expect(page.locator('script')).to_have_count(0)
                    assert backend['calls'] == 1 and not store.path.exists()
                    await page.locator('main').screenshot(path=str(output / (name + '-preview.png')))
                    async with page.expect_navigation():
                        await page.get_by_role('button', name='Discard and return', exact=True).click()
                    assert not store.path.exists() and not site.admin_api.face_enrollment_control.grants
                    await page.get_by_role('link', name='Enroll a local face identity', exact=True).click()
                    await input_image()
                    await page.get_by_label('I have permission to process this supplied image locally and create a private template.', exact=True).check()
                    async with page.expect_navigation():
                        await page.get_by_role('button', name='Analyze locally and review', exact=True).click()
                    confirm = page.get_by_label('Save this private local face template.', exact=True)
                    await expect(confirm).not_to_be_checked()
                    await page.get_by_role('button', name='Save local template', exact=True).click()
                    assert not store.path.exists() and backend['calls'] == 2
                    await confirm.focus()
                    await page.keyboard.press('Space')
                    await expect(confirm).to_be_checked()
                    async with page.expect_navigation():
                        await page.get_by_role('button', name='Save local template', exact=True).click()
                    await expect(page.get_by_role('heading', name='Local face identities', exact=True)).to_be_visible()
                    assert store.match(VECTOR, embedding_space=SPACE)[0] == LABEL
                    assert store.snapshot()['identities'][0]['templates'] == 1
                    await page.locator('main').screenshot(path=str(output / (name + '-saved.png')))
                    before = store.path.read_bytes()
                    backend['space'] = 'b' * 64
                    await page.get_by_role('link', name='Enroll a local face identity', exact=True).click()
                    await input_image()
                    await page.get_by_label('I have permission to process this supplied image locally and create a private template.', exact=True).check()
                    async with page.expect_navigation():
                        await page.get_by_role('button', name='Analyze locally and review', exact=True).click()
                    await expect(page.get_by_role('heading', name='Local enrollment not completed', exact=True)).to_be_visible()
                    assert store.path.read_bytes() == before and backend['calls'] == 3
                    await page.locator('main').screenshot(path=str(output / (name + '-mismatch.png')))
                    assert not site.admin_api.face_enrollment_control.grants
                    assert key.read_bytes() == key_before and not unowned
                    assert all(IMAGE not in p.read_bytes() for p in root.rglob('*') if p.is_file())
                    assert LABEL not in (root / 'fictional-audit.jsonl').read_text()
                    assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    result = {'unchecked_analysis_and_save_consents_enforced': True,
                        'discard_does_not_save': True, 'keyboard_confirmation': True,
                        'one_template_saved_with_correct_space': True, 'mismatch_preserves_saved_bytes': True,
                        'fictional_model_http_calls': 3, 'real_models_or_biometric_inputs': 0,
                        'no_uploaded_image_file': True, 'configuration_unchanged': True,
                        'unowned_requests': 0, 'visual_review': 'not_performed_by_runner'}
                    await context.close()
                finally:
                    await browser.close()
        finally:
            if admin is not None:
                await admin.close()
            await model.close()
        assert not site.admin_api.face_enrollment_control.grants
    assert not root.exists()
    return {**result, 'owned_profiles_servers_and_pending_templates_collected': True}


async def main(args):
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    source_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted((REPO / 'src/aikey').glob('*.py'))}
    results = {}
    for name, viewport in [('desktop', {'width': 1200, 'height': 900}),
                           ('mobile', {'width': 390, 'height': 844})]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    source_after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted((REPO / 'src/aikey').glob('*.py'))}
    assert source_before == source_after
    (args.output / 'result.json').write_text(json.dumps(
        {'scenarios': results, 'source_modules': source_before}, indent=2) + '\n')
    print(json.dumps({'desktop_mobile': 'passed', 'owned_resources_collected': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser-executable')
    asyncio.run(main(parser.parse_args()))
