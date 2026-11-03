"""Fictional saved Key scope only in sandboxed owned Chrome; no real cameras."""

import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))

from aiohttp.test_utils import TestServer  # noqa: E402
from aikey.admin_security import AdminSecurity  # noqa: E402
from aikey.config import initialize  # noqa: E402
from aikey.control_site import ControlSite  # noqa: E402


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect
    with tempfile.TemporaryDirectory(prefix='owned-key-face-browser-', dir=output) as temporary:
        root = Path(temporary)
        key = root / 'key/config.json'
        initialize(key, key.parent, controller_host='127.0.0.1')
        raw = json.loads(key.read_bytes())
        raw['face_recognition'] = {'server': 'http://127.0.0.1:1',
                                  'camera_ids': ['b' * 24, 'a' * 24], 'native_face_cameras': 'skip'}
        key.write_text(json.dumps(raw, indent=1) + '\n')
        templates = key.parent / 'faces/identities.json'
        templates.parent.mkdir(mode=0o700)
        canary = b'fictional template canary must not be parsed'
        templates.write_bytes(canary)
        templates.chmod(0o600)
        password = 'fictional saved Key face browser passphrase'
        site = ControlSite(key, signing_key=b'k' * 32,
            password_record=AdminSecurity.create_password_record(password), port=8765,
            audit_log=root / 'fictional-audit.jsonl')
        report = {'schema': 'aikey-camera-preflight/1', 'protect_version': '7.3.74',
            'fetched_at': int(time.time()), 'cameras': [
                {'id': c * 24, 'mac': '02000000000' + str(i), 'name': label,
                 'model': 'UVC G5 Flex', 'state': 'CONNECTED', 'smart_detect_types': [], 'audio_detect_types': []}
                for i, (c, label) in enumerate([('a', 'Fictional garden'),
                    ('b', 'Fictional office <script>'), ('c', 'Unapproved camera')], 1)]}
        async def load():
            return deepcopy(report)
        site.inventory_loader = load
        errors, unowned, posts = [], [], []
        admin = TestServer(site.app(), host='127.0.0.1')
        try:
            await admin.start_server()
            site.origin = str(admin.make_url('/')).rstrip('/')
            site.security = AdminSecurity(b'k' * 32, site.origin, allow_loopback_http=True)
            async with async_playwright() as driver:
                context = await driver.chromium.launch_persistent_context(
                    user_data_dir=str(root / 'browser-profile'), executable_path=executable,
                    headless=True, chromium_sandbox=True, viewport=viewport,
                    is_mobile=name == 'mobile', device_scale_factor=1,
                    args=['--disable-background-networking', '--disable-component-update',
                          '--disable-sync', '--no-first-run'])
                try:
                    async def route(route):
                        parsed = urlsplit(route.request.url)
                        if f'{parsed.scheme}://{parsed.netloc}' != site.origin:
                            unowned.append(route.request.url)
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route('**/*', route)
                    page = await context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: posts.append(urlsplit(request.url).path)
                            if request.method == 'POST' else None)
                    await page.goto(site.origin + '/key-face-cameras')
                    await expect(page.get_by_role('heading', name='Administrator sign in')).to_be_visible()
                    await page.get_by_label('Password', exact=True).fill(password)
                    await page.get_by_role('button', name='Sign in', exact=True).click()
                    await page.get_by_role('link', name='Saved Key face cameras', exact=True).click()
                    await expect(page.get_by_role('heading', name='Saved AI Key face cameras')).to_be_visible()
                    before = key.read_bytes()
                    garden = page.get_by_role('checkbox', name='Fictional garden', exact=True)
                    await garden.uncheck()
                    await page.get_by_role('button', name='Review camera restriction', exact=True).click()
                    await expect(page.get_by_role('heading', name='Review Key face cameras')).to_be_visible()
                    assert key.read_bytes() == before
                    await page.get_by_role('link', name='Cancel', exact=True).click()
                    assert key.read_bytes() == before
                    await page.get_by_role('checkbox', name='Fictional garden', exact=True).uncheck()
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    await page.screenshot(path=str(output / (name + '-saved-face-cameras.png')), full_page=True)
                    review = page.get_by_role('button', name='Review camera restriction', exact=True)
                    await review.focus()
                    assert await review.evaluate('el => el === document.activeElement')
                    await page.keyboard.press('Enter')
                    await expect(page.get_by_text('Fictional office <script>', exact=True)).to_be_visible()
                    checkbox = page.get_by_role('checkbox', name='Save only this reviewed restriction; running processing does not stop.', exact=True)
                    assert not await checkbox.is_checked()
                    await checkbox.focus()
                    await page.keyboard.press('Space')
                    assert await checkbox.is_checked() and await page.locator('script').count() == 0
                    await page.evaluate('scrollTo(0, 0)')
                    await expect(page.get_by_role('heading', name='Review Key face cameras')).to_be_visible()
                    await expect(page.get_by_role('heading', name='Review Key face cameras')).to_be_in_viewport(ratio=1)
                    await page.locator('main').screenshot(path=str(output / (name + '-main-restriction.png')))
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    await page.screenshot(path=str(output / (name + '-restriction-preview.png')), full_page=True)
                    await page.get_by_role('button', name='Save Key face restriction', exact=True).click()
                    await expect(page.get_by_role('heading', name='Key face restriction saved')).to_be_visible()
                    expected = deepcopy(raw)
                    expected['face_recognition']['camera_ids'] = ['b' * 24]
                    assert json.loads(key.read_bytes()) == expected and templates.read_bytes() == canary
                    await page.get_by_role('link', name='Read saved face cameras', exact=True).click()
                    await page.get_by_role('button', name='Review camera restriction', exact=True).click()
                    # Out-of-band fixture edit makes an already-reviewed no-op stale.
                    expected['metadata'] = {'fictional': 'concurrent fixture edit'}
                    key.write_text(json.dumps(expected) + '\n')
                    await checkbox.check()
                    await page.get_by_role('button', name='Save Key face restriction', exact=True).click()
                    await expect(page.get_by_role('heading', name='Key face scope not saved')).to_be_visible()
                    assert json.loads(key.read_bytes()) == expected
                    report['fetched_at'] = 1
                    site._inventory_snapshot = None
                    await page.get_by_role('link', name='Reload saved face cameras', exact=True).click()
                    await expect(page.get_by_text('Individual editing needs fresh, unambiguous names', exact=False)).to_be_visible()
                    await page.get_by_role('button', name='Review disabling saved Key recognition', exact=True).click()
                    await expect(page.get_by_text('Remove the entire saved AI Key face recognition policy.', exact=True)).to_be_visible()
                    assert not await checkbox.is_checked()
                    await checkbox.check()
                    await page.evaluate('scrollTo(0, 0)')
                    await expect(page.get_by_role('heading', name='Review Key face cameras')).to_be_visible()
                    await expect(page.get_by_role('heading', name='Review Key face cameras')).to_be_in_viewport(ratio=1)
                    await page.locator('main').screenshot(path=str(output / (name + '-main-disable.png')))
                    await page.screenshot(path=str(output / (name + '-disable-preview.png')), full_page=True)
                    await page.get_by_role('button', name='Save Key face restriction', exact=True).click()
                    await expect(page.get_by_role('heading', name='Key face restriction saved')).to_be_visible()
                    del expected['face_recognition']
                    assert json.loads(key.read_bytes()) == expected and templates.read_bytes() == canary
                    await page.get_by_role('link', name='Read saved face cameras', exact=True).click()
                    await expect(page.get_by_text('No AI Key face recognition policy is saved.', exact=False)).to_be_visible()
                    assert await page.locator('form').count() == 0
                    assert await page.evaluate('localStorage.length + sessionStorage.length') == 0
                    assert str(root) not in await page.content() and password not in await page.content()
                    assert 'Fictional office' not in site.audit.path.read_text()
                    assert not errors and not unowned
                    assert posts == ['/login', '/key-face-cameras/preview', '/key-face-cameras/preview',
                        '/key-face-cameras/apply', '/key-face-cameras/preview', '/key-face-cameras/apply',
                        '/key-face-cameras/preview', '/key-face-cameras/apply']
                    result = {'viewport': viewport, 'browser_version': context.browser.version,
                        'audit_enabled': True, 'keyboard_review_and_consent': True, 'cancel_preserves_exact_bytes': True,
                        'escaped_private_names': True, 'retained_existing_camera_only': True,
                        'stale_noop_refused': True, 'global_disable_without_inventory': True,
                        'template_canary_unchanged': True, 'unrelated_configuration_preserved': True,
                        'no_body_overflow': True, 'empty_browser_storage': True,
                        'real_controller_model_media_calls': 0, 'unowned_requests': 0,
                        'visual_review': 'not_performed_by_runner'}
                finally:
                    await context.close()
        finally:
            await admin.close()
    assert not root.exists()
    return {**result, 'owned_profiles_and_servers_collected': True}


async def main(args):
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted((REPO / 'src/aikey').glob('*.py'))}
    results = {}
    for name, viewport in [('desktop', {'width': 1200, 'height': 900}),
                           ('mobile', {'width': 390, 'height': 844})]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted((REPO / 'src/aikey').glob('*.py'))}
    assert before == after
    (args.output / 'result.json').write_text(json.dumps({'scenarios': results,
        'source_modules': before}, indent=2) + '\n')
    print(json.dumps({'desktop_mobile': 'passed', 'owned_resources_collected': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser-executable')
    asyncio.run(main(parser.parse_args()))
