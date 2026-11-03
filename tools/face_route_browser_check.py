"""Owned fictional local Face-route form in sandboxed Chrome; never a real model."""

import argparse
import asyncio
from copy import deepcopy
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


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect
    with tempfile.TemporaryDirectory(prefix='owned-face-route-', dir=output) as temporary:
        root = Path(temporary)
        calls = []
        async def unexpected(request):
            calls.append(request.path)
            return web.json_response({'unexpected': True}, status=500)
        servers = []
        for _ in range(2):
            app = web.Application()
            app.router.add_route('*', '/{path:.*}', unexpected)
            server = TestServer(app, host='127.0.0.1')
            await server.start_server()
            servers.append(server)
        old, new = [str(server.make_url('')).rstrip('/') for server in servers]
        admin = None
        try:
            key = root / 'key' / 'config.json'
            initialize(key, key.parent, controller_host='127.0.0.1')
            config = json.loads(key.read_text())
            config['face_recognition'] = {'server': old, 'camera_ids': ['fictional-camera-b', 'fictional-camera-a'],
                                          'native_face_cameras': 'skip'}
            config['metadata'] = {'opaque': 'FICTIONAL-PRIVATE-CANARY'}
            key.write_text(json.dumps(config) + '\n')
            before = key.read_bytes()
            face = key.parent / 'faces'
            face.mkdir(mode=0o700)
            template = face / 'identities.json'
            template.write_bytes(b'fictional-template-canary-not-to-be-read')
            template.chmod(0o600)
            template_before = template.read_bytes()
            password = 'fictional face route browser passphrase'
            site = ControlSite(key, signing_key=b'r' * 32,
                password_record=AdminSecurity.create_password_record(password), port=8765,
                audit_log=root / 'fictional-audit.jsonl')
            admin = TestServer(site.app(), host='127.0.0.1')
            await admin.start_server()
            site.origin = str(admin.make_url('')).rstrip('/')
            site.security = AdminSecurity(b'r' * 32, site.origin, allow_loopback_http=True)
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
                    await page.get_by_role('link', name='Local face service', exact=True).click()
                    await expect(page.get_by_role('heading', name='Saved local face service', exact=True)).to_be_visible()
                    route = page.get_by_label('Local face service address', exact=True)
                    await expect(route).to_have_value(old)
                    await route.fill(new)
                    await route.focus()
                    await page.locator('main').screenshot(path=str(output / (name + '-form.png')))
                    async with page.expect_navigation():
                        await page.get_by_role('button', name='Preview saved face route', exact=True).click()
                    await expect(page.get_by_role('heading', name='Confirm saved face route', exact=True)).to_be_visible()
                    consent = page.get_by_label('I verified this local destination and want to save only this route.', exact=True)
                    await expect(consent).not_to_be_checked()
                    assert key.read_bytes() == before and calls == []
                    await page.get_by_role('button', name='Save local face route', exact=True).click()
                    assert key.read_bytes() == before and calls == []
                    await page.keyboard.press('Escape')
                    await page.locator('h1').click()
                    await page.wait_for_timeout(300)  # Native validation bubble close animation.
                    await page.locator('main').screenshot(path=str(output / (name + '-preview.png')))
                    await page.get_by_role('link', name='Cancel and return', exact=True).click()
                    assert key.read_bytes() == before
                    await page.get_by_label('Local face service address', exact=True).fill(new)
                    async with page.expect_navigation():
                        await page.get_by_role('button', name='Preview saved face route', exact=True).click()
                    consent = page.get_by_label('I verified this local destination and want to save only this route.', exact=True)
                    await consent.focus()
                    await page.keyboard.press('Space')
                    await expect(consent).to_be_checked()
                    async with page.expect_navigation():
                        await page.get_by_role('button', name='Save local face route', exact=True).click()
                    await expect(page.get_by_role('heading', name='Local face route saved', exact=True)).to_be_visible()
                    expected = deepcopy(config)
                    expected['face_recognition']['server'] = new
                    assert json.loads(key.read_bytes()) == expected
                    assert template.read_bytes() == template_before and not site.admin_api.face_route_control.grants
                    await page.locator('main').screenshot(path=str(output / (name + '-saved.png')))
                    await page.get_by_role('link', name='Local face service', exact=True).click()
                    await page.get_by_label('Local face service address', exact=True).fill(old)
                    async with page.expect_navigation():
                        await page.get_by_role('button', name='Preview saved face route', exact=True).click()
                    key.write_bytes(key.read_bytes() + b'\n')
                    after = key.read_bytes()
                    await page.get_by_label('I verified this local destination and want to save only this route.', exact=True).check()
                    async with page.expect_navigation():
                        await page.get_by_role('button', name='Save local face route', exact=True).click()
                    await expect(page.get_by_role('heading', name='Local face route not saved', exact=True)).to_be_visible()
                    assert key.read_bytes() == after and calls == [] and not unowned
                    assert template.read_bytes() == template_before
                    audit = (root / 'fictional-audit.jsonl').read_text()
                    assert old not in audit and new not in audit and 'FICTIONAL-PRIVATE-CANARY' not in audit
                    assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    await page.locator('main').screenshot(path=str(output / (name + '-stale.png')))
                    result = {'unchanged_before_separate_confirmation': True,
                        'unchecked_consent_enforced': True, 'cancel_does_not_write': True,
                        'keyboard_confirmation': True, 'only_saved_server_changed': True,
                        'template_bytes_and_camera_order_preserved': True,
                        'stale_confirmation_refused': True, 'no_model_or_controller_requests': True,
                        'real_profiles_or_biometrics': 0, 'unowned_browser_requests': 0,
                        'visual_review': 'not_performed_by_runner'}
                    await context.close()
                finally:
                    await browser.close()
        finally:
            if admin is not None:
                await admin.close()
            for server in servers:
                await server.close()
        assert site.admin_api.face_route_control.closed and not site.admin_api.face_route_control.grants
    assert not root.exists()
    return {**result, 'owned_profiles_servers_and_pending_grants_collected': True}


async def main(args):
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    def hashes():
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((REPO / 'src/aikey').glob('*.py'))}
    before = hashes()
    results = {}
    for name, viewport in [('desktop', {'width': 1366, 'height': 1000}),
                           ('mobile', {'width': 390, 'height': 844})]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    assert hashes() == before
    report = {'schema': 1, 'source_module_hashes': before, 'source_unchanged': True,
              'browser': 'sandboxed installed Chrome', 'scenarios': results}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'source_modules': len(before), 'desktop': 'passed', 'mobile': 'passed',
                      'real_service_actions': 0, 'real_biometric_inputs': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser-executable', required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
