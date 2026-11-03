"""Sandboxed Chrome against an owned fictional identity store; never real faces."""

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

from aiohttp.test_utils import TestServer  # noqa: E402
from aikey.admin_security import AdminSecurity  # noqa: E402
from aikey.config import initialize  # noqa: E402
from aikey.control_site import ControlSite  # noqa: E402
from aikey.faces import FaceStore  # noqa: E402


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect
    with tempfile.TemporaryDirectory(prefix='owned-face-browser-', dir=output) as temporary:
        root = Path(temporary)
        key = root / 'key' / 'config.json'
        initialize(key, key.parent, controller_host='127.0.0.1')
        password = 'fictional identity browser passphrase'
        site = ControlSite(key, signing_key=b'f' * 32,
            password_record=AdminSecurity.create_password_record(password), port=8765,
            audit_log=root / 'fictional-audit.jsonl')
        store = FaceStore(key.parent)
        target = 'Fictional target <script> & "name"'
        sibling = 'Fictional sibling'
        long_name = 'Z' * 64
        vector = [1.0] + [0.0] * 127
        for label in (sibling, target, target, long_name):
            store.enroll(label, vector, embedding_space='a' * 64)
        before = store.path.read_bytes()
        config_before = key.read_bytes()
        errors, posts, unowned = [], [], []
        admin = TestServer(site.app(), host='127.0.0.1')
        try:
            await admin.start_server()
            site.origin = str(admin.make_url('/')).rstrip('/')
            site.security = AdminSecurity(b'f' * 32, site.origin, allow_loopback_http=True)
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
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    page.on('request', lambda r: posts.append(urlsplit(r.url).path)
                            if r.method == 'POST' else None)
                    await page.goto(site.origin + '/face-identities')
                    await expect(page.get_by_role('heading', name='Administrator sign in')).to_be_visible()
                    await page.get_by_label('Password', exact=True).fill(password)
                    async with page.expect_navigation():
                        await page.get_by_role('button', name='Sign in', exact=True).click()
                    await page.get_by_role('link', name='Local face identities', exact=True).click()
                    await expect(page.get_by_role('heading', name='Local face identities')).to_be_visible()
                    selected = page.locator('section.face-identity').filter(
                        has=page.get_by_role('heading', name=target, exact=True))
                    await expect(selected.get_by_text('Saved templates: 2', exact=True)).to_be_visible()
                    assert store.path.read_bytes() == before
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    await selected.screenshot(path=str(output / (name + '-identity.png')))
                    await page.screenshot(path=str(output / (name + '-list.png')), full_page=True)
                    review = selected.get_by_role('button', name='Review deletion', exact=True)
                    await review.focus()
                    assert await review.evaluate('el => el === document.activeElement')
                    async with page.expect_navigation():
                        await page.keyboard.press('Enter')
                    await expect(page.get_by_role('heading', name='Confirm local identity deletion')).to_be_visible()
                    await expect(page.get_by_text('Saved templates to remove: 2.', exact=False)).to_be_visible()
                    assert store.path.read_bytes() == before
                    await page.get_by_role('link', name='Cancel and return', exact=True).click()
                    assert store.path.read_bytes() == before
                    await selected.get_by_role('button', name='Review deletion', exact=True).click()
                    checkbox = page.get_by_role('checkbox', name='Delete all local templates for this identity.', exact=True)
                    assert not await checkbox.is_checked()
                    await checkbox.focus()
                    await page.keyboard.press('Space')
                    assert await checkbox.is_checked()
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    await page.screenshot(path=str(output / (name + '-confirmation.png')), full_page=True)
                    await page.get_by_role('button', name='Delete local identity', exact=True).click()
                    await expect(page.get_by_role('heading', name='Local face identities')).to_be_visible()
                    await expect(page.get_by_role('heading', name=target, exact=True)).to_have_count(0)
                    await expect(page.get_by_role('heading', name=sibling, exact=True)).to_be_visible()
                    assert store.names() == [sibling, long_name]
                    # An independent cooperating enrollment makes an already-reviewed grant stale.
                    long_row = page.locator('section.face-identity').filter(
                        has=page.get_by_role('heading', name=long_name, exact=True))
                    await long_row.get_by_role('button', name='Review deletion').click()
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    store.enroll('Fictional concurrently added identity', vector, embedding_space='a' * 64)
                    await checkbox.check()
                    await page.get_by_role('button', name='Delete local identity', exact=True).click()
                    await expect(page.get_by_role('heading', name='Identity action not completed')).to_be_visible()
                    assert long_name in store.names()
                    assert await page.evaluate('localStorage.length + sessionStorage.length') == 0
                    assert str(root) not in await page.content() and password not in await page.content()
                    audit = site.audit.path.read_text()
                    assert target not in audit and long_name not in audit and 'templates' not in audit
                    assert key.read_bytes() == config_before and not errors and not unowned
                    assert posts == ['/login', '/face-identities/preview', '/face-identities/preview',
                                     '/face-identities/apply', '/face-identities/preview', '/face-identities/apply']
                    result = {'viewport': viewport, 'browser_version': context.browser.version,
                        'audit_enabled': True, 'escaped_identity_labels': True, 'cancel_preserves_record': True,
                        'keyboard_review_and_confirmation': True, 'target_only_deleted': True,
                        'stale_concurrent_enrollment_refused': True, 'long_labels_without_overflow': True,
                        'fixture_config_bytes_unchanged': True, 'real_controller_model_media_calls': 0,
                        'unowned_requests': 0, 'body_horizontal_overflow': False,
                        'visual_review': 'not_performed_by_runner'}
                finally:
                    await context.close()
        finally:
            await admin.close()
    assert not root.exists()
    return {**result, 'owned_profiles_and_servers_collected': True}


async def main(args):
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((REPO / 'src/aikey').glob('*.py'))}
    results = {}
    for name, viewport in [('desktop', {'width': 1200, 'height': 900}),
                           ('mobile', {'width': 390, 'height': 844})]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((REPO / 'src/aikey').glob('*.py'))}
    assert before == after
    (args.output / 'result.json').write_text(json.dumps(
        {'scenarios': results, 'source_modules': before}, indent=2) + '\n')
    print(json.dumps({'desktop_mobile': 'passed', 'owned_resources_collected': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser-executable')
    asyncio.run(main(parser.parse_args()))
